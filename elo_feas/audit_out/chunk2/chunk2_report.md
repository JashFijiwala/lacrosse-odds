# Chunk 2 audit of the Elo feasibility experiment

**diagnostic, no re-selection.** Read-only. K = 40, lam = 1, h = 0 unchanged; the grid is not widened; the 2025 test is not rerun. Recomputation: `selection_games()` (2022-2024) through the independent engine `chunk2/indep_elo.py`. 2025: recorded `elo_feas/out/predictions.csv` only. Not a model comparison and not a result; the experiment remains a feasibility experiment — pipeline check, not a research result.

Hash check over 80 files: NO DIFFERENCES (chunk2/hash_diff.txt). Guard self-test: write outside audit_out (builtins.open): PASS; write outside audit_out (pathlib): PASS; write outside audit_out (os.open): PASS; read raw 2025 file name: PASS; read raw 2026 file name (pathlib): PASS; write to audit_out outside chunk2: PASS.

## 2.1 Independent reimplementation

`chunk2/indep_elo.py` imports nothing from `elo_feas` (checked: source scan, and `elo_feas.elo` was not in `sys.modules` after importing it). Same log-loss clipping (1e-6) as the recorded outputs.

- Gate A, grid: 120 metric values (six columns x 20 cells: 2024 Brier/log loss all and two-sided, 2023 Brier/log loss) plus n and rank: max |diff| = 1.11e-15; n and rank identical = True -> **PASS** (tolerance 1e-09).
- Gate B, predictions.csv 2023 + 2024 rows (1131) at K=40, lam=1: keys, teams, outcomes identical = True; max |diff| in R_a_before, R_b_before, p_a = 4.55e-13 -> **PASS**. (2022 rows, not gated: max |diff| 2.27e-13.)

- Recorded `predictions.csv`, all 2083 games 2022-2025 (no recomputation): for each team, R_before at its next appearance == R_before + K(S - p) (team A) or - K(S - p) (team B), with 1500 + lam(R - 1500) applied when the next appearance is in a new season (K=40, lam=1). 4086 successive-appearance checks (223 across a season boundary): max |diff| = 6.82e-13 -> **PASS**. By season of the next appearance: 2022: 1013 checks, max |diff| 4.55e-13; 2023: 1118 checks, max |diff| 4.55e-13; 2024: 1139 checks, max |diff| 6.82e-13; 2025: 816 checks, max |diff| 6.82e-13. First appearance at 1500 for all 80 teams: True.

## 2.2 Grids, calibration slope by cell, reliability bins

### Full grids (2022 warm-up; h = 0; independent engine)
| K | lam | brier_2023 | log_loss_2023 | brier_2024 | log_loss_2024 | rank |
|---|---|---|---|---|---|---|
| 10 | 0.50000 | 0.23248 | 0.65784 | 0.22820 | 0.64916 | 20 |
| 10 | 0.70000 | 0.22957 | 0.65190 | 0.22268 | 0.63777 | 19 |
| 10 | 0.85000 | 0.22754 | 0.64770 | 0.21858 | 0.62914 | 17 |
| 10 | 1.00000 | 0.22563 | 0.64373 | 0.21468 | 0.62072 | 16 |
| 15 | 0.50000 | 0.22617 | 0.64480 | 0.22046 | 0.63310 | 18 |
| 15 | 0.70000 | 0.22257 | 0.63730 | 0.21371 | 0.61881 | 14 |
| 15 | 0.85000 | 0.22014 | 0.63216 | 0.20894 | 0.60837 | 13 |
| 15 | 1.00000 | 0.21794 | 0.62743 | 0.20466 | 0.59862 | 10 |
| 20 | 0.50000 | 0.22103 | 0.63395 | 0.21419 | 0.61976 | 15 |
| 20 | 0.70000 | 0.21702 | 0.62541 | 0.20668 | 0.60348 | 12 |
| 20 | 0.85000 | 0.21441 | 0.61973 | 0.20158 | 0.59192 | 9 |
| 20 | 1.00000 | 0.21212 | 0.61465 | 0.19723 | 0.58150 | 7 |
| 30 | 0.50000 | 0.21333 | 0.61709 | 0.20480 | 0.59903 | 11 |
| 30 | 0.70000 | 0.20895 | 0.60741 | 0.19653 | 0.58034 | 6 |
| 30 | 0.85000 | 0.20628 | 0.60131 | 0.19122 | 0.56762 | 5 |
| 30 | 1.00000 | 0.20410 | 0.59620 | 0.18698 | 0.55671 | 3 |
| 40 | 0.50000 | 0.20799 | 0.60485 | 0.19830 | 0.58390 | 8 |
| 40 | 0.70000 | 0.20353 | 0.59468 | 0.18976 | 0.56401 | 4 |
| 40 | 0.85000 | 0.20097 | 0.58862 | 0.18446 | 0.55084 | 2 |
| 40 | 1.00000 | 0.19902 | 0.58388 | 0.18041 | 0.53997 | 1 |

