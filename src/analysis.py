"""Descriptive analysis of the Stage B, C and D snapshots. OFFLINE: no network, no credits.

Reads the raw files named in the ledger (read-only, "rb") for B1, B2, C1..C8 and D1, D2. B3 is
excluded (same snapshot as B2, h2h only, us+us2). Imports no HTTP library and not OddsClient;
the only project imports are normalize (classification) and ledger (credit summary), both stdlib-only.

Writes to pilot/normalized/:
  analysis_report.md     also printed
  event_snapshots.csv    one row per (snapshot, event)
  margins.csv            one row per complete_paired pregame (snapshot, event, book, market)
  spread_pairs.csv       one row per ordered cross-book spread orientation (one snapshot each)
  totals_pairs.csv       one row per ordered cross-book totals orientation (one snapshot each)
  movement_pairs.csv     one row per consecutive pregame observation pair of a series

No results data is joined. Nothing here infers causes. Settlement analysis is THEORETICAL.

Run: .\\.venv\\Scripts\\python.exe -m src.analysis
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
from datetime import timezone
from itertools import permutations
from pathlib import Path

from . import normalize as N
from .ledger import Ledger, LedgerCorrupt

TARGET_BOOKS = N.TARGET_BOOKS
MARKETS = N.MARKETS
COMPLETE = N.COMPLETE

# step -> season; priority order (also the order used to resolve duplicate returned timestamps)
STEP_SEASON = {"B1": 2025, "B2": 2026, "C1": 2025, "C2": 2025, "C3": 2025, "C4": 2025,
               "C5": 2026, "C6": 2026, "C7": 2026, "C8": 2026, "D1": 2025, "D2": 2026}
PRIORITY = list(STEP_SEASON)
CHAINS = {"2025 chain": ["B1", "C1", "C2", "C3", "C4"], "2026 chain": ["B2", "C5", "C6", "C7", "C8"]}
EXCLUDED_STEPS = {"B3": "same snapshot as B2, h2h only, regions us,us2"}

BUCKETS = [("(0,0.5h]", 1800), ("(0.5,2h]", 7200), ("(2,6h]", 21600), (">6h", None)]
BUCKET_NAMES = [b for b, _ in BUCKETS]
GROUPS2 = ["(0,2h]", ">2h"]
SPREAD_BUCKETS = ["<=3", "(3,6]", ">6", "no complete spread"]
LU_SKEW_S = 300
STEP_X = 0.001
EPS = 1e-12


class AnalysisError(Exception):
    pass


# ------------------------------------------------------------------ formulas
def parse_ts(v):
    return N.parse_ts(v)


def fmt_ts(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def pi(p):
    """Implied probability: 100/(p+100) if p>0 else |p|/(|p|+100). None if not a usable price."""
    return N.american_to_prob(p)


def decimal_odds(p):
    if not N._num(p) or p == 0:
        return None
    return 1 + p / 100.0 if p > 0 else 1 + 100.0 / abs(p)


def margin(price_a, price_b):
    """(pi_A + pi_B, overround O = sum - 1, hold = 1 - 1/sum)."""
    qa, qb = pi(price_a), pi(price_b)
    if qa is None or qb is None:
        return None
    s = qa + qb
    return s, s - 1, 1 - 1 / s


def bucket(tts_s):
    """tts bucket for a strictly positive time-to-start in seconds; None if not pregame."""
    if tts_s is None or tts_s <= 0:
        return None
    for name, upper in BUCKETS:
        if upper is None or tts_s <= upper:
            return name
    return None


def group2(tts_s):
    if tts_s is None or tts_s <= 0:
        return None
    return GROUPS2[0] if tts_s <= 7200 else GROUPS2[1]


def is_int(x) -> bool:
    return float(x).is_integer()


def pct(vals, q):
    s = sorted(vals)
    if not s:
        return None
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def summarize(vals) -> dict:
    vals = list(vals)
    if not vals:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p90": None}
    return {"n": len(vals), "mean": sum(vals) / len(vals), "median": statistics.median(vals),
            "p10": pct(vals, 0.10), "p90": pct(vals, 0.90)}


def f4(v) -> str:
    return "-" if v is None else f"{v:.4f}"


def f2(v) -> str:
    return "-" if v is None else f"{v:.2f}"


def share(k, n) -> str:
    return "-" if not n else f"{k}/{n} ({100.0 * k / n:.0f}%)"


# --------------------------------------------------------- gap and lock test
def leg_a(m, a):
    """Leg A covers when m > a (A at point -a, or Over a with m = total)."""
    return "win" if m > a else ("push" if m == a else "loss")


def leg_b(m, b):
    """Leg B covers when m < b (B at point +b, or Under b with m = total)."""
    return "win" if m < b else ("push" if m == b else "loss")


CATEGORY_NAMES = {("win", "loss"): "A-only covers", ("loss", "win"): "B-only covers", ("win", "win"): "both win",
                  ("loss", "loss"): "both lose", ("push", "win"): "A pushes, B wins", ("push", "loss"): "A pushes, B loses",
                  ("win", "push"): "B pushes, A wins", ("loss", "push"): "B pushes, A loses", ("push", "push"): "both push"}


def gap_analysis(a, b) -> dict:
    """Integer-score structure of leg A (covers if m > a) and leg B (covers if m < b)."""
    delta = b - a
    lo, hi = math.floor(min(a, b)) - 2, math.ceil(max(a, b)) + 2
    ints = range(lo, hi + 1)
    both_win = [m for m in ints if a < m < b]
    both_lose = [m for m in ints if b < m < a]
    pushes = []
    for m in ints:
        ra, rb = leg_a(m, a), leg_b(m, b)
        if ra == "push" or rb == "push":
            pushes.append((m, CATEGORY_NAMES[(ra, rb)]))
    cats = sorted({(leg_a(m, a), leg_b(m, b)) for m in ints})
    return {"delta": delta, "cat": ">0" if delta > EPS else ("<0" if delta < -EPS else "=0"),
            "both_win": both_win, "both_lose": both_lose, "pushes": pushes, "categories": cats}


def net_return(cat, x, dA, dB) -> float:
    ra, rb = cat
    ret_a = x * (dA - 1) if ra == "win" else (0.0 if ra == "push" else -x)
    ret_b = (1 - x) * (dB - 1) if rb == "win" else (0.0 if rb == "push" else -(1 - x))
    return ret_a + ret_b


def guaranteed_outcome(categories, price_a, price_b) -> dict:
    """THEORETICAL: best worst-case net return over stake share x on leg A (step 0.001)."""
    dA, dB = decimal_odds(price_a), decimal_odds(price_b)
    if dA is None or dB is None or not categories:
        return {"best_x": None, "best_worst": None, "locked": False}
    best_x, best = None, -math.inf
    for i in range(int(round(1 / STEP_X)) + 1):
        x = i * STEP_X
        worst = min(net_return(c, x, dA, dB) for c in categories)
        if worst > best + EPS:
            best, best_x = worst, x
    return {"best_x": best_x, "best_worst": best, "locked": best > EPS}


# ------------------------------------------------------------------ loading
def read_ledger_rows(root: Path) -> list[dict]:
    path = Path(root) / "pilot" / "ledger" / "credit_ledger.csv"
    if not path.exists():
        raise AnalysisError("pilot/ledger/credit_ledger.csv not found")
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_inputs(root: Path) -> dict:
    """Snapshots named in the ledger (status 200, odds endpoint), read-only. Duplicate returned timestamps
    are resolved by step priority, not execution order."""
    rows = read_ledger_rows(root)
    chosen, excluded = {}, Counter()
    for r in rows:
        if not r["endpoint"].endswith("/odds") or r["status_code"] != "200":
            continue
        m = re.match(r"^stage ([BCD]) step ([BCD]\d+)\b", r["purpose"])
        if not m or m.group(1) != r["stage"]:
            continue
        step = m.group(2)
        if step in EXCLUDED_STEPS:
            excluded[step] += 1
            continue
        if step in STEP_SEASON:
            chosen[step] = r
    snaps, seen_ts = [], {}
    for step in PRIORITY:
        r = chosen.get(step)
        if r is None:
            continue
        s = {"step": step, "season": STEP_SEASON[step], "raw_name": Path(r["raw_file"]).name, "meta": {},
             "events": [], "ts": None, "error": "", "dup_of": ""}
        raw = Path(r["raw_file"])
        if not raw.exists():
            raw = Path(root) / "pilot" / "raw" / raw.name
        try:
            with open(raw, "rb") as fh:                                  # read-only
                obj = json.loads(fh.read().decode("utf-8"))
            data = obj["data"]
            if not isinstance(data, list) or not all(isinstance(e, dict) and e.get("id") for e in data):
                raise ValueError("unexpected event structure")
            s["meta"] = {k: "" if obj.get(k) is None else str(obj[k])
                         for k in ("timestamp", "previous_timestamp", "next_timestamp")}
            s["events"] = data
            s["ts"] = parse_ts(s["meta"]["timestamp"])
            if s["ts"] is None:
                raise ValueError("no parseable returned timestamp")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            s["error"] = f"{type(exc).__name__}: {exc}"
        if not s["error"]:
            if s["ts"] in seen_ts:
                s["dup_of"] = seen_ts[s["ts"]]
            else:
                seen_ts[s["ts"]] = step
        snaps.append(s)
    missing = [s for s in ["B1", "B2", "C2", "C4", "C6", "C8", "D1", "D2"] if s not in chosen]
    return {"snapshots": snaps, "excluded": dict(excluded), "missing": missing}


def usable(snaps) -> list[dict]:
    return [s for s in snaps if not s["error"] and not s["dup_of"]]


# ------------------------------------------------------------- observations
def _pair(market, first_name, second_name):
    by = {o.get("name"): o for o in market.get("outcomes") or []}
    return by.get(first_name), by.get(second_name)


def observations(snaps) -> list[dict]:
    """One row per (snapshot, event, target book, market present)."""
    out = []
    for s in usable(snaps):
        for e in s["events"]:
            ct = parse_ts(e.get("commence_time"))
            tts_s = int((ct - s["ts"]).total_seconds()) if ct else None
            idx = N.book_index(e)
            for b in TARGET_BOOKS:
                for k in MARKETS:
                    m = N.first_market(idx.get(b), k)
                    if m is None:
                        continue
                    o = {"step": s["step"], "season": s["season"], "snap_ts": s["ts"], "event": e, "event_id": e["id"],
                         "book": b, "market": k, "m": m, "cls": N.classify_market(e, m), "tts_s": tts_s,
                         "pregame": tts_s is not None and tts_s > 0, "last_update": m.get("last_update", "")}
                    if o["cls"] == COMPLETE:
                        home, away = e.get("home_team"), e.get("away_team")
                        if k == "totals":
                            oa, ob = _pair(m, "Over", "Under")
                        else:
                            oa, ob = _pair(m, home, away)
                        o.update(price_a=oa.get("price"), price_b=ob.get("price"), point_a=oa.get("point"),
                                 point_b=ob.get("point"))
                        o["pi_sum"], o["overround"], o["hold"] = margin(o["price_a"], o["price_b"])
                    out.append(o)
    return out


def valid(obs, market=None) -> list[dict]:
    """complete_paired and pregame (tts > 0)."""
    return [o for o in obs if o["cls"] == COMPLETE and o["pregame"] and (market is None or o["market"] == market)]


def latest_per(obs, keyfn) -> list[dict]:
    """Event-weighted selection: per key, the observation with the largest returned timestamp."""
    best = {}
    for o in obs:
        k = keyfn(o)
        if k not in best or o["snap_ts"] > best[k]["snap_ts"]:
            best[k] = o
    return list(best.values())


def ebm(o):
    return (o["event_id"], o["book"], o["market"])


def event_weighted(obs) -> list[dict]:
    """Exactly one observation per (event, book, market): the latest valid pregame one. Because only
    valid pregame observations are candidates, a post-start latest snapshot falls back automatically."""
    return latest_per(valid(obs), ebm)


def abs_spread_lookup(obs) -> dict:
    """(step, event_id, book) -> |point| of that book's complete spreads in that snapshot."""
    return {(o["step"], o["event_id"], o["book"]): abs(o["point_a"]) for o in obs
            if o["market"] == "spreads" and o["cls"] == COMPLETE}


