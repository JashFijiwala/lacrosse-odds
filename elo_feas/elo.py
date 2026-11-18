"""Elo engine for the feasibility experiment. The spec is fixed (Chunk 3); do not improvise.

- R_bar = 1500 (constant). A team is created at R_bar on its first game date (after any season shrink).
- E_A = 1 / (1 + 10 ** (-(R_A - R_B + h*v) / 400)); v = +1 Home/Away, -1 Away/Home, 0 otherwise.
- R_A' = R_A + K*(S_A - E_A), R_B' = R_B + K*(S_B - E_B). Since E_B = 1 - E_A and S_B = 1 - S_A, the B update
  is applied as -K*(S_A - E_A), which is the same quantity and keeps each game exactly zero-sum.
- All games on one date are predicted from the ratings before that date; updates are applied afterwards.
- At the first date of each new season, every existing rating shrinks once: R <- R_bar + lam*(R - R_bar).
- Margin, overtime and venue (other than h*v) are not used.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from dataclasses import dataclass  # noqa: E402

import pandas as pd  # noqa: E402

R_BAR = 1500.0
SCALE = 400.0
ENGINE_COLUMNS = ["season", "date", "game_key", "team_a", "team_b", "y_a", "v", "sidedness"]
PRED_COLUMNS = ["season", "date", "game_key", "team_a", "team_b", "R_a_before", "R_b_before", "p_a", "y_a",
                "sidedness", "v"]


def expected(r_a: float, r_b: float, h: float = 0.0, v: int = 0) -> float:
    return 1.0 / (1.0 + 10.0 ** (-(r_a - r_b + h * v) / SCALE))


def venue_sign(flag_a: str, flag_b: str) -> int:
    if flag_a == "Home" and flag_b == "Away":
        return 1
    if flag_a == "Away" and flag_b == "Home":
        return -1
    return 0


def update(r_a: float, r_b: float, s_a: int, k: float, e_a: float) -> tuple[float, float]:
    d = k * (s_a - e_a)
    return r_a + d, r_b - d


def shrink(r: float, lam: float, r_bar: float = R_BAR) -> float:
    return r_bar + lam * (r - r_bar)


def from_canonical(games: pd.DataFrame) -> pd.DataFrame:
    """Canonical game table (elo_feas.data_layer) -> engine frame. team_a = team_lo (arbitrary, fixed)."""
    if (games["goals_lo"] == games["goals_hi"]).any():
        raise AssertionError("ties do not occur in the canonical table")
    flo, fhi = games["flag_lo"].fillna("").astype(str), games["flag_hi"].fillna("").astype(str)
    return pd.DataFrame({
        "season": games["season"].astype(int), "date": games["date"].astype(str), "game_key": games["game_key"],
        "team_a": games["team_lo"], "team_b": games["team_hi"],
        "y_a": (games["goals_lo"] > games["goals_hi"]).astype(int),
        "v": [venue_sign(a, b) for a, b in zip(flo, fhi)], "sidedness": games["sidedness"]})[ENGINE_COLUMNS] \
        .reset_index(drop=True)


@dataclass
class EloRun:
    predictions: pd.DataFrame      # PRED_COLUMNS, sorted by date, game_key
    ratings: dict                  # final ratings
    updates: pd.DataFrame          # one row per game: game_key, date, team_a, team_b, delta_a, delta_b, R_a_after, R_b_after
    shrinks: list                  # one dict per season boundary: season, date, teams
    first_seen: dict               # team -> first game date
    history: dict | None           # date -> ratings after that date's updates (keep_history=True only)


def _validate(g: pd.DataFrame) -> None:
    missing = [c for c in ENGINE_COLUMNS if c not in g.columns]
    if missing:
        raise ValueError(f"engine input missing columns {missing}")
    if not g["game_key"].is_unique:
        raise AssertionError("duplicate game_key in engine input")
    if not set(g["y_a"]) <= {0, 1}:
        raise AssertionError(f"y_a must be 0/1 (ties do not occur); got {sorted(set(g['y_a']))}")
    if not set(g["v"]) <= {-1, 0, 1}:
        raise AssertionError(f"v must be -1/0/1; got {sorted(set(g['v']))}")
    if (g["team_a"] == g["team_b"]).any():
        raise AssertionError("a team cannot play itself")
    per_date = g.groupby("date")["season"].nunique()
    if (per_date > 1).any():
        raise AssertionError(f"dates with more than one season: {list(per_date[per_date > 1].index)}")
    seasons_in_date_order = g.drop_duplicates("date").sort_values("date")["season"].tolist()
    if seasons_in_date_order != sorted(seasons_in_date_order):
        raise AssertionError("seasons are not in date order")


def run_elo(games: pd.DataFrame, k: float, lam: float, h: float = 0.0, r_bar: float = R_BAR,
            keep_history: bool = False) -> EloRun:
    """Run the engine over an engine frame (see from_canonical). Row order is irrelevant."""
    g = games[ENGINE_COLUMNS].copy()
    _validate(g)
    g = g.sort_values(["date", "game_key"], kind="mergesort")
    ratings, first_seen, shrinks, preds, ups = {}, {}, [], [], []
    history = {} if keep_history else None
    prev_season = None
    for date, day in g.groupby("date", sort=True):
        season = int(day["season"].iloc[0])
        if prev_season is not None and season != prev_season:
            teams = sorted(ratings)
            for t in teams:
                ratings[t] = shrink(ratings[t], lam, r_bar)
            shrinks.append({"season": season, "date": date, "teams": tuple(teams)})
        prev_season = season
        today = list(day["team_a"]) + list(day["team_b"])
        if len(today) != len(set(today)):
            raise AssertionError(f"a team plays twice on {date}")
        for t in today:
            if t not in ratings:
                ratings[t], first_seen[t] = r_bar, date
        pending = []
        for r in day.itertuples(index=False):                       # 1) predict every game of the date
            ra, rb = ratings[r.team_a], ratings[r.team_b]
            p = expected(ra, rb, h, r.v)
            preds.append((r.season, date, r.game_key, r.team_a, r.team_b, ra, rb, p, int(r.y_a), r.sidedness, int(r.v)))
            pending.append((r, ra, rb, p))
        for r, ra, rb, p in pending:                                 # 2) then apply that date's updates
            na, nb = update(ra, rb, int(r.y_a), k, p)
            ratings[r.team_a], ratings[r.team_b] = na, nb
            ups.append((r.game_key, date, r.team_a, r.team_b, na - ra, nb - rb, na, nb))
        if keep_history:
            history[date] = dict(ratings)
    predictions = pd.DataFrame(preds, columns=PRED_COLUMNS)
    updates = pd.DataFrame(ups, columns=["game_key", "date", "team_a", "team_b", "delta_a", "delta_b", "R_a_after",
                                         "R_b_after"])
    return EloRun(predictions, ratings, updates, shrinks, first_seen, history)
