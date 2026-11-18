"""Chunk 2 read-only audit (diagnostic, no re-selection). Writes only to elo_feas/audit_out/chunk2/.

Reuses the guards and hashing of ../methodology_audit.py (importing it installs the open/write guards), then
narrows the write area to chunk2/. Recomputation uses selection_games() (2022-2024) and the independent engine
indep_elo.py; 2025 enters only through the recorded elo_feas/out/predictions.csv. K = 40, lam = 1, h = 0 stay
frozen; nothing here feeds back into selection.
Run:  $env:PYTHONDONTWRITEBYTECODE=1; .\\.venv\\Scripts\\python.exe -B elo_feas\\audit_out\\chunk2\\chunk2_audit.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import os  # noqa: E402
import pathlib  # noqa: E402

CH2 = pathlib.Path(__file__).resolve().parent
AUD = CH2.parent
sys.path.insert(0, str(AUD))
sys.path.insert(0, str(CH2))

import methodology_audit as G  # noqa: E402  (installs the guards on import)

G.AUDIT_OUT = CH2                                   # from here on, writes are allowed only under chunk2/
import indep_elo as I  # noqa: E402

INDEP_PULLED_ELO = "elo_feas.elo" in sys.modules    # must be False: indep_elo imports nothing from elo_feas

import json  # noqa: E402
import math  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT, OUT = G.ROOT, G.OUT
DIAG = G.DIAG
SEED = 20261111
KS, LAMS = (10, 15, 20, 30, 40), (0.5, 0.7, 0.85, 1.0)
CELLS = [(float(k), float(l)) for k in KS for l in LAMS]
BEST = (40.0, 1.0)
TOL = 1e-9


# --------------------------------------------------------------------------- small helpers
def md(df: pd.DataFrame, fmt: str = "{:.5f}") -> str:
    cols = [c for c in df.columns if c != "label"]
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in df[cols].itertuples(index=False):
        cells = []
        for v in r:
            if isinstance(v, (float, np.floating)):
                cells.append(fmt.format(v) if np.isfinite(v) else str(v))
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def save(df: pd.DataFrame, name: str) -> pd.DataFrame:
    df.assign(label=DIAG).to_csv(CH2 / name, index=False, lineterminator="\n")
    return df


def slope_no_intercept(p, y) -> float:
    """Same estimator as audit_report D4: logistic fit of y on b*logit(p), no intercept, Newton from b = 1."""
    x = np.log(p / (1 - p))
    b = 1.0
    for _ in range(50):
        w = 1 / (1 + np.exp(-b * x))
        g, h = float(np.sum(x * (y - w))), float(np.sum(x * x * w * (1 - w)))
        step = g / h
        b += step
        if abs(step) < 1e-12:
            break
    return b


def np_brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def np_ll(p, y) -> float:
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))


def best_cell(df: pd.DataFrame, b="brier", ll="log_loss") -> tuple:
    r = df.sort_values([b, ll, "K", "lam"], kind="mergesort").iloc[0]
    return float(r["K"]), float(r["lam"])


def monotone(piv: pd.DataFrame) -> tuple[bool, bool]:
    mk = all(piv[c].is_monotonic_decreasing and piv[c].is_unique for c in piv.columns)
    ml = all(piv.loc[k].is_monotonic_decreasing and piv.loc[k].is_unique for k in piv.index)
    return mk, ml


def prior_counts(df: pd.DataFrame, within_season: bool) -> pd.DataFrame:
    """Games each team played on EARLIER dates (same-date games do not count). df needs date, game_key, season,
    team_a, team_b. Returns game_key, prior_a, prior_b."""
    cnt, rows = {}, []
    for date, day in df.sort_values(["date", "game_key"], kind="mergesort").groupby("date", sort=True):
        season = int(day["season"].iloc[0])
        key = (lambda t: (season, t)) if within_season else (lambda t: t)
        for r in day.itertuples(index=False):
            rows.append((r.game_key, cnt.get(key(r.team_a), 0), cnt.get(key(r.team_b), 0)))
        for t in list(day["team_a"]) + list(day["team_b"]):
            cnt[key(t)] = cnt.get(key(t), 0) + 1
    return pd.DataFrame(rows, columns=["game_key", "prior_a", "prior_b"])


# --------------------------------------------------------------------------- hashing (previous set + audit_out)
def hash_targets() -> list[pathlib.Path]:
    files = G.hash_targets()
    files += sorted((p for p in AUD.iterdir() if p.is_file()), key=lambda p: p.as_posix())
    return files


def snapshot() -> dict:
    return {G._rel(p): (p.stat().st_size, G.sha256_file(p)) for p in hash_targets()}


def main() -> int:
    selftest = G.guard_selftest()
    for name, fn in (("write to audit_out outside chunk2", lambda: open(AUD / "__no_such_dir__" / "x.txt", "w")),):
        try:
            fn()
            selftest.append((name, "FAIL: no exception"))
        except PermissionError:
            selftest.append((name, "PASS"))
        except Exception as e:  # noqa: BLE001
            selftest.append((name, f"FAIL: {type(e).__name__}"))
    if any(r != "PASS" for _, r in selftest):
        raise RuntimeError(f"guard self-test failed: {selftest}")
    if INDEP_PULLED_ELO:
        raise RuntimeError("importing indep_elo loaded elo_feas.elo")
    src = (CH2 / "indep_elo.py").read_text(encoding="utf-8")
    if "elo_feas" in src or "import elo" in src or "from elo" in src:
        raise RuntimeError("indep_elo.py references the experiment package")

    before = snapshot()
    G.write_snapshot(before, "hashes_before.csv")
    prev_after = {}
    for line in (AUD / "hashes_after.csv").read_text(encoding="utf-8").splitlines()[1:]:
        p, _, h = line.split(",")
        prev_after[p] = h
    ref_prev = G.compare_reference(before, prev_after)
    ref_prot = G.compare_reference(before, G.read_reference(ROOT / "audit_join" / "protected_hashes.txt"))
    if ref_prev or ref_prot:
        (CH2 / "hash_diff.txt").write_text("STOP: mismatch before Chunk 2\n" + "\n".join(ref_prev + ref_prot) + "\n",
                                           encoding="utf-8")
        return 2

    sections = run_chunk2()

    after = snapshot()
    G.write_snapshot(after, "hashes_after.csv")
    diff = [f"{'CHANGED' if k in before and k in after else ('ADDED' if k in after else 'REMOVED')} {k}"
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    L = [f"files hashed: {len(before)} before, {len(after)} after (previous audit set + elo_feas/audit_out/ "
         f"top-level files)",
         f"before vs after: {'NO DIFFERENCES' if not diff else str(len(diff)) + ' DIFFERENCES - STOP'}", *diff,
         f"before vs previous audit hashes_after.csv ({len(prev_after)} entries): ALL MATCH",
         "before vs audit_join/protected_hashes.txt (10 entries): ALL MATCH",
         "guard self-test: " + "; ".join(f"{n}={r}" for n, r in selftest),
         f"indep_elo import loaded elo_feas.elo: {INDEP_PULLED_ELO}; indep_elo.py source references elo_feas: False"]
    (CH2 / "hash_diff.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    head = ["# Chunk 2 audit of the Elo feasibility experiment", "",
            f"**{DIAG}.** Read-only. K = 40, lam = 1, h = 0 unchanged; the grid is not widened; the 2025 test is "
            "not rerun. Recomputation: `selection_games()` (2022-2024) through the independent engine "
            "`chunk2/indep_elo.py`. 2025: recorded `elo_feas/out/predictions.csv` only. Not a model comparison "
            "and not a result; the experiment remains a feasibility experiment — pipeline check, not a research "
            "result.", "",
            f"Hash check over {len(before)} files: {'NO DIFFERENCES' if not diff else 'DIFFERENCES - STOP'} "
            "(chunk2/hash_diff.txt). Guard self-test: " + "; ".join(f"{n}: {r}" for n, r in selftest) + ".", ""]
    (CH2 / "chunk2_report.md").write_text("\n".join(head + sections) + "\n", encoding="utf-8")
    return 2 if diff else 0


# --------------------------------------------------------------------------- the audit
def run_chunk2() -> list[str]:
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

    S: list[str] = []                 # report sections
    FIND = {"ERRORS": [], "UNRESOLVED": [], "OBSERVATIONS": []}
    DIFFS: list[dict] = []

    pred = pd.read_csv(OUT / "predictions.csv", dtype={"date": str, "game_key": str, "team_a": str, "team_b": str},
                       keep_default_na=False)
    grid_rec = pd.read_csv(OUT / "grid_2024.csv")
    mbs = pd.read_csv(OUT / "metrics_by_season.csv")
    frozen = json.loads((OUT / "frozen_params.json").read_text(encoding="utf-8"))
    D_rec = {n: pd.read_csv(AUD / f, keep_default_na=False) for n, f in
             (("D1", "grid_profile_2024.csv"), ("D2", "selection_stability.csv"), ("D3", "bootstrap_brier_diffs.csv"),
              ("D4", "calibration.csv"), ("D5", "subset_metrics.csv"), ("D6", "experience_buckets.csv"))}
    assert (frozen["K"], frozen["lam"], frozen["h"]) == (40.0, 1.0, 0.0)
    sel = A.selection_games()
    assert int(sel["season"].max()) <= 2024
    games = I.games_from_canonical(sel)
    assert max(g[0] for g in games) <= 2024

    runs, frames = {}, {}

    def cell(k, lam, no_update=frozenset(), tag=""):
        key = (k, lam, tag)
        if key not in frames:
            r = I.run(games, k, lam, no_update)
            runs[key] = r
            frames[key] = pd.DataFrame(r["preds"], columns=I.PRED_FIELDS)
        return frames[key]

    def metrics(df, season, sub="ALL"):
        d = df[df["season"] == season]
        if sub == "TWO_SIDED":
            d = d[d["sidedness"] == "TWO_SIDED"]
        return len(d), I.brier(d["p_a"], d["y_a"]), I.log_loss(d["p_a"], d["y_a"])

    # ======================================================================= 2.1
    rows = []
    for k, lam in CELLS:
        f = cell(k, lam)
        n24, b24, l24 = metrics(f, 2024)
        n24t, b24t, l24t = metrics(f, 2024, "TWO_SIDED")
        n23, b23, l23 = metrics(f, 2023)
        rows.append({"K": int(k), "lam": lam, "n_2024": n24, "brier_2024": b24, "log_loss_2024": l24,
                     "n_2024_two_sided": n24t, "brier_2024_two_sided": b24t, "log_loss_2024_two_sided": l24t,
                     "n_2023": n23, "brier_2023": b23, "log_loss_2023": l23})
    grid = pd.DataFrame(rows)
    order = grid.sort_values(["brier_2024", "log_loss_2024", "K", "lam"], kind="mergesort").index
    grid.loc[order, "rank"] = range(1, 21)
    grid["rank"] = grid["rank"].astype(int)
    save(grid, "indep_grid.csv")
    gr = grid_rec.sort_values(["K", "lam"]).reset_index(drop=True)
    gi = grid.sort_values(["K", "lam"]).reset_index(drop=True)
    mcols = ["brier_2024", "log_loss_2024", "brier_2024_two_sided", "log_loss_2024_two_sided", "brier_2023",
             "log_loss_2023"]
    ncols = ["n_2024", "n_2024_two_sided", "n_2023", "rank"]
    gdiff = np.abs(gr[mcols].to_numpy(float) - gi[mcols].to_numpy(float))
    n_ok = (gr[ncols].to_numpy() == gi[ncols].to_numpy()).all()
    gate_grid = bool(gdiff.max() <= TOL and n_ok and (gr[["K", "lam"]].to_numpy() == gi[["K", "lam"]].to_numpy()).all())
    for i in range(20):
        for j, c in enumerate(mcols):
            if gdiff[i, j] > TOL:
                DIFFS.append({"source": "grid_2024.csv", "item": f"K={gr.K[i]} lam={gr.lam[i]} {c}",
                              "recorded": gr[c][i], "recomputed": gi[c][i], "abs_diff": gdiff[i, j]})
        for c in ncols:
            if gr[c][i] != gi[c][i]:
                DIFFS.append({"source": "grid_2024.csv", "item": f"K={gr.K[i]} lam={gr.lam[i]} {c}",
                              "recorded": gr[c][i], "recomputed": gi[c][i], "abs_diff": abs(gr[c][i] - gi[c][i])})
    fb = cell(*BEST)
    pr_rec = pred[pred["season"].isin([2023, 2024])].reset_index(drop=True)
    pr_ind = fb[fb["season"].isin([2023, 2024])].reset_index(drop=True)
    keys_ok = len(pr_rec) == len(pr_ind) and all(
        (pr_rec[c].astype(str).to_numpy() == pr_ind[c].astype(str).to_numpy()).all()
        for c in ("season", "date", "game_key", "team_a", "team_b", "y_a", "sidedness"))
    pdiff = float(np.max(np.abs(pr_rec[["R_a_before", "R_b_before", "p_a"]].to_numpy() -
                                pr_ind[["R_a_before", "R_b_before", "p_a"]].to_numpy()))) if keys_ok else float("nan")
    gate_pred = keys_ok and pdiff <= TOL
    p22 = pred[pred["season"] == 2022].reset_index(drop=True)
    f22 = fb[fb["season"] == 2022].reset_index(drop=True)
    d22 = float(np.max(np.abs(p22[["R_a_before", "R_b_before", "p_a"]].to_numpy() -
                              f22[["R_a_before", "R_b_before", "p_a"]].to_numpy())))
    S += ["## 2.1 Independent reimplementation", "",
          "`chunk2/indep_elo.py` imports nothing from `elo_feas` (checked: source scan, and `elo_feas.elo` was not "
          "in `sys.modules` after importing it). Same log-loss clipping (1e-6) as the recorded outputs.", "",
          f"- Gate A, grid: {len(mcols) * 20} metric values (six columns x 20 cells: 2024 Brier/log loss all and "
          f"two-sided, 2023 Brier/log loss) plus n and rank: max |diff| = {gdiff.max():.3g}; n and rank identical "
          f"= {bool(n_ok)} -> **{'PASS' if gate_grid else 'FAIL'}** (tolerance {TOL:g}).",
          f"- Gate B, predictions.csv 2023 + 2024 rows ({len(pr_rec)}) at K=40, lam=1: keys, teams, outcomes "
          f"identical = {keys_ok}; max |diff| in R_a_before, R_b_before, p_a = {pdiff:.3g} -> "
          f"**{'PASS' if gate_pred else 'FAIL'}**. (2022 rows, not gated: max |diff| {d22:.3g}.)", ""]
    if not (gate_grid and gate_pred):
        FIND["ERRORS"].append("Independent reimplementation did not reproduce the recorded grid/predictions; "
                              "audit stopped at 2.1 as instructed.")
        S += ["**STOP: gate failed; sections 2.2-2.7 not run.**", ""]
        return S + findings_md(FIND, DIFFS)

    # recorded-predictions consistency (all seasons incl. 2025; arithmetic on recorded values only)
    K, LAM = float(frozen["K"]), float(frozen["lam"])
    a = pred[["season", "date", "game_key", "team_a", "R_a_before", "p_a", "y_a"]].copy()
    a["d"] = K * (a["y_a"] - a["p_a"])
    a = a.rename(columns={"team_a": "team", "R_a_before": "R"})
    b = pred[["season", "date", "game_key", "team_b", "R_b_before", "p_a", "y_a"]].copy()
    b["d"] = -K * (b["y_a"] - b["p_a"])
    b = b.rename(columns={"team_b": "team", "R_b_before": "R"})
    ap = pd.concat([a, b]).sort_values(["team", "date"], kind="mergesort").reset_index(drop=True)
    ap["post"] = ap["R"] + ap["d"]
    nxt = ap.groupby("team").shift(-1)
    has = nxt["R"].notna()
    cross = has & (nxt["season"] != ap["season"])
    expect = np.where(cross, 1500.0 + LAM * (ap["post"] - 1500.0), ap["post"])
    cdiff = np.abs(nxt["R"].to_numpy(float) - expect)[has.to_numpy()]
    firsts = ap.groupby("team").head(1)
    new_ok = bool((firsts["R"] == 1500.0).all())
    by_season = []
    for s in sorted(ap["season"].unique()):
        m = (has & (nxt["season"] == s)).to_numpy()
        by_season.append(f"{s}: {int(m.sum())} checks, max |diff| "
                         f"{float(np.max(np.abs(nxt['R'].to_numpy(float) - expect)[m])):.3g}")
    cons_ok = float(cdiff.max()) <= TOL and new_ok
    S += [f"- Recorded `predictions.csv`, all {len(pred)} games 2022-2025 (no recomputation): for each team, "
          f"R_before at its next appearance == R_before + K(S - p) (team A) or - K(S - p) (team B), with "
          f"1500 + lam(R - 1500) applied when the next appearance is in a new season (K={K:g}, lam={LAM:g}). "
          f"{int(has.sum())} successive-appearance checks ({int(cross.sum())} across a season boundary): max |diff| "
          f"= {float(cdiff.max()):.3g} -> **{'PASS' if cons_ok else 'FAIL'}**. By season of the next appearance: "
          + "; ".join(by_season) + f". First appearance at 1500 for all {len(firsts)} teams: {new_ok}.", ""]
    if not cons_ok:
        FIND["ERRORS"].append("Recorded predictions.csv fails the successive-appearance update check.")

    # ======================================================================= 2.2
    piv_b23 = grid.pivot(index="K", columns="lam", values="brier_2023")
    sp = lambda x, y: float(pd.Series(x).rank().corr(pd.Series(y).rank()))  # noqa: E731
    rho_b = sp(grid["brier_2023"], grid["brier_2024"])
    rho_l = sp(grid["log_loss_2023"], grid["log_loss_2024"])
    slopes = []
    for k, lam in CELLS:
        f = cell(k, lam)
        for s in (2023, 2024):
            d = f[(f["season"] == s) & (f["p_a"] != 0.5)]
            slopes.append({"K": int(k), "lam": lam, "season": s, "n": len(d),
                           "slope": slope_no_intercept(d["p_a"].to_numpy(float), d["y_a"].to_numpy(float))})
    slopes = save(pd.DataFrame(slopes), "calibration_slope_by_cell.csv")
    sp_piv = {s: slopes[slopes["season"] == s].pivot(index="K", columns="lam", values="slope") for s in (2023, 2024)}
    dec_k = {s: all(sp_piv[s][c].is_monotonic_decreasing for c in sp_piv[s].columns) for s in (2023, 2024)}
    dec_l = {s: all(sp_piv[s].loc[k].is_monotonic_decreasing for k in sp_piv[s].index) for s in (2023, 2024)}
    min_sl = {s: float(sp_piv[s].to_numpy().min()) for s in (2023, 2024)}
    min_at = {s: sp_piv[s].stack().idxmin() for s in (2023, 2024)}
    all_gt1 = bool((slopes["slope"] > 1).all())

    def rel_table(p, y, season):
        p, y = np.asarray(p, float), np.asarray(y, int)
        keep = p != 0.5
        pf = np.where(p[keep] > 0.5, p[keep], 1 - p[keep])
        yf = np.where(p[keep] > 0.5, y[keep], 1 - y[keep])
        order = np.argsort(pf, kind="mergesort")
        out = []
        for i, idx in enumerate(np.array_split(order, 5)):
            mp, ob = float(pf[idx].mean()), float(yf[idx].mean())
            se = math.sqrt(float(np.sum(pf[idx] * (1 - pf[idx])))) / len(idx)
            out.append({"season": season, "bin": i + 1, "n": len(idx), "p_min": float(pf[idx].min()),
                        "p_max": float(pf[idx].max()), "mean_p": mp, "observed": ob, "obs_minus_p": ob - mp,
                        "binomial_se": se, "z": (ob - mp) / se})
        return out, int((~keep).sum())
    rel_rows, rel_excl = [], {}
    for s in (2023, 2024, 2025):
        d = pred[pred["season"] == s]
        r, rel_excl[s] = rel_table(d["p_a"], d["y_a"], s)
        rel_rows += r
    rel = save(pd.DataFrame(rel_rows), "reliability_bins_2023_2025.csv")
    S += ["## 2.2 Grids, calibration slope by cell, reliability bins", "",
          "### Full grids (2022 warm-up; h = 0; independent engine)",
          md(grid[["K", "lam", "brier_2023", "log_loss_2023", "brier_2024", "log_loss_2024", "rank"]]), "",
          f"Spearman rank agreement across the 20 cells, 2023 vs 2024: Brier {rho_b:.4f}, log loss {rho_l:.4f}.",
          f"2023 Brier argmin: K={best_cell(grid, 'brier_2023', 'log_loss_2023')[0]:g}, "
          f"lam={best_cell(grid, 'brier_2023', 'log_loss_2023')[1]:g}; "
          f"2023 Brier monotone in K / in lam: {monotone(piv_b23)}.", "",
          "### Calibration slope (no-intercept logistic of y on logit p, as D4; > 1 = forecasts too close to 0.5)",
          "2023:", md(sp_piv[2023].reset_index().rename(columns=lambda c: f"lam={c}" if c != "K" else c), "{:.3f}"),
          "", "2024:", md(sp_piv[2024].reset_index().rename(columns=lambda c: f"lam={c}" if c != "K" else c),
                          "{:.3f}"), "",
          f"Slope falls as K rises at every lam: 2023 {dec_k[2023]}, 2024 {dec_k[2024]}; as lam rises at every K: "
          f"2023 {dec_l[2023]}, 2024 {dec_l[2024]}. Slope > 1 in all 40 cell-seasons: {all_gt1}. Smallest slope: "
          f"2023 {min_sl[2023]:.3f} at (K={min_at[2023][0]}, lam={min_at[2023][1]}), 2024 {min_sl[2024]:.3f} at "
          f"(K={min_at[2024][0]}, lam={min_at[2024][1]}). Point estimates only (no intervals per cell).", "",
          "### Reliability bins at K=40, lam=1 (recorded predictions.csv; favourite orientation; 5 equal-count bins)",
          "binomial_se = sqrt(sum p(1-p))/n, the SE of the observed rate if the forecasts were calibrated; "
          "z = (observed - mean_p)/se. Games with p = 0.5 excluded: " +
          ", ".join(f"{s}: {v}" for s, v in rel_excl.items()) + ".",
          md(rel[["season", "bin", "n", "p_min", "p_max", "mean_p", "observed", "obs_minus_p", "binomial_se", "z"]],
             "{:.4f}"), ""]

    # ======================================================================= 2.3
    g24 = fb[fb["season"] == 2024][["season", "date", "game_key", "team_a", "team_b"]].reset_index(drop=True)
    o24 = g24.sort_values(["date", "game_key"], kind="mergesort").reset_index(drop=True)
    split_date = o24["date"].iloc[len(o24) // 2]
    sched = fb[["season", "date", "game_key", "team_a", "team_b"]]
    pc_season = prior_counts(sched, within_season=True).set_index("game_key")
    pc_all = prior_counts(sched, within_season=False).set_index("game_key")
    k24 = g24["game_key"]
    first_half = (g24["date"] < split_date).to_numpy()
    s3 = ((pc_season.loc[k24, "prior_a"] >= 3) & (pc_season.loc[k24, "prior_b"] >= 3)).to_numpy()
    s8 = ((pc_all.loc[k24, "prior_a"] >= 8) & (pc_all.loc[k24, "prior_b"] >= 8)).to_numpy()
    slices = {"first half (date < " + split_date + ")": first_half, "second half": ~first_half,
              "both >= 3 prior 2024 games": s3, "not both >= 3 prior 2024 games": ~s3,
              "both >= 8 prior games since 2022": s8, "not both >= 8 prior games since 2022": ~s8}
    sl_rows = []
    for k, lam in CELLS:
        f = cell(k, lam)
        f24 = f[f["season"] == 2024].reset_index(drop=True)
        assert (f24["game_key"].to_numpy() == g24["game_key"].to_numpy()).all()
        for name, m in slices.items():
            x = f24[m]
            sl_rows.append({"slice": name, "K": int(k), "lam": lam, "n": len(x), "brier": I.brier(x["p_a"], x["y_a"]),
                            "log_loss": I.log_loss(x["p_a"], x["y_a"])})
    sl = save(pd.DataFrame(sl_rows), "slices_2024.csv")
    S += ["## 2.3 Slices of 2024 (full grid; best cell per slice by Brier, log loss breaks ties)", "",
          f"Half split: 2024 games sorted by date; the first half is every date before {split_date} (the date of "
          f"game {len(o24) // 2 + 1} of {len(o24)}), so no date is split. Prior-game counts use earlier dates only.",
          ""]
    best_rows = []
    for name in slices:
        x = sl[sl["slice"] == name]
        bk, bl = best_cell(x)
        piv = x.pivot(index="K", columns="lam", values="brier")
        mk, ml = monotone(piv)
        best_rows.append({"slice": name, "n": int(x["n"].iloc[0]), "best_K": int(bk), "best_lam": bl,
                          "best_brier": float(x[(x.K == bk) & (x.lam == bl)]["brier"].iloc[0]),
                          "brier_at_40_1": float(x[(x.K == 40) & (x.lam == 1.0)]["brier"].iloc[0]),
                          "monotone_in_K": mk, "monotone_in_lam": ml})
        S += [f"**{name}** (n = {int(x['n'].iloc[0])}; best K={bk:g}, lam={bl:g}) — Brier, K rows x lam columns:",
              md(piv.reset_index().rename(columns=lambda c: f"lam={c}" if c != "K" else c)), ""]
    best_sl = save(pd.DataFrame(best_rows), "slices_2024_best.csv")
    S += ["Summary:", md(best_sl), ""]

    # ======================================================================= 2.4
    comps = [(30.0, 1.0), (40.0, 0.85), (20.0, 1.0), (10.0, 1.0)]
    f24b = fb[fb["season"] == 2024].reset_index(drop=True)
    y = f24b["y_a"].to_numpy(float)
    dates = f24b["date"].to_numpy()
    udates, inv = np.unique(dates, return_inverse=True)
    rng_b = np.random.default_rng(SEED)
    rng_g = np.random.default_rng(SEED)
    bt = []
    for k, lam in comps:
        alt = cell(k, lam)
        alt = alt[alt["season"] == 2024].reset_index(drop=True)
        assert (alt["game_key"].to_numpy() == f24b["game_key"].to_numpy()).all()
        dlt = (f24b["p_a"].to_numpy() - y) ** 2 - (alt["p_a"].to_numpy() - y) ** 2
        sums = np.bincount(inv, weights=dlt)
        cnts = np.bincount(inv).astype(float)
        idx = rng_b.integers(0, len(udates), size=(2000, len(udates)))
        est = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
        gidx = rng_g.integers(0, len(dlt), size=(2000, len(dlt)))
        gest = dlt[gidx].mean(axis=1)
        bt.append({"comparison": f"(40,1) minus ({k:g},{lam:g})", "n_games": len(dlt), "n_dates": len(udates),
                   "mean_diff": float(dlt.mean()),
                   "date_block_ci95_lo": float(np.quantile(est, 0.025)),
                   "date_block_ci95_hi": float(np.quantile(est, 0.975)),
                   "date_block_se": float(est.std(ddof=1)),
                   "game_level_ci95_lo": float(np.quantile(gest, 0.025)),
                   "game_level_ci95_hi": float(np.quantile(gest, 0.975)),
                   "game_level_se": float(gest.std(ddof=1)),
                   "share_of_dates_alt_better": float(np.mean(np.bincount(inv, weights=dlt) > 0))})
    bt = save(pd.DataFrame(bt), "block_bootstrap_2024.csv")
    S += ["## 2.4 Uncertainty: paired 2024 Brier differences, game dates resampled as blocks", "",
          f"2000 resamples of the {len(udates)} 2024 game dates with replacement (seed {SEED}); estimate = sum of "
          "per-game differences in the drawn dates / games in the drawn dates. The game-level columns resample games "
          "(separate generator, same seed) for comparison. Negative = (40,1) better. All in-sample (2024 is the "
          "selection season). share_of_dates_alt_better = fraction of dates on which the alternative had the lower "
          "Brier sum.", md(bt), ""]

    # ======================================================================= 2.5
    S += ["## 2.5 Rating changes 2022-2024 (independent engine)", ""]
    big_rows, q_rows, sd_rows, net_rows = [], [], [], []
    for k, lam in ((40.0, 1.0), (20.0, 1.0)):
        f = cell(k, lam)
        r = runs[(k, lam, "")]
        ad = f["d"].abs().to_numpy()
        srt = np.sort(ad)[::-1]
        top = int(math.ceil(0.05 * len(ad)))
        q = {"cell": f"({k:g},{lam:g})", "n_updates": len(ad), "mean": ad.mean()}
        for qq in (0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99):
            q[f"q{int(qq * 100)}"] = float(np.quantile(ad, qq))
        q["max"] = float(ad.max())
        q["top5pct_n"] = top
        q["top5pct_share_of_total_abs_dR"] = float(srt[:top].sum() / ad.sum())
        q_rows.append(q)
        bg = f.assign(abs_d=ad).sort_values(["abs_d", "game_key"], ascending=[False, True], kind="mergesort").head(20)
        for x in bg.itertuples():
            win_a = x.y_a == 1
            big_rows.append({"cell": f"({k:g},{lam:g})", "date": x.date,
                             "winner": x.team_a if win_a else x.team_b, "loser": x.team_b if win_a else x.team_a,
                             "score": f"{int(x.goals_a if win_a else x.goals_b)}-{int(x.goals_b if win_a else x.goals_a)}",
                             "p_winner_pre": x.p_a if win_a else 1 - x.p_a, "abs_dR": x.abs_d,
                             "sidedness": x.sidedness})
        for s in (2023, 2024):
            v = np.array(list(r["starts"][s].values()))
            sd_rows.append({"cell": f"({k:g},{lam:g})", "point": f"start {s} (after shrink)", "n_teams": len(v),
                            "sd": float(v.std(ddof=0)), "min": float(v.min()), "max": float(v.max())})
        v = np.array(list(r["ends"][2024].values()))
        sd_rows.append({"cell": f"({k:g},{lam:g})", "point": "end 2024", "n_teams": len(v),
                        "sd": float(v.std(ddof=0)), "min": float(v.min()), "max": float(v.max())})
        for s in (2022, 2023, 2024):
            fs = f[f["season"] == s]
            teams = sorted(set(fs["team_a"]) | set(fs["team_b"]))
            net = np.array([r["ends"][s][t] - r["starts"][s].get(t, 1500.0) for t in teams])
            row = {"cell": f"({k:g},{lam:g})", "season": s, "n_teams": len(teams)}
            for qq in (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0):
                row[f"q{int(qq * 100)}"] = float(np.quantile(net, qq))
            row["n_abs_net_gt_100"] = int((np.abs(net) > 100).sum())
            row["n_net_gt_100"] = int((net > 100).sum())
            row["n_net_lt_-100"] = int((net < -100).sum())
            net_rows.append(row)
    qd = save(pd.DataFrame(q_rows), "update_size_quantiles.csv")
    bgd = save(pd.DataFrame(big_rows), "largest_updates.csv")
    sdd = save(pd.DataFrame(sd_rows), "rating_sd_by_season_start.csv")
    nd = save(pd.DataFrame(net_rows), "season_net_change.csv")
    S += ["|dR| per update (one value per game; both teams move by it):", md(qd, "{:.3f}"), "",
          "20 largest updates per cell (score = winner-loser goals; p_winner_pre = winner's pre-game probability):",
          md(bgd, "{:.3f}"), "",
          "Rating spread (population SD over teams holding a rating; 2022 starts with no teams):", md(sdd, "{:.2f}"),
          "", "Per-team net change within a season (end of season minus season start after shrink; newcomers from "
          "1500); teams that played that season:", md(nd, "{:.1f}"), ""]

    # ======================================================================= 2.6
    inf_rows = []
    for k, lam in ((40.0, 1.0), (10.0, 1.0)):
        f = cell(k, lam)
        x = f[f["season"] == 2024]
        se = np.sort(((x["p_a"] - x["y_a"]) ** 2).to_numpy())[::-1]
        top = int(math.ceil(0.05 * len(se)))
        inf_rows.append({"cell": f"({k:g},{lam:g})", "n_2024": len(se), "worst_n": top,
                         "share_of_brier_sum": float(se[:top].sum() / se.sum()),
                         "worst_min_sq_err": float(se[top - 1]), "brier_2024": float(se.mean())})
    infa = save(pd.DataFrame(inf_rows), "influence_worst5pct_2024.csv")
    f40 = cell(*BEST)
    early = f40[f40["season"] <= 2023].assign(abs_d=lambda d: d["d"].abs()) \
        .sort_values(["abs_d", "game_key"], ascending=[False, True], kind="mergesort")
    base_piv = grid.pivot(index="K", columns="lam", values="brier_2024")
    excl_rows, excl_lists, piv_txt = [], [], []
    for nx in (10, 20):
        drop = frozenset(early["game_key"].head(nx))
        excl_lists += [{"n_excluded": nx, "rank": i + 1, "game_key": g, "season": int(s), "abs_dR_at_40_1": float(v)}
                       for i, (g, s, v) in enumerate(zip(early["game_key"].head(nx), early["season"].head(nx),
                                                         early["abs_d"].head(nx)))]
        rows = []
        for k, lam in CELLS:
            f = cell(k, lam, drop, tag=f"drop{nx}")
            n, bb, ll = metrics(f, 2024)
            rows.append({"K": int(k), "lam": lam, "brier": bb, "log_loss": ll})
        dg = pd.DataFrame(rows)
        piv = dg.pivot(index="K", columns="lam", values="brier")
        mk, ml = monotone(piv)
        bk, bl = best_cell(dg)
        excl_rows += [{"n_excluded_from_updates": nx, "K": r.K, "lam": r.lam, "brier_2024": r.brier,
                       "log_loss_2024": r.log_loss, "delta_vs_full": r.brier - base_piv.loc[int(r.K), r.lam]}
                      for r in dg.itertuples()]
        piv_txt += [f"**{nx} largest 2022-2023 updates excluded from updates** (still scored): argmin K={bk:g}, "
                    f"lam={bl:g}; monotone in K {mk}, in lam {ml}. 2024 Brier:",
                    md(piv.reset_index().rename(columns=lambda c: f"lam={c}" if c != "K" else c)), ""]
    save(pd.DataFrame(excl_rows), "influence_excluded_grid_2024.csv")
    save(pd.DataFrame(excl_lists), "influence_excluded_games.csv")
    S += ["## 2.6 Influence", "",
          "(a) Share of the 2024 Brier sum from the worst 5% of games (expected share if errors were equal: 5%):",
          md(infa), "",
          "(b) The 10 and 20 largest-|update| games of 2022-2023 were identified at the frozen cell (40,1) (the "
          "ranking of |S - p| depends on the cell, so one cell had to be chosen) and excluded from rating updates in "
          "every cell, while still predicted. Lists in influence_excluded_games.csv.", ""] + piv_txt

    # ======================================================================= 2.7
    pc = pc_all.reindex(fb["game_key"]).reset_index()
    low = set(pc.loc[(pc["prior_a"] < 5) | (pc["prior_b"] < 5), "game_key"])
    fl = cell(40.0, 1.0, frozenset(low), tag="lowhist")
    lh_rows = []
    for s in (2022, 2023, 2024):
        base = fb[fb["season"] == s]
        keep = base[~base["game_key"].isin(low)]
        keep2 = fl[(fl["season"] == s) & ~fl["game_key"].isin(low)]
        lh_rows.append({"season": s, "variant": "all games (reference)", "n_scored": len(base),
                        "n_low_history": int(base["game_key"].isin(low).sum()),
                        "brier": I.brier(base["p_a"], base["y_a"]), "log_loss": I.log_loss(base["p_a"], base["y_a"])})
        lh_rows.append({"season": s, "variant": "low-history excluded from scoring only", "n_scored": len(keep),
                        "n_low_history": int(base["game_key"].isin(low).sum()),
                        "brier": I.brier(keep["p_a"], keep["y_a"]), "log_loss": I.log_loss(keep["p_a"], keep["y_a"])})
        lh_rows.append({"season": s, "variant": "excluded from updates and scoring", "n_scored": len(keep2),
                        "n_low_history": int(base["game_key"].isin(low).sum()),
                        "brier": I.brier(keep2["p_a"], keep2["y_a"]),
                        "log_loss": I.log_loss(keep2["p_a"], keep2["y_a"])})
    pc25 = prior_counts(pred[["season", "date", "game_key", "team_a", "team_b"]], within_season=False) \
        .set_index("game_key")
    p25 = pred[pred["season"] == 2025]
    low25 = ((pc25.loc[p25["game_key"], "prior_a"] < 5) | (pc25.loc[p25["game_key"], "prior_b"] < 5)).to_numpy()
    for name, x in (("all games (recorded)", p25), ("low-history games only (recorded)", p25[low25]),
                    ("low-history excluded from scoring (recorded)", p25[~low25])):
        lh_rows.append({"season": 2025, "variant": name, "n_scored": len(x), "n_low_history": int(low25.sum()),
                        "brier": np_brier(x["p_a"], x["y_a"]), "log_loss": np_ll(x["p_a"], x["y_a"])})
    lh = save(pd.DataFrame(lh_rows), "low_history.csv")
    S += ["## 2.7 Low history at K=40, lam=1", "",
          "Low-history game: either team had fewer than 5 games on earlier dates, counted since 2022 (the D6 "
          "convention; same-date games do not count). For 'excluded from updates and scoring' the engine is rerun "
          "on 2022-2024 with those games predicted but not updated; prior counts stay schedule-based. 2025 is "
          "descriptive from recorded predictions.csv (no rerun is possible without the 2025 file).",
          md(lh), ""]

    # ======================================================================= recorded tables vs recomputation
    def cmp(source, item, rec_v, new_v):
        try:
            rv, nv = float(rec_v), float(new_v)
            if not (abs(rv - nv) <= TOL or (np.isnan(rv) and np.isnan(nv))):
                DIFFS.append({"source": source, "item": item, "recorded": rec_v, "recomputed": new_v,
                              "abs_diff": abs(rv - nv)})
        except (TypeError, ValueError):
            if str(rec_v) != str(new_v):
                DIFFS.append({"source": source, "item": item, "recorded": rec_v, "recomputed": new_v,
                              "abs_diff": float("nan")})
    counts = {"grid_2024.csv": 20 * (len(mcols) + len(ncols))}
    # metrics_by_season: 2023/2024 independent engine, 2025 recorded predictions
    nm = 0
    for r in mbs.itertuples():
        src = fb if r.season <= 2024 else pred
        d = src[src["season"] == r.season]
        if r.subset == "TWO_SIDED":
            d = d[d["sidedness"] == "TWO_SIDED"]
        y_ = d["y_a"].to_numpy(float)
        vals = {"n": len(d), "brier": I.brier(d["p_a"], d["y_a"]), "log_loss": I.log_loss(d["p_a"], d["y_a"]),
                "brier_const": float(np.mean((0.5 - y_) ** 2)), "log_loss_const": math.log(2)}
        vals["brier_minus_const"] = vals["brier"] - vals["brier_const"]
        vals["log_loss_minus_const"] = vals["log_loss"] - vals["log_loss_const"]
        for c, v in vals.items():
            cmp("metrics_by_season.csv", f"{r.season} {r.subset} {c}", getattr(r, c), v)
            nm += 1
    counts["metrics_by_season.csv"] = nm
    # D1
    prof = []
    for lam in base_piv.columns:
        for k0, k1 in zip(base_piv.index[:-1], base_piv.index[1:]):
            prof.append(("K", f"lam={lam}", k0, k1, base_piv.loc[k0, lam], base_piv.loc[k1, lam]))
    for k in base_piv.index:
        for l0, l1 in zip(base_piv.columns[:-1], base_piv.columns[1:]):
            prof.append(("lam", f"K={k}", l0, l1, base_piv.loc[k, l0], base_piv.loc[k, l1]))
    d1 = D_rec["D1"]
    nm = 0
    for i, (ax, fx, f0, t0, bf, bt_) in enumerate(prof):
        r = d1.iloc[i]
        for c, v in (("axis", ax), ("fixed", fx), ("from", f0), ("to", t0), ("brier_2024_from", bf),
                     ("brier_2024_to", bt_), ("delta", bt_ - bf)):
            cmp("audit_report D1", f"row {i + 1} {c}", r[c], v)
            nm += 1
    counts["audit_report D1"] = nm
    # D2
    crit = ["brier_2024", "log_loss_2024", "brier_2024_two_sided", "log_loss_2024_two_sided", "brier_2023",
            "log_loss_2023"]
    nm = 0
    for i, c in enumerate(crit):
        r = grid.sort_values([c, "K", "lam"], kind="mergesort").iloc[0]
        rr = D_rec["D2"].iloc[i]
        for col, v in (("criterion", c), ("argmin_K", r["K"]), ("argmin_lam", r["lam"]), ("value", r[c]),
                       ("same_as_frozen", (float(r["K"]), float(r["lam"])) == BEST)):
            cmp("audit_report D2", f"{c} {col}", rr[col], v)
            nm += 1
    counts["audit_report D2"] = nm
    # D3 + D4: replay the first audit's random-number sequence exactly (one generator, same order of draws)
    rng = np.random.default_rng(SEED)

    def boot(x):
        idx = rng.integers(0, len(x), size=(2000, len(x)))
        bs = x[idx].mean(axis=1)
        return float(x.mean()), float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))
    d3 = []
    for s in (2023, 2024, 2025):
        for sub in ("ALL", "TWO_SIDED"):
            src = fb if s <= 2024 else pred
            d = src[src["season"] == s]
            if sub == "TWO_SIDED":
                d = d[d["sidedness"] == "TWO_SIDED"]
            p, yy = d["p_a"].to_numpy(float), d["y_a"].to_numpy(float)
            d3.append(boot((p - yy) ** 2 - (0.5 - yy) ** 2))
    pb = fb[fb["season"] == 2024]
    yb = pb["y_a"].to_numpy(float)
    for k, lam in ((40.0, 0.85), (30.0, 1.0), (40.0, 0.7), (30.0, 0.85)):
        alt = cell(k, lam)
        alt = alt[alt["season"] == 2024]
        d3.append(boot((pb["p_a"].to_numpy() - yb) ** 2 - (alt["p_a"].to_numpy() - yb) ** 2))
    nm = 0
    for i, (mn, lo, hi) in enumerate(d3):
        rr = D_rec["D3"].iloc[i]
        for col, v in (("mean_diff", mn), ("ci95_lo", lo), ("ci95_hi", hi)):
            cmp("audit_report D3", f"row {i + 1} ({rr['comparison']} {rr['season']} {rr['subset']}) {col}", rr[col], v)
            nm += 1
    counts["audit_report D3"] = nm
    nm = 0
    for i, s in enumerate((2023, 2024, 2025)):
        src = fb if s <= 2024 else pred
        d = src[(src["season"] == s) & (src["p_a"] != 0.5)]
        p, yy = d["p_a"].to_numpy(float), d["y_a"].to_numpy(float)
        pf, yf = np.where(p > 0.5, p, 1 - p), np.where(p > 0.5, yy, 1 - yy)
        bsl = slope_no_intercept(p, yy)
        idx = rng.integers(0, len(p), size=(500, len(p)))
        bs = np.array([slope_no_intercept(p[j], yy[j]) for j in idx])
        rr = D_rec["D4"].iloc[i]
        for col, v in (("n", len(d)), ("mean_p_fav", pf.mean()), ("favourite_win_rate", yf.mean()),
                       ("fav_rate_minus_mean_p", yf.mean() - pf.mean()), ("calibration_slope", bsl),
                       ("slope_ci95_lo", float(np.quantile(bs, 0.025))),
                       ("slope_ci95_hi", float(np.quantile(bs, 0.975)))):
            cmp("audit_report D4", f"{s} {col}", rr[col], v)
            nm += 1
    counts["audit_report D4"] = nm
    # D5
    nm = 0
    i = 0
    for s in (2023, 2024, 2025):
        src = fb if s <= 2024 else pred
        d = src[src["season"] == s]
        for name, x in (("ALL", d), ("TWO_SIDED", d[d["sidedness"] == "TWO_SIDED"]),
                        ("SINGLE_SIDED", d[d["sidedness"] == "SINGLE_SIDED"])):
            rr = D_rec["D5"].iloc[i]
            i += 1
            for col, v in (("season", s), ("subset", name), ("n", len(x)), ("share", len(x) / len(d)),
                           ("brier", I.brier(x["p_a"], x["y_a"])), ("log_loss", I.log_loss(x["p_a"], x["y_a"])),
                           ("mean_abs_rating_gap", float(np.mean(np.abs(x["R_a_before"] - x["R_b_before"]))))):
                cmp("audit_report D5", f"{s} {name} {col}", rr[col], v)
                nm += 1
    counts["audit_report D5"] = nm
    # D6
    mp = pc25.reset_index()
    mp["min_prior"] = mp[["prior_a", "prior_b"]].min(axis=1)
    nm, i = 0, 0
    for s in (2023, 2024, 2025):
        src = fb if s <= 2024 else pred
        d = src[src["season"] == s].merge(mp[["game_key", "min_prior"]], on="game_key")
        d["bucket"] = pd.cut(d["min_prior"], [-1, 4, 9, 19, 10 ** 6], labels=["0-4", "5-9", "10-19", "20+"])
        for bk in ["0-4", "5-9", "10-19", "20+"]:
            x = d[d["bucket"] == bk]
            if not len(x):
                continue
            rr = D_rec["D6"].iloc[i]
            i += 1
            for col, v in (("season", s), ("min_prior_games_bucket", bk), ("n", len(x)),
                           ("brier", I.brier(x["p_a"], x["y_a"])),
                           ("single_sided_in_bucket", int((x["sidedness"] == "SINGLE_SIDED").sum()))):
                cmp("audit_report D6", f"{s} {bk} {col}", rr[col], v)
                nm += 1
    counts["audit_report D6"] = nm
    if i != len(D_rec["D6"]):
        DIFFS.append({"source": "audit_report D6", "item": "row count", "recorded": len(D_rec["D6"]),
                      "recomputed": i, "abs_diff": abs(len(D_rec["D6"]) - i)})

    # ======================================================================= findings
    rel_big = rel[np.abs(rel["z"]) >= 2]
    FIND["ERRORS"] += [
        "Carried from the first audit, still standing: the Chunk 4 report's 'largest rating changes' table counted "
        "77 teams 'present at both' starts, including Lindenwood (no 2025 game; listed 2nd down) and St. Andrews and "
        "Wabash (no 2024 or 2025 game); its 'cross-checked against stored pre-game ratings' sentence covered only "
        "teams that played. Not re-examined here."]
    if DIFFS:
        FIND["ERRORS"].append(f"{len(DIFFS)} recomputed values differ from recorded tables by more than {TOL:g} "
                              "(table below).")
    b40 = bt[bt["comparison"] == "(40,1) minus (40,0.85)"].iloc[0]
    b30 = bt[bt["comparison"] == "(40,1) minus (30,1)"].iloc[0]
    FIND["UNRESOLVED"] += [
        f"The selected cell is a grid corner and the 2024 Brier is still falling at both edges; whether the optimum "
        f"lies beyond K=40 / lam=1 cannot be settled without widening the grid, which is out of scope here.",
        f"Is K=40, lam=1 meaningfully better than its neighbours? With dates as blocks the 2024 difference vs (40,0.85) "
        f"is {b40.mean_diff:+.5f} [{b40.date_block_ci95_lo:+.5f}, {b40.date_block_ci95_hi:+.5f}] and vs (30,1) "
        f"{b30.mean_diff:+.5f} [{b30.date_block_ci95_lo:+.5f}, {b30.date_block_ci95_hi:+.5f}]; this is in-sample "
        "(selection season), so it does not say how the choice generalises.",
        "2025 looks less under-confident than 2023/2024 (D4 slope 1.23 [0.98, 1.54] vs 1.49 and 1.57; no 2025 "
        "reliability bin has |z| >= 2). Whether that reflects the shorter 2025 slice (last date 2025-04-08), the "
        "larger rating spread by 2025, or sampling cannot be separated from recorded predictions alone.",
        "Two conventions here are my choices, not given in the brief: 2.6(b) identifies the largest updates at "
        "(40,1); 2.7 counts prior games since 2022 (the D6 convention), under which only 12 (2023) and 5 (2024) "
        "games are low-history. A within-season count would flag far more games and could change 2.7."]
    FIND["OBSERVATIONS"] += [
        f"Spearman rank agreement of the 20 cells between 2023 and 2024: Brier {rho_b:.3f}, log loss {rho_l:.3f}.",
        f"Calibration slope is > 1 in all 40 cell-seasons ({all_gt1}) and falls toward 1 as K rises (2023 "
        f"{dec_k[2023]}, 2024 {dec_k[2024]}) and as lam rises (2023 {dec_l[2023]}, 2024 {dec_l[2024]}); it is "
        f"closest to 1 at the selected corner (2023 {sp_piv[2023].loc[40, 1.0]:.3f}, 2024 "
        f"{sp_piv[2024].loc[40, 1.0]:.3f}). Every cell's forecasts are too close to 0.5, and the bolder the cell the "
        "better its Brier; this is consistent with (not proof of) the grid edge being driven by under-dispersed "
        "forecasts.",
        f"Reliability bins with |z| >= 2 (of 15): {len(rel_big)} -> " +
        ("; ".join(f"{int(r.season)} bin {int(r.bin)} z={r.z:+.2f}" for r in rel_big.itertuples()) or "none") + ".",
        "Best cell per 2024 slice: " + "; ".join(f"{r.slice} (n={r.n}): ({r.best_K:g},{r.best_lam:g})"
                                                 for r in best_sl.itertuples()) + ".",
        "Date-block vs game-level bootstrap SE ratio, 2024 paired differences: " +
        "; ".join(f"{r.comparison} {r.date_block_se / r.game_level_se:.2f}" for r in bt.itertuples()) + ".",
        f"Top 5% of updates carry {qd.iloc[0]['top5pct_share_of_total_abs_dR']:.1%} of total |dR| at (40,1) and "
        f"{qd.iloc[1]['top5pct_share_of_total_abs_dR']:.1%} at (20,1).",
        f"Worst 5% of 2024 games carry {infa.iloc[0]['share_of_brier_sum']:.1%} of the Brier sum at (40,1) and "
        f"{infa.iloc[1]['share_of_brier_sum']:.1%} at (10,1).",
        "With lam = 1 and no shrink the rating spread grows season on season: SD " +
        ", ".join(f"{r.point} {r.sd:.1f}" for r in sdd[sdd['cell'] == '(40,1)'].itertuples()) + " at (40,1).",
        "Slices of 2024: (40,1) is best in every slice except 'not both >= 8 prior games since 2022', which has "
        "n = 8 and is too small to read.",
        "Excluding the 10 / 20 largest 2022-2023 updates leaves the 2024 grid monotone with the same argmin (40,1); "
        "its 2024 Brier moves from 0.18041 to " +
        f"{float(pd.DataFrame(excl_rows).query('n_excluded_from_updates == 10 and K == 40 and lam == 1.0')['brier_2024'].iloc[0]):.5f}"
        " / " +
        f"{float(pd.DataFrame(excl_rows).query('n_excluded_from_updates == 20 and K == 40 and lam == 1.0')['brier_2024'].iloc[0]):.5f}.",
        "Low history: dropping low-history games from updates as well as scoring makes 2023 and 2024 Brier worse "
        "(" + "; ".join(f"{r.season}: {r.brier:.5f}" for r in lh[lh['variant'] == 'excluded from updates and scoring']
                        .itertuples() if r.season >= 2023) +
        ") than scoring-only exclusion (" +
        "; ".join(f"{r.season}: {r.brier:.5f}" for r in lh[lh['variant'] == 'low-history excluded from scoring only']
                  .itertuples() if r.season >= 2023) +
        "). In 2025 the 10 low-history games (all single-sided) score Brier 0.350 (descriptive, n = 10).",
        "Date blocks do not widen the 2024 intervals: SE ratios of about 1.0, so same-date correlation is not "
        "visible at this size."]
    S += findings_md(FIND, DIFFS, counts)
    return S


def findings_md(FIND, DIFFS, counts=None) -> list[str]:
    L = ["## FINDINGS", ""]
    for k in ("ERRORS", "UNRESOLVED", "OBSERVATIONS"):
        L += [f"### {k}"] + ([f"- {x}" for x in FIND[k]] or ["- none"]) + [""]
    L += ["## Differences from recorded values (tolerance 1e-9)", ""]
    if counts:
        L += ["Values compared: " + "; ".join(f"{k} {v}" for k, v in counts.items()) + "."]
    if DIFFS:
        df = save(pd.DataFrame(DIFFS), "differences_vs_recorded.csv")
        L += ["", md(df, "{:.6g}")]
    else:
        save(pd.DataFrame(columns=["source", "item", "recorded", "recomputed", "abs_diff"]),
             "differences_vs_recorded.csv")
        L += ["", "**No differences.** Every compared value reproduces within the tolerance."]
    L += ["", f"_{DIAG}_"]
    return L


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        with G._orig_open(CH2 / "files_opened.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# how\tR/W\tpath  (every open through builtins.open/io.open/pathlib/os.open, in order; "
                     "this file itself is written with the unwrapped open)\n")
            fh.write("\n".join(G.OPENED) + "\n")
    sys.exit(code)