def spread_bucket(v):
    if v is None:
        return "no complete spread"
    return "<=3" if v <= 3 else ("(3,6]" if v <= 6 else ">6")


# ---------------------------------------------------------- cross-book pairs
def cross_pairs(obs, market) -> list[dict]:
    """Ordered (book1, book2) orientations within ONE snapshot and one event.

    spreads: leg A = home team at book1 (a = -home point), leg B = away team at book2 (b = away point).
    totals:  leg A = Over x at book1 (a = x), leg B = Under y at book2 (b = y).
    """
    by = defaultdict(dict)
    for o in valid(obs, market):
        by[(o["step"], o["event_id"])][o["book"]] = o
    out = []
    for (step, eid), books in by.items():
        if len(books) < 2:
            continue
        for b1, b2 in permutations([b for b in TARGET_BOOKS if b in books], 2):
            o1, o2 = books[b1], books[b2]
            if market == "spreads":
                a, b = -o1["point_a"], o2["point_b"]
            else:
                a, b = o1["point_a"], o2["point_b"]
            price_a, price_b = o1["price_a"], o2["price_b"]
            g = gap_analysis(a, b)
            lock = guaranteed_outcome(g["categories"], price_a, price_b)
            lu1, lu2 = parse_ts(o1["last_update"]), parse_ts(o2["last_update"])
            qa, qb = pi(price_a), pi(price_b)
            out.append({"step": step, "season": o1["season"], "snap_ts": o1["snap_ts"], "event_id": eid,
                        "home_team": o1["event"].get("home_team", ""), "away_team": o1["event"].get("away_team", ""),
                        "tts_s": o1["tts_s"], "group": group2(o1["tts_s"]), "book1": b1, "book2": b2,
                        "a": a, "b": b, "price_a": price_a, "price_b": price_b, **g,
                        "pi_sum": (qa + qb) if qa is not None and qb is not None else None,
                        **lock, "lu1": o1["last_update"], "lu2": o2["last_update"],
                        "lu_diff_s": int(abs((lu1 - lu2).total_seconds())) if lu1 and lu2 else None})
    return out


