"""Scoring for the Elo feasibility experiment: Brier, log loss, constant baseline, reliability table."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

EPS = 1e-6
CONSTANT_P = 0.5
SUBSETS = ("ALL", "TWO_SIDED")


def brier(p, y) -> float:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2))


def log_loss(p, y) -> float:
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=float)
    return float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))


def subset(pred: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "ALL":
        return pred
    if name == "TWO_SIDED":
        return pred[pred["sidedness"] == "TWO_SIDED"]
    raise ValueError(f"unknown subset {name!r}")


def season_metrics(pred: pd.DataFrame, prob_col: str = "p_a", y_col: str = "y_a") -> pd.DataFrame:
    """Mean Brier and log loss per season, on ALL and TWO_SIDED games, for the model and the constant 0.5."""
    rows = []
    for s in sorted(pred["season"].unique()):
        for name in SUBSETS:
            d = subset(pred[pred["season"] == s], name)
            if not len(d):
                continue
            c = np.full(len(d), CONSTANT_P)
            rows.append({"season": int(s), "subset": name, "n": len(d),
                         "brier": brier(d[prob_col], d[y_col]), "log_loss": log_loss(d[prob_col], d[y_col]),
                         "brier_const": brier(c, d[y_col]), "log_loss_const": log_loss(c, d[y_col])})
    return pd.DataFrame(rows)


def reliability_table(p, y, n_bins: int = 5) -> tuple[pd.DataFrame, int]:
    """Favourite orientation (p_fav >= 0.5, y_fav), equal-count bins by p_fav. Games with p exactly 0.5 are
    excluded. Returns (table with bin, n, p_fav_min, p_fav_max, mean_predicted, observed_rate; n_excluded)."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=int)
    keep = p != 0.5
    p_fav = np.where(p[keep] > 0.5, p[keep], 1 - p[keep])
    y_fav = np.where(p[keep] > 0.5, y[keep], 1 - y[keep])
    order = np.argsort(p_fav, kind="mergesort")
    rows = []
    for i, idx in enumerate(np.array_split(order, n_bins)):
        if not len(idx):
            continue
        rows.append({"bin": i + 1, "n": len(idx), "p_fav_min": float(p_fav[idx].min()),
                     "p_fav_max": float(p_fav[idx].max()), "mean_predicted": float(p_fav[idx].mean()),
                     "observed_rate": float(y_fav[idx].mean())})
    return pd.DataFrame(rows, columns=["bin", "n", "p_fav_min", "p_fav_max", "mean_predicted", "observed_rate"]), \
        int((~keep).sum())
