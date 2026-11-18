"""Join audit: pilot odds events -> NCAALacrosseData results (seasons 2025 and 2026). OFFLINE.

Reads, read-only:
  external/NCAALacrosseData/data/ncaa_mens_lacrosse_matchstats_{2025,2026}.csv  (pinned commit, verified)
  the pilot odds snapshots, through src.analysis's own loaders (load_inputs, usable, observations, valid,
  event_weighted). src.analysis is the only project module imported. The real src.ledger is never imported:
  analysis.py does `from .ledger import Ledger, LedgerCorrupt` at module level but uses Ledger only in build(),
  which this audit never calls, so an inert stand-in is registered first (see import_analysis).
  src.odds_client, src.halt and src.stage_* are never imported. A socket guard is installed before the import.

Results columns used: date, team, opponent, home_away, home_score, visitor_score, overtime.
home_score is the ROW TEAM's goals and visitor_score the OPPONENT's goals, whatever home_away says.
No result / w / l column is read.

Matching rules are FROZEN. Unresolved names are reported, never guessed. A mechanical match is not a
verification; independently_verified comes only from manual_verification.csv, applied after matching.

Writes only under audit_join/out/, and only when run as a script:
  .\\.venv\\Scripts\\python.exe -B -m audit_join.join_audit
"""
from __future__ import annotations

import argparse
import csv
import difflib
import importlib
import json
import re
import socket
import subprocess
import sys
import types
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
RESULTS_REPO = ROOT / "external" / "NCAALacrosseData"
PINNED_COMMIT = "4ac0d2e5109de526120785a6bf16a50d4673b56e"
SEASONS = (2025, 2026)
OUT_DIR = HERE / "out"
ALIASES_CSV = HERE / "aliases.csv"
MANUAL_CSV = HERE / "manual_verification.csv"
RAW_DIR = ROOT / "pilot" / "raw"
TARGET_BOOKS = ("draftkings", "fanduel", "betmgm")
NY = ZoneInfo("America/New_York")

RESULT_COLUMNS = ["date", "team", "opponent", "home_away", "home_score", "visitor_score", "overtime"]
RENAME = {"team": "row_team", "opponent": "opp_raw", "home_score": "goals_row", "visitor_score": "goals_opp",
          "overtime": "ot_periods"}

TWO_SIDED_AGREE = "TWO_SIDED_AGREE"
SCORE_CONFLICT = "SCORE_CONFLICT"
SINGLE_SIDED = "SINGLE_SIDED"
MULTI_ROW = "MULTI_ROW"
MATCHED = ("UNIQUE_MATCH", "DATE_SHIFTED")
STATUSES = ("UNIQUE_MATCH", "DATE_SHIFTED", "AMBIGUOUS", "FAILED")
NAME_STATUSES = ("RESOLVED", "AMBIGUOUS_NAME", "UNRESOLVED")

LINK_COLUMNS = ["event_id", "season", "home_team", "away_team", "commence_latest", "commence_conflict",
                "candidate_dates", "status", "reason", "matched_game_key", "matched_date", "matched_via",
                "result_quality", "venue_class", "orientation", "n_books_h2h_any", "n_books_h2h_at_latest_pregame",
                "n_pregame_snapshots_with_h2h", "fav_context", "independently_verified"]
# Diagnostics the spec asks to record (candidate keys, conflict effect, NY date) plus the matched score.
LINK_EXTRA_COLUMNS = ["candidate_game_keys", "commence_conflict_changes_status", "status_first_sighting_only",
                      "status_latest_sighting_only", "ny_date_latest", "commence_distinct", "home_results_name",
                      "away_results_name", "home_name_status", "away_name_status", "home_goals", "away_goals",
                      "ot_periods"]


# --------------------------------------------------------------------------- guards
def _network_blocked(*args, **kwargs):
    raise RuntimeError("network blocked")


