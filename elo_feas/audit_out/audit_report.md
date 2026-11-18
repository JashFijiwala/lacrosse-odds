# Methodological audit of the Elo feasibility experiment

**diagnostic, no re-selection.** Read-only audit of `elo_feas/out/`. Nothing here changes K = 40, lam = 1.0, h = 0, widens the grid, or reruns the 2025 test. Not a model comparison and not a result. The underlying experiment remains: feasibility experiment — pipeline check, not a research result.

## Scope and guards
- Hash check over 62 files before and after: NO DIFFERENCES (hash_diff.txt).
- Guard self-test: write outside audit_out (builtins.open): PASS; write outside audit_out (pathlib): PASS; write outside audit_out (os.open): PASS; read raw 2025 file name: PASS; read raw 2026 file name (pathlib): PASS
- 2025 information comes only from `elo_feas/out/predictions.csv`, `canonical_games_2022_2026.csv` (2026 rows dropped on load), `reliability_2025.csv`, `metrics_by_season.csv` and the two JSON files in `elo_feas/out/`. Elo is recomputed only on `selection_games()` (raw 2022-2024 files, max season asserted <= 2024). `evaluate_2025` and `games_2022_2025` were replaced by raisers in memory.
- Every path opened through Python file APIs is in files_opened.txt. Module imports go through the import system and are not logged there; their sources are covered by the hash check.

## Recorded vs recomputed
110 recorded numbers compared; **9 mismatches**.

| item | recorded | recomputed | tolerance | match |
|---|---|---|---|---|
| teams present at both 2024 and 2025 starts | 77 | 75 | 0.00000 | MISMATCH |
| top down #2 team | Lindenwood | Stony Brook | 0.00000 | MISMATCH |
| top down #2 change | -144.50000 | -134.51800 | 0.05000 | MISMATCH |
| top down #3 team | Stony Brook | Wagner | 0.00000 | MISMATCH |
| top down #3 change | -134.50000 | -121.27200 | 0.05000 | MISMATCH |
| top down #4 team | Wagner | Queens (NC) | 0.00000 | MISMATCH |
| top down #4 change | -121.30000 | -106.83700 | 0.05000 | MISMATCH |
| top down #5 team | Queens (NC) | Hampton | 0.00000 | MISMATCH |
| top down #5 change | -106.80000 | -106.02600 | 0.05000 | MISMATCH |

