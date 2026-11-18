"""Tests for elo_feas.elo, elo_feas.metrics and elo_feas.audit_elo.

Engine tests use a synthetic league only, so no real outcome is looked at before Chunk 4. The only real-data
test (E6 loader check) builds the 2022-2024 game table and runs no Elo. Every test re-checks the socket
guard. Files are written only under tmp_path."""
import json
import math
import socket
import sys

import numpy as np
import pandas as pd
import pytest

sys.dont_write_bytecode = True

from audit_join import join_audit as J  # noqa: E402
from elo_feas import audit_elo as A  # noqa: E402
from elo_feas import data_layer as D  # noqa: E402
from elo_feas import elo as E  # noqa: E402
from elo_feas import metrics as M  # noqa: E402


@pytest.fixture(autouse=True)
def socket_guard(monkeypatch):
    J.install_socket_guard(monkeypatch)
    for call in (lambda: socket.create_connection(("example.invalid", 80)),
                 lambda: socket.getaddrinfo("example.invalid", 80),
                 lambda: socket.socket().connect(("127.0.0.1", 9))):
        with pytest.raises(RuntimeError, match="network blocked"):
            call()
    yield


# ----------------------------------------------------------------------------- synthetic league
def make_league(seasons=(2022, 2023, 2024), n_teams=16, n_dates=10, seed=0) -> pd.DataFrame:
    """Canonical-format games. T03 sits out the 2nd season; NEW1/NEW2 join mid 2nd season; GHOST appears once
    (single-sided) in the 1st season. Flags: Home/Away, Away/Home, Away/Away and single-sided."""
    rng = np.random.default_rng(seed)
    base = [f"T{i:02d}" for i in range(n_teams)]
    strength = {t: rng.normal(0, 1) for t in base + ["NEW1", "NEW2", "GHOST"]}
    rows = []
    for si, s in enumerate(seasons):
        teams = [t for t in base if not (si == 1 and t == "T03")]
        start = pd.Timestamp(f"{s}-02-01")
        for di in range(n_dates):
            d = (start + pd.Timedelta(days=7 * di)).strftime("%Y-%m-%d")
            today = list(teams) + (["NEW1"] if si >= 1 and di >= 4 else []) + (["NEW2"] if si >= 1 and di >= 6 else []) \
                + (["GHOST"] if si == 0 and di == 5 else [])
            rng.shuffle(today)
            if len(today) % 2:                               # one regular team sits out, never a special one
                today.remove([t for t in today if t.startswith("T")][-1])
            for a, b in zip(today[::2], today[1::2]):
                lo, hi = sorted((a, b))
                lo_wins = rng.random() < 1 / (1 + math.exp(-(strength[lo] - strength[hi])))
                gl, gh = (12, 8) if lo_wins else (7, 10)
                kind = "SINGLE" if "GHOST" in (lo, hi) else rng.choice(["HA", "AH", "AA", "SINGLE"], p=[.4, .4, .1, .1])
                fl, fh = {"HA": ("Home", "Away"), "AH": ("Away", "Home"), "AA": ("Away", "Away"),
                          "SINGLE": ("Home", "")}[kind]
                rows.append({"season": s, "date": d, "game_key": f"{d}|{lo}|{hi}", "team_lo": lo, "team_hi": hi,
                             "goals_lo": gl, "goals_hi": gh, "ot": 0, "flag_lo": fl, "flag_hi": fh,
                             "sidedness": "SINGLE_SIDED" if kind == "SINGLE" else "TWO_SIDED",
                             "n_source_rows": 1 if kind == "SINGLE" else 2})
    return pd.DataFrame(rows)[D.GAME_COLUMNS]


@pytest.fixture(scope="module")
def league():
    return make_league()


@pytest.fixture(scope="module")
def eng(league):
    return E.from_canonical(league)


# ----------------------------------------------------------------------------- E1
def test_e1_hand_examples():
    e = E.expected(1550, 1500)
    assert round(e, 3) == 0.571 and round(e, 4) == 0.5715
    ra, rb = E.update(1550, 1500, 1, 20, e)
    assert round(ra, 1) == 1558.6 and round(rb, 1) == 1491.4
    assert E.shrink(1600, 0.7) == pytest.approx(1570, abs=1e-12)
    assert round(M.brier([0.643], [1]), 4) == 0.1274
    assert round(M.log_loss([0.643], [1]), 4) == 0.4416
    assert M.brier([0.5], [1]) == 0.25 and round(M.log_loss([0.5], [0]), 4) == 0.6931
    assert E.venue_sign("Home", "Away") == 1 and E.venue_sign("Away", "Home") == -1
    assert E.venue_sign("Away", "Away") == 0 and E.venue_sign("Home", "") == 0
    assert E.expected(1500, 1500, h=50, v=1) > 0.5 > E.expected(1500, 1500, h=50, v=-1)


