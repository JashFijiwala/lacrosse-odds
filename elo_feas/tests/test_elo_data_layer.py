"""Tests for elo_feas.data_layer. Real-file tests skip if the pinned results repo is absent. Every test
re-checks that the socket guard is active. Outputs are written only under tmp_path."""
import socket
import sys

import numpy as np
import pandas as pd
import pytest

sys.dont_write_bytecode = True

from audit_join import join_audit as J  # noqa: E402
from elo_feas import data_layer as D  # noqa: E402

HAVE_DATA = all(J.results_path(s).exists() for s in D.SEASONS)
real = pytest.mark.skipif(not HAVE_DATA, reason="pinned results files not present")


@pytest.fixture(autouse=True)
def socket_guard(monkeypatch):
    J.install_socket_guard(monkeypatch)
    for call in (lambda: socket.create_connection(("example.invalid", 80)),
                 lambda: socket.getaddrinfo("example.invalid", 80),
                 lambda: socket.socket().connect(("127.0.0.1", 9))):
        with pytest.raises(RuntimeError, match="network blocked"):
            call()
    yield


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return D.build_all(write=False)


def title_frame(rows):
    """rows: (date, team, opp, flag, team_goals, opp_goals)."""
    return pd.DataFrame({"Date": [r[0] for r in rows], "Team": [r[1] for r in rows], "Opponent": [r[2] for r in rows],
                         "Home / Away": [r[3] for r in rows], "Home Score": [r[4] for r in rows],
                         "Visitor Score": [r[5] for r in rows], "Overtime": ["" for _ in rows]})


# ----------------------------------------------------------------------------- T1
@real
def test_t1_expected_counts(built):
    g, ex = built["games"], built["exclusions"]
    exp = {2022: (543, 543, 0), 2023: (561, 546, 15), 2024: (570, 548, 22), 2025: (409, 367, 42), 2026: (479, 479, 0)}
    for s, (n, two, one) in exp.items():
        gs = g[g.season == s]
        assert len(gs) == n, s
        assert (gs.sidedness == "TWO_SIDED").sum() == two, s
        assert (gs.sidedness == "SINGLE_SIDED").sum() == one, s
    assert not (ex.reason == "TIE").any()
    assert not g.game_key.duplicated().any()
    assert (ex[ex.season == 2025].reason == "DATE_OUTSIDE_SEASON").sum() == 2     # the two 2022-03-26 rows


# ----------------------------------------------------------------------------- T2
@real
@pytest.mark.parametrize("season", [2025, 2026])
def test_t2_matches_join_audit(built, season):
    kept, exc = J.load_results(season)
    old, _ = J.build_canonical_games(J.clean_opponents(kept), exc)
    new = built["games"][built["games"].season == season]
    assert len(new) == len(old)
    a = {(r.date, r.team_a, r.team_b): (r.goals_a, r.goals_b) for r in old.itertuples()}
    b = {(r.date, r.team_lo, r.team_hi): (r.goals_lo, r.goals_hi) for r in new.itertuples()}
    assert a == b


# ----------------------------------------------------------------------------- T3
def test_t3_longest_prefix():
    opp = ["Penn", "Penn St.", "Penn State", "Penn Charlotte, NC", "Villanova", "Pennant"]
    teams = ["Penn Quakers", "Penn St. Nittany Lions", "Penn State Nittany Lions", "Villanova Wildcats"]
    m = D.strip_mascots(teams, opp).set_index("original")
    assert m.loc["Penn Quakers", "stripped"] == "Penn"
    assert m.loc["Penn St. Nittany Lions", "stripped"] == "Penn St."
    assert m.loc["Penn St. Nittany Lions", "n_candidate_prefixes"] == 2
    assert m.loc["Penn State Nittany Lions", "stripped"] == "Penn State"
    assert m.loc["Penn State Nittany Lions", "n_candidate_prefixes"] == 2
    assert m.loc["Penn Quakers", "n_candidate_prefixes"] == 1
    assert m["stripped"].is_unique


def test_t3_word_boundary_and_failures():
    with pytest.raises(ValueError, match="Pennsylvania Quakers"):      # "Penn" is not a word-boundary prefix
        D.strip_mascots(["Pennsylvania Quakers"], ["Penn"])
    with pytest.raises(ValueError, match="Penn"):                       # exact equal: no mascot remainder
        D.strip_mascots(["Penn"], ["Penn"])
    with pytest.raises(AssertionError, match="collide"):               # two names strip to the same team
        D.strip_mascots(["Penn Quakers", "Penn Lions"], ["Penn"])


