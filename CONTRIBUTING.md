# Contributing

Read [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), and [the pilot design](docs/PILOT_DESIGN.md) first. Follow applicable subdirectory instructions. Use the virtual environment interpreter for every Python command.

## Changes and review

Once a maintainer has created the GitHub repository, use a topic branch and a pull request against the maintainer's default branch. Describe the concrete change, affected inputs and outputs, protocol implications, and offline checks run. Keep documentation and software changes separate from research experiments when practical. Do not commit, push, change visibility, or invite collaborators on somebody else's behalf without authorization.

Before staging, compare each path against the [publication inventory](repo_prep/2026-11-18/publication_inventory.csv). Stage explicitly enumerated paths only; do not use `git add .` or `git add -A`. Inspect the staged diff and filenames locally without exposing credential values. `.gitignore` does not remove already tracked files from Git history.

## Protect inputs and provenance

- Never commit credentials, `.env`, private keys, authorization headers, raw or normalized odds, or operational accounting state. [.env.example](.env.example) contains only an empty variable assignment. Offline work needs no key.
- Derived odds do not automatically become shareable. Obtain and document permission before uploading a withheld artifact, including to a private repository. Do not re-purchase API data to work around access restrictions.
- Preserve all frozen research artifacts, including failed runs, pre-registrations, amendments, hashes and independent implementations. Do not rename, move, edit or overwrite existing audit files. The ten protected source paths are listed in [protected_hashes.txt](audit_join/protected_hashes.txt).
- Keep frozen parameters, predictions, join rules, inclusion rules, market comparisons and numerical outputs unchanged. Model or rule changes require a new documented protocol, including information cutoffs and an evaluation design that acknowledges prior inspection of 2025.
- For future authorized experiments, choose a new output directory before running anything; record the protocol, input hashes, code revision, environment, timestamps, exclusions and deviations. Link back to prior runs. A separate output path is necessary but is not authorization to rerun the frozen evaluator or comparison.
- Never edit or delete ledger rows, HALT, pending records, or credit-accounting records. Corrections are appended through the supported workflow.

## Validation and acquisition

Run the [permitted offline checks](docs/REPRODUCIBILITY.md) in separate processes. Report skipped integration tests and missing inputs honestly; skipped tests are not reproduced results. Do not widen testing into raw 2025/2026 results, evaluator calls or protected-output writers during repository maintenance.

No API acquisition without explicit authorization, current accounting, a passed and recorded preceding gate, and budget checks. The hard cap is 500 credits; stage maxima are A 20, B 80, C 240, D 60, E 30, RESERVE 70. Only `src/odds_client.py` may call the API. The sequence after HALT is reconcile, investigate, clear-halt. See the design for the complete locked rules.

## Interpretation

Distinguish requested date, returned snapshot timestamp, market last-update time and scheduled start. Repeated unchanged prices are not movements. Analyze within-book and cross-book spreads separately. Settlement calculations remain theoretical until the relevant bookmaker rules are verified. The 300/100/100 figures are planning targets, not sufficiency claims.
