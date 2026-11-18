"""Experiment protocol and audits for the Elo feasibility experiment (implemented in Chunk 3, run in Chunk 4).

Protocol:
  1. run_selection(): seasons 2022-2024 only. Grid over K x lam (h = 0); 2022 is warm-up; select by the mean
     Brier on 2024 games (log loss breaks ties). Writes frozen_params.json (with the SHA256 of the 2022-2024
     game table) and then the h sensitivity table (2024, labelled sensitivity, never used for selection).
  2. evaluate_2025(): refuses unless frozen_params.json exists, is intact and its table hash matches; refuses a
     second run. Ratings run 2022 -> 2025 with the frozen parameters; each 2025 game is predicted before it is
     learned. 2026 is not used.
Audits: orientation invariance, leakage (from-scratch recomputation, future flips, same-date flips),
conservation and newcomer/shrink checks, usable on synthetic or real engine frames.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import hashlib  # noqa: E402
import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from elo_feas import data_layer as D  # noqa: E402
from elo_feas import elo as E  # noqa: E402
from elo_feas import metrics as M  # noqa: E402

OUT_DIR = D.OUT_DIR
SELECTION_SEASONS = (2022, 2023, 2024)
WARMUP_SEASON = 2022
SELECT_SEASON = 2024
TEST_SEASON = 2025
K_GRID = (10, 15, 20, 30, 40)
LAM_GRID = (0.5, 0.7, 0.85, 1.0)
H_PRIMARY = 0.0
H_SENSITIVITY = (0, 25, 50, 75)
FROZEN_NAME = "frozen_params.json"
MARKER_NAME = "test_2025_run.json"
PARAM_KEYS = ("K", "lam", "h", "R_bar", "table_sha256")
LABEL = "feasibility experiment — pipeline check, not a research result"


# --------------------------------------------------------------------------- hashing and inputs
def table_sha256(games: pd.DataFrame) -> str:
    """SHA256 of the canonical game table (GAME_COLUMNS, sorted by date, game_key, LF line endings)."""
    t = games[D.GAME_COLUMNS].sort_values(["date", "game_key"], kind="mergesort").reset_index(drop=True)
    return hashlib.sha256(t.to_csv(index=False, lineterminator="\n").encode("utf-8")).hexdigest()


def _params_sha256(fz: dict) -> str:
    return hashlib.sha256(json.dumps({k: fz[k] for k in PARAM_KEYS}, sort_keys=True).encode("utf-8")).hexdigest()


def selection_games(data_dir: Path | None = None) -> pd.DataFrame:
    """Canonical games for 2022-2024 only (the 2025 file is never read)."""
    return D.build_all(seasons=SELECTION_SEASONS, data_dir=data_dir, write=False)["games"]


def games_2022_2025(data_dir: Path | None = None) -> pd.DataFrame:
    """Canonical games for 2022-2025 (2026 is never read)."""
    return D.build_all(seasons=SELECTION_SEASONS + (TEST_SEASON,), data_dir=data_dir, write=False)["games"]


def _require_selection_only(games: pd.DataFrame) -> None:
    extra = sorted(set(games["season"]) - set(SELECTION_SEASONS))
    if extra:
        raise ValueError(f"selection must use seasons {SELECTION_SEASONS} only; got also {extra}")


# --------------------------------------------------------------------------- selection
def _score(pred: pd.DataFrame, season: int, name: str = "ALL") -> tuple[int, float, float]:
    d = M.subset(pred[pred["season"] == season], name)
    return len(d), M.brier(d["p_a"], d["y_a"]), M.log_loss(d["p_a"], d["y_a"])


def run_grid(games: pd.DataFrame, ks=K_GRID, lams=LAM_GRID, h: float = H_PRIMARY) -> pd.DataFrame:
    """One engine run over 2022-2024 per (K, lam). Scores 2024 (selection) and 2023 (context only)."""
    _require_selection_only(games)
    eng = E.from_canonical(games)
    rows = []
    for k in ks:
        for lam in lams:
            pred = E.run_elo(eng, k, lam, h).predictions
            n24, b24, l24 = _score(pred, SELECT_SEASON)
            n24t, b24t, l24t = _score(pred, SELECT_SEASON, "TWO_SIDED")
            n23, b23, l23 = _score(pred, 2023)
            rows.append({"K": k, "lam": lam, "h": h, "n_2024": n24, "brier_2024": b24, "log_loss_2024": l24,
                         "n_2024_two_sided": n24t, "brier_2024_two_sided": b24t, "log_loss_2024_two_sided": l24t,
                         "n_2023": n23, "brier_2023": b23, "log_loss_2023": l23})
    grid = pd.DataFrame(rows)
    order = grid.sort_values(["brier_2024", "log_loss_2024", "K", "lam"], kind="mergesort").index
    grid.loc[order, "rank"] = range(1, len(grid) + 1)
    grid["rank"] = grid["rank"].astype(int)
    return grid


def select_best(grid: pd.DataFrame, ks=K_GRID, lams=LAM_GRID) -> dict:
    best = grid.sort_values(["brier_2024", "log_loss_2024", "K", "lam"], kind="mergesort").iloc[0]
    edges = []
    if best["K"] in (min(ks), max(ks)):
        edges.append(f"K={best['K']} is on the grid edge {min(ks)}/{max(ks)}")
    if best["lam"] in (min(lams), max(lams)):
        edges.append(f"lam={best['lam']} is on the grid edge {min(lams)}/{max(lams)}")
    return {"K": float(best["K"]), "lam": float(best["lam"]), "brier_2024": float(best["brier_2024"]),
            "log_loss_2024": float(best["log_loss_2024"]), "on_grid_edge": bool(edges), "edge_reasons": edges}


def sensitivity_h(games: pd.DataFrame, k: float, lam: float, hs=H_SENSITIVITY) -> pd.DataFrame:
    """SENSITIVITY ONLY (not used for selection): frozen K, lam; h varied; scored on 2024."""
    _require_selection_only(games)
    eng = E.from_canonical(games)
    rows = []
    for h in hs:
        pred = E.run_elo(eng, k, lam, float(h)).predictions
        n, b, ll = _score(pred, SELECT_SEASON)
        nt, bt, lt = _score(pred, SELECT_SEASON, "TWO_SIDED")
        rows.append({"role": "SENSITIVITY (not used for selection)", "K": k, "lam": lam, "h": h, "n_2024": n,
                     "brier_2024": b, "log_loss_2024": ll, "brier_2024_two_sided": bt, "log_loss_2024_two_sided": lt,
                     "label": LABEL})
    return pd.DataFrame(rows)


def freeze_params(best: dict, games: pd.DataFrame, out_dir: Path = OUT_DIR, ks=K_GRID, lams=LAM_GRID) -> dict:
    """Write frozen_params.json. Refuses to overwrite it, and refuses after the 2025 test has started."""
    _require_selection_only(games)
    path, marker = Path(out_dir) / FROZEN_NAME, Path(out_dir) / MARKER_NAME
    if path.exists():
        raise RuntimeError(f"{path} already exists; parameters are frozen once")
    if marker.exists():
        raise RuntimeError(f"{marker} exists; the 2025 test has already been run")
    fz = {"K": best["K"], "lam": best["lam"], "h": H_PRIMARY, "R_bar": E.R_BAR, "table_sha256": table_sha256(games)}
    fz["params_sha256"] = _params_sha256(fz)
    fz.update({"label": LABEL, "selection_metric": f"mean Brier on {SELECT_SEASON} games (log loss breaks ties)",
               "brier_2024": best["brier_2024"], "log_loss_2024": best["log_loss_2024"],
               "on_grid_edge": best["on_grid_edge"], "edge_reasons": best["edge_reasons"],
               "grid_K": list(ks), "grid_lam": list(lams), "seasons": list(SELECTION_SEASONS),
               "warmup_season": WARMUP_SEASON,
               "n_games_per_season": {str(s): int((games["season"] == s).sum()) for s in SELECTION_SEASONS},
               "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fz, indent=2), encoding="utf-8")
    return fz


def load_frozen(out_dir: Path, selection_table: pd.DataFrame) -> dict:
    """Raises FileNotFoundError if missing, RuntimeError if the params were edited or the table hash differs."""
    path = Path(out_dir) / FROZEN_NAME
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run the 2022-2024 selection first")
    fz = json.loads(path.read_text(encoding="utf-8"))
    if fz.get("params_sha256") != _params_sha256(fz):
        raise RuntimeError(f"{path}: frozen parameters were edited after freezing")
    got = table_sha256(selection_table)
    if got != fz["table_sha256"]:
        raise RuntimeError(f"2022-2024 game table hash {got} != frozen {fz['table_sha256']}")
    return fz


def run_selection(out_dir: Path = OUT_DIR, games: pd.DataFrame | None = None, ks=K_GRID, lams=LAM_GRID) -> dict:
    """Step 1 of the protocol. Grid -> select -> freeze -> sensitivity. Never reads 2025."""
    games = selection_games() if games is None else games
    _require_selection_only(games)
    grid = run_grid(games, ks, lams)
    best = select_best(grid, ks, lams)
    fz = freeze_params(best, games, out_dir, ks, lams)                 # written before anything else
    sens = sensitivity_h(games, fz["K"], fz["lam"])
    grid.assign(label=LABEL).to_csv(Path(out_dir) / "grid_2024.csv", index=False)
    sens.to_csv(Path(out_dir) / "sensitivity_h_2024.csv", index=False)
    return {"grid": grid, "best": best, "frozen": fz, "sensitivity": sens}


# --------------------------------------------------------------------------- 2025 test (runs once)
def evaluate_2025(out_dir: Path = OUT_DIR, games: pd.DataFrame | None = None) -> dict:
    out_dir = Path(out_dir)
    marker = out_dir / MARKER_NAME
    if marker.exists():
        raise RuntimeError(f"{marker} exists; the 2025 test runs exactly once")
    games = games_2022_2025() if games is None else games
    late = sorted(s for s in set(games["season"]) if s > TEST_SEASON)
    if late:
        raise ValueError(f"seasons after {TEST_SEASON} are out of scope: {late}")
    sel = games[games["season"].isin(SELECTION_SEASONS)]
    fz = load_frozen(out_dir, sel)                                         # raises before any 2025 prediction
    marker.write_text(json.dumps({"status": "started", "params_sha256": fz["params_sha256"],
                                  "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}),
                      encoding="utf-8")
    run = E.run_elo(E.from_canonical(games), fz["K"], fz["lam"], fz["h"], fz["R_bar"])
    ref = E.run_elo(E.from_canonical(sel), fz["K"], fz["lam"], fz["h"], fz["R_bar"]).predictions
    same = run.predictions[run.predictions["season"].isin(SELECTION_SEASONS)].reset_index(drop=True)
    pd.testing.assert_frame_equal(same, ref.reset_index(drop=True))     # 2022-2024 identical to the selection run
    p25 = run.predictions[run.predictions["season"] == TEST_SEASON]
    met = M.season_metrics(p25)
    rel, n_half = M.reliability_table(p25["p_a"], p25["y_a"])
    run.predictions.assign(label=LABEL).to_csv(out_dir / "predictions.csv", index=False)
    rel.assign(label=LABEL).to_csv(out_dir / "reliability_2025.csv", index=False)
    marker.write_text(json.dumps({"label": LABEL, "status": "complete", "params_sha256": fz["params_sha256"],
                                  "n_2025_games": len(p25),
                                  "completed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}),
                      encoding="utf-8")
    return {"frozen": fz, "run": run, "metrics": met, "reliability": rel, "n_p_half_excluded": n_half}


# --------------------------------------------------------------------------- audits
def reorient(eng: pd.DataFrame, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
    """Swap team_a/team_b, flip y and v for a random half of games, then shuffle all rows. Returns (frame, mask)."""
    rng = np.random.default_rng(seed)
    g = eng.copy().reset_index(drop=True)
    m = rng.random(len(g)) < 0.5
    g.loc[m, ["team_a", "team_b"]] = g.loc[m, ["team_b", "team_a"]].to_numpy()
    g.loc[m, "y_a"] = 1 - g.loc[m, "y_a"]
    g.loc[m, "v"] = -g.loc[m, "v"]
    flipped = set(g.loc[m, "game_key"])
    g = g.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    return g, np.array([k in flipped for k in g["game_key"]])


def audit_orientation(eng: pd.DataFrame, k: float, lam: float, h: float, seed: int = 0) -> dict:
    base = E.run_elo(eng, k, lam, h)
    alt_eng, _ = reorient(eng, seed)
    alt = E.run_elo(alt_eng, k, lam, h)
    flipped = set(alt_eng.loc[alt_eng["team_a"] != alt_eng["game_key"].map(dict(zip(eng["game_key"], eng["team_a"]))),
                              "game_key"])
    a = base.predictions.set_index("game_key")["p_a"]
    b = alt.predictions.set_index("game_key")["p_a"].reindex(a.index)
    target = np.where(a.index.isin(flipped), 1 - a.to_numpy(), a.to_numpy())
    rdiff = max(abs(base.ratings[t] - alt.ratings[t]) for t in base.ratings)
    pdiff = float(np.max(np.abs(b.to_numpy() - target)))
    return {"n_flipped": len(flipped), "max_rating_diff": rdiff, "max_prob_diff": pdiff,
            "same_teams": set(base.ratings) == set(alt.ratings), "pass": rdiff <= 1e-12 and pdiff <= 1e-12}


def ratings_from_scratch(eng: pd.DataFrame, date: str, k: float, lam: float, h: float, r_bar: float = E.R_BAR) -> dict:
    """Ratings in force for `date`, recomputed using only games dated strictly before it (plus the season-boundary
    shrink if `date` opens a new season)."""
    prior = eng[eng["date"] < date]
    if not len(prior):
        return {}
    ratings = E.run_elo(prior, k, lam, h, r_bar).ratings
    if int(eng.loc[eng["date"] == date, "season"].iloc[0]) != int(prior["season"].max()):
        ratings = {t: E.shrink(r, lam, r_bar) for t, r in ratings.items()}
    return ratings


def audit_leakage_scratch(eng, k, lam, h, n_random: int = 200, dates=(), seed: int = 0, r_bar: float = E.R_BAR) -> dict:
    pred = E.run_elo(eng, k, lam, h, r_bar).predictions
    rng = np.random.default_rng(seed)
    pick = set(rng.choice(len(pred), size=min(n_random, len(pred)), replace=False).tolist())
    pick |= set(np.flatnonzero(pred["date"].isin(list(dates))).tolist())
    sub = pred.iloc[sorted(pick)]
    bad = []
    cache = {}
    for r in sub.itertuples(index=False):
        if r.date not in cache:
            cache[r.date] = ratings_from_scratch(eng, r.date, k, lam, h, r_bar)
        rat = cache[r.date]
        if rat.get(r.team_a, r_bar) != r.R_a_before or rat.get(r.team_b, r_bar) != r.R_b_before:
            bad.append(r.game_key)
    return {"n_checked": len(sub), "n_dates": len(cache), "mismatches": bad, "pass": not bad}


def audit_flip_future(eng, k, lam, h, date: str) -> dict:
    """Flip every outcome dated >= date; predictions dated <= date must be exactly unchanged."""
    alt = eng.copy()
    m = alt["date"] >= date
    alt.loc[m, "y_a"] = 1 - alt.loc[m, "y_a"]
    a, b = E.run_elo(eng, k, lam, h).predictions, E.run_elo(alt, k, lam, h).predictions
    keep = a["date"] <= date
    same = bool((a.loc[keep, "p_a"].to_numpy() == b.loc[keep, "p_a"].to_numpy()).all())
    later_changed = bool((a.loc[~keep, "p_a"].to_numpy() != b.loc[~keep, "p_a"].to_numpy()).any())
    return {"date": date, "n_flipped": int(m.sum()), "n_compared": int(keep.sum()), "unchanged": same,
            "later_changed": later_changed, "pass": same}


def audit_flip_one(eng, k, lam, h, date: str, seed: int = 0) -> dict:
    """Flip one game's outcome on `date`; the other games on that date must keep exactly the same predictions."""
    day = eng.index[eng["date"] == date]
    target = eng.loc[np.random.default_rng(seed).choice(day), "game_key"]
    alt = eng.copy()
    alt.loc[alt["game_key"] == target, "y_a"] = 1 - alt.loc[alt["game_key"] == target, "y_a"]
    a, b = E.run_elo(eng, k, lam, h).predictions, E.run_elo(alt, k, lam, h).predictions
    m = (a["date"] == date) & (a["game_key"] != target)
    same = bool((a.loc[m, "p_a"].to_numpy() == b.loc[m, "p_a"].to_numpy()).all())
    return {"date": date, "flipped_game": target, "n_compared": int(m.sum()), "pass": same}