def test_e1_clipping_and_ties_rejected(league):
    assert M.log_loss([0.0], [1]) == pytest.approx(-math.log(1e-6))
    tied = league.copy()
    tied.loc[0, "goals_hi"] = tied.loc[0, "goals_lo"]
    with pytest.raises(AssertionError, match="ties"):
        E.from_canonical(tied)
    bad = E.from_canonical(league).astype({"y_a": float})
    bad.loc[0, "y_a"] = 0.5
    with pytest.raises(AssertionError, match="y_a"):
        E.run_elo(bad, 20, 0.7)


# ----------------------------------------------------------------------------- E2
@pytest.mark.parametrize("h", [0.0, 50.0])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_e2_orientation_invariance(eng, h, seed):
    base = E.run_elo(eng, 20, 0.7, h)
    alt_eng, flipped = A.reorient(eng, seed)
    assert 0 < flipped.sum() < len(alt_eng)
    alt = E.run_elo(alt_eng, 20, 0.7, h)
    assert set(base.ratings) == set(alt.ratings)
    assert max(abs(base.ratings[t] - alt.ratings[t]) for t in base.ratings) <= 1e-12
    a = base.predictions.set_index("game_key")["p_a"]
    b = alt.predictions.set_index("game_key")["p_a"]
    fl = set(alt_eng.loc[flipped, "game_key"])
    for key, p in a.items():
        assert abs(b[key] - ((1 - p) if key in fl else p)) <= 1e-12
    assert A.audit_orientation(eng, 20, 0.7, h, seed)["pass"]


def test_e2_home_sign_matters(eng):
    """With h > 0 the v sign must change predictions; with h = 0 it must not."""
    alt = eng.copy()
    alt["v"] = -alt["v"]
    p0, q0 = E.run_elo(eng, 20, 0.7, 0).predictions.p_a, E.run_elo(alt, 20, 0.7, 0).predictions.p_a
    p50, q50 = E.run_elo(eng, 20, 0.7, 50).predictions.p_a, E.run_elo(alt, 20, 0.7, 50).predictions.p_a
    assert (p0 == q0).all() and not (p50 == q50).all()


# ----------------------------------------------------------------------------- E3
def test_e3a_prepost_ratings_equal_from_scratch(eng):
    dates = sorted(eng["date"].unique())
    chosen = [dates[3], dates[10], dates[20]]                 # dates[10] and [20] open seasons 2 and 3
    assert dates[10] == "2023-02-01" and dates[20] == "2024-02-01"
    res = A.audit_leakage_scratch(eng, 20, 0.7, 0.0, n_random=200, dates=chosen, seed=4)
    assert res["n_checked"] >= 200 and res["pass"], res["mismatches"][:5]
    # independent re-check of one season-opening date without the audit helper
    pred = E.run_elo(eng, 20, 0.7).predictions
    prior = E.run_elo(eng[eng.date < dates[10]], 20, 0.7).ratings
    for r in pred[pred.date == dates[10]].itertuples():
        assert r.R_a_before == (E.shrink(prior[r.team_a], 0.7) if r.team_a in prior else E.R_BAR)
        assert r.R_b_before == (E.shrink(prior[r.team_b], 0.7) if r.team_b in prior else E.R_BAR)


@pytest.mark.parametrize("idx", [5, 10, 17])
def test_e3b_flip_future_outcomes(eng, idx):
    d = sorted(eng["date"].unique())[idx]
    res = A.audit_flip_future(eng, 20, 0.7, 50.0, d)
    assert res["pass"] and res["later_changed"] and res["n_compared"] > 0


@pytest.mark.parametrize("idx", [0, 10, 25])
def test_e3c_flip_one_game_same_date(eng, idx):
    d = sorted(eng["date"].unique())[idx]
    res = A.audit_flip_one(eng, 20, 0.7, 50.0, d, seed=idx)
    assert res["pass"] and res["n_compared"] > 0


