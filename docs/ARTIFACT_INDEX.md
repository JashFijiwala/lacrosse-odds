# Artifact index and authority

Existing research files stay at their original paths. This index supersedes stale status descriptions, not the historical artifacts themselves. Publication decisions are per file in [the inventory](../repo_prep/2026-11-18/publication_inventory.csv). A code-styled path marked **withheld** may exist only on the owner's workspace; it is intentionally not a broken link in the published clone.

## Design and protected sources

- [PILOT_DESIGN.md](PILOT_DESIGN.md): locked budget, revised Gate A/B rules, timing and interpretation. Its opening “scaffold only; no request” label is historical. The private ledger now has 25 successful response rows spanning BOOTSTRAP, A, B, C and D; this does not imply that every originally planned tranche was executed.
- [AGENTS.md](../AGENTS.md) and [CLAUDE.md](../CLAUDE.md): maintenance rules. Use the narrower [current check commands](REPRODUCIBILITY.md) for this collaboration task instead of the historical blanket pytest command.
- [audit_join/protected_hashes.txt](../audit_join/protected_hashes.txt): authoritative list of ten protected `src` files. All `src` files and all joining/Elo artifacts were additionally frozen by the new preparation manifest.

## Joining sequence

| Record | Authority and access |
|---|---|
| [join_audit.py](../audit_join/join_audit.py), [aliases](../audit_join/aliases.csv), [manual verification](../audit_join/manual_verification.csv) | Frozen first implementation and verification notes |
| `audit_join/out/` | Original first-run outputs; odds-related files **withheld** |
| `audit_join/out_run1/` | Preserved first-run archive; [SHA256SUMS.txt](../audit_join/out_run1/SHA256SUMS.txt) records archive hashes; results-only tables are candidates |
| [join_revision.py](../audit_join/join_revision.py), [reviewed_overrides.csv](../audit_join/reviewed_overrides.csv) | Revised implementation and identity notes; override rows still literally say `PROPOSED_PENDING_HUMAN_REVIEW`, despite the filename. Do not silently relabel them approved |
| `audit_join/out_run2/join_revision.md` | Authoritative revised joining report, **withheld** because it contains odds-derived content |
| `audit_join/out_run2/event_game_links.csv`, `team_keyed_moneyline.csv` | Authoritative revised PRIMARY/sample flags and team-keyed price/probability inputs to later audits/comparison; **withheld** |
| `audit_join/out_run2/run_diff.csv`, `newly_matched_events.csv`, `b2_b3_overlap.csv` | Run changes and snapshot-overlap provenance; **withheld**, never deleted as clutter |

Run 2 supersedes run 1 for later comparison inputs. It does not erase run 1's protocol, outputs or failures. The archived and original results-only canonical tables both have 888 rows for 2025/2026; they remain distinct paths even when byte-identical.

## Frozen Elo experiment

| Artifact | Role |
|---|---|
| [data_layer_report.md](../elo_feas/out/data_layer_report.md) | Source pin, schema adaptation, counts, exclusions and coverage |
| [canonical_games_2022_2026.csv](../elo_feas/out/canonical_games_2022_2026.csv) | Results-only canonical table, 2,562 games; 2026 descriptive only |
| [team_season_membership.csv](../elo_feas/out/team_season_membership.csv), [exclusions.csv](../elo_feas/out/exclusions.csv), [mascot_strip_map.csv](../elo_feas/out/mascot_strip_map.csv) | Membership and data-cleaning provenance |
| [frozen_params.json](../elo_feas/out/frozen_params.json) | K=40, lambda=1, h=0 and selection-table/parameter hashes |
| [grid_2024.csv](../elo_feas/out/grid_2024.csv), [sensitivity_h_2024.csv](../elo_feas/out/sensitivity_h_2024.csv) | Original selection grid and explicitly separate home-term sensitivity |
| [predictions.csv](../elo_feas/out/predictions.csv) | Stored predictions for 2,083 games in 2022-2025; no rerun |
| [test_2025_run.json](../elo_feas/out/test_2025_run.json) | Completed evaluation marker; never remove to bypass the freeze |
| [experiment_report.md](../elo_feas/out/experiment_report.md), [metrics_by_season.csv](../elo_feas/out/metrics_by_season.csv), [2024 reliability](../elo_feas/out/reliability_2024.csv), [2025 reliability](../elo_feas/out/reliability_2025.csv) | Recorded experiment and metrics; interpret with [current status](RESEARCH_STATUS.md) |

## Audit sequence

1. **Root audit:** [audit_report.md](../elo_feas/audit_out/audit_report.md) and root CSVs check recorded versus recomputed values, integrity, calibration, season coverage and rating-change definitions. These are diagnostic records, not new model selection.
2. **Chunk 2:** [chunk2_report.md](../elo_feas/audit_out/chunk2/chunk2_report.md), [indep_elo.py](../elo_feas/audit_out/chunk2/indep_elo.py) and recorded tables establish independent 2022-2024 agreement, grid/calibration behavior, date-block uncertainty, influence and low-history diagnostics. No 2025 evaluator rerun.
3. **Chunk 3:** `elo_feas/audit_out/chunk3/chunk3_report.md` (**withheld**, mixed results and odds). Results-only venue/identity tables remain candidates; event bridge, snapshot rows and crosswalk-derived tables are withheld. `market_comparison_spec.md` (**withheld**) is the original pre-registration and is superseded by chunk 5 v2.1.1; do not use its older consensus rule for the recorded final comparison.
4. **Chunk 4:** [equiv_report.md](../elo_feas/audit_out/chunk4/equiv_report.md) and its independent implementation check K/denominator equivalence. `timing_counts.md` and `timing_events.csv` (**withheld**) establish T1/T2-related timing populations without computing comparison losses.
5. **Chunk 5:** the original `gates.md` and [gates_failed.md](../elo_feas/audit_out/chunk5/gates_failed.md) preserve the first failed G3 run. The final specification is `market_comparison_spec_v2.1.1.md` (**withheld**) with Amendment A1. `gates_rerun.md` and [gates_passed.json](../elo_feas/audit_out/chunk5/gates_passed.json) record the amended G1-G9 pass. `compare_report.md` and R1-R5 CSVs are the final paired comparison (**withheld**). No lost or failed run is disposable.

Each audit scope has its original `hashes_before*`, `hashes_after*`, `hash_diff*` and `files_opened*` evidence. These are preserved. They describe historical checks, not a current invitation to execute the writer scripts. The preparation uses a new [immutable manifest](../repo_prep/2026-11-18/immutable_before.csv) without overwriting any research manifest.

## Verified narrative cautions

- The design's “no API request yet” opening is no longer current; acquisition records exist.
- The experiment report's “only 2025 is out of sample” describes its original selection sequence, not future development after extensive 2025 inspection.
- Chunk 3's pre-registration and chunk 5's “comparison has not been executed” language are contemporaneous records. The final comparison report and R1-R5 exist now.
- The name `reviewed_overrides.csv` does not override its explicit pending-review field.
- [differences_vs_recorded.csv](../elo_feas/audit_out/chunk3/differences_vs_recorded.csv) contains 13 checks, all `MATCH`; it does not establish an additional numerical discrepancy. The changed probability tolerance is explicitly documented in A1, not hidden as a numerical correction.

The new [research status](RESEARCH_STATUS.md) is the current collaboration entry point. Historical reports remain immutable and must be read in that sequence.
