"""Guarded client for The Odds API. This module is the ONLY code path that
may call the API.

Safety properties (see docs/PILOT_DESIGN.md):
- No paid request before a validated baseline.json exists.
- Pre-flight refusals (HALT, pending.json, cap, stage budget, duplicates) happen
  before any network call.
- Write-ahead pending.json before every paid call; removed only after the
  ledger row is safely written.
- No retries of any kind. Any anomaly writes a HALT and raises.
- The API key is added to the query at the last moment and never written to
  params, ledger, sidecars, logs or exception messages.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter

from . import halt as H
from .ledger import (
    ST_NO_RESPONSE,
    ST_PERSIST_FAILED,
    ST_RECONCILE_MISMATCH,
    ST_RECONCILED,
    UNKNOWN,
    BaselineCorrupt,
    Ledger,
    LedgerCorrupt,
    is_uncertain,
    load_baseline,
)

logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
log = logging.getLogger("odds_client")

HARD_CAP = 500
FREE_ENDPOINTS = {"/v4/sports"}
FREE_PARAMS_ALLOWLIST = {"all"}
TIMEOUT_S = 30
QUOTA_HEADERS = ("x-requests-last", "x-requests-used", "x-requests-remaining")
_SECRET_PARAM_RE = re.compile(r"key|token|secret|password", re.I)


class OddsClientError(Exception):
    """Base error. Messages never contain the API key or a request URL."""


class OddsClientRefused(OddsClientError):
    """Pre-flight refusal: no network call was made."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def redact_params(params: dict) -> dict:
    return {k: v for k, v in params.items() if not _SECRET_PARAM_RE.search(str(k))}


def fingerprint(endpoint: str, redacted_params: dict, stage: str) -> str:
    canon = endpoint + json.dumps(redacted_params, sort_keys=True, separators=(",", ":")) + stage
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    budgets = {str(k): int(v) for k, v in cfg["stage_budgets"].items()}
    cap = min(int(cfg["credit_cap"]), HARD_CAP)
    if sum(budgets.values()) > cap:
        raise ValueError("stage budgets exceed the credit cap")
    cfg["stage_budgets"] = budgets
    cfg["credit_cap"] = cap
    return cfg