# ----------------------------------------------------------------------------- E4
def test_e4_duplicates_and_conservation(eng):
    run = E.run_elo(eng, 30, 0.85, 25.0)
    u = run.updates
    assert u["game_key"].is_unique and len(u) == len(eng) == len(run.predictions)
    assert set(u["game_key"]) == set(eng["game_key"])
    assert ((u["delta_a"] + u["delta_b"]).abs() <= 1e-12).all()
    m = u.merge(run.predictions, on="game_key")
    assert np.allclose(m["R_a_after"] - m["R_a_before"], m["delta_a"], atol=1e-12, rtol=0)
    assert A.audit_conservation(run, len(eng))["pass"]
    dup = pd.concat([eng, eng.iloc[[0]]])
    with pytest.raises(AssertionError, match="duplicate game_key"):
        E.run_elo(dup, 20, 0.7)
    twice = eng.copy()
    d0 = twice["date"].iloc[0]
    twice.loc[len(twice)] = [twice["season"].iloc[0], d0, "X|dup", twice["team_a"].iloc[0], "ZZZ", 1, 0, "TWO_SIDED"]
    with pytest.raises(AssertionError, match="twice"):
        E.run_elo(twice, 20, 0.7)


# ----------------------------------------------------------------------------- E5
def test_e5_newcomers_and_shrink(league, eng):
    lam = 0.7
    run = E.run_elo(eng, 20, lam, keep_history=True)
    dates = sorted(run.history)
    for team, first in run.first_seen.items():
        assert all(team not in run.history[d] for d in dates if d < first)
        p = run.predictions[run.predictions.date == first]
        r = p[p.team_a == team].R_a_before.tolist() + p[p.team_b == team].R_b_before.tolist()
        assert r == [E.R_BAR]
    assert run.first_seen["NEW2"] > "2023-02-01" and run.first_seen["GHOST"].startswith("2022")
    # exactly one shrink per boundary, none before the first season, applied to every existing team
    assert [(s["season"], s["date"]) for s in run.shrinks] == [(2023, "2023-02-01"), (2024, "2024-02-01")]
    for s in run.shrinks:
        assert set(s["teams"]) == {t for t, d in run.first_seen.items() if d < s["date"]}
    assert "T03" in run.shrinks[0]["teams"] and "GHOST" in run.shrinks[1]["teams"]
    # T03 sits out 2023: its 2024 opening rating is its end-of-2022 rating shrunk twice
    end22 = run.history[max(d for d in dates if d.startswith("2022"))]["T03"]
    p = run.predictions
    first24 = p[(p.season == 2024) & ((p.team_a == "T03") | (p.team_b == "T03"))].iloc[0]
    got = first24.R_a_before if first24.team_a == "T03" else first24.R_b_before
    assert got == E.shrink(E.shrink(end22, lam), lam)
    assert A.audit_newcomers_and_shrink(run, eng)["pass"]
    # lam = 1 means no shrink in value, but the boundary step still runs once per season
    run1 = E.run_elo(eng, 20, 1.0)
    assert len(run1.shrinks) == 2


# ----------------------------------------------------------------------------- E6
def test_e6_selection_rejects_2025(league):
    four = make_league(seasons=(2022, 2023, 2024, 2025))
    with pytest.raises(ValueError, match="2025"):
        A.run_grid(four)
    with pytest.raises(ValueError, match="2025"):
        A.sensitivity_h(four, 20, 0.7)
    grid = A.run_grid(league, ks=(10, 20), lams=(0.7, 1.0))
    assert len(grid) == 4 and set(grid["n_2024"]) == {int((league.season == 2024).sum())}


def test_e6_selection_loader_reads_only_2022_2024(monkeypatch):
    if not all(J.results_path(s).exists() for s in A.SELECTION_SEASONS):
        pytest.skip("pinned results files not present")
    seen = []
    orig = D.load_season
    monkeypatch.setattr(D, "load_season", lambda s, data_dir=None: (seen.append(s), orig(s, data_dir))[1])
    g = A.selection_games()
    assert seen == [2022, 2023, 2024] and set(g["season"]) == {2022, 2023, 2024}


