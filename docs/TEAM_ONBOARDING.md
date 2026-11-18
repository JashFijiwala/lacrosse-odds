# Team onboarding

Read [README](../README.md), [data access](DATA_ACCESS.md), then [reproducibility](REPRODUCIBILITY.md). The supported and locally verified setup is Windows PowerShell with CPython **3.10.9**. Other platforms and Python versions have not been validated. The upstream scraper's Python 3.12 requirement is separate; this project consumes saved CSVs and does not require running that scraper.

## Environment setup

Work from the clone's root. Have an approved CPython 3.10.9 installation available. If `.venv` does not exist, use that installation's absolute interpreter path to create it (replace the placeholder before running):

```powershell
& '<path-to-approved-Python-3.10.9>\python.exe' -m venv .venv
```

This is the bootstrap exception: the virtual environment interpreter cannot create itself. All subsequent Python commands use it directly:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install --no-index --find-links '<approved-local-wheel-directory>' -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

The wheel directory must already contain the pinned distributions compatible with Windows and CPython 3.10. If it is unavailable, stop dependency installation and arrange approved package access. With that access separately authorized, the installation command is ` .\.venv\Scripts\python.exe -m pip install -r requirements.txt` (network-capable; not part of offline checks). Do not upgrade pins to make installation easier. All 31 installed distribution versions matched the existing requirements during preparation; installation in a new environment was not attempted.

## Safe offline commands

```powershell
.\.venv\Scripts\python.exe -B tools\repository_checks.py
.\.venv\Scripts\python.exe -B tools\offline_tests.py
```

`repository_checks.py` reads the explicit commit inventory, checks hashes of available immutable files, validates current documentation links, checks dependency metadata and imports, and reports input presence by filename only. It never loads `.env`, instantiates `OddsClient`, or opens upstream results. Missing private files are expected in a clone; missing approved candidate files are failures.

`offline_tests.py` runs four reviewed pytest groups in separate processes, with network and private-input guards. It preserves the pilot's `conftest.py` fixtures and uses `--noconftest` for joining/Elo groups. Its exact selections are in [reproducibility](REPRODUCIBILITY.md). Test-generated mock data is temporary and is not research data.

## What a clone lacks

| Missing item | Reason / next step |
|---|---|
| `.env`, credentials | Local secrets; no key is needed for offline onboarding |
| `.venv`, package caches | Machine-specific; create the environment above |
| `external/NCAALacrosseData/` | Independent ignored repository; use the pinned retrieval procedure in [data access](DATA_ACCESS.md) when authorized |
| `pilot/raw/`, `pilot/normalized/` | Explicit prohibition on publishing odds data |
| `pilot/ledger/` | Private operational accounting; never fabricate or reset it for a clone |
| Odds-derived joins, per-game market probabilities and comparison reports | Sharing permission unresolved; see the per-file inventory |

Use the checker to diagnose file presence. Do not run bootstrap, reconcile, stage commands or a scraper in response to missing files. Do not copy the entire original workspace. Ask the project owner for permitted access to a specific input and confirm its recorded hash. If rights do not permit transfer, work on the published results-only artifacts and synthetic tests.

## Local Git ownership checks

During preparation, the sandbox account differed from the workspace owner. Read-only Git commands used `git -c safe.directory=E:/projects/lacrosse-odds-research -c core.excludesFile=NUL ...`, without changing global Git configuration. A teammate's own clone should normally not need an exception. Investigate an ownership warning before trusting an unfamiliar repository; do not add a global wildcard exception.