def audit_conservation(run: E.EloRun, n_games: int) -> dict:
    u = run.updates
    sums = (u["delta_a"] + u["delta_b"]).abs()
    return {"n_updates": len(u), "n_games": n_games, "each_key_once": bool(u["game_key"].is_unique),
            "max_abs_sum": float(sums.max()) if len(u) else 0.0,
            "pass": len(u) == n_games and bool(u["game_key"].is_unique) and (sums <= 1e-12).all()}


def audit_newcomers_and_shrink(run: E.EloRun, eng: pd.DataFrame, r_bar: float = E.R_BAR) -> dict:
    p = run.predictions
    first = pd.concat([p[["date", "team_a", "R_a_before"]].set_axis(["date", "team", "R"], axis=1),
                       p[["date", "team_b", "R_b_before"]].set_axis(["date", "team", "R"], axis=1)]) \
        .sort_values("date", kind="mergesort").drop_duplicates("team")
    newcomers_ok = bool((first["R"] == r_bar).all())
    starts = eng.groupby("season")["date"].min().sort_index()
    expected_boundaries = list(starts.iloc[1:].items())
    got = [(s["season"], s["date"]) for s in run.shrinks]
    coverage = all(set(s["teams"]) == {t for t, d in run.first_seen.items() if d < s["date"]} for s in run.shrinks)
    return {"newcomers_at_r_bar": newcomers_ok, "boundaries_expected": expected_boundaries, "boundaries_got": got,
            "shrink_covers_all_existing": coverage,
            "pass": newcomers_ok and got == [(int(s), d) for s, d in expected_boundaries] and coverage}
