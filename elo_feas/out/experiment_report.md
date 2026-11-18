# Elo feasibility experiment report

**feasibility experiment — pipeline check, not a research result.** This checks that the pipeline runs end to end without leakage. It is not a model comparison, not a betting signal and not evidence about lacrosse. All numbers below are computed by `elo_feas/run_experiment.py`.

## Setup

- Results repo pin `4ac0d2e5109de526120785a6bf16a50d4673b56e`; canonical games from `elo_feas.data_layer` (join_audit functions unchanged).
- Engine: R_bar = 1500 constant; newcomers at R_bar; date batching; season shrink R <- R_bar + lam (R - R_bar); margin, overtime and venue unused in the primary model (h = 0).
- Selection on 2022-2024 only; 2022 is warm-up and excluded from every metric; select by mean Brier on all 2024 games (log loss breaks ties). 2025 is predicted once with frozen parameters. 2026 is not used.
- The results file's outcome-label column is never loaded; outcomes come from goals. No odds data is used.

## Order of operations (UTC)

| time | step |
|---|---|
| 2026-11-11T02:44:37+00:00 | building canonical game table for 2022-2024 (2025 file not read) |
| 2026-11-11T02:44:37+00:00 | grid K=(10, 15, 20, 30, 40) x lam=(0.5, 0.7, 0.85, 1.0), h=0, scored on 2024; 2022 warm-up |
| 2026-11-11T02:44:40+00:00 | frozen_params.json written (K=40.0, lam=1.0, table_sha256=9a25bbd49671...); sensitivity_h_2024.csv written |
| 2026-11-11T02:44:40+00:00 | evaluate_2025: first read of the 2025 file in this run; hash check; predictions |
| 2026-11-11T02:44:41+00:00 | 2025 test complete: 409 games predicted |
| 2026-11-11T02:44:41+00:00 | metrics_by_season.csv, reliability_2024.csv written (reliability_2025.csv written by evaluate_2025) |
| 2026-11-11T02:44:52+00:00 | audits: ALL PASS |

frozen_params.json `frozen_at_utc` = 2026-11-11T02:44:39+00:00; 2025 marker written only after the frozen file and table hash were verified.

## Games per season

| season | games | two-sided | single-sided |
|---|---|---|---|
| 2022 | 543 | 543 | 0 |
| 2023 | 561 | 546 | 15 |
| 2024 | 570 | 548 | 22 |
| 2025 | 409 | 367 | 42 |

## Newcomer teams per season (first game in the data)

| season | newcomers | names |
|---|---|---|
| 2022 | 73 | (all teams: cold start) |
| 2023 | 4 | Lindenwood, Queens (NC), St. Andrews, Wabash |
| 2024 | 1 | Le Moyne |
| 2025 | 2 | Iona, Mercyhurst |

## Grid (2024 selection metrics; h = 0)

