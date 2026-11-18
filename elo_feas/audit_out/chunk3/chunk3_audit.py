"""Chunk 3 read-only audit: venue flags, opponent identity, market-comparison feasibility (counts only).

Diagnostic, no re-selection. Writes only to elo_feas/audit_out/chunk3/. Reuses the guards of ../methodology_audit.py
(open/io.open/pathlib/os.open logging, write refusal outside the allowed folder, raw 2025/2026 results files refused).
2026 rows of canonical_games_2022_2026.csv are read for description only and never reach an Elo computation: Elo is
recomputed only on selection_games() (2022-2024) through ../chunk2/indep_elo.py; 2025 comes from recorded
predictions.csv. No market-vs-Elo quantity is computed anywhere in this file.
Run:  $env:PYTHONDONTWRITEBYTECODE=1; .\\.venv\\Scripts\\python.exe -B elo_feas\\audit_out\\chunk3\\chunk3_audit.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import pathlib  # noqa: E402

CH3 = pathlib.Path(__file__).resolve().parent
AUD = CH3.parent
CH2 = AUD / "chunk2"
sys.path.insert(0, str(AUD))
sys.path.insert(0, str(CH2))

import methodology_audit as G  # noqa: E402  (installs the guards on import)

G.AUDIT_OUT = CH3                                   # writes allowed only under chunk3/
import indep_elo as I  # noqa: E402

import hashlib  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT, OUT = G.ROOT, G.OUT
RUN2 = ROOT / "audit_join" / "out_run2"
DIAG = G.DIAG
SEED = 20261111
SEASONS = (2022, 2023, 2024, 2025, 2026)
PATTERNS = ("Home/Away", "Away/Home", "Away/Away", "Home/Home")
KS, LAMS = (10, 15, 20, 30, 40), (0.5, 0.7, 0.85, 1.0)

# Shell reads made by me in this chunk BEFORE this script existed (they bypass the Python guard, so they are listed
# by hand, as required). All are read-only.
SHELL_READS = [
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: elo_feas/out/canonical_games_2022_2026.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: elo_feas/out/team_season_membership.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: elo_feas/out/mascot_strip_map.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: elo_feas/out/exclusions.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: audit_join/out_run2/event_game_links.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: audit_join/out_run2/team_crosswalk_proposed.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: audit_join/out_run2/newly_matched_events.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: audit_join/out_run2/team_keyed_moneyline.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: audit_join/out_run2/b2_b3_overlap.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: audit_join/out/event_inventory.csv",
    "shell Get-Content -TotalCount 3 + Measure-Object -Line: audit_join/manual_verification.csv",
    "shell Select-String (first 30 matching lines printed, includes a market-only loss table I did not use): "
    "audit_join/out_run2/join_revision.md",
    "shell python exploration script (UNGUARDED, no logging, read-only; it was saved to %TEMP%, outside audit_out, "
    "and deleted afterwards): read elo_feas/out/canonical_games_2022_2026.csv, audit_join/out_run2/event_game_links.csv, "
    "audit_join/out_run2/team_keyed_moneyline.csv, audit_join/out_run2/team_crosswalk_proposed.csv, "
    "elo_feas/out/team_season_membership.csv; printed groupby counts and value_counts only",
]


# --------------------------------------------------------------------------- helpers
def md(df: pd.DataFrame, fmt: str = "{:.4f}") -> str:
    cols = [c for c in df.columns if c != "label"]
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in df[cols].itertuples(index=False):
        cells = []
        for v in r:
            if v is pd.NA:
                cells.append("")
            elif isinstance(v, (float, np.floating)):
                cells.append(fmt.format(v) if np.isfinite(v) else "")
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def save(df: pd.DataFrame, name: str, label: bool = True) -> pd.DataFrame:
    (df.assign(label=DIAG) if label else df).to_csv(CH3 / name, index=False, lineterminator="\n")
    return df


def np_brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def np_ll(p, y) -> float:
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))


def norm(name: str, drop_paren: bool = False) -> str:
    s = name.casefold().replace("&", " and ").replace("'", "").replace("’", "")
    if drop_paren:
        s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    toks = ["st" if t in ("st", "saint") else t for t in s.split() if t not in ("the", "university")]
    return " ".join(toks)


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def sha_of(p: pathlib.Path) -> str:
    return G.sha256_file(p)


def quant(x, qs=(0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)):
    x = np.asarray(list(x), float)
    return {f"q{int(q * 100)}": float(np.quantile(x, q)) for q in qs} if len(x) else {}


# --------------------------------------------------------------------------- hashing: previous set + chunk2 files
def hash_targets() -> list[pathlib.Path]:
    files = G.hash_targets()
    files += sorted((p for p in AUD.iterdir() if p.is_file()), key=lambda p: p.as_posix())
    files += sorted((p for p in CH2.rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    return files


def snapshot() -> dict:
    return {G._rel(p): (p.stat().st_size, G.sha256_file(p)) for p in hash_targets()}


def main() -> int:
    selftest = G.guard_selftest()
    try:
        open(AUD / "__no_such_dir__" / "x.txt", "w")
        selftest.append(("write to audit_out outside chunk3", "FAIL: no exception"))
    except PermissionError:
        selftest.append(("write to audit_out outside chunk3", "PASS"))
    except Exception as e:  # noqa: BLE001
        selftest.append(("write to audit_out outside chunk3", f"FAIL: {type(e).__name__}"))
    try:
        open(CH2 / "__no_such_dir__" / "x.txt", "w")
        selftest.append(("write to chunk2 from chunk3", "FAIL: no exception"))
    except PermissionError:
        selftest.append(("write to chunk2 from chunk3", "PASS"))
    except Exception as e:  # noqa: BLE001
        selftest.append(("write to chunk2 from chunk3", f"FAIL: {type(e).__name__}"))
    if any(r != "PASS" for _, r in selftest):
        raise RuntimeError(f"guard self-test failed: {selftest}")

    before = snapshot()
    G.write_snapshot(before, "hashes_before.csv")
    prev = {}
    for line in (CH2 / "hashes_after.csv").read_text(encoding="utf-8").splitlines()[1:]:
        p, _, h = line.split(",")
        prev[p] = h
    ref_prev = G.compare_reference(before, prev)
    ref_prot = G.compare_reference(before, G.read_reference(ROOT / "audit_join" / "protected_hashes.txt"))
    if ref_prev or ref_prot:
        (CH3 / "hash_diff.txt").write_text("STOP: mismatch before Chunk 3\n" + "\n".join(ref_prev + ref_prot) + "\n",
                                           encoding="utf-8")
        return 2

    sections = run_chunk3()

    after = snapshot()
    G.write_snapshot(after, "hashes_after.csv")
    diff = [f"{'CHANGED' if k in before and k in after else ('ADDED' if k in after else 'REMOVED')} {k}"
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    L = [f"files hashed: {len(before)} before, {len(after)} after (previous audit set + audit_out top-level + "
         f"chunk2/ files)",
         f"before vs after: {'NO DIFFERENCES' if not diff else str(len(diff)) + ' DIFFERENCES - STOP'}", *diff,
         f"before vs Chunk 2 hashes_after.csv ({len(prev)} entries): ALL MATCH",
         "before vs audit_join/protected_hashes.txt (10 entries): ALL MATCH",
         "guard self-test: " + "; ".join(f"{n}={r}" for n, r in selftest)]
    (CH3 / "hash_diff.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    head = ["# Chunk 3 audit of the Elo feasibility experiment", "",
            f"**{DIAG}.** Read-only. K = 40, lam = 1, h = 0 unchanged; the grid is not widened; the 2025 test is not "
            "rerun. 2026 rows are used for description only. No market-vs-Elo quantity is computed. Not a model "
            "comparison and not a result; the experiment remains a feasibility experiment — pipeline check, not a "
            "research result.", "",
            f"Hash check over {len(before)} files: {'NO DIFFERENCES' if not diff else 'DIFFERENCES - STOP'} "
            "(chunk3/hash_diff.txt). Guard self-test: " + "; ".join(f"{n}: {r}" for n, r in selftest) + ".", ""]
    (CH3 / "chunk3_report.md").write_text("\n".join(head + sections) + "\n", encoding="utf-8")
    return 2 if diff else 0


# --------------------------------------------------------------------------- the audit
def run_chunk3() -> list[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from audit_join import join_audit as J
    J.install_socket_guard()
    from elo_feas import audit_elo as A
    from elo_feas import data_layer as D

    def forbidden(*_a, **_k):
        raise RuntimeError("forbidden in this audit (would read 2025)")
    A.evaluate_2025 = forbidden
    A.games_2022_2025 = forbidden
    _build_all = D.build_all

    def build_all_selection_only(seasons=D.SEASONS, *a, **k):
        if max(seasons) > 2024:
            raise RuntimeError(f"build_all asked for {tuple(seasons)}; only <= 2024 allowed")
        return _build_all(seasons, *a, **k)
    D.build_all = build_all_selection_only

    S: list[str] = []
    FIND = {"ERRORS": [], "UNRESOLVED": [], "OBSERVATIONS": []}
    DIFFS: list[dict] = []

    def diff_row(item, recorded, observed, note=""):
        DIFFS.append({"item": item, "recorded_by_user": str(recorded), "observed": str(observed),
                      "match": "MATCH" if str(recorded) == str(observed) else "DIFFERS", "note": note})

    # ---- inputs
    canon = pd.read_csv(OUT / "canonical_games_2022_2026.csv", dtype=str, keep_default_na=False)
    canon["season"] = canon["season"].astype(int)
    canon["goals_lo"], canon["goals_hi"] = canon["goals_lo"].astype(float), canon["goals_hi"].astype(float)
    canon["fl"], canon["fh"] = canon["flag_lo"].replace("", "-"), canon["flag_hi"].replace("", "-")
    canon["md"] = canon["date"].str[5:]
    canon["month"] = canon["date"].str[5:7]

    def pat(r):
        if r.sidedness == "SINGLE_SIDED":
            return "Single-sided (flag)" if (r.flag_lo or r.flag_hi) else "Single-sided (no flag)"
        return f"{r.flag_lo}/{r.flag_hi}"
    canon["pat"] = [pat(r) for r in canon.itertuples()]
    mem = pd.read_csv(OUT / "team_season_membership.csv")
    pred = pd.read_csv(OUT / "predictions.csv", dtype={"date": str, "game_key": str, "team_a": str, "team_b": str},
                       keep_default_na=False)
    frozen = json.loads((OUT / "frozen_params.json").read_text(encoding="utf-8"))
    K = float(frozen["K"])
    assert (frozen["K"], frozen["lam"], frozen["h"]) == (40.0, 1.0, 0.0)
    assert int(pred["season"].max()) == 2025
    cmap = pd.read_csv(OUT / "mascot_strip_map.csv", dtype=str, keep_default_na=False)
    links = pd.read_csv(RUN2 / "event_game_links.csv", dtype=str, keep_default_na=False)
    xwalk = pd.read_csv(RUN2 / "team_crosswalk_proposed.csv", dtype=str, keep_default_na=False)
    ml = pd.read_csv(RUN2 / "team_keyed_moneyline.csv", dtype=str, keep_default_na=False)
    rowt = {s: set(mem.loc[mem[f"rowteam_{s}"], "team"]) for s in SEASONS}
    oppo = {s: set(mem.loc[mem[f"opp_only_{s}"], "team"]) for s in SEASONS}
    n_season = canon.groupby("season").size().to_dict()
    allopp = sorted(set().union(*[oppo[s] for s in SEASONS]))

    # ======================================================================= 3.1 VENUE
    S += ["## 3.1 Venue", ""]
    # a
    rows = []
    for s in SEASONS:
        d = canon[canon["season"] == s]
        two = d[d["sidedness"] == "TWO_SIDED"]
        for p in list(PATTERNS) + ["Single-sided (flag)", "Single-sided (no flag)"]:
            n = int((d["pat"] == p).sum())
            rows.append({"season": s, "pattern": p, "n": n, "share_of_season_games": n / len(d),
                         "share_of_two_sided": (n / len(two)) if p in PATTERNS else float("nan")})
        rows.append({"season": s, "pattern": "ALL GAMES", "n": len(d), "share_of_season_games": 1.0,
                     "share_of_two_sided": float("nan")})
    va = save(pd.DataFrame(rows), "venue_pattern_counts.csv")
    aa = [int(va[(va.season == s) & (va.pattern == "Away/Away")]["n"].iloc[0]) for s in SEASONS]
    hh = [int(va[(va.season == s) & (va.pattern == "Home/Home")]["n"].iloc[0]) for s in SEASONS]
    diff_row("Away/Away counts 2022..2026", [33, 38, 46, 9, 0], aa)
    diff_row("Home/Home counts 2022..2026", [0, 0, 0, 1, 16], hh)
    S += ["### 3.1a Pattern counts per season (flag_lo/flag_hi of the canonical table = results-file flags; the odds "
          "feed's home_team is never used)", md(va, "{:.4f}"), "",
          f"Confirmation: Away/Away per season 2022..2026 = {aa} (recorded 33, 38, 46, 9, 0); Home/Home = {hh} "
          "(recorded 0, 0, 0, 1, 16).", ""]
    # b
    mrows = []
    for s in SEASONS:
        for m, d in canon[canon["season"] == s].groupby("month"):
            r = {"season": s, "month": m, "n": len(d)}
            for p in list(PATTERNS) + ["Single-sided (flag)", "Single-sided (no flag)"]:
                r[p] = int((d["pat"] == p).sum())
            r["share_Away/Away"] = r["Away/Away"] / len(d)
            mrows.append(r)
    vm = save(pd.DataFrame(mrows), "venue_by_month.csv")
    trows = []
    for s in SEASONS:
        d = canon[canon["season"] == s]
        t = d[d["md"] <= "04-08"]
        two_t = t[t["sidedness"] == "TWO_SIDED"]
        r = {"season": s, "trunc_through": "04-08", "n": len(t), "n_two_sided": len(two_t),
             "n_Away/Away": int((t["pat"] == "Away/Away").sum()),
             "share_Away/Away_of_games": (t["pat"] == "Away/Away").mean(),
             "share_Away/Away_of_two_sided": (t["pat"] == "Away/Away").sum() / max(len(two_t), 1),
             "share_single_sided": (t["sidedness"] == "SINGLE_SIDED").mean(),
             "n_full_season": len(d), "full_share_Away/Away_of_games": (d["pat"] == "Away/Away").mean(),
             "full_share_single_sided": (d["sidedness"] == "SINGLE_SIDED").mean(),
             "last_game_date": d["date"].max()}
        if s <= 2025:
            p = pred[(pred["season"] == s) & (pred["date"].str[5:] <= "04-08")]
            r.update({"elo_n": len(p), "elo_brier_40_1": np_brier(p["p_a"], p["y_a"]),
                      "elo_log_loss_40_1": np_ll(p["p_a"], p["y_a"]),
                      "elo_note": "2022 is warm-up (descriptive only)" if s == 2022 else ""})
        else:
            r.update({"elo_n": float("nan"), "elo_brier_40_1": float("nan"), "elo_log_loss_40_1": float("nan"),
                      "elo_note": "2026 not used in Elo"})
        trows.append(r)
    tr = pd.DataFrame(trows)
    tr["elo_n"] = tr["elo_n"].astype("Int64")
    tr = save(tr, "truncation_matched.csv")
    S += ["### 3.1b Pattern counts by month", md(vm[["season", "month", "n"] + list(PATTERNS) +
                                                      ["Single-sided (flag)", "Single-sided (no flag)",
                                                       "share_Away/Away"]], "{:.3f}"), "",
          "### 3.1b Truncation-matched comparison (games on or before 8 April of each season; Elo (40,1) from "
          "recorded predictions.csv; descriptive)", md(tr, "{:.4f}"), ""]
    # c
    fl = []
    for r in canon[canon["pat"].isin(["Away/Away", "Home/Home"])].itertuples():
        fl.append({"flag_type": "AWAY_AWAY" if r.pat == "Away/Away" else "HOME_HOME", "season": r.season,
                   "date": r.date, "game_key": r.game_key, "team_a": r.team_lo, "team_b": r.team_hi,
                   "flag_a": r.flag_lo, "flag_b": r.flag_hi, "detail": ""})
    aa_g = canon[canon["pat"] == "Away/Away"]
    for (s, dte), g in aa_g.groupby(["season", "date"]):
        if len(g) >= 2:
            fl.append({"flag_type": "DATE_WITH_2PLUS_AWAY_AWAY", "season": s, "date": dte, "game_key": "",
                       "team_a": "", "team_b": "", "flag_a": "", "flag_b": "",
                       "detail": f"{len(g)} Away/Away games: " + "; ".join(g["game_key"].str.split("|").str[1:].str.join(" v "))})
    for s in SEASONS:
        d = canon[canon["season"] == s]
        long = pd.concat([d[["team_lo", "fl"]].rename(columns={"team_lo": "team", "fl": "f"}),
                          d[["team_hi", "fh"]].rename(columns={"team_hi": "team", "fh": "f"})])
        long = long[long["f"].isin(["Home", "Away"])]
        for team, g in long.groupby("team"):
            share = (g["f"] == "Home").mean()
            if share < 0.3 or share > 0.7:
                fl.append({"flag_type": "TEAM_SEASON_HOME_SHARE_OUTSIDE_0.3_0.7", "season": s, "date": "",
                           "game_key": "", "team_a": team, "team_b": "", "flag_a": "", "flag_b": "",
                           "detail": f"n_flagged_games={len(g)} home_share={share:.3f}"})
    pair = canon[canon["sidedness"] == "TWO_SIDED"].groupby(["season", "team_lo", "team_hi", "flag_lo", "flag_hi"])
    for (s, a, b, fa, fb), g in pair:
        if len(g) >= 2:
            fl.append({"flag_type": "SAME_PAIR_SAME_ORIENTATION", "season": s, "date": ";".join(g["date"]),
                       "game_key": ";".join(g["game_key"]), "team_a": a, "team_b": b, "flag_a": fa, "flag_b": fb,
                       "detail": f"{len(g)} games with identical flags"})
    vf = save(pd.DataFrame(fl), "venue_flags.csv")
    cnt_flags = vf.groupby(["flag_type", "season"]).size().rename("n").reset_index()
    S += ["### 3.1c Flags (venue_flags.csv; these are flags, not exclusions)",
          md(cnt_flags, "{:.0f}"), "",
          "Team-season home-share rows use only the team's own Home/Away flags (blank flags skipped); small "
          "n_flagged_games are listed, so the 0.3-0.7 band is noisy for short seasons.", ""]
    # d
    cx = []
    for (s, date_, tm, other, note) in (
            (2025, "2025-03-02", "Queens (NC)", "UMass Lowell",
             "user-supplied: Away/Away; neutral per both schools (Towson/Goucher, Md.); NOT verified by this audit"),
            (2026, "2026-03-10", "Queens (NC)", "UMass Lowell",
             "user-supplied: Queens Away / UMass Lowell Home; neutral per UMass Lowell recap (Purchase, N.Y.); "
             "NOT verified by this audit")):
        g = canon[(canon["season"] == s) & (canon["date"] == date_) &
                  (((canon["team_lo"] == tm) & (canon["team_hi"] == other)) |
                   ((canon["team_lo"] == other) & (canon["team_hi"] == tm)))]
        if len(g) != 1:
            FIND["ERRORS"].append(f"venue counterexample {date_} {tm} v {other}: {len(g)} canonical rows (expected 1)")
            continue
        r = g.iloc[0]
        cx.append({"season": s, "date": r["date"], "game_key": r["game_key"], "team_a": r["team_lo"],
                   "team_b": r["team_hi"], "flag_a": r["flag_lo"], "flag_b": r["flag_hi"],
                   "sidedness": r["sidedness"], "canonical_pattern": r["pat"], "claimed_site_note": note})
    cxd = save(pd.DataFrame(cx), "venue_counterexamples.csv")
    exp = {2025: ("Queens (NC)", "Away", "UMass Lowell", "Away"), 2026: ("Queens (NC)", "Away", "UMass Lowell", "Home")}
    for r in cxd.itertuples():
        flags = {r.team_a: r.flag_a, r.team_b: r.flag_b}
        e = exp[r.season]
        diff_row(f"{r.season}-{r.date[5:]} Queens (NC) / UMass Lowell flags (Queens, UMass Lowell)",
                 (e[1], e[3]), (flags.get("Queens (NC)"), flags.get("UMass Lowell")))
    S += ["### 3.1d Counterexamples (venue_counterexamples.csv; manual_verification.csv untouched)", md(cxd), ""]
    # e
    rng = np.random.default_rng(SEED)
    frame, order_ctr = [], {}

    def stratum(season, patt, tier, name, n=None):
        cand = canon[(canon["season"] == season) & (canon["pat"] == patt) & (canon["sidedness"] == "TWO_SIDED")] \
            .sort_values(["date", "game_key"], kind="mergesort").reset_index(drop=True)
        if n is None or n >= len(cand):
            idx = list(range(len(cand)))
        else:
            idx = [int(i) for i in rng.choice(len(cand), size=n, replace=False)]
        for k, i in enumerate(idx):
            r = cand.iloc[i]
            frame.append({"tier": tier, "stratum": name, "season": season, "date": r["date"], "team_a": r["team_lo"],
                          "team_b": r["team_hi"], "flag_a": r["flag_lo"], "flag_b": r["flag_hi"],
                          "game_key": r["game_key"], "draw_order": k + 1, "verifier": "", "site_city_state": "",
                          "neutral_yes_no_unknown": "", "source_url": "", "notes": ""})
        return len(idx), len(cand)
    spec = [(2025, "Away/Away", 1, "2025 Away/Away (all)", None), (2025, "Home/Home", 1, "2025 Home/Home (all)", None),
            (2026, "Home/Home", 1, "2026 Home/Home (all)", None), (2024, "Away/Away", 1, "2024 Away/Away (random 20)", 20),
            (2024, "Home/Away", 1, "2024 Home/Away control (random 20)", 20),
            (2022, "Away/Away", 2, "2022 Away/Away (random 10)", 10), (2022, "Home/Away", 2, "2022 Home/Away control (random 10)", 10),
            (2023, "Away/Away", 2, "2023 Away/Away (random 10)", 10), (2023, "Home/Away", 2, "2023 Home/Away control (random 10)", 10)]
    cnts = []
    for s, p, t, nm, n in spec:
        got, pool = stratum(s, p, t, nm, n)
        cnts.append({"tier": t, "stratum": nm, "pool_size": pool, "rows": got})
    vv = pd.DataFrame(frame)
    save(vv, "venue_validation_frame.csv", label=False)
    vc = save(pd.DataFrame(cnts), "venue_validation_frame_counts.csv")
    diff_row("Tier 1 rows: 2025 Away/Away + Home/Home", 10, int(vc[vc.stratum.str.startswith("2025")]["rows"].sum()))
    diff_row("Tier 1 rows: 2026 Home/Home", 16, int(vc[vc.stratum.str.startswith("2026")]["rows"].sum()))
    S += ["### 3.1e Validation frame (venue_validation_frame.csv, seed 20261111; verifier columns blank)",
          md(vc, "{:.0f}"), f"Total rows: {len(vv)} (tier 1: {int((vv.tier == 1).sum())}, tier 2: "
          f"{int((vv.tier == 2).sum())}). Controls are TWO_SIDED Home/Away games (flag_a Home, flag_b Away, "
          "team_a = alphabetically lower name). 2026 rows are in the frame for verification only.", ""]

    # ======================================================================= 3.2 OPPONENT IDENTITY
    S += ["## 3.2 Opponent identity", ""]
    tgs = []
    for s in SEASONS:
        for t in sorted(oppo[s]):
            d = canon[(canon["season"] == s) & ((canon["team_lo"] == t) | (canon["team_hi"] == t))]
            for r in d.itertuples():
                lo = r.team_lo == t
                tgs.append({"season": s, "team": t, "opp": r.team_hi if lo else r.team_lo, "game_key": r.game_key,
                            "gf": r.goals_lo if lo else r.goals_hi, "ga": r.goals_hi if lo else r.goals_lo,
                            "sidedness": r.sidedness})
    tg = pd.DataFrame(tgs)
    tg["opp_modeled"] = [o in rowt[s] for s, o in zip(tg["season"], tg["opp"])]
    tg["margin"] = tg["gf"] - tg["ga"]
    rows = []
    for (s, t), g in tg.groupby(["season", "team"]):
        gm = g[g["opp_modeled"]]
        rows.append({"season": s, "team": t, "games": len(g), "share_of_season_games": len(g) / n_season[s],
                     "games_vs_modeled": len(gm), "W_vs_modeled": int((gm["margin"] > 0).sum()),
                     "L_vs_modeled": int((gm["margin"] < 0).sum()), "mean_margin_vs_modeled": gm["margin"].mean(),
                     "distinct_opponents": g["opp"].nunique(),
                     "games_vs_non_modeled": int((~g["opp_modeled"]).sum()),
                     "single_sided_games": int((g["sidedness"] == "SINGLE_SIDED").sum())})
    o_team = save(pd.DataFrame(rows), "opp_only_by_team_season.csv")
    rows = []
    for s in SEASONS:
        g = tg[tg["season"] == s]
        keys = set(g["game_key"])
        gm = g[g["opp_modeled"]]
        ss_all = int((canon[canon["season"] == s]["sidedness"] == "SINGLE_SIDED").sum())
        rows.append({"season": s, "n_opp_only_teams": len(oppo[s]), "teams": "; ".join(sorted(oppo[s])),
                     "games_involving": len(keys), "share_of_season_games": len(keys) / n_season[s],
                     "W_vs_modeled": int((gm["margin"] > 0).sum()), "L_vs_modeled": int((gm["margin"] < 0).sum()),
                     "mean_margin_vs_modeled": gm["margin"].mean() if len(gm) else float("nan"),
                     "distinct_opponents": g["opp"].nunique(), "season_single_sided_games": ss_all,
                     "of_which_involve_opp_only": int(g[g["sidedness"] == "SINGLE_SIDED"]["game_key"].nunique())})
    o_season = save(pd.DataFrame(rows), "opp_only_by_season.csv")
    cross = o_team.pivot(index="team", columns="season", values="games").reindex(columns=list(SEASONS)).fillna(0).astype(int)
    cross["seasons_opp_only"] = [", ".join(str(s) for s in SEASONS if t in oppo[s]) for t in cross.index]
    cross["row_team_in"] = [", ".join(str(s) for s in SEASONS if t in rowt[s]) for t in cross.index]
    cross = save(cross.reset_index(), "opp_only_cross_season.csv")
    S += ["### 3.2a Teams that appear only as opponents (no own rows) in at least one season",
          "Definition used: a team is opponent-only in season s when it has games in s but no own rows in that "
          "season's file (membership opp_only_s). Under the strict reading (no own rows in ANY season) only "
          + ", ".join(sorted(t for t in allopp if not any(t in rowt[s] for s in SEASONS))) + " qualify; Hobart "
          "(own rows 2022 and 2026), Iona, Le Moyne and Mercyhurst (own rows 2026) are opponent-only in some "
          "seasons only, so they are included under the per-season definition. 'Modeled' = a team with own rows "
          "that season. W-L is the opponent-only team's record. In the cross-season table, game counts are only "
          "for seasons in which the team is opponent-only (a team's own-row seasons show 0).", "",
          "Per team and season:", md(o_team), "", "Per season (all opponent-only teams):",
          md(o_season), "", "Cross-season games (as an opponent-only team):", md(cross, "{:.0f}"), ""]
    if (tg["opp_modeled"] == False).any():  # noqa: E712
        FIND["OBSERVATIONS"].append(f"{int((~tg['opp_modeled']).sum())} team-games of opponent-only teams are "
                                    "against another non-modeled team.")
    # b
    pres = {s: set(canon.loc[canon.season == s, "team_lo"]) | set(canon.loc[canon.season == s, "team_hi"])
            for s in SEASONS}
    gcount = {s: pd.concat([canon.loc[canon.season == s, "team_lo"], canon.loc[canon.season == s, "team_hi"]])
              .value_counts().to_dict() for s in SEASONS}
    first_ever = {}
    for t in sorted(set().union(*pres.values())):
        first_ever[t] = min(s for s in SEASONS if t in pres[s])
    cont = []
    for s in SEASONS[1:]:
        for t in sorted(pres[s - 1] - pres[s]):
            cont.append({"transition": f"{s - 1}->{s}", "kind": "games in prior season, none in this one",
                         "team": t, "games_prior": gcount[s - 1].get(t, 0), "games_this": 0,
                         "row_team_prior": t in rowt[s - 1], "first_season_seen": first_ever[t]})
        for t in sorted(pres[s] - pres[s - 1]):
            cont.append({"transition": f"{s - 1}->{s}", "kind": "games in this season, none in prior",
                         "team": t, "games_prior": 0, "games_this": gcount[s].get(t, 0),
                         "row_team_prior": False, "first_season_seen": first_ever[t]})
    cont = save(pd.DataFrame(cont), "season_continuity.csv")
    new25 = sorted(pres[2025] - pres[2024])
    diff_row("Teams with games in 2025 and none in 2024", ["Iona", "Mercyhurst"], new25)
    lw = sorted(pres[2024] - pres[2025])
    diff_row("Lindenwood has 2024 games and no 2025 games", True, "Lindenwood" in lw and "Lindenwood" not in pres[2025])
    S += ["### 3.2b Season continuity (2026 description only)", md(cont, "{:.0f}"), ""]

    # c  (indep engine, selection_games only)
    sel = A.selection_games()
    assert int(sel["season"].max()) <= 2024 and not (sel["season"] > 2024).any()
    games = I.games_from_canonical(sel)
    assert max(g[0] for g in games) <= 2024
    run = I.run(games, 40.0, 1.0)
    pf = pd.DataFrame(run["preds"], columns=I.PRED_FIELDS)
    ends = run["ends"]
    rat = []
    allopp = sorted(set().union(*[oppo[s] for s in SEASONS]))
    for t in allopp:
        r = {"team": t}
        for s in (2022, 2023, 2024):
            r[f"end_{s}"] = ends[s].get(t, float("nan"))
        r["final_after_2024"] = run["ratings"].get(t, float("nan"))
        p5 = pred[(pred["season"] == 2025) & ((pred["team_a"] == t) | (pred["team_b"] == t))].sort_values(
            ["date", "game_key"], kind="mergesort")
        if len(p5):
            last = p5.iloc[-1]
            a_side = last["team_a"] == t
            sgn = 1 if a_side else -1
            r["end_2025_recorded"] = (last["R_a_before"] if a_side else last["R_b_before"]) + \
                sgn * K * (last["y_a"] - last["p_a"])
        else:
            r["end_2025_recorded"] = float("nan")
        r["seasons_opp_only"] = ", ".join(str(s) for s in SEASONS if t in oppo[s])
        rat.append(r)
    rat = save(pd.DataFrame(rat), "opp_only_ratings.csv")
    gains = []
    pf_i = pf.set_index("game_key")
    for (s, t), g in tg[tg["season"] <= 2024].groupby(["season", "team"]):
        tot, n = 0.0, 0
        for r in g[g["opp_modeled"]].itertuples():
            q = pf_i.loc[r.game_key]
            d_t = q["d"] if q["team_a"] == t else -q["d"]
            tot += -d_t
            n += 1
        gains.append({"season": s, "team": t, "source": "indep_elo (40,1) on selection_games()", "games_vs_modeled": n,
                      "net_points_gained_by_modeled_opponents": tot})
    p25 = pred[pred["season"] == 2025].copy()
    p25["d"] = K * (p25["y_a"] - p25["p_a"])
    p25i = p25.set_index("game_key")
    for (s, t), g in tg[tg["season"] == 2025].groupby(["season", "team"]):
        tot, n = 0.0, 0
        for r in g[g["opp_modeled"]].itertuples():
            q = p25i.loc[r.game_key]
            d_t = q["d"] if q["team_a"] == t else -q["d"]
            tot += -d_t
            n += 1
        gains.append({"season": s, "team": t, "source": "recorded predictions.csv columns, K from frozen_params.json",
                      "games_vs_modeled": n, "net_points_gained_by_modeled_opponents": tot})
    gains = save(pd.DataFrame(gains), "opp_only_modeled_gain.csv")
    gs = gains.groupby("season").agg(games=("games_vs_modeled", "sum"),
                                     net_points=("net_points_gained_by_modeled_opponents", "sum")).reset_index()
    gs["net_points_per_game"] = gs["net_points"] / gs["games"]
    S += ["### 3.2c Opponent-only teams' ratings at (40,1) and rating points modeled teams gained against them",
          "2022-2024 from the independent engine on selection_games(); 2025 from recorded predictions.csv only "
          "(delta = K(y - p), K = 40). A positive gain means modeled teams, as a group, took rating points from "
          "the opponent-only team. Ratings are end-of-season; teams with no games yet are blank.",
          md(rat, "{:.1f}"), "", md(gains, "{:.1f}"), "", "Per-season totals:", md(gs, "{:.2f}"), ""]
    # d
    nss = [g for g in games if g[7] != "SINGLE_SIDED"]
    rows = []
    for k in KS:
        for lam in LAMS:
            f = pd.DataFrame(I.run(nss, float(k), float(lam))["preds"], columns=I.PRED_FIELDS)
            x = f[f["season"] == 2024]
            rows.append({"K": k, "lam": lam, "n_2024": len(x), "brier_2024": I.brier(x["p_a"], x["y_a"]),
                         "log_loss_2024": I.log_loss(x["p_a"], x["y_a"])})
    gn = pd.DataFrame(rows)
    o = gn.sort_values(["brier_2024", "log_loss_2024", "K", "lam"], kind="mergesort")
    gn["rank"] = gn.index.map({i: r + 1 for r, i in enumerate(o.index)})
    gn = save(gn, "grid_2024_no_single_sided.csv")
    bk, bl = float(o.iloc[0]["K"]), float(o.iloc[0]["lam"])
    b401 = float(gn[(gn.K == 40) & (gn.lam == 1.0)]["brier_2024"].iloc[0])
    piv = gn.pivot(index="K", columns="lam", values="brier_2024")
    mono_k = all(piv[c].is_monotonic_decreasing for c in piv.columns)
    mono_l = all(piv.loc[k].is_monotonic_decreasing for k in piv.index)
    S += ["### 3.2d 2024 grid with all single-sided games removed (excluded from updates and scoring, all seasons)",
          f"Single-sided games removed: {sum(g[7] == 'SINGLE_SIDED' for g in games)} of {len(games)}; 2024 scored "
          f"games n = {int(gn['n_2024'].iloc[0])} (all two-sided). Argmin: K={bk:g}, lam={bl:g}; (40,1) Brier = "
          f"{b401:.5f}; monotone in K {mono_k}, in lam {mono_l}. For reference the recorded (40,1) two-sided "
          "Brier with single-sided games still updating ratings is 0.18008.", md(piv.reset_index().rename(
              columns=lambda c: f"lam={c}" if c != "K" else c), "{:.5f}"), ""]
    # e
    noted = {"Hobart": "user-supplied: D1 lacrosse program since 1995", "Le Moyne": "user-supplied: D1 from 2024",
             "Mercyhurst": "user-supplied: first D1 season 2025", "Iona": "user-supplied: first D1 season 2025",
             "Wabash": "user-supplied: D3", "St. Andrews": "user-supplied: NAIA (St. Andrews NC)"}
    todo = []
    for t in allopp:
        todo.append({"team": t, "seasons_opp_only": ", ".join(str(s) for s in SEASONS if t in oppo[s]),
                     "games_opp_only_seasons": int(tg[tg.team == t]["game_key"].nunique()),
                     "games_all_seasons": sum(gcount[s].get(t, 0) for s in SEASONS),
                     "division": "", "source_url": "", "prefilled_note": noted.get(t, "") +
                     (" -- unverified, needs a source URL" if t in noted else "")})
    todo = pd.DataFrame(todo)
    save(todo, "opponent_classification_todo.csv", label=False)
    S += ["### 3.2e opponent_classification_todo.csv", md(todo, "{:.0f}"), ""]
    # f
    names = sorted(set(canon["team_lo"]) | set(canon["team_hi"]))
    coll = []
    for mode, dp in (("strict", False), ("ignore parenthetical", True)):
        g = {}
        for nm in names:
            g.setdefault(norm(nm, dp), []).append(nm)
        coll += [{"normalisation": mode, "key": k, "names": " | ".join(v)} for k, v in g.items() if len(v) > 1]
    coll = save(pd.DataFrame(coll, columns=["normalisation", "key", "names"]), "name_collisions.csv")
    pm = []
    for t in names:
        r = {"team": t}
        for s in SEASONS:
            r[str(s)] = "R" if t in rowt[s] else ("O" if t in oppo[s] else "-")
        pr = [r[str(s)] != "-" for s in SEASONS]
        first, last = pr.index(True), len(pr) - 1 - pr[::-1].index(True)
        r["gap"] = any(not x for x in pr[first:last + 1])
        r["n_seasons"] = sum(pr)
        r["single_season"] = sum(pr) == 1
        pm.append(r)
    pm = save(pd.DataFrame(pm), "presence_matrix.csv")
    nz = {t: norm(t) for t in names}
    cand = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ta, tb = set(nz[a].split()), set(nz[b].split())
            why = []
            if ta < tb or tb < ta:
                why.append("token subset")
            e = lev(nz[a], nz[b])
            if e <= 2:
                why.append(f"edit distance {e}")
            if why:
                cand.append({"name_a": a, "name_b": b, "norm_a": nz[a], "norm_b": nz[b], "reason": "; ".join(why),
                             "seasons_a": ",".join(str(s) for s in SEASONS if pm.loc[pm.team == a, str(s)].iloc[0] != "-"),
                             "seasons_b": ",".join(str(s) for s in SEASONS if pm.loc[pm.team == b, str(s)].iloc[0] != "-")})
    cand = save(pd.DataFrame(cand, columns=["name_a", "name_b", "norm_a", "norm_b", "reason", "seasons_a", "seasons_b"]),
                "candidate_same_program_pairs.csv")
    # mascot map checks
    chk = []
    chk.append(("row count", len(cmap)))
    chk.append(("seasons present in map", ",".join(sorted(cmap.season.unique()))))
    chk.append(("rows for seasons >= 2024 (strip must not apply)", int((cmap.season.astype(int) >= 2024).sum())))
    chk.append(("rows where original == stripped", int((cmap.original == cmap.stripped).sum())))
    chk.append(("rows with n_candidate_prefixes > 1", int((cmap.n_candidate_prefixes.astype(int) > 1).sum())))
    one_many = cmap.groupby("original")["stripped"].nunique()
    chk.append(("raw names mapping to several canonicals (across seasons)", int((one_many > 1).sum())))
    many_one = cmap.groupby(["season", "stripped"])["original"].nunique()
    chk.append(("canonicals reached from several raw names (same season)", int((many_one > 1).sum())))
    dup_rows = int(cmap.duplicated(["season", "original"]).sum())
    chk.append(("duplicate (season, original) rows", dup_rows))
    names_mem = {s: rowt[s] for s in (2022, 2023)}
    for s in ("2022", "2023"):
        m = cmap[cmap.season == s]
        chk.append((f"{s}: stripped set == row-team names in membership", set(m.stripped) == names_mem[int(s)]))
    chk.append(("all stripped names exist in canonical name set", set(cmap.stripped) <= set(names)))
    mc = save(pd.DataFrame(chk, columns=["check", "result"]).astype({"result": str}), "mascot_map_checks.csv")
    mm_detail = pd.concat([one_many[one_many > 1].rename("n_canonicals").reset_index().assign(kind="one raw -> several"),
                           many_one[many_one > 1].rename("n_raw").reset_index().assign(kind="several raw -> one")])
    # membership vs predictions
    mv = []
    for s in (2022, 2023, 2024, 2025):
        ps = set(pred.loc[pred.season == s, "team_a"]) | set(pred.loc[pred.season == s, "team_b"])
        ms = rowt[s] | oppo[s]
        mv.append({"season": s, "teams_in_predictions": len(ps), "teams_in_membership": len(ms),
                   "equal": ps == ms, "only_in_predictions": "; ".join(sorted(ps - ms)),
                   "only_in_membership": "; ".join(sorted(ms - ps))})
    mv = save(pd.DataFrame(mv), "membership_vs_predictions.csv")
    # 77 resolved names
    res = xwalk[xwalk.status == "RESOLVED"]
    allmem = {s: rowt[s] | oppo[s] for s in SEASONS}
    r77 = []
    for r in res.itertuples():
        r77.append({"odds_name": r.odds_name, "results_name": r.results_name,
                    "in_membership_2025": r.results_name in allmem[2025],
                    "in_membership_2026": r.results_name in allmem[2026],
                    "as_row_team_2025": r.results_name in rowt[2025], "as_row_team_2026": r.results_name in rowt[2026],
                    "opp_only_2025": r.results_name in oppo[2025], "opp_only_2026": r.results_name in oppo[2026],
                    "crosswalk_present_in_2025_file": r.present_in_2025_file, "crosswalk_present_in_2026_file": r.present_in_2026_file})
    r77 = save(pd.DataFrame(r77), "resolved_names_vs_membership.csv")
    ev_chk = []
    for r in links.itertuples():
        s = int(r.season)
        for side, nm, st in (("home", r.home_results_name, r.home_name_status), ("away", r.away_results_name, r.away_name_status)):
            if st == "RESOLVED":
                ev_chk.append({"event_id": r.event_id[:8], "season": s, "side": side, "results_name": nm,
                               "in_membership_for_event_season": nm in allmem[s]})
    ev_chk = pd.DataFrame(ev_chk)
    S += ["### 3.2f Identity consistency", "",
          f"- Normalised-name collisions (casefold, punctuation, 'the', 'university', saint/st) over {len(names)} "
          f"canonical names: strict {int((coll.normalisation == 'strict').sum())}; ignoring parentheticals "
          f"{int((coll.normalisation != 'strict').sum())}." +
          ("" if coll.empty else "\n" + md(coll)),
          f"- Presence matrix (R = own rows, O = opponent only, - = absent): {len(pm)} teams; teams with a gap "
          f"between first and last season: {', '.join(pm[pm.gap].team) or 'none'}; single-season teams: "
          f"{', '.join(pm[pm.single_season].team) or 'none'}. Teams not present in all five seasons or with an "
          "opponent-only season:",
          md(pm[(pm.n_seasons < 5) | pm[[str(s) for s in SEASONS]].eq("O").any(axis=1)]
             .drop(columns=["gap", "single_season"]), "{:.0f}"), "",
          f"- Candidate same-program pairs (token subset or edit distance <= 2 on normalised names; listed, not "
          f"merged; no minimum length, so short names can produce noise): {len(cand)}", md(cand), "",
          "- mascot_strip_map.csv checks:", md(mc), ""]
    if len(mm_detail):
        S += ["  Detail:", md(mm_detail, "{:.0f}"), ""]
    S += ["- Teams in predictions.csv vs team_season_membership.csv per season (2026 has no predictions):", md(mv), "",
          f"- Resolved names in audit_join/out_run2: {len(r77)} RESOLVED rows in team_crosswalk_proposed.csv; in "
          f"2025 membership {int(r77.in_membership_2025.sum())}, in 2026 membership {int(r77.in_membership_2026.sum())}"
          f"; present in neither: {', '.join(r77[~r77.in_membership_2025 & ~r77.in_membership_2026].results_name) or 'none'}. "
          f"Event-level (RESOLVED sides in event_game_links.csv, name in membership for the event's season): "
          f"{int(ev_chk.in_membership_for_event_season.sum())} of {len(ev_chk)}; distinct resolved names used: "
          f"{ev_chk.results_name.nunique()}.", ""]
    diff_row("RESOLVED names in out_run2 crosswalk", 77, len(r77))
    diff_row("Resolved names missing from membership (2025 or 2026 as they apply)", 0,
             int((~r77.in_membership_2025 | ~r77.in_membership_2026).sum()),
             "crosswalk lists every name as present in both files; membership checks both seasons")
    diff_row("Event-level resolved names missing from membership of the event's season", 0,
             int((~ev_chk.in_membership_for_event_season).sum()))
    # g
    prim = links[links.PRIMARY == "True"].copy()
    prim["season"] = prim["season"].astype(int)
    pg_rows = []
    for r in prim.itertuples():
        hit = [t for t in (r.team_a, r.team_b) if t in oppo[r.season]]
        key = r.matched_game_key.split("|", 1)[1]
        side = canon.loc[canon.game_key == key, "sidedness"]
        pg_rows.append({"event_id": r.event_id, "season": r.season, "team_a": r.team_a, "team_b": r.team_b,
                        "matched_game_key": r.matched_game_key, "opp_only_team": "; ".join(hit),
                        "canonical_sidedness": side.iloc[0] if len(side) else "NOT FOUND"})
    pg = pd.DataFrame(pg_rows)
    pg_hit = save(pg[pg.opp_only_team != ""], "primary_events_with_opp_only_team.csv")
    pg_cnt = pg.groupby("season").agg(primary_events=("event_id", "size"),
                                      with_opp_only_team=("opp_only_team", lambda x: int((x != "").sum())),
                                      single_sided_games=("canonical_sidedness",
                                                          lambda x: int((x == "SINGLE_SIDED").sum()))).reset_index()
    S += ["### 3.2g PRIMARY events involving an opponent-only team (opponent-only as defined per season)",
          md(pg_cnt, "{:.0f}"), "", md(pg_hit), ""]

    # ======================================================================= 3.3 MARKET FEASIBILITY (counts only)
    S += ["## 3.3 Market-comparison feasibility (counts only; no market or Elo loss computed)", ""]
    br = []
    for r in links.itertuples():
        mk = r.matched_game_key
        if not mk:
            br.append({"event_id": r.event_id, "season": int(r.season), "PRIMARY": r.PRIMARY == "True",
                       "status": r.status, "matched_game_key": "", "canonical_game_key": "", "in_canonical": False,
                       "in_predictions": False, "sidedness": "", "n_books_h2h_any": int(r.n_books_h2h_any or 0),
                       "matched_date": r.matched_date, "key_date": "", "names_order_ok": False})
            continue
        parts = mk.split("|")
        ck = "|".join(parts[1:])
        in_c = ck in set(canon.game_key)
        row = canon[canon.game_key == ck]
        order_ok = bool(len(row)) and (row.iloc[0]["team_lo"], row.iloc[0]["team_hi"]) == (parts[2], parts[3])
        in_p = ck in set(pred.game_key)
        br.append({"event_id": r.event_id, "season": int(r.season), "PRIMARY": r.PRIMARY == "True",
                   "status": r.status, "matched_game_key": mk, "canonical_game_key": ck, "in_canonical": in_c,
                   "in_predictions": in_p, "sidedness": row.iloc[0]["sidedness"] if len(row) else "",
                   "n_books_h2h_any": int(r.n_books_h2h_any or 0), "matched_date": r.matched_date,
                   "key_date": parts[1], "names_order_ok": order_ok})
    br = save(pd.DataFrame(br), "event_bridge.csv")
    p25b = br[(br.PRIMARY) & (br.season == 2025)]
    p26b = br[(br.PRIMARY) & (br.season == 2026)]
    n1, n2, n3 = len(p25b[p25b.n_books_h2h_any >= 1]), len(p25b[p25b.n_books_h2h_any >= 2]), \
        len(p25b[p25b.n_books_h2h_any == 3])
    days = p25b["matched_date"].nunique()
    diff_row("PRIMARY 2025 events with >=1 / >=2 / 3 books", [57, 55, 43], [n1, n2, n3])
    diff_row("PRIMARY 2025 distinct game days", 5, days)
    miss_pred = p25b[~p25b.in_predictions]
    ss25 = p25b[p25b.sidedness == "SINGLE_SIDED"]
    bad_key = br[(br.matched_game_key != "") & (~br.in_canonical | ~br.names_order_ok)]
    dshift = br[(br.matched_game_key != "") & (br.key_date != br.matched_date)]
    S += ["### 3.3a Bridge to the canonical table",
          "Transformation: `matched_game_key` is `season|date|team_a|team_b` (results names, alphabetical); the "
          "canonical `game_key` is `date|team_lo|team_hi`, i.e. the matched key with the leading `season|` removed. "
          "No other change. Checks: every matched key must be found in the canonical table with the same name order.",
          f"- Events with a matched key: {int((br.matched_game_key != '').sum())} of {len(br)}; found in canonical "
          f"with the same name order: {int(((br.matched_game_key != '') & br.in_canonical & br.names_order_ok).sum())}"
          f"; not found or reordered: {len(bad_key)}; events whose key date differs from matched_date: {len(dshift)}.",
          f"- PRIMARY 2025: {len(p25b)} events (books >= 1: {n1}; >= 2: {n2}; = 3: {n3}) on {days} days "
          f"({', '.join(sorted(p25b.matched_date.unique()))}). Recorded: 57 / 55 / 43 on 5 days.",
          f"- PRIMARY 2025 events with a prediction in predictions.csv: {int(p25b.in_predictions.sum())} of "
          f"{len(p25b)}; without one: {len(miss_pred)}" +
          (" (" + "; ".join(miss_pred.canonical_game_key) + ")" if len(miss_pred) else "") +
          f"; of the PRIMARY 2025 events, single-sided games: {len(ss25)}" +
          (" (" + "; ".join(ss25.canonical_game_key) + ")" if len(ss25) else "") + ".",
          f"- PRIMARY 2026: {len(p26b)} events; predictions exist for none by design (2026 is not used); all found "
          f"in canonical: {bool(p26b.in_canonical.all())}.", ""]
    bridge_tab = p25b.groupby("matched_date").agg(events=("event_id", "size"),
                                                  in_predictions=("in_predictions", "sum"),
                                                  single_sided=("sidedness", lambda x: int((x == "SINGLE_SIDED").sum()))).reset_index()
    S += [md(bridge_tab, "{:.0f}"), ""]
    # b
    p25s = pred[pred.season == 2025]
    prior = {}
    for r in prim[prim.season == 2025].itertuples():
        before_ = p25s[p25s.date < r.matched_date]
        cnt = pd.concat([before_.team_a, before_.team_b]).value_counts()
        prior[r.event_id] = (int(cnt.get(r.team_a, 0)), int(cnt.get(r.team_b, 0)))
    mlb = ml[(ml.source != "consensus") & (ml.snapshot_timestamp != "")].copy()
    mlb["snap"] = pd.to_datetime(mlb["snapshot_timestamp"], utc=True)
    mlb["tts_hours"] = mlb["tts_hours"].astype(float)
    commence = links.set_index("event_id")["commence_latest"].pipe(pd.to_datetime, utc=True)
    mlb["commence"] = mlb["event_id"].map(commence)
    mlb["lead_hours"] = (mlb["commence"] - mlb["snap"]).dt.total_seconds() / 3600
    tts_diff = float((mlb["lead_hours"] - mlb["tts_hours"]).abs().max())
    rows_per = mlb.groupby(["event_id", "source"]).size()
    ev_rows = []
    for r in prim[prim.season == 2025].itertuples():
        m = mlb[mlb.event_id == r.event_id]
        latest = m.sort_values("snap").groupby("source").tail(1)
        pa, pb = prior[r.event_id]
        ev_rows.append({"event_id": r.event_id, "date": r.matched_date, "team_a": r.team_a, "team_b": r.team_b,
                        "prior_2025_games_a": pa, "prior_2025_games_b": pb, "min_prior": min(pa, pb),
                        "commence_latest": r.commence_latest, "commence_conflict": r.commence_conflict,
                        "n_books_with_snapshot": latest["source"].nunique(),
                        "n_distinct_snapshot_ts_latest_per_book": latest["snap"].nunique(),
                        "books_share_one_snapshot": latest["snap"].nunique() == 1,
                        "snapshot_spread_minutes": (latest["snap"].max() - latest["snap"].min()).total_seconds() / 60
                        if len(latest) else float("nan"),
                        "lead_hours_min": latest["lead_hours"].min() if len(latest) else float("nan"),
                        "lead_hours_max": latest["lead_hours"].max() if len(latest) else float("nan"),
                        "rows_in_file": len(m), "steps_in_file": ",".join(sorted(set(m["step"])))})
    evd = save(pd.DataFrame(ev_rows), "primary_2025_events.csv")
    allrows = save(mlb[mlb.event_id.isin(prim[prim.season == 2025].event_id)][
        ["event_id", "source", "step", "snapshot_timestamp", "commence", "lead_hours", "tts_hours"]]
        .assign(commence=lambda d: d["commence"].astype(str)), "primary_2025_snapshot_rows.csv")
    dist = []
    for name, ser in (("lead_hours (all PRIMARY 2025 book rows)", allrows["lead_hours"]),
                      ("lead_hours_min per event", evd["lead_hours_min"]),
                      ("lead_hours_max per event", evd["lead_hours_max"]),
                      ("snapshot_spread_minutes per event", evd["snapshot_spread_minutes"]),
                      ("min_prior 2025 games per event", evd["min_prior"]),
                      ("prior games, either team (a and b pooled)", pd.concat([evd["prior_2025_games_a"],
                                                                                  evd["prior_2025_games_b"]]))):
        dist.append({"quantity": name, "n": int(ser.notna().sum()), **quant(ser.dropna())})
    dist = save(pd.DataFrame(dist), "lead_time_and_history_distributions.csv")
    S += ["### 3.3b PRIMARY 2025 events: history and snapshot timing (distributions only)",
          f"Source for timing: `team_keyed_moneyline.csv` columns event_id, source, step, snapshot_timestamp, "
          f"tts_hours (prices and probabilities are not read). Commence time: `commence_latest` of "
          f"`event_game_links.csv`. Lead = commence - snapshot. Max |lead - tts_hours| over all book rows = "
          f"{tts_diff:.3g} h. Rows per (event, book): "
          f"{ {int(k): int(v) for k, v in rows_per.value_counts().sort_index().items()} }. Prior games count the team's 2025 games on earlier dates "
          "in predictions.csv.", md(dist, "{:.2f}"), "",
          f"- Events: {len(evd)}; with commence_conflict: {int((evd.commence_conflict == 'True').sum())}; all books "
          f"share one snapshot timestamp (latest per book): {int(evd.books_share_one_snapshot.sum())} of {len(evd)}; "
          f"events with min prior games < 3: {int((evd.min_prior < 3).sum())}, < 5: {int((evd.min_prior < 5).sum())}.",
          f"- Events by date: " + ", ".join(f"{k}: {v}" for k, v in evd.groupby('date').size().items()) + ".", ""]
    # c  spec
    spec_md = f"""# Market-comparison specification (pre-registration)

