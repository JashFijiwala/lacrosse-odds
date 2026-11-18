# Data access and attribution

This document records evidence on disk as of 2026-11-18. No network request, download, scraping or external licence verification was performed during preparation.

## Public results provenance

The saved results come from [dwillis/NCAALacrosseData](https://github.com/dwillis/NCAALacrosseData), pinned at **`4ac0d2e5109de526120785a6bf16a50d4673b56e`**. The local checkout's HEAD equals that pin and its working tree is clean. The pin is also hard-coded in [join_audit.py](../audit_join/join_audit.py) and recorded in the [data-layer report](../elo_feas/out/data_layer_report.md). Upstream describes the data as NCAA Division I statistics collected from stats.ncaa.org and credits Derek Willis.

The checked local repository has an MIT licence, copyright 2022 Matt Waite. Preserve its full notice, reproduced in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), with redistributed upstream material and results-derived tables. No separate data licence or NCAA permission statement was found locally. The MIT notice is the local repository-level basis for treating reviewed results-only artifacts as Git candidates; it is not an independent verification of NCAA source rights. Resolve any institutional or provider requirements before broader public redistribution. It also does not license our original code; no project licence has been added.

## Representing the external checkout

`external/` remains ignored. It is a separate nested Git repository, with no parent gitlink or `.gitmodules`; do not accidentally stage it as one. Use documented pinned retrieval instead of vendoring or silently converting it to a submodule. Its environment, scrapers, database, player data and unrelated seasons are unnecessary for the safe checks here.

For later, separately authorized public-source setup (not executed during preparation):

```powershell
git clone --no-checkout https://github.com/dwillis/NCAALacrosseData external/NCAALacrosseData
git -C external/NCAALacrosseData checkout --detach 4ac0d2e5109de526120785a6bf16a50d4673b56e
git -C external/NCAALacrosseData rev-parse HEAD
git -C external/NCAALacrosseData status --short
```

Run these only when the destination does not already exist. Verify the printed commit and a clean status; read and retain upstream's licence. For an offline machine, arrange an approved local clone or bundle at that same commit. Do not run upstream scrapers or install their dependencies to reproduce this project's saved inputs. In this maintenance task, do not open the raw 2025/2026 files even after setup.

## Results schema and coverage

The schema below is verified from [data_layer.py](../elo_feas/data_layer.py) and the recorded reports, without reopening raw 2025/2026 results.

| Seasons | Headers | Team names |
|---|---|---|
| 2022 | `date`, `team`, `opponent`, `home_away`, `home_score`, `visitor_score`, `overtime` | Row-team mascot suffix stripped by the longest same-season opponent prefix |
| 2023 | `Date`, `Team`, `Opponent`, `Home / Away`, `Home Score`, `Visitor Score`, `Overtime` | Same mascot rule |
| 2024 | Same title-case family as 2023 | No mascot stripping |
| 2025, 2026 | Same snake-case family as 2022 | No mascot stripping |

The fifth column, `result`/`Result`, is excluded by the loader. Outcomes come from goals. “Home Score” and “Visitor Score” mean the row team's and opponent's goals regardless of the venue flag; they do not imply canonical home/away orientation. Two reciprocal rows generally become one canonical game, with explicit single-sided/conflict handling. Frozen name/join rules must not be changed to improve coverage retrospectively.

The [recorded coverage](../elo_feas/audit_out/calendar_coverage.csv) runs through 2025-04-08 in 2025, rather than the late-May endpoints in 2022-2024. Source availability is not full-season completeness. Venue flags vary by season; see [limitations](RESEARCH_STATUS.md).

## Private odds and derived artifacts

The locked [pilot design](PILOT_DESIGN.md) prohibits publishing `pilot/raw/` and `pilot/normalized/`. No local redistribution grant or provider contract was found. Therefore raw prices, event payloads, joined event tables, snapshot metadata, per-game de-vigged probabilities, and market-loss reports outside those directories are also **unresolved sharing permission**, unless the per-file inventory explicitly places an artifact in the results-only candidate set. New documentation describes scope and methods without exporting the market-loss tables or price/probability observations.

The Cradl observations are quoted odds, not automatically public project data. Keep them withheld pending permission too. Do not recommend or undertake independent re-purchase by teammates. A private GitHub repository is still an upload to a third party and does not solve this permission question.

## Inputs by workflow

| Workflow | Required inputs | Fresh-clone capability |
|---|---|---|
| Read frozen Elo outputs and results-only diagnostics | Reviewed `elo_feas/out/` and results-only audit files; upstream attribution | Available from proposed commit |
| Synthetic engine, schema adapter and joining tests | Code, small configuration/crosswalk files, dependencies | Available; no external data or key |
| Mocked pilot tests | Code/configuration, fake API fixtures in tests | Available; no real ledger or key |
| Rebuild selection data (2022-2024) | Pinned public matchstats CSVs for those seasons | Requires separate source setup and a future separate-output protocol; not run here |
| Rebuild original 2025/2026 joins | Pinned results, private raw odds, original ledger, frozen crosswalks | Unavailable without permitted private inputs; not an onboarding command |
| Recreate market comparison | Frozen predictions, run-2 PRIMARY links and team-keyed moneylines, original timing inputs/spec/gates | Withheld inputs; original run must not be rerun in place |
| New API acquisition | Explicit authorization, real key, valid baseline/accounting and preceding gates | Outside offline collaboration scope |

Paths and publication decisions are enumerated in [publication_inventory.csv](../repo_prep/2026-11-18/publication_inventory.csv). Withheld artifacts remain on the original workspace at their original paths; the inventory does not authorize their transfer.