| K | lam | n 2024 | Brier 2024 | log loss 2024 | Brier 2024 two-sided | log loss 2024 two-sided | rank |
|---|---|---|---|---|---|---|---|
| 10 | 0.5 | 570 | 0.22820 | 0.64916 | 0.22810 | 0.64897 | 20 |
| 10 | 0.7 | 570 | 0.22268 | 0.63777 | 0.22249 | 0.63738 | 19 |
| 10 | 0.85 | 570 | 0.21858 | 0.62914 | 0.21832 | 0.62858 | 17 |
| 10 | 1.0 | 570 | 0.21468 | 0.62072 | 0.21433 | 0.61997 | 16 |
| 15 | 0.5 | 570 | 0.22046 | 0.63310 | 0.22037 | 0.63291 | 18 |
| 15 | 0.7 | 570 | 0.21371 | 0.61881 | 0.21351 | 0.61838 | 14 |
| 15 | 0.85 | 570 | 0.20894 | 0.60837 | 0.20865 | 0.60773 | 13 |
| 15 | 1.0 | 570 | 0.20466 | 0.59862 | 0.20427 | 0.59775 | 10 |
| 20 | 0.5 | 570 | 0.21419 | 0.61976 | 0.21412 | 0.61960 | 15 |
| 20 | 0.7 | 570 | 0.20668 | 0.60348 | 0.20649 | 0.60306 | 12 |
| 20 | 0.85 | 570 | 0.20158 | 0.59192 | 0.20130 | 0.59128 | 9 |
| 20 | 1.0 | 570 | 0.19723 | 0.58150 | 0.19683 | 0.58059 | 7 |
| 30 | 0.5 | 570 | 0.20480 | 0.59903 | 0.20480 | 0.59900 | 11 |
| 30 | 0.7 | 570 | 0.19653 | 0.58034 | 0.19641 | 0.58005 | 6 |
| 30 | 0.85 | 570 | 0.19122 | 0.56762 | 0.19098 | 0.56708 | 5 |
| 30 | 1.0 | 570 | 0.18698 | 0.55671 | 0.18661 | 0.55587 | 3 |
| 40 | 0.5 | 570 | 0.19830 | 0.58390 | 0.19837 | 0.58405 | 8 |
| 40 | 0.7 | 570 | 0.18976 | 0.56401 | 0.18971 | 0.56391 | 4 |
| 40 | 0.85 | 570 | 0.18446 | 0.55084 | 0.18429 | 0.55049 | 2 |
| 40 | 1.0 | 570 | 0.18041 | 0.53997 | 0.18008 | 0.53929 | 1 |

Selected: **K = 40, lam = 1** (Brier 2024 0.18041, log loss 0.53997). On grid edge: **YES** (K=40.0 is on the grid edge 10/40; lam=1.0 is on the grid edge 0.5/1.0).
Spread of 2024 Brier across the 20 cells: 0.18041 to 0.22820.

## Metrics by season (frozen parameters; Elo vs constant 0.5)

| season | subset | n | Brier Elo | Brier 0.5 | diff | log loss Elo | log loss 0.5 | diff |
|---|---|---|---|---|---|---|---|---|
| 2023 | ALL | 561 | 0.1990 | 0.2500 | -0.0510 | 0.5839 | 0.6931 | -0.1093 |
| 2023 | TWO_SIDED | 546 | 0.1976 | 0.2500 | -0.0524 | 0.5806 | 0.6931 | -0.1125 |
| 2024 | ALL | 570 | 0.1804 | 0.2500 | -0.0696 | 0.5400 | 0.6931 | -0.1532 |
| 2024 | TWO_SIDED | 548 | 0.1801 | 0.2500 | -0.0699 | 0.5393 | 0.6931 | -0.1539 |
| 2025 | ALL | 409 | 0.1874 | 0.2500 | -0.0626 | 0.5558 | 0.6931 | -0.1374 |
| 2025 | TWO_SIDED | 367 | 0.1860 | 0.2500 | -0.0640 | 0.5520 | 0.6931 | -0.1412 |

2023 and 2024 are in-sample for the K/lam choice (2024 is the selection season); only 2025 is out of sample.

## Reliability 2024 (favourite orientation, 5 equal-count bins; 0 games with p = 0.5 excluded)

| bin | n | p_fav min | p_fav max | mean predicted | observed rate |
|---|---|---|---|---|---|
| 1 | 114 | 0.500 | 0.562 | 0.5283 | 0.5614 |
| 2 | 114 | 0.563 | 0.625 | 0.5964 | 0.5614 |
| 3 | 114 | 0.625 | 0.692 | 0.6597 | 0.7807 |
| 4 | 114 | 0.692 | 0.779 | 0.7299 | 0.8421 |
| 5 | 114 | 0.781 | 0.948 | 0.8415 | 0.9123 |

## Reliability 2025 (favourite orientation, 5 equal-count bins; 0 games with p = 0.5 excluded)

| bin | n | p_fav min | p_fav max | mean predicted | observed rate |
|---|---|---|---|---|---|
| 1 | 82 | 0.500 | 0.569 | 0.5335 | 0.5732 |
| 2 | 82 | 0.569 | 0.642 | 0.6077 | 0.6220 |
| 3 | 82 | 0.642 | 0.709 | 0.6768 | 0.7561 |
| 4 | 82 | 0.710 | 0.799 | 0.7522 | 0.7805 |
| 5 | 81 | 0.801 | 0.962 | 0.8628 | 0.9012 |

