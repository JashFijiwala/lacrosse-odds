"""Offline normalization, completeness and Cradl reconciliation for Stage B.

NO network, NO credits. Reads the Stage B raw files named in the ledger (read-only),
pilot/normalized/stage_a_events.csv (team names only) and, if present,
pilot/cradl_observations.csv. Imports no HTTP library and not OddsClient.

Writes to pilot/normalized/:
  odds_long.csv                      one row per outcome
  team_names.csv                     sorted unique team names (no division or gender decided)
  stage_b_normalization_report.md    compact tables; also printed

Everything is a count of events in the sampled snapshots: not games, not matched to
results. No results data is joined.

Run: .\\.venv\\Scripts\\python.exe -m src.normalize
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
TARGET_BOOKS = ("draftkings", "fanduel", "betmgm")
COVERAGE_BOOKS = TARGET_BOOKS + ("fliff",)
MARKETS = ("h2h", "spreads", "totals")
NEAR_PI = 0.005

ABSENT = "absent"
COMPLETE = "complete_paired"
INCOMPLETE = "present_incomplete"
MISMATCHED = "present_mismatched"

LONG_COLUMNS = ["snapshot_requested_date", "snapshot_timestamp", "previous_timestamp", "next_timestamp", "step",
                "event_id", "commence_time", "home_team", "away_team", "bookmaker", "region_requested", "market",
                "market_last_update", "outcome_name", "price_american", "point"]


class NormalizeError(Exception):
    pass


# ----------------------------------------------------------------- basics
def parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def american_to_prob(price):
    """Implied probability from an American price. None for non-numeric or zero."""
    if not _num(price) or price == 0:
        return None
    return 100.0 / (price + 100.0) if price > 0 else abs(price) / (abs(price) + 100.0)


def classify_market(event: dict, bookmaker_market) -> str:
    """Completeness of one bookmaker market for an event.

    Same rules as stage_b.classify_market except spreads: two team-named outcomes with numeric
    price and point whose points sum to zero are complete_paired (so 0/0 is paired).
    """
    if bookmaker_market is None:
        return ABSENT
    key = bookmaker_market.get("key")
    outcomes = bookmaker_market.get("outcomes")
    if not isinstance(outcomes, list) or not all(isinstance(o, dict) for o in outcomes):
        return INCOMPLETE
    names = [o.get("name") for o in outcomes]
    if not all(isinstance(n, str) for n in names):
        return INCOMPLETE
    home, away = event.get("home_team"), event.get("away_team")
    teams_ok = (isinstance(home, str) and isinstance(away, str) and home != away and len(outcomes) == 2
                and sorted(names) == sorted([home, away]))
    if key == "h2h":
        ok = teams_ok and all(_num(o.get("price")) and abs(o["price"]) >= 100 for o in outcomes)
        return COMPLETE if ok else INCOMPLETE
    if key == "spreads":
        if not (teams_ok and all(_num(o.get("price")) and _num(o.get("point")) for o in outcomes)):
            return INCOMPLETE
        return COMPLETE if abs(outcomes[0]["point"] + outcomes[1]["point"]) < 1e-9 else MISMATCHED
    if key == "totals":
        ok = (len(outcomes) == 2 and sorted(names) == ["Over", "Under"]
              and all(_num(o.get("price")) and _num(o.get("point")) for o in outcomes)
              and outcomes[0]["point"] == outcomes[1]["point"])
        return COMPLETE if ok else INCOMPLETE
    return INCOMPLETE


def load_stage_b_classifier():
    """stage_b.classify_market(key, market, home, away), loaded from the source text via ast so that
    stage_b (and through it OddsClient and requests) is never imported."""
    path = Path(__file__).with_name("stage_b.py")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        wanted_names = {"ABSENT", "COMPLETE", "INCOMPLETE", "MISMATCHED"}
        body = [n for n in tree.body
                if (isinstance(n, ast.FunctionDef) and n.name in {"_num", "classify_market"})
                or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in wanted_names
                                                      for t in n.targets))]
        ns = {"math": math}
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), ns)  # noqa: S102
        return ns["classify_market"]
    except Exception as exc:  # noqa: BLE001
        raise NormalizeError(f"could not load stage_b.classify_market from source ({type(exc).__name__})") from None


def book_index(event: dict) -> dict:
    """bookmaker key -> list of market dicts, merged across any duplicate bookmaker entries."""
    idx = defaultdict(list)
    for b in event.get("bookmakers") or []:
        if isinstance(b, dict) and b.get("key"):
            idx[b["key"]].extend(m for m in (b.get("markets") or []) if isinstance(m, dict))
    return idx


def first_market(markets, key):
    return next((m for m in markets or [] if m.get("key") == key), None)


# ------------------------------------------------------------------ loading
def step_from_purpose(purpose: str):
    p = purpose.split()
    if len(p) >= 4 and p[:3] == ["stage", "B", "step"] and p[3] in ("B1", "B2", "B3"):
        return p[3]
    if len(p) >= 5 and p[:3] == ["stage", "B", "fallback"] and p[3] in ("B1", "B2", "B3"):
        return f"{p[3]}_fallback_{p[4]}"
    return None


def load_snapshots(root: Path) -> list[dict]:
    ledger_path = root / "pilot" / "ledger" / "credit_ledger.csv"
    if not ledger_path.exists():
        raise NormalizeError("pilot/ledger/credit_ledger.csv not found")
    with open(ledger_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    latest = {}
    counts = Counter()
    for r in rows:
        step = step_from_purpose(r["purpose"]) if r["stage"] == "B" and r["endpoint"].endswith("/odds") else None
        if step and r["status_code"] == "200":
            latest[step] = r
            counts[step] += 1
    snaps = []
    for step in sorted(latest, key=lambda s: (s[:2], len(s), s)):
        r = latest[step]
        try:
            params = json.loads(r["params_redacted_json"] or "{}")
        except ValueError:
            params = {}
        snap = {"step": step, "ledger": r, "requested": params.get("date", ""), "regions": params.get("regions", ""),
                "markets_requested": params.get("markets", ""), "raw_name": Path(r["raw_file"]).name,
                "n_ledger_200": counts[step], "meta": {}, "events": [], "error": ""}
        raw = Path(r["raw_file"])
        if not raw.exists():
            raw = root / "pilot" / "raw" / raw.name
        try:
            with open(raw, "rb") as fh:                      # read-only
                obj = json.loads(fh.read().decode("utf-8"))
            data = obj["data"]
            if not isinstance(data, list) or not all(isinstance(e, dict) and e.get("id") for e in data):
                raise ValueError("unexpected event structure")
            snap["meta"] = {k: "" if obj.get(k) is None else str(obj[k])
                            for k in ("timestamp", "previous_timestamp", "next_timestamp")}
            snap["events"] = data
        except (OSError, ValueError, KeyError, TypeError) as exc:
            snap["error"] = f"{type(exc).__name__}: {exc}"
        snaps.append(snap)
    return snaps


def good(snaps):
    return [s for s in snaps if not s["error"]]


# ------------------------------------------------------------------ long table
def fmt_point(p) -> str:
    return "" if p is None else str(p)


def build_long_rows(snap: dict) -> list[dict]:
    rows = []
    m = snap["meta"]
    for e in snap["events"]:
        for b in e.get("bookmakers") or []:
            for mk in b.get("markets") or []:
                for o in mk.get("outcomes") or []:
                    rows.append({
                        "snapshot_requested_date": snap["requested"], "snapshot_timestamp": m["timestamp"],
                        "previous_timestamp": m["previous_timestamp"], "next_timestamp": m["next_timestamp"],
                        "step": snap["step"], "event_id": e["id"], "commence_time": e.get("commence_time", ""),
                        "home_team": e.get("home_team", ""), "away_team": e.get("away_team", ""),
                        "bookmaker": b.get("key", ""), "region_requested": snap["regions"],
                        "market": mk.get("key", ""), "market_last_update": mk.get("last_update", ""),
                        "outcome_name": o.get("name", ""),
                        "price_american": "" if o.get("price") is None else o.get("price"),
                        "point": fmt_point(o.get("point"))})
    return rows


def csv_text(columns, rows) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in columns})
    return buf.getvalue()


def atomic_write(path: Path, text: str) -> None:
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


# ------------------------------------------------------------------ coverage
def coverage_table(events, books=COVERAGE_BOOKS) -> dict:
    out = {b: {"any": 0, "h2h": 0, "spreads": 0, "totals": 0} for b in books}
    for e in events:
        idx = book_index(e)
        for b in books:
            mkts = idx.get(b)
            if not mkts:
                continue
            out[b]["any"] += 1
            for k in MARKETS:
                if classify_market(e, first_market(mkts, k)) == COMPLETE:
                    out[b][k] += 1
    return out


def target_book_distribution(events) -> dict:
    dist = {0: 0, 1: 0, 2: 0, 3: 0}
    for e in events:
        idx = book_index(e)
        n = sum(1 for b in TARGET_BOOKS if classify_market(e, first_market(idx.get(b), "h2h")) == COMPLETE)
        dist[n] += 1
    return dist


def reconcile_b2_b3(b2: dict, b3: dict) -> dict:
    """(book, event) pairs with a market in B2 and no h2h market in B3, plus B3-only counts."""
    ev3 = {e["id"]: e for e in b3["events"]}
    missing, pairs2 = [], 0
    for e in b2["events"]:
        idx2 = book_index(e)
        e3 = ev3.get(e["id"])
        idx3 = book_index(e3) if e3 else {}
        for book in sorted(idx2):
            if not idx2[book]:
                continue
            pairs2 += 1
            if first_market(idx3.get(book), "h2h") is not None:
                continue
            if e3 is None:
                state = "event not in B3 payload"
            elif book not in idx3:
                state = "book has no entry for event in B3"
            else:
                state = "book entry in B3 has no h2h (markets: " + (",".join(m["key"] for m in idx3[book]) or "none") + ")"
            missing.append({"event_id": e["id"], "home_team": e.get("home_team", ""), "away_team": e.get("away_team", ""),
                            "commence_time": e.get("commence_time", ""), "bookmaker": book,
                            "b2_markets": ",".join(m["key"] for m in idx2[book]), "b3_state": state})
    ev2 = {e["id"]: e for e in b2["events"]}
    b3_only = Counter()
    pairs3 = 0
    for e in b3["events"]:
        idx3 = book_index(e)
        idx2 = book_index(ev2[e["id"]]) if e["id"] in ev2 else {}
        for book in idx3:
            if first_market(idx3[book], "h2h") is None:
                continue
            pairs3 += 1
            if not idx2.get(book):
                b3_only[book] += 1
    same_ts = b2["meta"]["timestamp"] == b3["meta"]["timestamp"]
    return {"missing": missing, "pairs_b2": pairs2, "pairs_b3_h2h": pairs3, "b3_only": dict(sorted(b3_only.items())),
            "same_timestamp": same_ts}


def spreads_totals_without_h2h(snap: dict) -> list[dict]:
    out = []
    for e in snap["events"]:
        for book, mkts in sorted(book_index(e).items()):
            keys = [m["key"] for m in mkts]
            if ("spreads" in keys or "totals" in keys) and "h2h" not in keys:
                out.append({"event_id": e["id"], "home_team": e.get("home_team", ""), "away_team": e.get("away_team", ""),
                            "bookmaker": book, "markets": ",".join(keys)})
    return out


# ------------------------------------------------------------------ identity
def identity_checks(snap: dict) -> dict:
    events = snap["events"]
    ids = Counter(e["id"] for e in events)
    triples = defaultdict(list)
    for e in events:
        triples[(e.get("home_team", ""), e.get("away_team", ""), e.get("commence_time", ""))].append(e["id"])
    snap_ts = parse_ts(snap["meta"]["timestamp"])
    before = []
    for e in events:
        ct = parse_ts(e.get("commence_time"))
        if ct is not None and snap_ts is not None and ct < snap_ts:
            before.append({"event_id": e["id"], "home_team": e.get("home_team", ""), "away_team": e.get("away_team", ""),
                           "commence_time": e.get("commence_time", ""), "seconds_before": int((snap_ts - ct).total_seconds())})
    dup_book, dup_market = 0, 0
    for e in events:
        names = Counter(b.get("key") for b in e.get("bookmakers") or [])
        dup_book += sum(1 for n in names.values() if n > 1)
        for b in e.get("bookmakers") or []:
            dup_market += sum(1 for n in Counter(m.get("key") for m in b.get("markets") or []).values() if n > 1)
    return {"dup_event_ids": {i: n for i, n in ids.items() if n > 1},
            "dup_triples": {t: v for t, v in triples.items() if len(v) > 1},
            "before_snapshot": before, "dup_event_bookmaker": dup_book, "dup_event_bookmaker_market": dup_market}


def read_stage_a_team_names(root: Path):
    path = root / "pilot" / "normalized" / "stage_a_events.csv"
    if not path.exists():
        return None
    names = set()
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            names.update(n for n in (r.get("home_team"), r.get("away_team")) if n)
    return names


def team_name_rows(snaps, stage_a_names) -> list[dict]:
    b_names = set()
    for s in snaps:
        for e in s["events"]:
            b_names.update(n for n in (e.get("home_team"), e.get("away_team")) if n)
    a_names = stage_a_names or set()
    return [{"team_name": n, "in_stage_a": "yes" if n in a_names else "no", "in_stage_b": "yes" if n in b_names else "no"}
            for n in sorted(a_names | b_names, key=lambda n: (n.casefold(), n))]


# --------------------------------------------------------------------- parity
def parity(snaps, anchors_by_step: dict, stage_b_classify) -> dict:
    compared, anchor_compared, diffs = 0, 0, []
    for s in snaps:
        for e in s["events"]:
            is_anchor = e["id"] == anchors_by_step.get(s["step"][:2])
            for b in e.get("bookmakers") or []:
                for m in b.get("markets") or []:
                    ours = classify_market(e, m)
                    theirs = stage_b_classify(m.get("key"), m, e.get("home_team", ""), e.get("away_team", ""))
                    compared += 1
                    anchor_compared += is_anchor
                    if ours != theirs:
                        diffs.append({"step": s["step"], "event_id": e["id"], "anchor": is_anchor,
                                      "bookmaker": b.get("key"), "market": m.get("key"), "normalize": ours,
                                      "stage_b": theirs,
                                      "outcomes": "; ".join(f"{o.get('name')} {o.get('price')} {o.get('point', '')}".strip()
                                                            for o in m.get("outcomes") or [])})
    return {"compared": compared, "anchor_compared": anchor_compared, "diffs": diffs,
            "anchor_diffs": [d for d in diffs if d["anchor"]]}


def anchor_event_ids(root: Path) -> dict:
    """Map 'B1' -> Quinnipiac event id, 'B2'/'B3' -> NJIT event id, read from anchors.json."""
    path = root / "pilot" / "normalized" / "anchors.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        by = {a["anchor_id"]: a.get("event_id") for a in doc.get("anchors", []) if a.get("found")}
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {"B1": by.get("quinnipiac_air_force"), "B2": by.get("njit_vermont"), "B3": by.get("njit_vermont")}


# --------------------------------------------------------------------- Cradl
def norm_name(s) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s).lower().replace("&", " and ")).split()


def team_matches(cradl_name, payload_name) -> bool:
    c, p = norm_name(cradl_name), norm_name(payload_name)
    return bool(c) and p[:len(c)] == c


def parse_price(s):
    if s is None:
        return None
    t = str(s).strip().replace("−", "-").lstrip("+")
    try:
        v = float(t)
    except ValueError:
        return None
    return int(v) if v == int(v) else v


def parse_point(s):
    t = "" if s is None else str(s).strip().replace("−", "-").lstrip("+")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def points_equal(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-9)


def label_quote(cradl_price, cradl_point, our_price, our_point) -> str:
    if cradl_price == our_price and points_equal(cradl_point, our_point):
        return "exact"
    cp, op = american_to_prob(cradl_price), american_to_prob(our_price)
    if cp is not None and op is not None and points_equal(cradl_point, our_point) and abs(op - cp) <= NEAR_PI + 1e-12:
        return "near"
    return "different"


def _label_sides(label: str):
    parts = re.split(r"\s+(?:vs\.?|v|@|at)\s+", str(label), maxsplit=1, flags=re.I)
    return (parts[0], parts[1]) if len(parts) == 2 else None


def reconcile_cradl(snaps, rows) -> list[dict]:
    snaps = good(snaps)
    events = {}
    for s in snaps:
        for e in s["events"]:
            events.setdefault(e["id"], e)
    out = []
    for r in rows:
        res = {"game_label": r.get("game_label", ""), "game_date": r.get("game_date", ""), "book": r.get("book", ""),
               "market": r.get("market", ""), "team": r.get("team", ""), "cradl_price": r.get("american_price", ""),
               "cradl_point": r.get("point", ""), "event_id": "", "snapshot_step": "", "snapshot_timestamp": "",
               "commence_time": "", "gap_s": "", "our_market_last_update": "", "our_price": "", "our_point": "",
               "delta_pi": "", "label": "not_found", "note": ""}
        out.append(res)
        cprice, cpoint = parse_price(r.get("american_price")), parse_point(r.get("point"))
        sides = _label_sides(r.get("game_label", ""))
        cands = []
        for e in events.values():
            ct = parse_ts(e.get("commence_time"))
            if ct is None or ct.astimezone(ET).strftime("%Y-%m-%d") != str(r.get("game_date", "")).strip():
                continue
            home, away = e.get("home_team", ""), e.get("away_team", "")
            if not (team_matches(r.get("team", ""), home) or team_matches(r.get("team", ""), away)
                    or str(r.get("market", "")).lower() == "totals"):
                continue
            if sides and not ((team_matches(sides[0], home) and team_matches(sides[1], away))
                              or (team_matches(sides[0], away) and team_matches(sides[1], home))):
                continue
            cands.append(e)
        if not cands:
            res["note"] = "no event on that ET date matches the team names"
            continue
        if len(cands) > 1:
            res["note"] = f"ambiguous: {len(cands)} events match"
            continue
        ev = cands[0]
        res["event_id"] = ev["id"]
        found = None
        why = "event found; no snapshot has this book"
        for s in snaps:
            e = next((x for x in s["events"] if x["id"] == ev["id"]), None)
            if e is None:
                continue
            mkts = book_index(e).get(str(r.get("book", "")).strip().lower())
            if not mkts:
                continue
            why = "event and book found; book has no such market"
            m = first_market(mkts, str(r.get("market", "")).strip().lower())
            if m is None:
                continue
            why = "market found; no outcome matches the team"
            for o in m.get("outcomes") or []:
                name = o.get("name", "")
                hit = (str(name).casefold() == str(r.get("team", "")).strip().casefold()
                       if m.get("key") == "totals" else team_matches(r.get("team", ""), name))
                if hit:
                    found = (s, e, m, o)
                    break
            if found:
                break
        if not found:
            res["note"] = why
            continue
        s, e, m, o = found
        our_price, our_point = o.get("price"), o.get("point")
        res.update(snapshot_step=s["step"], snapshot_timestamp=s["meta"]["timestamp"],
                   commence_time=e.get("commence_time", ""), our_market_last_update=m.get("last_update", ""),
                   our_price=our_price, our_point=fmt_point(our_point))
        ts, ct = parse_ts(s["meta"]["timestamp"]), parse_ts(e.get("commence_time"))
        if ts and ct:
            res["gap_s"] = int((ct - ts).total_seconds())
        if cprice is None:
            res.update(label="different", note="Cradl price not parseable")
            continue
        cp, op = american_to_prob(cprice), american_to_prob(our_price)
        if cp is not None and op is not None:
            res["delta_pi"] = f"{op - cp:+.4f}"
        res["label"] = label_quote(cprice, cpoint, our_price, our_point)
    return out


def read_cradl(root: Path):
    path = root / "pilot" / "cradl_observations.csv"
    if not path.exists():
        return None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        need = {"game_label", "game_date", "book", "market", "team", "american_price", "point"}
        if not need <= set(reader.fieldnames or []):
            raise NormalizeError("cradl_observations.csv is missing columns: " + ", ".join(sorted(need - set(reader.fieldnames or []))))
        return [r for r in reader if any((v or "").strip() for v in r.values())]


# --------------------------------------------------------------------- report
def md(headers, rows) -> str:
    esc = lambda c: str(c).replace("|", "/")  # noqa: E731
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def build_report(root: Path) -> tuple[str, dict]:
    snaps_all = load_snapshots(root)
    snaps = good(snaps_all)
    if not snaps:
        raise NormalizeError("no readable Stage B snapshots found in the ledger")
    by_step = {s["step"]: s for s in snaps}
    long_rows = [r for s in snaps for r in build_long_rows(s)]
    stage_a_names = read_stage_a_team_names(root)
    team_rows = team_name_rows(snaps, stage_a_names)
    anchors = anchor_event_ids(root)

    L = ["# Stage B normalization report", "",
         "Offline, read-only on pilot/raw. Every table counts **events in the sampled close-proxy snapshots**: "
         "not games, and not matched to results. **No results data is joined.** Nothing here establishes division, "
         "gender or sufficiency.", "", "## Inputs", ""]
    L.append(md(["Step", "Ledger status", "Raw file", "Regions", "Requested date", "Snapshot timestamp",
                 "previous_timestamp", "next_timestamp", "Events", "Bookmaker entries", "Long rows"],
                [[s["step"], s["ledger"]["status_code"] if not s["error"] else "UNREADABLE: " + s["error"], s["raw_name"],
                  s["regions"], s["requested"], s["meta"].get("timestamp", ""), s["meta"].get("previous_timestamp", ""),
                  s["meta"].get("next_timestamp", ""), len(s["events"]),
                  sum(len(e.get("bookmakers") or []) for e in s["events"]),
                  sum(1 for r in long_rows if r["step"] == s["step"])] for s in snaps_all]))
    L += ["", f"Stage A files: team names only ({'read' if stage_a_names is not None else 'stage_a_events.csv not found'}).",
          "", "## Outputs", "",
          md(["File", "Rows"], [["odds_long.csv", len(long_rows)], ["team_names.csv", len(team_rows)]])]
    for s in snaps:
        if s["n_ledger_200"] > 1:
            L.append(f"\nNote: {s['step']} has {s['n_ledger_200']} status-200 ledger rows; the latest is used.")

    # parity
    L += ["", "## Classification parity with stage_b", ""]
    try:
        par = parity(snaps, anchors, load_stage_b_classifier())
        L.append(f"Compared {par['compared']} (event, bookmaker, market) classifications across {', '.join(by_step)}; "
                 f"{par['anchor_compared']} are on anchor events. Anchor events differing: {len(par['anchor_diffs'])}. "
                 f"All differences: {len(par['diffs'])}. The only intended rule difference is spreads at 0/0.")
        if par["diffs"]:
            L += ["", md(["Step", "Event", "Anchor", "Bookmaker", "Market", "normalize", "stage_b", "Outcomes"],
                         [[d["step"], d["event_id"], "yes" if d["anchor"] else "no", d["bookmaker"], d["market"],
                           d["normalize"], d["stage_b"], d["outcomes"]] for d in par["diffs"]])]
    except NormalizeError as exc:
        par = None
        L.append(f"Parity unavailable: {exc}")

    # coverage
    L += ["", "## Coverage per snapshot (events)", "",
          "Columns: events where the book has any market / complete_paired h2h / spreads / totals. "
          "Spreads use the sum-to-zero rule."]
    for s in snaps:
        cov = coverage_table(s["events"])
        other = sorted({b["key"] for e in s["events"] for b in e.get("bookmakers") or []
                        if b.get("key") and b["key"] not in COVERAGE_BOOKS})
        L += ["", f"### {s['step']} ({len(s['events'])} events in payload; regions {s['regions']}; markets requested "
                  f"{s['markets_requested']})", "",
              md(["Bookmaker", "Any market", "h2h complete_paired", "spreads complete_paired", "totals complete_paired"],
                 [[b, cov[b]["any"], cov[b]["h2h"], cov[b]["spreads"], cov[b]["totals"]] for b in COVERAGE_BOOKS])]
        if other:
            L.append(f"\nOther bookmaker keys in the payload (not tabulated): {', '.join(other)}")

    L += ["", "## Events by number of target books with complete_paired h2h (draftkings, fanduel, betmgm; fliff excluded)", ""]
    dist_rows = []
    for s in snaps:
        d = target_book_distribution(s["events"])
        dist_rows.append([s["step"], len(s["events"]), d[0], d[1], d[2], d[3]])
    L.append(md(["Snapshot", "Events", "0 books", "1 book", "2 books", "3 books"], dist_rows))

    L += ["", "## B2 vs B3 same-snapshot reconciliation", ""]
    if "B2" in by_step and "B3" in by_step:
        rec = reconcile_b2_b3(by_step["B2"], by_step["B3"])
        L.append(f"B2 returned timestamp {by_step['B2']['meta']['timestamp']}; B3 returned timestamp "
                 f"{by_step['B3']['meta']['timestamp']} (identical: {'yes' if rec['same_timestamp'] else 'NO'}). "
                 f"(book, event) pairs with any market in B2: {rec['pairs_b2']}. Pairs with an h2h market in B3: "
                 f"{rec['pairs_b3_h2h']}. Pairs in B2 with no h2h in B3: {len(rec['missing'])}.")
        if rec["missing"]:
            L += ["", md(["Event", "Home", "Away", "Commence", "Book", "Markets in B2", "State in B3"],
                         [[m["event_id"], m["home_team"], m["away_team"], m["commence_time"], m["bookmaker"],
                           m["b2_markets"], m["b3_state"]] for m in rec["missing"]])]
        L.append("\nPairs with h2h in B3 and no market in B2, by book: "
                 + (", ".join(f"{k} {v}" for k, v in rec["b3_only"].items()) or "none"))
    else:
        L.append("B2 and/or B3 not available.")

    L += ["", "## Events with spreads or totals but no h2h at the same book", ""]
    for s in snaps:
        lst = spreads_totals_without_h2h(s)
        L.append(f"**{s['step']}**: {len(lst)} (event, book) pairs")
        if lst:
            L += ["", md(["Event", "Home", "Away", "Book", "Markets"],
                         [[x["event_id"], x["home_team"], x["away_team"], x["bookmaker"], x["markets"]] for x in lst]), ""]

    # identity
    L += ["", "## Identity checks", ""]
    idrows = []
    for s in snaps:
        c = identity_checks(s)
        idrows.append([s["step"], len(c["dup_event_ids"]), len(c["dup_triples"]), len(c["before_snapshot"]),
                       c["dup_event_bookmaker"], c["dup_event_bookmaker_market"]])
    L.append(md(["Snapshot", "Duplicate event_ids", "Duplicate (home, away, commence)", "Commence before snapshot",
                 "Duplicate (event, bookmaker)", "Duplicate (event, bookmaker, market)"], idrows))
    for s in snaps:
        c = identity_checks(s)
        for i, n in c["dup_event_ids"].items():
            L.append(f"\n{s['step']}: event_id {i} appears {n} times")
        for t, v in c["dup_triples"].items():
            L.append(f"\n{s['step']}: {t[0]} / {t[1]} / {t[2]} shared by event_ids {', '.join(v)}")
        if c["before_snapshot"]:
            L += ["", f"{s['step']}: events with commence_time before the snapshot timestamp", "",
                  md(["Event", "Home", "Away", "Commence", "Seconds before snapshot"],
                     [[x["event_id"], x["home_team"], x["away_team"], x["commence_time"], x["seconds_before"]]
                      for x in c["before_snapshot"]])]
    steps = list(by_step)
    shared = [f"{a}&{b}: {len({e['id'] for e in by_step[a]['events']} & {e['id'] for e in by_step[b]['events']})}"
              for i, a in enumerate(steps) for b in steps[i + 1:]]
    L += ["", "Event_ids shared between snapshots: " + "; ".join(shared) + ".",
          f"\nUnique team names (Stage B and Stage A events): {len(team_rows)}; written to team_names.csv "
          "(no division or gender decided)."]

    # Cradl
    L += ["", "## Cradl reconciliation (not pass/fail)", ""]
    cradl_res = None
    try:
        cradl_rows = read_cradl(root)
    except NormalizeError as exc:
        cradl_rows = None
        L.append(f"Cradl file unusable: {exc}")
    else:
        if cradl_rows is None:
            L.append("no Cradl file; skipped")
        else:
            cradl_res = reconcile_cradl(snaps, cradl_rows)
            counts = Counter(r["label"] for r in cradl_res)
            L.append(f"{len(cradl_res)} Cradl rows: exact {counts['exact']}, near (|delta pi| <= {NEAR_PI}, same point) "
                     f"{counts['near']}, different {counts['different']}, not_found {counts['not_found']}. "
                     "delta_pi = our implied probability minus Cradl's. The Cradl observation time is not recorded in the file.")
            L += ["", md(["Game", "Date", "Book", "Market", "Team", "Cradl price", "Cradl point", "Snapshot", "Our price",
                          "Our point", "delta_pi", "Label", "Note"],
                         [[r["game_label"], r["game_date"], r["book"], r["market"], r["team"], r["cradl_price"],
                           r["cradl_point"] or "-", r["snapshot_step"] or "-", r["our_price"] if r["our_price"] != "" else "-",
                           r["our_point"] or "-", r["delta_pi"] or "-", r["label"], r["note"]] for r in cradl_res])]
            seen, timing = set(), []
            for r in cradl_res:
                k = (r["event_id"], r["snapshot_step"])
                if r["snapshot_step"] and k not in seen:
                    seen.add(k)
                    timing.append([r["game_label"], r["snapshot_step"], r["snapshot_timestamp"], r["commence_time"],
                                   r["gap_s"], r["our_market_last_update"]])
            if timing:
                L += ["", "Timing of our snapshot against the scheduled start:", "",
                      md(["Game", "Snapshot", "Snapshot timestamp", "Scheduled start", "Seconds to start",
                          "Our market last_update (last matched row)"], timing)]
    L += ["", "## Scope", "",
          "- Counts above are of events in sampled snapshots, not distinct games and not target-sample counts.",
          "- No results data is joined; T1 to T3 are not assessed here.",
          "- Prices are as returned (American); implied probabilities are used only for the Cradl comparison."]
    return "\n".join(L) + "\n", {"long_rows": long_rows, "team_rows": team_rows, "parity": par, "cradl": cradl_res,
                                  "snapshots": snaps_all}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="normalize", description="Offline Stage B normalization (no network)")
    ap.add_argument("--root", default=".", help="project root (default: current directory)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        text, out = build_report(root)
    except NormalizeError as exc:
        print(f"ERROR: {exc}")
        return 1
    norm = root / "pilot" / "normalized"
    atomic_write(norm / "odds_long.csv", csv_text(LONG_COLUMNS, out["long_rows"]))
    atomic_write(norm / "team_names.csv", csv_text(["team_name", "in_stage_a", "in_stage_b"], out["team_rows"]))
    atomic_write(norm / "stage_b_normalization_report.md", text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
