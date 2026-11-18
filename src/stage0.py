"""Stage 0 report. Makes NO network call.

Reads pilot/ledger/baseline.json and the raw /v4/sports response that
bootstrap-baseline saved, then reports:
  - whether remaining credits >= 500 (from the baseline),
  - lacrosse_ncaa: key, title, active,
  - any other sport key containing "lacrosse".

Run: .\\.venv\\Scripts\\python.exe -m src.stage0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ledger import BaselineCorrupt, Ledger, LedgerCorrupt, load_baseline

SPORT_KEY = "lacrosse_ncaa"
MIN_REMAINING = 500


def analyze(baseline: dict, sports, all_param_used: bool):
    """Pure function: returns (lines, status) with status PASS, FAIL or INCONCLUSIVE."""
    lines = [f"Baseline: used={baseline['used']} remaining={baseline['remaining']} ts={baseline['ts']}"]
    status = "PASS"

    if baseline["remaining"] >= MIN_REMAINING:
        lines.append(f"Check remaining >= {MIN_REMAINING}: PASS ({baseline['remaining']})")
    else:
        lines.append(f"Check remaining >= {MIN_REMAINING}: FAIL ({baseline['remaining']})")
        status = "FAIL"

    if not isinstance(sports, list) or not all(isinstance(s, dict) for s in sports):
        lines.append("Saved /v4/sports response is not a list of objects: cannot check sport keys.")
        return lines, "FAIL"

    lines.append(f"Sports in saved response: {len(sports)} "
                 f"(request {'included' if all_param_used else 'did NOT include'} all=true)")

    def describe(s):
        active = s.get("active")
        return (f"key={s.get('key', '?')} title={s.get('title', '?')} "
                f"active={'unknown' if active is None else str(active).lower()}")

    target = [s for s in sports if s.get("key") == SPORT_KEY]
    if target:
        lines.append(f"{SPORT_KEY}: LISTED ({describe(target[0])})")
    elif all_param_used:
        lines.append(f"{SPORT_KEY}: NOT LISTED in the saved response")
        status = "FAIL"
    else:
        lines.append(f"{SPORT_KEY}: not listed, but the request lacked all=true, so out-of-season sports are "
                     "omitted and this is inconclusive")
        if status == "PASS":
            status = "INCONCLUSIVE"

    others = [s for s in sports if "lacrosse" in str(s.get("key", "")).lower() and s.get("key") != SPORT_KEY]
    lines.append(f"Other sport keys containing 'lacrosse': {len(others)}")
    lines.extend(f"  {describe(s)}" for s in others)
    lines.append(f"STAGE 0: {status}")
    return lines, status


def _bootstrap_row(ledger: Ledger):
    for r in ledger.rows():
        if r["stage"] == "BOOTSTRAP" and r["endpoint"] == "/v4/sports" and r["status_code"] == "200":
            return r
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stage0", description="Stage 0 report (no network)")
    ap.add_argument("--root", default=".", help="project root (default: current directory)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    ledger_dir = root / "pilot" / "ledger"

    if not (ledger_dir / "baseline.json").exists():
        print("run bootstrap-baseline first")
        return 1
    try:
        baseline = load_baseline(ledger_dir / "baseline.json")
        ledger = Ledger(ledger_dir / "credit_ledger.csv", ledger_dir / "baseline.json")
        row = _bootstrap_row(ledger)
    except BaselineCorrupt as exc:
        print(f"baseline.json does not validate: {exc}")
        return 1
    except LedgerCorrupt as exc:
        print(f"ledger does not validate: {exc}")
        return 1
    if row is None:
        print("No successful bootstrap row in the ledger; cannot find the saved /v4/sports response.")
        return 1

    raw_path = Path(row["raw_file"])
    if not raw_path.exists():
        raw_path = root / "pilot" / "raw" / raw_path.name
    try:
        sports = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"Saved /v4/sports response {raw_path.name} is missing or not valid JSON.")
        return 1
    try:
        params = json.loads(row["params_redacted_json"] or "{}")
    except ValueError:
        params = {}
    all_used = str(params.get("all", "")).lower() == "true"

    lines, status = analyze(baseline, sports, all_used)
    print("\n".join(lines))
    if not all_used:
        print("NOTE: bootstrap_baseline() sent GET /v4/sports without all=true. Sport-key findings above "
              "describe in-season sports only.")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