def event_level_points(obs, market) -> list[dict]:
    """Per (snapshot, event) with >= 2 target books complete: max cross-book |point difference|."""
    by = defaultdict(dict)
    for o in valid(obs, market):
        by[(o["step"], o["event_id"])][o["book"]] = o
    out = []
    for (step, eid), books in by.items():
        if len(books) < 2:
            continue
        pts = [o["point_a"] for o in books.values()]
        out.append({"step": step, "event_id": eid, "snap_ts": next(iter(books.values()))["snap_ts"],
                    "n_books": len(books), "max_diff": max(pts) - min(pts)})
    return out


def ew_pairs(pairs, ev_points) -> list[dict]:
    """Event-weighted cross-book pairs: per event, all orientations of the latest pregame snapshot in which
    >= 2 target books quote the market complete."""
    latest = {}
    for p in ev_points:
        if p["event_id"] not in latest or p["snap_ts"] > latest[p["event_id"]][1]:
            latest[p["event_id"]] = (p["step"], p["snap_ts"])
    return [p for p in pairs if latest.get(p["event_id"], (None,))[0] == p["step"]]


# ------------------------------------------------------------------ movement
def _outcomes(m) -> dict:
    return {o["name"]: (o.get("price"), o.get("point")) for o in m.get("outcomes") or []
            if isinstance(o, dict) and isinstance(o.get("name"), str)}


def movement(obs, steps) -> dict:
    """Series (event, book, market) of valid pregame observations in the given steps' snapshots."""
    series = defaultdict(list)
    for o in valid(obs):
        if o["step"] in steps:
            series[ebm(o)].append(o)
    pairs, ser = [], []
    for key, lst in series.items():
        lst.sort(key=lambda o: o["snap_ts"])
        dedup = []
        for o in lst:                                   # collapse duplicate returned timestamps
            if not dedup or o["snap_ts"] != dedup[-1]["snap_ts"]:
                dedup.append(o)
        price_chg = point_chg = False
        max_dpi = None
        for x, y in zip(dedup, dedup[1:]):
            ox, oy = _outcomes(x["m"]), _outcomes(y["m"])
            pc = qc = False
            dpi = None
            for name in set(ox) & set(oy):
                (px, tx), (py, ty) = ox[name], oy[name]
                if px != py:
                    pc = True
                    qa, qb = pi(px), pi(py)
                    if qa is not None and qb is not None:
                        dpi = max(dpi or 0.0, abs(qb - qa))
                if tx != ty:
                    qc = True
            price_chg |= pc
            point_chg |= qc
            if dpi is not None:
                max_dpi = max(max_dpi or 0.0, dpi)
            pairs.append({"event_id": key[0], "book": key[1], "market": key[2], "step_from": x["step"],
                          "step_to": y["step"], "ts_from": fmt_ts(x["snap_ts"]), "ts_to": fmt_ts(y["snap_ts"]),
                          "tts_from_s": x["tts_s"], "tts_to_s": y["tts_s"], "bucket_from": bucket(x["tts_s"]),
                          "bucket_to": bucket(y["tts_s"]), "price_changed": pc, "point_changed": qc,
                          "changed": pc or qc, "max_dpi": dpi,
                          "outcomes_from": "; ".join(f"{n} {p} {t if t is not None else ''}".strip() for n, (p, t) in sorted(ox.items())),
                          "outcomes_to": "; ".join(f"{n} {p} {t if t is not None else ''}".strip() for n, (p, t) in sorted(oy.items()))})
        ser.append({"event_id": key[0], "book": key[1], "market": key[2], "n_obs": len(dedup),
                    "price_changed": price_chg, "point_changed": point_chg, "max_dpi": max_dpi if price_chg else None})
    multi = [s for s in ser if s["n_obs"] >= 2]
    ev = defaultdict(lambda: {"price": False, "point": False})
    for s in multi:
        ev[s["event_id"]]["price"] |= s["price_changed"]
        ev[s["event_id"]]["point"] |= s["point_changed"]
    return {"series": multi, "pairs": pairs, "events": dict(ev)}


