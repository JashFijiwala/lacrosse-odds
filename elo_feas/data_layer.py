"""Results data layer for the Elo feasibility experiment (no Elo here).

Reads the pinned NCAALacrosseData men's match files for 2022-2026, adapts the two header families to one
common frame, strips mascots from 2022/2023 row-team names, and builds an orientation-free canonical game
table by running audit_join.join_audit.prepare_results / clean_opponents / build_canonical_games UNCHANGED.
join_audit is imported read-only. The `result` column is never loaded. No network.

    python -m elo_feas.data_layer          # builds everything into elo_feas/out/
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

from audit_join import join_audit as J  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"
SEASONS = (2022, 2023, 2024, 2025, 2026)
MASCOT_SEASONS = (2022, 2023)
FAMILY = {2022: "snake", 2023: "title", 2024: "title", 2025: "snake", 2026: "snake"}
COMMON_COLUMNS = ["season", "date", "team", "opponent", "team_goals", "opp_goals", "flag", "overtime"]
FLAGS = ("Home", "Away")

# role -> header, per family. home_score/visitor_score are the ROW TEAM's and the OPPONENT's goals.
HEADERS = {
    "snake": dict(date="date", team="team", opponent="opponent", flag="home_away", team_goals="home_score",
                  opp_goals="visitor_score", overtime="overtime"),
    "title": dict(date="Date", team="Team", opponent="Opponent", flag="Home / Away", team_goals="Home Score",
                  opp_goals="Visitor Score", overtime="Overtime"),
}
# The first 8 header cells of every real file, in order (the 5th is the column we never load).
CORE_HEADER = {
    "snake": ["date", "team", "opponent", "home_away", "result", "home_score", "visitor_score", "overtime"],
    "title": ["Date", "Team", "Opponent", "Home / Away", "Result", "Home Score", "Visitor Score", "Overtime"],
}
_DROPPED_IDX = 4


def setup_guards() -> dict:
    """Same pattern as join_audit: socket guard, then src.analysis behind the inert src.ledger stand-in."""
    J.install_socket_guard()
    _, info = J.import_analysis()
    return info


# --------------------------------------------------------------------------- 1. schema adapters
def _adapt(raw: pd.DataFrame, season: int, family: str) -> pd.DataFrame:
    m = HEADERS[family]
    missing = [c for c in m.values() if c not in raw.columns]
    if missing:
        raise ValueError(f"season {season}: '{family}' header family is missing columns {missing}; "
                         f"frame has {list(raw.columns)[:12]}{'...' if raw.shape[1] > 12 else ''}")
    flag = raw[m["flag"]].astype(str).str.strip()
    bad = sorted(set(flag) - set(FLAGS))
    if bad:
        raise ValueError(f"season {season}: unknown values in '{m['flag']}': {bad} (expected only {list(FLAGS)})")
    ot = pd.to_numeric(raw[m["overtime"]], errors="coerce").fillna(0)
    out = pd.DataFrame({
        "season": season,
        "date": pd.to_datetime(raw[m["date"]].astype(str).str.strip(), format="%Y-%m-%d", errors="coerce"),
        "team": raw[m["team"]].astype(str).str.strip(),
        "opponent": raw[m["opponent"]].astype(str).str.strip(),
        "team_goals": pd.to_numeric(raw[m["team_goals"]], errors="coerce"),
        "opp_goals": pd.to_numeric(raw[m["opp_goals"]], errors="coerce"),
        "flag": flag,
        "overtime": (ot > 0).astype(int)})
    return out.reset_index(drop=True)[COMMON_COLUMNS]


def adapt_snake(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """2022, 2025, 2026 headers (date, team, opponent, home_away, home_score, visitor_score, overtime)."""
    return _adapt(raw, season, "snake")


def adapt_title(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """2023, 2024 headers (Date, Team, Opponent, Home / Away, Home Score, Visitor Score, Overtime)."""
    return _adapt(raw, season, "title")


ADAPTERS = {"snake": adapt_snake, "title": adapt_title}


def load_raw(season: int, data_dir: Path | None = None) -> pd.DataFrame:
    """Read a season file as strings. Verifies the first 8 headers; never loads the 5th (result) column."""
    fam = FAMILY[season]
    path = J.results_path(season, data_dir)
    header = list(pd.read_csv(path, nrows=0).columns)
    if header[:8] != CORE_HEADER[fam]:
        raise ValueError(f"season {season} ({path.name}): first 8 headers {header[:8]} != expected {CORE_HEADER[fam]}")
    drop = CORE_HEADER[fam][_DROPPED_IDX]
    return pd.read_csv(path, dtype=str, keep_default_na=False, usecols=lambda c: c != drop)


# --------------------------------------------------------------------------- 2. mascot strip (rule R2a)
def strip_mascots(teams, opponents) -> pd.DataFrame:
    """Strip the LONGEST same-season distinct opponent string that is a word-boundary prefix of the row-team
    name and leaves a non-empty remainder. Returns original, stripped, n_candidate_prefixes, chosen_prefix,
    all_candidates. Raises ValueError for a name with no candidate."""
    opp = sorted({str(o).strip() for o in opponents}, key=lambda s: (-len(s), s))
    names = sorted({str(x).strip() for x in teams})
    rows = []
    for t in names:
        cands = [o for o in opp if o and t.startswith(o + " ") and t[len(o) + 1:].strip()]
        if not cands:
            raise ValueError(f"no opponent-string prefix found for row-team name {t!r}")
        rows.append({"original": t, "stripped": cands[0], "n_candidate_prefixes": len(cands),
                     "chosen_prefix": cands[0], "all_candidates": " | ".join(cands)})
    m = pd.DataFrame(rows)
    assert len(m) == len(names) and m["original"].is_unique, "a name did not get exactly one result"
    assert m["stripped"].is_unique, f"stripped names collide: {m[m['stripped'].duplicated(keep=False)].to_dict('records')}"
    assert m["stripped"].nunique() == m["original"].nunique(), "stripping changed the number of distinct teams"
    return m


def load_season(season: int, data_dir: Path | None = None):
    """Returns (common_frame_with_canonical_team_names, mascot_map_or_None)."""
    common = ADAPTERS[FAMILY[season]](load_raw(season, data_dir), season)
    mmap = None
    if season in MASCOT_SEASONS:
        mmap = strip_mascots(common["team"], common["opponent"])
        common = common.assign(team=common["team"].map(dict(zip(mmap["original"], mmap["stripped"]))))
        assert common["team"].notna().all()
        mmap.insert(0, "season", season)
    return common, mmap


# --------------------------------------------------------------------------- 3-4. canonical games
GAME_COLUMNS = ["season", "date", "game_key", "team_lo", "team_hi", "goals_lo", "goals_hi", "ot", "flag_lo",
                "flag_hi", "sidedness", "n_source_rows"]
EXCL_COLUMNS = ["season", "kind", "ref", "date", "reason", "detail"]


def to_join_schema(common: pd.DataFrame) -> pd.DataFrame:
    """Rename the common frame in memory to the schema join_audit.prepare_results expects."""
    return pd.DataFrame({"date": common["date"].dt.strftime("%Y-%m-%d"), "team": common["team"],
                         "opponent": common["opponent"], "home_away": common["flag"],
                         "home_score": common["team_goals"], "visitor_score": common["opp_goals"],
                         "overtime": common["overtime"]})[J.RESULT_COLUMNS]


def build_games(common: pd.DataFrame, season: int):
    """join_audit.prepare_results -> clean_opponents -> build_canonical_games, all unchanged.
    Returns (games, exclusions, info)."""
    kept, exc = J.prepare_results(to_join_schema(common), season)     # includes the out-of-window rule
    opp = J.clean_opponents(kept)
    g, acct = J.build_canonical_games(opp, exc)
    excl = [{"season": season, "kind": "ROW", "ref": r.row_id, "date": r.date, "reason": r.exclude_reason,
             "detail": f"{r.row_team} vs {r.opp_raw}"} for r in exc.itertuples()]
    games = pd.DataFrame(columns=GAME_COLUMNS)
    if len(g):
        special = g["result_quality"].isin([J.MULTI_ROW, J.SCORE_CONFLICT])
        bad = g["winner"].eq("TIE") | special
        for r in g[bad].itertuples():
            reason = r.result_quality if r.result_quality in (J.MULTI_ROW, J.SCORE_CONFLICT) else "TIE"
            excl.append({"season": season, "kind": "GAME", "ref": r.game_key, "date": r.date, "reason": reason,
                         "detail": f"{r.team_a} {r.goals_a}-{r.goals_b} {r.team_b}"})
        k = g[~bad]
        games = pd.DataFrame({
            "season": k["season"], "date": k["date"],
            "game_key": k["date"] + "|" + k["team_a"] + "|" + k["team_b"],
            "team_lo": k["team_a"], "team_hi": k["team_b"], "goals_lo": k["goals_a"], "goals_hi": k["goals_b"],
            "ot": (k["ot_periods"] > 0).astype(int),
            "flag_lo": k["home_flag_a"].replace("missing", ""), "flag_hi": k["home_flag_b"].replace("missing", ""),
            "sidedness": k["two_sided"].map({True: "TWO_SIDED", False: "SINGLE_SIDED"}),
            "n_source_rows": k["n_rows"]})[GAME_COLUMNS]
        games = games.sort_values(["date", "game_key"]).reset_index(drop=True)
    info = {"acct": acct, "row_teams": set(common["team"]), "opp_names": set(opp["opp_name"]),
            "n_rows": len(common)}
    return games, pd.DataFrame(excl, columns=EXCL_COLUMNS), info


# --------------------------------------------------------------------------- 5-6. identity, diagnostics
def membership(infos: dict) -> pd.DataFrame:
    teams = sorted(set().union(*[i["row_teams"] | i["opp_names"] for i in infos.values()]))
    df = pd.DataFrame({"team": teams})
    for s, i in infos.items():
        df[f"rowteam_{s}"] = df["team"].isin(i["row_teams"])
    for s, i in infos.items():
        df[f"opp_only_{s}"] = df["team"].isin(i["opp_names"] - i["row_teams"])
    return df


def adjacent_differences(infos: dict) -> list[dict]:
    seasons = sorted(infos)
    out = []
    for a, b in zip(seasons, seasons[1:]):
        ra, rb = infos[a]["row_teams"], infos[b]["row_teams"]
        oob, ooa = infos[b]["opp_names"] - rb, infos[a]["opp_names"] - ra
        out.append({"a": a, "b": b,
                    "only_a": [(n, f"opp-only in {b}" if n in oob else f"absent in {b}") for n in sorted(ra - rb)],
                    "only_b": [(n, f"opp-only in {a}" if n in ooa else f"absent in {a}") for n in sorted(rb - ra)]})
    return out


def _md(headers, rows) -> str:
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
                     + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def build_report(games, excl, infos, mmaps, diffs) -> str:
    L = ["# Elo feasibility: results data layer report", "",
         "Generated by `elo_feas/data_layer.py`. Every number below is computed by code from the pinned files.",
         f"Results repo pin: `{J.PINNED_COMMIT}`. The 5th source column (outcome label) is never loaded.",
         "`join_audit.prepare_results`, `clean_opponents` and `build_canonical_games` were used UNCHANGED for every "
         "season (inputs renamed in memory to their schema). `ot` is 1 if any overtime period was recorded.", ""]
    rows = []
    for s in sorted(infos):
        g, e = games[games.season == s], excl[excl.season == s]
        rows.append([s, infos[s]["n_rows"], int((e.kind == "ROW").sum()), int((e.kind == "GAME").sum()), len(g),
                     int((g.sidedness == "TWO_SIDED").sum()), int((g.sidedness == "SINGLE_SIDED").sum())])
    L += ["## Games per season", "", _md(["season", "source rows", "row exclusions", "game exclusions (ties/conflict/multi)",
                                          "games", "two-sided", "single-sided"], rows), ""]
    rows = []
    for s in sorted(infos):
        g = games[games.season == s]
        n = pd.concat([g.team_lo, g.team_hi]).value_counts()
        own = n[n.index.isin(infos[s]["row_teams"])]
        rows.append([s, len(own), int(own.min()), float(own.median()), int(own.max())])
    L += ["## Games per team per season (teams with own rows)", "",
          _md(["season", "teams", "min", "median", "max"], rows), ""]
    rows = []
    for s in sorted(infos):
        t = games[(games.season == s) & (games.sidedness == "TWO_SIDED")]
        ha = ((t.flag_lo == "Home") & (t.flag_hi == "Away")) | ((t.flag_lo == "Away") & (t.flag_hi == "Home"))
        rows.append([s, len(t), int(((t.flag_lo == "Away") & (t.flag_hi == "Away")).sum()),
                     int(((t.flag_lo == "Home") & (t.flag_hi == "Home")).sum()), int(ha.sum())])
    L += ["## Venue flags in two-sided games", "",
          _md(["season", "two-sided", "both Away", "both Home", "Home/Away"], rows), ""]
    rows, detail = [], []
    for s in sorted(infos):
        g = games[games.season == s]
        ghost = sorted(infos[s]["opp_names"] - infos[s]["row_teams"])
        cnt = {n: int(((g.team_lo == n) | (g.team_hi == n)).sum()) for n in ghost}
        rows.append([s, len(ghost), sum(cnt.values())])
        detail += [[s, n, c] for n, c in cnt.items()]
    L += ["## Teams with no own rows", "", _md(["season", "teams", "games involving them"], rows), "",
          _md(["season", "team", "games"], detail) if detail else "(none)", ""]
    L += ["## Mascot strip (rule R2a)", ""]
    for s, m in mmaps.items():
        multi = m[m.n_candidate_prefixes > 1]
        L += [f"- {s}: {len(m)} row-team names stripped, {m.stripped.nunique()} distinct results, "
              f"{len(multi)} with more than one candidate prefix."]
        for r in multi.itertuples():
            L.append(f"    - {r.original!r}: candidates [{r.all_candidates}] -> chosen {r.chosen_prefix!r}")
    L += ["", "## Exclusions", ""]
    L += [_md(EXCL_COLUMNS, excl.values.tolist()) if len(excl) else "(none)", ""]
    L += ["## Row-team names present in one season and absent in the next", ""]
    for d in diffs:
        L += [f"### {d['a']} -> {d['b']}", "",
              f"- in {d['a']} not {d['b']}: " + (", ".join(f"{n} ({w})" for n, w in d["only_a"]) or "(none)"),
              f"- in {d['b']} not {d['a']}: " + (", ".join(f"{n} ({w})" for n, w in d["only_b"]) or "(none)"), ""]
    return "\n".join(L)


def build_all(seasons=SEASONS, data_dir: Path | None = None, write: bool = True, out_dir: Path = OUT_DIR) -> dict:
    setup_guards()
    if data_dir is None:
        J.verify_pin()
    all_games, all_excl, infos, mmaps = [], [], {}, {}
    for s in seasons:
        common, mmap = load_season(s, data_dir)
        g, e, info = build_games(common, s)
        all_games.append(g)
        all_excl.append(e)
        infos[s] = info
        if mmap is not None:
            mmaps[s] = mmap
    games = pd.concat(all_games, ignore_index=True).sort_values(["date", "game_key"]).reset_index(drop=True)
    excl = pd.concat(all_excl, ignore_index=True)
    mem, diffs = membership(infos), adjacent_differences(infos)
    report = build_report(games, excl, infos, mmaps, diffs)
    res = {"games": games, "exclusions": excl, "infos": infos, "mascot_maps": mmaps, "membership": mem,
           "diffs": diffs, "report": report}
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        games.to_csv(out_dir / "canonical_games_2022_2026.csv", index=False)
        excl.to_csv(out_dir / "exclusions.csv", index=False)
        mem.to_csv(out_dir / "team_season_membership.csv", index=False)
        if mmaps:
            pd.concat(mmaps.values())[["season", "original", "stripped", "n_candidate_prefixes", "chosen_prefix"]] \
                .to_csv(out_dir / "mascot_strip_map.csv", index=False)
        (out_dir / "data_layer_report.md").write_text(report, encoding="utf-8")
    return res


def main() -> int:
    r = build_all()
    print("games per season:", r["games"].groupby("season").size().to_dict())
    print("\nNAMES WITH MORE THAN ONE CANDIDATE PREFIX")
    for s, m in r["mascot_maps"].items():
        for x in m[m.n_candidate_prefixes > 1].itertuples():
            print(f"  {s}: {x.original!r}: [{x.all_candidates}] -> {x.chosen_prefix!r}")
    print("\nROW-TEAM NAMES PRESENT IN ONE SEASON, ABSENT IN THE NEXT")
    for d in r["diffs"]:
        print(f"  {d['a']} -> {d['b']}\n    in {d['a']} not {d['b']}: {d['only_a']}\n    in {d['b']} not {d['a']}: {d['only_b']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
