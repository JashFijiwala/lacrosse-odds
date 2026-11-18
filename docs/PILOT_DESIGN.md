# Pilot design: The Odds API historical endpoints, NCAA men's lacrosse

Status: scaffold only (Chunk 2). No API request has been made yet.

## 1. Budget

- Hard credit cap: **500** (from `pilot/config/pilot.yaml`, also enforced by `HARD_CAP` in `src/odds_client.py`).
- Stage budgets:

| Stage | Credits |
|---|---|
| A | 20 |
| B | 80 |
| C | 240 |
| D | 60 |
| E | 30 |
| RESERVE | 70 |
| **Total** | **500** |

RESERVE is not a stage. It is drawn on only with `request(..., use_reserve=True)`, and every draw is
recorded as `reserve_draw=N` in the ledger notes.

## 2. Staged plan, gates and planning targets

### 2.1 Stages

Credits are maximums; actual spend is measured from quota headers. Global cap 500.

- **Stage 0 (0 credits):** `GET /v4/sports?all=true` via `bootstrap_baseline()`. Sets the baseline;
  confirms remaining >= 500 and that `lacrosse_ncaa` is listed.
- **Stage A (<=20): coverage discovery.** Historical-events probes at 15 dates (12:00Z; <=1 credit when
  non-empty, 0 when empty), optional participants call (1), anchor detection for Quinnipiac–Air Force
  (2025-02-28) and NJIT–Vermont (2026-03-28).
- **Stage B (<=80): odds content at the close-proxy snapshot.** Anchors at start minus 60 s (revised from
  start minus 10 min; see 2.4), markets `h2h,spreads,totals`, regions `us` (30 each); one moneyline-only
  snapshot with regions `us,us2` (20).
- **Stage C (<=240): movement chains.** Both anchors at start minus 48h, 24h, 6h, 1h (30 each).
- **Stage D (<=60): breadth.** Saturdays 2025-03-08T15:50Z and 2026-03-07T16:50Z (30 each; revised, see 2.4).
- **Stage E (<=30, conditional):** one odds snapshot on a pre-2025 date, only if Stage A shows pre-2025
  events visible.
- **RESERVE (<=70):** retries, one extra close snapshot, at most 4 Stage A follow-up probes. Requires
  `use_reserve`.

### 2.2 Gates

- **Gate A:** GO if a non-empty probe in 2025-02-01..2025-05-03, a non-empty probe in
  2026-01-31..2026-05-02, and both anchors found. If an anchor is missing or a season looks empty, the
  result is DIAGNOSE (at most 4 follow-ups). An empty probe means "no events visible at that snapshot",
  never "no coverage".
- **Gate B:** GO if both anchors' event IDs appear in the odds payload, at least one target book has a
  complete two-sided moneyline, and spreads and totals appear somewhere. Cradl comparison is a
  reconciliation, not pass/fail: log the API snapshot time, the scheduled start and Cradl's observation;
  classify each as exact, tick difference or different; investigate differences. The only hard stop is
  no target-book quote in a pre-start snapshot of either anchor after one fallback snapshot.
- **Target books:** draftkings, fanduel, betmgm (keys to be confirmed from the payload).

### 2.3 Planning targets

These are **not sufficiency claims**. A 500-credit pilot cannot measure them; power analysis comes later.

- **T1 >= 300:** distinct games with a complete two-sided pregame moneyline from at least one target
  book, identified as D1 men's, matched to a historical result, after deduplication.
- **T2 >= 100:** games with at least 2 target books complete and two-sided in the same snapshot.
- **T3 >= 100:** games with at least 3 distinct pregame snapshots carrying a complete quote. Separately
  count games with at least one genuine price or point change; unchanged repeats are not movement, and
  movement-analysis sufficiency is judged by that count.

### 2.4 Revisions after Stage A

- **(a) Close-proxy snapshot = start minus 60 s.** This supersedes start minus 10 minutes. It is a
  "close-proxy snapshot", not a final pre-start price. A quote can be stale even when the snapshot is
  close to the start, so always keep the returned snapshot `timestamp` and each market-level
  `last_update`. `staleness_seconds` = returned timestamp minus market `last_update`; above 900 s is
  flagged STALE_QUOTE. A snapshot more than 900 s before the start is flagged STALE, and one after the
  start is flagged POST-START. The bookmaker-level `last_update` is deprecated and is not used.
- **(b) Observed snapshot grid: every 5 minutes at :38 past.** From Stage A, a request for 12:00:00Z
  returned 11:55:38Z. A requested time therefore maps to the preceding grid point, up to about 5 minutes
  earlier.
- **(c) Stage D times are 2025-03-08T15:50Z and 2026-03-07T16:50Z.** Observed start times include 16:00Z,
  17:00Z, 18:00Z, 18:30Z and 19:00Z, so the 2025 snapshot moves one hour earlier to precede the earliest
  starts.
- **(d) Sampled visibility only.** The empty 2025-01-25 probe and the non-empty 2025-02-01 probe describe
  what was visible at those sampled snapshots. They do not establish when the historical archive begins
  for this sport.

### 2.5 Stage B completeness classification (fetch and report only)

Per bookmaker and market, for the anchor event, using the payload's own `home_team` and `away_team`:

- **h2h** is `complete_paired` only with exactly two outcomes named exactly the home and away teams (no
  duplicates, no other names), each with a numeric American price with abs >= 100.
