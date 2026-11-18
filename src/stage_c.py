"""Stage C: adaptive pregame movement chains for the two Stage A anchors.

Every call goes through OddsClient (stage="C"). This module imports no HTTP library.
Raw files in pilot/raw are only ever opened read-only ("rb").

Snapshots at anchor start minus 48h, 24h, 6h and 1h, in two tranches of at most 120 credits:
  tranche 1 (recommended first): C2, C4, C6, C8   (start-24h, start-1h)
  tranche 2 (deferred pending user review): C1, C3, C5, C7   (start-48h, start-6h)

  --plan                                                    both tranches, per-step and per-tranche max cost
  --run --step C2[,C4,...] --confirm-paid                   listed steps sequentially, each once
  --run --step C1[,...] --confirm-paid --approve-tranche-2  tranche 2 steps need the extra flag

Executed vs not-run is derived from the ledger, so a restart cannot lose track. Any HALT,
unexpected cost, non-200, malformed body or persistence failure stops the command; later
steps in the list are skipped. An anchor missing from a snapshot, or an empty payload, is a
finding, not a failure.

Every count in the report is of events in sampled snapshots: not games, not matched to
results. Unchanged repeated prices are not movement.

Run: .\\.venv\\Scripts\\python.exe -m src.stage_c --plan
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path

from . import halt as H
from . import normalize as N
from . import stage_b as SB
from .ledger import Ledger, LedgerCorrupt
from .odds_client import OddsClient, OddsClientError, fingerprint, load_config, redact_params

ODDS_ENDPOINT = SB.ODDS_ENDPOINT
STAGE = "C"
COST = (0, 30)
REGIONS = "us"
MARKETS = "h2h,spreads,totals"
TARGET_BOOKS = N.TARGET_BOOKS
REPORT_MARKETS = N.MARKETS

STEP_ORDER = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
STEPS = {
    "C1": {"anchor_id": "quinnipiac_air_force", "hours": 48, "tranche": 2},
    "C2": {"anchor_id": "quinnipiac_air_force", "hours": 24, "tranche": 1},
    "C3": {"anchor_id": "quinnipiac_air_force", "hours": 6, "tranche": 2},
    "C4": {"anchor_id": "quinnipiac_air_force", "hours": 1, "tranche": 1},
    "C5": {"anchor_id": "njit_vermont", "hours": 48, "tranche": 2},
    "C6": {"anchor_id": "njit_vermont", "hours": 24, "tranche": 1},
    "C7": {"anchor_id": "njit_vermont", "hours": 6, "tranche": 2},
    "C8": {"anchor_id": "njit_vermont", "hours": 1, "tranche": 1},
}
ANCHOR_ORDER = ["quinnipiac_air_force", "njit_vermont"]
# Stage B close-proxy snapshot per anchor (B1/B2 primaries only; B3 is h2h-only with us,us2).
CLOSE_PROXY_STEP = {SB.STEPS[s]["anchor_id"]: s for s in ("B1", "B2")}

NOT_RUN_T1 = "NOT_RUN (tranche 1, ready)"
NOT_RUN_T2 = "NOT_RUN (tranche 2: deferred, needs --approve-tranche-2)"
ANCHOR_PRESENT = "ANCHOR_PRESENT"
ANCHOR_ABSENT = "ANCHOR_ABSENT"
EMPTY_PAYLOAD = "EMPTY_PAYLOAD"
HALTED = "HALTED"
REFUSED = "REFUSED"
USABLE = {ANCHOR_PRESENT, ANCHOR_ABSENT, EMPTY_PAYLOAD}


class StageCError(Exception):
    pass


# ------------------------------------------------------------------ steps
def tranche_steps(tranche: int) -> list[str]:
    return [s for s in STEP_ORDER if STEPS[s]["tranche"] == tranche]


def not_run_status(step: str) -> str:
    return NOT_RUN_T1 if STEPS[step]["tranche"] == 1 else NOT_RUN_T2


def step_params(step: str, anchors: dict) -> dict:
    spec = STEPS[step]
    anchor = anchors.get(spec["anchor_id"])
    if anchor is None:
        raise StageCError(f"anchor {spec['anchor_id']} is not marked found in anchors.json")
    date = anchor["start"] - timedelta(hours=spec["hours"])
    return {"date": SB.fmt_ts(date), "regions": REGIONS, "markets": MARKETS,
            "oddsFormat": "american", "dateFormat": "iso"}


def purpose_for(step: str) -> str:
    return f"stage C step {step} {STEPS[step]['anchor_id']}"


def parse_purpose(purpose: str):
    p = purpose.split()
    if len(p) >= 4 and p[:3] == ["stage", "C", "step"] and p[3] in STEPS:
        return p[3]
    return None


def parse_step_list(text: str) -> list[str]:
    return [s.strip().upper() for s in str(text or "").split(",") if s.strip()]


# ------------------------------------------------------------------ ledger
def collect_attempts(rows) -> dict:
    """step -> {'first': (line, row), 'row200': row or None, 'last': row}. Ledger line numbers count the
    header as line 1."""
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


def halt_fingerprints(ledger_dir: Path):
    """(fingerprint of the active HALT or '', {fingerprint: code} for HALTs recorded in history)."""
    active = H.read_halt(ledger_dir / "HALT") or {}
    hist = {}
    try:
        lines = (ledger_dir / "halt_history.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            h = json.loads(line).get("halt") or {}
        except ValueError:
            continue
        if h.get("fingerprint"):
            hist[h["fingerprint"]] = h.get("reason_code", "")
    return active.get("fingerprint", ""), active.get("reason_code", ""), hist


def read_raw(path_text: str, root: Path):
    raw = Path(path_text)
    if not raw.exists():
        raw = Path(root) / "pilot" / "raw" / raw.name
    with open(raw, "rb") as fh:                          # read-only
        return json.loads(fh.read().decode("utf-8"))


def evaluate_step(step: str, att, anchors: dict, root: Path, halts) -> dict:
    spec = STEPS[step]
    anchor = anchors.get(spec["anchor_id"])
    ev = {"step": step, "tranche": spec["tranche"], "anchor_id": spec["anchor_id"], "hours": spec["hours"],
          "anchor_event_id": anchor["event_id"] if anchor else "", "anchor_start": anchor["start"] if anchor else None,
          "requested": "", "meta": {}, "events": [], "n_events": None, "seconds_to_start": None,
          "anchor_present": None, "books": [], "cost": 0, "ledger_status": "-", "fingerprint": "", "notes": [],
          "executed": att is not None}
    if att is None:
        ev["status"] = not_run_status(step)
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
        meta, events = SB.parse_odds_wrapper(read_raw(row["raw_file"], root))
    except (OSError, ValueError, SB.StageBParseError) as exc:
        ev["status"] = HALTED
        ev["notes"].append(f"saved response unusable ({type(exc).__name__})")
        return ev
    returned = SB.parse_ts(meta["timestamp"])
    ev.update(meta=meta, events=events, n_events=len(events))
    if returned and anchor:
        ev["seconds_to_start"] = int((anchor["start"] - returned).total_seconds())
    target = next((e for e in events if e.get("id") == ev["anchor_event_id"]), None)
    ev["anchor_present"] = target is not None
    if target is not None:
        idx = N.book_index(target)
        for b in TARGET_BOOKS:
            mk = {k: N.first_market(idx.get(b), k) for k in REPORT_MARKETS}
            ev["books"].append({"key": b, **{k: N.classify_market(target, mk[k]) for k in REPORT_MARKETS},
                                **{f"{k}_lu": (mk[k] or {}).get("last_update", "") for k in REPORT_MARKETS}})
    if not SB.cost_ok(len(events), row["actual_cost"], COST[1]):
        ev["notes"].append(f"cost check FAILED (actual {row['actual_cost']} with {len(events)} events)")
    if active_fp and active_fp == row["fingerprint"]:
        ev["status"] = HALTED
        ev["notes"].append(f"HALT {active_code} active for this request; excluded from movement tables")
        return ev
    if row["fingerprint"] in hist:
        ev["notes"].append(f"HALT {hist[row['fingerprint']]} was raised and cleared")
    if not events:
        ev["status"] = EMPTY_PAYLOAD
    else:
        ev["status"] = ANCHOR_PRESENT if target is not None else ANCHOR_ABSENT
    return ev


def evaluate_all(root: Path, rows, anchors: dict) -> dict:
    atts = collect_attempts(rows)
    halts = halt_fingerprints(Path(root) / "pilot" / "ledger")
    return {s: evaluate_step(s, atts.get(s), anchors, root, halts) for s in STEP_ORDER}


# --------------------------------------------------------------- snapshots
def close_proxy_snapshots(root: Path) -> dict:
    """anchor_id -> Stage B close-proxy snapshot {'label', 'meta', 'events'} (read-only), when available."""
    try:
        snaps = N.load_snapshots(Path(root))
    except N.NormalizeError:
        return {}
    by_step = {s["step"]: s for s in N.good(snaps)}
    out = {}
    for anchor_id, step in CLOSE_PROXY_STEP.items():
        s = by_step.get(step)
        if s:
            out[anchor_id] = {"label": f"{step} close-proxy", "meta": s["meta"], "events": s["events"]}
    return out


def chain(anchor_id: str, evaluated: dict, close_proxy: dict) -> dict:
    """Distinct snapshots of one anchor chain, in returned-timestamp order.

    Duplicates are resolved by a fixed priority (Stage B close-proxy, then C1..C8), never by execution
    order: a later-priority snapshot with an already-seen timestamp adds no observation for any event.
    """
    cands = []
    cp = close_proxy.get(anchor_id)
    if cp:
        cands.append({"label": cp["label"], "meta": cp["meta"], "events": cp["events"]})
    for s in STEP_ORDER:
        ev = evaluated[s]
        if ev["anchor_id"] == anchor_id and ev["status"] in USABLE:
            cands.append({"label": s, "meta": ev["meta"], "events": ev["events"]})
    kept, dup_of, untimed = {}, {}, []
    for c in cands:
        ts = SB.parse_ts(c["meta"].get("timestamp"))
        if ts is None:
            untimed.append(c["label"])
            continue
        c["ts"] = ts
        if ts in kept:
            dup_of[c["label"]] = kept[ts]["label"]
            continue
        kept[ts] = c
    return {"snapshots": [kept[t] for t in sorted(kept)], "dup_of": dup_of, "untimed": untimed}


# ----------------------------------------------------------------- movement
def _outcomes(market) -> dict:
    out = {}
    for o in (market or {}).get("outcomes") or []:
        if isinstance(o, dict) and isinstance(o.get("name"), str):
            out[o["name"]] = (o.get("price"), o.get("point"))
    return out


def _book_order(keys) -> list[str]:
    return [b for b in TARGET_BOOKS if b in keys] + sorted(k for k in keys if k not in TARGET_BOOKS)


def anchor_movement_rows(anchor_event_id: str, snapshots: list[dict]) -> list[dict]:
    """One row per (book, market, outcome) of the anchor event; cells per distinct snapshot."""
    cells = {}
    for i, s in enumerate(snapshots):
        e = next((x for x in s["events"] if x.get("id") == anchor_event_id), None)
        if e is None:
            continue
        for book, mkts in N.book_index(e).items():
            for k in REPORT_MARKETS:
                m = N.first_market(mkts, k)
                for name, (price, point) in _outcomes(m).items():
                    cells.setdefault((book, k, name), {})[i] = (price, point, m.get("last_update", ""))
    books = _book_order({k[0] for k in cells})
    rows = []
    for book in books:
        for k in REPORT_MARKETS:
            for name in sorted(n for (b, mk, n) in cells if b == book and mk == k):
                obs, prev = [], None
                for i in range(len(snapshots)):
                    c = cells[(book, k, name)].get(i)
                    if c is None:
                        obs.append(None)
                        continue
                    changed = prev is not None and (c[0], c[1]) != (prev[0], prev[1])
                    obs.append({"price": c[0], "point": c[1], "last_update": c[2], "changed": changed})
                    prev = c
                rows.append({"book": book, "market": k, "outcome": name, "obs": obs})
    return rows


def series_summary(snapshots: list[dict]) -> dict:
    """Per market (and 'all'): (event, book, market) series with >= 2 distinct snapshot observations."""
    series = {}
    for s in snapshots:
        for e in s["events"]:
            for book, mkts in N.book_index(e).items():
                for k in REPORT_MARKETS:
                    m = N.first_market(mkts, k)
                    if m is not None:
                        series.setdefault((e["id"], book, k), []).append(_outcomes(m))
    out = {k: {"observed": 0, "price_changed": 0, "point_changed": 0, "dpi": []} for k in REPORT_MARKETS + ("all",)}
    for (_, _, k), obs in series.items():
        if len(obs) < 2:
            continue
        price_chg, point_chg, max_dpi = False, False, None
        for a, b in zip(obs, obs[1:]):
            for name in set(a) & set(b):
                (pa, xa), (pb, xb) = a[name], b[name]
                if pa != pb:
                    price_chg = True
                    qa, qb = N.american_to_prob(pa), N.american_to_prob(pb)
                    if qa is not None and qb is not None:
                        max_dpi = max(max_dpi or 0.0, abs(qb - qa))
                if xa != xb:
                    point_chg = True
        for key in (k, "all"):
            out[key]["observed"] += 1
            out[key]["price_changed"] += price_chg
            out[key]["point_changed"] += point_chg
            if price_chg and max_dpi is not None:
                out[key]["dpi"].append(max_dpi)
    return out


def other_games(ev: dict, close_proxy: dict) -> dict | None:
    cp = close_proxy.get(ev["anchor_id"])
    if cp is None:
        return None
    ids = {e["id"] for e in ev["events"]}
    cp_ids = {e["id"] for e in cp["events"]}
    overlap = [e for e in ev["events"] if e["id"] in cp_ids]
    return {"events": len(ids), "in_close_proxy": len(overlap), "dist": N.target_book_distribution(overlap),
            "only_here": len(ids - cp_ids), "only_close_proxy": len(cp_ids - ids), "cp_label": cp["label"]}


# ------------------------------------------------------------------ report
def _f(v) -> str:
    return "-" if v in (None, "") else str(v)


def _cell(o) -> str:
    if o is None:
        return "-"
    txt = _f(o["price"]) + (f" / {o['point']}" if o["point"] is not None else "")
    if o["changed"]:
        txt += " [changed]"
    return txt + f" (lu {_f(o['last_update'])})"


def credits(root: Path, evaluated: dict) -> dict:
    cfg = load_config(Path(root) / "pilot" / "config" / "pilot.yaml")
    ledger_dir = Path(root) / "pilot" / "ledger"
    summ = Ledger(ledger_dir / "credit_ledger.csv", ledger_dir / "baseline.json").summary(cfg["stage_budgets"])
    unspent = {t: COST[1] * sum(1 for s in tranche_steps(t) if not evaluated[s]["executed"]) for t in (1, 2)}
    return {"stage": summ["per_stage"].get(STAGE, 0), "budget": cfg["stage_budgets"][STAGE],
            "committed": summ["committed"], "cap": cfg["credit_cap"],
            "remaining": cfg["credit_cap"] - summ["committed"], "unspent": unspent}


def build_report(root: Path, evaluated: dict, anchors: dict) -> str:
    close_proxy = close_proxy_snapshots(root)
    chains = {a: chain(a, evaluated, close_proxy) for a in ANCHOR_ORDER}
    dup_of = {k: v for c in chains.values() for k, v in c["dup_of"].items()}

    L = ["# Stage C report: pregame movement chains (sampled snapshots)", "",
         "Every count is of **events in sampled snapshots**: not games, and not matched to results. "
         "No results data is joined. A repeated identical price is not movement.", "",
         "## 1. Step table", ""]
    L.append(N.md(["Step", "Tranche", "Status", "Anchor", "Offset", "Requested date", "Returned timestamp",
                   "Seconds to start", "Events in payload", "Anchor status", "Cost", "Ledger status", "Notes"],
                  [[s, ev["tranche"], ev["status"], ev["anchor_id"], f"start-{ev['hours']}h",
                    _f(ev["requested"]) if ev["executed"] else "-", _f(ev["meta"].get("timestamp")),
                    _f(ev["seconds_to_start"]), _f(ev["n_events"]),
                    {True: "present", False: "absent", None: "-"}[ev["anchor_present"]], _f(ev["cost"]),
                    ev["ledger_status"],
                    "; ".join(([f"same snapshot as {dup_of[s]} (adds no observation)"] if s in dup_of else [])
                              + ev["notes"])]
                   for s, ev in evaluated.items()]))
    L += ["", "NOT_RUN is not a failure. Tranche 2 steps stay deferred until the user approves them."]

    run = [ev for ev in evaluated.values() if ev["meta"]]
    if run:
        L += ["", "### Snapshot detail and anchor classification (target books)", "",
              N.md(["Step", "previous_timestamp", "next_timestamp", "Book", "h2h", "spreads", "totals",
                    "h2h last_update", "spreads last_update", "totals last_update"],
                   [[ev["step"], _f(ev["meta"].get("previous_timestamp")), _f(ev["meta"].get("next_timestamp")),
                     b["key"], b["h2h"], b["spreads"], b["totals"], _f(b["h2h_lu"]), _f(b["spreads_lu"]),
                     _f(b["totals_lu"])] for ev in run for b in ev["books"]]
                   + [[ev["step"], _f(ev["meta"].get("previous_timestamp")), _f(ev["meta"].get("next_timestamp")),
                       "(anchor absent)" if ev["n_events"] else "(empty payload)", "-", "-", "-", "-", "-", "-"]
                      for ev in run if not ev["books"]]),
              "", "Classification uses normalize.classify_market (spreads: points summing to zero are paired)."]

    L += ["", "## 2. Anchor movement tables", "",
          "Columns are distinct returned snapshots in timestamp order (Stage B close-proxy included). Cell: "
          "price / point (lu = market-level last_update). [changed] = price or point differs from the previous "
          "observation in the same row. A repeated identical price is NOT movement."]
    for a in ANCHOR_ORDER:
        c = chains[a]
        anchor = anchors.get(a)
        L += ["", f"### {a} (event {anchor['event_id'] if anchor else '-'}, start "
                  f"{SB.fmt_ts(anchor['start']) if anchor else '-'})", ""]
        snaps = c["snapshots"]
        if not snaps:
            L.append("No usable snapshots for this anchor yet.")
            continue
        rows = anchor_movement_rows(anchor["event_id"], snaps) if anchor else []
        heads = [f"{s['label']} {SB.fmt_ts(s['ts'])}" for s in snaps]
        if rows:
            L.append(N.md(["Book", "Market", "Outcome"] + heads,
                          [[r["book"], r["market"], r["outcome"]] + [_cell(o) for o in r["obs"]] for r in rows]))
        else:
            L.append("Anchor event not present in any usable snapshot.")
        if len(snaps) < 2:
            L.append("\nOnly one distinct snapshot so far: no movement can be observed yet.")
        for lbl, first in c["dup_of"].items():
            L.append(f"\n{lbl}: same snapshot as {first}; not a separate column.")
        for lbl in c["untimed"]:
            L.append(f"\n{lbl}: no parseable returned timestamp; not placed in the table.")

    L += ["", "## 3. Cross-event movement summary", "",
          "Series = one (event, bookmaker, market) with at least 2 distinct snapshot observations in the same anchor "
          "chain (Stage C snapshots + Stage B close-proxy), all bookmakers. A price or point change is a difference "
          "between consecutive observations of the same outcome. Median |delta pi|: per price-changed series, the "
          "largest absolute change in implied probability of any outcome between consecutive observations; median "
          "across those series. Counts are of events, not games; no results joined."]
    for a in ANCHOR_ORDER:
        snaps = chains[a]["snapshots"]
        summ = series_summary(snaps)
        L += ["", f"### {a} ({len(snaps)} distinct snapshots)", "",
              N.md(["Market", "Series observed", "Any price change", "Any point change", "Median |delta pi| (changed)"],
                   [[k, v["observed"], v["price_changed"], v["point_changed"],
                     f"{statistics.median(v['dpi']):.4f}" if v["dpi"] else "-"] for k, v in summ.items()])]

    L += ["", "## 4. Other-games capture (events in sampled snapshots, not games, not matched to results)", "",
          "Overlap = events in this snapshot whose event_id is also in the anchor's Stage B close-proxy snapshot. "
          "The 1/2/3-book columns count overlap events with complete_paired h2h at exactly that many target books "
          "(draftkings, fanduel, betmgm) in this snapshot.", ""]
    og_rows = []
    for s, ev in evaluated.items():
        if ev["status"] not in USABLE:
            continue
        if s in dup_of:
            og_rows.append([s, f"same snapshot as {dup_of[s]} (adds no observation)"] + ["-"] * 7)
            continue
        og = other_games(ev, close_proxy)
        if og is None:
            og_rows.append([s, "no close-proxy snapshot", ev["n_events"]] + ["-"] * 6)
            continue
        og_rows.append([s, og["cp_label"], og["events"], og["in_close_proxy"], og["dist"][1], og["dist"][2],
                        og["dist"][3], og["only_here"], og["only_close_proxy"]])
    L.append(N.md(["Step", "Compared with", "Events in payload", "Also in close-proxy", "h2h at 1 book",
                   "h2h at 2 books", "h2h at 3 books", "Only in this snapshot", "Only in close-proxy"], og_rows)
             if og_rows else "No executed Stage C steps with a usable payload yet.")

    cr = credits(root, evaluated)
    L += ["", "## 5. Credits", "",
          f"- Stage C spent (pessimistic, from the ledger): {cr['stage']} of {cr['budget']}",
          f"- Cumulative committed across all stages: {cr['committed']}",
          f"- Remaining against the {cr['cap']} cap: {cr['remaining']}",
          f"- Maximum still unspent: tranche 1 {cr['unspent'][1]}, tranche 2 {cr['unspent'][2]} "
          "(not-run steps are attributed 0 credits)",
          "", "## 6. Scope", "",
          "- Sampled snapshots only (Stage C plus the Stage B close-proxy). No claim is made about typical movement "
          "beyond what is observed here.",
          "- Event counts, not games; no results data is joined; nothing here assesses T1 to T3.",
          "- Snapshot times are the returned timestamps; quotes inside a snapshot can be older (see last_update)."]
    return "\n".join(L) + "\n"


def write_report(root: Path, evaluated: dict, anchors: dict):
    text = build_report(root, evaluated, anchors)
    out = Path(root) / "pilot" / "normalized" / "stage_c_report.md"
    H.atomic_write_text(out, text)
    return text, out


# --------------------------------------------------------------------- CLI
def _ledger(root: Path) -> Ledger:
    d = Path(root) / "pilot" / "ledger"
    return Ledger(d / "credit_ledger.csv", d / "baseline.json")


def print_plan(root: Path) -> int:
    cfg = load_config(root / "pilot" / "config" / "pilot.yaml")
    try:
        anchors = SB.load_anchors(root)
    except SB.StageBError as exc:
        print(f"Cannot plan: {exc}. No request was made.")
        return 2
    try:
        evaluated = evaluate_all(root, _ledger(root).rows(), anchors)
    except LedgerCorrupt:
        evaluated = None
        print("(ledger unreadable: step status unknown)")
    print(f"Stage C plan: pregame movement chains, stage budget {cfg['stage_budgets'][STAGE]}")
    print(f"endpoint: GET {ODDS_ENDPOINT}")
    titles = {1: "Tranche 1 (recommended first; needs --confirm-paid)",
              2: "Tranche 2 (deferred pending user review; needs --confirm-paid AND --approve-tranche-2)"}
    for t in (1, 2):
        steps = tranche_steps(t)
        print(f"{titles[t]}: max {COST[1] * len(steps)} credits")
        for s in steps:
            spec = STEPS[s]
            try:
                ptxt = " ".join(f"{k}={v}" for k, v in step_params(s, anchors).items())
            except StageCError as exc:
                ptxt = f"(cannot compute: {exc})"
            status = evaluated[s]["status"] if evaluated else "unknown"
            print(f"  {s}  anchor={spec['anchor_id']}  start-{spec['hours']}h  {ptxt}  expected cost "
                  f"{COST[0]}..{COST[1]}  max cost {COST[1]}  status={status}")
        if evaluated:
            left = COST[1] * sum(1 for s in steps if not evaluated[s]["executed"])
            print(f"  tranche {t} maximum still unspent: {left}")
    print(f"total maximum: {COST[1] * len(STEP_ORDER)} credits of {cfg['stage_budgets'][STAGE]} (Stage C budget).")
    print("No request was made.")
    return 0


def refusals(steps: list[str], confirm_paid: bool, approve_t2: bool, attempts: dict) -> list[str]:
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
    t2 = [s for s in steps if s in STEPS and STEPS[s]["tranche"] == 2]
    if t2 and not approve_t2:
        out.append(f"{', '.join(t2)} {'is a tranche 2 step' if len(t2) == 1 else 'are tranche 2 steps'} "
                   "(deferred pending user review); add --approve-tranche-2")
    for s in steps:
        if s in attempts:
            line, row = attempts[s]["first"]
            out.append(f"{s} is already recorded in the ledger (line {line}, ts {row['ts_utc']}, status "
                       f"{row['status_code']}); each step runs once")
    return out


def run_steps(root: Path, step_text: str, confirm_paid: bool, approve_t2: bool) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    steps = parse_step_list(step_text)
    try:
        attempts = collect_attempts(_ledger(root).rows())
    except LedgerCorrupt as exc:
        print(f"Refused: {exc}. No request was made.")
        return 2
    reasons = refusals(steps, confirm_paid, approve_t2, attempts)
    if not reasons:
        try:
            anchors = SB.load_anchors(root)
            plans = {s: step_params(s, anchors) for s in steps}
        except (SB.StageBError, StageCError) as exc:
            reasons.append(str(exc))
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

    rc, done, log = 0, [], []
    for i, step in enumerate(steps):
        params = plans[step]
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
                    H.write_halt(client.halt_path, "UNEXPECTED_COST", text, fingerprint=fp,
                                 evidence=[result["raw_file"]])
                    log.append(f"HALT UNEXPECTED_COST: {text}")
                    rc = 2
                else:
                    done.append(step)
                    log.append(f"{step}: executed (cost {result['actual_cost']}, {len(events)} events)")
        if rc:
            if steps[i + 1:]:
                log.append(f"Skipped (not requested): {', '.join(steps[i + 1:])}")
            break

    print("This invocation:")
    for line in log:
        print(f"  {line}")
    try:
        evaluated = evaluate_all(root, client.ledger.rows(), anchors)
        text, out = write_report(root, evaluated, anchors)
    except Exception as exc:  # noqa: BLE001 - the ledger already records the outcome
        print(f"Could not write the report: {type(exc).__name__}: {exc}")
        return 2
    print()
    print(text)
    print(f"(report written to {out.name})")
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stage_c", description="Stage C pregame movement chains")
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--run", action="store_true")
    p.add_argument("--step", help="comma list, e.g. C2,C4,C6,C8")
    p.add_argument("--confirm-paid", action="store_true")
    p.add_argument("--approve-tranche-2", action="store_true")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.plan:
        if args.step or args.confirm_paid or args.approve_tranche_2:
            parser.error("--plan takes no --step, --confirm-paid or --approve-tranche-2")
        return print_plan(root)
    if not args.step:
        parser.error("--run requires --step")
    return run_steps(root, args.step, args.confirm_paid, args.approve_tranche_2)


if __name__ == "__main__":
    sys.exit(main())
