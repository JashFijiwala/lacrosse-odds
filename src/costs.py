"""Calendar and full-acquisition cost estimator. OFFLINE: no network, no credits.

Reads saved data only: the ledger and the raw files it names (Stage B/C/D snapshots, "rb"), the Stage A
event list (pilot/normalized/stage_a_events.csv, read-only) and, by import, the offline helpers in
analysis.py (which itself imports no HTTP library). Imports no HTTP library and not OddsClient.

Every figure carries one label:
  MEASURED             computed from saved data
  PROVIDER-DOCUMENTED  credit formulas published by the provider
  ASSUMPTION           an unmeasured input, or arithmetic on one

Nothing here establishes season coverage or sufficiency. No results data is joined.

Writes pilot/normalized/cost_report.md (also printed).

Run: .\\.venv\\Scripts\\python.exe -m src.costs
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import analysis as A
from . import normalize as N

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception as exc:                                             # pragma: no cover - missing tz database
    ET = None
    _TZ_ERROR = f"{type(exc).__name__}: {exc}"

MEASURED, DOCUMENTED, ASSUMPTION = "MEASURED", "PROVIDER-DOCUMENTED", "ASSUMPTION"
LABELS = (MEASURED, DOCUMENTED, ASSUMPTION)

PLAN_SMALL, PLAN_LARGE = 20_000, 100_000                             # $30 and $59 plans
GRID_G = (25, 40, 60)                                                # game days per season
GRID_S = (1, 3, 6, 9)                                                # start-slot snapshots per game day
GRID_SEASONS = (1, 2)
GRID_C = ((10, "h2h only"), (20, "any two markets"), (30, "all three markets"))
SCAN_K = (1, 2, 3)                                                   # days between events requests
SCAN_L = (90, 120)                                                   # season length in days
N_GAMES = (300, 500, 600)
EVENT_BINS = ((1, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30), (31, None))


class CostsError(Exception):
    pass


# ---------------------------------------------------------- credit formulas
def odds_credits(markets: int, regions: int) -> int:
    """PROVIDER-DOCUMENTED: historical odds = 10 x markets x regions per request."""
    return 10 * markets * regions


def events_credits(nonempty: bool) -> int:
    """PROVIDER-DOCUMENTED: historical events = 1 per non-empty request, 0 if empty."""
    return 1 if nonempty else 0


def event_odds_credits(unique_markets: int, regions: int) -> int:
    """PROVIDER-DOCUMENTED: historical event-odds = 10 x unique markets x regions per request."""
    return 10 * unique_markets * regions


def grid_total(g: int, s: int, c: int, seasons: int, regions: int = 1) -> int:
    """ASSUMPTION arithmetic: total = G x s x c x seasons (c doubles with regions=us,us2)."""
    return g * s * c * regions * seasons


def fits(total: int, plan: int) -> bool:
    return total <= plan


def grid_rows() -> list[dict]:
    rows = []
    for seasons in GRID_SEASONS:
        for c, desc in GRID_C:
            for g in GRID_G:
                for s in GRID_S:
                    t1, t2 = grid_total(g, s, c, seasons, 1), grid_total(g, s, c, seasons, 2)
                    rows.append({"seasons": seasons, "c": c, "desc": desc, "G": g, "s": s, "us": t1, "us_us2": t2,
                                 "us_20k": fits(t1, PLAN_SMALL), "us_100k": fits(t1, PLAN_LARGE),
                                 "us2_20k": fits(t2, PLAN_SMALL), "us2_100k": fits(t2, PLAN_LARGE)})
    return rows


def scan_requests(season_days: int, every_k: int) -> int:
    return math.ceil(season_days / every_k)


def break_even_games_per_slot(markets_snapshot: int, markets_event: int) -> float:
    """Same regions on both sides: snapshot = 10 m_s r, event-odds per game = 10 m_e r."""
    return markets_snapshot / markets_event


# ----------------------------------------------------- ledger evidence
def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def endpoint_kind(endpoint: str):
    if not endpoint.startswith("/v4/historical/"):
        return None
    if re.search(r"/events/[^/]+/odds$", endpoint):
        return "event_odds"
    if endpoint.endswith("/odds"):
        return "odds"
    if endpoint.endswith("/events"):
        return "events"
    return None


def _payload_nonempty(row: dict, root: Path):
    """True/False from the raw file (read-only); None if it cannot be read."""
    raw = Path(row.get("raw_file", ""))
    if not raw.exists():
        raw = Path(root) / "pilot" / "raw" / raw.name
    try:
        with open(raw, "rb") as fh:
            obj = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    data = obj.get("data") if isinstance(obj, dict) else obj
    return bool(data) if isinstance(data, (list, dict)) else None


def ledger_evidence(root: Path, rows=None) -> dict:
    """Group the successful historical ledger rows by cost-relevant shape and compare with the formulas."""
    rows = A.read_ledger_rows(root) if rows is None else rows
    groups = defaultdict(lambda: {"n": 0, "costs": Counter(), "unknown_payload": 0})
    for r in rows:
        kind = endpoint_kind(r.get("endpoint", ""))
        if kind is None or r.get("status_code") != "200":
            continue
        try:
            params = json.loads(r.get("params_redacted_json") or "{}")
        except ValueError:
            params = {}
        cost = _int(r.get("actual_cost"))
        nonempty = _payload_nonempty(r, root)
        if kind == "events":
            key = (kind, 0, 0, nonempty)
        else:
            m = len({x for x in str(params.get("markets", "")).split(",") if x})
            rg = len({x for x in str(params.get("regions", "")).split(",") if x})
            key = (kind, m, rg, nonempty)
        g = groups[key]
        g["n"] += 1
        g["costs"][cost] += 1
        if nonempty is None:
            g["unknown_payload"] += 1
    spent = [_int(r.get("cumulative_spent")) for r in rows]
    spent = [v for v in spent if v is not None]
    remaining = [_int(r.get("header_remaining")) for r in rows]
    remaining = [v for v in remaining if v is not None]
    return {"groups": dict(groups), "spent": max(spent) if spent else None,
            "remaining": remaining[-1] if remaining else None, "n_rows": len(rows)}


def predicted(kind: str, markets: int, regions: int, nonempty) -> int | None:
    if kind == "events":
        return None if nonempty is None else events_credits(nonempty)
    if kind == "odds":
        return odds_credits(markets, regions) if nonempty else None
    return event_odds_credits(markets, regions) if nonempty else None


def formula_rows(ev: dict) -> list[list]:
    """Table 1 rows: Item | Formula / prediction | Ledger evidence | Status | Label."""
    out = [
        ["historical odds", "10 x markets x regions per request", "-", "formula", DOCUMENTED],
        ["historical events", "1 per non-empty request, 0 if empty", "-", "formula", DOCUMENTED],
        ["historical event-odds", "10 x unique markets x regions per request", "-", "formula", DOCUMENTED],
    ]

    def status(kind, m, r, ne):
        g = ev["groups"].get((kind, m, r, ne))
        if not g:
            return "0 rows", "NOT YET OBSERVED"
        pred = predicted(kind, m, r, ne)
        obs = ", ".join(f"{c} x{n}" for c, n in sorted(g["costs"].items(), key=lambda kv: (kv[0] is None, kv[0])))
        ok = pred is not None and set(g["costs"]) == {pred}
        return f"{g['n']} rows, observed cost {obs}", ("CONFIRMED" if ok else "MISMATCH")

    for m in (1, 2, 3):
        for r in (1, 2):
            ev_s, st = status("odds", m, r, True)
            out.append([f"odds, {m} market(s) x {r} region(s)", f"{odds_credits(m, r)} per non-empty request", ev_s,
                        st, MEASURED])
    ev_s, st = status("odds", 3, 1, False)
    out.append(["odds, empty payload", "0 (project assumption in stage_b.cost_ok; not a documented figure here)",
                ev_s, st, MEASURED])
    for ne in (True, False):
        ev_s, st = status("events", 0, 0, ne)
        out.append([f"events, {'non-empty' if ne else 'empty'} payload", f"{events_credits(ne)} per request",
                    ev_s, st, MEASURED])
    n_eo = sum(g["n"] for k, g in ev["groups"].items() if k[0] == "event_odds")
    out.append(["event-odds (any shape)", "10 x unique markets x regions",
                f"{n_eo} rows", "NOT YET OBSERVED" if not n_eo else "see ledger", MEASURED])
    return out


# ---------------------------------------------------------------- calendar
def _need_tz():
    if ET is None:
        raise CostsError("time zone database not available (install tzdata): " + _TZ_ERROR)


def local_dt(dt_utc: datetime) -> datetime:
    _need_tz()
    return dt_utc.astimezone(ET)


def local_date(dt_utc: datetime) -> Date:
    return local_dt(dt_utc).date()


def day_start_utc(d: Date) -> datetime:
    _need_tz()
    return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(timezone.utc)


def day_length_hours(d: Date) -> float:
    return (day_start_utc(d + timedelta(days=1)) - day_start_utc(d)).total_seconds() / 3600


def utc_offset_hours(dt_utc: datetime) -> int:
    return int(local_dt(dt_utc).utcoffset().total_seconds() // 3600)


def minute_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def possibly_partial(d: Date, earliest_cover_ts: datetime) -> bool:
    """An event on local date d could have started before the earliest snapshot that covers d if that snapshot
    was returned after the local day began. Events that started earlier would not be listed."""
    return earliest_cover_ts > day_start_utc(d)


def read_stage_a(root: Path):
    """Sightings from pilot/normalized/stage_a_events.csv (read-only). Returns (sightings, note)."""
    path = Path(root) / "pilot" / "normalized" / "stage_a_events.csv"
    if not path.exists():
        return [], "stage_a_events.csv not found; Stage A not used"
    out, bad = [], 0
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            ct, ts = N.parse_ts(r.get("commence_time_utc")), N.parse_ts(r.get("returned_timestamp"))
            if not r.get("event_id") or ct is None or ts is None:
                bad += 1
                continue
            out.append({"event_id": r["event_id"], "commence": ct, "snap_ts": ts, "source": f"A:{r.get('probe_id', '')}",
                        "kind": "A"})
    return out, f"{len(out)} Stage A event rows read" + (f"; {bad} unparseable skipped" if bad else "")


def snapshot_sightings(snaps) -> tuple[list[dict], int]:
    out, bad = [], 0
    for s in A.usable(snaps):
        for e in s["events"]:
            ct = A.parse_ts(e.get("commence_time"))
            if ct is None:
                bad += 1
                continue
            out.append({"event_id": e["id"], "commence": ct, "snap_ts": s["ts"], "source": s["step"], "kind": "BCD"})
    return out, bad


def build_calendar(sightings: list[dict]) -> dict:
    """Dedupe by event_id; group by LOCAL (America/New_York) date of the start. The start used for an event is
    the commence_time in the latest snapshot that lists it."""
    _need_tz()
    by_id = defaultdict(list)
    for x in sightings:
        by_id[x["event_id"]].append(x)
    events, conflicts, conflict_rows = {}, 0, []
    for eid, lst in by_id.items():
        lst = sorted(lst, key=lambda x: x["snap_ts"])
        latest = lst[-1]
        if len({x["commence"] for x in lst}) > 1:
            conflicts += 1
            by_time = defaultdict(list)
            for x in lst:
                by_time[x["commence"]].append(x["source"])
            conflict_rows.append({"event_id": eid, "used": minute_key(latest["commence"]),
                                  "times": {minute_key(t): srcs for t, srcs in sorted(by_time.items())}})
        events[eid] = {"commence": latest["commence"], "local_date": local_date(latest["commence"]),
                       "sightings": lst, "kinds": {x["kind"] for x in lst}}
    dates = {}
    for eid, e in events.items():
        d = dates.setdefault(e["local_date"], {"events": [], "starts": set(), "cover": {}, "slot_events": Counter()})
        d["events"].append(eid)
        d["starts"].add(minute_key(e["commence"]))
        d["slot_events"][minute_key(e["commence"])] += 1
        for x in e["sightings"]:
            d["cover"].setdefault(x["source"], x["snap_ts"])
    for d, info in dates.items():
        starts_local = sorted(local_dt(events[i]["commence"]) for i in info["events"])
        info["earliest_local"], info["latest_local"] = starts_local[0], starts_local[-1]
        src, ts = min(info["cover"].items(), key=lambda kv: (kv[1], kv[0]))
        info["first_cover_src"], info["first_cover_ts"] = src, ts
        info["window_h"] = (ts - day_start_utc(d)).total_seconds() / 3600
        info["partial"] = possibly_partial(d, ts)
        info["n_from_A"] = sum(1 for i in info["events"] if "A" in events[i]["kinds"])
        info["n_from_BCD"] = sum(1 for i in info["events"] if "BCD" in events[i]["kinds"])
        info["n_both"] = sum(1 for i in info["events"] if events[i]["kinds"] == {"A", "BCD"})
        info["offset_h"] = utc_offset_hours(info["earliest_local"].astimezone(timezone.utc))
        info["day_len_h"] = day_length_hours(d)
    utc_differs = sum(1 for e in events.values() if e["commence"].astimezone(timezone.utc).date() != e["local_date"])
    fixed5 = sum(1 for e in events.values()
                 if (e["commence"].astimezone(timezone.utc) - timedelta(hours=5)).date() != e["local_date"])
    return {"events": events, "dates": dict(sorted(dates.items())), "conflicts": conflicts,
            "conflict_rows": sorted(conflict_rows, key=lambda r: r["event_id"]),
            "utc_date_differs": utc_differs, "fixed_minus5_differs": fixed5}


def dst_transitions(d0: Date, d1: Date) -> list[dict]:
    """Local dates in [d0, d1] whose length is not 24h (a US clock change)."""
    out, d = [], d0
    while d <= d1:
        n = day_length_hours(d)
        if abs(n - 24) > 1e-9:
            out.append({"date": d, "hours": n,
                        "before": utc_offset_hours(day_start_utc(d)),
                        "after": utc_offset_hours(day_start_utc(d + timedelta(days=1)))})
        d += timedelta(days=1)
    return out


def hist_starts(dates: dict, only_complete=False) -> Counter:
    return Counter(len(i["starts"]) for i in dates.values() if not (only_complete and i["partial"]))


def bin_label(lo, hi) -> str:
    return f"{lo}+" if hi is None else (str(lo) if lo == hi else f"{lo}-{hi}")


def hist_events(dates: dict, only_complete=False) -> Counter:
    c = Counter()
    for i in dates.values():
        if only_complete and i["partial"]:
            continue
        n = len(i["events"])
        for lo, hi in EVENT_BINS:
            if n >= lo and (hi is None or n <= hi):
                c[bin_label(lo, hi)] += 1
                break
    return c


def slot_sizes(dates: dict, only_complete=False) -> list[int]:
    """Events per distinct (local date, start minute) slot."""
    return [n for i in dates.values() if not (only_complete and i["partial"]) for n in i["slot_events"].values()]


# ---------------------------------------------------------------- coverage
def tts_summary(snaps) -> list[dict]:
    out = []
    for s in A.usable(snaps):
        vals = []
        for e in s["events"]:
            ct = A.parse_ts(e.get("commence_time"))
            if ct is not None:
                vals.append((ct - s["ts"]).total_seconds() / 3600)
        out.append({"step": s["step"], "season": s["season"], "ts": A.fmt_ts(s["ts"]), "n": len(vals),
                    "min": min(vals) if vals else None, "median": statistics.median(vals) if vals else None,
                    "max": max(vals) if vals else None, "n_nonpregame": sum(1 for v in vals if v <= 0)})
    return out


COV_KEYS = [("h2h1", "h2h complete at >=1 target book"), ("h2h2", "h2h complete at >=2 target books"),
            ("h2h3", "h2h complete at 3 target books"), ("spreads1", "spreads complete at >=1 target book"),
            ("totals1", "totals complete at >=1 target book")]


def coverage(snaps, obs) -> dict:
    """Event-weighted (latest valid pregame observation per event-book-market). Denominator: distinct events with
    at least one pregame appearance (tts > 0) in a usable snapshot, including events with no target-book quote."""
    ew = A.event_weighted(obs)
    books = defaultdict(lambda: defaultdict(set))
    for o in ew:
        books[(o["event_id"])][o["market"]].add(o["book"])
    season_of, pregame = {}, set()
    for s in A.usable(snaps):
        for e in s["events"]:
            ct = A.parse_ts(e.get("commence_time"))
            if ct is not None and (ct - s["ts"]).total_seconds() > 0:
                pregame.add(e["id"])
                season_of.setdefault(e["id"], s["season"])
    out = {}
    for label, ids in (("all", pregame), ("2025", {i for i in pregame if season_of[i] == 2025}),
                       ("2026", {i for i in pregame if season_of[i] == 2026})):
        row = {"n": len(ids), "h2h1": 0, "h2h2": 0, "h2h3": 0, "spreads1": 0, "totals1": 0}
        for i in ids:
            h, sp, to = (len(books[i].get(m, ())) for m in ("h2h", "spreads", "totals"))
            row["h2h1"] += h >= 1
            row["h2h2"] += h >= 2
            row["h2h3"] += h >= 3
            row["spreads1"] += sp >= 1
            row["totals1"] += to >= 1
        out[label] = row
    return out


# ------------------------------------------------------------------ report
def md(headers, rows) -> str:
    return N.md(headers, rows)


def fnum(v, nd=1) -> str:
    return "-" if v is None else f"{v:.{nd}f}"


def yes(b) -> str:
    return "yes" if b else "no"


def build(root: Path) -> tuple[str, dict]:
    root = Path(root)
    _need_tz()
    inp = A.load_inputs(root)
    snaps = inp["snapshots"]
    if not A.usable(snaps):
        raise CostsError("no readable Stage B/C/D snapshots found in the ledger")
    obs = A.observations(snaps)
    tables: dict[str, tuple[list, list]] = {}
    L: list[str] = []

    def table(key, title, headers, rows, note=None):
        assert headers[-1] == "Label" and all(r[-1] in LABELS for r in rows), key
        tables[key] = (headers, rows)
        L.extend(["", f"### {title}", ""])
        if note:
            L.extend([note, ""])
        L.append(md(headers, rows))

    ev = ledger_evidence(root)
    a_sight, a_note = read_stage_a(root)
    s_sight, s_bad = snapshot_sightings(snaps)
    cal = build_calendar(a_sight + s_sight)
    dates = cal["dates"]
    cov = coverage(snaps, obs)

    L += ["# Calendar and full-acquisition cost estimator (offline)", "",
          "Offline: no network, no OddsClient, no credits. Reads saved data only. No results data is joined.", "",
          "Every figure is labelled:", "",
          "- **MEASURED**: computed from saved data (the ledger, the raw files, stage_a_events.csv).",
          "- **PROVIDER-DOCUMENTED**: a credit formula published by the provider.",
          "- **ASSUMPTION**: an unmeasured input, or arithmetic on one. Not a result.", "",
          "**Nothing here establishes season coverage or sufficiency.** The measured part describes only the sampled "
          "game days in the pilot's snapshots; the assumed parts are planning grids, not forecasts.",
          f"[MEASURED] Credits committed so far: {ev['spent']} of the pilot cap 500; plan balance "
          f"{ev['remaining']} of 20,000 (last header). Ledger rows: {ev['n_rows']}."]

    # 1
    L += ["", "## 1. Credit formulas (unit: credits per request)"]
    table("t1", "Table 1. Formulas and what the ledger confirms",
          ["Item", "Formula / prediction", "Ledger evidence", "Status", "Label"], formula_rows(ev),
          note="Rows labelled MEASURED compare each observed cost with the formula; CONFIRMED means every matching "
               "ledger row cost exactly the predicted amount. Only combinations that appear in the ledger can be "
               "confirmed; the grids below use the others on the strength of the documentation alone.")

    # 2
    L += ["", "## 2. Calendar from saved data (unit: events deduped by event_id; local dates in America/New_York)"]
    ndates = len(dates)
    n_ev = len(cal["events"])
    n_part = sum(1 for i in dates.values() if i["partial"])
    summ = [["Distinct events (Stage A + B/C/D, deduped by event_id)", n_ev, MEASURED],
            ["Stage A input", a_note, MEASURED],
            ["B/C/D snapshots used (B3 excluded)", ", ".join(s["step"] for s in A.usable(snaps)), MEASURED],
            ["Snapshot events with unparseable commence_time (skipped)", s_bad, MEASURED],
            ["Events whose commence_time differs between sightings (latest used)", cal["conflicts"], MEASURED],
            ["Local dates with at least one event", ndates, MEASURED],
            ["Dates flagged possibly partial", f"{n_part} of {ndates}", MEASURED],
            ["Events whose UTC date differs from their local date (e.g. 00:00Z)", cal["utc_date_differs"], MEASURED],
            ["Events whose local date would differ under a fixed UTC-5 offset", cal["fixed_minus5_differs"], MEASURED]]
    table("t2_summary", "Table 2a. Calendar summary", ["Item", "Value", "Label"], summ,
          note="Local date = the date of the start time in America/New_York (an event at 00:00Z belongs to the previous "
               "local date). The conversion uses the time-zone database, never a fixed offset.")

    rows = []
    for d, i in dates.items():
        rows.append([d.isoformat(), f"UTC{i['offset_h']:+d}" + (f" ({i['day_len_h']:.0f}h day)" if abs(i['day_len_h'] - 24) > 1e-9 else ""),
                     len(i["events"]), f"{i['n_from_A']} / {i['n_from_BCD']} / {i['n_both']}", len(i["starts"]),
                     i["earliest_local"].strftime("%H:%M"), i["latest_local"].strftime("%H:%M"),
                     A.fmt_ts(i["first_cover_ts"]) + f" ({i['first_cover_src']})",
                     fnum(i["window_h"]), "possibly partial" if i["partial"] else "no", MEASURED])
    table("t2_dates", "Table 2b. By local date (unit: events seen on that local date)",
          ["Local date", "UTC offset", "Events seen", "From A / B,C,D / both", "Distinct start times (UTC minute)",
           "Earliest local start", "Latest local start", "First snapshot that listed it (UTC)",
           "Hours from local midnight to that snapshot", "Partial?", "Label"], rows,
          note="A date is 'possibly partial' when the earliest snapshot that lists any of its events was returned after "
               "the local day began: an event that started in that earlier window would not be listed. This is "
               "conservative; it does not say such an event exists. Dates with a snapshot taken before local midnight "
               "are not flagged. Which snapshot 'covers' a date is the earliest one in which one of its events "
               "appears (sampled visibility), not a stated horizon of the endpoint.")

    hs, hs_c = hist_starts(dates), hist_starts(dates, True)
    rows = [[k, hs.get(k, 0), hs_c.get(k, 0), MEASURED] for k in range(1, max(hs, default=0) + 1)]
    table("t2_hist_starts", "Table 2c. Histogram: distinct start times per local date (unit: local dates)",
          ["Distinct start times", "Dates (all)", "Dates (not possibly partial)", "Label"], rows)
    he, he_c = hist_events(dates), hist_events(dates, True)
    rows = [[bin_label(lo, hi), he.get(bin_label(lo, hi), 0), he_c.get(bin_label(lo, hi), 0), MEASURED]
            for lo, hi in EVENT_BINS]
    table("t2_hist_events", "Table 2d. Histogram: events per local date (unit: local dates)",
          ["Events on the date", "Dates (all)", "Dates (not possibly partial)", "Label"], rows,
          note="Partial dates can under-count. The sampled dates are not a random sample of the season.")

    rows = [[c["event_id"], c["used"], "; ".join(f"{t}: {','.join(srcs)}" for t, srcs in c["times"].items()), MEASURED]
            for c in cal["conflict_rows"]]
    table("t2_conflicts", "Table 2e. Events listed with different commence_time values (unit: events)",
          ["event_id", "Start used (UTC minute, latest snapshot)", "Start time: snapshots listing it", "Label"],
          rows or [["none", "-", "-", MEASURED]],
          note="Where sightings disagree the latest snapshot's value decides the local date and start-time count. "
               "Nothing here says which value is right.")

    span = (min(dates), max(dates)) if dates else None
    rows = []
    for t in (dst_transitions(*span) if span else []):
        prev_n = len(dates[t["date"] - timedelta(days=1)]["events"]) if (t["date"] - timedelta(days=1)) in dates else 0
        rows.append([t["date"].isoformat(), f"{t['hours']:.0f}h", f"UTC{t['before']:+d} -> UTC{t['after']:+d}",
                     len(dates[t["date"]]["events"]) if t["date"] in dates else 0, prev_n, MEASURED])
    table("t2_dst", "Table 2f. DST changes inside the observed date span (unit: events on the date)",
          ["Local date of change", "Length of that local day", "UTC offset", "Events seen that day",
           "Events seen the day before", "Label"],
          rows or [["none in span", "-", "-", 0, 0, MEASURED]],
          note="US clocks change at 02:00 local (2025-03-09 and 2026-03-08). Start times before and after the change "
               "map to different UTC offsets; the date grouping above handles both.")

    # 3
    L += ["", "## 3. Horizon and coverage (unit: events in snapshots; reuses analysis.py)"]
    rows = [[t["step"], t["season"], t["ts"], t["n"], fnum(t["min"]), fnum(t["median"]), fnum(t["max"]),
             t["n_nonpregame"], MEASURED] for t in tts_summary(snaps)]
    table("t3_tts", "Table 3a. Time to start per snapshot, hours (unit: events in the snapshot)",
          ["Step", "Season", "Returned timestamp (UTC)", "Events", "tts min", "tts median", "tts max",
           "Events with tts <= 0", "Label"], rows,
          note="tts = the event's own commence_time minus the returned snapshot timestamp.")
    rows = []
    for label in ("all", "2025", "2026"):
        c = cov[label]
        rows.append([label, c["n"]] + [A.share(c[k], c["n"]) for k, _ in COV_KEYS] + [MEASURED])
    table("t3_cov", "Table 3b. Event-weighted coverage (unit: distinct events with a pregame appearance)",
          ["Group", "n events"] + [d for _, d in COV_KEYS] + ["Label"], rows,
          note="Rule: one observation per (event, book, market), the latest valid pregame one (complete_paired, "
               "tts > 0) in the B/C/D snapshots; target books draftkings, fanduel, betmgm. The denominator includes "
               "events with no target-book quote. These are sampled game days, so the fractions describe the samples, "
               "not the season.")

    # 4
    L += ["", "## 4. Full-acquisition grid (ASSUMPTION; regions=us unless stated)", "",
          "**G (game days per season) is NOT measured.** Neither is s (snapshots per game day). Total = G x s x c x "
          "seasons, with c the credits per snapshot (10 x markets x regions). regions=us,us2 doubles c. 'Fits' compares "
          f"the total with {PLAN_SMALL:,} credits (the $30 plan) and {PLAN_LARGE:,} credits (the $59 plan); credits "
          f"already spent in the pilot are not netted out."]
    rows = [[r["seasons"], f"{r['c']} ({r['desc']})", r["G"], r["s"], f"{r['us']:,}", yes(r["us_20k"]),
             yes(r["us_100k"]), f"{r['us_us2']:,}", yes(r["us2_20k"]), yes(r["us2_100k"]), ASSUMPTION]
            for r in grid_rows()]
    table("t4", "Table 4. Total credits = G x s x c x seasons (unit: credits)",
          ["Seasons", "c per snapshot", "G", "s", "Total (us)", "us fits 20,000", "us fits 100,000",
           "Total (us,us2)", "us,us2 fits 20,000", "us,us2 fits 100,000", "Label"], rows)

    # 5
    L += ["", "## 5. Calendar-discovery scan scenario (ASSUMPTION)", "",
          "One historical-events request every k days over a season of L days, at 1 credit per non-empty request "
          "(an upper bound: empty requests cost 0). It could replace the assumed G and s with measured values, but it "
          "only works if the events endpoint lists games that have no odds. That is **UNVERIFIED**."]
    horizons = defaultdict(float)
    for x in a_sight:
        horizons[x["source"]] = max(horizons[x["source"]], (x["commence"] - x["snap_ts"]).total_seconds() / 3600)
    if horizons:
        hv = sorted(horizons.values())
        L += ["", f"[MEASURED] Stage A: the {len(hv)} non-empty probes listed events up to {hv[0]:.1f} to {hv[-1]:.1f} "
                  f"hours (median {statistics.median(hv):.1f}) after their snapshot timestamp; whether a request every "
                  "2 or 3 days would list every game in between is not established."]
    rows = [[length, k, scan_requests(length, k), scan_requests(length, k), 2 * scan_requests(length, k), ASSUMPTION]
            for length in SCAN_L for k in SCAN_K]
    table("t5", "Table 5. Events-request scan (unit: credits)",
          ["Season length L (days)", "Every k days", "Requests per season", "Credits per season (max)",
           "Credits for two seasons (max)", "Label"], rows)

    # 6
    L += ["", "## 6. Per-event alternative via event-odds (ASSUMPTION)", "",
          "Near-close odds per game at 10 x markets x regions credits per game. **N (games) is NOT verified**; it would "
          "have to come from a schedule or results source. Event ids must also be known, which costs events requests "
          "(Table 5) or comes from a schedule source."]
    rows = [[n, m, r, f"{event_odds_credits(m, r):,}", f"{n * event_odds_credits(m, r):,}", ASSUMPTION]
            for n in N_GAMES for m in (1, 2, 3) for r in (1, 2)]
    table("t6a", "Table 6a. Event-odds total for N games (unit: credits)",
          ["N games", "Markets", "Regions", "Credits per game", "Total credits", "Label"], rows)
    sizes_all = slot_sizes(dates)
    sizes_ok = slot_sizes(dates, True)
    L += ["", f"[MEASURED] Events per distinct start time on the sampled dates: mean {fnum(statistics.mean(sizes_all) if sizes_all else None, 2)}, "
              f"median {fnum(statistics.median(sizes_all) if sizes_all else None, 1)} over {len(sizes_all)} date-start slots "
              f"(not possibly partial dates: mean {fnum(statistics.mean(sizes_ok) if sizes_ok else None, 2)} over {len(sizes_ok)} slots). "
              "These count every listed event, including those with no target-book odds."]
    rows = [[ms, f"{10 * ms}", me, f"{10 * me}", fnum(break_even_games_per_slot(ms, me), 2), ASSUMPTION]
            for ms in (1, 2, 3) for me in (1, 2, 3)]
    table("t6b", "Table 6b. Break-even games per snapshot slot (regions=1; same regions on both sides cancel)",
          ["Snapshot markets", "Snapshot credits", "Event-odds markets", "Event-odds credits per game",
           "Break-even games per slot", "Label"], rows,
          note="A snapshot costs the same however many games it shows, so it is cheaper than per-game event-odds when "
               "the games it serves per slot exceed the break-even value (snapshot markets / event-odds markets). "
               "Games that have no odds in the snapshot do not count as served.")

    # 7
    L += ["", "## 7. Usable-games illustration (illustrative, not a forecast)"]
    allc = cov["all"]
    rows = []
    for n in N_GAMES:
        for k, d in COV_KEYS:
            frac = (allc[k] / allc["n"]) if allc["n"] else None
            rows.append([n, d, A.share(allc[k], allc["n"]), "-" if frac is None else f"{n * frac:.0f}", ASSUMPTION])
    table("t7", "Table 7. N x measured event-weighted fraction (unit: games, illustrative)",
          ["N games (ASSUMPTION)", "Coverage measure", "Measured fraction (Table 3b, all)",
           "N x fraction (illustrative, not a forecast)", "Label"], rows,
          note="The fractions are MEASURED on the pilot's sampled game days; N is assumed, so every product is an "
               "ASSUMPTION. The sampled days are not representative of a season, so the fractions need not carry over.")

    # 8
    L += ["", "## 8. Scope note", "",
          "- **MEASURED** (saved data): ledger-confirmed credit costs for the shapes actually used; the calendar of the "
          "events seen on the sampled dates; time to start per snapshot; event-weighted coverage fractions on the "
          "sampled game days.",
          "- **PROVIDER-DOCUMENTED**: the three credit formulas. The event-odds formula and several "
          "market/region combinations in the grid have not been observed in the ledger.",
          "- **ASSUMPTION**: G, s, the plan sizes' use, scan frequency, N, and every product built from them.",
          "- **Needs a results or schedule source**: N (the number of games), the true number of game days, "
          "start-time distribution over a full season, and whether the events endpoint lists games without odds.",
          "- Nothing here shows that any season is covered, that any sample size is sufficient, or what a game result "
          "was. The 300/100/100 numbers remain planning targets."]
    return "\n".join(L) + "\n", {"tables": tables, "calendar": cal, "coverage": cov, "evidence": ev}


def write_outputs(root: Path, text: str) -> None:
    N.atomic_write(Path(root) / "pilot" / "normalized" / "cost_report.md", text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="costs", description="Offline calendar and cost estimator (no network)")
    ap.add_argument("--root", default=".", help="project root (default: current directory)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        text, _ = build(root)
    except (CostsError, A.AnalysisError) as exc:
        print(f"ERROR: {exc}")
        return 1
    write_outputs(root, text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