## Integrity checks
| id | check | status | detail |
|---|---|---|---|
| I1 | frozen params_sha256 recomputes | PASS | params_sha256=6cd0ce93f7c282a7fef83aadae832f72c997405317f658ca1cec70e1264c44ab |
| I2 | frozen K/lam/h are the values audited | PASS | K=40.0 lam=1.0 h=0.0 |
| I3 | table_sha256(selection_games()) == frozen table_sha256 | PASS | max season 2024; 9a25bbd49671e6e890215c0d0a919c68b1e9a5ed0e3413dfae75053cc750bebd |
| I4 | canonical_games_2022_2026.csv rows <= 2024 identical to selection_games() | PASS | rows 1674 vs 1674 |
| I5r | no 'result' column in predictions.csv / canonical csv | PASS | columns: season,date,game_key,team_a,team_b,R_a_before,R_b_before,p_a,y_a,sidedness,v,label |
| I5R | no 'Result' column in predictions.csv / canonical csv | PASS | columns: season,date,game_key,team_a,team_b,R_a_before,R_b_before,p_a,y_a,sidedness,v,label |
| I6 | predictions.csv 2022-2024 rows == fresh run on selection_games() (K=40, lam=1, h=0) | PASS | keys/outcomes identical=True; max |diff| R/p = 4.55e-13 |
| I7 | predictions.csv 2022-2025 games, y_a (from goals), v, sidedness agree with canonical csv | PASS | pred rows 2083, canonical<=2025 rows 2083, unmatched 0 |
| I8 | p_a == E(R_a_before, R_b_before) with h=0 on every row (incl. 2025) | PASS | max |diff| = 7.77e-16 |
| I9 | no team appears twice on one date | PASS | duplicates 0 |
| I10 | first 2025 pre-game rating == shrink(end-2024 rating from selection run); 2025 newcomers at 1500 | PASS | returning teams 75, max |diff| 2.27e-13; newcomers ['Iona', 'Mercyhurst'] |
| I11 | frozen_at_utc < marker completed_at_utc; mtime(frozen) <= mtime(predictions) <= mtime(marker) | PASS | frozen_at 2026-11-11T02:44:39+00:00; marker completed 2026-11-11T02:44:41+00:00; mtime order ok=True |
| I12 | 2022 is warm-up: no 2022 rows in metrics_by_season.csv, no 2022 metric columns in grid | PASS |  |
| I13 | marker status complete with 409 games; params_sha256 matches frozen | PASS | n_2025 in predictions.csv 409 |
| I14 | one update per game overall (2022-2025 game count == recorded updates) | PASS | 2083 games |
| R1 | grid recomputed from selection_games() == grid_2024.csv | PASS | max |diff| 8.33e-17; ranks identical |
| R2 | h sensitivity recomputed == sensitivity_h_2024.csv | PASS | max |diff| 8.33e-17 |
| R3 | metrics recomputed from predictions.csv == metrics_by_season.csv | PASS | rows 6; max |diff| 1.39e-16 |
| R4.2024 | reliability 2024 recomputed from predictions.csv == reliability_2024.csv | PASS | max |diff| 1.11e-16; p=0.5 excluded 0 |
| R4.2025 | reliability 2025 recomputed from predictions.csv == reliability_2025.csv | PASS | max |diff| 0; p=0.5 excluded 0 |
| D0 | with lam = 1 the 2024->2025 start change equals the in-season 2024 change (no 2025 data involved) | INFO | teams in Chunk 4 set 77; teams with games in both seasons 75; Chunk 4 set teams lacking a 2024 or 2025 game: ['Lindenwood', 'St. Andrews', 'Wabash']; max |start_2025 - end-2024 rating| 0 |
| D1 | 2024 Brier strictly decreasing in K at every lam, and in lam at every K (recorded claim: monotone) | INFO: monotone on both axes | last step K 30->40 at lam=1: -0.00658; last step lam 0.85->1.0 at K=40: -0.00406 |

## D0 Rating-change lists under both definitions
| list | team | start_2024 | start_2025 | change | games_2024 | games_2025 |
|---|---|---|---|---|---|---|
| top up, Chunk 4 definition | Sacred Heart | 1355.7 | 1520.8 | 165.1 | 18 | 11 |
| top up, Chunk 4 definition | Notre Dame | 1780.4 | 1940.1 | 159.6 | 17 | 8 |
| top up, Chunk 4 definition | Syracuse | 1512.6 | 1669.2 | 156.6 | 18 | 11 |
| top up, Chunk 4 definition | Towson | 1479.6 | 1628.7 | 149.1 | 17 | 11 |
| top up, Chunk 4 definition | UAlbany | 1435.5 | 1547.3 | 111.8 | 18 | 12 |
| top down, Chunk 4 definition | Mount St. Mary's | 1441.8 | 1239.5 | -202.3 | 15 | 11 |
| top down, Chunk 4 definition | Lindenwood | 1353.8 | 1209.3 | -144.5 | 12 | 0 |
| top down, Chunk 4 definition | Stony Brook | 1542.4 | 1407.8 | -134.5 | 14 | 11 |
| top down, Chunk 4 definition | Wagner | 1289.0 | 1167.7 | -121.3 | 15 | 11 |
| top down, Chunk 4 definition | Queens (NC) | 1336.6 | 1229.7 | -106.8 | 13 | 11 |
| top up, played both seasons | Sacred Heart | 1355.7 | 1520.8 | 165.1 | 18 | 11 |
| top up, played both seasons | Notre Dame | 1780.4 | 1940.1 | 159.6 | 17 | 8 |
| top up, played both seasons | Syracuse | 1512.6 | 1669.2 | 156.6 | 18 | 11 |
| top up, played both seasons | Towson | 1479.6 | 1628.7 | 149.1 | 17 | 11 |
| top up, played both seasons | UAlbany | 1435.5 | 1547.3 | 111.8 | 18 | 12 |
| top down, played both seasons | Mount St. Mary's | 1441.8 | 1239.5 | -202.3 | 15 | 11 |
| top down, played both seasons | Stony Brook | 1542.4 | 1407.8 | -134.5 | 14 | 11 |
| top down, played both seasons | Wagner | 1289.0 | 1167.7 | -121.3 | 15 | 11 |
| top down, played both seasons | Queens (NC) | 1336.6 | 1229.7 | -106.8 | 13 | 11 |
| top down, played both seasons | Hampton | 1247.9 | 1141.9 | -106.0 | 13 | 12 |
| in Chunk 4 set but no game in 2024 or 2025 | Lindenwood | 1353.8 | 1209.3 | -144.5 | 12 | 0 |
| in Chunk 4 set but no game in 2024 or 2025 | St. Andrews | 1469.4 | 1469.4 | 0.0 | 0 | 0 |
| in Chunk 4 set but no game in 2024 or 2025 | Wabash | 1470.7 | 1470.7 | 0.0 | 0 | 0 |

