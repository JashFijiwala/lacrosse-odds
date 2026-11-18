"""Chunk 4 (A: engine equivalence on 2022-2024; B: timing counts for the 57 PRIMARY 2025 events).

Diagnostic, no re-selection. Writes only to elo_feas/audit_out/chunk4/. Reuses the guards of ../methodology_audit.py.
Part A uses selection_games() (2022-2024) and the standalone engine equiv_check.py (no elo_feas import).
Part B computes counts only: no Brier, no log loss, no Elo-vs-market quantity; no p_a value is loaded; the odds
feed's home_team and the `result` column are never loaded (usecols).
Run:  $env:PYTHONDONTWRITEBYTECODE=1; .\\.venv\\Scripts\\python.exe -B elo_feas\\audit_out\\chunk4\\chunk4_run.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import pathlib  # noqa: E402

CH4 = pathlib.Path(__file__).resolve().parent
AUD = CH4.parent
CH2, CH3 = AUD / "chunk2", AUD / "chunk3"
sys.path.insert(0, str(AUD))
sys.path.insert(0, str(CH4))

import methodology_audit as G  # noqa: E402  (installs the guards on import)

G.AUDIT_OUT = CH4

import re  # noqa: E402
from datetime import date as Date, datetime, timedelta, timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pandas as pd  # noqa: E402

import equiv_check as Q  # noqa: E402

ROOT, OUT, DIAG = G.ROOT, G.OUT, G.DIAG
RUN2 = ROOT / "audit_join" / "out_run2"
TOL = 1e-9
ET = ZoneInfo("America/New_York")
UTC = timezone.utc

# Reads made by me through the Read/Grep tools (not Python) before/while writing these scripts; listed by hand.
SHELL_READS = [
    "tool Read: elo_feas/audit_out/chunk2/indep_elo.py (source, whole file)",
    "tool Read: elo_feas/audit_out/chunk3/chunk3_audit.py (source, lines 1-260 and 700-825)",
    "tool Read: elo_feas/out/grid_2024.csv (header + 2 rows)",
    "tool Read: elo_feas/out/predictions.csv (header + 1 row)",
    "tool Read: elo_feas/out/canonical_games_2022_2026.csv (header + 1 row)",
    "tool Grep: elo_feas/*.py and elo_feas/audit_out/*.py (source text only: selection_games, venue_sign, hash_targets)",
    "tool Read: elo_feas/audit_out/chunk2/hash_diff.txt (before the context summary)",
]


def md(df: pd.DataFrame, fmt: str = "{:.3g}") -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        cells = []
        for v in r:
            if isinstance(v, float):
                cells.append("" if v != v else fmt.format(v))
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def hash_targets() -> list[pathlib.Path]:
    files = G.hash_targets()
    files += sorted((p for p in AUD.iterdir() if p.is_file()), key=lambda p: p.as_posix())
    files += sorted((p for p in CH2.rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    files += sorted((p for p in CH3.rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    return files


def snapshot() -> dict:
    return {G._rel(p): (p.stat().st_size, G.sha256_file(p)) for p in hash_targets()}


def main() -> int:
    selftest = G.guard_selftest()
    for label, d in (("write to audit_out outside chunk4", AUD), ("write to chunk3 from chunk4", CH3)):
        try:
            open(d / "__no_such_dir__" / "x.txt", "w")
            selftest.append((label, "FAIL: no exception"))
        except PermissionError:
            selftest.append((label, "PASS"))
        except Exception as e:  # noqa: BLE001
            selftest.append((label, f"FAIL: {type(e).__name__}"))
    if any(r != "PASS" for _, r in selftest):
        raise RuntimeError(f"guard self-test failed: {selftest}")

    before = snapshot()
    G.write_snapshot(before, "hashes_before.csv")
    prev = {}
    for line in (CH3 / "hashes_after.csv").read_text(encoding="utf-8").splitlines()[1:]:
        p, _, h = line.split(",")
        prev[p] = h
    bad = G.compare_reference(before, prev) + G.compare_reference(before, G.read_reference(
        ROOT / "audit_join" / "protected_hashes.txt"))
    if bad:
        (CH4 / "hash_diff.txt").write_text("STOP: mismatch before Chunk 4\n" + "\n".join(bad) + "\n", encoding="utf-8")
        return 2

    equiv_md, equiv_verdict = run_a()
    timing_md = run_b()

    after = snapshot()
    G.write_snapshot(after, "hashes_after.csv")
    diff = [f"{'CHANGED' if k in before and k in after else ('ADDED' if k in after else 'REMOVED')} {k}"
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    L = [f"files hashed: {len(before)} before, {len(after)} after (previous audit set + audit_out top-level + "
         "chunk2/ + chunk3/ files)",
         f"before vs after: {'NO DIFFERENCES' if not diff else str(len(diff)) + ' DIFFERENCES - STOP'}", *diff,
         f"before vs Chunk 3 hashes_after.csv ({len(prev)} entries): ALL MATCH",
         "before vs audit_join/protected_hashes.txt (10 entries): ALL MATCH",
         "guard self-test: " + "; ".join(f"{n}={r}" for n, r in selftest)]
    (CH4 / "hash_diff.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    hl = f"Hash check over {len(before)} files: {'NO DIFFERENCES' if not diff else 'DIFFERENCES - STOP'} (hash_diff.txt)."
    (CH4 / "equiv_report.md").write_text(equiv_md.replace("@@HASH@@", hl), encoding="utf-8")
    (CH4 / "timing_counts.md").write_text(timing_md.replace("@@HASH@@", hl), encoding="utf-8")
    return 2 if diff else 0


# --------------------------------------------------------------------------- Part A
def run_a() -> tuple[str, str]:
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

    src = (CH4 / "equiv_check.py").read_text(encoding="utf-8")
    no_import = re.search(r"^\s*(from|import)\s+elo_feas", src, re.M) is None
    games = Q.check_games(A.selection_games())
    assert max(g[0] for g in games) <= 2024
    n_games = len(games)
    base = {lam: Q.run(games, 40, 400, lam) for lam in (1.0, 0.85)}

    S = ["# Chunk A: equivalence of (K, D) parametrisations of the frozen Elo engine", "",
         f"**{DIAG}.** Read-only; selection data only (selection_games(), seasons 2022-2024, {n_games} games). K = 40, "
         "lam = 1, h = 0 and the grid are unchanged; nothing is retuned; 2025 is not touched. Engine: "
         "`chunk4/equiv_check.py`, ratings in rating points with an explicit denominator D; it imports nothing from "
         f"`elo_feas` (source scan: {'no elo_feas import' if no_import else 'FOUND an elo_feas import'}); the games "
         "frame comes from selection_games() via the runner. Tolerance 1e-9.", "@@HASH@@", ""]
    ok_all = no_import

    # 1. gate
    grid = pd.read_csv(OUT / "grid_2024.csv")
    g = grid[(grid.K == 40) & (grid.lam == 1.0) & (grid.h == 0.0)]
    assert len(g) == 1
    g = g.iloc[0]
    r = base[1.0]["rows"]
    gate = []
    for s in (2023, 2024):
        n, b, ll = Q.brier_ll(r, s)
        gate += [(f"n_{s}", n, int(g[f"n_{s}"]), abs(n - int(g[f"n_{s}"]))),
                 (f"brier_{s}", b, g[f"brier_{s}"], abs(b - g[f"brier_{s}"])),
                 (f"log_loss_{s}", ll, g[f"log_loss_{s}"], abs(ll - g[f"log_loss_{s}"]))]
    gt = pd.DataFrame(gate, columns=["quantity", "this engine (40, 400, 1.0)", "grid_2024.csv", "abs diff"])
    pr = pd.read_csv(OUT / "predictions.csv", dtype={"game_key": str},
                     usecols=["season", "date", "game_key", "R_a_before", "R_b_before", "p_a", "y_a"])
    pr = pr[pr.season <= 2024].set_index("game_key")
    mine = pd.DataFrame(r, columns=["season", "date", "game_key", "Ra", "Rb", "p", "y"]).set_index("game_key")
    assert set(mine.index) == set(pr.index) and len(mine) == len(pr)
    mine = mine.loc[pr.index]
    pt = []
    for lab, m in (("2023 + 2024 rows", pr.season.isin([2023, 2024])), ("2022 rows (informational)", pr.season == 2022)):
        pt.append((lab, int(m.sum()), float((mine.Ra[m] - pr.R_a_before[m]).abs().max()),
                   float((mine.Rb[m] - pr.R_b_before[m]).abs().max()), float((mine.p[m] - pr.p_a[m]).abs().max()),
                   int((mine.y[m] != pr.y_a[m]).sum())))
    ptab = pd.DataFrame(pt, columns=["predictions.csv rows", "n", "max abs diff R_a_before", "max abs diff R_b_before",
                                     "max abs diff p_a", "y_a mismatches"])
    gate_ok = bool(gt["abs diff"].max() < TOL and ptab.iloc[0, 2:5].max() < TOL and ptab.iloc[0, 5] == 0)
    ok_all &= gate_ok
    S += ["## 1. Gate: (K=40, D=400, lam=1.0) against the recorded outputs", md(gt, "{:.12g}"), "", md(ptab, "{:.3g}"), "",
          f"Gate: **{'PASS' if gate_ok else 'FAIL'}** (all differences < {TOL:g}, y_a identical).", ""]

    # 2. equivalence
    eq = []
    pairs = [("(20, 200)", 20, 200, "equivalent"), ("(80, 800)", 80, 800, "equivalent"),
             ("(10, 100)", 10, 100, "equivalent"), ("(40, 200)", 40, 200, "CONTROL (not equivalent)")]
    for lam in (1.0, 0.85):
        for lab, K, Dn, kind in pairs:
            alt = Q.run(games, K, Dn, lam)
            dp, dz = Q.max_diff(base[lam], alt)
            eq.append((lam, lab, f"{K / Dn:.4g}", kind, dp, dz))
    et = pd.DataFrame(eq, columns=["lam", "(K, D)", "K/D", "role", "max abs diff p (all games)",
                                   "max abs diff season-end z=(R-1500)/D"])
    eq_ok = bool(et[et.role == "equivalent"][["max abs diff p (all games)",
                                              "max abs diff season-end z=(R-1500)/D"]].max().max() < TOL)
    ctl_fail = bool(et[et.role != "equivalent"]["max abs diff p (all games)"].min() > 1e-4)
    ok_all &= eq_ok and ctl_fail
    S += ["## 2. Equivalence against (K=40, D=400) over all 2022-2024 games",
          "z_end is compared for every (season, team) at the end of each season (before the next shrink). "
          "Reference run is (40, 400) at the same lam.", md(et, "{:.3g}"), "",
          f"Equivalent pairs: max over rows below {TOL:g}: **{'PASS' if eq_ok else 'FAIL'}**; control differs in p by "
          f"at least {et[et.role != 'equivalent']['max abs diff p (all games)'].min():.3g} (test can fail: "
          f"{'yes' if ctl_fail else 'NO'}).", ""]

    # 3. grid mapping
    gm = []
    for lam in (0.5, 0.7, 0.85, 1.0):
        for K in (10, 20, 30, 40):
            _, b1, _ = Q.brier_ll(Q.run(games, K, 400, lam)["rows"], 2024)
            _, b2, _ = Q.brier_ll(Q.run(games, K / 2, 200, lam)["rows"], 2024)
            gm.append((lam, K, b1, K / 2, b2, abs(b1 - b2)))
    gmt = pd.DataFrame(gm, columns=["lam", "K (D=400)", "brier_2024 (K, 400)", "K/2 (D=200)", "brier_2024 (K/2, 200)",
                                    "abs diff"])
    gm_ok = bool(gmt["abs diff"].max() < TOL)
    ok_all &= gm_ok
    S += ["## 3. Grid mapping: 2024 Brier of (K, D=400) vs (K/2, D=200), h = 0", md(gmt, "{:.12g}"), "",
          f"Max abs diff over the 16 cells: {gmt['abs diff'].max():.3g} -> **{'PASS' if gm_ok else 'FAIL'}**.", ""]

    # 4. home-advantage scaling
    hr = []
    for lam in (1.0, 0.85):
        ref = Q.run(games, 40, 400, lam, h=25)
        h0 = Q.run(games, 40, 400, lam, h=0)
        for lab, K, Dn, h, kind in (("(h=12.5, K=20, D=200)", 20, 200, 12.5, "equivalent"),
                                    ("(h=25, K=20, D=200)", 20, 200, 25, "CONTROL (not equivalent)"),
                                    ("(h=0, K=40, D=400)", 40, 400, 0, "CONTROL: h switched off")):
            alt = h0 if lab.startswith("(h=0") else Q.run(games, K, Dn, lam, h=h)
            dp, dz = Q.max_diff(ref, alt)
            hr.append((lam, lab, kind, dp, dz))
    ht = pd.DataFrame(hr, columns=["lam", "alternative vs (h=25, K=40, D=400)", "role", "max abs diff p",
                                   "max abs diff season-end z"])
    h_ok = bool(ht[ht.role == "equivalent"][["max abs diff p", "max abs diff season-end z"]].max().max() < TOL)
    h_ctl = bool(ht[ht.role != "equivalent"]["max abs diff p"].min() > 1e-4)
    ok_all &= h_ok and h_ctl
    S += ["## 4. Home advantage in rating units: h = 25 at D = 400 vs h = 12.5 at (K=20, D=200)", md(ht, "{:.3g}"), "",
          f"Equivalent row max below {TOL:g}: **{'PASS' if h_ok else 'FAIL'}**; controls differ (test can fail): "
          f"{'yes' if h_ctl else 'NO'}.", ""]

    verdict = ("**Verdict: " + ("PASS" if ok_all else "FAIL") +
               "** - the engine reproduces the recorded cell; pairs with the same K/D (and h/D) give the same "
               "probabilities and the same (R-1500)/D to within 1e-9 at lam = 1.0 and 0.85; the non-equivalent controls "
               "differ.") if ok_all else "**Verdict: FAIL** - see the tables above."
    S += [verdict, "", f"_{DIAG}_", ""]
    return "\n".join(S), verdict


# --------------------------------------------------------------------------- Part B
def et_utc(d: Date, hour: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=ET).astimezone(UTC)


def run_b() -> str:
    # DST self-test: 2025-03-09 02:00 ET is the change
    assert et_utc(Date(2025, 3, 9), 0) == datetime(2025, 3, 9, 5, tzinfo=UTC)
    assert et_utc(Date(2025, 3, 9), 6) == datetime(2025, 3, 9, 10, tzinfo=UTC)
    assert et_utc(Date(2025, 3, 10), 0) == datetime(2025, 3, 10, 4, tzinfo=UTC)

    canon = pd.read_csv(OUT / "canonical_games_2022_2026.csv", dtype=str, keep_default_na=False,
                        usecols=["season", "date", "game_key", "team_lo", "team_hi", "goals_lo", "goals_hi"])
    pred = pd.read_csv(OUT / "predictions.csv", dtype=str, keep_default_na=False,
                       usecols=["season", "date", "game_key", "team_a", "team_b", "y_a"])
    links = pd.read_csv(RUN2 / "event_game_links.csv", dtype=str, keep_default_na=False,
                        usecols=["event_id", "season", "PRIMARY", "matched_game_key", "matched_date", "commence_latest",
                                 "commence_conflict", "n_books_h2h_any"])
    ml = pd.read_csv(RUN2 / "team_keyed_moneyline.csv", dtype=str, keep_default_na=False,
                     usecols=["event_id", "source", "step", "snapshot_timestamp", "tts_hours"])
    dates_of: dict[str, set] = {}
    for r in canon.itertuples():
        for t in (r.team_lo, r.team_hi):
            dates_of.setdefault(t, set()).add(r.date)

    def last_before(t: str, d: str) -> str | None:
        c = [x for x in dates_of.get(t, ()) if x < d]
        return max(c) if c else None

    prim = links[(links.PRIMARY == "True") & (links.season == "2025")].copy()
    assert len(prim) == 57, len(prim)
    mlb = ml[(ml.source != "consensus") & (ml.snapshot_timestamp != "")].copy()
    mlb["snap"] = pd.to_datetime(mlb["snapshot_timestamp"], utc=True)
    books_all = sorted(mlb[mlb.event_id.isin(prim.event_id)].source.unique())
    rows = []
    cov = []                                                   # (event_id, book)
    for r in prim.sort_values(["matched_date", "event_id"]).itertuples():
        parts = r.matched_game_key.split("|")
        key, ta, tb, d = "|".join(parts[1:]), parts[2], parts[3], r.matched_date
        m = mlb[mlb.event_id == r.event_id]
        latest = m.sort_values("snap").groupby("source").tail(1)
        books = sorted(latest.source.unique())
        cov += [(r.event_id, b) for b in books]
        snap = latest.snap.min()
        la, lb = last_before(ta, d), last_before(tb, d)
        assert la or lb, f"no earlier game for either team in {key}"
        lmax = max(x for x in (la, lb) if x)
        t_e = et_utc(Date.fromisoformat(lmax) + timedelta(days=1))
        d0, d06 = et_utc(Date.fromisoformat(d), 0), et_utc(Date.fromisoformat(d), 6)
        dprev = (Date.fromisoformat(d) - timedelta(days=1)).isoformat()
        commence = pd.to_datetime(r.commence_latest, utc=True)
        rows.append({"event_id": r.event_id, "matched_date": d, "team_a": ta, "team_b": tb, "game_key": key,
                     "n_books_h2h_any": int(r.n_books_h2h_any), "n_books_in_file": len(books), "books": ";".join(books),
                     "snapshot_utc": snap.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "snapshots_equal_across_books": bool(latest.snap.nunique() == 1),
                     "commence_utc": commence.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "last_game_a": la or "", "last_game_b": lb or "",
                     "t_e_utc": t_e.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "gameday_start_utc": d0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "gameday06_utc": d06.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "T1": bool(snap >= t_e), "GAMEDAY": bool(snap >= d0), "GAMEDAY06": bool(snap >= d06),
                     "TEAM_PLAYED_DAY_BEFORE": bool(dprev in dates_of.get(ta, ()) or dprev in dates_of.get(tb, ()))})
    ev = pd.DataFrame(rows)
    ev.assign(label=DIAG).to_csv(CH4 / "timing_events.csv", index=False, lineterminator="\n")

    tiers = [(">= 1 book", ev.n_books_h2h_any >= 1), (">= 2 books", ev.n_books_h2h_any >= 2),
             ("3 books", ev.n_books_h2h_any == 3)]

    def counts(sub: pd.DataFrame) -> dict:
        return {"events": len(sub), "T1": int(sub.T1.sum()), "not-T1": int((~sub.T1).sum()),
                "GAMEDAY": int(sub.GAMEDAY.sum()), "GAMEDAY06": int(sub.GAMEDAY06.sum()),
                "TEAM_PLAYED_DAY_BEFORE": int(sub.TEAM_PLAYED_DAY_BEFORE.sum())}

    by_tier = pd.DataFrame([{"tier": n, **counts(ev[m])} for n, m in tiers])
    by_date = pd.DataFrame([{"tier": n, "matched_date": d, **counts(ev[m & (ev.matched_date == d)])}
                            for n, m in tiers for d in sorted(ev.matched_date.unique())])
    xt = []
    for n, m in tiers:
        s = ev[m]
        for t1 in (True, False):
            xt.append({"tier": n, "T1": t1, "GAMEDAY=True": int((s.T1.eq(t1) & s.GAMEDAY).sum()),
                       "GAMEDAY=False": int((s.T1.eq(t1) & ~s.GAMEDAY).sum())})
    xtab = pd.DataFrame(xt)
    nt = ev[~ev.T1][["matched_date", "team_a", "team_b", "snapshot_utc", "last_game_a", "last_game_b", "t_e_utc",
                     "n_books_h2h_any", "GAMEDAY", "GAMEDAY06"]]
    nt = nt.rename(columns={"matched_date": "date"})

    cv = pd.DataFrame(cov, columns=["event_id", "book"]).merge(ev[["event_id", "T1"]], on="event_id")
    cvt = cv.groupby("book").agg(events_quoted=("event_id", "nunique"), events_quoted_and_T1=("T1", "sum")).reset_index()
    cvt["events_quoted_and_T1"] = cvt["events_quoted_and_T1"].astype(int)
    tier_t1 = pd.DataFrame([{"tier": n, "events": int(m.sum()), "events_also_T1": int((m & ev.T1).sum())}
                            for n, m in tiers])

    # G1 / G2 (counts only)
    p25 = pred[pred.season == "2025"]
    cm = canon.set_index("game_key")
    g1 = g2 = g_order = 0
    for r in ev.itertuples():
        pm = p25[p25.game_key == r.game_key]
        if len(pm) == 1:
            g1 += 1
            c = cm.loc[r.game_key]
            y_goals = int(float(c.goals_lo) > float(c.goals_hi))
            if (pm.iloc[0].team_a, pm.iloc[0].team_b) == (c.team_lo, c.team_hi):
                g_order += 1
            if y_goals == int(pm.iloc[0].y_a):
                g2 += 1
    n_book_mismatch = int((ev.n_books_h2h_any != ev.n_books_in_file).sum())
    same_snap = int(ev.snapshots_equal_across_books.sum())
    daily = ev.groupby("matched_date").size().tolist()
    rec = [("PRIMARY 2025 events with >=1 / >=2 / 3 books", "[57, 55, 43]",
            str([int(m.sum()) for _, m in tiers])),
           ("distinct game days", "5", str(ev.matched_date.nunique())),
           ("events per day (2025-02-28, 03-01, 03-02, 03-08, 03-09)", "[9, 20, 2, 24, 2]", str(daily)),
           ("events with all books on one snapshot", "57", str(same_snap)),
           ("median snapshot lead (h), from this table", "4.24",
            f"{((pd.to_datetime(ev.commence_utc) - pd.to_datetime(ev.snapshot_utc)).dt.total_seconds() / 3600).median():.2f}"),
           ("events where file books == n_books_h2h_any", "57", str(57 - n_book_mismatch))]
    rect = pd.DataFrame(rec, columns=["item", "recorded", "observed"])
    rect["match"] = ["MATCH" if a == b else "DIFFERS" for a, b in zip(rect.recorded, rect.observed)]

    S = ["# Chunk B: timing counts for the 57 PRIMARY 2025 events", "",
         f"**{DIAG}.** Read-only counts. No Brier, no log loss and no Elo-versus-market quantity is computed; no `p_a` "
         "value is loaded (predictions.csv is read with usecols season, date, game_key, team_a, team_b, y_a); "
         "`home_team` and `result` are not loaded; no raw 2025/2026 results file is opened. K=40, lam=1, h=0 unchanged.",
         "@@HASH@@", "",
         "Definitions. d = matched_date (a results date). last(X) = the team's most recent canonical game with date < d, "
         "any season. t_e = 00:00 America/New_York on the day after max(last(A), last(B)), converted to UTC with zoneinfo "
         "(the 2025-03-09 change is handled by constructing local midnight: 2025-03-09 00:00 ET = 05:00Z, "
         "2025-03-10 00:00 ET = 04:00Z, 2025-03-09 06:00 ET = 10:00Z; asserted). Snapshot = the event's snapshot "
         f"(latest per book; the books share one timestamp in {same_snap} of 57 events, so min = max). T1 = snapshot >= t_e; "
         "GAMEDAY = snapshot >= 00:00 ET on d; GAMEDAY06 = snapshot >= 06:00 ET on d; TEAM_PLAYED_DAY_BEFORE = either "
         "team has a canonical game dated d-1. Book tiers are the recorded n_books_h2h_any (nested: >=1, >=2, =3); "
         f"books present in the file for these events: {', '.join(books_all)}.", "",
         "## 1-2. Counts by book tier", md(by_tier, "{:.0f}"), "",
         "### By game date and tier", md(by_date, "{:.0f}"), "",
         "### T1 x GAMEDAY cross-tab (per tier)", md(xtab, "{:.0f}"), "",
         f"### Every not-T1 event ({len(nt)})", md(nt) if len(nt) else "(none)", "",
         "## 3. Per-book coverage (PRIMARY 2025 events)", md(cvt, "{:.0f}"), "",
         "Events per book tier that are also T1:", md(tier_t1, "{:.0f}"), "",
         "## 4. G1 / G2 (counts only)",
         f"- G1: events mapping to exactly one predictions.csv row (season 2025, by game_key): **{g1} of {len(ev)}**; "
         f"of those, team_a/team_b order equals the canonical team_lo/team_hi: {g_order}.",
         f"- G2: y from canonical goals (goals_lo > goals_hi) equals predictions.csv `y_a`: **{g2} of {len(ev)}**. "
         "(The odds `result` column is not used.)", "",
         "## Differences from recorded numbers", md(rect), "",
         "Any row marked DIFFERS is a finding." if (rect.match == "DIFFERS").any() else "No differences from the "
         "recorded numbers.", "", f"_{DIAG}_", ""]
    return "\n".join(S)


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        with G._orig_open(CH4 / "files_opened.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# how\tR/W\tpath  (shell/tool reads first, listed by hand; then every open through "
                     "builtins.open/io.open/pathlib/os.open, in order)\n")
            fh.write("\n".join(SHELL_READS) + "\n# --- python opens logged by the guard ---\n")
            fh.write("\n".join(G.OPENED) + "\n")
    sys.exit(code)
