# NCAA men's lacrosse: odds pilot and Elo feasibility

This project asks whether historical NCAA men's lacrosse odds can be matched reliably to game results, and whether a simple Elo pipeline is a useful starting point for further research. It is a feasibility study, not evidence of a betting advantage.

The workspace contains completed acquisition and matching work, a frozen Elo model (**K=40, lambda=1.0, h=0**), independent audits, and a completed pre-registered descriptive comparison on **57 PRIMARY games across five dates in early 2025**. The 2025 data has since been inspected extensively. It is **not an untouched test set for future model development**. See [research status](docs/RESEARCH_STATUS.md) for verified scope and limitations.

## Start here

1. Read this README, then [team onboarding](docs/TEAM_ONBOARDING.md).
2. Read [data access and attribution](docs/DATA_ACCESS.md) before obtaining or sharing inputs.
3. Read [reproducibility](docs/REPRODUCIBILITY.md) for permitted commands and excluded runs.
4. Use the [artifact index](docs/ARTIFACT_INDEX.md) to distinguish frozen records from later diagnostics.
5. Read [contributing](CONTRIBUTING.md) before changing code or proposing an experiment.

## Quick start: Windows, offline research checks

The verified environment is **CPython 3.10.9 on Windows**. Preserve [requirements.txt](requirements.txt). If `.venv` already exists, use its interpreter directly; activation is unnecessary. For a fresh clone, create the environment and install dependencies as described in [onboarding](docs/TEAM_ONBOARDING.md). Installation needs a pre-provisioned interpreter and either an offline wheel directory or separately authorized package access; no packages were downloaded during repository preparation.

```powershell
.\.venv\Scripts\python.exe -B tools\repository_checks.py
.\.venv\Scripts\python.exe -B tools\offline_tests.py
```

Neither command needs a real API key, private odds inputs, or the external results checkout. The second runs only the reviewed subsets in separate processes. Do not substitute a bare `pytest` run: the historical default suite mixes incompatible fixtures and can open private or raw season data.

## Repository map

| Path | Purpose |
|---|---|
| [src/](src/) | Guarded API acquisition, normalization, analysis, credit accounting |
| [pilot/config/pilot.yaml](pilot/config/pilot.yaml) | Locked 500-credit cap and stage budgets |
| `pilot/raw/`, `pilot/normalized/`, `pilot/ledger/` | Private/local inputs, derived odds, and operational accounting; absent from a clone |
| [audit_join/](audit_join/) | Frozen joining code, protected-code hashes, archived run 1 and revised run 2; some outputs withheld |
| [elo_feas/](elo_feas/) | Elo/data-layer code, frozen results-only outputs, and successive audits |
| [tests/](tests/), [elo_feas/tests/](elo_feas/tests/) | Mocked pilot tests, synthetic joining/engine tests, and restricted integration tests |
| `external/NCAALacrosseData/` | Separate, ignored, pinned upstream repository; not a submodule or vendored dependency |
| [docs/](docs/) | Design, onboarding, access, reproduction, research status, and artifact index |
| [repo_prep/2026-11-18/](repo_prep/2026-11-18/) | Publication inventory, explicit commit list, integrity manifests, validation evidence |

## Two workflows with different authorization

**Offline research:** inspect published results-only tables and reports, verify hashes, and run the selected synthetic/mocked tests. This can be done without private odds inputs. A fresh clone cannot rebuild the original odds joins or market comparison. See [data requirements](docs/DATA_ACCESS.md) and [pipeline stages](docs/REPRODUCIBILITY.md).

**API acquisition: commands can consume credits and require explicit budget and gate authorization.** Do not run acquisition to repair missing inputs in a clone. `OddsClient` is the only authorized API path. Even bootstrap and reconciliation make network requests; they are outside offline onboarding. Read [the locked design](docs/PILOT_DESIGN.md), [AGENTS.md](AGENTS.md), and [CLAUDE.md](CLAUDE.md). After a HALT, reconcile, investigate, then clear the halt through the supported command; never delete state or retry first. The design's opening “scaffold only” status is historical; use the newer status and artifact index for completed work.

## Publication and ownership

The [publication review](repo_prep/2026-11-18/REVIEW.md) identifies exactly which files are candidates for the first commit. Raw and normalized odds must never be published under this project's instructions. Other odds-derived files remain withheld pending sharing permission, even when outside those directories. A private GitHub repository does not by itself establish permission to upload them.

No project code licence has been chosen. The upstream results repository's MIT notice is preserved in [third-party notices](docs/THIRD_PARTY_NOTICES.md); it does not license this project's code or settle separate source-provider rights.
