"""Tests for audit_join.join_audit.

Synthetic data only, except: the golden and coverage tests (real files; skipped if absent) and the
protected-file hash test. Every test installs the socket guard. Nothing is written outside tmp_path.
"""
import builtins
import hashlib
import socket
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.dont_write_bytecode = True

from audit_join import join_audit as J  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REAL_2025 = J.results_path(2025)
REAL_2026 = J.results_path(2026)
RAW_DIR = ROOT / "pilot" / "raw"
LEDGER = ROOT / "pilot" / "ledger" / "credit_ledger.csv"


@pytest.fixture(autouse=True)
def socket_guard(monkeypatch):
    J.install_socket_guard(monkeypatch)
    for call in (lambda: socket.create_connection(("example.invalid", 80)),
                 lambda: socket.getaddrinfo("example.invalid", 80),
                 lambda: socket.socket().connect(("127.0.0.1", 9))):
        with pytest.raises(RuntimeError, match="network blocked"):
            call()
    yield


# ----------------------------------------------------------------------------- helpers
NA = "NA"


def results_df(rows, extra=None):
    df = pd.DataFrame([dict(zip(J.RESULT_COLUMNS, map(str, r))) for r in rows])
    for k, v in (extra or {}).items():
        df[k] = v
    return df


def build(rows, season=2025, extra=None):
    kept, excl = J.prepare_results(results_df(rows, extra), season)
    kept = J.clean_opponents(kept)
    games, acct = J.build_canonical_games(kept, excl)
    return games, acct, kept, excl


def event(home, away, *sightings, eid="e1"):
    s = list(sightings)
    return {"event_id": eid, "season": int(s[-1][:4]), "home_team": home, "away_team": away,
            "commence_sightings": s, "commence_latest": s[-1], "commence_conflict": len(set(s)) > 1,
            "n_books_h2h_any": 3, "n_books_h2h_at_latest_pregame": 3, "n_pregame_snapshots_with_h2h": 1,
            "fav_context": ""}


def match(events, games, kept, aliases=(), results_names=None):
    names = J.results_canonical_names(kept)
    res = J.resolve_names([e["home_team"] for e in events] + [e["away_team"] for e in events], names, list(aliases))
    return J.match_events(events, res, games, names if results_names is None else results_names)


def one(games):
    assert len(games) == 1
    return games.iloc[0]


# ----------------------------------------------------------------------------- 1
def test_01_reciprocal_rows_one_game_goals_margin_winner():
    # a result column with wrong values is present and must be ignored
    g = one(build([("2025-03-01", "Zeta", "Alpha", "Home", 10, 7, NA),
                   ("2025-03-01", "Alpha", "Zeta", "Away", 7, 10, NA)], extra={"result": "W"})[0])
    assert (g.team_a, g.team_b, g.goals_a, g.goals_b) == ("Alpha", "Zeta", 7, 10)
    assert g.margin_a_minus_b == -3 and g.winner == "b" and g.winner_team == "Zeta"
    assert g.result_quality == J.TWO_SIDED_AGREE and g.n_rows == 2

    g = one(build([("2025-03-02", "Alpha", "Zeta", "Home", 12, 4, 1),
                   ("2025-03-02", "Zeta", "Alpha", "Away", 4, 12, 1)], extra={"result": "L"})[0])
    assert (g.goals_a, g.goals_b, g.margin_a_minus_b, g.winner, g.ot_periods) == (12, 4, 8, "a", 1)


# ----------------------------------------------------------------------------- 2
def test_02_home_score_is_row_team_goals_even_when_away():
    g = one(build([("2025-03-01", "Alpha", "Zeta", "Away", 15, 3, NA)])[0])
    assert (g.goals_a, g.goals_b, g.winner) == (15, 3, "a")
    g = one(build([("2025-03-01", "Zeta", "Alpha", "Away", 9, 11, NA)])[0])   # only b's row exists
    assert (g.team_a, g.goals_a, g.goals_b, g.winner) == ("Alpha", 11, 9, "a")