def install_socket_guard(monkeypatch=None) -> None:
    """Replace socket.socket.connect, socket.create_connection and socket.getaddrinfo with a raiser."""
    targets = [(socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
               (socket, "getaddrinfo")]
    for obj, name in targets:
        if monkeypatch is not None:
            monkeypatch.setattr(obj, name, _network_blocked)
        else:
            setattr(obj, name, _network_blocked)


def _is_forbidden_module(name: str) -> bool:
    return name in ("src.odds_client", "src.halt") or name.startswith("src.stage") or name == "src.ledger"


def _ledger_standin() -> types.ModuleType:
    m = types.ModuleType("src.ledger")
    m.__doc__ = "Inert stand-in registered by audit_join.join_audit; the real src.ledger is never imported."
    m._JOIN_AUDIT_STANDIN = True

    class LedgerCorrupt(Exception):
        pass

    class Ledger:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("src.ledger is not imported by join_audit; Ledger is unavailable here")

    m.Ledger, m.LedgerCorrupt = Ledger, LedgerCorrupt
    return m


def import_analysis():
    """Import src.analysis read-only, after the socket guard, without executing the real src.ledger.

    Returns (module, info). If src.analysis was already imported by other code in this process (for example
    another test module), it is reused as is and info says so."""
    install_socket_guard()
    sys.dont_write_bytecode = True
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    info = {"reused_existing_import": "src.analysis" in sys.modules, "new_forbidden_modules": []}
    if not info["reused_existing_import"]:
        before = set(sys.modules)
        standin = None
        if "src.ledger" not in sys.modules:
            standin = sys.modules["src.ledger"] = _ledger_standin()
        try:
            importlib.import_module("src.analysis")
        finally:
            if standin is not None and sys.modules.get("src.ledger") is standin:
                del sys.modules["src.ledger"]          # a later real `import src.ledger` elsewhere stays real
        new = sorted(m for m in set(sys.modules) - before if _is_forbidden_module(m))
        info["new_forbidden_modules"] = new
        if new:
            raise RuntimeError(f"importing src.analysis loaded forbidden modules: {new}")
    A = sys.modules["src.analysis"]
    info["ledger_module"] = ("inert stand-in (real src.ledger not imported)"
                             if A.Ledger.__qualname__.startswith("_ledger_standin")
                             else "real (src.analysis was already imported elsewhere)")
    return A, info


def verify_pin(repo: Path = RESULTS_REPO) -> str:
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    if head != PINNED_COMMIT:
        raise RuntimeError(f"results repo HEAD {head} != pinned {PINNED_COMMIT}")
    return head


# --------------------------------------------------------------------------- 1. results
def results_path(season: int, data_dir: Path | None = None) -> Path:
    return Path(data_dir or RESULTS_REPO / "data") / f"ncaa_mens_lacrosse_matchstats_{season}.csv"


def load_results(season: int, path: Path | None = None):
    """Returns (kept_rows, excluded_rows) as DataFrames."""
    raw = pd.read_csv(path or results_path(season), dtype=str, keep_default_na=False)
    return prepare_results(raw, season)


def prepare_results(raw: pd.DataFrame, season: int):
    missing = [c for c in RESULT_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"season {season}: missing columns {missing}")
    df = raw[RESULT_COLUMNS].copy().rename(columns=RENAME)
    df.insert(0, "row_id", [f"{season}:L{i + 2}" for i in range(len(df))])   # CSV line number (header = L1)
    df.insert(1, "season", season)
    for c in ("date", "row_team", "opp_raw", "home_away"):
        df[c] = df[c].astype(str).str.strip()
    parsed = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce")
    df["date"] = parsed.dt.strftime("%Y-%m-%d")
    df["goals_row"] = pd.to_numeric(df["goals_row"], errors="coerce")
    df["goals_opp"] = pd.to_numeric(df["goals_opp"], errors="coerce")
    df["ot_periods"] = pd.to_numeric(df["ot_periods"], errors="coerce").fillna(0).astype(int)
    reason = pd.Series("", index=df.index, dtype=object)
    reason[parsed.isna()] = "DATE_UNPARSEABLE"
    reason[(reason == "") & (parsed.dt.year != season)] = "DATE_OUTSIDE_SEASON"
    reason[(reason == "") & (df["goals_row"].isna() | df["goals_opp"].isna())] = "SCORE_MISSING"
    excluded = df[reason != ""].copy()
    excluded["exclude_reason"] = reason[reason != ""]
    kept = df[reason == ""].copy()
    kept["goals_row"] = kept["goals_row"].astype(int)
    kept["goals_opp"] = kept["goals_opp"].astype(int)
    return kept.reset_index(drop=True), excluded.reset_index(drop=True)


def _longest_prefix(s: str, names_longest_first) -> tuple[str | None, str]:
    for n in names_longest_first:
        if s == n:
            return n, ""
        if s.startswith(n + " "):
            return n, s[len(n) + 1:].strip()
    return None, ""


def clean_opponents(rows: pd.DataFrame) -> pd.DataFrame:
    """Map each opponent string to a canonical name. Known names = the file's distinct team strings."""
    known = sorted(set(rows["row_team"]), key=lambda x: (-len(x), x))
    first = {s: _longest_prefix(s, known) for s in sorted(set(rows["opp_raw"]))}
    opp_only = sorted((s for s, (n, _) in first.items() if n is None), key=lambda x: (-len(x), x))
    mapping = {}
    for s, (name, loc) in first.items():
        if name is not None:
            mapping[s] = (name, loc, False)
            continue
        y, loc2 = _longest_prefix(s, [o for o in opp_only if o != s])     # data-driven opponent-only prefix rule
        mapping[s] = (y, loc2, True) if y else (s, "", True)
    out = rows.copy()
    out["opp_name"] = out["opp_raw"].map(lambda s: mapping[s][0])
    out["opp_location_text"] = out["opp_raw"].map(lambda s: mapping[s][1])
    out["had_location_text"] = out["opp_location_text"] != ""
    out["opp_only"] = out["opp_raw"].map(lambda s: mapping[s][2])
    return out


VENUE_TWO = {("Home", "Away"): "A_HOME", ("Away", "Home"): "B_HOME", ("Away", "Away"): "BOTH_AWAY",
             ("Home", "Home"): "BOTH_HOME"}


def _venue_class(kind, ra, rb) -> str:
    if kind == "two":
        return VENUE_TWO.get((ra["home_away"], rb["home_away"]), "UNKNOWN_FLAGS")
    if kind == "single":
        r, side = (ra, "A") if ra is not None else (rb, "B")
        f = r["home_away"]
        return f"SINGLE_{side}_{f.upper()}" if f in ("Home", "Away") else "UNKNOWN_FLAGS"
    return "MULTI_ROW"


def build_canonical_games(rows: pd.DataFrame, excluded: pd.DataFrame | None = None):
    """One canonical game per (date, frozenset{row_team, opp_name}). team_a < team_b lexicographically
    (arbitrary but deterministic). Returns (games DataFrame, accounting dict)."""
    n_excluded = 0 if excluded is None else len(excluded)
    rows_total = len(rows) + n_excluded
    groups = defaultdict(list)
    for r in rows.to_dict("records"):
        groups[(r["date"], frozenset((r["row_team"], r["opp_name"])))].append(r)
    games, kinds, used = [], Counter(), []
    multi_rows = 0
    for (d, pair), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], sorted(kv[0][1]))):
        names = sorted(pair)
        a, b = names[0], names[-1]
        teams = sorted(r["row_team"] for r in rs)
        if len(names) == 2 and len(rs) == 2 and teams == names:
            kind = "two"
        elif len(names) == 2 and len(rs) == 1:
            kind = "single"
        else:
            kind = "multi"
            multi_rows += len(rs)
        kinds[kind] += 1
        used += [r["row_id"] for r in rs]
        ra = next((r for r in rs if r["row_team"] == a), None)
        rb = next((r for r in rs if r["row_team"] == b), None) if len(names) == 2 else None
        goals_a = ra["goals_row"] if ra is not None else (rb["goals_opp"] if rb is not None else None)
        goals_b = rb["goals_row"] if rb is not None else (ra["goals_opp"] if ra is not None else None)
        if kind == "multi":
            quality = MULTI_ROW
        elif kind == "single":
            quality = SINGLE_SIDED
        else:
            agree = ra["goals_row"] == rb["goals_opp"] and rb["goals_row"] == ra["goals_opp"]
            quality = TWO_SIDED_AGREE if agree else SCORE_CONFLICT
        winner = "a" if goals_a > goals_b else ("b" if goals_b > goals_a else "TIE")
        ots = [int(r["ot_periods"]) for r in rs]
        season = int(rs[0]["season"])
        games.append({
            "game_key": f"{season}|{d}|{a}|{b}", "season": season, "date": d, "team_a": a, "team_b": b,
            "goals_a": int(goals_a), "goals_b": int(goals_b), "margin_a_minus_b": int(goals_a - goals_b),
            "winner": winner, "winner_team": {"a": a, "b": b}.get(winner, "TIE"),
            "ot_periods": max(ots), "ot_mismatch": kind == "two" and len(set(ots)) > 1,
            "home_flag_a": ra["home_away"] if ra is not None else "missing",
            "home_flag_b": rb["home_away"] if rb is not None else "missing",
            "venue_class": _venue_class(kind, ra, rb), "result_quality": quality, "n_rows": len(rs),
            "two_sided": kind == "two", "opp_only_flag": any(bool(r["opp_only"]) for r in rs),
            "had_location_text_flag": any(bool(r["had_location_text"]) for r in rs),
            "row_ids": "; ".join(r["row_id"] for r in rs)})
    # A row never feeds two games, and a reciprocal pair yields exactly one game.
    assert len(used) == len(set(used)) == len(rows), "a results row was used by more than one game"
    assert rows_total == n_excluded + 2 * kinds["two"] + kinds["single"] + multi_rows, "accounting identity failed"
    acct = {"rows_total": rows_total, "rows_excluded": n_excluded, "two_sided_games": kinds["two"],
            "single_sided_games": kinds["single"], "multi_row_games": kinds["multi"], "multi_row_rows": multi_rows}
    gdf = pd.DataFrame(games)
    if len(gdf):
        acct.update(score_conflict=int((gdf.result_quality == SCORE_CONFLICT).sum()),
                    ot_mismatch=int(gdf.ot_mismatch.sum()), ties=int((gdf.winner == "TIE").sum()))
    else:
        acct.update(score_conflict=0, ot_mismatch=0, ties=0)
    return gdf, acct


