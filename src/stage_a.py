"""Stage A: coverage discovery with historical-events probes.

Every call goes through OddsClient (stage="A"). This module imports no HTTP library.

  --plan                     print every request with its maximum cost; no network, no client
  --run --confirm-paid       execute the probes (already-successful probes are skipped)
  --followup --date ISO [--from ISO --to ISO] --confirm-paid
                             one extra probe, at most 4, with use_reserve=True

Counts describe the sampled snapshots only. Nothing here asserts anything about
dates that were not sampled.

Run: .\\.venv\\Scripts\\python.exe -m src.stage_a --plan
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import halt as H
from .ledger import Ledger, LedgerCorrupt
from .odds_client import OddsClient, OddsClientError, fingerprint, load_config, redact_params

SPORT = "lacrosse_ncaa"
ENDPOINT = f"/v4/historical/sports/{SPORT}/events"
STAGE = "A"
EXPECTED_COST = (0, 1)
MAX_FOLLOWUPS = 4

PURPOSE_PROBE = "stage A probe"
PURPOSE_FOLLOWUP = "stage A followup"
PURPOSE_PARTICIPANTS = "stage A participants"

# The participants endpoint path has NOT been confirmed against the docs.
# Set the flag to True only after checking the path below in the Odds API docs.
PARTICIPANTS_ENDPOINT = f"/v4/sports/{SPORT}/participants"
PARTICIPANTS_ENDPOINT_CONFIRMED = False

ET = ZoneInfo("America/New_York")
ISO_Z = "%Y-%m-%dT%H:%M:%SZ"

PROBE_DATES = [
    "2023-03-04", "2023-05-20", "2024-03-02", "2024-04-06", "2025-01-25",
    "2025-02-01", "2025-02-22", "2025-02-27", "2025-03-08", "2025-04-05",
    "2026-01-31", "2026-02-14", "2026-03-07", "2026-03-27", "2026-04-18",
]
WINDOWS = {
    "2025-02-27": ("2025-02-28T00:00:00Z", "2025-03-01T10:00:00Z"),
    "2026-03-27": ("2026-03-28T00:00:00Z", "2026-03-29T10:00:00Z"),
}
ANCHORS = [
    {"anchor_id": "quinnipiac_air_force", "tokens": ("quinnipiac", "air force"),
     "expected_date_et": "2025-02-28", "probe_date": "2025-02-27"},
    {"anchor_id": "njit_vermont", "tokens": ("njit", "vermont"),
     "expected_date_et": "2026-03-28", "probe_date": "2026-03-27"},
]
GATE_WINDOWS = {"2025": ("2025-02-01", "2025-05-03"), "2026": ("2026-01-31", "2026-05-02")}
SEASONS = ["2023", "2024", "2025", "2026"]

EVENT_COLUMNS = ["probe_id", "requested_date", "returned_timestamp", "previous_timestamp", "next_timestamp",
                 "event_id", "commence_time_utc", "commence_time_et", "home_team", "away_team"]
SUMMARY_COLUMNS = ["probe_id", "requested_date", "returned_timestamp", "n_events", "actual_cost",
                   "season_label", "window_filter"]


class StageAParseError(Exception):
    pass


class _Stop(Exception):
    """Ends a run after an error has been reported."""


class ArgError(Exception):
    pass


# ----------------------------------------------------------------- probes
def build_probes() -> list[dict]:
    probes = []
    for i, d in enumerate(PROBE_DATES, start=1):
        params = {"date": f"{d}T12:00:00Z", "dateFormat": "iso"}
        if d in WINDOWS:
            params["commenceTimeFrom"], params["commenceTimeTo"] = WINDOWS[d]
        probes.append({"probe_id": f"P{i:02d}", "params": params})
    return probes


def probe_fp(params: dict) -> str:
    return fingerprint(ENDPOINT, redact_params(params), STAGE)


def window_filter(params: dict) -> str:
    if "commenceTimeFrom" in params:
        return f"commenceTimeFrom={params['commenceTimeFrom']};commenceTimeTo={params['commenceTimeTo']}"
    return "none"


def season_label(params: dict) -> str:
    return str(params["date"])[:4]


# ---------------------------------------------------------------- parsing
def _s(value) -> str:
    return "" if value is None else str(value)


def convert_time(value):
    """Return (commence_time_utc, commence_time_et). Unparseable values are kept raw with a blank ET."""
    if not isinstance(value, str) or not value:
        return "", ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value, ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(ISO_Z), dt.astimezone(ET).isoformat(timespec="seconds")


def parse_wrapper(obj):
    """Tolerant parse of {timestamp, previous_timestamp, next_timestamp, data: [events]}.

    Raises StageAParseError when the structure is unexpected. An empty data list is normal.
    """
    if not isinstance(obj, dict):
        raise StageAParseError("top-level JSON is not an object")
    data = obj.get("data")
    if not isinstance(data, list):
        raise StageAParseError("'data' is missing or not a list")
    meta = {k: _s(obj.get(k)) for k in ("timestamp", "previous_timestamp", "next_timestamp")}
    events = []
    for i, e in enumerate(data):
        if not isinstance(e, dict) or e.get("id") in (None, ""):
            raise StageAParseError(f"data[{i}] is not an object with an id")
        utc, et = convert_time(e.get("commence_time"))
        events.append({"event_id": str(e["id"]), "commence_time_utc": utc, "commence_time_et": et,
                       "home_team": _s(e.get("home_team")), "away_team": _s(e.get("away_team"))})
    return meta, events


# ---------------------------------------------------------------- ledger
def _load_result(probe_id, params, row):
    res = {"probe_id": probe_id, "params": params, "row": row, "status": "missing",
           "meta": {}, "events": [], "error": ""}
    if row is None:
        return res
    try:
        obj = json.loads(Path(row["raw_file"]).read_text(encoding="utf-8"))
        res["meta"], res["events"] = parse_wrapper(obj)
        res["status"] = "done"
    except (OSError, ValueError, StageAParseError) as exc:
        res["status"] = "unreadable"
        res["error"] = f"{type(exc).__name__}: {exc}"
    return res


def followup_fingerprints(rows) -> dict:
    """fingerprint -> first ledger row, for every follow-up attempted (in first-seen order)."""
    out = {}
    for r in rows:
        if r["endpoint"] == ENDPOINT and r["purpose"].startswith(PURPOSE_FOLLOWUP):
            out.setdefault(r["fingerprint"], r)
    return out


def collect_results(ledger: Ledger, probes: list[dict]) -> list[dict]:
    """Base probes in plan order, then follow-ups in the order they were first attempted."""
    rows = ledger.rows()
    last200 = {r["fingerprint"]: r for r in rows if r["endpoint"] == ENDPOINT and r["status_code"] == "200"}
    results, base_fps = [], set()
    for p in probes:
        fp = probe_fp(p["params"])
        base_fps.add(fp)
        results.append(_load_result(p["probe_id"], p["params"], last200.get(fp)))
    n = 0
    for fp, first in followup_fingerprints(rows).items():
        if fp in base_fps:
            continue
        n += 1
        try:
            params = json.loads(first["params_redacted_json"])
        except ValueError:
            params = {}
        results.append(_load_result(f"F{n}", params, last200.get(fp)))
    return results


# ----------------------------------------------------------------- anchors
def detect_anchors(results: list[dict]) -> list[dict]:
    events: dict[str, dict] = {}
    for r in results:
        if r["status"] != "done":
            continue
        for e in r["events"]:
            rec = events.setdefault(e["event_id"], dict(e, probe_ids=[]))
            if r["probe_id"] not in rec["probe_ids"]:
                rec["probe_ids"].append(r["probe_id"])

    out = []
    for a in ANCHORS:
        full, near = [], []
        for rec in events.values():
            text = f"{rec['home_team']} {rec['away_team']}".lower()
            hits = [t for t in a["tokens"] if t in text]
            if len(hits) == len(a["tokens"]):
                full.append(rec)
            elif hits:
                near.append((rec, hits))
        full.sort(key=lambda r: r["commence_time_utc"])
        near.sort(key=lambda x: x[0]["commence_time_utc"])
        first = full[0] if full else None
        out.append({
            "anchor_id": a["anchor_id"],
            "tokens": list(a["tokens"]),
            "expected_date_et": a["expected_date_et"],
            "found": first is not None,
            "event_id": first["event_id"] if first else "",
            "commence_time_utc": first["commence_time_utc"] if first else "",
            "commence_time_et": first["commence_time_et"] if first else "",
            "matched_names": [first["home_team"], first["away_team"]] if first else [],
            "commence_date_et_matches_expected": bool(first) and first["commence_time_et"][:10] == a["expected_date_et"],
            "all_matches": [{"event_id": r["event_id"], "commence_time_utc": r["commence_time_utc"],
                             "commence_time_et": r["commence_time_et"], "probe_ids": r["probe_ids"]} for r in full],
            "near_miss_candidates": [
                {"event_id": r["event_id"], "commence_time_utc": r["commence_time_utc"],
                 "commence_time_et": r["commence_time_et"], "home_team": r["home_team"],
                 "away_team": r["away_team"], "tokens_matched": hits, "probe_ids": r["probe_ids"]}
                for r, hits in near[:25]],
        })
    return out


# -------------------------------------------------------------------- gate
def evaluate_gate(summaries: list[dict], anchors_found: dict, base_complete: bool) -> dict:
    """summaries: [{'requested_date': 'YYYY-MM-DD...', 'n_events': int}] for completed probes."""
    def nonempty_in(lo, hi):
        return sum(1 for s in summaries if lo <= s["requested_date"][:10] <= hi and s["n_events"] > 0)

    n25 = nonempty_in(*GATE_WINDOWS["2025"])
    n26 = nonempty_in(*GATE_WINDOWS["2026"])
    gate = {"window_2025_nonempty_probes": n25, "window_2026_nonempty_probes": n26,
            "anchors_found": dict(anchors_found), "base_probes_complete": base_complete,
            "status": "", "reasons": []}
    if not base_complete:
        gate["status"] = "INCOMPLETE"
        gate["reasons"].append("not every planned probe has a successful result yet; the gate is not evaluated")
        return gate
    reasons = gate["reasons"]
    if n25 == 0:
        reasons.append("no visible events at the sampled snapshots in 2025-02-01..2025-05-03")
    if n26 == 0:
        reasons.append("no visible events at the sampled snapshots in 2026-01-31..2026-05-02")
    for aid, found in anchors_found.items():
        if not found:
            reasons.append(f"anchor {aid} not found in the sampled snapshots")
    gate["status"] = "GO" if not reasons else "DIAGNOSE"
    return gate


def _shift(iso: str, hours: int) -> str:
    return (datetime.strptime(iso, ISO_Z) + timedelta(hours=hours)).strftime(ISO_Z)


def suggest_followups(results: list[dict], anchors: list[dict]) -> list[str]:
    """Suggestions only. Nothing here is executed."""
    out = []
    for a, spec in zip(anchors, ANCHORS):
        if a["found"]:
            continue
        pdate = f"{spec['probe_date']}T12:00:00Z"
        frm, to = WINDOWS[spec["probe_date"]]
        out.append(f"{a['anchor_id']}: same probe 24h earlier: --followup --date {_shift(pdate, -24)} "
                   f"--from {frm} --to {to}")
        out.append(f"{a['anchor_id']}: same probe 48h earlier: --followup --date {_shift(pdate, -48)} "
                   f"--from {frm} --to {to}")
        out.append(f"{a['anchor_id']}: wider commence window: --followup --date {pdate} "
                   f"--from {_shift(frm, -24)} --to {_shift(to, 24)}")
        out.append(f"{a['anchor_id']}: name variants: re-scan the saved events for other spellings of "
                   f"{' / '.join(a['tokens'])} (costs no credits)")
    for year, (lo, hi) in GATE_WINDOWS.items():
        empty = [r["params"]["date"] for r in results
                 if r["status"] == "done" and lo <= r["params"]["date"][:10] <= hi and not r["events"]]
        done_in = [r for r in results if r["status"] == "done" and lo <= r["params"]["date"][:10] <= hi]
        if done_in and len(empty) == len(done_in):
            out.append(f"{year} window: the same probe 24h and 48h earlier for one of: {', '.join(empty)}")
    return out


# ------------------------------------------------------------------ outputs
def _csv_text(columns, rows) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in columns})
    return buf.getvalue()


def write_outputs(root: Path, ledger: Ledger, budgets: dict, probes: list[dict]) -> dict:
    out_dir = Path(root) / "pilot" / "normalized"
    results = collect_results(ledger, probes)
    done = [r for r in results if r["status"] == "done"]

    event_rows, summary_rows = [], []
    for r in done:
        meta = r["meta"]
        for e in r["events"]:
            event_rows.append({"probe_id": r["probe_id"], "requested_date": r["params"]["date"],
                               "returned_timestamp": meta["timestamp"],
                               "previous_timestamp": meta["previous_timestamp"],
                               "next_timestamp": meta["next_timestamp"], **e})
        summary_rows.append({"probe_id": r["probe_id"], "requested_date": r["params"]["date"],
                             "returned_timestamp": meta["timestamp"], "n_events": len(r["events"]),
                             "actual_cost": r["row"]["actual_cost"], "season_label": season_label(r["params"]),
                             "window_filter": window_filter(r["params"])})

    anchors = detect_anchors(results)
    base = results[:len(probes)]
    base_complete = all(r["status"] == "done" for r in base)
    gate = evaluate_gate([{"requested_date": r["params"]["date"], "n_events": len(r["events"])} for r in done],
                         {a["anchor_id"]: a["found"] for a in anchors}, base_complete)
    spend = ledger.summary(budgets)
    stage_spent = spend["per_stage"].get(STAGE, 0)
    reserve_draws = spend["reserve_by_stage"].get(STAGE, 0)

    seasons = {}
    for s in SEASONS:
        rs = [r for r in done if season_label(r["params"]) == s]
        ids, dates = set(), set()
        for r in rs:
            for e in r["events"]:
                ids.add(e["event_id"])
                dates.add((e["commence_time_et"] or e["commence_time_utc"])[:10])
        seasons[s] = {"probes_run": len(rs), "nonempty": sum(1 for r in rs if r["events"]),
                      "event_ids": len(ids), "commence_dates": len(dates)}
    pre25 = [r for r in done if season_label(r["params"]) in ("2023", "2024")]

    anchors_doc = {"generated_ts": H.utc_now(), "anchors": anchors}
    summary_md = render_summary(results, probes, seasons, pre25, anchors, gate, stage_spent, reserve_draws,
                                budgets)

    out_dir.mkdir(parents=True, exist_ok=True)
    H.atomic_write_text(out_dir / "stage_a_events.csv", _csv_text(EVENT_COLUMNS, event_rows))
    H.atomic_write_text(out_dir / "probe_summary.csv", _csv_text(SUMMARY_COLUMNS, summary_rows))
    H.atomic_write_text(out_dir / "anchors.json", json.dumps(anchors_doc, indent=2))
    H.atomic_write_text(out_dir / "stage_a_summary.md", summary_md)
    return {"results": results, "anchors": anchors, "gate": gate, "seasons": seasons,
            "stage_spent": stage_spent, "reserve_draws": reserve_draws, "n_event_rows": len(event_rows),
            "out_dir": out_dir}


def render_summary(results, probes, seasons, pre25, anchors, gate, stage_spent, reserve_draws, budgets) -> str:
    done = [r for r in results if r["status"] == "done"]
    base_done = sum(1 for r in results[:len(probes)] if r["status"] == "done")
    follow = results[len(probes):]
    L = ["# Stage A summary: coverage discovery probes", "",
         f"Generated {H.utc_now()}. These counts describe the sampled snapshots only. They say nothing about "
         "dates or snapshots that were not sampled.", "",
         "## Probes", "",
         f"Planned probes with a successful result: {base_done} of {len(probes)}. "
         f"Follow-ups attempted: {len(follow)} of {MAX_FOLLOWUPS}.", ""]
    unreadable = [r["probe_id"] for r in results if r["status"] == "unreadable"]
    if unreadable:
        L += [f"Probes whose saved response could not be read: {', '.join(unreadable)}.", ""]
    L += ["| Season | Probes run | Non-empty probes | Distinct event IDs | Distinct commence dates (ET) |",
          "|---|---|---|---|---|"]
    for s, v in seasons.items():
        L.append(f"| {s} | {v['probes_run']} | {v['nonempty']} | {v['event_ids']} | {v['commence_dates']} |")
    L += ["", f"Pre-2025 probes with visible events: {sum(1 for r in pre25 if r['events'])} of {len(pre25)} run "
          "(Stage E is conditional on this being above zero).", "",
          "| Probe | Requested date | Returned timestamp | Events | Credits | Window filter |",
          "|---|---|---|---|---|---|"]
    for r in done:
        L.append(f"| {r['probe_id']} | {r['params']['date']} | {r['meta']['timestamp']} | {len(r['events'])} | "
                 f"{r['row']['actual_cost']} | {window_filter(r['params'])} |")
    L += ["", "## Anchors", "",
          "| Anchor | Expected date (ET) | Found | Event ID | Commence (UTC) | Commence (ET) | Near-miss candidates |",
          "|---|---|---|---|---|---|---|"]
    for a in anchors:
        L.append(f"| {a['anchor_id']} | {a['expected_date_et']} | {'yes' if a['found'] else 'no'} | "
                 f"{a['event_id'] or '-'} | {a['commence_time_utc'] or '-'} | {a['commence_time_et'] or '-'} | "
                 f"{len(a['near_miss_candidates'])} |")
    for a in anchors:
        if a["found"] and not a["commence_date_et_matches_expected"]:
            L.append(f"\nNote: {a['anchor_id']} was matched on an ET date other than {a['expected_date_et']}.")
    L += ["", "## Credits", "",
          f"Stage A credits used (ledger, pessimistic accounting): {stage_spent} of {budgets.get('A', 0)} "
          f"budgeted. Drawn from RESERVE: {reserve_draws} of {budgets.get('RESERVE', 0)}.", "",
          f"## Gate A: {gate['status']}", "",
          f"- Probes with visible events at the sampled snapshots, 2025-02-01..2025-05-03: "
          f"{gate['window_2025_nonempty_probes']}",
          f"- Probes with visible events at the sampled snapshots, 2026-01-31..2026-05-02: "
          f"{gate['window_2026_nonempty_probes']}"]
    for aid, found in gate["anchors_found"].items():
        L.append(f"- Anchor {aid}: {'found' if found else 'not found'}")
    if gate["reasons"]:
        L += ["", "Reasons:"] + [f"- {x}" for x in gate["reasons"]]
    if gate["status"] == "DIAGNOSE":
        sugg = suggest_followups(results, anchors)
        L += ["", f"Suggested follow-ups (suggestions only; at most {MAX_FOLLOWUPS} may be run):"]
        L += [f"- {x}" for x in sugg] or ["- none generated"]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------- CLI
def _iso_z(value: str, label: str) -> str:
    try:
        return datetime.strptime(value, ISO_Z).strftime(ISO_Z)
    except (TypeError, ValueError):
        raise ArgError(f"{label} must look like 2025-02-27T12:00:00Z (UTC, with seconds and a trailing Z)") from None


def print_plan(root: Path, include_participants: bool) -> int:
    cfg = load_config(root / "pilot" / "config" / "pilot.yaml")
    ledger_dir = root / "pilot" / "ledger"
    ledger = Ledger(ledger_dir / "credit_ledger.csv", ledger_dir / "baseline.json")
    probes = build_probes()
    print(f"Stage A plan: {len(probes)} historical-events probes, stage budget {cfg['stage_budgets']['A']}")
    print(f"endpoint: GET {ENDPOINT}  (expected cost {EXPECTED_COST[0]}..{EXPECTED_COST[1]} each)")
    total = 0
    for p in probes:
        try:
            done = ledger.fingerprint_state(probe_fp(p["params"]))["has_200"]
            note = "already successful: would skip" if done else "to run"
        except LedgerCorrupt:
            note = "ledger unreadable"
        total += EXPECTED_COST[1]
        print(f"  {p['probe_id']}  date={p['params']['date']}  filter={window_filter(p['params'])}  "
              f"max cost {EXPECTED_COST[1]}  [{note}]")
    if include_participants:
        if PARTICIPANTS_ENDPOINT_CONFIRMED:
            print(f"  participants  GET {PARTICIPANTS_ENDPOINT}  max cost 1")
            total += 1
        else:
            print("  participants  DISABLED: endpoint path not yet confirmed against the docs")
    print(f"total maximum: {total} credits of {cfg['stage_budgets']['A']} (Stage A budget). "
          f"Follow-ups (max {MAX_FOLLOWUPS}, 1 credit each) are extra and use use_reserve=True.")
    print("No request was made.")
    return 0


def _halt_malformed(client: OddsClient, fp: str, raw_file: str, text: str) -> None:
    H.write_halt(client.halt_path, "MALFORMED_BODY", text, fingerprint=fp, evidence=[raw_file])
    try:
        H.append_event(client.events_path, {"event": "halt", "reason_code": "MALFORMED_BODY",
                                            "fingerprint": fp, "text": text})
    except OSError:
        pass


def _call_probe(client: OddsClient, pid: str, params: dict, purpose: str, use_reserve: bool):
    result = client.request(ENDPOINT, params, expected_cost=EXPECTED_COST, stage=STAGE, purpose=purpose,
                            use_reserve=use_reserve)
    try:
        _, events = parse_wrapper(result["data"])
    except StageAParseError as exc:
        _halt_malformed(client, result["fingerprint"], result["raw_file"], f"{pid}: {exc}")
        raise _Stop(f"{pid}: unexpected response structure ({exc}). Raw file kept at {result['raw_file']}. "
                    "HALT MALFORMED_BODY written; reconcile before clearing it.") from None
    return result, events


def _finish(root: Path, client: OddsClient) -> None:
    try:
        info = write_outputs(root, client.ledger, client.budgets, build_probes())
    except Exception as exc:  # noqa: BLE001 - best effort; the run result is already recorded in the ledger
        print(f"Could not write outputs: {type(exc).__name__}: {exc}")
        return
    rel = info["out_dir"].relative_to(root) if info["out_dir"].is_relative_to(root) else info["out_dir"]
    print(f"Wrote {rel}\\stage_a_events.csv ({info['n_event_rows']} rows), probe_summary.csv, anchors.json, "
          "stage_a_summary.md")
    for s, v in info["seasons"].items():
        print(f"  {s}: probes run {v['probes_run']}, non-empty {v['nonempty']}, distinct event IDs "
              f"{v['event_ids']}, distinct commence dates {v['commence_dates']}")
    for a in info["anchors"]:
        print(f"  anchor {a['anchor_id']}: {'found' if a['found'] else 'not found'} "
              f"(near-miss candidates: {len(a['near_miss_candidates'])})")
    print(f"Stage A credits (ledger, pessimistic): {info['stage_spent']} of {client.budgets['A']}; "
          f"RESERVE draws: {info['reserve_draws']}")
    print(f"Gate A: {info['gate']['status']}")
    for reason in info["gate"]["reasons"]:
        print(f"  - {reason}")


def run(root: Path, confirm_paid: bool, include_participants: bool) -> int:
    if not confirm_paid:
        print("Refused: --run makes paid API calls. Re-run with --confirm-paid. Use --plan to preview. "
              "No request was made.")
        return 2
    if include_participants and not PARTICIPANTS_ENDPOINT_CONFIRMED:
        print("Refused: --include-participants is disabled until the participants endpoint path is confirmed "
              "against the docs (PARTICIPANTS_ENDPOINT_CONFIRMED in src/stage_a.py). No request was made.")
        return 2
    try:
        client = OddsClient(root=root)
    except OddsClientError as exc:
        print(f"ERROR: {exc}")
        return 2

    probes = build_probes()
    rc, executed, skipped = 0, 0, 0
    try:
        results = collect_results(client.ledger, probes)
        for res in results:
            pid, params = res["probe_id"], res["params"]
            if res["status"] == "done":
                skipped += 1
                print(f"{pid}: skipped (already successful)")
                continue
            if res["status"] == "unreadable":
                fp = probe_fp(params)
                _halt_malformed(client, fp, res["row"]["raw_file"],
                                f"{pid}: saved response is unreadable or has an unexpected structure ({res['error']})")
                raise _Stop(f"{pid}: a successful ledger row exists but its saved response is unusable "
                            f"({res['error']}). HALT MALFORMED_BODY written. Not re-fetching automatically.")
            result, events = _call_probe(client, pid, params, f"{PURPOSE_PROBE} {pid}", use_reserve=False)
            executed += 1
            print(f"{pid}: {params['date']} -> {len(events)} events, cost {result['actual_cost']}")
        if include_participants:
            fp = fingerprint(PARTICIPANTS_ENDPOINT, {}, STAGE)
            if client.ledger.fingerprint_state(fp)["has_200"]:
                skipped += 1
                print("participants: skipped (already successful)")
            else:
                result = client.request(PARTICIPANTS_ENDPOINT, {}, expected_cost=EXPECTED_COST, stage=STAGE,
                                        purpose=PURPOSE_PARTICIPANTS)
                executed += 1
                print(f"participants: saved {Path(result['raw_file']).name}, cost {result['actual_cost']}")
    except _Stop as stop:
        print(f"ERROR: {stop}")
        rc = 2
    except OddsClientError as exc:
        print(f"ERROR: {exc}")
        rc = 2
    except LedgerCorrupt as exc:
        print(f"ERROR: ledger does not validate: {exc}")
        return 2
    print(f"Executed {executed}, skipped {skipped}.")
    _finish(root, client)
    return rc


def followup(root: Path, date: str, frm, to, confirm_paid: bool) -> int:
    try:
        date = _iso_z(date, "--date")
        if (frm is None) != (to is None):
            raise ArgError("--from and --to must be given together")
        if frm is not None:
            frm, to = _iso_z(frm, "--from"), _iso_z(to, "--to")
    except ArgError as exc:
        print(f"Refused: {exc}. No request was made.")
        return 2
    if not confirm_paid:
        print("Refused: --followup makes a paid API call. Re-run with --confirm-paid. No request was made.")
        return 2
    try:
        client = OddsClient(root=root)
        attempted = len(followup_fingerprints(client.ledger.rows()))
    except (OddsClientError, LedgerCorrupt) as exc:
        print(f"ERROR: {exc}")
        return 2
    if attempted >= MAX_FOLLOWUPS:
        print(f"Refused: {attempted} follow-ups already attempted; the limit is {MAX_FOLLOWUPS}. "
              "No request was made.")
        return 2

    params = {"date": date, "dateFormat": "iso"}
    if frm is not None:
        params["commenceTimeFrom"], params["commenceTimeTo"] = frm, to
    n = attempted + 1
    purpose = f"{PURPOSE_FOLLOWUP} {n}/{MAX_FOLLOWUPS} (RESERVE-designated)"
    rc = 0
    try:
        result, events = _call_probe(client, f"F{n}", params, purpose, use_reserve=True)
        print(f"F{n}: {date} -> {len(events)} events, cost {result['actual_cost']}")
    except (_Stop, OddsClientError) as exc:
        print(f"ERROR: {exc}")
        rc = 2
    _finish(root, client)
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stage_a", description="Stage A coverage discovery")
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="print the requests and maximum costs; no network")
    g.add_argument("--run", action="store_true", help="execute the probes (requires --confirm-paid)")
    g.add_argument("--followup", action="store_true", help="run one follow-up probe (requires --confirm-paid)")
    p.add_argument("--confirm-paid", action="store_true")
    p.add_argument("--include-participants", action="store_true")
    p.add_argument("--date")
    p.add_argument("--from", dest="from_")
    p.add_argument("--to")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not args.followup and (args.date or args.from_ or args.to):
        parser.error("--date/--from/--to are only valid with --followup")
    if args.followup and not args.date:
        parser.error("--followup requires --date")
    if args.plan:
        return print_plan(root, args.include_participants)
    if args.run:
        return run(root, args.confirm_paid, args.include_participants)
    return followup(root, args.date, args.from_, args.to, args.confirm_paid)


if __name__ == "__main__":
    sys.exit(main())
