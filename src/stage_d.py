"""Stage D: breadth snapshots on two other game days.

Every call goes through OddsClient (stage="D"). This module imports no HTTP library.
Raw files in pilot/raw are only opened read-only ("rb"). Stage D never draws on RESERVE.

  D1 date 2025-03-08T15:50:00Z
  D2 date 2026-03-07T16:50:00Z

  --plan                                  steps, parameters, maximum cost; no request
  --run --step D1[,D2] --confirm-paid     listed steps sequentially, each once

A step already recorded in the ledger is refused without a request and without cost. A comma
list is all-or-nothing at refusal time and stops at the first HALT or error. No anchor logic:
every (event, bookmaker, market) is classified with normalize.classify_market.

Every count is of events in a sampled snapshot: not games, not matched to results.

Run: .\\.venv\\Scripts\\python.exe -m src.stage_d --plan
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import halt as H
from . import normalize as N
from . import stage_b as SB
from . import stage_c as SC
from .ledger import Ledger, LedgerCorrupt
from .odds_client import OddsClient, OddsClientError, fingerprint, load_config, redact_params

ODDS_ENDPOINT = SB.ODDS_ENDPOINT
STAGE = "D"
COST = (0, 30)
REGIONS = "us"
MARKETS = "h2h,spreads,totals"
TARGET_BOOKS = N.TARGET_BOOKS

STEP_ORDER = ["D1", "D2"]
STEPS = {
    "D1": {"date": "2025-03-08T15:50:00Z", "season": 2025, "refs": ["B1", "C2", "C4"]},
    "D2": {"date": "2026-03-07T16:50:00Z", "season": 2026, "refs": ["B2", "C6", "C8"]},
}

NOT_RUN = "NOT_RUN"
EXECUTED = "EXECUTED"
EMPTY_PAYLOAD = "EMPTY_PAYLOAD"
HALTED = "HALTED"
REFUSED = "REFUSED"


# ------------------------------------------------------------------ steps
def step_params(step: str) -> dict:
    return {"date": STEPS[step]["date"], "regions": REGIONS, "markets": MARKETS,
            "oddsFormat": "american", "dateFormat": "iso"}


def purpose_for(step: str) -> str:
    return f"stage D step {step}"


def parse_purpose(purpose: str):
    p = purpose.split()
    if len(p) >= 4 and p[:3] == ["stage", "D", "step"] and p[3] in STEPS:
        return p[3]
    return None


def parse_step_list(text: str) -> list[str]:
    return [s.strip().upper() for s in str(text or "").split(",") if s.strip()]


# ------------------------------------------------------------------ ledger
def collect_attempts(rows) -> dict:
    """step -> {'first': (ledger line, row), 'row200': row or None, 'last': row}; the header is line 1."""
    out = {}
    for i, r in enumerate(rows):
        if r["stage"] != STAGE or r["endpoint"] != ODDS_ENDPOINT:
            continue
        step = parse_purpose(r["purpose"])
        if step is None:
            continue
        att = out.setdefault(step, {"first": (i + 2, r), "row200": None, "last": r})
        att["last"] = r
        if r["status_code"] == "200" and att["row200"] is None:
            att["row200"] = r
    return out


def evaluate_step(step: str, att, root: Path, halts) -> dict:
    ev = {"step": step, "season": STEPS[step]["season"], "status": NOT_RUN, "requested": "", "meta": {},
          "events": [], "n_events": None, "seconds_before_requested": None, "cost": 0, "ledger_status": "-",
          "fingerprint": "", "notes": [], "executed": att is not None}
    if att is None:
        return ev
    row = att["row200"] or att["last"]
    try:
        params = json.loads(row["params_redacted_json"] or "{}")
    except ValueError:
        params = {}
    ev.update(requested=params.get("date", ""), cost=row["actual_cost"], ledger_status=row["status_code"],
              fingerprint=row["fingerprint"])
    active_fp, active_code, hist = halts
    if att["row200"] is None:
        ev["status"] = HALTED
        ev["notes"].append(f"ledger status {row['status_code']}: no usable payload")
        return ev
    try:
        meta, events = SB.parse_odds_wrapper(SC.read_raw(row["raw_file"], root))
    except (OSError, ValueError, SB.StageBParseError) as exc:
        ev["status"] = HALTED
        ev["notes"].append(f"saved response unusable ({type(exc).__name__})")
        return ev
    ev.update(meta=meta, events=events, n_events=len(events))
    req, ret = SB.parse_ts(ev["requested"]), SB.parse_ts(meta["timestamp"])
    if req and ret:
        ev["seconds_before_requested"] = int((req - ret).total_seconds())
    if not SB.cost_ok(len(events), row["actual_cost"], COST[1]):
        ev["notes"].append(f"cost check FAILED (actual {row['actual_cost']} with {len(events)} events)")
    if active_fp and active_fp == row["fingerprint"]:
        ev["status"] = HALTED
        ev["notes"].append(f"HALT {active_code} active for this request")
        return ev
    if row["fingerprint"] in hist:
        ev["notes"].append(f"HALT {hist[row['fingerprint']]} was raised and cleared")
    ev["status"] = EXECUTED if events else EMPTY_PAYLOAD
    return ev


def evaluate_all(root: Path, rows) -> dict:
    atts = collect_attempts(rows)
    halts = SC.halt_fingerprints(Path(root) / "pilot" / "ledger")
    return {s: evaluate_step(s, atts.get(s), root, halts) for s in STEP_ORDER}


# ----------------------------------------------------------------- analysis
def timing(ev: dict) -> dict:
    """Commence-time summary of the events in the payload (UTC)."""
    snap_ts = SB.parse_ts(ev["meta"].get("timestamp"))
    times = []
    for e in ev["events"]:
        ct = SB.parse_ts(e.get("commence_time"))
        if ct is not None:
            times.append((ct, e))
    by_date, by_hour, before = Counter(), Counter(), []
    for ct, e in times:
        by_date[ct.strftime("%Y-%m-%d")] += 1
        by_hour[ct.strftime("%H:00")] += 1
        if snap_ts is not None and ct < snap_ts:
            before.append({"event_id": e["id"], "home_team": e.get("home_team", ""), "away_team": e.get("away_team", ""),
                           "commence_time": e.get("commence_time", ""), "seconds_before": int((snap_ts - ct).total_seconds())})
    return {"earliest": SB.fmt_ts(min(t for t, _ in times)) if times else "-",
            "latest": SB.fmt_ts(max(t for t, _ in times)) if times else "-",
            "no_commence": len(ev["events"]) - len(times),
            "by_date": dict(sorted(by_date.items())), "by_hour": dict(sorted(by_hour.items())), "before": before}


def reference_snapshots(root: Path, rows, step: str) -> list[dict]:
    """Earlier same-season snapshots: {'label', 'ids'} or {'label', 'ids': None, 'why'} when unavailable."""
    out = []
    b_snaps = {}
    try:
        b_snaps = {s["step"]: s for s in N.good(N.load_snapshots(Path(root)))}
    except N.NormalizeError:
        pass
    try:
        anchors = SB.load_anchors(Path(root))
        c_eval = SC.evaluate_all(Path(root), rows, anchors)
    except (SB.StageBError, LedgerCorrupt):
        c_eval = {}
    for label in STEPS[step]["refs"]:
        if label.startswith("B"):
            s = b_snaps.get(label)
            out.append({"label": label, "ids": {e["id"] for e in s["events"]}} if s
                       else {"label": label, "ids": None, "why": "not available"})
        else:
            ev = c_eval.get(label)
            if ev is not None and ev["status"] in SC.USABLE:
                out.append({"label": label, "ids": {e["id"] for e in ev["events"]}})
            else:
                out.append({"label": label, "ids": None, "why": ev["status"] if ev else "not available"})
    return out


def overlap(ev: dict, refs: list[dict]) -> dict:
    ids = {e["id"] for e in ev["events"]}
    avail = [r for r in refs if r["ids"] is not None]
    seen = set().union(*[r["ids"] for r in avail]) if avail else set()
    return {"shared": {r["label"]: len(ids & r["ids"]) for r in avail}, "shared_any": len(ids & seen),
            "new": len(ids - seen), "n_refs": len(avail), "unavailable": {r["label"]: r["why"] for r in refs if r["ids"] is None}}


def read_known_names(root: Path):
    path = Path(root) / "pilot" / "normalized" / "team_names.csv"
    if not path.exists():
        return None
    import csv
    with open(path, encoding="utf-8", newline="") as fh:                   # read-only
        return {r["team_name"] for r in csv.DictReader(fh) if r.get("team_name")}


def team_names(ev: dict) -> set:
    return {n for e in ev["events"] for n in (e.get("home_team"), e.get("away_team")) if n}


# ------------------------------------------------------------------ report
def _f(v) -> str:
    return "-" if v in (None, "") else str(v)


def credits(root: Path, evaluated: dict) -> dict:
    cfg = load_config(Path(root) / "pilot" / "config" / "pilot.yaml")
    d = Path(root) / "pilot" / "ledger"
    summ = Ledger(d / "credit_ledger.csv", d / "baseline.json").summary(cfg["stage_budgets"])
    return {"stage": summ["per_stage"].get(STAGE, 0), "budget": cfg["stage_budgets"][STAGE],
            "committed": summ["committed"], "cap": cfg["credit_cap"], "remaining": cfg["credit_cap"] - summ["committed"],
            "unspent": COST[1] * sum(1 for e in evaluated.values() if not e["executed"])}


def build_report(root: Path, evaluated: dict, rows) -> str:
    usable = [e for e in evaluated.values() if e["status"] == EXECUTED]
    L = ["# Stage D report: breadth snapshots (sampled)", "",
         "Every count is of **events in a sampled snapshot**: not games, and not matched to results. "
         "No results data is joined. There is no anchor logic here.", "", "## 1. Step table", ""]
    L.append(N.md(["Step", "Status", "Requested date", "Returned timestamp", "previous_timestamp", "next_timestamp",
                   "Seconds returned snapshot is before requested date", "Events in payload", "Cost", "Ledger status", "Notes"],
                  [[s, ev["status"], _f(ev["requested"]) if ev["executed"] else "-", _f(ev["meta"].get("timestamp")),
                    _f(ev["meta"].get("previous_timestamp")), _f(ev["meta"].get("next_timestamp")),
                    _f(ev["seconds_before_requested"]), _f(ev["n_events"]), _f(ev["cost"]), ev["ledger_status"],
                    "; ".join(ev["notes"])] for s, ev in evaluated.items()]))
    L += ["", "NOT_RUN is not a failure. An EMPTY_PAYLOAD is a finding about what was visible at that sampled snapshot."]

    L += ["", "## 2. Coverage per snapshot (events)", "",
          "Events where the book has any market / complete_paired h2h / spreads / totals (normalize.classify_market; "
          "spreads: points summing to zero are paired)."]
    if not usable:
        L += ["", "No executed step with events yet."]
    for ev in usable:
        cov = N.coverage_table(ev["events"], TARGET_BOOKS)
        dist = N.target_book_distribution(ev["events"])
        lst = N.spreads_totals_without_h2h(ev)
        L += ["", f"### {ev['step']} ({len(ev['events'])} events in payload)", "",
              N.md(["Bookmaker", "Any market", "h2h complete_paired", "spreads complete_paired", "totals complete_paired"],
                   [[b, cov[b]["any"], cov[b]["h2h"], cov[b]["spreads"], cov[b]["totals"]] for b in TARGET_BOOKS]),
              "", "Events by number of target books with complete_paired h2h:", "",
              N.md(["0 books", "1 book", "2 books", "3 books"], [[dist[0], dist[1], dist[2], dist[3]]]),
              "", f"Events with spreads or totals but no h2h at the same book: {len(lst)} (event, book) pairs"]
        if lst:
            L += ["", N.md(["Event", "Home", "Away", "Book", "Markets"],
                           [[x["event_id"], x["home_team"], x["away_team"], x["bookmaker"], x["markets"]] for x in lst])]

    L += ["", "## 3. Timing of events in the payload (UTC)", ""]
    for ev in usable:
        t = timing(ev)
        L += [f"### {ev['step']} (returned snapshot {_f(ev['meta'].get('timestamp'))})", "",
              f"- Earliest commence_time: {t['earliest']}; latest: {t['latest']}"
              + (f"; events without a parseable commence_time: {t['no_commence']}" if t["no_commence"] else ""),
              f"- Events by commence date: " + (", ".join(f"{k} {v}" for k, v in t["by_date"].items()) or "none"),
              f"- Events by commence hour: " + (", ".join(f"{k} {v}" for k, v in t["by_hour"].items()) or "none"),
              f"- Events with commence_time before the returned snapshot timestamp: {len(t['before'])}"]
        if t["before"]:
            L += ["", N.md(["Event", "Home", "Away", "Commence", "Seconds before snapshot"],
                           [[x["event_id"], x["home_team"], x["away_team"], x["commence_time"], x["seconds_before"]]
                            for x in t["before"]])]
        L.append("")
    if not usable:
        L.append("No executed step with events yet.")

    L += ["", "## 4. Overlap with earlier same-season snapshots (event_id; counts only)", ""]
    ov_rows = []
    for ev in usable:
        o = overlap(ev, reference_snapshots(root, rows, ev["step"]))
        ov_rows.append([ev["step"], ev["season"], len(ev["events"]),
                        ", ".join(f"{k} {v}" for k, v in o["shared"].items()) or "-", o["shared_any"], o["new"],
                        ", ".join(f"{k} ({v})" for k, v in o["unavailable"].items()) or "-"])
    L.append(N.md(["Step", "Season", "Events in payload", "Shared with (reference: events)", "Shared with any reference",
                   "New in this snapshot", "References not available"], ov_rows) if ov_rows
             else "No executed step with events yet.")
    L.append("\nReferences: 2025 = B1, C2, C4; 2026 = B2, C6, C8. 'New' means in none of the available references. "
             "A reference that was not run is not an empty snapshot.")

    L += ["", "## 5. Team names", ""]
    known = read_known_names(root)
    if known is None:
        L.append("pilot/normalized/team_names.csv not found; new-name detection skipped.")
    for ev in usable:
        names = team_names(ev)
        if known is None:
            L.append(f"- {ev['step']}: {len(names)} unique team names")
            continue
        new = sorted(names - known, key=lambda n: (n.casefold(), n))
        L.append(f"- {ev['step']}: {len(names)} unique team names; not in team_names.csv: {len(new)}"
                 + (f" ({'; '.join(new)})" if new else ""))
    L.append("\nNo division or gender is decided here. team_names.csv is read only.")

    cr = credits(root, evaluated)
    L += ["", "## 6. Credits", "",
          f"- Stage D spent (pessimistic, from the ledger): {cr['stage']} of {cr['budget']}",
          f"- Cumulative committed across all stages: {cr['committed']}",
          f"- Remaining against the {cr['cap']} cap: {cr['remaining']}",
          f"- Maximum still unspent in Stage D: {cr['unspent']} (not-run steps are attributed 0 credits)",
          "", "## 7. Scope", "",
          "- Sampled snapshots only; events, not games; no results data is joined; nothing here assesses T1 to T3.",
          "- Snapshot times are the returned timestamps; quotes inside a snapshot can be older than the snapshot."]
    return "\n".join(L) + "\n"


def write_report(root: Path, evaluated: dict, rows):
    text = build_report(root, evaluated, rows)
    out = Path(root) / "pilot" / "normalized" / "stage_d_report.md"
    H.atomic_write_text(out, text)
    return text, out


# --------------------------------------------------------------------- CLI
def _ledger(root: Path) -> Ledger:
    d = Path(root) / "pilot" / "ledger"
    return Ledger(d / "credit_ledger.csv", d / "baseline.json")


def print_plan(root: Path) -> int:
    cfg = load_config(root / "pilot" / "config" / "pilot.yaml")
    try:
        evaluated = evaluate_all(root, _ledger(root).rows())
    except LedgerCorrupt:
        evaluated = None
        print("(ledger unreadable: step status unknown)")
    print(f"Stage D plan: breadth snapshots, stage budget {cfg['stage_budgets'][STAGE]} (never draws on RESERVE)")
    print(f"endpoint: GET {ODDS_ENDPOINT}")
    for s in STEP_ORDER:
        ptxt = " ".join(f"{k}={v}" for k, v in step_params(s).items())
        status = evaluated[s]["status"] if evaluated else "unknown"
        print(f"  {s}  {ptxt}  expected cost {COST[0]}..{COST[1]}  max cost {COST[1]}  status={status}")
    print(f"total maximum: {COST[1] * len(STEP_ORDER)} credits of {cfg['stage_budgets'][STAGE]} (Stage D budget).")
    print("No request was made.")
    return 0


def refusals(steps: list[str], confirm_paid: bool, attempts: dict) -> list[str]:
    """Reasons to refuse the whole command before any request. No network."""
    out = []
    if not steps:
        out.append("--step needs at least one step")
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        out.append(f"unknown step(s) {', '.join(unknown)}; valid: {', '.join(STEP_ORDER)}")
    dups = sorted({s for s in steps if steps.count(s) > 1})
    if dups:
        out.append(f"step(s) listed more than once: {', '.join(dups)}")
    if not confirm_paid:
        out.append("--run makes paid API calls; re-run with --confirm-paid (use --plan to preview)")
    for s in steps:
        if s in attempts:
            line, row = attempts[s]["first"]
            out.append(f"{s} is already recorded in the ledger (line {line}, ts {row['ts_utc']}, status "
                       f"{row['status_code']}); each step runs once")
    return out


def run_steps(root: Path, step_text: str, confirm_paid: bool) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    steps = parse_step_list(step_text)
    try:
        attempts = collect_attempts(_ledger(root).rows())
    except LedgerCorrupt as exc:
        print(f"Refused: {exc}. No request was made.")
        return 2
    reasons = refusals(steps, confirm_paid, attempts)
    if not reasons and H.read_halt(root / "pilot" / "ledger" / "HALT") is not None:
        reasons.append("a HALT is active; resolve it with reconcile and clear-halt first")
    if reasons:
        print("Refused (no request was made, no ledger row written):")
        for r in reasons:
            print(f"  {REFUSED}: {r}")
        return 2
    try:
        client = OddsClient(root=root)
    except OddsClientError as exc:
        print(f"Refused: {exc}. No request was made.")
        return 2

    rc, log = 0, []
    for i, step in enumerate(steps):
        params = step_params(step)
        fp = fingerprint(ODDS_ENDPOINT, redact_params(params), STAGE)
        try:
            result = client.request(ODDS_ENDPOINT, params, expected_cost=COST, stage=STAGE, purpose=purpose_for(step))
        except OddsClientError as exc:
            log.append(f"{step}: ERROR: {exc}")
            rc = 2
        else:
            try:
                _, events = SB.parse_odds_wrapper(result["data"])
            except SB.StageBParseError as exc:
                H.write_halt(client.halt_path, "MALFORMED_BODY", f"{step}: {exc}", fingerprint=fp,
                             evidence=[result["raw_file"]])
                log.append(f"{step}: unexpected response structure ({exc}). Raw file kept. HALT MALFORMED_BODY.")
                rc = 2
            else:
                if not SB.cost_ok(len(events), result["actual_cost"], COST[1]):
                    text = (f"{step}: actual cost {result['actual_cost']} with {len(events)} events; expected "
                            f"{COST[1]} for a non-empty payload or 0 for an empty one")
                    H.write_halt(client.halt_path, "UNEXPECTED_COST", text, fingerprint=fp, evidence=[result["raw_file"]])
                    log.append(f"HALT UNEXPECTED_COST: {text}")
                    rc = 2
                else:
                    log.append(f"{step}: {'executed' if events else 'EMPTY_PAYLOAD'} (cost {result['actual_cost']}, "
                               f"{len(events)} events)")
        if rc:
            if steps[i + 1:]:
                log.append(f"Skipped (not requested): {', '.join(steps[i + 1:])}")
            break

    print("This invocation:")
    for line in log:
        print(f"  {line}")
    try:
        rows = client.ledger.rows()
        text, out = write_report(root, evaluate_all(root, rows), rows)
    except Exception as exc:  # noqa: BLE001 - the ledger already records the outcome
        print(f"Could not write the report: {type(exc).__name__}: {exc}")
        return 2
    print()
    print(text)
    print(f"(report written to {out.name})")
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stage_d", description="Stage D breadth snapshots")
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--run", action="store_true")
    p.add_argument("--step", help="comma list, e.g. D1,D2")
    p.add_argument("--confirm-paid", action="store_true")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.plan:
        if args.step or args.confirm_paid:
            parser.error("--plan takes no --step or --confirm-paid")
        return print_plan(root)
    if not args.step:
        parser.error("--run requires --step")
    return run_steps(root, args.step, args.confirm_paid)


if __name__ == "__main__":
    sys.exit(main())
