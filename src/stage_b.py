"""Stage B: close-proxy odds snapshots for the two Stage A anchors.

Fetch and report only. No overround, movement or spread analysis happens here.
Every call goes through OddsClient (stage="B"). This module imports no HTTP library.

The close-proxy snapshot is requested at anchor start minus 60 s. It is a proxy:
the returned snapshot can be earlier than requested, and a quote inside it can be
older still (see market-level last_update).

  --plan                                    steps, parameters, maximum cost; no network
  --run --step B1|B2|B3 --confirm-paid      one step; B2 needs B1 PASS, B3 needs B2 PASS
  --run --step BX --fallback-minutes 15|30 --confirm-paid
                                            one fallback snapshot after a STOP, at most one
                                            per anchor, with use_reserve=True

Run: .\\.venv\\Scripts\\python.exe -m src.stage_b --plan
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import halt as H
from .ledger import Ledger, LedgerCorrupt
from .odds_client import OddsClient, OddsClientError, fingerprint, load_config, redact_params

SPORT = "lacrosse_ncaa"
ODDS_ENDPOINT = f"/v4/historical/sports/{SPORT}/odds"
STAGE = "B"
PROXY_OFFSET_S = 60
STALE_SNAPSHOT_S = 900
STALE_QUOTE_S = 900
FALLBACK_MINUTES = (15, 30)
ISO_Z = "%Y-%m-%dT%H:%M:%SZ"

STEP_ORDER = ["B1", "B2", "B3"]
STEPS = {
    "B1": {"anchor_id": "quinnipiac_air_force", "regions": "us", "markets": "h2h,spreads,totals",
           "cost": (0, 30), "requires": None},
    "B2": {"anchor_id": "njit_vermont", "regions": "us", "markets": "h2h,spreads,totals",
           "cost": (0, 30), "requires": "B1"},
    "B3": {"anchor_id": "njit_vermont", "regions": "us,us2", "markets": "h2h",
           "cost": (0, 20), "requires": "B2"},
}
REPORTED_MARKETS = ("h2h", "spreads", "totals")

ABSENT = "absent"
COMPLETE = "complete_paired"
INCOMPLETE = "present_incomplete"
MISMATCHED = "present_mismatched"


class StageBError(Exception):
    pass


class StageBParseError(Exception):
    pass


# ------------------------------------------------------------------ time
def parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO_Z)


# --------------------------------------------------------------- anchors
def load_anchors(root: Path) -> dict:
    """anchor_id -> {'event_id', 'start'} from pilot/normalized/anchors.json (never hard-coded)."""
    path = Path(root) / "pilot" / "normalized" / "anchors.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StageBError(f"{path.name} is missing or unreadable; run Stage A first") from None
    out = {}
    for a in doc.get("anchors", []):
        start = parse_ts(a.get("commence_time_utc"))
        if a.get("found") and a.get("event_id") and start is not None:
            out[a["anchor_id"]] = {"event_id": a["event_id"], "start": start}
    return out


def step_params(step: str, anchors: dict, fallback_minutes: int = 0) -> dict:
    spec = STEPS[step]
    anchor = anchors.get(spec["anchor_id"])
    if anchor is None:
        raise StageBError(f"anchor {spec['anchor_id']} is not marked found in anchors.json")
    date = anchor["start"] - timedelta(seconds=PROXY_OFFSET_S) - timedelta(minutes=fallback_minutes)
    return {"date": fmt_ts(date), "regions": spec["regions"], "markets": spec["markets"],
            "oddsFormat": "american", "dateFormat": "iso"}


def purpose_for(step: str, fallback_minutes: int = 0) -> str:
    anchor_id = STEPS[step]["anchor_id"]
    if fallback_minutes:
        return f"stage B fallback {step} {fallback_minutes}m {anchor_id}"
    return f"stage B step {step} {anchor_id}"


def parse_purpose(purpose: str):
    parts = purpose.split()
    if len(parts) >= 4 and parts[:3] == ["stage", "B", "step"] and parts[3] in STEPS:
        return {"kind": "primary", "step": parts[3], "minutes": 0}
    if len(parts) >= 5 and parts[:3] == ["stage", "B", "fallback"] and parts[3] in STEPS:
        try:
            return {"kind": "fallback", "step": parts[3], "minutes": int(parts[4].rstrip("m"))}
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- parsing
def parse_odds_wrapper(obj):
    if not isinstance(obj, dict):
        raise StageBParseError("top-level JSON is not an object")
    data = obj.get("data")
    if not isinstance(data, list):
        raise StageBParseError("'data' is missing or not a list")
    for i, e in enumerate(data):
        if not isinstance(e, dict) or not e.get("id"):
            raise StageBParseError(f"data[{i}] is not an object with an id")
        books = e.get("bookmakers", [])
        if not isinstance(books, list):
            raise StageBParseError(f"data[{i}].bookmakers is not a list")
        for j, b in enumerate(books):
            if not isinstance(b, dict) or not b.get("key") or not isinstance(b.get("markets", []), list):
                raise StageBParseError(f"data[{i}].bookmakers[{j}] is malformed")
            for k, m in enumerate(b.get("markets", [])):
                if not isinstance(m, dict) or not m.get("key"):
                    raise StageBParseError(f"data[{i}].bookmakers[{j}].markets[{k}] is malformed")
    meta = {k: "" if obj.get(k) is None else str(obj.get(k))
            for k in ("timestamp", "previous_timestamp", "next_timestamp")}
    return meta, data


# ---------------------------------------------------------- classification
def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def classify_market(key: str, market, home: str, away: str) -> str:
    """Completeness of one bookmaker market for the anchor event (see docs/PILOT_DESIGN.md)."""
    if market is None:
        return ABSENT
    outcomes = market.get("outcomes")
    if not isinstance(outcomes, list) or not all(isinstance(o, dict) for o in outcomes):
        return INCOMPLETE
    names = [o.get("name") for o in outcomes]
    if not all(isinstance(n, str) for n in names):
        return INCOMPLETE
    teams_ok = len(outcomes) == 2 and home != away and sorted(names) == sorted([home, away])
    if key == "h2h":
        prices_ok = all(_num(o.get("price")) and abs(o["price"]) >= 100 for o in outcomes)
        return COMPLETE if teams_ok and prices_ok else INCOMPLETE
    if key == "spreads":
        if not (teams_ok and all(_num(o.get("price")) and _num(o.get("point")) for o in outcomes)):
            return INCOMPLETE
        p1, p2 = outcomes[0]["point"], outcomes[1]["point"]
        return COMPLETE if (p1 == -p2 and p1 != 0) else MISMATCHED
    if key == "totals":
        ok = (len(outcomes) == 2 and sorted(names) == ["Over", "Under"]
              and all(_num(o.get("price")) and _num(o.get("point")) for o in outcomes)
              and outcomes[0]["point"] == outcomes[1]["point"])
        return COMPLETE if ok else INCOMPLETE
    return INCOMPLETE


def find_market(bookmaker: dict, key: str):
    for m in bookmaker.get("markets", []):
        if m.get("key") == key:
            return m
    return None


# ---------------------------------------------------------------- analysis
def _as_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def cost_ok(n_events: int, actual_cost, max_cost: int) -> bool:
    """Non-empty payload must cost the maximum; 0 is acceptable only for an empty payload."""
    actual = _as_int(actual_cost)
    if actual is None:
        return False
    return actual == max_cost if n_events > 0 else actual == 0


def analyze(obj, anchor_event_id: str, anchor_start: datetime, actual_cost, max_cost: int) -> dict:
    meta, events = parse_odds_wrapper(obj)
    returned = parse_ts(meta["timestamp"])
    secs = int((anchor_start - returned).total_seconds()) if returned else None
    snap_flags = []
    if secs is not None and secs > STALE_SNAPSHOT_S:
        snap_flags.append("STALE")
    if secs is not None and secs < 0:
        snap_flags.append("POST-START")

    anchor = next((e for e in events if e.get("id") == anchor_event_id), None)
    books, quotes = [], []
    if anchor is not None:
        home, away = str(anchor.get("home_team", "")), str(anchor.get("away_team", ""))
        for b in anchor.get("bookmakers", []):
            classes = {k: classify_market(k, find_market(b, k), home, away) for k in REPORTED_MARKETS}
            books.append({"key": b["key"], **classes})
            for m in b.get("markets", []):
                lu = parse_ts(m.get("last_update"))   # market-level; bookmaker-level last_update is not used
                stale = int((returned - lu).total_seconds()) if (returned and lu) else None
                quotes.append({"bookmaker": b["key"], "market": m["key"],
                               "classification": classes.get(m["key"], "-"),
                               "last_update": m.get("last_update") or "", "staleness_seconds": stale,
                               "flag": "STALE_QUOTE" if stale is not None and stale > STALE_QUOTE_S else ""})

    counts = Counter()
    for e in events:
        for key in {b["key"] for b in e.get("bookmakers", [])}:
            counts[key] += 1

    c_ok = cost_ok(len(events), actual_cost, max_cost)
    reasons = []
    if not c_ok:
        reasons.append(f"cost check failed (actual {actual_cost}, expected {max_cost} for a non-empty payload "
                       "or 0 for an empty one)")
    if anchor is None:
        reasons.append("anchor event not present in this close-proxy snapshot")
    elif not any(b["h2h"] == COMPLETE for b in books):
        reasons.append("no bookmaker has h2h classified complete_paired for the anchor")
    return {
        "meta": meta, "seconds_to_start": secs, "snapshot_flags": snap_flags, "n_events": len(events),
        "anchor_present": anchor is not None,
        "anchor_commence_time": (anchor or {}).get("commence_time", ""),
        "books": books, "quotes": quotes, "bookmaker_event_counts": dict(sorted(counts.items())),
        "cost_ok": c_ok, "status": "PASS" if not reasons else "STOP", "reasons": reasons,
    }


# ------------------------------------------------------------------ ledger
def collect_attempts(rows) -> list[dict]:
    """Stage B attempts in ledger order, one per fingerprint, with the latest row and latest 200 row."""
    out, idx = [], {}
    for r in rows:
        if r["endpoint"] != ODDS_ENDPOINT or r["stage"] != STAGE:
            continue
        parsed = parse_purpose(r["purpose"])
        if parsed is None:
            continue
        fp = r["fingerprint"]
        if fp not in idx:
            idx[fp] = len(out)
            out.append({**parsed, "fingerprint": fp, "row": r, "row200": None})
        att = out[idx[fp]]
        att["row"] = r
        if r["status_code"] == "200":
            att["row200"] = r
    return out


def evaluate_attempt(att: dict, anchors: dict) -> dict:
    spec = STEPS[att["step"]]
    anchor = anchors.get(spec["anchor_id"])
    row = att["row200"] or att["row"]
    try:
        params = json.loads(row["params_redacted_json"] or "{}")
    except ValueError:
        params = {}
    ev = {**att, "anchor_id": spec["anchor_id"], "requested_date": params.get("date", ""),
          "max_cost": spec["cost"][1], "actual_cost": row["actual_cost"], "ledger_status": row["status_code"],
          "anchor_event_id": anchor["event_id"] if anchor else "",
          "anchor_start": fmt_ts(anchor["start"]) if anchor else "", "analysis": None}
    if att["row200"] is None:
        ev.update(status="STOP", reasons=[f"ledger status {row['status_code']}: no usable payload"])
        return ev
    if anchor is None:
        ev.update(status="STOP", reasons=["anchor missing from anchors.json"])
        return ev
    try:
        obj = json.loads(Path(row["raw_file"]).read_text(encoding="utf-8"))
        a = analyze(obj, anchor["event_id"], anchor["start"], row["actual_cost"], spec["cost"][1])
    except (OSError, ValueError, StageBParseError) as exc:
        ev.update(status="STOP", reasons=[f"saved response unusable ({type(exc).__name__}: {exc})"])
        return ev
    ev.update(analysis=a, status=a["status"], reasons=a["reasons"])
    return ev


def step_status(step: str, evaluated: list[dict]) -> str:
    mine = [e for e in evaluated if e["step"] == step]
    if not mine:
        return "NOT_RUN"
    return "PASS" if any(e["status"] == "PASS" for e in mine) else "STOP"


def fallback_used(anchor_id: str, evaluated: list[dict]) -> bool:
    return any(e["kind"] == "fallback" and e["anchor_id"] == anchor_id for e in evaluated)


def check_allowed(step: str, fallback_minutes, evaluated: list[dict]) -> None:
    """Raise StageBError if ordering or fallback rules forbid this request. No network."""
    spec = STEPS[step]
    req = spec["requires"]
    if req and step_status(req, evaluated) != "PASS":
        raise StageBError(f"{step} requires {req} to have a PASS report (currently {step_status(req, evaluated)})")
    if not fallback_minutes:
        return
    if fallback_minutes not in FALLBACK_MINUTES:
        raise StageBError(f"--fallback-minutes must be one of {FALLBACK_MINUTES}")
    primaries = [e for e in evaluated if e["step"] == step and e["kind"] == "primary" and e["row200"]]
    if not primaries or step_status(step, evaluated) != "STOP":
        raise StageBError(f"a fallback for {step} is allowed only after {step} has a STOP report from a "
                          "successful (HTTP 200) response")
    if fallback_used(spec["anchor_id"], evaluated):
        raise StageBError(f"the single fallback for anchor {spec['anchor_id']} has already been used")


# ------------------------------------------------------------------ report
def _fmt(v) -> str:
    return "-" if v in (None, "") else str(v)


def render_section(ev: dict, evaluated: list[dict]) -> str:
    label = (f"{ev['step']} fallback ({ev['minutes']} min earlier)" if ev["kind"] == "fallback"
             else f"{ev['step']} primary")
    L = [f"## {label} - close-proxy snapshot - anchor {ev['anchor_id']}", ""]
    offset = f"start minus {PROXY_OFFSET_S} s" + (f" minus {ev['minutes']} min" if ev["minutes"] else "")
    L += [f"- Snapshot label: close-proxy ({offset})",
          f"- Requested date: {_fmt(ev['requested_date'])}"]
    a = ev["analysis"]
    if a:
        m = a["meta"]
        L += [f"- Returned timestamp: {_fmt(m['timestamp'])}; previous_timestamp: {_fmt(m['previous_timestamp'])}; "
              f"next_timestamp: {_fmt(m['next_timestamp'])}",
              f"- Anchor start (anchors.json): {ev['anchor_start']}; payload commence_time: "
              f"{_fmt(a['anchor_commence_time'])}",
              f"- Seconds from returned snapshot to anchor start: {_fmt(a['seconds_to_start'])}"
              + (f" [{', '.join(a['snapshot_flags'])}]" if a["snapshot_flags"] else ""),
              f"- Events in payload: {a['n_events']}",
              f"- Anchor event {ev['anchor_event_id']} present: {'yes' if a['anchor_present'] else 'no'}"]
    L += [f"- Actual cost: {_fmt(ev['actual_cost'])} (maximum {ev['max_cost']}); cost check: "
          f"{'OK' if a and a['cost_ok'] else ('FAILED' if a else 'n/a')}; ledger status: {ev['ledger_status']}",
          f"- Step status: {ev['status']}"]
    L += [f"  - {r}" for r in ev["reasons"]]
    if a and a["anchor_present"]:
        L += ["", "### Anchor bookmakers", "", "| Bookmaker | h2h | spreads | totals |", "|---|---|---|---|"]
        L += [f"| {b['key']} | {b['h2h']} | {b['spreads']} | {b['totals']} |" for b in a["books"]] or \
             ["| (none) | - | - | - |"]
        L += ["", "### Anchor quotes (market-level last_update)", "",
              "| Bookmaker | Market | Classification | last_update | staleness_seconds | Flag |",
              "|---|---|---|---|---|---|"]
        L += [f"| {q['bookmaker']} | {q['market']} | {q['classification']} | {_fmt(q['last_update'])} | "
              f"{_fmt(q['staleness_seconds'])} | {q['flag']} |" for q in a["quotes"]] or \
             ["| (none) | - | - | - | - | |"]
    if a:
        L += ["", "### Bookmakers across payload", "", "| Bookmaker | Events quoted |", "|---|---|"]
        L += [f"| {k} | {n} |" for k, n in a["bookmaker_event_counts"].items()] or ["| (none) | 0 |"]
    if ev["status"] == "STOP":
        L += ["", "### Suggestions (not executed)", ""]
        if fallback_used(ev["anchor_id"], evaluated):
            L.append(f"- The single fallback for anchor {ev['anchor_id']} has been used. Later steps stay blocked.")
        else:
            for n in FALLBACK_MINUTES:
                L.append(f"- One fallback snapshot {n} min earlier, charged to RESERVE: "
                         f"`--run --step {ev['step']} --fallback-minutes {n} --confirm-paid`")
            L.append(f"- At most one fallback per anchor. Later steps stay blocked until {ev['step']} has a PASS report.")
    return "\n".join(L) + "\n"


def write_report(root: Path, ledger: Ledger, anchors: dict):
    evaluated = [evaluate_attempt(a, anchors) for a in collect_attempts(ledger.rows())]
    header = ("# Stage B fetch report: close-proxy snapshots\n\n"
              "Fetch and report only. No overround, movement or spread analysis. A close-proxy snapshot is "
              "requested at anchor start minus 60 s; quotes inside it can be older (see last_update).\n\n")
    sections = {e["fingerprint"]: render_section(e, evaluated) for e in evaluated}
    out = Path(root) / "pilot" / "normalized" / "stage_b_fetch_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    H.atomic_write_text(out, header + "\n".join(sections.values()))
    return evaluated, sections, out


# --------------------------------------------------------------------- CLI
def print_plan(root: Path) -> int:
    cfg = load_config(root / "pilot" / "config" / "pilot.yaml")
    try:
        anchors = load_anchors(root)
    except StageBError as exc:
        print(f"Cannot plan: {exc}. No request was made.")
        return 2
    ledger_dir = root / "pilot" / "ledger"
    try:
        evaluated = [evaluate_attempt(a, anchors)
                     for a in collect_attempts(Ledger(ledger_dir / "credit_ledger.csv",
                                                      ledger_dir / "baseline.json").rows())]
    except LedgerCorrupt:
        evaluated = []
        print("(ledger unreadable: step status unknown)")
    print(f"Stage B plan: close-proxy snapshots, stage budget {cfg['stage_budgets']['B']}")
    print(f"endpoint: GET {ODDS_ENDPOINT}")
    total = 0
    for step in STEP_ORDER:
        spec = STEPS[step]
        try:
            p = step_params(step, anchors)
            ptxt = " ".join(f"{k}={v}" for k, v in p.items())
        except StageBError as exc:
            ptxt = f"(cannot compute: {exc})"
        total += spec["cost"][1]
        print(f"  {step}  anchor={spec['anchor_id']}  {ptxt}  expected cost {spec['cost'][0]}..{spec['cost'][1]}  "
              f"max cost {spec['cost'][1]}  requires={spec['requires'] or '-'}  status={step_status(step, evaluated)}")
    print(f"total maximum: {total} credits of {cfg['stage_budgets']['B']} (Stage B budget). A fallback "
          f"({'/'.join(map(str, FALLBACK_MINUTES))} min earlier, one per anchor) is extra and uses use_reserve=True.")
    print("No request was made.")
    return 0


def run_step(root: Path, step: str, confirm_paid: bool, fallback_minutes) -> int:
    if step not in STEPS:
        print(f"Refused: unknown step {step!r}. No request was made.")
        return 2
    if not confirm_paid:
        print("Refused: --run makes a paid API call. Re-run with --confirm-paid. Use --plan to preview. "
              "No request was made.")
        return 2
    try:
        client = OddsClient(root=root)
        anchors = load_anchors(root)
        evaluated = [evaluate_attempt(a, anchors) for a in collect_attempts(client.ledger.rows())]
        check_allowed(step, fallback_minutes, evaluated)
        params = step_params(step, anchors, fallback_minutes or 0)
    except (OddsClientError, StageBError, LedgerCorrupt) as exc:
        print(f"Refused: {exc}. No request was made.")
        return 2

    spec = STEPS[step]
    # Fallbacks are RESERVE-designated. A later primary step may then overflow the Stage B budget by at
    # most the fallback's cost (the three primaries sum to exactly 80), so it may draw RESERVE too.
    use_reserve = bool(fallback_minutes) or any(e["kind"] == "fallback" for e in evaluated)
    fp = fingerprint(ODDS_ENDPOINT, redact_params(params), STAGE)
    rc = 0
    try:
        result = client.request(ODDS_ENDPOINT, params, expected_cost=spec["cost"], stage=STAGE,
                                purpose=purpose_for(step, fallback_minutes or 0), use_reserve=use_reserve)
        try:
            _, events = parse_odds_wrapper(result["data"])
        except StageBParseError as exc:
            H.write_halt(client.halt_path, "MALFORMED_BODY", f"{step}: {exc}", fingerprint=fp,
                         evidence=[result["raw_file"]])
            print(f"ERROR: {step}: unexpected response structure ({exc}). Raw file kept. HALT MALFORMED_BODY.")
            rc = 2
        else:
            if not cost_ok(len(events), result["actual_cost"], spec["cost"][1]):
                text = (f"{step}: actual cost {result['actual_cost']} with {len(events)} events; expected "
                        f"{spec['cost'][1]} for a non-empty payload or 0 for an empty one")
                H.write_halt(client.halt_path, "UNEXPECTED_COST", text, fingerprint=fp,
                             evidence=[result["raw_file"]])
                print(f"HALT UNEXPECTED_COST: {text}")
                rc = 2
    except OddsClientError as exc:
        print(f"ERROR: {exc}")
        rc = 2

    try:
        evaluated, sections, out = write_report(root, client.ledger, anchors)
    except Exception as exc:  # noqa: BLE001 - the ledger already records the outcome
        print(f"Could not write the report: {type(exc).__name__}: {exc}")
        return 2
    if fp in sections:
        print(sections[fp])
        print(f"(section written to {out.name})")
        mine = next(e for e in evaluated if e["fingerprint"] == fp)
        if rc == 0 and mine["status"] != "PASS":
            rc = 1
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stage_b", description="Stage B close-proxy odds snapshots")
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--run", action="store_true")
    p.add_argument("--step", choices=STEP_ORDER)
    p.add_argument("--confirm-paid", action="store_true")
    p.add_argument("--fallback-minutes", type=int)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.plan:
        if args.step or args.fallback_minutes:
            parser.error("--plan takes no --step or --fallback-minutes")
        return print_plan(root)
    if not args.step:
        parser.error("--run requires --step")
    if args.fallback_minutes is not None and args.fallback_minutes not in FALLBACK_MINUTES:
        print(f"Refused: --fallback-minutes must be one of {FALLBACK_MINUTES}. No request was made.")
        return 2
    return run_step(root, args.step, args.confirm_paid, args.fallback_minutes)


if __name__ == "__main__":
    sys.exit(main())