def results_canonical_names(rows: pd.DataFrame) -> set:
    return set(rows["row_team"]) | set(rows["opp_name"])


# --------------------------------------------------------------------------- 2. odds inventory
def _parse_utc(v: str) -> datetime:
    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def build_event_inventory(A, root: Path = ROOT):
    """One row per event_id from analysis.load_inputs / usable / observations / valid / event_weighted.
    Returns (rows, usable_snapshots)."""
    inp = A.load_inputs(Path(root))
    snaps = sorted(A.usable(inp["snapshots"]), key=lambda s: s["ts"])
    obs = A.observations(inp["snapshots"])
    v = A.valid(obs, "h2h")
    ew = A.event_weighted([o for o in obs if o["market"] == "h2h"])
    sight = defaultdict(list)
    for s in snaps:
        for e in s["events"]:
            ct = e.get("commence_time")
            cdt = A.parse_ts(ct)
            tts = (cdt - s["ts"]).total_seconds() if cdt else None
            sight[e["id"]].append({"step": s["step"], "snap_ts": s["ts"], "commence_time": ct,
                                   "home": e.get("home_team"), "away": e.get("away_team"),
                                   "pregame": tts is not None and tts > 0})
    books, snaps_h2h, books_at = defaultdict(set), defaultdict(set), defaultdict(set)
    for o in v:
        books[o["event_id"]].add(o["book"])
        snaps_h2h[o["event_id"]].add(o["snap_ts"])
        books_at[(o["event_id"], o["snap_ts"])].add(o["book"])
    ewd = {(o["event_id"], o["book"]): o for o in ew}
    rows = []
    for eid, ss in sight.items():
        latest = ss[-1]
        distinct = list(dict.fromkeys(x["commence_time"] for x in ss))
        pre = [x for x in ss if x["pregame"]]
        lp = pre[-1] if pre else None
        row = {"event_id": eid, "season": _parse_utc(latest["commence_time"]).year,
               "home_team": latest["home"], "away_team": latest["away"],
               "commence_sightings": [x["commence_time"] for x in ss],
               "commence_sightings_text": "; ".join(f"{x['step']}={x['commence_time']}" for x in ss),
               "commence_distinct": " | ".join(distinct), "commence_first": ss[0]["commence_time"],
               "commence_latest": latest["commence_time"], "commence_conflict": len(distinct) > 1,
               "team_names_conflict": len({(x["home"], x["away"]) for x in ss}) > 1, "n_snapshots": len(ss),
               "latest_pregame_step": lp["step"] if lp else "",
               "n_books_h2h_any": len(books[eid]), "n_pregame_snapshots_with_h2h": len(snaps_h2h[eid]),
               "n_books_h2h_at_latest_pregame": len(books_at.get((eid, lp["snap_ts"]), ())) if lp else 0}
        fav_obs = None
        for b in TARGET_BOOKS:
            o = ewd.get((eid, b))
            row[f"{b}_home_price"] = o["price_a"] if o else None
            row[f"{b}_away_price"] = o["price_b"] if o else None
            row[f"{b}_price_step"] = o["step"] if o else ""
            if o and (fav_obs is None or o["snap_ts"] > fav_obs["snap_ts"]):
                fav_obs = o
        if fav_obs:
            pa, pb = fav_obs["price_a"], fav_obs["price_b"]
            team = latest["home"] if pa < pb else (latest["away"] if pb < pa else "PICK")
            row["fav_context"] = f"{team} ({fav_obs['book']} {fav_obs['step']}: {pa}/{pb})"
        else:
            row["fav_context"] = ""
        rows.append(row)
    rows.sort(key=lambda r: (r["commence_latest"], r["event_id"]))
    return rows, snaps