# ----------------------------------------------------------------------------- 3
def test_03_single_sided_and_accounting_identity():
    rows = [("2025-03-01", "Alpha", "Beta", "Home", 9, 8, NA),        # two-sided
            ("2025-03-01", "Beta", "Alpha", "Away", 8, 9, NA),
            ("2025-03-02", "Alpha", "Iona", "Home", 14, 6, NA),       # single-sided, opponent-only
            ("2025-03-08", "Alpha", "Beta", "Home", 5, 4, NA),        # multi-row key (3 rows)
            ("2025-03-08", "Beta", "Alpha", "Away", 4, 5, NA),
            ("2025-03-08", "Alpha", "Beta", "Home", 5, 4, NA),
            ("2022-03-26", "Beta", "Alpha", "Home", 3, 2, NA)]        # excluded
    games, acct, kept, excl = build(rows)
    assert acct["rows_total"] == 7
    assert (acct["rows_excluded"], acct["two_sided_games"], acct["single_sided_games"], acct["multi_row_rows"]) == (1, 1, 1, 3)
    assert acct["rows_total"] == acct["rows_excluded"] + 2 * acct["two_sided_games"] + acct["single_sided_games"] + acct["multi_row_rows"]
    single = games[games.result_quality == J.SINGLE_SIDED].iloc[0]
    assert single.team_b == "Iona" and bool(single.opp_only_flag) and single.venue_class == "SINGLE_A_HOME"
    used = [rid for ids in games.row_ids for rid in ids.split("; ")]
    assert len(used) == len(set(used)) == len(kept)                # no row feeds two games


# ----------------------------------------------------------------------------- 4
def test_04_score_conflict_and_multi_row_not_matchable():
    g = one(build([("2025-03-01", "Alpha", "Zeta", "Home", 10, 7, NA),
                   ("2025-03-01", "Zeta", "Alpha", "Away", 8, 10, NA)])[0])
    assert g.result_quality == J.SCORE_CONFLICT
    games, _, kept, _ = build([("2025-03-08", "Alpha", "Beta", "Home", 5, 4, NA),
                               ("2025-03-08", "Beta", "Alpha", "Away", 4, 5, NA),
                               ("2025-03-08", "Alpha", "Beta", "Home", 5, 4, NA)])
    assert one(games).result_quality == J.MULTI_ROW
    link = match([event("Alpha", "Beta", "2025-03-08T17:00:00Z")], games, kept).iloc[0]
    assert (link.status, link.reason, link.matched_game_key) == ("AMBIGUOUS", "MULTI_ROW_ONLY", "")
    assert link.candidate_game_keys == "2025|2025-03-08|Alpha|Beta"


# ----------------------------------------------------------------------------- 5
def test_05_venue_class():
    rows = [("2025-03-01", "Alpha", "Beta", "Home", 1, 0, NA), ("2025-03-01", "Beta", "Alpha", "Away", 0, 1, NA),
            ("2025-03-02", "Alpha", "Beta", "Away", 1, 0, NA), ("2025-03-02", "Beta", "Alpha", "Home", 0, 1, NA),
            ("2025-03-03", "Alpha", "Beta", "Away", 1, 0, NA), ("2025-03-03", "Beta", "Alpha", "Away", 0, 1, NA),
            ("2025-03-04", "Alpha", "Beta", "Home", 1, 0, NA), ("2025-03-04", "Beta", "Alpha", "Home", 0, 1, NA)]
    vc = dict(zip(build(rows)[0].date, build(rows)[0].venue_class))
    assert vc == {"2025-03-01": "A_HOME", "2025-03-02": "B_HOME", "2025-03-03": "BOTH_AWAY", "2025-03-04": "BOTH_HOME"}


# ----------------------------------------------------------------------------- 6
def test_06_out_of_season_row_excluded_and_never_used():
    games, acct, kept, excl = build([("2022-03-26", "Alpha", "Beta", "Home", 3, 2, NA),
                                     ("2025-03-01", "Alpha", "Beta", "Home", 9, 8, NA),
                                     ("2025-03-01", "Beta", "Alpha", "Away", 8, 9, NA)])
    assert list(excl.exclude_reason) == ["DATE_OUTSIDE_SEASON"] and acct["rows_excluded"] == 1
    assert "2022-03-26" not in set(games.date) and len(kept) == 2
    assert excl.row_id.iloc[0] not in "; ".join(games.row_ids)


