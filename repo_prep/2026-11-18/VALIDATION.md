# Preparation validation — 2026-11-18

All preparation was local and offline. There were no API calls, scraping, package downloads, paid operations, frozen-evaluation/comparison reruns, staging commands, commits, pushes, remote creation or collaborator invitations. The real `.env` and raw upstream 2025/2026 results were not opened.

## Offline tests

The reviewed selections in [tools/offline_tests.py](../../tools/offline_tests.py) passed in the original workspace and again in a temporary copy containing only the 181 proposed commit files:

| Separate process | Passed | Deliberately deselected |
|---|---:|---:|
| Mocked pilot, with its conftest | 180 | 1 |
| Synthetic joining and hash checks, `--noconftest` | 25 | 4 |
| Synthetic Elo engine, `--noconftest` | 20 | 3 |
| Synthetic schema adapters, `--noconftest` | 5 | 8 |
| **Total per workspace** | **230** | **16** |

The exact argument lists and original outputs are in [test_results.json](test_results.json); the candidate-only copy's outputs are in [fresh_clone_check.json](fresh_clone_check.json). Network/private-input/workspace-write guards were active in test children. The copy had no `.env`, `.venv`, private pilot data/accounting, or `external/` checkout. It used the existing verified virtual environment's interpreter and installed packages; it was not a new dependency installation or an actual Git clone. Temporary copies were removed after their checks, with their destination verified under the system temporary directory. No original artifact was deleted.

Not run: saved-snapshot parity, real 2025/2026 joining integration, real full-results data-layer checks, the evaluator gate test, the synthetic 2025 audit test, and the public-source selection-loader test. Reasons and exact selectors are in [reproducibility](../../docs/REPRODUCIBILITY.md). In particular, the evaluator gate test calls the frozen evaluator even with synthetic inputs, so it was excluded. No research entry-point writer was run.

## Integrity and setup

- [immutable_before.csv](immutable_before.csv) and [immutable_after.csv](immutable_after.csv): **194/194 unchanged**, no missing or modified files. This includes all 13 src files (including the ten protected paths), all 30 audit_join files, all 129 Elo files, tests, accounting/Cradl/configuration and locked instructions/design/pins. [hash_comparison.json](hash_comparison.json) records the comparison.
- The original protected-hash list's ten entries all match. No code, frozen output, audit record, ledger row, HALT or pending record was changed.
- CPython **3.10.9**, Windows. All **31** existing pinned distributions match the installed versions. ` .\.venv\Scripts\python.exe -m pip check` returned `No broken requirements found.` Twenty dependency/project imports succeeded without calling acquisition or evaluation entry points.
- [repository_check_results.json](repository_check_results.json) records the final candidate text scan, local-path/link checks and imports. The temporary copy also passed those checks: 152 available original immutable files matched and 42 intentionally withheld/private original files were absent. Missing private inputs are not evidence of full research reproduction.
- [.gitattributes](../../.gitattributes) disables newline conversion for immutable research/configuration and preparation records. Read-only `git check-attr -z` confirmed `text: unset` for all 190 original immutable paths eligible for Git (the other four are accounting/Cradl). No index write was needed.

## Publication hygiene

- **181 file candidates** are enumerated in [commit_files.txt](commit_files.txt). [publication_inventory.csv](publication_inventory.csv) also lists **39 unresolved-sharing files** and **141 private/local rows** (136 files plus five directory/cache categories). No file remained unclassified.
- [ignore_check.json](ignore_check.json): no candidate is ignored; every individually inventoried private/withheld file is ignored. Restricted derivatives are listed individually for sharing-rights reasons, not excluded to reduce size. Environment/cache categories are ignored too.
- No credential-pattern or identifying user-home-path matches were found in the proposed publication files. The scanner reports locations/types only and never reads the real `.env`. This is a heuristic scan, not a proof that arbitrary unknown credential formats cannot exist. The initial working-tree/internal-ref scan is in [initial_secret_scan.json](initial_secret_scan.json).
- No tracked files, no staged files, **zero reachable commits**, and no configured remotes. Two existing internal Codex refs point to trees, not commits. Their 179 distinct blobs were separately scanned: no credential matches or `.env`/raw/normalized filenames were found. They **do contain withheld derivatives and private accounting state**. [git_exposure_after.json](git_exposure_after.json) lists paths without exposing values. Preserve those refs locally; never mirror-push or export `.git`. Publish only a newly approved branch commit with the explicit inventory.
- Largest candidate: `elo_feas/out/predictions.csv`, **424,659 bytes** (about 415 KiB). No candidate exceeds either 50 MiB or 100 MiB; the package is about 2.5 MiB before Git compression. [folder_sizes.csv](folder_sizes.csv) records metadata-only workspace folder sizes. The largest initial workspace file was the excluded upstream SQLite database, 1,134,592 bytes. No GitHub file-size concern was identified within these local review thresholds.
- The external checkout is clean at `4ac0d2e5109de526120785a6bf16a50d4673b56e`. The full local MIT notice was copied exactly into [third-party notices](../../docs/THIRD_PARTY_NOTICES.md). Upstream availability, current provider terms, fresh installation and cross-platform behavior were not network-verified.

## Scope of readiness

The candidate package supports reading the results-only research outputs, tracing preserved audit provenance, environment setup with pre-provisioned packages, integrity checks and the reviewed mocked/synthetic tests. It does not supply private odds inputs or reproduce the original joins/evaluation/comparison. Odds-sharing permissions, original-code ownership/licence choice, source-rights requirements, and the actual GitHub repository/publication decision remain with the owner. See [REVIEW.md](REVIEW.md).