- **spreads** is `complete_paired` only with exactly two outcomes named for the home and away teams, each
  with a numeric price and point, and the points equal in magnitude and opposite in sign. Same-sign or
  unequal points (including 0 / 0) are `present_mismatched`.
- **totals** is `complete_paired` only with outcomes Over and Under, each with a numeric price, sharing
  the same numeric point.
- Anything else that exists is `present_incomplete`; a missing market key is `absent`.

A step is PASS when the anchor event is present, at least one bookmaker's h2h is `complete_paired`, and
the cost check passes (a non-empty payload costs exactly the maximum; 0 only for an empty payload).
Otherwise it is STOP: later steps are blocked, and one fallback snapshot 15 or 30 minutes earlier may be
run per anchor (`--fallback-minutes N`, use_reserve=True).

## 3. Time semantics (do not conflate)

| Field | Meaning |
|---|---|
| requested `date` | The query parameter we send: "give me the snapshot at or before this time". |
| returned snapshot `timestamp` | The time of the snapshot the API actually returned. It can be earlier than the requested `date`. Analysis uses this, never the requested date. |
| market-level `last_update` | When that bookmaker's market last changed. It can be much older than the snapshot timestamp. |
| `commence_time` | Scheduled game start. Pre-game vs in-play classification compares snapshot `timestamp` to `commence_time`. |

## 4. Interpretation rules

- **Unchanged repeated prices are not line movements.** The same price seen in consecutive snapshots
  is a single observation that persisted. A movement needs a changed price or point, ideally with a
  changed `last_update`.
- **Settlement math is "theoretical"** until each bookmaker's settlement rules (overtime, push and
  void handling, minimum-play rules) have been checked against that book's published rules. Label
  every such result "theoretical".
- **Within-book and cross-book spreads are analysed separately.** A within-book spread is the vig or
  overround across one book's outcomes. A cross-book spread is the difference between books on the
  same outcome. They are never pooled into one statistic.

## 5. Safety rules (locked)

1. `src/odds_client.py` (`OddsClient`) is the **only** code path that calls the API.
2. **Never publish the API key or the raw data.** `.env`, `pilot/raw/` and `pilot/normalized/` are
   gitignored. The key is added to the query string at the last moment. It is never stored in params,
   the ledger, sidecars, logs or exception messages.
3. **No paid request before `baseline.json` exists and validates.** The baseline comes from a
   zero-cost `/v4/sports` call (`cli bootstrap-baseline`).
4. Pre-flight refusals happen before any network call. A request is refused if any of these hold:
   - a HALT is active, or the in-process halt flag is set
   - the baseline is missing or corrupt
   - `pending.json` exists
   - pessimistic committed spend + expected max would exceed the cap or the stage budget (unless
     `use_reserve`)
   - the fingerprint already has a 200 row (unless `allow_duplicate`)
   - the fingerprint has an unresolved UNKNOWN or PERSIST_FAILED row
5. **Pessimistic accounting:** an uncertain request counts at its expected maximum until it is
   reconciled.
6. **Write-ahead:** `pending.json` is written with fsync before each paid call, and is removed only
   after the ledger row is safely written.
7. **No retries of any kind** (`max_retries=0`, no retry loops).
8. **Never retry a request after a HALT without reconciling.** The order is always:
   `reconcile --fingerprint <fp>` → investigate → `clear-halt --reason-code <code> --resolution "<why>"`.
   Every HALT and its resolution is kept in `pilot/ledger/halt_history.jsonl` and `events.log`.

## 6. Files

| Path | Purpose |
|---|---|
| `pilot/config/pilot.yaml` | sport, base URL, cap, regions, markets, formats, stage budgets |
| `pilot/ledger/credit_ledger.csv` | append-only ledger (columns listed in `src/ledger.py: COLUMNS`) |
| `pilot/ledger/baseline.json` | `used`, `remaining`, `ts` at bootstrap; `cumulative_spent = header_used - baseline.used` |
| `pilot/ledger/pending.json` | write-ahead record of the in-flight request |
| `pilot/ledger/HALT` | active HALT (JSON) |
| `pilot/ledger/halt_history.jsonl`, `events.log` | audit trail |
| `pilot/ledger/emergency_*.json` | metadata for a response whose body could not be saved |
| `pilot/raw/{stage}_{UTCstamp}_{endpoint}_{date}.json` + `.meta.json` | raw bodies and sidecars (gitignored) |
| `pilot/normalized/` | derived tables (gitignored) |
| `pilot/figures/` | figures |

### Ledger status values

`status_code` holds the HTTP status (`200`, `429`, ...) or one of these values:

| Value | Meaning |
|---|---|
| `NO_RESPONSE` | timeout or connection error; `actual_cost` = `UNKNOWN` |
| `PERSIST_FAILED` | a response arrived but the body or sidecar could not be saved; the HTTP status is in `notes` |
| `RECONCILED` | a zero-cost `/v4/sports` check. `actual_cost` = credits attributed to the reconciled fingerprint (the unexplained change in `x-requests-used`). `notes` includes `reconciles=<fp>` and, where the response is unrecoverable, `refetch_of=<fp>`. |
| `RECONCILE_MISMATCH` | as RECONCILED, but the unexplained usage exceeded the pessimistic estimate. Raises HALT UNEXPECTED_COST. |