class OddsClient:
    def __init__(self, root: str | Path = ".", session: requests.Session | None = None,
                 env_file: str | Path | None = None):
        self.root = Path(root).resolve()
        self.config = load_config(self.root / "pilot" / "config" / "pilot.yaml")
        self.cap = self.config["credit_cap"]
        self.budgets = self.config["stage_budgets"]
        self.base_url = str(self.config["base_url"]).rstrip("/")

        self.raw_dir = self.root / "pilot" / "raw"
        self.ledger_dir = self.root / "pilot" / "ledger"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.ledger_dir / "credit_ledger.csv"
        self.baseline_path = self.ledger_dir / "baseline.json"
        self.pending_path = self.ledger_dir / "pending.json"
        self.halt_path = self.ledger_dir / "HALT"
        self.history_path = self.ledger_dir / "halt_history.jsonl"
        self.events_path = self.ledger_dir / "events.log"
        self.ledger = Ledger(self.ledger_path, self.baseline_path)
        self._env_file = Path(env_file) if env_file else self.root / ".env"
        self._session = session if session is not None else self._make_session()
        self._startup_checks()

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _make_session() -> requests.Session:
        s = requests.Session()
        adapter = HTTPAdapter(max_retries=0)  # no automatic retries of any kind
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        return s

    def _startup_checks(self) -> None:
        try:
            self.ledger.validate()
        except LedgerCorrupt as exc:
            self._halt("LEDGER_CORRUPT", str(exc), evidence=[self.ledger_path])
        try:
            load_baseline(self.baseline_path)
        except BaselineCorrupt as exc:
            self._halt("BASELINE_CORRUPT", str(exc), evidence=[self.baseline_path])
        if self.pending_path.exists():
            fp = ""
            try:
                fp = json.loads(self.pending_path.read_text(encoding="utf-8")).get("fingerprint", "")
            except (OSError, ValueError):
                pass
            self._halt("PENDING_FOUND", "pending.json present at startup: a request's outcome is unknown",
                       fingerprint=fp, evidence=[self.pending_path])

    def _api_key(self) -> str:
        if self._env_file.exists():
            load_dotenv(self._env_file, override=False)
        key = os.environ.get("ODDS_API_KEY", "").strip()
        if not key:
            raise OddsClientError("ODDS_API_KEY is not set (environment or .env); no request was made")
        return key

    # ---------------------------------------------------------------- helpers
    def _event(self, event: dict) -> None:
        try:
            H.append_event(self.events_path, event)
        except OSError:
            log.warning("could not append to events.log")

    def _halt(self, code, text, fingerprint="", evidence=None, supersede=False):
        result = H.write_halt(self.halt_path, code, text, fingerprint, evidence,
                              supersede=supersede, history_path=self.history_path)
        self._event({"event": "halt", "reason_code": code, "fingerprint": fingerprint, "text": text})
        return result

    def _refuse(self, reason: str, **ctx):
        self._event({"event": "refused", "reason": reason, **ctx})
        raise OddsClientRefused(reason)

    def _check_not_halted(self) -> None:
        if H.process_halted():
            self._refuse("in-process HALT flag set (a HALT could not be written); restart after resolving")
        existing = H.read_halt(self.halt_path)
        if existing is not None:
            self._refuse(f"HALT {existing.get('reason_code')} is active; resolve it with reconcile/clear-halt")

    def _require_baseline(self) -> dict:
        try:
            base = load_baseline(self.baseline_path)
        except BaselineCorrupt as exc:
            self._halt("BASELINE_CORRUPT", str(exc), evidence=[self.baseline_path])
            self._refuse("baseline.json is corrupt")
        if base is None:
            self._refuse("baseline.json missing: run bootstrap-baseline before any request")
        return base

    def _write_pending(self, data: dict) -> None:
        H.atomic_write_text(self.pending_path, json.dumps(data, indent=2))

    def _remove_pending(self) -> None:
        try:
            os.remove(self.pending_path)
        except FileNotFoundError:
            pass

    def _save_raw(self, stage: str, endpoint: str, date, body: bytes) -> Path:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", endpoint.removeprefix("/v4/")).strip("-") or "root"
        date_part = re.sub(r"[^0-9A-Za-z]", "", str(date)) if date else "na"
        base = f"{stage}_{_stamp()}_{slug}_{date_part}"
        final = self.raw_dir / f"{base}.json"
        n = 1
        while final.exists():
            final = self.raw_dir / f"{base}_{n}.json"
            n += 1
        fd, tmp = tempfile.mkstemp(prefix=".raw_", suffix=".tmp", dir=str(self.raw_dir))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, final)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return final

    def _base_row(self, stage, purpose, endpoint, redacted, fp, emin, emax, notes) -> dict:
        return {
            "ts_utc": H.utc_now(),
            "stage": stage,
            "purpose": purpose,
            "endpoint": endpoint,
            "params_redacted_json": json.dumps(redacted, sort_keys=True),
            "fingerprint": fp,
            "expected_cost_min": emin,
            "expected_cost_max": emax,
            "header_last": "",
            "header_used": "",
            "header_remaining": "",
            "actual_cost": UNKNOWN,
            "cumulative_spent": UNKNOWN,
            "status_code": "",
            "raw_file": "",
            "notes": ";".join(notes),
        }

    # ------------------------------------------------------------- public API
    def request(self, endpoint: str, params: dict, expected_cost=(0, 0), stage: str = "",
                purpose: str = "", use_reserve: bool = False, allow_duplicate: bool = False) -> dict:
        """Make one paid API call after full pre-flight. Returns
        {'data', 'raw_file', 'actual_cost', 'headers', 'fingerprint'}."""
        # --- pre-flight: nothing below touches the network until _send_and_persist
        self._check_not_halted()
        self._require_baseline()
        if self.pending_path.exists():
            self._refuse("pending.json exists: a previous request's outcome is unknown")
        try:
            self.ledger.validate()
        except LedgerCorrupt as exc:
            self._halt("LEDGER_CORRUPT", str(exc), evidence=[self.ledger_path])
            self._refuse("ledger is corrupt")
        try:
            emin, emax = (int(expected_cost[0]), int(expected_cost[1]))
        except (TypeError, ValueError, IndexError):
            self._refuse("expected_cost must be a (min, max) pair of ints")
        if not 0 <= emin <= emax:
            self._refuse("expected_cost must satisfy 0 <= min <= max")
        if stage not in self.budgets or stage == "RESERVE":
            self._refuse(f"unknown stage {stage!r}; RESERVE is drawn on via use_reserve=True")
        if not isinstance(params, dict):
            self._refuse("params must be a dict")
        if any(_SECRET_PARAM_RE.search(str(k)) for k in params):
            self._refuse("params must not contain credentials; the key is added internally")
        if not endpoint.startswith("/v4/"):
            self._refuse("endpoint must start with /v4/")

        redacted = redact_params(params)
        fp = fingerprint(endpoint, redacted, stage)
        state = self.ledger.fingerprint_state(fp)
        if state["unresolved"]:
            self._refuse("fingerprint has an UNKNOWN/PERSIST_FAILED row; reconcile it first", fingerprint=fp)
        notes = []
        if state["has_200"]:
            if not allow_duplicate:
                self._refuse("duplicate fingerprint already has a status-200 row (pass allow_duplicate=True)",
                             fingerprint=fp)
            notes.append("allow_duplicate=1")
            self._event({"event": "duplicate_allowed", "fingerprint": fp})

        summary = self.ledger.summary(self.budgets)
        if summary["committed"] + emax > self.cap:
            self._refuse(f"cap: committed {summary['committed']} + max {emax} > cap {self.cap}", fingerprint=fp)
        stage_own = summary["per_stage"].get(stage, 0) - summary["reserve_by_stage"].get(stage, 0)
        overflow = min(emax, stage_own + emax - self.budgets[stage])
        if overflow > 0:
            if not use_reserve:
                self._refuse(f"stage {stage} budget {self.budgets[stage]} would be exceeded "
                             f"(spent {stage_own} + max {emax})", fingerprint=fp)
            reserve_left = self.budgets.get("RESERVE", 0) - summary["reserve_used"]
            if overflow > reserve_left:
                self._refuse(f"RESERVE insufficient: need {overflow}, left {reserve_left}", fingerprint=fp)
            notes.append(f"reserve_draw={overflow}")
            self._event({"event": "reserve_draw", "stage": stage, "amount": overflow, "fingerprint": fp})

        key = self._api_key()
        prev_used = self.ledger.last_known_used()
        try:
            self._write_pending({
                "fingerprint": fp, "stage": stage, "endpoint": endpoint, "params_redacted": redacted,
                "expected_cost_min": emin, "expected_cost_max": emax, "purpose": purpose, "ts": H.utc_now(),
            })
        except OSError:
            self._refuse("could not write pending.json; no request was made")
        return self._send_and_persist(endpoint, params, redacted, fp, stage, purpose, emin, emax, notes,
                                      key, paid=True, prev_used=prev_used)

    def bootstrap_baseline(self) -> dict:
        """Zero-cost /v4/sports call that records the starting usage in baseline.json."""
        if H.process_halted():
            self._refuse("in-process HALT flag set")
        if self.baseline_path.exists():
            self._refuse("baseline.json already exists; bootstrap refused")
        existing = H.read_halt(self.halt_path)
        if existing is not None:
            self._refuse(f"HALT {existing.get('reason_code')} is active")
        captured = {}

        def hook(row, q):
            row["cumulative_spent"] = 0
            captured.update(q)
            return row

        result = self._free_call("BOOTSTRAP", "bootstrap baseline", row_hook=hook)
        baseline = {"used": captured["used"], "remaining": captured["remaining"], "ts": H.utc_now()}
        try:
            H.atomic_write_text(self.baseline_path, json.dumps(baseline, indent=2))
        except OSError:
            self._halt("BASELINE_CORRUPT", "baseline.json could not be written after bootstrap",
                       evidence=[result["raw_file"]])
            raise OddsClientError("baseline.json could not be written; HALT BASELINE_CORRUPT") from None
        self._event({"event": "baseline_written", **baseline})
        return baseline

    def reconcile(self, fp: str) -> dict:
        """Zero-cost /v4/sports call that attributes any unexplained usage to `fp`.
        Allowed while a HALT exists."""
        if H.process_halted():
            self._refuse("in-process HALT flag set")
        try:
            base = load_baseline(self.baseline_path)
            rows = self.ledger.rows()
        except (BaselineCorrupt, LedgerCorrupt) as exc:
            self._refuse(f"cannot reconcile: {exc}")
        pending = None
        if self.pending_path.exists():
            try:
                pending = json.loads(self.pending_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pending = None

        unresolved = [r for r in self.ledger.unresolved() if r["fingerprint"] == fp]
        matching = [r for r in rows if r["fingerprint"] == fp and r["status_code"] not in
                    (ST_RECONCILED, ST_RECONCILE_MISMATCH)]
        if unresolved:
            target = unresolved[-1]
        elif matching:
            target = matching[-1]
        elif pending and pending.get("fingerprint") == fp:
            target = {"stage": pending.get("stage", ""), "expected_cost_max": pending.get("expected_cost_max", 0),
                      "status_code": "PENDING", "raw_file": "", "notes": "", "actual_cost": UNKNOWN}
        else:
            self._refuse("fingerprint not found in ledger or pending.json")
        expected_max = _as_int(target["expected_cost_max"]) or 0
        if base is None and expected_max != 0:
            self._refuse("baseline.json missing; only zero-cost fingerprints can be reconciled")

        unrecoverable = self._is_unrecoverable(target)
        prev_used = self.ledger.last_known_used()
        outcome = {}

        def hook(row, q):
            diff = q["used"] - prev_used if prev_used is not None else None
            mismatch = diff is not None and (diff < 0 or diff > expected_max)
            notes = [f"reconciles={fp}", f"prior_status={target['status_code']}",
                     f"unexplained_diff={diff if diff is not None else UNKNOWN}",
                     f"pessimistic_estimate={expected_max}", "reconcile_call_cost=0"]
            if unrecoverable:
                notes.append(f"refetch_of={fp}")
            if mismatch:
                notes.append("MISMATCH")
            row.update({
                "stage": target["stage"],
                "purpose": "reconcile",
                "fingerprint": fp,
                "actual_cost": max(diff, 0) if diff is not None else 0,
                "status_code": ST_RECONCILE_MISMATCH if mismatch else ST_RECONCILED,
                "notes": ";".join(notes),
            })
            outcome.update(diff=diff, mismatch=mismatch, used=q["used"])
            return row

        result = self._free_call("RECONCILE", f"reconcile {fp[:12]}", row_hook=hook)
        if pending and pending.get("fingerprint") == fp:
            self._remove_pending()
        if outcome["mismatch"]:
            self._halt("UNEXPECTED_COST",
                       f"reconcile found unexplained usage {outcome['diff']} vs pessimistic estimate {expected_max}",
                       fingerprint=fp, evidence=[result["raw_file"], self.ledger_path], supersede=True)
            raise OddsClientError(f"reconcile mismatch for {fp[:12]}: unexplained usage {outcome['diff']} "
                                  f"exceeds estimate {expected_max}; HALT UNEXPECTED_COST")
        return {"fingerprint": fp, "unexplained_diff": outcome["diff"], "unrecoverable": unrecoverable,
                "raw_file": result["raw_file"]}

    def _is_unrecoverable(self, target: dict) -> bool:
        if target["status_code"] == ST_NO_RESPONSE:
            return True
        if target["raw_file"] and target["raw_file"] != "SAVE_FAILED":
            return not Path(target["raw_file"]).exists()
        evidence = (H.read_halt(self.halt_path) or {}).get("evidence", [])
        for part in str(target.get("notes", "")).split(";"):
            k, _, v = part.partition("=")
            if k == "temp_copy" and v:
                evidence.append(v)
        # A body copy (raw file or temp copy) counts; the emergency metadata record and pending.json do not.
        bodies = [p for p in evidence if Path(p).exists() and not Path(p).name.startswith("emergency_")
                  and Path(p).suffix == ".json" and Path(p).name != "pending.json"]
        return not bodies

    def clear_halt(self, reason_code: str, resolution: str) -> dict:
        current = H.read_halt(self.halt_path)
        if current is None:
            self._refuse("no HALT is active")
        if current.get("reason_code") != reason_code:
            self._refuse(f"reason code {reason_code!r} does not match the active HALT "
                         f"{current.get('reason_code')!r}")
        resolution = (resolution or "").strip()
        if len(resolution) < 20:
            self._refuse("resolution text must be at least 20 characters")
        fp = current.get("fingerprint", "")

        if reason_code in H.NEEDS_RECONCILE:
            try:
                rows = self.ledger.rows()
            except LedgerCorrupt as exc:
                self._refuse(f"ledger does not validate: {exc}")
            if not fp or not any(r["fingerprint"] == fp and r["status_code"] == ST_RECONCILED
                                 and r["ts_utc"] >= current.get("ts", "") for r in rows):
                self._refuse("no RECONCILED ledger row for the HALT's fingerprint; run reconcile first")

        if reason_code == "PERSISTENCE_FAILED":
            evidence = [Path(p) for p in current.get("evidence", [])]
            accounted = any(p.exists() for p in evidence)
            if not accounted:
                if "unrecoverable" not in resolution.lower():
                    self._refuse("neither the emergency record nor a raw artifact exists; the resolution must "
                                 "state the response is unrecoverable")
                rows = self.ledger.rows()
                refetch = max([_as_int(r["expected_cost_max"]) or 0 for r in rows
                               if r["fingerprint"] == fp and r["status_code"] == ST_PERSIST_FAILED] or [0])
                committed = self.ledger.summary(self.budgets)["committed"]
                if committed + refetch > self.cap:
                    self._refuse(f"re-fetch ({refetch}) would exceed the cap (committed {committed})")

        if reason_code == "LEDGER_CORRUPT":
            try:
                self.ledger.validate()
            except LedgerCorrupt as exc:
                self._refuse(f"ledger still does not validate: {exc}")
        if reason_code == "BASELINE_CORRUPT":
            try:
                if load_baseline(self.baseline_path) is None:
                    self._refuse("baseline.json is missing")
            except BaselineCorrupt:
                self._refuse("baseline.json still does not validate")

        record = {"cleared_ts": H.utc_now(), "halt": current, "resolution": resolution}
        try:
            with open(self.history_path, "a", encoding="utf-8", newline="") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            H.append_event(self.events_path, {"event": "halt_cleared", "reason_code": reason_code,
                                              "fingerprint": fp, "resolution": resolution})
        except OSError:
            raise OddsClientError("could not record the HALT resolution; HALT stays in place") from None
        os.remove(self.halt_path)  # deleted last
        return record

    # -------------------------------------------------------------- internals
    def _free_call(self, stage: str, purpose: str, params: dict | None = None, row_hook=None) -> dict:
        endpoint = "/v4/sports"
        params = dict(params or {})
        if endpoint not in FREE_ENDPOINTS:
            self._refuse("free path is restricted to FREE_ENDPOINTS")
        if set(params) - FREE_PARAMS_ALLOWLIST:
            self._refuse("free path accepts only the params allowlist {'all'}")
        key = self._api_key()
        fp = fingerprint(endpoint, params, stage)
        return self._send_and_persist(endpoint, params, dict(params), fp, stage, purpose, 0, 0, [], key,
                                      paid=False, prev_used=None, row_hook=row_hook)

    def _send_and_persist(self, endpoint, params, redacted, fp, stage, purpose, emin, emax, notes, key,
                          paid, prev_used, row_hook=None) -> dict:
        url = self.base_url + endpoint
        send_params = dict(params)
        send_params["apiKey"] = key  # added at the last moment; never stored
        error_type = None
        resp = None
        try:
            resp = self._session.get(url, params=send_params, timeout=TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - any failure here is NO_RESPONSE
            error_type = type(exc).__name__
        finally:
            send_params = None
            key = None
        if error_type is not None:
            # Raised outside the except block so no exception context (which may hold the URL) is chained.
            self._on_no_response(endpoint, redacted, fp, stage, purpose, emin, emax, notes, error_type, paid)
            raise OddsClientError(f"{error_type} calling {endpoint}: outcome unknown, HALT NO_RESPONSE written; "
                                  "do not retry, run reconcile") from None

        status = int(resp.status_code)
        body = resp.content or b""
        hdrs = {str(k).lower(): v for k, v in resp.headers.items()}
        quota = {h: hdrs.get(h) for h in QUOTA_HEADERS}
        last, used, remaining = (_as_int(quota[h]) for h in QUOTA_HEADERS)
        headers_ok = None not in (last, used, remaining)
        sha = hashlib.sha256(body).hexdigest()
        resp = None

        row = self._base_row(stage, purpose, endpoint, redacted, fp, emin, emax, notes)
        row.update({
            "header_last": quota["x-requests-last"] or "",
            "header_used": quota["x-requests-used"] or "",
            "header_remaining": quota["x-requests-remaining"] or "",
            "actual_cost": last if last is not None else UNKNOWN,
            "cumulative_spent": self.ledger.cumulative_spent_for(used),
        })

        # (1) raw body
        try:
            raw_path = self._save_raw(stage, endpoint, params.get("date"), body)
        except Exception as exc:  # noqa: BLE001
            failure = type(exc).__name__
        else:
            failure = None
        if failure is not None:
            self._on_save_failed(row, status, quota, last, body, sha, emin, emax, fp, endpoint, paid, failure)

        # (2) sidecar
        sidecar = raw_path.with_suffix(".meta.json")
        sidecar_ok = True
        try:
            H.atomic_write_text(sidecar, json.dumps({
                "ts_utc": row["ts_utc"], "stage": stage, "endpoint": endpoint, "params_redacted": redacted,
                "fingerprint": fp, "status_code": status, "quota_headers": quota,
                "expected_cost": [emin, emax], "body_bytes": len(body), "body_sha256": sha,
            }, indent=2, sort_keys=True))
        except OSError:
            sidecar_ok = False

        # validation
        problems = []
        data = None
        if status != 200:
            problems.append(("NON_200", f"HTTP {status}"))
        if not headers_ok:
            problems.append(("MISSING_HEADERS", f"quota headers missing or non-integer: {quota}"))
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            problems.append(("MALFORMED_BODY", "body is not valid JSON"))
        if last is not None and not emin <= last <= emax:
            problems.append(("UNEXPECTED_COST", f"x-requests-last={last} outside expected [{emin}, {emax}]"))
        if last is not None and used is not None and prev_used is not None and used - prev_used != last:
            problems.append(("UNEXPECTED_COST",
                             f"x-requests-last={last} disagrees with change in x-requests-used ({used - prev_used})"))
        if not sidecar_ok:
            problems.append(("PERSISTENCE_FAILED", "sidecar .meta.json could not be written"))

        row["raw_file"] = str(raw_path)
        row["status_code"] = str(status)
        if not sidecar_ok:
            row["status_code"] = ST_PERSIST_FAILED
            row["notes"] = ";".join(filter(None, [row["notes"], f"http_status={status}", "sidecar_failed=1"]))
        if row_hook is not None and not problems:
            row = row_hook(row, {"last": last, "used": used, "remaining": remaining})

        # (3) ledger row
        try:
            self.ledger.append(row)
        except Exception as exc:  # noqa: BLE001
            failure = type(exc).__name__
        else:
            failure = None
        if failure is not None:
            self._halt("LEDGER_WRITE_FAILED", f"ledger append failed ({failure}) after raw file was saved",
                       fingerprint=fp, evidence=[raw_path, sidecar, self.pending_path])
            raise OddsClientError(f"ledger append failed for {endpoint} after the response was saved to "
                                  f"{raw_path.name}; credits may have been consumed; HALT LEDGER_WRITE_FAILED; "
                                  "pending.json kept") from None

        # (4) pending
        if paid:
            self._remove_pending()

        if problems:
            order = ["NON_200", "MISSING_HEADERS", "MALFORMED_BODY", "UNEXPECTED_COST", "PERSISTENCE_FAILED"]
            code = sorted(problems, key=lambda p: order.index(p[0]))[0][0]
            text = "; ".join(t for _, t in problems)
            self._halt(code, text, fingerprint=fp, evidence=[raw_path, sidecar])
            raise OddsClientError(f"HALT {code} for {endpoint}: {text}")

        return {"data": data, "raw_file": str(raw_path), "actual_cost": last, "fingerprint": fp,
                "headers": {"last": last, "used": used, "remaining": remaining}}

    def _on_no_response(self, endpoint, redacted, fp, stage, purpose, emin, emax, notes, error_type, paid):
        row = self._base_row(stage, purpose, endpoint, redacted, fp, emin, emax,
                             list(notes) + [f"error_type={error_type}"])
        row["status_code"] = ST_NO_RESPONSE
        evidence = [self.ledger_path]
        try:
            self.ledger.append(row)
            if paid:
                self._remove_pending()
        except Exception:  # noqa: BLE001
            evidence.append(self.pending_path)
        self._halt("NO_RESPONSE", f"{error_type} on {endpoint}; charge unknown", fingerprint=fp, evidence=evidence)

    def _on_save_failed(self, row, status, quota, last, body, sha, emin, emax, fp, endpoint, paid, failure):
        ts = _stamp()
        temp_copy = Path(tempfile.gettempdir()) / f"odds_emergency_{ts}_{sha[:12]}.json"
        try:
            with open(temp_copy, "wb") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:  # noqa: BLE001
            temp_copy = None
        emergency = self.ledger_dir / f"emergency_{ts}.json"
        record = {
            "ts_utc": H.utc_now(), "endpoint": endpoint, "fingerprint": fp, "status_code": status,
            "quota_headers": quota, "expected_cost": [emin, emax], "body_bytes": len(body),
            "body_sha256": sha, "temp_copy": str(temp_copy) if temp_copy else None, "save_error": failure,
        }
        try:
            H.atomic_write_text(emergency, json.dumps(record, indent=2, sort_keys=True))
        except Exception:  # noqa: BLE001
            emergency = None
        row["raw_file"] = "SAVE_FAILED"
        row["status_code"] = ST_PERSIST_FAILED
        row["notes"] = ";".join(filter(None, [
            row["notes"], f"http_status={status}", f"emergency={emergency or 'WRITE_FAILED'}",
            f"temp_copy={temp_copy or 'WRITE_FAILED'}"]))
        try:
            self.ledger.append(row)
            if paid:
                self._remove_pending()
        except Exception:  # noqa: BLE001
            pass
        self._halt("PERSISTENCE_FAILED", f"raw body could not be saved ({failure}); credits were consumed",
                   fingerprint=fp, evidence=[p for p in (emergency, temp_copy) if p])
        raise OddsClientError(f"response for {endpoint} received but the raw body could not be saved. Credits were "
                              f"consumed (x-requests-last={last if last is not None else UNKNOWN}). "
                              "HALT PERSISTENCE_FAILED. Do NOT retry this request; reconcile first.")