## D1 Grid shape near the selected corner (2024 Brier; no widening)
| axis | fixed | from | to | brier_2024_from | brier_2024_to | delta |
|---|---|---|---|---|---|---|
| K | lam=1.0 | 10.00000 | 15.00000 | 0.21468 | 0.20466 | -0.01002 |
| K | lam=1.0 | 15.00000 | 20.00000 | 0.20466 | 0.19723 | -0.00743 |
| K | lam=1.0 | 20.00000 | 30.00000 | 0.19723 | 0.18698 | -0.01025 |
| K | lam=1.0 | 30.00000 | 40.00000 | 0.18698 | 0.18041 | -0.00658 |
| lam | K=40 | 0.50000 | 0.70000 | 0.19830 | 0.18976 | -0.00854 |
| lam | K=40 | 0.70000 | 0.85000 | 0.18976 | 0.18446 | -0.00529 |
| lam | K=40 | 0.85000 | 1.00000 | 0.18446 | 0.18041 | -0.00406 |

## D2 Cell each criterion would pick (diagnostic only; selection stays K=40, lam=1)
| criterion | argmin_K | argmin_lam | value | same_as_frozen |
|---|---|---|---|---|
| brier_2024 | 40 | 1.00000 | 0.18041 | True |
| log_loss_2024 | 40 | 1.00000 | 0.53997 | True |
| brier_2024_two_sided | 40 | 1.00000 | 0.18008 | True |
| log_loss_2024_two_sided | 40 | 1.00000 | 0.53929 | True |
| brier_2023 | 40 | 1.00000 | 0.19902 | True |
| log_loss_2023 | 40 | 1.00000 | 0.58388 | True |

## D3 Paired bootstrap of Brier differences (2000 game-level resamples, seed 20261111)
Games share teams and dates, so game-level resampling understates uncertainty; read as rough.
| comparison | season | subset | n | mean_diff | ci95_lo | ci95_hi | note |
|---|---|---|---|---|---|---|---|
| Elo(K=40,lam=1) minus constant 0.5, Brier | 2023 | ALL | 561 | -0.05098 | -0.06213 | -0.03838 | in-sample for K/lam |
| Elo(K=40,lam=1) minus constant 0.5, Brier | 2023 | TWO_SIDED | 546 | -0.05244 | -0.06307 | -0.04055 | in-sample for K/lam |
| Elo(K=40,lam=1) minus constant 0.5, Brier | 2024 | ALL | 570 | -0.06959 | -0.08122 | -0.05707 | in-sample for K/lam |
| Elo(K=40,lam=1) minus constant 0.5, Brier | 2024 | TWO_SIDED | 548 | -0.06992 | -0.08264 | -0.05813 | in-sample for K/lam |
| Elo(K=40,lam=1) minus constant 0.5, Brier | 2025 | ALL | 409 | -0.06255 | -0.07775 | -0.04565 | out of sample (predictions.csv, not rerun) |
| Elo(K=40,lam=1) minus constant 0.5, Brier | 2025 | TWO_SIDED | 367 | -0.06396 | -0.08105 | -0.04666 | out of sample (predictions.csv, not rerun) |
| K=40,lam=1 minus K=40,lam=0.85, Brier | 2024 | ALL | 570 | -0.00406 | -0.00571 | -0.00235 | selection season (in-sample); recomputed on selection_games() |
| K=40,lam=1 minus K=30,lam=1, Brier | 2024 | ALL | 570 | -0.00658 | -0.00829 | -0.00483 | selection season (in-sample); recomputed on selection_games() |
| K=40,lam=1 minus K=40,lam=0.7, Brier | 2024 | ALL | 570 | -0.00935 | -0.01272 | -0.00606 | selection season (in-sample); recomputed on selection_games() |
| K=40,lam=1 minus K=30,lam=0.85, Brier | 2024 | ALL | 570 | -0.01081 | -0.01357 | -0.00807 | selection season (in-sample); recomputed on selection_games() |

