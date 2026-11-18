# AGENTS.md

Pilot of The Odds API historical endpoints for NCAA men's lacrosse, with a hard cap of 500 credits.
The full design and locked rules are in `docs/PILOT_DESIGN.md`. Read it before changing anything.

## Commands (always use the venv interpreter; do not rely on Activate.ps1)

```
.\.venv\Scripts\python.exe -m pytest -q                 # offline tests, no network
.\.venv\Scripts\python.exe -m src.cli plan
.\.venv\Scripts\python.exe -m src.cli ledger-status
.\.venv\Scripts\python.exe -m src.cli bootstrap-baseline   # zero-cost /v4/sports, once
.\.venv\Scripts\python.exe -m src.cli reconcile --fingerprint <fp>
.\.venv\Scripts\python.exe -m src.cli clear-halt --reason-code <CODE> --resolution "<at least 20 chars>"
```

## Locked rules

- `OddsClient` in `src/odds_client.py` is the ONLY code path that calls the API. Never call the API
  with ad-hoc requests, curl or a browser using the key.
- Never print, echo, log or commit the API key or any `.env` content. Tests use the fake key `TESTKEY123`.
- Never publish the key or the raw data (`pilot/raw/`, `pilot/normalized/` are gitignored).
- Never retry a request after a HALT without reconciling first: reconcile → investigate → clear-halt.
- Never edit or delete ledger rows. Corrections are new rows.
- Do not delete `HALT` or `pending.json` by hand.
- Stage budgets: A 20, B 80, C 240, D 60, E 30, RESERVE 70. Do not start a stage until the previous
  gate (Gate A / Gate B as revised; see the design doc) has been passed and recorded.
- Keep the returned snapshot `timestamp`, the requested `date`, the market-level `last_update` and
  `commence_time` distinct.
- Unchanged repeated prices are not line movements.
- Settlement math is "theoretical" until each book's rules have been verified.
- Analyse within-book and cross-book spreads separately.
- The 300/100/100 numbers are planning targets, not sufficiency claims.
- Do not commit without being asked.