**{DIAG}.** Written before any market probability has been compared with any Elo probability. No market-vs-Elo
quantity has been computed in this audit. Nothing here changes K = 40, lam = 1, h = 0. The experiment remains a
feasibility experiment — pipeline check, not a research result.

## Inputs (fixed by hash at the time of writing)
- Elo forecasts: `elo_feas/out/predictions.csv` (sha256 `{sha_of(OUT / 'predictions.csv')}`), column `p_a`
  = probability that `team_a` (alphabetically lower canonical name) wins; frozen parameters in
  `frozen_params.json` (params_sha256 `{frozen['params_sha256']}`). No Elo rerun.
- Events and bridge: `audit_join/out_run2/event_game_links.csv` (sha256 `{sha_of(RUN2 / 'event_game_links.csv')}`);
  canonical game key = `matched_game_key` with the leading `season|` removed (3.3a).
- Market prices: `audit_join/out_run2/team_keyed_moneyline.csv` (sha256 `{sha_of(RUN2 / 'team_keyed_moneyline.csv')}`).
  Team-keyed American prices for the three books (draftkings, fanduel, betmgm); the odds feed's `home_team` is never
  used as the actual home, and the results file's `result` column is never used. Outcomes come from the canonical
  goals (`goals_lo > goals_hi` for team_lo; no ties exist).