# ----------------------------------------------------------------------------- 7
def test_07_opponent_cleaning():
    rows = [("2025-03-01", "Duke", "Air Force Chicago, IL", "Away", 10, 9, NA),
            ("2025-03-01", "Air Force", "Duke Chicago, IL", "Away", 9, 10, NA),
            ("2025-02-22", "Duke", "Hobart", "Home", 15, 5, NA),
            ("2025-03-05", "Duke", "Hobart Ridgefield HS", "Away", 12, 7, NA),
            ("2025-03-06", "Penn", "Penn St. Philadelphia, PA", "Home", 8, 7, NA),
            ("2025-03-06", "Penn St.", "Penn", "Away", 7, 8, NA)]
    games, _, kept, _ = build(rows)
    k = kept.set_index("opp_raw")
    assert k.loc["Air Force Chicago, IL", "opp_name"] == "Air Force"
    assert k.loc["Air Force Chicago, IL", "opp_location_text"] == "Chicago, IL"
    assert bool(k.loc["Air Force Chicago, IL", "had_location_text"]) and not bool(k.loc["Air Force Chicago, IL", "opp_only"])
    assert k.loc["Hobart Ridgefield HS", "opp_name"] == "Hobart" and bool(k.loc["Hobart Ridgefield HS", "opp_only"])
    assert k.loc["Hobart", "opp_name"] == "Hobart" and bool(k.loc["Hobart", "opp_only"])
    assert k.loc["Penn St. Philadelphia, PA", "opp_name"] == "Penn St."           # longest known name wins
    af = games[games.date == "2025-03-01"].iloc[0]
    assert af.result_quality == J.TWO_SIDED_AGREE and bool(af.had_location_text_flag) and af.venue_class == "BOTH_AWAY"


# ----------------------------------------------------------------------------- 8
def test_08_name_resolution():
    aliases = J.load_aliases()
    cands = {"Penn", "Penn St.", "Boston U.", "Air Force", "Ohio St.", "UAlbany", "Duke"}
    res = J.resolve_names(["Pennsylvania Quakers", "Penn State Nittany Lions", "Boston Univ. Terriers",
                           "Ohio State Buckeyes", "Albany Great Danes", "Air Forse", "Duke Blue Devils"], cands, aliases)
    assert (res["Pennsylvania Quakers"]["results_name"], res["Pennsylvania Quakers"]["method"]) == ("Penn", "ALIAS")
    ps = res["Penn State Nittany Lions"]
    assert (ps["results_name"], ps["method"], ps["status"]) == ("Penn St.", "PREFIX", "RESOLVED")
    assert ps["collision_flag"] and set(ps["collision_candidates"].split("; ")) == {"Penn", "Penn St."}
    log = J.collision_log(res)
    row = log[log.odds_name == "Penn State Nittany Lions"].iloc[0]
    assert set(row.candidates.split("; ")) == {"Penn", "Penn St."} and row.chosen == "Penn St."
    assert (res["Boston Univ. Terriers"]["results_name"], res["Boston Univ. Terriers"]["method"]) == ("Boston U.", "ALIAS")
    assert res["Ohio State Buckeyes"]["results_name"] == "Ohio St."
    assert res["Albany Great Danes"]["results_name"] == "UAlbany"
    un = res["Air Forse"]
    assert un["status"] == "UNRESOLVED" and un["results_name"] == "" and "Air Force" in un["suggestion_only"]
    tie = J.resolve_names(["Boston U"], {"Boston U.", "Boston U"}, aliases)["Boston U"]
    assert tie["status"] == "AMBIGUOUS_NAME" and set(tie["tied_candidates"].split("; ")) == {"Boston U.", "Boston U"}

    # the matcher never uses a suggestion: the Air Force game exists but the event fails on the name
    games, _, kept, _ = build([("2025-03-01", "Air Force", "Duke", "Home", 9, 8, NA),
                               ("2025-03-01", "Duke", "Air Force", "Away", 8, 9, NA)])
    link = match([event("Air Forse", "Duke Blue Devils", "2025-03-01T19:00:00Z")], games, kept, aliases).iloc[0]
    assert (link.status, link.reason, link.matched_game_key) == ("FAILED", "NAME_UNRESOLVED", "")
    names = J.results_canonical_names(kept) | {"Air Force "}                       # creates a normalized tie
    r2 = J.resolve_names(["Air Force", "Duke"], names, aliases)
    l2 = J.match_events([event("Air Force", "Duke", "2025-03-01T19:00:00Z")], r2, games, names).iloc[0]
    assert (l2.status, l2.reason) == ("AMBIGUOUS", "NAME")


# ----------------------------------------------------------------------------- 9
def _pair_games(*dates):
    rows = []
    for d in dates:
        rows += [(d, "Alpha", "Beta", "Home", 5, 4, NA), (d, "Beta", "Alpha", "Away", 4, 5, NA)]
    return build(rows, season=int(dates[0][:4]))