## D4 Calibration (no-intercept logistic slope on logit p; 500 resamples for the interval)
| season | n | mean_p_fav | favourite_win_rate | fav_rate_minus_mean_p | calibration_slope | slope_ci95_lo | slope_ci95_hi | reading |
|---|---|---|---|---|---|---|---|---|
| 2023 | 561 | 0.64605 | 0.70232 | 0.05627 | 1.48546 | 1.22721 | 1.81863 | slope > 1: forecasts too close to 0.5; slope < 1: too extreme |
| 2024 | 570 | 0.67116 | 0.73158 | 0.06042 | 1.57082 | 1.31888 | 1.87488 | slope > 1: forecasts too close to 0.5; slope < 1: too extreme |
| 2025 | 409 | 0.68616 | 0.72616 | 0.04000 | 1.22553 | 0.98009 | 1.53981 | slope > 1: forecasts too close to 0.5; slope < 1: too extreme |

## D5 Single-sided games
| season | subset | n | share | brier | log_loss | mean_abs_rating_gap |
|---|---|---|---|---|---|---|
| 2023 | ALL | 561 | 1.00000 | 0.19902 | 0.58388 | 110.56542 |
| 2023 | TWO_SIDED | 546 | 0.97326 | 0.19756 | 0.58063 | 110.82769 |
| 2023 | SINGLE_SIDED | 15 | 0.02674 | 0.25216 | 0.70199 | 101.01880 |
| 2024 | ALL | 570 | 1.00000 | 0.18041 | 0.53997 | 134.80341 |
| 2024 | TWO_SIDED | 548 | 0.96140 | 0.18008 | 0.53929 | 134.16571 |
| 2024 | SINGLE_SIDED | 22 | 0.03860 | 0.18843 | 0.55689 | 150.68800 |
| 2025 | ALL | 409 | 1.00000 | 0.18745 | 0.55580 | 150.10325 |
| 2025 | TWO_SIDED | 367 | 0.89731 | 0.18604 | 0.55196 | 151.98074 |
| 2025 | SINGLE_SIDED | 42 | 0.10269 | 0.19979 | 0.58930 | 133.69760 |

## D6 Brier by the smaller prior-game count of the two teams (all seasons counted from 2022)
| season | min_prior_games_bucket | n | brier | single_sided_in_bucket |
|---|---|---|---|---|
| 2023 | 0-4 | 12 | 0.33548 | 2 |
| 2023 | 5-9 | 9 | 0.16284 | 0 |
| 2023 | 10-19 | 228 | 0.19754 | 7 |
| 2023 | 20+ | 312 | 0.19589 | 6 |
| 2024 | 0-4 | 5 | 0.22673 | 5 |
| 2024 | 5-9 | 5 | 0.25306 | 5 |
| 2024 | 10-19 | 13 | 0.15827 | 0 |
| 2024 | 20+ | 547 | 0.17984 | 12 |
| 2025 | 0-4 | 10 | 0.35015 | 10 |
| 2025 | 5-9 | 10 | 0.13712 | 10 |
| 2025 | 10-19 | 12 | 0.18378 | 12 |
| 2025 | 20+ | 377 | 0.18458 | 10 |