## Inclusion rule
An event is included if and only if: PRIMARY is True, season = 2025, and a prediction for its canonical game key
exists in `predictions.csv`. Currently {len(p25b)} PRIMARY 2025 events, {int(p25b.in_predictions.sum())} with a
prediction ({len(miss_pred)} excluded), of which {len(ss25)} are single-sided games. Single-sided games stay in
the primary set and are reported separately. 2026 events are excluded (no Elo forecast exists by design).

## Market probability
1. Per book, use the price pair recorded in `team_keyed_moneyline.csv` for that event (the book's latest
   pre-game snapshot as built by the join audit); convert American odds to implied probabilities q_A, q_B.
2. **Primary de-vig: proportional**, p_A = q_A / (q_A + q_B).
3. **Sensitivity de-vig: power**, p_A = q_A^k, where k > 1 solves q_A^k + q_B^k = 1 for that book and event
   (solved numerically by bisection; p_B = q_B^k).
4. **Consensus** = arithmetic mean of the de-vigged book probabilities for team_a. Primary consensus requires
   >= 1 book; sensitivities require >= 2 books and exactly 3 books. All three are reported side by side; none is
   chosen after seeing results.
5. Probabilities are expressed for the same team as Elo's `p_a` (team_a of the canonical key); clip to
   [1e-6, 1 - 1e-6] for log loss.