## SENSITIVITY ONLY: home term h (not used for selection; frozen K, lam; 2024)

Uses the results file's Home/Away flags (v = +1 Home/Away, -1 Away/Home, 0 otherwise). Never the odds feed's home_team.

| h | Brier 2024 | log loss 2024 | Brier 2024 two-sided | log loss 2024 two-sided |
|---|---|---|---|---|
| 0 | 0.18041 | 0.53997 | 0.18008 | 0.53929 |
| 25 | 0.17979 | 0.53879 | 0.17943 | 0.53802 |
| 50 | 0.18109 | 0.54154 | 0.18076 | 0.54083 |
| 75 | 0.18421 | 0.54816 | 0.18398 | 0.54765 |

## Largest rating changes between the 2024 and 2025 season starts (after shrink; 77 teams present at both)

Top 5 up:

| team | start 2024 | start 2025 | change |
|---|---|---|---|
| Sacred Heart | 1355.7 | 1520.8 | 165.1 |
| Notre Dame | 1780.4 | 1940.1 | 159.6 |
| Syracuse | 1512.6 | 1669.2 | 156.6 |
| Towson | 1479.6 | 1628.7 | 149.1 |
| UAlbany | 1435.5 | 1547.3 | 111.8 |

Top 5 down:

| team | start 2024 | start 2025 | change |
|---|---|---|---|
| Mount St. Mary's | 1441.8 | 1239.5 | -202.3 |
| Lindenwood | 1353.8 | 1209.3 | -144.5 |
| Stony Brook | 1542.4 | 1407.8 | -134.5 |
| Wagner | 1289.0 | 1167.7 | -121.3 |
| Queens (NC) | 1336.6 | 1229.7 | -106.8 |

Start ratings were cross-checked against each team's stored pre-game rating at its first game of the season.

## Audits on the real 2022-2025 data (frozen parameters)

| audit | pass | details |
|---|---|---|
| orientation invariance (h=0) | PASS | n_flipped=1080, max_rating_diff=2.2737367544323206e-13, max_prob_diff=6.661338147750939e-16, same_teams=True |
| orientation invariance (h=50, sign of v) | PASS | n_flipped=1039, max_rating_diff=2.2737367544323206e-13, max_prob_diff=6.661338147750939e-16, same_teams=True |
| pre-game ratings = from-scratch (200 random + 3 dates) | PASS | n_checked=256, n_dates=109 |
| flip all outcomes >= first 2025 date | PASS | date=2025-02-01, n_flipped=409, n_compared=1691, unchanged=True, later_changed=True |
| flip all outcomes >= mid-2025 date | PASS | date=2025-03-06, n_flipped=207, n_compared=1877, unchanged=True, later_changed=True |
| flip one game on first 2025 date | PASS | date=2025-02-01, flipped_game=2025-02-01|Bucknell|Merrimack, n_compared=16 |
| conservation / one update per game | PASS | n_updates=2083, n_games=2083, each_key_once=True, max_abs_sum=2.2737367544323206e-13 |
| newcomers at 1500 / one shrink per boundary | PASS | newcomers_at_r_bar=True, boundaries_got=[(2023, '2023-02-04'), (2024, '2024-02-03'), (2025, '2025-02-01')], shrink_covers_all_existing=True |

## Caveats (feasibility only)

- 2025 has 409 games (42 single-sided, against teams with no own rows); the file covers a shorter slice of the season than 2022-2024 (see data_layer_report.md, games per team).
- One season of out-of-sample games is small; no confidence intervals were computed and no claim is made.
- 2023 and 2024 metrics are not out of sample. Single-sided games use the same engine; teams with no own rows get ratings only from those games.
- The 2025 and 2026 Home/Away flags behave differently from 2022-2024 (data_layer_report.md); this only matters for the h sensitivity, which uses 2024.
- feasibility experiment — pipeline check, not a research result.
