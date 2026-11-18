# Research status as of 2026-11-18

The completed work is a historical-odds pilot, a results data layer, a frozen Elo feasibility experiment, successive methodological audits, and a pre-registered descriptive paired market comparison. This document summarizes existing artifacts; no evaluation, selection or comparison was run during repository preparation.

## Frozen Elo feasibility

[Frozen parameters](../elo_feas/out/frozen_params.json) are **K=40, lambda=1.0, h=0**, with the rating centre fixed at 1500. The original grid had five K values and four shrinkage values. Selection used mean Brier on 2024, with log loss breaking ties; 2022 was warm-up. The selected cell is on the upper edge of both grid axes. This does not establish an optimum beyond that grid.

The [recorded metrics](../elo_feas/out/metrics_by_season.csv) and [experiment report](../elo_feas/out/experiment_report.md) give:

| Season | Canonical games | Two-sided | Single-sided | Frozen Elo Brier (all) | Frozen Elo log loss (all) |
|---|---:|---:|---:|---:|---:|
| 2022 | 543 | 543 | 0 | Warm-up | Warm-up |
| 2023 | 561 | 546 | 15 | 0.199017 | 0.583879 |
| 2024 | 570 | 548 | 22 | 0.180406 | 0.539969 |
| 2025 | 409 | 367 | 42 | 0.187449 | 0.555796 |

The stored predictions contain 2,083 rows across 2022-2025, including warm-up rows; the completion marker records 409 evaluated 2025 games. The canonical results table additionally contains 479 games from 2026 for data description. There are no 2026 Elo predictions in the frozen run.

The original 2025 evaluation followed the parameter freeze. Subsequent audits and market analysis inspected 2025 extensively. It must not be presented as an untouched test set for any future selection, calibration or model extension. The original report's historical out-of-sample description refers to that original selection sequence only.

## Completed paired comparison

The authoritative local record is `elo_feas/audit_out/chunk5/compare_report.md`, accompanied by R1-R5 tables. These odds-derived numerical results are withheld from the proposed Git commit pending sharing permission; they have not been replaced or recalculated. The new collaboration documents describe scope without republishing those loss tables or per-game market probabilities.

The fixed population is 57 PRIMARY 2025 games, all with stored Elo predictions and none single-sided:

| Game date | Games |
|---|---:|
| 2025-02-28 | 9 |
| 2025-03-01 | 20 |
| 2025-03-02 | 2 |
| 2025-03-08 | 24 |
| 2025-03-09 | 2 |

The nested book-availability groups have 57 games with at least one book, 55 with at least two, and 43 with three. BetMGM, DraftKings and FanDuel cover 48, 56 and 51 games respectively (155 event/book pairs). The spec uses proportional de-vigging within each book and an equal-weight mean of available book probabilities for consensus. Consensus composition varies; the three-book subset holds composition fixed. Brier and log loss are descriptive; the paired comparison did not report intervals, standard errors, tests or correlations. This is not a population-wide accuracy or profitability claim.

The original G3 gate failed because stored probabilities had at most six decimal places, while the specification demanded agreement within 1e-9. Only 6 of 155 pairs met that tolerance; all 155 met 5e-7. Amendment A1, recorded before losses were computed, uses full-precision probabilities recomputed from raw team-keyed prices for analysis and 5e-7 for checking the rounded stored values. The first failure, amended spec, passing G1-G9 rerun and final comparison all remain intact. See [the artifact sequence](ARTIFACT_INDEX.md).

## Information cutoffs and limitations

- **Calibration and the grid corner:** [recorded calibration](../elo_feas/audit_out/calibration.csv) suggests under-extreme probabilities in the selection seasons. The 2024 no-intercept slope is 1.570824. This is diagnostic, not permission to widen the grid or recalibrate on inspected 2025. The [equivalence audit](../elo_feas/audit_out/chunk4/equiv_report.md) shows that jointly rescaling K and the Elo denominator can preserve predictions; changing the denominator alone is not a free calibration fix.
- **Partial season:** [calendar coverage](../elo_feas/audit_out/calendar_coverage.csv) ends on 2025-04-08, versus late May in 2022-2024. The 57 market games are concentrated on five early dates; two dates contain only two games each. They do not represent an entire season or all games/books.
- **Venue flags:** the [data-layer report](../elo_feas/out/data_layer_report.md) records Away/Away counts of 33, 38, 46, 9, 0 in 2022-2026 and Home/Home counts of 0, 0, 0, 1, 16. These are data flags, not validated neutral sites or automatic exclusions. A [validation frame](../elo_feas/audit_out/chunk3/venue_validation_frame.csv) exists with verifier fields still blank. The primary model uses h=0; odds-feed home labels are not ground truth for venue.
- **Opponent-only teams:** [membership](../elo_feas/out/team_season_membership.csv) and the [opponent-only audit](../elo_feas/audit_out/chunk3/opp_only_by_season.csv) track teams without their own source rows. In 2025, four such teams account for 42 single-sided games. Their sparse ratings and identity/division classification remain issues; none of the 57 PRIMARY games involves an opponent-only team under the recorded definition.
- **Timing:** Elo updates are batched by results date. The comparison's T1 checks each team's prior-game cutoff and holds for all 57 games. It does not cover every indirect rating-chain effect. T2 requires the snapshot on or after midnight America/New_York on game day; it leaves 33 games (27 with three books), on 2025-02-28 and 2025-03-08 only. Snapshot timestamps do not establish when a bookmaker formed a price. Returned timestamp, requested date, market last-update and scheduled start remain distinct.
- **Interpretation:** no evidence here establishes a betting edge, complete historical coverage or sufficient power. Settlement math remains theoretical until book-specific rules are verified. Unchanged quotes are not movements, and within-book and cross-book spreads must be analyzed separately.

## Proposed directions, not completed work

The requested future directions from the professor are offense/defense/style information, prominence and coverage, and women's lacrosse feasibility. No local completed experiment establishes these extensions. They need new protocols, input-rights and coverage checks, and evaluation cutoffs that account for prior inspection of 2025. The existence of upstream women's files does not establish usable women's odds coverage or authorize any acquisition.