Spearman rank agreement across the 20 cells, 2023 vs 2024: Brier 0.9835, log loss 0.9835.
2023 Brier argmin: K=40, lam=1; 2023 Brier monotone in K / in lam: (True, True).

### Calibration slope (no-intercept logistic of y on logit p, as D4; > 1 = forecasts too close to 0.5)
2023:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 5.805 | 4.901 | 4.332 | 3.856 |
| 15 | 4.146 | 3.538 | 3.144 | 2.810 |
| 20 | 3.313 | 2.854 | 2.549 | 2.286 |
| 30 | 2.467 | 2.161 | 1.947 | 1.757 |
| 40 | 2.030 | 1.804 | 1.638 | 1.485 |

2024:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 6.470 | 5.066 | 4.136 | 3.372 |
| 15 | 4.683 | 3.773 | 3.136 | 2.595 |
| 20 | 3.774 | 3.113 | 2.629 | 2.203 |
| 30 | 2.834 | 2.425 | 2.099 | 1.796 |
| 40 | 2.336 | 2.051 | 1.808 | 1.571 |

Slope falls as K rises at every lam: 2023 True, 2024 True; as lam rises at every K: 2023 True, 2024 True. Slope > 1 in all 40 cell-seasons: True. Smallest slope: 2023 1.485 at (K=40, lam=1.0), 2024 1.571 at (K=40, lam=1.0). Point estimates only (no intervals per cell).

### Reliability bins at K=40, lam=1 (recorded predictions.csv; favourite orientation; 5 equal-count bins)
binomial_se = sqrt(sum p(1-p))/n, the SE of the observed rate if the forecasts were calibrated; z = (observed - mean_p)/se. Games with p = 0.5 excluded: 2023: 0, 2024: 0, 2025: 0.
| season | bin | n | p_min | p_max | mean_p | observed | obs_minus_p | binomial_se | z |
|---|---|---|---|---|---|---|---|---|---|
| 2023 | 1 | 113 | 0.5002 | 0.5525 | 0.5283 | 0.5398 | 0.0115 | 0.0469 | 0.2456 |
| 2023 | 2 | 112 | 0.5528 | 0.6071 | 0.5793 | 0.5982 | 0.0189 | 0.0466 | 0.4055 |
| 2023 | 3 | 112 | 0.6073 | 0.6635 | 0.6361 | 0.7679 | 0.1317 | 0.0454 | 2.8992 |
| 2023 | 4 | 112 | 0.6638 | 0.7281 | 0.6957 | 0.7500 | 0.0543 | 0.0434 | 1.2493 |
| 2023 | 5 | 112 | 0.7289 | 0.9314 | 0.7918 | 0.8571 | 0.0653 | 0.0381 | 1.7136 |
| 2024 | 1 | 114 | 0.5003 | 0.5624 | 0.5283 | 0.5614 | 0.0331 | 0.0467 | 0.7075 |
| 2024 | 2 | 114 | 0.5625 | 0.6247 | 0.5964 | 0.5614 | -0.0350 | 0.0459 | -0.7621 |
| 2024 | 3 | 114 | 0.6249 | 0.6918 | 0.6597 | 0.7807 | 0.1210 | 0.0443 | 2.7298 |
| 2024 | 4 | 114 | 0.6919 | 0.7794 | 0.7299 | 0.8421 | 0.1122 | 0.0415 | 2.7022 |
| 2024 | 5 | 114 | 0.7812 | 0.9476 | 0.8415 | 0.9123 | 0.0708 | 0.0340 | 2.0843 |
| 2025 | 1 | 82 | 0.5002 | 0.5694 | 0.5335 | 0.5732 | 0.0397 | 0.0550 | 0.7214 |
| 2025 | 2 | 82 | 0.5694 | 0.6422 | 0.6077 | 0.6220 | 0.0143 | 0.0539 | 0.2652 |
| 2025 | 3 | 82 | 0.6423 | 0.7095 | 0.6768 | 0.7561 | 0.0793 | 0.0516 | 1.5362 |
| 2025 | 4 | 82 | 0.7098 | 0.7986 | 0.7522 | 0.7805 | 0.0282 | 0.0476 | 0.5935 |
| 2025 | 5 | 81 | 0.8013 | 0.9616 | 0.8628 | 0.9012 | 0.0385 | 0.0379 | 1.0141 |