# -------------------------------------------------------------------- report
def md(headers, rows) -> str:
    return N.md(headers, rows)


def margin_rows(pooled, ew, keyfn, keys):
    rows = []
    for key in keys:
        for view, src in (("pooled", pooled), ("event-weighted", ew)):
            vals = [o for o in src if keyfn(o) == key]
            so, sh = summarize(o["overround"] for o in vals), summarize(o["hold"] for o in vals)
            if so["n"] == 0 and view == "pooled":
                break
            rows.append(list(key) + [view, so["n"], f4(so["mean"]), f4(so["median"]), f4(so["p10"]), f4(so["p90"]),
                                     f4(sh["mean"]), f4(sh["median"]), f4(sh["p10"]), f4(sh["p90"])])
    return rows


MARGIN_HEAD = ["View", "n", "O mean", "O median", "O p10", "O p90", "hold mean", "hold median", "hold p10", "hold p90"]
EW_RULE = ("Event-weighted rule: exactly one observation per (event, book, market), from the latest returned snapshot "
           "with tts > 0 in which it is complete_paired (a post-start latest snapshot falls back to the latest pregame one).")


def pair_summary_rows(pairs, label_fn, labels):
    rows = []
    for lab in labels:
        ps = [p for p in pairs if label_fn(p) == lab]
        if not ps:
            continue
        lu = [p["lu_diff_s"] for p in ps if p["lu_diff_s"] is not None]
        bw = [p for p in ps if p["both_win"]]
        bl = [p for p in ps if p["both_lose"]]
        rows.append(list(lab) + [len(ps), sum(p["cat"] == ">0" for p in ps), sum(p["cat"] == "=0" for p in ps),
                                 sum(p["cat"] == "<0" for p in ps),
                                 f"{len(bw)} ({sum(len(p['both_win']) for p in ps)})",
                                 f"{len(bl)} ({sum(len(p['both_lose']) for p in ps)})",
                                 sum(1 for p in ps if p["pushes"]),
                                 sum(1 for p in ps if p["pi_sum"] is not None and p["pi_sum"] < 1),
                                 sum(1 for p in ps if p["locked"]),
                                 f"{statistics.median(lu):.0f}" if lu else "-", sum(1 for v in lu if v > LU_SKEW_S)])
    return rows


PAIR_HEAD = ["n orientations", "delta >0", "delta =0", "delta <0", "with both-win integers (total integers)",
             "with both-lose integers (total integers)", "with push cases", "pi_A+pi_B<1", "locked (THEORETICAL)",
             "median |lu diff| s", f"|lu diff| > {LU_SKEW_S} s"]


def dist_text(values) -> str:
    c = Counter(values)
    return ", ".join(f"{k:g}: {v}" for k, v in sorted(c.items())) or "-"


def cross_section(L, title, market, obs, pairs, unit_note):
    evp = event_level_points(obs, market)
    ewp = ew_pairs(pairs, evp)
    latest_ev = latest_per(evp, lambda p: p["event_id"])
    L += ["", f"## {title}", "", unit_note, ""]
    L.append(f"### Event level, per snapshot (unit: events in snapshots; pregame; >= 2 target books complete {market})")
    L.append("")
    steps = sorted({p["step"] for p in evp}, key=PRIORITY.index)
    rows = []
    for st in steps:
        ps = [p for p in evp if p["step"] == st]
        rows.append([st, len(ps), sum(1 for p in ps if p["max_diff"] > EPS), dist_text(p["max_diff"] for p in ps)])
    rows.append(["pooled (event-snapshots)", len(evp), sum(1 for p in evp if p["max_diff"] > EPS),
                 dist_text(p["max_diff"] for p in evp)])
    rows.append(["event-weighted (events)", len(latest_ev), sum(1 for p in latest_ev if p["max_diff"] > EPS),
                 dist_text(p["max_diff"] for p in latest_ev)])
    L.append(md(["Scope", "Events with >= 2 target books", "With >= 1 cross-book point mismatch",
                 "Max cross-book |point difference| (value: count)"], rows) if evp else "No qualifying events.")
    L += ["", "Event-weighted row: per event, the latest pregame snapshot in which >= 2 target books quote it complete."]

    L += ["", f"### Orientations per snapshot, by tts group (unit: ordered book-pair orientations; all orientations summed)", ""]
    labels = [(st, g) for st in steps for g in GROUPS2]
    rows = pair_summary_rows(pairs, lambda p: (p["step"], p["group"]), labels)
    L.append(md(["Snapshot", "tts group"] + PAIR_HEAD, rows) if rows else "No orientations.")
    orients = list(permutations(TARGET_BOOKS, 2))
    L += ["", "### Orientations pooled across snapshots, by tts group and ordered (book1, book2) "
              "(unit: ordered book-pair orientations; each within one snapshot)", ""]
    rows = pair_summary_rows(pairs, lambda p: (p["group"], f"{p['book1']} -> {p['book2']}"),
                             [(g, f"{x} -> {y}") for g in GROUPS2 for x, y in orients])
    L.append(md(["tts group", "book1 -> book2"] + PAIR_HEAD, rows) if rows else "No orientations.")
    L += ["", f"### Orientations, event-weighted (unit: ordered book-pair orientations; one snapshot per event: "
              f"the latest pregame snapshot with >= 2 target books complete; events n = {len(latest_ev)})", ""]
    rows = pair_summary_rows(ewp, lambda p: (f"{p['book1']} -> {p['book2']}",), [(f"{x} -> {y}",) for x, y in orients])
    L.append(md(["book1 -> book2"] + PAIR_HEAD, rows) if rows else "No orientations.")
    locked = [p for p in pairs if p["locked"]]
    L += ["", f"Orientations flagged locked (THEORETICAL, unit: orientations): {len(locked)}"]
    if locked:
        L += ["", md(["Snapshot", "Event", "book1 -> book2", "a", "b", "price A", "price B", "pi_A+pi_B",
                      "best x", "best worst-case net", "|lu diff| s"],
                     [[p["step"], p["event_id"], f"{p['book1']} -> {p['book2']}", f"{p['a']:g}", f"{p['b']:g}",
                       p["price_a"], p["price_b"], f4(p["pi_sum"]), f"{p['best_x']:.3f}", f4(p["best_worst"]),
                       p["lu_diff_s"] if p["lu_diff_s"] is not None else "-"] for p in locked])]
    return ewp