## D7 Home/Away flag pairs in the canonical table (results-file flags)
| season | sidedness | pair | n | share_of_season |
|---|---|---|---|---|
| 2022 | TWO_SIDED | Away/Away | 33 | 0.06077 |
| 2022 | TWO_SIDED | Away/Home | 267 | 0.49171 |
| 2022 | TWO_SIDED | Home/Away | 243 | 0.44751 |
| 2023 | SINGLE_SIDED | -/Away | 3 | 0.00535 |
| 2023 | SINGLE_SIDED | -/Home | 5 | 0.00891 |
| 2023 | SINGLE_SIDED | Away/- | 4 | 0.00713 |
| 2023 | SINGLE_SIDED | Home/- | 3 | 0.00535 |
| 2023 | TWO_SIDED | Away/Away | 38 | 0.06774 |
| 2023 | TWO_SIDED | Away/Home | 267 | 0.47594 |
| 2023 | TWO_SIDED | Home/Away | 241 | 0.42959 |
| 2024 | SINGLE_SIDED | -/Away | 5 | 0.00877 |
| 2024 | SINGLE_SIDED | -/Home | 9 | 0.01579 |
| 2024 | SINGLE_SIDED | Away/- | 4 | 0.00702 |
| 2024 | SINGLE_SIDED | Home/- | 4 | 0.00702 |
| 2024 | TWO_SIDED | Away/Away | 46 | 0.08070 |
| 2024 | TWO_SIDED | Away/Home | 255 | 0.44737 |
| 2024 | TWO_SIDED | Home/Away | 247 | 0.43333 |
| 2025 | SINGLE_SIDED | -/Away | 13 | 0.03178 |
| 2025 | SINGLE_SIDED | -/Home | 13 | 0.03178 |
| 2025 | SINGLE_SIDED | Away/- | 10 | 0.02445 |
| 2025 | SINGLE_SIDED | Home/- | 6 | 0.01467 |
| 2025 | TWO_SIDED | Away/Away | 9 | 0.02200 |
| 2025 | TWO_SIDED | Away/Home | 177 | 0.43276 |
| 2025 | TWO_SIDED | Home/Away | 180 | 0.44010 |
| 2025 | TWO_SIDED | Home/Home | 1 | 0.00244 |

## D8 Calendar coverage
| season | month | games | first_date | last_date | n_dates |
|---|---|---|---|---|---|
| 2022 | 01 | 1 | 2022-01-29 | 2022-05-30 | 78 |
| 2022 | 02 | 132 | 2022-01-29 | 2022-05-30 | 78 |
| 2022 | 03 | 171 | 2022-01-29 | 2022-05-30 | 78 |
| 2022 | 04 | 187 | 2022-01-29 | 2022-05-30 | 78 |
| 2022 | 05 | 52 | 2022-01-29 | 2022-05-30 | 78 |
| 2023 | 02 | 136 | 2023-02-04 | 2023-05-29 | 71 |
| 2023 | 03 | 181 | 2023-02-04 | 2023-05-29 | 71 |
| 2023 | 04 | 195 | 2023-02-04 | 2023-05-29 | 71 |
| 2023 | 05 | 49 | 2023-02-04 | 2023-05-29 | 71 |
| 2024 | 02 | 148 | 2024-02-03 | 2024-05-27 | 75 |
| 2024 | 03 | 212 | 2024-02-03 | 2024-05-27 | 75 |
| 2024 | 04 | 164 | 2024-02-03 | 2024-05-27 | 75 |
| 2024 | 05 | 46 | 2024-02-03 | 2024-05-27 | 75 |
| 2025 | 02 | 159 | 2025-02-01 | 2025-04-08 | 48 |
| 2025 | 03 | 208 | 2025-02-01 | 2025-04-08 | 48 |
| 2025 | 04 | 42 | 2025-02-01 | 2025-04-08 | 48 |

_diagnostic, no re-selection_
