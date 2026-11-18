"""Append-only credit ledger for the Odds API pilot.

One CSV row per API interaction. Each row is written with a single write()
followed by flush() and os.fsync(). Rows are never edited or removed;
corrections are new rows (e.g. RECONCILED).

Spend accounting is pessimistic: until a RECONCILED row exists for its
fingerprint, any row whose actual_cost is UNKNOWN or whose status is
PERSIST_FAILED counts at max(known cost, expected_cost_max).
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

COLUMNS = [
    "ts_utc",
    "stage",
    "purpose",
    "endpoint",
    "params_redacted_json",
    "fingerprint",
    "expected_cost_min",
    "expected_cost_max",
    "header_last",
    "header_used",
    "header_remaining",
    "actual_cost",
    "cumulative_spent",
    "status_code",
    "raw_file",
    "notes",
]

UNKNOWN = "UNKNOWN"
# Non-HTTP values of status_code.
ST_NO_RESPONSE = "NO_RESPONSE"
ST_PERSIST_FAILED = "PERSIST_FAILED"
ST_RECONCILED = "RECONCILED"
ST_RECONCILE_MISMATCH = "RECONCILE_MISMATCH"


class LedgerCorrupt(Exception):
    pass


class BaselineCorrupt(Exception):
    pass


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_uncertain(row: dict) -> bool:
    return row.get("actual_cost") == UNKNOWN or row.get("status_code") == ST_PERSIST_FAILED


def _fsync_write(path: Path, text: str, mode: str = "a") -> None:
    with open(path, mode, encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def load_baseline(path: Path):
    """Return {'used': int, 'remaining': int, 'ts': str}, None if missing.

    Raises BaselineCorrupt if the file exists but does not validate.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        used, remaining = data["used"], data["remaining"]
        if not (isinstance(used, int) and isinstance(remaining, int)) or isinstance(used, bool):
            raise ValueError("used/remaining must be ints")
        if used < 0 or remaining < 0:
            raise ValueError("negative values")
        return {"used": used, "remaining": remaining, "ts": str(data.get("ts", ""))}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BaselineCorrupt(f"baseline.json does not validate ({type(exc).__name__})") from None


class Ledger:
    def __init__(self, path: Path, baseline_path: Path):
        self.path = Path(path)
        self.baseline_path = Path(baseline_path)

    # ---- integrity -------------------------------------------------------
    def ensure_header(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _fsync_write(self.path, ",".join(COLUMNS) + "\r\n", mode="w")

    def validate(self) -> None:
        """Raise LedgerCorrupt if the header or any row (notably the tail) fails to parse."""
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        if not raw:
            return
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise LedgerCorrupt("ledger is not valid UTF-8") from None
        if not text.endswith("\n"):
            raise LedgerCorrupt("ledger tail is incomplete (no trailing newline)")
        try:
            records = list(csv.reader(io.StringIO(text, newline="")))
        except csv.Error:
            raise LedgerCorrupt("ledger CSV does not parse") from None
        if not records or records[0] != COLUMNS:
            raise LedgerCorrupt("ledger header does not match the expected columns")
        for n, rec in enumerate(records[1:], start=2):
            if len(rec) != len(COLUMNS):
                raise LedgerCorrupt(f"ledger line {n} has {len(rec)} fields, expected {len(COLUMNS)}")

    # ---- read/write ------------------------------------------------------
    def rows(self) -> list[dict]:
        self.validate()
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    def append(self, row: dict) -> None:
        unknown = set(row) - set(COLUMNS)
        if unknown:
            raise ValueError(f"unknown ledger columns: {sorted(unknown)}")
        self.ensure_header()
        clean = {c: str(row.get(c, "")).replace("\r", " ").replace("\n", " ") for c in COLUMNS}
        buf = io.StringIO()
        csv.DictWriter(buf, fieldnames=COLUMNS).writerow(clean)
        _fsync_write(self.path, buf.getvalue())  # one write, flush, fsync

    # ---- accounting ------------------------------------------------------
    def cumulative_spent_for(self, header_used) -> object:
        used = _as_int(header_used)
        base = load_baseline(self.baseline_path)
        if used is None or base is None:
            return UNKNOWN
        return used - base["used"]

    def last_known_used(self):
        """Highest x-requests-used seen in the ledger, falling back to the baseline."""
        values = [_as_int(r["header_used"]) for r in self.rows()]
        values = [v for v in values if v is not None]
        base = load_baseline(self.baseline_path)
        if base is not None:
            values.append(base["used"])
        return max(values) if values else None

    @staticmethod
    def _unresolved_indices(rows: list[dict]) -> list[int]:
        """Indices of uncertain rows with no later RECONCILED row for the same fingerprint."""
        out = []
        for i, r in enumerate(rows):
            if r["status_code"] in (ST_RECONCILED, ST_RECONCILE_MISMATCH) or not is_uncertain(r):
                continue
            later = rows[i + 1:]
            if not any(x["fingerprint"] == r["fingerprint"] and x["status_code"] == ST_RECONCILED for x in later):
                out.append(i)
        return out

    def unresolved(self) -> list[dict]:
        rows = self.rows()
        return [rows[i] for i in self._unresolved_indices(rows)]

    def fingerprint_state(self, fingerprint: str) -> dict:
        rows = self.rows()
        unresolved = [rows[i] for i in self._unresolved_indices(rows) if rows[i]["fingerprint"] == fingerprint]
        return {
            "has_200": any(r["fingerprint"] == fingerprint and r["status_code"] == "200" for r in rows),
            "unresolved": unresolved,
        }

    def summary(self, budgets: dict) -> dict:
        """Pessimistic spend per stage, reserve use and global committed spend."""
        rows = self.rows()
        unresolved = set(self._unresolved_indices(rows))
        per_stage: dict[str, int] = {}
        reserve_by_stage: dict[str, int] = {}
        for i, r in enumerate(rows):
            known = _as_int(r["actual_cost"]) or 0
            if i in unresolved:
                cost = max(known, _as_int(r["expected_cost_max"]) or 0)
            else:
                cost = known
            per_stage[r["stage"]] = per_stage.get(r["stage"], 0) + cost
            for part in r["notes"].split(";"):
                k, _, v = part.strip().partition("=")
                if k == "reserve_draw" and _as_int(v) is not None:
                    reserve_by_stage[r["stage"]] = reserve_by_stage.get(r["stage"], 0) + _as_int(v)
        reserve_used = sum(reserve_by_stage.values())
        cumulative = [_as_int(r["cumulative_spent"]) for r in rows]
        cumulative = [c for c in cumulative if c is not None]
        header_spent = max(cumulative) if cumulative else 0
        pessimistic_extra = 0
        for i in unresolved:
            r = rows[i]
            known = _as_int(r["actual_cost"]) or 0
            pessimistic_extra += max(known, _as_int(r["expected_cost_max"]) or 0)
        # Stage-attributed spend can exceed header spend (pessimism); use the larger.
        committed = max(header_spent + pessimistic_extra, sum(per_stage.values()))
        return {
            "per_stage": per_stage,
            "reserve_used": reserve_used,
            "reserve_by_stage": reserve_by_stage,
            "header_spent": header_spent,
            "pessimistic_extra": pessimistic_extra,
            "committed": committed,
            "unresolved": [rows[i] for i in sorted(unresolved)],
        }