def test_e6_frozen_params_gate(tmp_path):
    four = make_league(seasons=(2022, 2023, 2024, 2025))
    sel = four[four.season <= 2024]
    with pytest.raises(FileNotFoundError):
        A.evaluate_2025(tmp_path, four)                       # no frozen file
    assert not (tmp_path / A.MARKER_NAME).exists() and not (tmp_path / "predictions.csv").exists()
    res = A.run_selection(tmp_path, sel)
    fz = json.loads((tmp_path / A.FROZEN_NAME).read_text())
    assert fz["table_sha256"] == A.table_sha256(sel) and fz["h"] == 0.0 and fz["R_bar"] == 1500.0
    assert (fz["K"], fz["lam"]) == (res["best"]["K"], res["best"]["lam"])
    assert len(res["grid"]) == 20 and list(res["sensitivity"]["h"]) == [0, 25, 50, 75]
    with pytest.raises(RuntimeError, match="frozen once"):
        A.run_selection(tmp_path, sel)
    # hash mismatch: a 2023 score edited after freezing
    tampered = four.copy()
    i = tampered.index[tampered.season == 2023][0]
    tampered.loc[i, ["goals_lo", "goals_hi"]] = tampered.loc[i, ["goals_hi", "goals_lo"]].to_numpy()
    with pytest.raises(RuntimeError, match="hash"):
        A.evaluate_2025(tmp_path, tampered)
    # edited parameters
    other = tmp_path / "edited"
    other.mkdir()
    (other / A.FROZEN_NAME).write_text(json.dumps({**fz, "K": fz["K"] + 1}))
    with pytest.raises(RuntimeError, match="edited"):
        A.evaluate_2025(other, four)
    # 2026 is out of scope
    with pytest.raises(ValueError, match="2026"):
        A.evaluate_2025(tmp_path, make_league(seasons=(2022, 2023, 2024, 2025, 2026)))
    assert not (tmp_path / A.MARKER_NAME).exists()
    # the real run: once
    out = A.evaluate_2025(tmp_path, four)
    pred = pd.read_csv(tmp_path / "predictions.csv")
    assert list(pred.columns) == E.PRED_COLUMNS + ["label"] and (pred.label == A.LABEL).all() and set(pred.season) == {2022, 2023, 2024, 2025}
    assert out["metrics"]["season"].eq(2025).all()
    with pytest.raises(RuntimeError, match="exactly once"):
        A.evaluate_2025(tmp_path, four)
    with pytest.raises(RuntimeError, match="already been run"):
        (tmp_path / A.FROZEN_NAME).unlink()
        A.freeze_params(res["best"], sel, tmp_path)


def test_e6_2025_predictions_are_pre_game(tmp_path):
    """Every 2025 prediction is made before the game is learned: flipping all 2025 outcomes on or after a date
    leaves predictions up to and including that date unchanged."""
    four = E.from_canonical(make_league(seasons=(2022, 2023, 2024, 2025)))
    d = sorted(four[four.season == 2025].date.unique())[3]
    assert A.audit_flip_future(four, 20, 0.7, 0.0, d)["pass"]


# ----------------------------------------------------------------------------- E7
def test_e7_metrics_and_calibration(eng):
    pred = E.run_elo(eng, 20, 0.7).predictions
    tab, n_half = M.reliability_table(pred.p_a, pred.y_a)
    assert n_half == int((pred.p_a == 0.5).sum()) and n_half > 0
    assert tab["n"].sum() + n_half == len(pred)
    assert len(tab) == 5 and tab["n"].max() - tab["n"].min() <= 1
    assert (tab["p_fav_min"] > 0.5).all() and tab["mean_predicted"].is_monotonic_increasing
    sm = M.season_metrics(pred)
    assert set(sm.subset) == {"ALL", "TWO_SIDED"} and set(sm.season) == {2022, 2023, 2024}
    assert np.allclose(sm.brier_const, 0.25) and np.allclose(sm.log_loss_const, math.log(2))
    for s in (2022, 2023, 2024):
        assert sm[(sm.season == s) & (sm.subset == "ALL")].n.iloc[0] == int((pred.season == s).sum())
        assert sm[(sm.season == s) & (sm.subset == "TWO_SIDED")].n.iloc[0] == \
            int(((pred.season == s) & (pred.sidedness == "TWO_SIDED")).sum())
    # favourite orientation by hand
    t, n = M.reliability_table([0.2, 0.7, 0.5, 0.9, 0.4], [0, 1, 1, 0, 1], n_bins=2)
    assert n == 1 and t["n"].tolist() == [2, 2]
    assert t["mean_predicted"].tolist() == pytest.approx([0.65, 0.85])   # p_fav: 0.6, 0.7 | 0.8, 0.9
    assert t["observed_rate"].tolist() == pytest.approx([0.5, 0.5])      # y_fav: 0, 1 | 1, 0