## 2.3 Slices of 2024 (full grid; best cell per slice by Brier, log loss breaks ties)

Half split: 2024 games sorted by date; the first half is every date before 2024-03-23 (the date of game 286 of 570), so no date is split. Prior-game counts use earlier dates only.

**first half (date < 2024-03-23)** (n = 279; best K=40, lam=1) — Brier, K rows x lam columns:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 0.23412 | 0.22891 | 0.22510 | 0.22155 |
| 15 | 0.22810 | 0.22153 | 0.21700 | 0.21308 |
| 20 | 0.22302 | 0.21550 | 0.21058 | 0.20661 |
| 30 | 0.21498 | 0.20631 | 0.20108 | 0.19729 |
| 40 | 0.20902 | 0.19975 | 0.19445 | 0.19092 |

**second half** (n = 291; best K=40, lam=1) — Brier, K rows x lam columns:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 0.22252 | 0.21671 | 0.21233 | 0.20810 |
| 15 | 0.21314 | 0.20621 | 0.20121 | 0.19659 |
| 20 | 0.20573 | 0.19822 | 0.19295 | 0.18824 |
| 30 | 0.19504 | 0.18715 | 0.18177 | 0.17710 |
| 40 | 0.18802 | 0.18017 | 0.17488 | 0.17033 |

**both >= 3 prior 2024 games** (n = 435; best K=40, lam=1) — Brier, K rows x lam columns:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 0.22542 | 0.21952 | 0.21510 | 0.21083 |
| 15 | 0.21692 | 0.20980 | 0.20469 | 0.20001 |
| 20 | 0.21015 | 0.20233 | 0.19690 | 0.19212 |
| 30 | 0.20032 | 0.19189 | 0.18629 | 0.18158 |
| 40 | 0.19382 | 0.18527 | 0.17970 | 0.17512 |

**not both >= 3 prior 2024 games** (n = 135; best K=40, lam=1) — Brier, K rows x lam columns:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 0.23715 | 0.23286 | 0.22981 | 0.22709 |
| 15 | 0.23189 | 0.22632 | 0.22264 | 0.21967 |
| 20 | 0.22720 | 0.22069 | 0.21666 | 0.21368 |
| 30 | 0.21924 | 0.21146 | 0.20711 | 0.20440 |
| 40 | 0.21274 | 0.20421 | 0.19981 | 0.19743 |

**both >= 8 prior games since 2022** (n = 562; best K=40, lam=1) — Brier, K rows x lam columns:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 0.22788 | 0.22228 | 0.21812 | 0.21415 |
| 15 | 0.22005 | 0.21318 | 0.20833 | 0.20396 |
| 20 | 0.21370 | 0.20606 | 0.20086 | 0.19640 |
| 30 | 0.20420 | 0.19577 | 0.19033 | 0.18595 |
| 40 | 0.19763 | 0.18891 | 0.18346 | 0.17923 |

