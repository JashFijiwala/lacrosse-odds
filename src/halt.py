"""HALT file handling. A HALT blocks every paid request until it is cleared
through `cli clear-halt` with a recorded resolution."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VALID_CODES = {
    "UNEXPECTED_COST",
    "NON_200",
    "MALFORMED_BODY",
    "MISSING_HEADERS",
    "PERSISTENCE_FAILED",
    "LEDGER_WRITE_FAILED",
    "PENDING_FOUND",
    "NO_RESPONSE",
    "LEDGER_CORRUPT",
    "BASELINE_CORRUPT",
}

# Codes whose clearance requires a RECONCILED ledger row for the HALT's fingerprint.
NEEDS_RECONCILE = {
    "NO_RESPONSE",
    "PERSISTENCE_FAILED",
    "LEDGER_WRITE_FAILED",
    "PENDING_FOUND",
    "NON_200",
    "MALFORMED_BODY",
    "MISSING_HEADERS",
    "UNEXPECTED_COST",
}

REQUIRED_RESOLUTION = {
    "UNEXPECTED_COST": "Run `reconcile --fingerprint <fp>`, explain the cost difference, then clear-halt.",
    "NON_200": "Inspect the saved body/sidecar, run reconcile, then clear-halt with the cause.",
    "MALFORMED_BODY": "Inspect the saved body, run reconcile, then clear-halt with the cause.",
    "MISSING_HEADERS": "Run reconcile to establish the true usage, then clear-halt.",
    "PERSISTENCE_FAILED": "Credits were consumed. Recover the body from the emergency copy if possible, "
                          "run reconcile, then clear-halt. Do NOT retry the request blindly.",
    "LEDGER_WRITE_FAILED": "The raw file was saved but the ledger row was not. Run reconcile, then clear-halt.",
    "PENDING_FOUND": "A request's outcome is unknown. Run reconcile for the pending fingerprint, then clear-halt.",
    "NO_RESPONSE": "The request may have been charged. Run reconcile, then clear-halt. Never retry blindly.",
    "LEDGER_CORRUPT": "Repair credit_ledger.csv so it validates (do not discard rows), then clear-halt.",
    "BASELINE_CORRUPT": "Repair baseline.json so used/remaining are ints, then clear-halt.",
}

# Set when a HALT could not be written; blocks every further call in this process.
_PROCESS_HALTED = {"flag": False, "code": None}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def process_halted() -> bool:
    return _PROCESS_HALTED["flag"]


def reset_process_flag_for_tests() -> None:
    _PROCESS_HALTED["flag"] = False
    _PROCESS_HALTED["code"] = None


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_event(events_path: Path, event: dict) -> None:
    line = json.dumps({"ts": utc_now(), **event}, sort_keys=True) + "\n"
    with open(events_path, "a", encoding="utf-8", newline="") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read_halt(path: Path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # An unreadable HALT is still a HALT.
        return {"reason_code": "UNREADABLE", "reason_text": "HALT file exists but is unreadable"}


def write_halt(path: Path, code: str, text: str, fingerprint: str = "", evidence=None,
               supersede: bool = False, history_path: Path | None = None) -> dict | None:
    """Create the HALT file. An existing HALT is kept (the first cause wins)
    unless supersede=True, in which case the old one is archived to history first.

    If the HALT cannot be written, print loudly to stderr and set the in-process flag.
    """
    if code not in VALID_CODES:
        raise ValueError(f"invalid HALT code {code!r}")
    path = Path(path)
    halt = {
        "ts": utc_now(),
        "reason_code": code,
        "reason_text": text,
        "fingerprint": fingerprint,
        "evidence": [str(e) for e in (evidence or [])],
        "required_resolution": REQUIRED_RESOLUTION[code],
    }
    try:
        if path.exists():
            if not supersede:
                return read_halt(path)
            if history_path is not None:
                old = read_halt(path)
                with open(history_path, "a", encoding="utf-8", newline="") as fh:
                    fh.write(json.dumps({"superseded_ts": utc_now(), "halt": old, "superseded_by": code}) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
        atomic_write_text(path, json.dumps(halt, indent=2))
        return halt
    except Exception as exc:  # noqa: BLE001 - must never fail silently
        _PROCESS_HALTED["flag"] = True
        _PROCESS_HALTED["code"] = code
        print(
            "\n" + "!" * 72 + f"\n!!! HALT {code} COULD NOT BE WRITTEN ({type(exc).__name__}).\n"
            "!!! All further API calls in this process are blocked.\n"
            f"!!! Reason: {text}\n" + "!" * 72 + "\n",
            file=sys.stderr,
        )
        return None