def _raw_odds_files(raw_dir: Path):
    for f in sorted(Path(raw_dir).glob("*.json")):
        if f.name.endswith(".meta.json"):
            continue
        meta = f.with_name(f.name[:-len(".json")] + ".meta.json")
        if not meta.exists():
            continue
        with open(meta, "rb") as fh:
            m = json.loads(fh.read().decode("utf-8"))
        if str(m.get("endpoint", "")).rstrip("/").endswith("/odds"):
            yield f


def tally_bookmakers(snaps, raw_dir: Path | None = RAW_DIR) -> dict:
    """Observation only. Keys from the analysis loader's payloads; if no non-target key is visible there,
    the raw odds payloads (meta endpoint ending in /odds) are parsed read-only."""
    def tally(events_iter):
        ev = defaultdict(set)
        for e in events_iter:
            for b in e.get("bookmakers") or []:
                ev[b.get("key")].add(e.get("id"))
        non = sorted(k for k in ev if k not in TARGET_BOOKS)
        touched = set().union(*(ev[k] for k in non)) if non else set()
        return {"events_per_key": {k: len(v) for k, v in sorted(ev.items())}, "non_target_keys": non,
                "events_touched_by_non_target": len(touched)}
    out = {"loader": tally(e for s in snaps for e in s["events"]),
           "loader_source": "analysis.load_inputs payloads (usable snapshots; analysis excludes B3)", "raw": None}
    if not out["loader"]["non_target_keys"] and raw_dir is not None and Path(raw_dir).exists():
        files = list(_raw_odds_files(raw_dir))
        evs = []
        for f in files:
            with open(f, "rb") as fh:
                obj = json.loads(fh.read().decode("utf-8"))
            evs += obj.get("data", []) if isinstance(obj, dict) else obj
        out["raw"] = tally(evs)
        out["raw_source"] = f"{len(files)} raw odds payloads in pilot/raw (meta endpoint ends with /odds), read-only"
    return out