**not both >= 8 prior games since 2022** (n = 8; best K=40, lam=0.5) — Brier, K rows x lam columns:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 0.25019 | 0.25068 | 0.25131 | 0.25226 |
| 15 | 0.24971 | 0.25066 | 0.25194 | 0.25389 |
| 20 | 0.24896 | 0.25044 | 0.25251 | 0.25564 |
| 30 | 0.24699 | 0.24977 | 0.25366 | 0.25942 |
| 40 | 0.24484 | 0.24909 | 0.25491 | 0.26327 |

Summary:
| slice | n | best_K | best_lam | best_brier | brier_at_40_1 | monotone_in_K | monotone_in_lam |
|---|---|---|---|---|---|---|---|
| first half (date < 2024-03-23) | 279 | 40 | 1.00000 | 0.19092 | 0.19092 | True | True |
| second half | 291 | 40 | 1.00000 | 0.17033 | 0.17033 | True | True |
| both >= 3 prior 2024 games | 435 | 40 | 1.00000 | 0.17512 | 0.17512 | True | True |
| not both >= 3 prior 2024 games | 135 | 40 | 1.00000 | 0.19743 | 0.19743 | True | True |
| both >= 8 prior games since 2022 | 562 | 40 | 1.00000 | 0.17923 | 0.17923 | True | True |
| not both >= 8 prior games since 2022 | 8 | 40 | 0.50000 | 0.24484 | 0.26327 | False | False |

## 2.4 Uncertainty: paired 2024 Brier differences, game dates resampled as blocks

2000 resamples of the 75 2024 game dates with replacement (seed 20261111); estimate = sum of per-game differences in the drawn dates / games in the drawn dates. The game-level columns resample games (separate generator, same seed) for comparison. Negative = (40,1) better. All in-sample (2024 is the selection season). share_of_dates_alt_better = fraction of dates on which the alternative had the lower Brier sum.
| comparison | n_games | n_dates | mean_diff | date_block_ci95_lo | date_block_ci95_hi | date_block_se | game_level_ci95_lo | game_level_ci95_hi | game_level_se | share_of_dates_alt_better |
|---|---|---|---|---|---|---|---|---|---|---|
| (40,1) minus (30,1) | 570 | 75 | -0.00658 | -0.00824 | -0.00478 | 0.00088 | -0.00831 | -0.00488 | 0.00088 | 0.26667 |
| (40,1) minus (40,0.85) | 570 | 75 | -0.00406 | -0.00570 | -0.00247 | 0.00082 | -0.00568 | -0.00239 | 0.00084 | 0.24000 |
| (40,1) minus (20,1) | 570 | 75 | -0.01682 | -0.02052 | -0.01277 | 0.00196 | -0.02062 | -0.01275 | 0.00199 | 0.22667 |
| (40,1) minus (10,1) | 570 | 75 | -0.03428 | -0.04113 | -0.02706 | 0.00348 | -0.04078 | -0.02758 | 0.00344 | 0.18667 |

## 2.5 Rating changes 2022-2024 (independent engine)

|dR| per update (one value per game; both teams move by it):
| cell | n_updates | mean | q10 | q25 | q50 | q75 | q90 | q95 | q99 | max | top5pct_n | top5pct_share_of_total_abs_dR |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (40,1) | 1674 | 16.851 | 8.947 | 12.810 | 17.147 | 20.778 | 24.231 | 26.749 | 30.708 | 33.983 | 84 | 0.087 |
| (20,1) | 1674 | 9.007 | 6.388 | 7.747 | 9.170 | 10.279 | 11.417 | 12.232 | 13.710 | 15.322 | 84 | 0.073 |