def test_09_date_logic():
    g, _, k, _ = _pair_games("2025-03-01")
    l = match([event("Alpha", "Beta", "2025-03-01T19:00:00Z")], g, k).iloc[0]
    assert (l.status, l.matched_via, l.matched_date) == ("UNIQUE_MATCH", "utc_date", "2025-03-01")
    g, _, k, _ = _pair_games("2025-02-28")
    l = match([event("Alpha", "Beta", "2025-03-01T01:00:00Z")], g, k).iloc[0]
    assert (l.status, l.matched_via, l.matched_date) == ("DATE_SHIFTED", "previous_day", "2025-02-28")
    assert l.ny_date_latest == "2025-02-28"
    g, _, k, _ = _pair_games("2025-02-28", "2025-03-01")
    l = match([event("Alpha", "Beta", "2025-03-01T19:00:00Z")], g, k).iloc[0]
    assert (l.status, l.reason) == ("AMBIGUOUS", "MULTIPLE_CANDIDATE_GAMES") and l.candidate_game_keys.count(";") == 1
    g, _, k, _ = _pair_games("2025-02-20")
    l = match([event("Alpha", "Beta", "2025-03-01T19:00:00Z")], g, k).iloc[0]
    assert (l.status, l.reason) == ("FAILED", "NO_GAME_FOR_PAIR")
    g, _, k, _ = _pair_games("2025-03-02")                                        # the day AFTER
    l = match([event("Alpha", "Beta", "2025-03-01T23:00:00Z")], g, k).iloc[0]
    assert (l.status, l.reason) == ("FAILED", "NO_GAME_FOR_PAIR")
    assert l.candidate_dates == "2025-02-28; 2025-03-01"
    g, _, k, _ = _pair_games("2025-03-01")                                        # no game on 04-01; Beta unknown
    l = match([event("Alpha", "Beta", "2025-04-01T19:00:00Z")], g, k, results_names={"Alpha"}).iloc[0]
    assert (l.status, l.reason) == ("FAILED", "TEAM_NOT_IN_RESULTS")
    l = match([event("Alpha", "Beta", "2025-04-01T19:00:00Z")], g, k).iloc[0]
    assert (l.status, l.reason) == ("FAILED", "NO_GAME_FOR_PAIR")


# ----------------------------------------------------------------------------- 10
def test_10_commence_conflict_union_and_status_change():
    g, _, k, _ = _pair_games("2026-03-28")
    l = match([event("Alpha", "Beta", "2026-03-28T16:00:00Z", "2026-03-28T16:02:00Z")], g, k).iloc[0]
    assert l.commence_conflict and l.status == "UNIQUE_MATCH" and not l.commence_conflict_changes_status
    assert l.candidate_dates == "2026-03-27; 2026-03-28"
    l = match([event("Alpha", "Beta", "2026-03-28T23:58:00Z", "2026-03-29T00:02:00Z")], g, k).iloc[0]
    assert l.candidate_dates == "2026-03-27; 2026-03-28; 2026-03-29"
    assert (l.status, l.matched_via) == ("UNIQUE_MATCH", "utc_date")                # union of candidate dates
    assert (l.status_first_sighting_only, l.status_latest_sighting_only) == ("UNIQUE_MATCH", "DATE_SHIFTED")
    assert l.commence_conflict_changes_status


# ----------------------------------------------------------------------------- 11
def test_11_orientation_is_not_a_matching_input():
    g, _, k, _ = _pair_games("2025-03-01")                                          # Alpha Home, Beta Away
    a = match([event("Alpha", "Beta", "2025-03-01T19:00:00Z")], g, k).iloc[0]
    b = match([event("Beta", "Alpha", "2025-03-01T19:00:00Z")], g, k).iloc[0]
    assert a.status == b.status == "UNIQUE_MATCH" and a.matched_game_key == b.matched_game_key
    assert (a.orientation, b.orientation) == ("AGREES", "DISAGREES")
    assert (a.home_goals, a.away_goals, b.home_goals, b.away_goals) == (5, 4, 4, 5)
    g1, _, k1, _ = build([("2025-03-01", "Alpha", "Iona", "Home", 5, 4, NA)])
    for h, w in (("Alpha", "Iona"), ("Iona", "Alpha")):
        l = match([event(h, w, "2025-03-01T19:00:00Z")], g1, k1).iloc[0]
        assert l.status == "UNIQUE_MATCH" and l.orientation == "SINGLE_SIDED_NOT_CHECKABLE"


