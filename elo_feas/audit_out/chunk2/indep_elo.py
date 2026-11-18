"""Independent Elo reimplementation for the Chunk 2 audit (diagnostic, no re-selection).

Written from the spec only; imports nothing from the experiment package.
  R_bar = 1500. At the first game date of a new season every existing rating becomes 1500 + lam*(R - 1500);
  teams first seen on a date then enter at 1500 (newcomers after the shrink).
  E_a = 1 / (1 + 10^(-(R_a - R_b)/400)); h = 0, so venue is ignored.
  All games on a date are predicted from the ratings before that date, then updated: d = K*(S_a - E_a),
  R_a += d, R_b -= d (zero-sum). Games listed in no_update are predicted (scored) but leave ratings unchanged.
Team A of a game is the alphabetically lower canonical name (team_lo); S_a = 1 if goals_lo > goals_hi.
"""
from __future__ import annotations

import math
import sys

sys.dont_write_bytecode = True

R_BAR = 1500.0
SCALE = 400.0
EPS = 1e-6                                   # log-loss clipping used by the recorded outputs
PRED_FIELDS = ("season", "date", "game_key", "team_a", "team_b", "goals_a", "goals_b", "sidedness",
               "R_a_before", "R_b_before", "p_a", "y_a", "d")


def games_from_canonical(df) -> list[tuple]:
    """Canonical game rows -> sorted tuples (season, date, game_key, team_a, team_b, goals_a, goals_b, sidedness)."""
    rows = []
    for r in df[["season", "date", "game_key", "team_lo", "team_hi", "goals_lo", "goals_hi", "sidedness"]] \
            .itertuples(index=False):
        ga, gb = float(r.goals_lo), float(r.goals_hi)
        if ga == gb:
            raise ValueError(f"tie in {r.game_key}")
        rows.append((int(r.season), str(r.date), str(r.game_key), str(r.team_lo), str(r.team_hi), ga, gb,
                     str(r.sidedness)))
    rows.sort(key=lambda t: (t[1], t[2]))
    return rows


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-(ra - rb) / SCALE))


def run(games: list[tuple], k: float, lam: float, no_update=frozenset()) -> dict:
    ratings: dict[str, float] = {}
    preds: list[tuple] = []
    starts: dict[int, dict] = {}            # season -> ratings in force at its first date (after shrink, pre-newcomer)
    ends: dict[int, dict] = {}              # season -> ratings after its last date
    prev_season = None
    i, n = 0, len(games)
    while i < n:
        date = games[i][1]
        j = i
        while j < n and games[j][1] == date:
            j += 1
        day = games[i:j]
        season = day[0][0]
        if any(g[0] != season for g in day):
            raise ValueError(f"two seasons on {date}")
        if prev_season is None:
            starts[season] = {}
        elif season != prev_season:
            if season < prev_season:
                raise ValueError("seasons out of date order")
            ends[prev_season] = dict(ratings)
            for t in ratings:
                ratings[t] = R_BAR + lam * (ratings[t] - R_BAR)
            starts[season] = dict(ratings)
        prev_season = season
        seen = set()
        for g in day:
            for t in (g[3], g[4]):
                if t in seen:
                    raise ValueError(f"{t} plays twice on {date}")
                seen.add(t)
                if t not in ratings:
                    ratings[t] = R_BAR
        batch = []
        for g in day:                                            # predict the whole date first
            ra, rb = ratings[g[3]], ratings[g[4]]
            batch.append((g, ra, rb, expected(ra, rb), 1 if g[5] > g[6] else 0))
        for g, ra, rb, p, y in batch:                            # then update
            d = 0.0 if g[2] in no_update else k * (y - p)
            ratings[g[3]], ratings[g[4]] = ra + d, rb - d
            preds.append((g[0], g[1], g[2], g[3], g[4], g[5], g[6], g[7], ra, rb, p, y, d))
        i = j
    if prev_season is not None:
        ends[prev_season] = dict(ratings)
    return {"preds": preds, "ratings": ratings, "starts": starts, "ends": ends}


def brier(ps, ys) -> float:
    ps, ys = list(ps), list(ys)
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def log_loss(ps, ys) -> float:
    ps, ys = list(ps), list(ys)
    tot = 0.0
    for p, y in zip(ps, ys):
        p = min(max(p, EPS), 1 - EPS)
        tot += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return tot / len(ps)