def build(root: Path) -> tuple[str, dict]:
    root = Path(root)
    inp = load_inputs(root)
    snaps = inp["snapshots"]
    if not usable(snaps):
        raise AnalysisError("no readable Stage B/C/D snapshots found in the ledger")
    obs = observations(snaps)
    pre = valid(obs)
    ew = event_weighted(obs)
    ew_ids = {id(o) for o in ew}
    spread_lu = abs_spread_lookup(obs)

    L = ["# Descriptive analysis: Stage B, C and D snapshots (offline)", "",
         "## Definitions", "",
         "- Implied probability from American price p: pi(p) = 100/(p+100) if p > 0, else |p|/(|p|+100).",
         "- Overround of a two-outcome market: O = pi_A + pi_B - 1. Hold: hold = 1 - 1/(pi_A + pi_B). No de-vig is used.",
         "- Time-to-start (tts) per event per snapshot, from each event's OWN commence_time: "
         "tts = commence_time - returned snapshot timestamp (hours).",
         "- PREGAME RULE: an observation is pregame only if tts > 0. Observations with tts <= 0 are excluded from "
         "margins, cross-book pairs and movement, and counted in the inventory. Buckets: (0,0.5h], (0.5,2h], (2,6h], >6h "
         "((0,0.5h] means 0 < tts <= 0.5h).",
         "- Pooled view: every valid pregame event-book-market observation counts once per snapshot it appears in.",
         f"- {EW_RULE}",
         "- Spread gap: leg A = team A (home) at book 1 with point -a (covers if m > a, m = A score - B score); leg B = "
         "team B (away) at book 2 with point +b (covers if m < b). delta = b - a. Totals: leg A = Over x at book 1, leg B = "
         "Under y at book 2, m = total score, delta = y - x.",
         "- Integer-score gap: integers m with a < m < b (both win) and with b < m < a (both lose). Push: integer m equal to "
         "an integer point on a leg. All integer margins are enumerated, including 0, because overtime and push rules are "
         "unverified.",
         "- Guaranteed-outcome test (THEORETICAL only): stake share x on leg A, 1 - x on leg B, x in steps of 0.001; net "
         "return per reachable outcome category from decimal odds (a push returns the stake); 'locked' only if the best "
         "worst-case net return is strictly > 0. This ignores stake limits, line movement, rule differences, fees and "
         "quote-time skew, and is not a tradable claim.",
         "- Only complete_paired markets are used (normalize.classify_market; spreads: points sum to zero). Target books: "
         + ", ".join(TARGET_BOOKS) + ".",
         "", "## Counting units and independence", "",
         "- Units used: events in snapshots; event-book-market observations; series (event, book, market); ordered "
         "book-pair orientations; independent clusters (game days). 'Events' means distinct event_ids counted once.",
         "- Series are NOT independent: books pricing one game, markets within one book, and games on one day are "
         "correlated. For movement the pilot has only **2 independent chains (one game day each)**.",
         "- No results data is joined. Nothing here infers causes. Every cross-book comparison uses ONE snapshot.",
         f"- Excluded: B3 ({EXCLUDED_STEPS['B3']})."
         + (f" Missing from the ledger: {', '.join(inp['missing'])}." if inp["missing"] else "")]

    # 1. inventory
    L += ["", "## 1. Inventory (unit: events in snapshots)", ""]
    inv, es_rows = [], []
    for s in snaps:
        if s["error"]:
            inv.append([s["step"], s["season"], "UNREADABLE: " + s["error"]] + ["-"] * 9)
            continue
        if s["dup_of"]:
            inv.append([s["step"], s["season"], s["meta"]["timestamp"], f"same snapshot as {s['dup_of']}; excluded"]
                       + ["-"] * 8)
            continue
        sob = [o for o in obs if o["step"] == s["step"]]
        tts = {}
        for e in s["events"]:
            ct = parse_ts(e.get("commence_time"))
            tts[e["id"]] = int((ct - s["ts"]).total_seconds()) if ct else None
        pre_ids = [i for i, t in tts.items() if t is not None and t > 0]
        post = len(s["events"]) - len(pre_ids)
        pre_t = [tts[i] / 3600 for i in pre_ids]
        hours = {parse_ts(e.get("commence_time")).strftime("%H") for e in s["events"] if e["id"] in pre_ids}
        cells = []
        for b in TARGET_BOOKS:
            bo = [o for o in sob if o["book"] == b and o["pregame"]]
            cells.append("/".join(str(len({o["event_id"] for o in bo if (k == "any" or (o["market"] == k and o["cls"] == COMPLETE))}))
                                  for k in ("any",) + MARKETS))
        inv.append([s["step"], s["season"], s["meta"]["timestamp"], len(s["events"])] + cells
                   + [f2(min(pre_t)) if pre_t else "-", f2(statistics.median(pre_t)) if pre_t else "-",
                      f2(max(pre_t)) if pre_t else "-", len(hours), post])
        for e in s["events"]:
            idx = N.book_index(e)
            row = {"step": s["step"], "season": s["season"], "snapshot_timestamp": s["meta"]["timestamp"],
                   "event_id": e["id"], "home_team": e.get("home_team", ""), "away_team": e.get("away_team", ""),
                   "commence_time": e.get("commence_time", ""),
                   "tts_hours": "" if tts[e["id"]] is None else f"{tts[e['id']] / 3600:.4f}",
                   "pregame": "yes" if (tts[e["id"]] or 0) > 0 else "no", "tts_bucket": bucket(tts[e["id"]]) or "post-start"}
            for b in TARGET_BOOKS:
                for k in MARKETS:
                    row[f"{b}_{k}"] = N.classify_market(e, N.first_market(idx.get(b), k))
            es_rows.append(row)
    L.append(md(["Step", "Season", "Returned timestamp", "Events"]
                + [f"{b} pregame events any/h2h/spreads/totals" for b in TARGET_BOOKS]
                + ["tts min h", "tts median h", "tts max h", "Distinct commence hours (UTC)", "Post-start events excluded"],
                inv))
    L += ["", "Post-start (tts <= 0) event-book-market observations excluded, per snapshot and market "
              "(unit: event-book-market observations, target books, any classification):", ""]
    post_rows = []
    for s in usable(snaps):
        sob = [o for o in obs if o["step"] == s["step"] and not o["pregame"]]
        post_rows.append([s["step"]] + [sum(1 for o in sob if o["market"] == k) for k in MARKETS])
    L.append(md(["Step"] + list(MARKETS), post_rows))
    others = Counter(b.get("key") for s in usable(snaps) for e in s["events"] for b in e.get("bookmakers") or []
                     if b.get("key") not in TARGET_BOOKS)
    L.append("\nOther bookmaker keys (inventory only; not analysed; unit: event entries): "
             + (", ".join(f"{k} {v}" for k, v in sorted(others.items())) or "none"))
    appear = Counter()
    for s in usable(snaps):
        for i in {e["id"] for e in s["events"]}:
            appear[i] += 1
    by_season = defaultdict(set)
    for s in usable(snaps):
        by_season[s["season"]].update(e["id"] for e in s["events"])
    dist = Counter(min(n, 4) for n in appear.values())
    L += ["", f"Distinct event_ids (events): overall {len(appear)}; "
              + "; ".join(f"{k} {len(v)}" for k, v in sorted(by_season.items()))
              + f". Events by number of snapshots they appear in (any tts): 1: {dist[1]}, 2: {dist[2]}, 3: {dist[3]}, 4+: {dist[4]}."]

    # 2. events without complete h2h
    L += ["", "## 2. Events with no complete h2h at any target book (unit: events in snapshots, pregame)", ""]
    per_se = defaultdict(list)
    for o in obs:
        if o["pregame"]:
            per_se[(o["step"], o["event_id"])].append(o)
    no_h2h = []
    for (st, eid), lst in sorted(per_se.items(), key=lambda kv: (PRIORITY.index(kv[0][0]), kv[0][1])):
        if not any(o["market"] == "h2h" and o["cls"] == COMPLETE for o in lst):
            e = lst[0]["event"]
            no_h2h.append([st, eid, e.get("home_team", ""), e.get("away_team", ""), e.get("commence_time", ""),
                           f2(lst[0]["tts_s"] / 3600), len({o["book"] for o in lst}),
                           len({o["book"] for o in lst if o["market"] == "spreads" and o["cls"] == COMPLETE}),
                           len({o["book"] for o in lst if o["market"] == "totals" and o["cls"] == COMPLETE})])
    L.append(md(["Snapshot", "Event", "Home", "Away", "Commence", "tts h", "Target books with any market",
                 "Books complete spreads", "Books complete totals"], no_h2h) if no_h2h else "None.")
    L += ["", "**Hypothesis check (not a finding about cause; unit: events in snapshots for the pooled row, events counted "
              "once for the event-weighted row):** median |spread| for events with vs without complete h2h at any target "
              "book. Per (snapshot, event): median of |point| across target books' complete spreads in that snapshot; "
              "events without any complete spread are not in the comparison.", ""]

    def hyp(keys):
        w, wo, nos = [], [], 0
        for k in keys:
            lst = per_se[k]
            sp = [abs(o["point_a"]) for o in lst if o["market"] == "spreads" and o["cls"] == COMPLETE]
            has = any(o["market"] == "h2h" and o["cls"] == COMPLETE for o in lst)
            if not sp:
                nos += 1
                continue
            (w if has else wo).append(statistics.median(sp))
        return w, wo, nos

    latest_se = {}
    for (st, eid), lst in per_se.items():
        if eid not in latest_se or lst[0]["snap_ts"] > per_se[latest_se[eid]][0]["snap_ts"]:
            latest_se[eid] = (st, eid)
    hrows = []
    for view, keys in (("pooled (event-snapshots)", list(per_se)), ("event-weighted (events; latest pregame snapshot)",
                                                                     list(latest_se.values()))):
        w, wo, nos = hyp(keys)
        hrows.append([view, len(w), f2(statistics.median(w)) if w else "-", len(wo), f2(statistics.median(wo)) if wo else "-",
                      nos])
    L.append(md(["View", "n with h2h", "median |spread| with h2h", "n without h2h", "median |spread| without h2h",
                 "n with no complete spread (excluded)"], hrows))

    # 3. margins
    margin_csv = []
    for o in pre:
        margin_csv.append({"step": o["step"], "season": o["season"], "snapshot_timestamp": fmt_ts(o["snap_ts"]),
                           "event_id": o["event_id"], "book": o["book"], "market": o["market"],
                           "tts_hours": f"{o['tts_s'] / 3600:.4f}", "tts_bucket": bucket(o["tts_s"]),
                           "price_a": o["price_a"], "price_b": o["price_b"],
                           "point_a": "" if o["point_a"] is None else o["point_a"],
                           "point_b": "" if o["point_b"] is None else o["point_b"],
                           "pi_sum": f"{o['pi_sum']:.6f}", "overround": f"{o['overround']:.6f}", "hold": f"{o['hold']:.6f}",
                           "abs_spread_same_book": "" if o["market"] != "h2h" or (o["step"], o["event_id"], o["book"]) not in spread_lu
                           else spread_lu[(o["step"], o["event_id"], o["book"])],
                           "market_last_update": o["last_update"], "event_weighted_selected": "yes" if id(o) in ew_ids else "no"})
    L += ["", "## 3. Margins (unit: event-book-market observations; complete_paired, pregame only)", "",
          f"Pooled n = {len(pre)} observations; event-weighted n = {len(ew)} (event, book, market). {EW_RULE}", ""]
    keys = [(b, k, bk) for b in TARGET_BOOKS for k in MARKETS for bk in BUCKET_NAMES + ["all"]]
    L.append(md(["Book", "Market", "tts bucket"] + MARGIN_HEAD,
                margin_rows(pre, ew, lambda o: (o["book"], o["market"], bucket(o["tts_s"])), [k for k in keys if k[2] != "all"])
                + margin_rows(pre, ew, lambda o: (o["book"], o["market"], "all"), [k for k in keys if k[2] == "all"])))
    L += ["", f"### h2h margins by |spread| bucket (unit: event-book-market observations; |spread| from the same book, "
              f"event and snapshot). {EW_RULE}", ""]
    sb = lambda o: (o["book"], spread_bucket(spread_lu.get((o["step"], o["event_id"], o["book"]))))  # noqa: E731
    L.append(md(["Book", "|spread| bucket"] + MARGIN_HEAD,
                margin_rows([o for o in pre if o["market"] == "h2h"], [o for o in ew if o["market"] == "h2h"], sb,
                            [(b, x) for b in TARGET_BOOKS for x in SPREAD_BUCKETS])))
    L += ["", f"### Margins for 0 < tts <= 0.5h only (unit: event-book-market observations). {EW_RULE}", ""]
    near = [o for o in pre if bucket(o["tts_s"]) == BUCKET_NAMES[0]]
    near_ew = [o for o in ew if bucket(o["tts_s"]) == BUCKET_NAMES[0]]
    rows = margin_rows(near, near_ew, lambda o: (o["book"], o["market"]), [(b, k) for b in TARGET_BOOKS for k in MARKETS])
    L.append(md(["Book", "Market"] + MARGIN_HEAD, rows) if rows else "No observations in (0,0.5h].")
    neg = [o for o in pre if o["overround"] < 0]
    L += ["", f"Within-book negative overround (O < 0; unit: event-book-market observations): pooled {len(neg)}, "
              f"event-weighted {sum(1 for o in ew if o['overround'] < 0)}."]
    if neg:
        L += ["", md(["Snapshot", "Event", "Book", "Market", "Price A", "Price B", "Points", "O"],
                     [[o["step"], o["event_id"], o["book"], o["market"], o["price_a"], o["price_b"],
                       f"{o['point_a']}/{o['point_b']}" if o["point_a"] is not None else "-", f4(o["overround"])]
                      for o in neg])]

    # 4. within-book spreads
    L += ["", f"## 4. Within-book spreads (unit: event-book-market observations, spreads, complete_paired, pregame). "
              f"{EW_RULE}", ""]
    rows = []
    for b in TARGET_BOOKS:
        for view, src in (("pooled", pre), ("event-weighted", ew)):
            sp = [o for o in src if o["book"] == b and o["market"] == "spreads"]
            n = len(sp)
            ints = sum(1 for o in sp if is_int(o["point_a"]))
            halves = sum(1 for o in sp if abs(abs(o["point_a"]) % 1 - 0.5) < 1e-9)
            rows.append([b, view, n, share(ints, n), share(halves, n), share(n - ints - halves, n),
                         share(sum(1 for o in sp if o["price_a"] == o["price_b"]), n),
                         dist_text(abs(o["point_a"]) for o in sp)])
    L.append(md(["Book", "View", "n", "Integer lines", "Half-point lines", "Other", "Equal prices both sides",
                 "|point| distribution (value: count)"], rows))

    # 5, 6. cross-book pairs
    sp_pairs = cross_pairs(obs, "spreads")
    to_pairs = cross_pairs(obs, "totals")
    sp_ew = cross_section(L, "5. Cross-book spread pairs (THEORETICAL)", "spreads", obs, sp_pairs,
                          "Each orientation compares two target books within ONE snapshot and one event. Columns: numerical "
                          "delta category; integer-score gap counts; push cases; pi_A + pi_B < 1; locked by the "
                          "guaranteed-outcome test; quote-time skew (|last_update difference|).")
    to_ew = cross_section(L, "6. Cross-book totals pairs (THEORETICAL)", "totals", obs, to_pairs,
                          "Leg A = Over x at book1, leg B = Under y at book2, delta = y - x; otherwise as section 5.")

    # 7. movement
    L += ["", "## 7. Movement (pregame observations only, by each event's own tts)", "",
          "Series = (event, book, market) of complete_paired pregame observations; each observation is a distinct "
          "returned snapshot (duplicate timestamps collapsed). A repeated identical price is NOT movement. Only "
          "**2 independent chains (one game day each)** exist: results are descriptive. D1/D2 are not part of a chain.",
          "", "Independent clusters (unit: game days) available for movement: 2 (2025 chain: B1 + C steps of "
              "quinnipiac_air_force; 2026 chain: B2 + C steps of njit_vermont). Every count below is nested inside these "
              "2 clusters."]
    mv_all = {}
    for name, steps in CHAINS.items():
        mv_all[name] = movement(obs, set(steps))
    mv_all["pooled (both chains)"] = movement(obs, set(CHAINS["2025 chain"]) | set(CHAINS["2026 chain"]))
    rows_a, rows_b = [], []
    for name, mv in mv_all.items():
        ev = mv["events"]
        rows_a.append([name, len(ev), sum(1 for v in ev.values() if v["price"]), sum(1 for v in ev.values() if v["point"])])
        ch = [s["max_dpi"] for s in mv["series"] if s["max_dpi"] is not None]
        rows_b.append([name, len(mv["series"]), sum(s["price_changed"] for s in mv["series"]),
                       sum(s["point_changed"] for s in mv["series"]), f4(statistics.median(ch)) if ch else "-"])
    L += ["", "### 7a. Event level, headline (unit: events; observable in >= 2 pregame snapshots at >= 1 target book)", "",
          md(["Chain", "Events observable", "With any price change", "With any point change"], rows_a),
          "", "### 7b. Series level, secondary (unit: series; NOT independent)", "",
          md(["Chain", "Series observed", "With price change", "With point change",
              "Median |delta pi| of price-changed series"], rows_b),
          "", "Median |delta pi|: per price-changed series, the largest change in implied probability of any outcome between "
              "consecutive observations."]
    L += ["", "### 7c. Consecutive observation pairs by the event's own tts at the start and end of the interval "
              "(unit: consecutive observation pairs of series; NOT independent)", ""]
    rows = []
    for name, mv in mv_all.items():
        cnt = Counter((p["bucket_from"], p["bucket_to"], p["changed"]) for p in mv["pairs"])
        for bf in BUCKET_NAMES:
            for bt in BUCKET_NAMES:
                ch, un = cnt[(bf, bt, True)], cnt[(bf, bt, False)]
                if ch or un:
                    rows.append([name, bf, bt, ch + un, ch, un])
    L.append(md(["Chain", "tts at start", "tts at end", "Pairs", "Changed", "Unchanged"], rows) if rows else "No pairs.")
    L += ["", "### 7d. By book and by market (unit: series and consecutive pairs; NOT independent)", ""]
    rows = []
    for name, mv in mv_all.items():
        for dim, values in (("book", TARGET_BOOKS), ("market", MARKETS)):
            for v in values:
                ss = [s for s in mv["series"] if s[dim] == v]
                ps = [p for p in mv["pairs"] if p[dim] == v]
                if ss or ps:
                    rows.append([name, f"{dim}={v}", len(ss), sum(s["price_changed"] for s in ss),
                                 sum(s["point_changed"] for s in ss), len(ps), sum(p["changed"] for p in ps)])
    L.append(md(["Chain", "Slice", "Series observed", "With price change", "With point change", "Pairs", "Pairs changed"],
                rows) if rows else "No series.")

    # 8. credits and scope
    L += ["", "## 8. Credits and scope", ""]
    try:
        d = root / "pilot" / "ledger"
        summ = Ledger(d / "credit_ledger.csv", d / "baseline.json").summary({})
        cap = 500
        try:
            m = re.search(r"^credit_cap:\s*(\d+)", (root / "pilot" / "config" / "pilot.yaml").read_text(encoding="utf-8"), re.M)
            cap = min(int(m.group(1)), 500) if m else 500
        except OSError:
            pass
        L += [f"- Committed credits (pessimistic, from the ledger): {summ['committed']}; by stage: "
              + ", ".join(f"{k} {v}" for k, v in sorted(summ["per_stage"].items()) if v),
              f"- Remaining against the {cap} cap: {cap - summ['committed']}. This analysis used 0 credits."]
    except LedgerCorrupt as exc:
        L.append(f"- Ledger unreadable: {exc}")
    L += ["- Scope: sampled snapshots only; events in snapshots, not games; no results data joined; nothing here assesses "
          "T1 to T3 or sufficiency; settlement analysis is THEORETICAL (push and overtime rules unverified); no causes "
          "are inferred."]

    def pair_csv(pairs, ew_set):
        ids = {id(p) for p in ew_set}
        return [{"step": p["step"], "season": p["season"], "snapshot_timestamp": fmt_ts(p["snap_ts"]), "event_id": p["event_id"],
                 "home_team": p["home_team"], "away_team": p["away_team"], "tts_hours": f"{p['tts_s'] / 3600:.4f}",
                 "tts_group": p["group"], "book1": p["book1"], "book2": p["book2"], "a": p["a"], "b": p["b"],
                 "delta": p["delta"], "delta_category": p["cat"], "both_win_integers": " ".join(map(str, p["both_win"])),
                 "both_lose_integers": " ".join(map(str, p["both_lose"])),
                 "push_cases": "; ".join(f"m={m}: {c}" for m, c in p["pushes"]), "price_a": p["price_a"],
                 "price_b": p["price_b"], "pi_sum": f4(p["pi_sum"]), "best_x": "" if p["best_x"] is None else f"{p['best_x']:.3f}",
                 "best_worst_net_theoretical": f4(p["best_worst"]), "locked_theoretical": "yes" if p["locked"] else "no",
                 "last_update_1": p["lu1"], "last_update_2": p["lu2"],
                 "lu_diff_s": "" if p["lu_diff_s"] is None else p["lu_diff_s"],
                 "event_weighted_selected": "yes" if id(p) in ids else "no"} for p in pairs]

    mv_csv = []
    for name in CHAINS:
        for p in mv_all[name]["pairs"]:
            mv_csv.append({"chain": name, **{k: p[k] for k in ("event_id", "book", "market", "step_from", "step_to",
                                                                 "ts_from", "ts_to")},
                           "tts_from_hours": f"{p['tts_from_s'] / 3600:.4f}", "tts_to_hours": f"{p['tts_to_s'] / 3600:.4f}",
                           "bucket_from": p["bucket_from"], "bucket_to": p["bucket_to"],
                           "price_changed": "yes" if p["price_changed"] else "no",
                           "point_changed": "yes" if p["point_changed"] else "no", "max_abs_delta_pi": f4(p["max_dpi"]),
                           "outcomes_from": p["outcomes_from"], "outcomes_to": p["outcomes_to"]})
    return "\n".join(L) + "\n", {"event_snapshots": es_rows, "margins": margin_csv, "spread_pairs": pair_csv(sp_pairs, sp_ew),
                                  "totals_pairs": pair_csv(to_pairs, to_ew), "movement_pairs": mv_csv,
                                  "obs": obs, "snapshots": snaps, "movement": mv_all}


def write_outputs(root: Path, text: str, out: dict) -> None:
    norm = Path(root) / "pilot" / "normalized"
    for name in ("event_snapshots", "margins", "spread_pairs", "totals_pairs", "movement_pairs"):
        rows = out[name]
        cols = list(rows[0].keys()) if rows else ["(no rows)"]
        N.atomic_write(norm / f"{name}.csv", N.csv_text(cols, rows))
    N.atomic_write(norm / "analysis_report.md", text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="analysis", description="Offline descriptive analysis of Stage B/C/D (no network)")
    ap.add_argument("--root", default=".", help="project root (default: current directory)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        text, out = build(root)
    except AnalysisError as exc:
        print(f"ERROR: {exc}")
        return 1
    write_outputs(root, text, out)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