# ----------------------------------------------------------------------------- 12
def test_12_independently_verified_applied_after_matching(monkeypatch, tmp_path):
    g, _, k, _ = _pair_games("2025-03-01", "2025-03-08")
    evs = [event("Alpha", "Beta", "2025-03-01T19:00:00Z", eid="e1"),
           event("Alpha", "Beta", "2025-03-08T19:00:00Z", eid="e2")]
    names = J.results_canonical_names(k)
    res = J.resolve_names(["Alpha", "Beta"], names, [])

    def no_open(*a, **kw):
        raise AssertionError("matching must not read any file")
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", no_open)
        links = J.match_events(evs, res, g, names)
    assert set(links.independently_verified) == {"no"}

    manual = tmp_path / "manual.csv"
    manual.write_text("home_team_substring,away_team_substring,results_date,note\n"
                      "Alpha,Beta,2025-03-08,test\nGamma,Delta,2025-03-08,no such link\n", encoding="utf-8")
    out, unmatched = J.apply_manual_verification(links, manual)
    assert list(out.independently_verified) == ["no", "yes"] and len(unmatched) == 1
    pd.testing.assert_frame_equal(out.drop(columns="independently_verified"),
                                  links.drop(columns="independently_verified"))
    assert J.apply_manual_verification(links, tmp_path / "absent.csv")[0].independently_verified.eq("no").all()


# ----------------------------------------------------------------------------- 13, 14 (real data)
def _real_available():
    return REAL_2025.exists() and REAL_2026.exists() and RAW_DIR.exists() and LEDGER.exists()


@pytest.fixture(scope="module")
def real():
    if not _real_available():
        pytest.skip("external\\NCAALacrosseData or pilot raw/ledger files are absent; real-data test skipped")
    J.verify_pin()
    A, info = J.import_analysis()
    inv, snaps = J.build_event_inventory(A, ROOT)
    kept_all, games_all = [], []
    for y in J.SEASONS:
        kept, excl = J.load_results(y)
        kept = J.clean_opponents(kept)
        games, _ = J.build_canonical_games(kept, excl)
        kept_all.append(kept)
        games_all.append(games)
    kept = pd.concat(kept_all, ignore_index=True)
    return {"info": info, "inv": inv, "kept": kept, "games": pd.concat(games_all, ignore_index=True)}


def test_13_golden_four_known_events(real):
    assert real["info"]["new_forbidden_modules"] == []
    picks = [("Air Force", "Quinnipiac"), ("Denver", "Marist"), ("Syracuse", "Duke"), ("Navy", "Colgate")]
    evs = [e for h, a in picks for e in real["inv"] if h in e["home_team"] and a in e["away_team"]]
    assert len(evs) == 4
    names = J.results_canonical_names(real["kept"])
    res = J.resolve_names([e["home_team"] for e in evs] + [e["away_team"] for e in evs], names, J.load_aliases())
    links = J.match_events(evs, res, real["games"], names)
    got = [(l.status, l.matched_date, l.orientation, int(l.home_goals), int(l.away_goals), int(l.ot_periods))
           for l in links.itertuples()]
    assert got == [("UNIQUE_MATCH", "2025-02-28", "AGREES", 21, 8, 0),
                   ("DATE_SHIFTED", "2025-02-28", "AGREES", 18, 5, 0),
                   ("UNIQUE_MATCH", "2026-03-28", "AGREES", 16, 15, 0),
                   ("UNIQUE_MATCH", "2026-03-28", "AGREES", 15, 16, 2)]     # Navy 15, Colgate 16, 2 OT
    assert links.iloc[1].commence_latest.startswith("2025-03-01")
    assert set(links.independently_verified) == {"no"}


def test_14_coverage_reproduction(real):
    inv = real["inv"]
    n = [e["n_books_h2h_any"] for e in inv]
    assert len(inv) == 139
    assert (sum(x >= 1 for x in n), sum(x >= 2 for x in n), sum(x == 3 for x in n)) == (131, 127, 105)


# ----------------------------------------------------------------------------- 15
def test_15_protected_files_unchanged():
    lines = (ROOT / "audit_join" / "protected_hashes.txt").read_text(encoding="utf-8").split("\n")
    expected = dict(reversed(l.split("  ", 1)) for l in lines if l.strip())
    assert len(expected) == 10
    for rel, digest in expected.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel
