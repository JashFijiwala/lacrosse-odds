# Reproducibility and permitted checks

Use Windows CPython 3.10.9 and the existing pins. See [onboarding](TEAM_ONBOARDING.md) for setup. Offline does not mean safe to rerun every script: several offline entry points overwrite frozen artifacts or open season files outside this maintenance task's scope.

## Recorded pipeline, not a rerun recipe

| Stage | Code / inputs | Recorded outputs and scope |
|---|---|---|
| Budgeted acquisition | `src/odds_client.py`, stage modules, config, key, baseline/ledger and gates | `pilot/raw/`, ledger; network and potential credits; private |
| Normalization and pilot analysis | `src/normalize.py`, `analysis.py`, `costs.py`; saved snapshots, ledger, Cradl observations | `pilot/normalized/`; prohibited from publication |
| Join run 1 | `audit_join/join_audit.py`; odds, pinned 2025/2026 results, aliases/manual verification | `audit_join/out/`; frozen archive in `out_run1/` |
| Join run 2 | `audit_join/join_revision.py`; archived run 1, reviewed overrides, same private inputs | `audit_join/out_run2/`; authoritative revised PRIMARY flags and team-keyed moneylines |
| Results data layer | `elo_feas/data_layer.py`; pinned 2022-2026 results, unchanged join helpers | Canonical games, membership, exclusions and data-layer report in `elo_feas/out/` |
| Elo selection | `audit_elo.py`, `elo.py`, `metrics.py`; 2022 warm-up, 2023/2024 history, 2024 selection metric | 20-cell grid, frozen parameters, 2024 sensitivity; K=40, lambda=1, h=0 |
| Original frozen evaluation | `run_experiment.py` / `audit_elo.evaluate_2025`; frozen hashes and canonical games | Existing predictions, metrics and completion marker; 409 games in 2025; no new evaluation here |
| Diagnostic audits | `audit_out/` root, then chunks 2-4; recorded outputs, independent engines and selection data | Calibration, influence, venue/identity, equivalence and timing records; no re-selection |
| Paired comparison | Chunk 5 spec v2.1.1/A1, gates, existing 57 predictions, run-2 prices and PRIMARY links | G1-G9 rerun record, R1-R5 and comparison report; already completed, not rerun |

Paths withheld from the proposed commit are documented in the [artifact index](ARTIFACT_INDEX.md). Their absence prevents a fresh clone from re-creating the original market analysis. Published results-only tables permit inspection and integrity checks, not an assertion that the full pipeline has been reproduced.

## Safe checks

```powershell
.\.venv\Scripts\python.exe -B tools\repository_checks.py
.\.venv\Scripts\python.exe -B tools\offline_tests.py
```

The first checks candidate files, current documentation paths, installed dependency metadata/imports, protected-code hashes and available immutable-file hashes. It treats withheld files as optional in a clone. It checks upstream input *presence* only, without reading their contents. Credential scanning covers candidate text and reports locations/types only; it never loads the real `.env`. Preparation also checked internal Git snapshot trees separately from the working tree; see the [validation record](../repo_prep/2026-11-18/VALIDATION.md).

The second launches these exact pytest selections, one process per row, with the same virtual-environment interpreter and an additional guard against network access, reading private inputs/raw upstream 2025/2026 files, and writing project research paths. It disables third-party plugin autoload and uses temporary directories for fixtures.

| Group | Pytest arguments after `-q` |
|---|---|
| Pilot | `tests/test_odds_client.py tests/test_stage_a.py tests/test_stage_b.py tests/test_stage_c.py tests/test_stage_d.py tests/test_normalize.py tests/test_analysis.py tests/test_costs.py -k "not test_parity_on_saved_anchor_events"` |
| Join | `--noconftest tests/test_join_audit.py tests/test_join_revision.py -k "not test_13_golden_four_known_events and not test_14_coverage_reproduction and not test_11_real_four_known_events_run2_path and not test_12_real_flip_invariance_on_run1_primary"` |
| Engine | `--noconftest elo_feas/tests/test_elo_engine.py -k "not test_e6_frozen_params_gate and not test_e6_selection_loader_reads_only_2022_2024 and not test_e6_2025_predictions_are_pre_game"` |
| Adapters | `--noconftest elo_feas/tests/test_elo_data_layer.py -k "test_t3_longest_prefix or test_t3_word_boundary_and_failures or test_t4_adapter_fails_loudly or test_t5_outcome_column_never_carried or test_t6_synthetic_flip_of_each_pair"` |

Do not merge the groups into one pytest invocation. `tests/conftest.py` imports real pilot modules and supplies the fake key, request mocks, isolated roots and network guard. Joining uses an inert `src.ledger` stand-in and rejects pilot modules already imported into that process. `--noconftest` is required for join/Elo groups; it would remove fixtures required by pilot tests. The historical `pytest.ini` default points only at `tests/`, so it also omits the Elo tests.

Excluded tests either open saved private odds/raw 2025/2026 results, use the frozen evaluator (even on synthetic inputs), or fall outside the strictly synthetic clone check. The engine's synthetic 2025-labelled audit test is conservatively excluded as well. They were not run; a missing-data skip is not a verification of them.

## Commands outside this maintenance scope

Do not invoke the acquisition CLI, stage mains, normalization/analysis writers, join mains, `elo_feas.data_layer`, `elo_feas.run_experiment`, audit runners, chunk-5 gates, or comparison runner against this workspace. Do not delete the 2025 marker to make an evaluator run. Even `src.cli plan` and `ledger-status` instantiate `OddsClient`; startup checks can write a HALT if local accounting is corrupt. They are not the recommended clone diagnostics. Bootstrap and reconcile additionally contact the API.

Original reports and manifests are immutable research evidence. Existing run guards and file hashes are part of that evidence. A future replication would require authorization, permitted input access, a separately reviewed protocol, separate outputs, read-only originals, input/code/environment hashes, and explicit handling of the extensively inspected 2025 data. No such replication has been implemented or executed here.