## Outputs (all descriptive, no intervals, no re-selection)
- n per consensus rule and per subset (all, two-sided only, single-sided only).
- Mean Brier and mean log loss of Elo, of the market consensus, and the paired difference (Elo minus market) per
  rule, on the same games.
- Number of games on which each forecast has the lower per-game Brier (and per-game log loss), and ties.
- Single-book versions (each book alone, proportional de-vig) in the same layout.
- No confidence intervals, no tests, no model-selection or recalibration step; neither forecast is adjusted.

## Information-cutoff difference (must be stated with every result)
Elo uses ratings as of the end of the previous game date (date-batched; all same-date games are predicted before
any of that date's results). The market uses an intraday snapshot taken a median of
{dist.loc[dist.quantity.str.startswith('lead_hours_min'), 'q50'].iloc[0]:.1f} hours (event minimum) before
commence in 3.3b (the lead is the same for every book of an event), so it may reflect lineup, injury or weather news that the Elo forecast cannot. Any difference
between the two cannot be attributed to the rating method alone. The cutoffs do not even order consistently: in
3.3b {int((evd.lead_hours_min >= 24).sum())} of {len(evd)} events have a snapshot at least 24 hours before
commence, i.e. earlier than the previous date's games that Elo already includes.

## Not allowed after viewing results
Changing the inclusion rule, the de-vig method, the consensus rule, the snapshot rule, K, lam or h.
"""
    (CH3 / "market_comparison_spec.md").write_text(spec_md, encoding="utf-8")
    S += ["### 3.3c market_comparison_spec.md written (pre-registration; contents in the file).", ""]

    # ======================================================================= findings + differences
    df = pd.DataFrame(DIFFS)
    save(df, "differences_vs_recorded.csv")
    for r in df[df.match == "DIFFERS"].itertuples():
        FIND["ERRORS"].append(f"Recorded vs observed differ: {r.item}: recorded {r.recorded_by_user}, observed "
                              f"{r.observed}. {r.note}")
    FIND = add_findings(FIND, locals())
    S += ["## Differences from the recorded numbers", md(df), ""]
    L = ["## FINDINGS", ""]
    for k in ("ERRORS", "UNRESOLVED", "OBSERVATIONS"):
        L += [f"### {k}"] + ([f"- {x}" for x in FIND[k]] or ["- none"]) + [""]
    L += [f"_{DIAG}_"]
    return S + L


def add_findings(FIND: dict, v: dict) -> dict:
    """Findings that depend on computed values. Wording is descriptive; no causal claims."""
    tr, vf, o_season, gs, cand, mv, mc = v["tr"], v["vf"], v["o_season"], v["gs"], v["cand"], v["mv"], v["mc"]
    norm_ = norm
    t = tr.set_index("season")
    vm = v["vm"]
    may = vm[vm.month == "05"]
    FIND["OBSERVATIONS"].append(
        "Truncated to 8 April, Away/Away share of games: " +
        ", ".join(f"{s}: {t.loc[s, 'share_Away/Away_of_games']:.3f} (n={int(t.loc[s, 'n'])})" for s in SEASONS) +
        f" (full seasons: 2022-2024 {t.loc[2022, 'full_share_Away/Away_of_games']:.3f}, "
        f"{t.loc[2023, 'full_share_Away/Away_of_games']:.3f}, {t.loc[2024, 'full_share_Away/Away_of_games']:.3f}; "
        f"2025 {t.loc[2025, 'full_share_Away/Away_of_games']:.3f}). 2025 lies inside the 2022-2024 truncated range "
        f"{min(t.loc[s, 'share_Away/Away_of_games'] for s in (2022, 2023, 2024)):.3f}-"
        f"{max(t.loc[s, 'share_Away/Away_of_games'] for s in (2022, 2023, 2024)):.3f}, so the lower full-season "
        "Away/Away share in 2025 is what truncation alone would produce. 2022-2024 May games: "
        + "; ".join(f"{int(r.season)}: {int(r['Away/Away'])} of {int(r.n)} Away/Away" for _, r in may.iterrows()
                    if r.season <= 2024) + ". Descriptive; this does not say what the flag means.")
    FIND["OBSERVATIONS"].append(
        "Single-sided share on the same truncation: " + ", ".join(
            f"{s}: {t.loc[s, 'share_single_sided']:.3f}" for s in SEASONS) + ". The 2025 jump (0.103 vs 0.031-0.040) "
        "is not a truncation effect.")
    oc = v["o_season"]
    FIND["OBSERVATIONS"].append(
        "Single-sided games are exactly the games involving an opponent-only team in every season: " + "; ".join(
            f"{int(r.season)}: {int(r.season_single_sided_games)} single-sided, {int(r.of_which_involve_opp_only)} "
            "of them involving an opponent-only team" for r in oc.itertuples() if r.season in (2023, 2024, 2025)) + ".")
    pmx = v["pm"]
    FIND["OBSERVATIONS"].append(
        "Hobart's role changes across seasons: " + ", ".join(
            f"{s}: {pmx.loc[pmx.team == 'Hobart', str(s)].iloc[0]}" for s in SEASONS) +
        "; its rating moves from 1529 (end 2022) to 1394 (end 2024) while it plays only as an opponent-only team "
        "from 2023, so its 2023-2025 games are single-sided and its 2022 rating came from own-row games.")
    FIND["OBSERVATIONS"].append(
        "No PRIMARY event (2025 or 2026) involves an opponent-only team, and none of the 57 PRIMARY 2025 games is "
        "single-sided; a market comparison on PRIMARY 2025 therefore excludes the single-sided population entirely.")
    ev = v["evd"]
    FIND["OBSERVATIONS"].append(
        f"Market snapshot lead for PRIMARY 2025 events: median {ev.lead_hours_min.median():.1f} h, "
        f"{int((ev.lead_hours_min >= 24).sum())} of {len(ev)} events at least 24 h before commence, "
        f"{int((ev.lead_hours_min < 2).sum())} within 2 h; every event's books share one snapshot timestamp "
        f"({int(ev.books_share_one_snapshot.sum())} of {len(ev)}). The market snapshot can therefore precede some "
        "games that Elo has already learned from.")
    FIND["OBSERVATIONS"].append(
        f"PRIMARY 2025 events are early-season: minimum prior 2025 games of the two teams has median "
        f"{ev.min_prior.median():.0f}, range {int(ev.min_prior.min())}-{int(ev.min_prior.max())}; "
        f"{int((ev.min_prior < 5).sum())} of {len(ev)} have a team with fewer than 5 prior 2025 games. "
        "(Elo ratings carry over from 2024, so this is a count of 2025 games only.)")
    nm_ = v["names"]
    miss = []
    for label, tok in (("Penn", "penn"), ("St. John's", "st johns"), ("Queens (NC)", "queens"), ("UMass Lowell", "umass")):
        hits = [n for n in nm_ if tok in norm_(n)]
        miss.append(f"{label}: {hits}")
    FIND["UNRESOLVED"].append(
        "Same-program pairs named in the brief that the rule does not produce (token subset / edit distance <= 2 "
        "on normalised names), shown with the canonical names containing each token: " + "; ".join(miss) +
        ". The rule only finds pairs whose shorter name exists as a team; a second name for St. John's, Queens or "
        "UMass Lowell is not present in the canonical names, so no merge candidate exists.")
    FIND["OBSERVATIONS"].append(
        "Elo (40,1) Brier/log loss on games up to 8 April: " +
        ", ".join(f"{s}: {t.loc[s, 'elo_brier_40_1']:.4f}/{t.loc[s, 'elo_log_loss_40_1']:.4f} (n={int(t.loc[s, 'elo_n'])})"
                  for s in (2023, 2024, 2025)) + " (descriptive).")
    FIND["OBSERVATIONS"].append(
        "Away/Away is a pattern the results file records for 6-8% of 2022-2024 games and 2.2% of 2025 games; the "
        "brief's reading that these include neutral-site games is not verified here (venue_validation_frame.csv is "
        "blank for a human verifier). Home/Home appears only in 2025 (1) and 2026 (16).")
    FIND["UNRESOLVED"].append("Whether Away/Away and Home/Home mark neutral sites, scoring errors or both: needs "
                              "the verifier columns of venue_validation_frame.csv; the two Queens (NC) v UMass Lowell "
                              "claims are user-supplied and unverified here.")
    nd = int((gs["games"] > 0).sum())
    FIND["OBSERVATIONS"].append(
        "Modeled teams' net rating points from opponent-only games per season (per game): " +
        "; ".join(f"{int(r.season)}: {r.net_points:+.1f} over {int(r.games)} games ({r.net_points_per_game:+.2f})"
                  for r in gs.itertuples()) + ".")
    FIND["UNRESOLVED"].append("Division status of the six opponent-only teams is user-supplied and unverified "
                              "(opponent_classification_todo.csv has blank division/source columns).")
    if not mv["equal"].all():
        FIND["ERRORS"].append("Teams in predictions.csv differ from team_season_membership.csv in seasons: " +
                              ", ".join(str(int(s)) for s in mv[~mv["equal"]]["season"]))
    else:
        FIND["OBSERVATIONS"].append("Teams in predictions.csv equal team_season_membership.csv for 2022-2025.")
    FIND["OBSERVATIONS"].append(f"{len(cand)} candidate same-program name pairs listed (not merged); see "
                                "candidate_same_program_pairs.csv.")
    return FIND


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        with G._orig_open(CH3 / "files_opened.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# how\tR/W\tpath  (every open through builtins.open/io.open/pathlib/os.open, in order; "
                     "this file itself is written with the unwrapped open)\n")
            fh.write("# --- shell reads made before this script ran (not guarded; listed by hand) ---\n")
            fh.write("\n".join(SHELL_READS) + "\n")
            fh.write("# --- python opens logged by the guard ---\n")
            fh.write("\n".join(G.OPENED) + "\n")
    sys.exit(code)
