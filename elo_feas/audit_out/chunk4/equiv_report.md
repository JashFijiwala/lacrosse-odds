# Chunk A: equivalence of (K, D) parametrisations of the frozen Elo engine

**diagnostic, no re-selection.** Read-only; selection data only (selection_games(), seasons 2022-2024, 1674 games). K = 40, lam = 1, h = 0 and the grid are unchanged; nothing is retuned; 2025 is not touched. Engine: `chunk4/equiv_check.py`, ratings in rating points with an explicit denominator D; it imports nothing from `elo_feas` (source scan: no elo_feas import); the games frame comes from selection_games() via the runner. Tolerance 1e-9.
Hash check over 136 files: NO DIFFERENCES (hash_diff.txt).

## 1. Gate: (K=40, D=400, lam=1.0) against the recorded outputs
| quantity | this engine (40, 400, 1.0) | grid_2024.csv | abs diff |
|---|---|---|---|
| n_2023 | 561 | 561 | 0 |
| brier_2023 | 0.199016577365 | 0.199016577365 | 0 |
| log_loss_2023 | 0.583879130623 | 0.583879130623 | 2.22044604925e-16 |
| n_2024 | 570 | 570 | 0 |
| brier_2024 | 0.180406130091 | 0.180406130091 | 2.77555756156e-17 |
| log_loss_2024 | 0.539969288806 | 0.539969288806 | 4.4408920985e-16 |

| predictions.csv rows | n | max abs diff R_a_before | max abs diff R_b_before | max abs diff p_a | y_a mismatches |
|---|---|---|---|---|---|
| 2023 + 2024 rows | 1131 | 4.55e-13 | 4.55e-13 | 1.11e-16 | 0 |
| 2022 rows (informational) | 543 | 2.27e-13 | 2.27e-13 | 8.33e-17 | 0 |

Gate: **PASS** (all differences < 1e-09, y_a identical).

## 2. Equivalence against (K=40, D=400) over all 2022-2024 games
z_end is compared for every (season, team) at the end of each season (before the next shrink). Reference run is (40, 400) at the same lam.
| lam | (K, D) | K/D | role | max abs diff p (all games) | max abs diff season-end z=(R-1500)/D |
|---|---|---|---|---|---|
| 1 | (20, 200) | 0.1 | equivalent | 3e-15 | 4e-15 |
| 1 | (80, 800) | 0.1 | equivalent | 1.39e-15 | 1.72e-15 |
| 1 | (10, 100) | 0.1 | equivalent | 5.22e-15 | 7.39e-15 |
| 1 | (40, 200) | 0.2 | CONTROL (not equivalent) | 0.201 | 0.599 |
| 0.85 | (20, 200) | 0.1 | equivalent | 3e-15 | 2.84e-15 |
| 0.85 | (80, 800) | 0.1 | equivalent | 1.61e-15 | 1.72e-15 |
| 0.85 | (10, 100) | 0.1 | equivalent | 6.11e-15 | 6.22e-15 |
| 0.85 | (40, 200) | 0.2 | CONTROL (not equivalent) | 0.188 | 0.563 |

Equivalent pairs: max over rows below 1e-09: **PASS**; control differs in p by at least 0.188 (test can fail: yes).

## 3. Grid mapping: 2024 Brier of (K, D=400) vs (K/2, D=200), h = 0
| lam | K (D=400) | brier_2024 (K, 400) | K/2 (D=200) | brier_2024 (K/2, 200) | abs diff |
|---|---|---|---|---|---|
| 0.5 | 10 | 0.228196509123 | 5 | 0.228196509123 | 8.32667268469e-17 |
| 0.5 | 20 | 0.214192712956 | 10 | 0.214192712956 | 2.77555756156e-17 |
| 0.5 | 30 | 0.204802447966 | 15 | 0.204802447966 | 5.55111512313e-17 |
| 0.5 | 40 | 0.198296833017 | 20 | 0.198296833017 | 5.55111512313e-17 |
| 0.7 | 10 | 0.222681358293 | 5 | 0.222681358293 | 2.49800180541e-16 |
| 0.7 | 20 | 0.206680469626 | 10 | 0.206680469626 | 2.77555756156e-17 |
| 0.7 | 30 | 0.196529423499 | 15 | 0.196529423499 | 2.77555756156e-17 |
| 0.7 | 40 | 0.189756116151 | 20 | 0.189756116151 | 8.32667268469e-17 |
| 0.85 | 10 | 0.2185830161 | 5 | 0.2185830161 | 8.32667268469e-17 |
| 0.85 | 20 | 0.201582328465 | 10 | 0.201582328465 | 1.38777878078e-16 |
| 0.85 | 30 | 0.191219730561 | 15 | 0.191219730561 | 2.77555756156e-17 |
| 0.85 | 40 | 0.184461845438 | 20 | 0.184461845438 | 5.55111512313e-17 |
| 1 | 10 | 0.214683840025 | 5 | 0.214683840025 | 1.94289029309e-16 |
| 1 | 20 | 0.197230147995 | 10 | 0.197230147995 | 5.55111512313e-17 |
| 1 | 30 | 0.18698332962 | 15 | 0.18698332962 | 1.94289029309e-16 |
| 1 | 40 | 0.180406130091 | 20 | 0.180406130091 | 0 |

Max abs diff over the 16 cells: 2.5e-16 -> **PASS**.

## 4. Home advantage in rating units: h = 25 at D = 400 vs h = 12.5 at (K=20, D=200)
| lam | alternative vs (h=25, K=40, D=400) | role | max abs diff p | max abs diff season-end z |
|---|---|---|---|---|
| 1 | (h=12.5, K=20, D=200) | equivalent | 3.22e-15 | 3.89e-15 |
| 1 | (h=25, K=20, D=200) | CONTROL (not equivalent) | 0.0516 | 0.0213 |
| 1 | (h=0, K=40, D=400) | CONTROL: h switched off | 0.0514 | 0.0213 |
| 0.85 | (h=12.5, K=20, D=200) | equivalent | 2.83e-15 | 2.86e-15 |
| 0.85 | (h=25, K=20, D=200) | CONTROL (not equivalent) | 0.052 | 0.0188 |
| 0.85 | (h=0, K=40, D=400) | CONTROL: h switched off | 0.0516 | 0.0194 |

Equivalent row max below 1e-09: **PASS**; controls differ (test can fail): yes.

**Verdict: PASS** - the engine reproduces the recorded cell; pairs with the same K/D (and h/D) give the same probabilities and the same (R-1500)/D to within 1e-9 at lam = 1.0 and 0.85; the non-equivalent controls differ.

_diagnostic, no re-selection_