20 largest updates per cell (score = winner-loser goals; p_winner_pre = winner's pre-game probability):
| cell | date | winner | loser | score | p_winner_pre | abs_dR | sidedness |
|---|---|---|---|---|---|---|---|
| (40,1) | 2024-03-09 | Wagner | Marist | 9-8 | 0.150 | 33.983 | TWO_SIDED |
| (40,1) | 2023-04-01 | Michigan | Maryland | 16-11 | 0.154 | 33.848 | TWO_SIDED |
| (40,1) | 2024-04-06 | Colgate | Army West Point | 14-11 | 0.164 | 33.442 | TWO_SIDED |
| (40,1) | 2024-04-27 | North Carolina | Duke | 15-12 | 0.168 | 33.271 | TWO_SIDED |
| (40,1) | 2024-02-24 | Canisius | Robert Morris | 9-8 | 0.168 | 33.269 | TWO_SIDED |
| (40,1) | 2024-04-20 | Detroit Mercy | Bellarmine | 18-10 | 0.177 | 32.906 | TWO_SIDED |
| (40,1) | 2024-04-13 | Lafayette | Boston U. | 11-10 | 0.179 | 32.835 | TWO_SIDED |
| (40,1) | 2024-02-17 | Canisius | Bellarmine | 8-7 | 0.180 | 32.809 | TWO_SIDED |
| (40,1) | 2023-03-29 | Detroit Mercy | Robert Morris | 14-8 | 0.187 | 32.509 | TWO_SIDED |
| (40,1) | 2023-02-11 | Loyola Maryland | Maryland | 12-7 | 0.191 | 32.350 | TWO_SIDED |
| (40,1) | 2024-03-09 | St. John's (NY) | Binghamton | 13-6 | 0.195 | 32.184 | TWO_SIDED |
| (40,1) | 2024-02-03 | Colgate | Penn St. | 13-12 | 0.205 | 31.808 | TWO_SIDED |
| (40,1) | 2024-04-13 | Brown | Princeton | 13-12 | 0.211 | 31.572 | TWO_SIDED |
| (40,1) | 2023-05-06 | Michigan | Maryland | 14-5 | 0.221 | 31.172 | TWO_SIDED |
| (40,1) | 2023-03-11 | NJIT | Lindenwood | 14-13 | 0.222 | 31.103 | TWO_SIDED |
| (40,1) | 2023-05-04 | UAlbany | Vermont | 10-4 | 0.228 | 30.890 | TWO_SIDED |
| (40,1) | 2024-03-23 | St. John's (NY) | Le Moyne | 9-7 | 0.229 | 30.825 | SINGLE_SIDED |
| (40,1) | 2023-03-14 | Canisius | St. Bonaventure | 13-5 | 0.233 | 30.664 | TWO_SIDED |
| (40,1) | 2023-03-11 | Penn St. | Cornell | 10-6 | 0.234 | 30.630 | TWO_SIDED |
| (40,1) | 2023-03-01 | Hampton | St. Andrews | 16-4 | 0.235 | 30.600 | SINGLE_SIDED |
| (20,1) | 2024-04-06 | Colgate | Army West Point | 14-11 | 0.234 | 15.322 | TWO_SIDED |
| (20,1) | 2024-02-24 | Canisius | Robert Morris | 9-8 | 0.257 | 14.851 | TWO_SIDED |
| (20,1) | 2024-04-13 | Lafayette | Boston U. | 11-10 | 0.259 | 14.815 | TWO_SIDED |
| (20,1) | 2023-04-01 | Michigan | Maryland | 16-11 | 0.260 | 14.793 | TWO_SIDED |
| (20,1) | 2024-04-27 | North Carolina | Duke | 15-12 | 0.264 | 14.729 | TWO_SIDED |
| (20,1) | 2024-03-09 | Wagner | Marist | 9-8 | 0.264 | 14.711 | TWO_SIDED |
| (20,1) | 2024-03-09 | St. John's (NY) | Binghamton | 13-6 | 0.272 | 14.565 | TWO_SIDED |
| (20,1) | 2024-04-20 | Detroit Mercy | Bellarmine | 18-10 | 0.272 | 14.562 | TWO_SIDED |
| (20,1) | 2024-03-23 | St. John's (NY) | Le Moyne | 9-7 | 0.288 | 14.238 | SINGLE_SIDED |
| (20,1) | 2023-05-06 | Michigan | Maryland | 14-5 | 0.291 | 14.188 | TWO_SIDED |
| (20,1) | 2024-02-17 | Canisius | Bellarmine | 8-7 | 0.293 | 14.135 | TWO_SIDED |
| (20,1) | 2023-02-11 | Loyola Maryland | Maryland | 12-7 | 0.295 | 14.102 | TWO_SIDED |
| (20,1) | 2023-03-29 | Detroit Mercy | Robert Morris | 14-8 | 0.295 | 14.091 | TWO_SIDED |
| (20,1) | 2024-04-13 | Brown | Princeton | 13-12 | 0.311 | 13.777 | TWO_SIDED |
| (20,1) | 2023-05-04 | UAlbany | Vermont | 10-4 | 0.312 | 13.765 | TWO_SIDED |
| (20,1) | 2023-03-14 | Canisius | St. Bonaventure | 13-5 | 0.312 | 13.753 | TWO_SIDED |
| (20,1) | 2024-03-27 | Canisius | Siena | 13-12 | 0.313 | 13.734 | TWO_SIDED |
| (20,1) | 2024-03-20 | Syracuse | Duke | 10-4 | 0.315 | 13.702 | TWO_SIDED |
| (20,1) | 2023-03-11 | Penn St. | Cornell | 10-6 | 0.316 | 13.675 | TWO_SIDED |
| (20,1) | 2023-03-11 | NJIT | Lindenwood | 14-13 | 0.321 | 13.587 | TWO_SIDED |

Rating spread (population SD over teams holding a rating; 2022 starts with no teams):
| cell | point | n_teams | sd | min | max |
|---|---|---|---|---|---|
| (40,1) | start 2023 (after shrink) | 73 | 97.70 | 1292.44 | 1776.65 |
| (40,1) | start 2024 (after shrink) | 77 | 135.09 | 1206.75 | 1780.45 |
| (40,1) | end 2024 | 78 | 169.30 | 1115.81 | 1940.07 |
| (20,1) | start 2023 (after shrink) | 73 | 55.43 | 1384.37 | 1656.50 |
| (20,1) | start 2024 (after shrink) | 77 | 82.28 | 1325.54 | 1655.91 |
| (20,1) | end 2024 | 78 | 106.43 | 1258.06 | 1760.26 |

Per-team net change within a season (end of season minus season start after shrink; newcomers from 1500); teams that played that season:
| cell | season | n_teams | q0 | q10 | q25 | q50 | q75 | q90 | q100 | n_abs_net_gt_100 | n_net_gt_100 | n_net_lt_-100 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| (40,1) | 2022 | 73 | -207.6 | -122.2 | -77.4 | -10.0 | 70.8 | 116.8 | 276.7 | 28 | 13 | 15 |
| (40,1) | 2023 | 77 | -220.4 | -102.2 | -46.9 | 4.7 | 39.7 | 82.1 | 204.5 | 17 | 7 | 10 |
| (40,1) | 2024 | 76 | -202.3 | -98.7 | -48.7 | 5.8 | 39.6 | 83.8 | 165.1 | 14 | 6 | 8 |
| (20,1) | 2022 | 73 | -115.6 | -68.3 | -44.0 | -2.6 | 39.9 | 66.3 | 156.5 | 4 | 2 | 2 |
| (20,1) | 2023 | 77 | -121.0 | -64.6 | -25.9 | 5.2 | 33.0 | 51.9 | 106.2 | 3 | 2 | 1 |
| (20,1) | 2024 | 76 | -113.5 | -60.3 | -28.3 | 2.9 | 27.6 | 51.7 | 108.4 | 2 | 1 | 1 |

## 2.6 Influence

(a) Share of the 2024 Brier sum from the worst 5% of games (expected share if errors were equal: 5%):
| cell | n_2024 | worst_n | share_of_brier_sum | worst_min_sq_err | brier_2024 |
|---|---|---|---|---|---|
| (40,1) | 570 | 29 | 0.16184 | 0.47167 | 0.18041 |
| (10,1) | 570 | 29 | 0.08946 | 0.33801 | 0.21468 |

(b) The 10 and 20 largest-|update| games of 2022-2023 were identified at the frozen cell (40,1) (the ranking of |S - p| depends on the cell, so one cell had to be chosen) and excluded from rating updates in every cell, while still predicted. Lists in influence_excluded_games.csv.

**10 largest 2022-2023 updates excluded from updates** (still scored): argmin K=40, lam=1; monotone in K True, in lam True. 2024 Brier:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 0.22823 | 0.22278 | 0.21878 | 0.21501 |
| 15 | 0.22052 | 0.21390 | 0.20930 | 0.20527 |
| 20 | 0.21428 | 0.20696 | 0.20211 | 0.19810 |
| 30 | 0.20493 | 0.19694 | 0.19200 | 0.18826 |
| 40 | 0.19844 | 0.19024 | 0.18538 | 0.18193 |

**20 largest 2022-2023 updates excluded from updates** (still scored): argmin K=40, lam=1; monotone in K True, in lam True. 2024 Brier:
| K | lam=0.5 | lam=0.7 | lam=0.85 | lam=1.0 |
|---|---|---|---|---|
| 10 | 0.22828 | 0.22291 | 0.21899 | 0.21533 |
| 15 | 0.22060 | 0.21411 | 0.20965 | 0.20580 |
| 20 | 0.21439 | 0.20725 | 0.20259 | 0.19883 |
| 30 | 0.20508 | 0.19735 | 0.19268 | 0.18930 |
| 40 | 0.19862 | 0.19072 | 0.18619 | 0.18317 |

## 2.7 Low history at K=40, lam=1

Low-history game: either team had fewer than 5 games on earlier dates, counted since 2022 (the D6 convention; same-date games do not count). For 'excluded from updates and scoring' the engine is rerun on 2022-2024 with those games predicted but not updated; prior counts stay schedule-based. 2025 is descriptive from recorded predictions.csv (no rerun is possible without the 2025 file).
| season | variant | n_scored | n_low_history | brier | log_loss |
|---|---|---|---|---|---|
| 2022 | all games (reference) | 543 | 208 | 0.21922 | 0.62882 |
| 2022 | low-history excluded from scoring only | 335 | 208 | 0.20705 | 0.60295 |
| 2022 | excluded from updates and scoring | 335 | 208 | 0.22855 | 0.64881 |
| 2023 | all games (reference) | 561 | 12 | 0.19902 | 0.58388 |
| 2023 | low-history excluded from scoring only | 549 | 12 | 0.19603 | 0.57732 |
| 2023 | excluded from updates and scoring | 549 | 12 | 0.20567 | 0.59913 |
| 2024 | all games (reference) | 570 | 5 | 0.18041 | 0.53997 |
| 2024 | low-history excluded from scoring only | 565 | 5 | 0.18000 | 0.53910 |
| 2024 | excluded from updates and scoring | 565 | 5 | 0.18601 | 0.55347 |
| 2025 | all games (recorded) | 409 | 10 | 0.18745 | 0.55580 |
| 2025 | low-history games only (recorded) | 10 | 10 | 0.35015 | 0.94003 |
| 2025 | low-history excluded from scoring (recorded) | 399 | 10 | 0.18337 | 0.54617 |

## FINDINGS

### ERRORS
- Carried from the first audit, still standing: the Chunk 4 report's 'largest rating changes' table counted 77 teams 'present at both' starts, including Lindenwood (no 2025 game; listed 2nd down) and St. Andrews and Wabash (no 2024 or 2025 game); its 'cross-checked against stored pre-game ratings' sentence covered only teams that played. Not re-examined here.

### UNRESOLVED
- The selected cell is a grid corner and the 2024 Brier is still falling at both edges; whether the optimum lies beyond K=40 / lam=1 cannot be settled without widening the grid, which is out of scope here.
- Is K=40, lam=1 meaningfully better than its neighbours? With dates as blocks the 2024 difference vs (40,0.85) is -0.00406 [-0.00570, -0.00247] and vs (30,1) -0.00658 [-0.00824, -0.00478]; this is in-sample (selection season), so it does not say how the choice generalises.
- 2025 looks less under-confident than 2023/2024 (D4 slope 1.23 [0.98, 1.54] vs 1.49 and 1.57; no 2025 reliability bin has |z| >= 2). Whether that reflects the shorter 2025 slice (last date 2025-04-08), the larger rating spread by 2025, or sampling cannot be separated from recorded predictions alone.
- Two conventions here are my choices, not given in the brief: 2.6(b) identifies the largest updates at (40,1); 2.7 counts prior games since 2022 (the D6 convention), under which only 12 (2023) and 5 (2024) games are low-history. A within-season count would flag far more games and could change 2.7.

### OBSERVATIONS
- Spearman rank agreement of the 20 cells between 2023 and 2024: Brier 0.983, log loss 0.983.
- Calibration slope is > 1 in all 40 cell-seasons (True) and falls toward 1 as K rises (2023 True, 2024 True) and as lam rises (2023 True, 2024 True); it is closest to 1 at the selected corner (2023 1.485, 2024 1.571). Every cell's forecasts are too close to 0.5, and the bolder the cell the better its Brier; this is consistent with (not proof of) the grid edge being driven by under-dispersed forecasts.
- Reliability bins with |z| >= 2 (of 15): 4 -> 2023 bin 3 z=+2.90; 2024 bin 3 z=+2.73; 2024 bin 4 z=+2.70; 2024 bin 5 z=+2.08.
- Best cell per 2024 slice: first half (date < 2024-03-23) (n=279): (40,1); second half (n=291): (40,1); both >= 3 prior 2024 games (n=435): (40,1); not both >= 3 prior 2024 games (n=135): (40,1); both >= 8 prior games since 2022 (n=562): (40,1); not both >= 8 prior games since 2022 (n=8): (40,0.5).
- Date-block vs game-level bootstrap SE ratio, 2024 paired differences: (40,1) minus (30,1) 1.00; (40,1) minus (40,0.85) 0.97; (40,1) minus (20,1) 0.98; (40,1) minus (10,1) 1.01.
- Top 5% of updates carry 8.7% of total |dR| at (40,1) and 7.3% at (20,1).
- Worst 5% of 2024 games carry 16.2% of the Brier sum at (40,1) and 8.9% at (10,1).
- With lam = 1 and no shrink the rating spread grows season on season: SD start 2023 (after shrink) 97.7, start 2024 (after shrink) 135.1, end 2024 169.3 at (40,1).
- Slices of 2024: (40,1) is best in every slice except 'not both >= 8 prior games since 2022', which has n = 8 and is too small to read.
- Excluding the 10 / 20 largest 2022-2023 updates leaves the 2024 grid monotone with the same argmin (40,1); its 2024 Brier moves from 0.18041 to 0.18193 / 0.18317.
- Low history: dropping low-history games from updates as well as scoring makes 2023 and 2024 Brier worse (2023: 0.20567; 2024: 0.18601) than scoring-only exclusion (2023: 0.19603; 2024: 0.18000). In 2025 the 10 low-history games (all single-sided) score Brier 0.350 (descriptive, n = 10).
- Date blocks do not widen the 2024 intervals: SE ratios of about 1.0, so same-date correlation is not visible at this size.

## Differences from recorded values (tolerance 1e-9)

Values compared: grid_2024.csv 200; metrics_by_season.csv 42; audit_report D1 217; audit_report D2 30; audit_report D3 30; audit_report D4 21; audit_report D5 63; audit_report D6 60.

**No differences.** Every compared value reproduces within the tolerance.

_diagnostic, no re-selection_
