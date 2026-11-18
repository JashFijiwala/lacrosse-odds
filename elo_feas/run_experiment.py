"""Chunk 4 runner: Elo feasibility experiment. Feasibility experiment - pipeline check, not a research result.

Order (enforced by audit_elo): 2022-2024 table -> grid -> frozen_params.json -> sensitivity -> 2025 test (once)
-> derived tables, audits and report from the stored predictions. No parameter is changed after 2025.

    python -m elo_feas.run_experiment
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pandas as pd  # noqa: E402

from audit_join import join_audit as J  # noqa: E402
from elo_feas import audit_elo as A  # noqa: E402
from elo_feas import data_layer as D  # noqa: E402
from elo_feas import elo as E  # noqa: E402
from elo_feas import metrics as M  # noqa: E402

OUT = D.OUT_DIR
LABEL = A.LABEL
REPORT_SEASONS = (2023, 2024, 2025)
ALL_SEASONS = A.SELECTION_SEASONS + (A.TEST_SEASON,)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _f(x, nd=4) -> str:
    return f"{x:.{nd}f}"


def season_start_ratings(run: E.EloRun, eng: pd.DataFrame, lam: float, r_bar: float) -> dict:
    """season -> {team: rating in force at that season's first date (after the shrink)} for existing teams."""
    starts = eng.groupby("season")["date"].min().sort_index()
    dates = sorted(run.history)
    out = {}
    for s, d0 in starts.items():
        prev = [d for d in dates if d < d0]
        out[int(s)] = {} if not prev else {t: E.shrink(r, lam, r_bar) for t, r in run.history[prev[-1]].items()}
    return out


def main() -> int:
    log = []

    def step(msg):
        log.append((_now(), msg))
        print(f"[{log[-1][0]}] {msg}")

    for name in (A.FROZEN_NAME, A.MARKER_NAME, "predictions.csv"):
        if (OUT / name).exists():
            raise RuntimeError(f"{OUT / name} already exists; the experiment has already been run")
    D.setup_guards()
    J.verify_pin()

    # 1. selection on 2022-2024 only
    step("building canonical game table for 2022-2024 (2025 file not read)")
    sel_games = A.selection_games()
    step(f"grid K={A.K_GRID} x lam={A.LAM_GRID}, h=0, scored on 2024; 2022 warm-up")
    sel = A.run_selection(OUT, sel_games)
    fz = sel["frozen"]
    step(f"frozen_params.json written (K={fz['K']}, lam={fz['lam']}, table_sha256={fz['table_sha256'][:12]}...); "
         f"sensitivity_h_2024.csv written")
    assert (OUT / A.FROZEN_NAME).exists() and not (OUT / A.MARKER_NAME).exists()

    # 2. the 2025 test, once
    step("evaluate_2025: first read of the 2025 file in this run; hash check; predictions")
    ev = A.evaluate_2025(OUT)
    step(f"2025 test complete: {int((ev['run'].predictions.season == 2025).sum())} games predicted")

    # 3. derived tables (no parameter changes from here on)
    k, lam, h, rbar = fz["K"], fz["lam"], fz["h"], fz["R_bar"]
    games = A.games_2022_2025()
    assert A.table_sha256(games[games.season.isin(A.SELECTION_SEASONS)]) == fz["table_sha256"]
    eng = E.from_canonical(games)
    pred = ev["run"].predictions
    run = E.run_elo(eng, k, lam, h, rbar, keep_history=True)
    pd.testing.assert_frame_equal(run.predictions, pred)

    met = M.season_metrics(pred[pred.season.isin(REPORT_SEASONS)])
    met["brier_minus_const"] = met["brier"] - met["brier_const"]
    met["log_loss_minus_const"] = met["log_loss"] - met["log_loss_const"]
    met.assign(label=LABEL).to_csv(OUT / "metrics_by_season.csv", index=False)
    rel = {}
    for s in (2024, 2025):
        p = pred[pred.season == s]
        rel[s] = M.reliability_table(p.p_a, p.y_a)
    rel[2024][0].assign(label=LABEL).to_csv(OUT / "reliability_2024.csv", index=False)
    pd.testing.assert_frame_equal(rel[2025][0], ev["reliability"])
    step("metrics_by_season.csv, reliability_2024.csv written (reliability_2025.csv written by evaluate_2025)")

    # counts, newcomers, rating changes
    counts = [[s, int((games.season == s).sum()), int(((games.season == s) & (games.sidedness == "TWO_SIDED")).sum()),
               int(((games.season == s) & (games.sidedness == "SINGLE_SIDED")).sum())] for s in ALL_SEASONS]
    date_season = dict(zip(eng.date, eng.season))
    newc = pd.Series({t: date_season[d] for t, d in run.first_seen.items()})
    newcomers = {s: sorted(newc[newc == s].index) for s in ALL_SEASONS}
    starts = season_start_ratings(run, eng, lam, rbar)
    for s in (2024, 2025):                      # cross-check against the stored pre-game ratings
        p = pred[pred.season == s]
        firsts = pd.concat([p[["date", "team_a", "R_a_before"]].set_axis(["date", "team", "R"], axis=1),
                            p[["date", "team_b", "R_b_before"]].set_axis(["date", "team", "R"], axis=1)]) \
            .sort_values("date", kind="mergesort").drop_duplicates("team")
        for r in firsts.itertuples():
            assert r.R == starts[s].get(r.team, rbar), (s, r.team)
    common = sorted(set(starts[2024]) & set(starts[2025]))
    chg = pd.DataFrame({"team": common, "start_2024": [starts[2024][t] for t in common],
                        "start_2025": [starts[2025][t] for t in common]})
    chg["change"] = chg.start_2025 - chg.start_2024
    up = chg.sort_values(["change", "team"], ascending=[False, True]).head(5)
    down = chg.sort_values(["change", "team"], ascending=[True, True]).head(5)

    # audits on the real 2022-2025 engine frame with the frozen parameters
    d25 = sorted(eng[eng.season == 2025].date.unique())
    d24 = sorted(eng[eng.season == 2024].date.unique())
    audits = [
        ("orientation invariance (h=0)", A.audit_orientation(eng, k, lam, 0.0, seed=11)),
        ("orientation invariance (h=50, sign of v)", A.audit_orientation(eng, k, lam, 50.0, seed=12)),
        ("pre-game ratings = from-scratch (200 random + 3 dates)",
         A.audit_leakage_scratch(eng, k, lam, h, 200, dates=(d24[0], d24[len(d24) // 2], d25[0]), seed=13, r_bar=rbar)),
        ("flip all outcomes >= first 2025 date", A.audit_flip_future(eng, k, lam, h, d25[0])),
        ("flip all outcomes >= mid-2025 date", A.audit_flip_future(eng, k, lam, h, d25[len(d25) // 2])),
        ("flip one game on first 2025 date", A.audit_flip_one(eng, k, lam, h, d25[0], seed=14)),
        ("conservation / one update per game", A.audit_conservation(run, len(eng))),
        ("newcomers at 1500 / one shrink per boundary", A.audit_newcomers_and_shrink(run, eng, rbar)),
    ]
    all_pass = all(a["pass"] for _, a in audits)
    step(f"audits: {'ALL PASS' if all_pass else 'FAILURES'}")

    # report
    md = D._md
    g = sel["grid"].sort_values(["K", "lam"])
    best = sel["best"]
    L = [f"# Elo feasibility experiment report", "",
         f"**{LABEL}.** This checks that the pipeline runs end to end without leakage. It is not a model comparison, "
         "not a betting signal and not evidence about lacrosse. All numbers below are computed by "
         "`elo_feas/run_experiment.py`.", "",
         "## Setup", "",
         f"- Results repo pin `{J.PINNED_COMMIT}`; canonical games from `elo_feas.data_layer` (join_audit functions unchanged).",
         f"- Engine: R_bar = {rbar:g} constant; newcomers at R_bar; date batching; season shrink R <- R_bar + lam (R - R_bar); "
         "margin, overtime and venue unused in the primary model (h = 0).",
         "- Selection on 2022-2024 only; 2022 is warm-up and excluded from every metric; select by mean Brier on all 2024 "
         "games (log loss breaks ties). 2025 is predicted once with frozen parameters. 2026 is not used.",
         "- The results file's outcome-label column is never loaded; outcomes come from goals. No odds data is used.", "",
         "## Order of operations (UTC)", "", md(["time", "step"], log), "",
         f"frozen_params.json `frozen_at_utc` = {fz['frozen_at_utc']}; 2025 marker written only after the frozen file "
         "and table hash were verified.", "",
         "## Games per season", "", md(["season", "games", "two-sided", "single-sided"], counts), "",
         "## Newcomer teams per season (first game in the data)", "",
         md(["season", "newcomers", "names"],
            [[s, len(v), ", ".join(v) if s != 2022 else "(all teams: cold start)"] for s, v in newcomers.items()]), "",
         "## Grid (2024 selection metrics; h = 0)", "",
         md(["K", "lam", "n 2024", "Brier 2024", "log loss 2024", "Brier 2024 two-sided", "log loss 2024 two-sided", "rank"],
            [[int(r.K), r.lam, r.n_2024, _f(r.brier_2024, 5), _f(r.log_loss_2024, 5), _f(r.brier_2024_two_sided, 5),
              _f(r.log_loss_2024_two_sided, 5), r.rank] for r in g.itertuples()]), "",
         f"Selected: **K = {best['K']:g}, lam = {best['lam']:g}** (Brier 2024 {_f(best['brier_2024'], 5)}, "
         f"log loss {_f(best['log_loss_2024'], 5)}). On grid edge: **{'YES' if best['on_grid_edge'] else 'no'}**"
         + (f" ({'; '.join(best['edge_reasons'])})." if best["on_grid_edge"] else "."),
         f"Spread of 2024 Brier across the 20 cells: {_f(g.brier_2024.min(), 5)} to {_f(g.brier_2024.max(), 5)}.", "",
         "## Metrics by season (frozen parameters; Elo vs constant 0.5)", "",
         md(["season", "subset", "n", "Brier Elo", "Brier 0.5", "diff", "log loss Elo", "log loss 0.5", "diff"],
            [[r.season, r.subset, r.n, _f(r.brier), _f(r.brier_const), _f(r.brier_minus_const, 4), _f(r.log_loss),
              _f(r.log_loss_const), _f(r.log_loss_minus_const, 4)] for r in met.itertuples()]), "",
         "2023 and 2024 are in-sample for the K/lam choice (2024 is the selection season); only 2025 is out of sample.", ""]
    for s in (2024, 2025):
        t, nh = rel[s]
        L += [f"## Reliability {s} (favourite orientation, 5 equal-count bins; {nh} games with p = 0.5 excluded)", "",
              md(["bin", "n", "p_fav min", "p_fav max", "mean predicted", "observed rate"],
                 [[r.bin, r.n, _f(r.p_fav_min, 3), _f(r.p_fav_max, 3), _f(r.mean_predicted), _f(r.observed_rate)]
                  for r in t.itertuples()]), ""]
    sens = sel["sensitivity"]
    L += ["## SENSITIVITY ONLY: home term h (not used for selection; frozen K, lam; 2024)", "",
          "Uses the results file's Home/Away flags (v = +1 Home/Away, -1 Away/Home, 0 otherwise). Never the odds feed's home_team.", "",
          md(["h", "Brier 2024", "log loss 2024", "Brier 2024 two-sided", "log loss 2024 two-sided"],
             [[r.h, _f(r.brier_2024, 5), _f(r.log_loss_2024, 5), _f(r.brier_2024_two_sided, 5),
               _f(r.log_loss_2024_two_sided, 5)] for r in sens.itertuples()]), "",
          f"## Largest rating changes between the 2024 and 2025 season starts (after shrink; {len(chg)} teams present at both)", "",
          "Top 5 up:", "", md(["team", "start 2024", "start 2025", "change"],
                               [[r.team, _f(r.start_2024, 1), _f(r.start_2025, 1), _f(r.change, 1)] for r in up.itertuples()]), "",
          "Top 5 down:", "", md(["team", "start 2024", "start 2025", "change"],
                                 [[r.team, _f(r.start_2024, 1), _f(r.start_2025, 1), _f(r.change, 1)] for r in down.itertuples()]), "",
          "Start ratings were cross-checked against each team's stored pre-game rating at its first game of the season.", "",
          "## Audits on the real 2022-2025 data (frozen parameters)", "",
          md(["audit", "pass", "details"],
             [[n, "PASS" if a["pass"] else "**FAIL**",
               ", ".join(f"{k2}={v}" for k2, v in a.items() if k2 not in ("pass", "mismatches", "boundaries_expected"))]
              for n, a in audits]), ""]
    two25 = int(((games.season == 2025) & (games.sidedness == "TWO_SIDED")).sum())
    n25 = int((games.season == 2025).sum())
    L += ["## Caveats (feasibility only)", "",
          f"- 2025 has {n25} games ({n25 - two25} single-sided, against teams with no own rows); the file covers a shorter "
          "slice of the season than 2022-2024 (see data_layer_report.md, games per team).",
          "- One season of out-of-sample games is small; no confidence intervals were computed and no claim is made.",
          "- 2023 and 2024 metrics are not out of sample. Single-sided games use the same engine; teams with no own rows "
          "get ratings only from those games.",
          "- The 2025 and 2026 Home/Away flags behave differently from 2022-2024 (data_layer_report.md); this only matters "
          "for the h sensitivity, which uses 2024.",
          f"- {LABEL}.", ""]
    (OUT / "experiment_report.md").write_text("\n".join(L), encoding="utf-8")
    step("experiment_report.md written")
    if not all_pass:
        print("AUDIT FAILURE: see experiment_report.md")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