# ----------------------------------------------------------------------------- T4
def test_t4_adapter_fails_loudly():
    ok = title_frame([("2024-02-03", "A", "B", "Home", 5, 4)])
    out = D.adapt_title(ok, 2024)
    assert list(out.columns) == D.COMMON_COLUMNS and out.loc[0, "team_goals"] == 5
    with pytest.raises(ValueError, match="Visitor Score"):
        D.adapt_title(ok.rename(columns={"Visitor Score": "Visitor Pts"}), 2024)
    with pytest.raises(ValueError, match=r"Home / Away"):
        D.adapt_title(ok.drop(columns=["Home / Away"]), 2024)
    with pytest.raises(ValueError, match="date"):                      # wrong family for the frame
        D.adapt_snake(ok, 2024)
    with pytest.raises(ValueError, match="Neutral"):
        D.adapt_title(ok.assign(**{"Home / Away": "Neutral"}), 2024)


# ----------------------------------------------------------------------------- T5
def test_t5_outcome_column_never_carried():
    f = title_frame([("2024-02-03", "A", "B", "Home", 5, 4)]).assign(Result="W")
    assert not any("result" in c.lower() for c in D.adapt_title(f, 2024).columns)


@real
def test_t5_real_pipeline_has_no_outcome_column(tmp_path):
    for s in D.SEASONS:
        assert not any(c.lower() == "result" for c in D.load_raw(s).columns)
    r = D.build_all(write=True, out_dir=tmp_path)
    frames = [r["games"], r["exclusions"], r["membership"], *r["mascot_maps"].values()]
    for df in frames:
        assert not any(c.lower() == "result" for c in df.columns)
    for p in tmp_path.glob("*.csv"):
        header = p.read_text(encoding="utf-8").splitlines()[0].lower().split(",")
        assert "result" not in header, p.name


# ----------------------------------------------------------------------------- T6
@real
@pytest.mark.parametrize("season", [2023, 2025])
def test_t6_orientation_free(season):
    common, _ = D.load_season(season)
    base, bex, _ = D.build_games(common, season)
    shuffled = common.sample(frac=1.0, random_state=7).reset_index(drop=True)
    flipped = common.iloc[::-1].reset_index(drop=True)
    for variant in (shuffled, flipped):
        g, ex, _ = D.build_games(variant, season)
        pd.testing.assert_frame_equal(base.sort_values(["date", "game_key"]).reset_index(drop=True),
                                      g.sort_values(["date", "game_key"]).reset_index(drop=True))
        assert sorted(bex.reason) == sorted(ex.reason)


def test_t6_synthetic_flip_of_each_pair():
    rows = [("2024-02-03", "A", "B", "Home", 5, 4), ("2024-02-03", "B", "A", "Away", 4, 5),
            ("2024-02-10", "A", "C", "Away", 3, 8)]
    base, _, _ = D.build_games(D.adapt_title(title_frame(rows), 2024), 2024)
    alt = [rows[1], rows[0], rows[2]]
    g, _, _ = D.build_games(D.adapt_title(title_frame(alt), 2024), 2024)
    pd.testing.assert_frame_equal(base, g)
    assert list(base.game_key) == ["2024-02-03|A|B", "2024-02-10|A|C"]
    assert list(base.sidedness) == ["TWO_SIDED", "SINGLE_SIDED"]
    assert base.loc[1, "flag_lo"] == "Away" and base.loc[1, "flag_hi"] == ""


# ----------------------------------------------------------------------------- T7
@real
def test_t7_key_sanity(built):
    g = built["games"]
    assert set(g.n_source_rows) <= {1, 2}
    assert (g.team_lo < g.team_hi).all()
    assert (g.game_key == g.date + "|" + g.team_lo + "|" + g.team_hi).all()
    both = pd.concat([g[["date", "team_lo"]].rename(columns={"team_lo": "t"}),
                      g[["date", "team_hi"]].rename(columns={"team_hi": "t"})])
    assert not both.duplicated().any()
    assert g.equals(g.sort_values(["date", "game_key"]).reset_index(drop=True))
    assert g[["goals_lo", "goals_hi"]].notna().all().all() and (g.goals_lo != g.goals_hi).all()


@real
def test_mascot_assertions_hold_on_real_seasons(built):
    for s in D.MASCOT_SEASONS:
        m = built["mascot_maps"][s]
        assert m.stripped.is_unique and m.stripped.nunique() == m.original.nunique()
        row = m[m.original == "Penn St. Nittany Lions"].iloc[0]
        assert row.stripped == "Penn St." and row.n_candidate_prefixes == 2
