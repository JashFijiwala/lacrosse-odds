"""Chunk A: standalone Elo engine with an explicit probability denominator D (diagnostic, no re-selection).

Imports nothing from elo_feas. The caller (chunk4_run.py) supplies the games frame from selection_games()
(seasons 2022-2024); `check_games` asserts max season <= 2024.

  P = 1 / (1 + 10^(-(Ra - Rb + h*v) / D));  R_a += K (S - P), R_b -= K (S - P)   (zero-sum)
  at the first date of a new season every rating -> 1500 + lam (R - 1500); newcomers enter at 1500 after the shrink;
  all games on a date are predicted from the ratings before that date, then updated (date-batched).
Ratings are kept in rating points (R-space); z = (R - 1500)/D is reported only for comparison.
"""
from __future__ import annotations

import math
import sys

sys.dont_write_bytecode = True

R_BAR = 1500.0
EPS = 1e-6


def venue_sign(fa: str, fb: str) -> int:
    if fa == "Home" and fb == "Away":
        return 1
    if fa == "Away" and fb == "Home":
        return -1
    return 0


def check_games(df) -> list[tuple]:
    """selection_games() frame -> sorted tuples (season, date, key, a, b, y_a, v)."""
    assert int(df["season"].max()) <= 2024, "only seasons <= 2024 allowed"
    rows = []
    for r in df.itertuples(index=False):
        ga, gb = float(r.goals_lo), float(r.goals_hi)
        assert ga != gb
        rows.append((int(r.season), str(r.date), str(r.game_key), str(r.team_lo), str(r.team_hi),
                     1 if ga > gb else 0, venue_sign(str(r.flag_lo or ""), str(r.flag_hi or ""))))
    rows.sort(key=lambda t: (t[1], t[2]))
    return rows


def prob(ra: float, rb: float, h: float, v: int, D: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-(ra - rb + h * v) / D))


def run(games: list[tuple], K: float, D: float, lam: float, h: float = 0.0) -> dict:
    R: dict[str, float] = {}
    out = []                                  # (season, date, key, Ra, Rb, p, y)
    ends: dict[int, dict] = {}
    prev = None
    i, n = 0, len(games)
    while i < n:
        date = games[i][1]
        j = i
        while j < n and games[j][1] == date:
            j += 1
        day = games[i:j]
        s = day[0][0]
        if prev is not None and s != prev:
            ends[prev] = dict(R)
            for t in R:
                R[t] = R_BAR + lam * (R[t] - R_BAR)
        prev = s
        for g in day:
            for t in (g[3], g[4]):
                if t not in R:
                    R[t] = R_BAR
        batch = [(g, R[g[3]], R[g[4]]) for g in day]
        for g, ra, rb in batch:
            p = prob(ra, rb, h, g[6], D)
            d = K * (g[5] - p)
            R[g[3]], R[g[4]] = ra + d, rb - d
            out.append((g[0], g[1], g[2], ra, rb, p, g[5]))
        i = j
    if prev is not None:
        ends[prev] = dict(R)
    z_end = {s: {t: (r - R_BAR) / D for t, r in e.items()} for s, e in ends.items()}
    return {"rows": out, "p": [r[5] for r in out], "z_end": z_end}


def brier_ll(rows, season: int | None = None) -> tuple[int, float, float]:
    sel = [r for r in rows if season is None or r[0] == season]
    n = len(sel)
    b = sum((r[5] - r[6]) ** 2 for r in sel) / n
    ll = 0.0
    for r in sel:
        p = min(max(r[5], EPS), 1 - EPS)
        ll += -(r[6] * math.log(p) + (1 - r[6]) * math.log(1 - p))
    return n, b, ll / n


def max_diff(a: dict, b: dict) -> tuple[float, float]:
    """(max |p diff| over all games, max |z_end diff| over all (season, team))."""
    dp = max(abs(x - y) for x, y in zip(a["p"], b["p"]))
    dz = 0.0
    for s in a["z_end"]:
        assert set(a["z_end"][s]) == set(b["z_end"][s])
        for t, z in a["z_end"][s].items():
            dz = max(dz, abs(z - b["z_end"][s][t]))
    return dp, dz