# --------------------------------------------------------------------------- 3. names
def normalize_name(s: str) -> str:
    s = str(s).lower().replace("&", " and ")
    s = re.sub(r"[.'\u2019`]", "", s)
    s = re.sub(r"[^\w\s]|_", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _sub_token(s: str, pattern: str, replacement: str) -> str:
    out = re.sub(rf"(?<!\S){re.escape(pattern)}(?!\S)", replacement, s)
    return re.sub(r"\s+", " ", out).strip()


def _match_form(n: str) -> str:
    return _sub_token(n, "state", "st")


def load_aliases(path: Path = ALIASES_CSV) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return [(normalize_name(r["odds_pattern"]), normalize_name(r["results_name"])) for r in csv.DictReader(fh)]


def apply_aliases(n: str, aliases) -> str:
    for pat, rep in aliases:
        n = _sub_token(n, pat, rep)
    return n


def _candidate_maps(names):
    exact, match = defaultdict(set), defaultdict(set)
    for name in names:
        n = normalize_name(name)
        exact[n].add(name)
        match[_match_form(n)].add(name)
    return exact, match


def _prefix_hits(s: str, match_map) -> list[str]:
    return sorted((k for k in match_map if s == k or s.startswith(k + " ")), key=lambda k: (-len(k), k))


def resolve_name(odds_name: str, exact_map, match_map, aliases) -> dict:
    """EXACT, then ALIAS, then PREFIX (state -> st, longest word-prefix). Resolves only if exactly one
    results name remains at the winning method and length."""
    n = normalize_name(odds_name)
    rec = {"odds_name": odds_name, "normalized": n, "results_name": "", "method": "", "status": "UNRESOLVED",
           "tied_candidates": "", "suggestion_only": "", "collision_flag": False, "collision_candidates": "",
           "matched_form": ""}
    names, used = None, None
    if n in exact_map:
        rec["method"], names, used = "EXACT", exact_map[n], n
    else:
        a = apply_aliases(n, aliases)
        if a != n:
            hits = _prefix_hits(_match_form(a), match_map)
            if hits:
                rec["method"], names, used = "ALIAS", match_map[hits[0]], _match_form(a)
        if names is None:
            hits = _prefix_hits(_match_form(n), match_map)
            if hits:
                rec["method"], names, used = "PREFIX", match_map[hits[0]], _match_form(n)
    if names is None:
        sug = difflib.get_close_matches(_match_form(n), list(match_map), n=3, cutoff=0.6)
        rec["suggestion_only"] = "; ".join(sorted(match_map[k])[0] for k in sug)
        return rec
    rec["matched_form"] = used
    every = sorted({x for k in _prefix_hits(used, match_map) for x in match_map[k]})
    if len(every) >= 2:
        rec["collision_flag"] = True
        rec["collision_candidates"] = "; ".join(every)
    if len(names) == 1:
        rec["status"], rec["results_name"] = "RESOLVED", next(iter(names))
    else:
        rec["status"], rec["tied_candidates"] = "AMBIGUOUS_NAME", "; ".join(sorted(names))
    return rec


def resolve_names(odds_names, candidate_names, aliases) -> dict:
    exact_map, match_map = _candidate_maps(candidate_names)
    return {n: resolve_name(n, exact_map, match_map, aliases) for n in sorted(set(odds_names))}


def build_crosswalk(events, resolutions: dict, names_by_season: dict) -> pd.DataFrame:
    n_events = Counter()
    for e in events:
        n_events[e["home_team"]] += 1
        n_events[e["away_team"]] += 1
    rows = []
    for name, r in sorted(resolutions.items()):
        rows.append({"odds_name": name, "results_name": r["results_name"], "method": r["method"],
                     "status": r["status"], "n_events": n_events[name],
                     "present_in_2025_file": bool(r["results_name"]) and r["results_name"] in names_by_season.get(2025, set()),
                     "present_in_2026_file": bool(r["results_name"]) and r["results_name"] in names_by_season.get(2026, set()),
                     "suggestion_only": r["suggestion_only"], "collision_flag": r["collision_flag"],
                     "tied_candidates": r["tied_candidates"]})
    return pd.DataFrame(rows)


def collision_log(resolutions: dict) -> pd.DataFrame:
    rows = [{"odds_name": r["odds_name"], "matched_form": r["matched_form"], "candidates": r["collision_candidates"],
             "chosen": r["results_name"] or r["tied_candidates"], "method": r["method"], "status": r["status"]}
            for r in resolutions.values() if r["collision_flag"]]
    return pd.DataFrame(rows, columns=["odds_name", "matched_form", "candidates", "chosen", "method", "status"])


# --------------------------------------------------------------------------- 4. matching
def candidate_dates(sightings) -> tuple[set, set]:
    """(UTC dates, preceding days) over every sighting. Never a day after."""
    utc, prev = set(), set()
    for ct in sightings:
        d = _parse_utc(ct).date()
        utc.add(d.isoformat())
        prev.add((d - timedelta(days=1)).isoformat())
    return utc, prev


def _classify(pair, utc, prev, games_by_pair):
    cands = sorted((g for g in games_by_pair.get(pair, []) if g["date"] in utc or g["date"] in prev),
                   key=lambda g: (g["date"], g["game_key"]))
    if not cands:
        return "FAILED", "", cands
    if len(cands) > 1:
        return "AMBIGUOUS", "MULTIPLE_CANDIDATE_GAMES", cands
    if cands[0]["result_quality"] == MULTI_ROW:
        return "AMBIGUOUS", "MULTI_ROW_ONLY", cands
    return ("UNIQUE_MATCH" if cands[0]["date"] in utc else "DATE_SHIFTED"), "", cands


def orientation(game: dict, home_results_name: str) -> str:
    """Diagnostic only; never an input to matching."""
    vc = game["venue_class"]
    side = "A" if home_results_name == game["team_a"] else "B"
    if vc in ("A_HOME", "B_HOME"):
        return "AGREES" if vc == f"{side}_HOME" else "DISAGREES"
    if vc == "BOTH_AWAY":
        return "NEUTRAL_OR_UNKNOWN"
    if vc == "BOTH_HOME":
        return "CONFLICT"
    if vc.startswith("SINGLE_"):
        return "SINGLE_SIDED_NOT_CHECKABLE"     # one flag cannot exclude a neutral site or a both-Home conflict
    return "NEUTRAL_OR_UNKNOWN"


def _games_by_pair(games) -> dict:
    recs = games.to_dict("records") if isinstance(games, pd.DataFrame) else list(games)
    out = defaultdict(list)
    for g in recs:
        out[frozenset((g["team_a"], g["team_b"]))].append(g)
    return out


def match_event(ev: dict, resolutions: dict, games_by_pair: dict, results_names: set) -> dict:
    rh, ra = resolutions[ev["home_team"]], resolutions[ev["away_team"]]
    sightings = list(ev["commence_sightings"])
    utc, prev = candidate_dates(sightings)
    link = {c: "" for c in LINK_COLUMNS + LINK_EXTRA_COLUMNS}
    link.update({k: ev.get(k, "") for k in ("event_id", "season", "home_team", "away_team", "n_books_h2h_any",
                                            "n_books_h2h_at_latest_pregame", "n_pregame_snapshots_with_h2h",
                                            "fav_context")})
    link.update(commence_latest=ev["commence_latest"], commence_conflict=bool(ev.get("commence_conflict")),
                candidate_dates="; ".join(sorted(utc | prev)),
                ny_date_latest=_parse_utc(ev["commence_latest"]).astimezone(NY).date().isoformat(),
                commence_distinct=" | ".join(dict.fromkeys(sightings)), home_results_name=rh["results_name"],
                away_results_name=ra["results_name"], home_name_status=rh["status"],
                away_name_status=ra["status"], commence_conflict_changes_status=False,
                independently_verified="no")
    statuses = (rh["status"], ra["status"])
    if "AMBIGUOUS_NAME" in statuses:
        link.update(status="AMBIGUOUS", reason="NAME")
        return link
    if "UNRESOLVED" in statuses:
        link.update(status="FAILED", reason="NAME_UNRESOLVED")
        return link
    if rh["results_name"] == ra["results_name"]:
        link.update(status="AMBIGUOUS", reason="NAME_SAME_TEAM")
        return link
    pair = frozenset((rh["results_name"], ra["results_name"]))
    status, reason, cands = _classify(pair, utc, prev, games_by_pair)
    if status == "FAILED":
        both = rh["results_name"] in results_names and ra["results_name"] in results_names
        reason = "NO_GAME_FOR_PAIR" if both else "TEAM_NOT_IN_RESULTS"
    link.update(status=status, reason=reason, candidate_game_keys="; ".join(g["game_key"] for g in cands))
    if ev.get("commence_conflict"):
        s_first = _classify(pair, *candidate_dates([sightings[0]]), games_by_pair)[0]
        s_last = _classify(pair, *candidate_dates([sightings[-1]]), games_by_pair)[0]
        link.update(status_first_sighting_only=s_first, status_latest_sighting_only=s_last,
                    commence_conflict_changes_status=s_first != s_last)
    if status in MATCHED:
        g = cands[0]
        home_is_a = rh["results_name"] == g["team_a"]
        link.update(matched_game_key=g["game_key"], matched_date=g["date"],
                    matched_via="utc_date" if g["date"] in utc else "previous_day",
                    result_quality=g["result_quality"], venue_class=g["venue_class"],
                    orientation=orientation(g, rh["results_name"]),
                    home_goals=g["goals_a"] if home_is_a else g["goals_b"],
                    away_goals=g["goals_b"] if home_is_a else g["goals_a"], ot_periods=g["ot_periods"])
    return link


def match_events(events, resolutions: dict, games, results_names: set) -> pd.DataFrame:
    """Mechanical matching. Reads nothing from disk; independently_verified is always 'no' here."""
    gbp = _games_by_pair(games)
    links = [match_event(ev, resolutions, gbp, results_names) for ev in events]
    return pd.DataFrame(links, columns=LINK_COLUMNS + LINK_EXTRA_COLUMNS)


def apply_manual_verification(links: pd.DataFrame, path: Path = MANUAL_CSV):
    """Applied to the final links table only. Returns (links copy, manual rows that matched no link)."""
    out = links.copy()
    out["independently_verified"] = "no"
    unmatched = []
    if not Path(path).exists():
        return out, unmatched
    with open(path, encoding="utf-8", newline="") as fh:
        manual = list(csv.DictReader(fh))
    for m in manual:
        mask = (out["home_team"].str.contains(m["home_team_substring"], case=False, regex=False)
                & out["away_team"].str.contains(m["away_team_substring"], case=False, regex=False)
                & (out["matched_date"] == m["results_date"]) & out["status"].isin(MATCHED))
        if mask.any():
            out.loc[mask, "independently_verified"] = "yes"
        else:
            unmatched.append(m)
    return out, unmatched


# --------------------------------------------------------------------------- 5. report and outputs
def md_table(headers, rows) -> str:
    def cell(v):
        return str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(map(cell, headers)) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def _season_scopes(df):
    return [("all", df)] + [(str(y), df[df["season"] == y]) for y in SEASONS]


def _books_row(label, scope, season, d, col):
    n = d[col].astype(int)
    return [label, scope, season, len(d), int((n >= 1).sum()), int((n >= 2).sum()), int((n == 3).sum())]


def build_report(ctx: dict) -> str:
    links, inv, xw, coll = ctx["links"], ctx["inventory"], ctx["crosswalk"], ctx["collisions"]
    acct, bk, info = ctx["accounting"], ctx["bookmakers"], ctx["analysis_info"]
    matched = links[links["status"].isin(MATCHED)]
    L = ["# Join audit: pilot odds events vs NCAALacrosseData results (2025, 2026)", "",
         f"- Results repo commit: {ctx['pin']} (pinned, verified). Columns used: {', '.join(RESULT_COLUMNS)}; "
         "home_score = row team's goals, visitor_score = opponent's goals; no result/w/l column read.",
         f"- Odds events: src.analysis load_inputs / usable / observations / valid / event_weighted (read-only). "
         f"src.ledger module in this process: {info.get('ledger_module')}; reused existing import: "
         f"{info.get('reused_existing_import')}.",
         "- Matching rules are frozen; mechanical matching is not verification. Every count below is computed "
         "from the output tables.", ""]

    L += ["## A. Events", "", md_table(["Scope", "Events"], [[s, len(d)] for s, d in _season_scopes(links)]), ""]

    L += ["## B. Match status", ""]
    rows = [[st] + [int((d["status"] == st).sum()) for _, d in _season_scopes(links)] for st in STATUSES]
    L += [md_table(["Status", "all", "2025", "2026"], rows), ""]
    rr = links[links["status"].isin(["FAILED", "AMBIGUOUS"])].groupby(["status", "reason"]).size()
    L += [md_table(["Status", "Reason", "Events"], [[s, r, int(n)] for (s, r), n in rr.items()]) if len(rr)
          else "No FAILED or AMBIGUOUS events.", ""]
    conf = links[links["commence_conflict"].astype(bool)]
    L += [f"Events with commence_conflict: {len(conf)}; of these, status would differ between first-only and "
          f"latest-only sightings: {int(conf['commence_conflict_changes_status'].astype(bool).sum())}.", ""]

    L += ["## C. Moneyline coverage (target books draftkings, fanduel, betmgm)", ""]
    rows = []
    for col in ("n_books_h2h_any", "n_books_h2h_at_latest_pregame"):
        for scope, base in (("all events", links), ("matched events", matched)):
            for s, d in _season_scopes(base):
                rows.append(_books_row(col, scope, s, d, col))
    L += [md_table(["Definition", "Scope", "Season", "Events", ">=1 book", ">=2 books", "3 books"], rows), ""]
    rows = []
    for scope, base in (("all events", links), ("matched events", matched)):
        for s, d in _season_scopes(base):
            n = d["n_pregame_snapshots_with_h2h"].astype(int)
            rows.append([scope, s, len(d), int((n == 0).sum()), int((n == 1).sum()), int((n == 2).sum()),
                         int((n >= 3).sum())])
    L += ["Pregame snapshots with a valid h2h at >= 1 target book (unit: events):", "",
          md_table(["Scope", "Season", "Events", "0", "1", "2", "3+"], rows), ""]

    L += ["## D. Orientation and venue among matched events (diagnostic only)", ""]
    oc, vc = matched["orientation"].value_counts(), matched["venue_class"].value_counts()
    L += [md_table(["Orientation", "Events"], [[k, int(v)] for k, v in oc.items()]), "",
          md_table(["venue_class", "Events"], [[k, int(v)] for k, v in vc.items()]), ""]
    bad = matched[matched["orientation"].isin(["DISAGREES", "CONFLICT"])]
    L += ["Events with orientation DISAGREES or CONFLICT:", "",
          md_table(["event_id", "Home (odds)", "Away (odds)", "Results date", "venue_class", "Orientation"],
                   bad[["event_id", "home_team", "away_team", "matched_date", "venue_class", "orientation"]].values.tolist())
          if len(bad) else "None.", ""]

    L += ["## E. Team name resolution (unit: distinct odds team names)", ""]
    L += [md_table(["Status", "Names"], [[s, int((xw["status"] == s).sum())] for s in NAME_STATUSES]), "",
          md_table(["Method", "Resolved names"],
                   [[m, int(((xw["status"] == "RESOLVED") & (xw["method"] == m)).sum())] for m in ("EXACT", "ALIAS", "PREFIX")]), ""]
    rows = []
    for _, r in xw[xw["status"] != "RESOLVED"].iterrows():
        blocked = int(((links["home_team"] == r["odds_name"]) | (links["away_team"] == r["odds_name"])).sum())
        rows.append([r["odds_name"], r["status"], blocked, r["tied_candidates"], r["suggestion_only"]])
    L += ["Unresolved or ambiguous names and the events each blocks (suggestions are never used):", "",
          md_table(["Odds name", "Status", "Events blocked", "Tied candidates", "suggestion_only"], rows)
          if rows else "None.", ""]
    L += ["Collision log (two or more results names are word-prefixes; the longest is chosen):", "",
          md_table(list(coll.columns), coll.values.tolist()) if len(coll) else "None.", ""]

    L += ["## F. Results integrity (per season)", ""]
    keys = ["rows_total", "rows_excluded", "two_sided_games", "single_sided_games", "multi_row_games",
            "multi_row_rows", "score_conflict", "ot_mismatch", "ties"]
    L += [md_table(["Season"] + keys, [[y] + [acct[y][k] for k in keys] for y in SEASONS]), ""]
    for y in SEASONS:
        a = acct[y]
        L.append(f"- {y}: rows_total {a['rows_total']} = excluded {a['rows_excluded']} + 2 x {a['two_sided_games']} "
                 f"+ {a['single_sided_games']} + {a['multi_row_rows']} = "
                 f"{a['rows_excluded'] + 2 * a['two_sided_games'] + a['single_sided_games'] + a['multi_row_rows']} (holds)")
    ex = ctx["excluded"]
    if len(ex):
        L += ["", md_table(["Season", "Reason", "Rows"],
                           [[s, r, int(n)] for (s, r), n in ex.groupby(["season", "exclude_reason"]).size().items()])]
    L.append("")

    L += ["## G. Sample available for model-versus-market (books = n_books_h2h_any)", "",
          "STRICT = matched (UNIQUE_MATCH or DATE_SHIFTED), result_quality TWO_SIDED_AGREE, orientation not "
          "DISAGREES/CONFLICT. LENIENT = as STRICT but result_quality may also be SINGLE_SIDED.", ""]
    ok_orient = ~matched["orientation"].isin(["DISAGREES", "CONFLICT"])
    tiers = {"STRICT": matched[ok_orient & (matched["result_quality"] == TWO_SIDED_AGREE)],
             "LENIENT": matched[ok_orient & matched["result_quality"].isin([TWO_SIDED_AGREE, SINGLE_SIDED])]}
    rows = []
    for t, d in tiers.items():
        for lab, cond in ((">=1", lambda n: n >= 1), (">=2", lambda n: n >= 2), ("3", lambda n: n == 3)):
            for s, ds in _season_scopes(d):
                x = ds[cond(ds["n_books_h2h_any"].astype(int))]
                days = x["matched_date"].nunique()
                rows.append([t, lab, s, len(x), days, f"{len(x) / days:.2f}" if days else "-"])
    L += [md_table(["Tier", "Books", "Season", "Events", "Distinct game days", "Events per game day"], rows), ""]

    yes = int((links["independently_verified"] == "yes").sum())
    L += ["## H. Manual verification", "",
          f"- Links with independently_verified = yes: {yes} of {len(links)} (from manual_verification.csv, applied "
          f"after matching). Manual rows that matched no link: {len(ctx.get('manual_unmatched', []))}.",
          "- Mechanical matching is separate from manual verification: a UNIQUE_MATCH or DATE_SHIFTED status is "
          "not a confirmation.", ""]

    L += ["## I. Observation: bookmaker keys outside the three targets (excluded from every coverage figure)", ""]
    for label, key in (("loader", "loader"), ("raw", "raw")):
        t = bk.get(key)
        if not t:
            continue
        src = bk.get(f"{key}_source", "")
        L.append(f"- {label} ({src}): keys {t['events_per_key']}; non-target keys {t['non_target_keys'] or 'none'}; "
                 f"events touched by a non-target key: {t['events_touched_by_non_target']}")
    L.append("")
    return "\n".join(L)


def write_outputs(ctx: dict, out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    inv = pd.DataFrame(ctx["inventory"]).drop(columns=["commence_sightings"], errors="ignore")
    files = {"canonical_games_2025_2026.csv": ctx["games"], "excluded_results_rows.csv": ctx["excluded"],
             "team_crosswalk_proposed.csv": ctx["crosswalk"], "name_collisions.csv": ctx["collisions"],
             "event_inventory.csv": inv, "event_game_links.csv": ctx["links"]}
    written = []
    for name, df in files.items():
        p = out_dir / name
        df.to_csv(p, index=False, lineterminator="\n")
        written.append(p)
    p = out_dir / "join_audit.md"
    p.write_text(ctx["report"], encoding="utf-8", newline="\n")
    written.append(p)
    return written


def run_audit(root: Path = ROOT, data_dir: Path | None = None, write: bool = True, out_dir: Path = OUT_DIR) -> dict:
    pin = verify_pin()
    A, info = import_analysis()
    kept_all, excl_all, games_all, acct, names_by_season = [], [], [], {}, {}
    for y in SEASONS:
        kept, excl = load_results(y, results_path(y, data_dir))
        kept = clean_opponents(kept)
        games, a = build_canonical_games(kept, excl)
        kept_all.append(kept)
        excl_all.append(excl)
        games_all.append(games)
        acct[y] = a
        names_by_season[y] = results_canonical_names(kept)
    games = pd.concat(games_all, ignore_index=True)
    excluded = pd.concat(excl_all, ignore_index=True)
    results_names = set().union(*names_by_season.values())
    inv, snaps = build_event_inventory(A, root)
    aliases = load_aliases()
    resolutions = resolve_names([e["home_team"] for e in inv] + [e["away_team"] for e in inv], results_names, aliases)
    links = match_events(inv, resolutions, games, results_names)
    links, manual_unmatched = apply_manual_verification(links)
    ctx = {"pin": pin, "analysis_info": info, "games": games, "excluded": excluded, "accounting": acct,
           "inventory": inv, "crosswalk": build_crosswalk(inv, resolutions, names_by_season),
           "collisions": collision_log(resolutions), "links": links, "manual_unmatched": manual_unmatched,
           "bookmakers": tally_bookmakers(snaps)}
    ctx["report"] = build_report(ctx)
    if write:
        ctx["written"] = write_outputs(ctx, out_dir)
    return ctx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Offline join audit (writes audit_join/out/).")
    ap.add_argument("--no-write", action="store_true", help="compute and print the report only")
    args = ap.parse_args(argv)
    ctx = run_audit(write=not args.no_write)
    print(ctx["report"])
    for p in ctx.get("written", []):
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
