"""READ-ONLY methodological audit of the Elo feasibility experiment. Every table: diagnostic, no re-selection.

Hard rules implemented here:
- Writes only under elo_feas/audit_out/ (guards on open, io.open, pathlib, os.open, mkdir, remove, rename, rmtree).
- The raw results files for 2025 and 2026 can never be opened (guard raises); every path opened is logged to
  audit_out/files_opened.txt.
- evaluate_2025 and games_2022_2025 are replaced in memory by raisers; data_layer.build_all is wrapped so any
  season > 2024 raises. Elo is recomputed only on selection_games() (2022-2024).
- K = 40, lam = 1.0, h = 0 as frozen; the grid is not widened; nothing here feeds back into selection.
- Deterministic: fixed seeds, sorted inputs, no timestamps in outputs.
Run:  $env:PYTHONDONTWRITEBYTECODE=1; .\\.venv\\Scripts\\python.exe -B elo_feas\\audit_out\\methodology_audit.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import builtins  # noqa: E402
import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import pathlib  # noqa: E402
import shutil  # noqa: E402

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUDIT_OUT = HERE
OUT = ROOT / "elo_feas" / "out"
DIAG = "diagnostic, no re-selection"
RAW_BLOCK = ("ncaa_mens_lacrosse_matchstats_2025", "ncaa_mens_lacrosse_matchstats_2026")
PROTECTED = ("odds_client.py", "ledger.py", "halt.py", "stage_a.py", "stage_b.py", "stage_c.py", "stage_d.py",
             "normalize.py", "analysis.py", "costs.py")
FROZEN_K, FROZEN_LAM, FROZEN_H = 40.0, 1.0, 0.0
SEED = 20261111
N_BOOT = 2000

# --------------------------------------------------------------------------- guards (installed before anything else)
OPENED: list[str] = []
_orig_open, _orig_os_open = builtins.open, os.open
_orig_mkdir, _orig_remove, _orig_unlink = os.mkdir, os.remove, os.unlink
_orig_rename, _orig_replace, _orig_rmdir, _orig_rmtree = os.rename, os.replace, os.rmdir, shutil.rmtree


def _rel(p) -> str:
    a = pathlib.Path(os.path.abspath(os.fspath(p)))
    try:
        return a.relative_to(ROOT).as_posix()
    except ValueError:
        return a.as_posix()


def _inside_audit_out(p) -> bool:
    a = os.path.normcase(os.path.abspath(os.fspath(p)))
    base = os.path.normcase(str(AUDIT_OUT))
    return a == base or a.startswith(base + os.sep)


def _check(p, writing: bool, how: str) -> None:
    if isinstance(p, int):
        OPENED.append(f"{how}\t{'W' if writing else 'R'}\t<fd {p}>")
        return
    OPENED.append(f"{how}\t{'W' if writing else 'R'}\t{_rel(p)}")
    low = os.fspath(p).replace("\\", "/").lower()
    if any(b in low for b in RAW_BLOCK):
        raise PermissionError(f"audit guard: raw 2025/2026 results file may not be opened: {p}")
    if writing and not _inside_audit_out(p):
        raise PermissionError(f"audit guard: write outside elo_feas/audit_out refused: {p}")


def guarded_open(file, mode="r", *args, **kwargs):
    _check(file, any(c in mode for c in "wax+"), "open")
    return _orig_open(file, mode, *args, **kwargs)


def guarded_os_open(path, flags, *args, **kwargs):
    w = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC)
    _check(path, bool(w), "os.open")
    return _orig_os_open(path, flags, *args, **kwargs)


def _write_op(orig, name):
    def f(*paths, **kwargs):
        for p in paths:
            if isinstance(p, (str, bytes, os.PathLike)):
                _check(p, True, name)
        return orig(*paths, **kwargs)
    return f


def install_guards() -> None:
    builtins.open = guarded_open
    io.open = guarded_open
    if hasattr(pathlib, "_NormalAccessor"):                       # Python 3.10: Path.open goes via the accessor
        pathlib._NormalAccessor.open = staticmethod(guarded_open)
    os.open = guarded_os_open
    os.mkdir = _write_op(_orig_mkdir, "mkdir")
    os.remove = _write_op(_orig_remove, "remove")
    os.unlink = _write_op(_orig_unlink, "unlink")
    os.rename = _write_op(_orig_rename, "rename")
    os.replace = _write_op(_orig_replace, "replace")
    os.rmdir = _write_op(_orig_rmdir, "rmdir")
    shutil.rmtree = _write_op(_orig_rmtree, "rmtree")


def guard_selftest() -> list[tuple[str, str]]:
    """Each probe must raise PermissionError from the guard. Probes point at paths that do not exist, so a broken
    guard yields FileNotFoundError instead and nothing is created."""
    probes = [("write outside audit_out (builtins.open)", lambda: open(ROOT / "__no_such_dir__" / "x.txt", "w")),
              ("write outside audit_out (pathlib)", lambda: (ROOT / "__no_such_dir__" / "x.txt").write_text("x")),
              ("write outside audit_out (os.open)",
               lambda: os.open(str(ROOT / "__no_such_dir__" / "x.txt"), os.O_WRONLY | os.O_CREAT)),
              ("read raw 2025 file name", lambda: open(ROOT / "__no_such_dir__" /
                                                        "ncaa_mens_lacrosse_matchstats_2025.csv")),
              ("read raw 2026 file name (pathlib)", lambda: (ROOT / "__no_such_dir__" /
                                                              "ncaa_mens_lacrosse_matchstats_2026.csv").read_text())]
    res = []
    for name, fn in probes:
        try:
            fn()
            res.append((name, "FAIL: no exception"))
        except PermissionError:
            res.append((name, "PASS"))
        except Exception as e:  # noqa: BLE001
            res.append((name, f"FAIL: {type(e).__name__}"))
    OPENED.append("-- end of guard self-test probes (above lines are the refused probes) --")
    return res


install_guards()

# --------------------------------------------------------------------------- hashing
def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_targets() -> list[pathlib.Path]:
    files = [ROOT / "src" / n for n in PROTECTED]
    files += sorted((p for p in (ROOT / "audit_join").rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    files += sorted((p for p in OUT.rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    files += sorted((ROOT / "elo_feas").glob("*.py"), key=lambda p: p.as_posix())
    files += sorted((p for p in (ROOT / "elo_feas" / "tests").rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    for must in ("audit_join/manual_verification.csv", "audit_join/reviewed_overrides.csv",
                 "audit_join/out_run1/SHA256SUMS.txt", "audit_join/out_run2/join_revision.md"):
        if ROOT / must not in files:
            raise RuntimeError(f"expected hash target missing: {must}")
    seen, out = set(), []
    for p in files:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def snapshot() -> dict[str, tuple[int, str]]:
    return {_rel(p): (p.stat().st_size, sha256_file(p)) for p in hash_targets()}


def write_snapshot(snap: dict, name: str) -> None:
    lines = ["path,size,sha256"] + [f"{k},{v[0]},{v[1]}" for k, v in snap.items()]
    (AUDIT_OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_reference(p: pathlib.Path) -> dict[str, str]:
    ref = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            h, path = line.split(None, 1)
            ref[path.strip()] = h
    return ref


def compare_reference(snap: dict, ref: dict) -> list[str]:
    bad = []
    for path, h in sorted(ref.items()):
        if path not in snap:
            bad.append(f"MISSING NOW {path}")
        elif snap[path][1] != h:
            bad.append(f"CHANGED {path}: reference {h} now {snap[path][1]}")
    return bad


# --------------------------------------------------------------------------- recorded numbers (Chunk 4 report)
RECORDED = {
    "games": {2022: (543, 543, 0), 2023: (561, 546, 15), 2024: (570, 548, 22), 2025: (409, 367, 42)},
    "newcomers": {2022: 73, 2023: ["Lindenwood", "Queens (NC)", "St. Andrews", "Wabash"], 2024: ["Le Moyne"],
                  2025: ["Iona", "Mercyhurst"]},
    "selected": (40.0, 1.0), "brier_2024_sel": 0.18041, "log_loss_2024_sel": 0.53997,
    "brier_range": (0.18041, 0.22820),
    "metrics": {(2023, "ALL"): (561, 0.1990, 0.5839), (2023, "TWO_SIDED"): (546, 0.1976, 0.5806),
                (2024, "ALL"): (570, 0.1804, 0.5400), (2024, "TWO_SIDED"): (548, 0.1801, 0.5393),
                (2025, "ALL"): (409, 0.1874, 0.5558), (2025, "TWO_SIDED"): (367, 0.1860, 0.5520)},
    "sens": {0: 0.18041, 25: 0.17979, 50: 0.18109, 75: 0.18421},
    "up": [("Sacred Heart", 165.1), ("Notre Dame", 159.6), ("Syracuse", 156.6), ("Towson", 149.1),
           ("UAlbany", 111.8)],
    "down": [("Mount St. Mary's", -202.3), ("Lindenwood", -144.5), ("Stony Brook", -134.5), ("Wagner", -121.3),
             ("Queens (NC)", -106.8)],
    "n_both": 77,
    "rel": {2024: ([0.5283, 0.5964, 0.6597, 0.7299, 0.8415], [0.5614, 0.5614, 0.7807, 0.8421, 0.9123]),
            2025: ([0.5335, 0.6077, 0.6768, 0.7522, 0.8628], [0.5732, 0.6220, 0.7561, 0.7805, 0.9012])},
    "table_sha256": "9a25bbd49671e6e890215c0d0a919c68b1e9a5ed0e3413dfae75053cc750bebd",
    "params_sha256": "6cd0ce93f7c282a7fef83aadae832f72c997405317f658ca1cec70e1264c44ab",
    "n_updates": 2083,
}


def main() -> int:
    selftest = guard_selftest()
    if any(r != "PASS" for _, r in selftest):
        raise RuntimeError(f"guard self-test failed: {selftest}")

    before = snapshot()
    write_snapshot(before, "hashes_before.csv")
    ref_protected = compare_reference(before, read_reference(ROOT / "audit_join" / "protected_hashes.txt"))
    ref_chunk4 = compare_reference(before, read_reference(AUDIT_OUT / "reference_hashes_chunk4_prerun.txt"))
    if ref_protected or ref_chunk4:
        (AUDIT_OUT / "hash_diff.txt").write_text(
            "STOP: reference mismatch before the audit\n" + "\n".join(ref_protected + ref_chunk4) + "\n",
            encoding="utf-8")
        return 2

    findings = run_audit()

    after = snapshot()
    write_snapshot(after, "hashes_after.csv")
    diff = []
    for k in sorted(set(before) | set(after)):
        if k not in after:
            diff.append(f"REMOVED {k}")
        elif k not in before:
            diff.append(f"ADDED {k}")
        elif before[k] != after[k]:
            diff.append(f"CHANGED {k}: {before[k][1]} -> {after[k][1]}")
    L = [f"files hashed: {len(before)} before, {len(after)} after",
         f"before vs after: {'NO DIFFERENCES' if not diff else str(len(diff)) + ' DIFFERENCES - STOP'}", *diff,
         f"before vs audit_join/protected_hashes.txt ({len(read_reference(ROOT / 'audit_join' / 'protected_hashes.txt'))}"
         f" entries): {'ALL MATCH' if not ref_protected else 'MISMATCH'}",
         f"before vs Chunk 4 pre-run record ({len(read_reference(AUDIT_OUT / 'reference_hashes_chunk4_prerun.txt'))}"
         f" entries, audit_join + elo_feas sources/tests): {'ALL MATCH' if not ref_chunk4 else 'MISMATCH'}",
         "guard self-test: " + "; ".join(f"{n}={r}" for n, r in selftest)]
    (AUDIT_OUT / "hash_diff.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    write_report(findings, selftest, diff, len(before))
    return 2 if diff else 0


# --------------------------------------------------------------------------- the audit
def run_audit() -> dict:
    import numpy as np
    import pandas as pd

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from audit_join import join_audit as J
    J.install_socket_guard()
    from elo_feas import audit_elo as A
    from elo_feas import data_layer as D
    from elo_feas import elo as E
    from elo_feas import metrics as M

    def forbidden(*_a, **_k):
        raise RuntimeError("forbidden in this audit (would read 2025)")
    A.evaluate_2025 = forbidden
    A.games_2022_2025 = forbidden
    _build_all = D.build_all

    def build_all_selection_only(seasons=D.SEASONS, *a, **k):
        if max(seasons) > 2024:
            raise RuntimeError(f"audit: build_all asked for seasons {tuple(seasons)}; only <= 2024 allowed")
        return _build_all(seasons, *a, **k)
    D.build_all = build_all_selection_only

    rng = np.random.default_rng(SEED)
    checks, recorded, F = [], [], {}

    def chk(cid, name, ok, detail):
        checks.append({"id": cid, "check": name, "status": ok if isinstance(ok, str) else ("PASS" if ok else "FAIL"),
                       "detail": detail})

    def rec(item, recorded_v, recomputed_v, tol):
        if isinstance(recorded_v, (int, float)) and isinstance(recomputed_v, (int, float)):
            ok = abs(float(recorded_v) - float(recomputed_v)) <= tol
        else:
            ok = recorded_v == recomputed_v
        recorded.append({"item": item, "recorded": recorded_v, "recomputed": recomputed_v, "tolerance": tol,
                         "match": "MATCH" if ok else "MISMATCH"})

    def save(df, name):
        df.assign(label=DIAG).to_csv(AUDIT_OUT / name, index=False, lineterminator="\n")
        F[name] = df

    # ---- inputs: elo_feas/out only (plus selection_games for 2022-2024 recomputation)
    pred = pd.read_csv(OUT / "predictions.csv", dtype={"date": str, "game_key": str, "team_a": str, "team_b": str},
                       keep_default_na=False)
    grid = pd.read_csv(OUT / "grid_2024.csv")
    sens = pd.read_csv(OUT / "sensitivity_h_2024.csv")
    mbs = pd.read_csv(OUT / "metrics_by_season.csv")
    rel_csv = {2024: pd.read_csv(OUT / "reliability_2024.csv"), 2025: pd.read_csv(OUT / "reliability_2025.csv")}
    canon = pd.read_csv(OUT / "canonical_games_2022_2026.csv", dtype=str, keep_default_na=False)
    canon = canon[canon["season"].astype(int) <= 2025].reset_index(drop=True)      # 2026 rows dropped unexamined
    frozen = json.loads((OUT / "frozen_params.json").read_text(encoding="utf-8"))
    marker = json.loads((OUT / "test_2025_run.json").read_text(encoding="utf-8"))
    sel = A.selection_games()
    assert int(sel["season"].max()) <= 2024, "selection_games returned a season after 2024"
    eng = E.from_canonical(sel)
    assert int(eng["season"].max()) <= 2024

    # ---- I: integrity / provenance
    chk("I1", "frozen params_sha256 recomputes", A._params_sha256(frozen) == frozen["params_sha256"],
        f"params_sha256={frozen['params_sha256']}")
    chk("I2", "frozen K/lam/h are the values audited", (frozen["K"], frozen["lam"], frozen["h"]) ==
        (FROZEN_K, FROZEN_LAM, FROZEN_H), f"K={frozen['K']} lam={frozen['lam']} h={frozen['h']}")
    tsel = A.table_sha256(sel)
    chk("I3", "table_sha256(selection_games()) == frozen table_sha256", tsel == frozen["table_sha256"],
        f"max season {int(sel['season'].max())}; {tsel}")
    c24 = canon[canon["season"].astype(int) <= 2024][D.GAME_COLUMNS].sort_values(["date", "game_key"],
                                                                               kind="mergesort")
    s_rt = pd.read_csv(io.StringIO(sel[D.GAME_COLUMNS].sort_values(["date", "game_key"], kind="mergesort")
                                   .to_csv(index=False)), dtype=str, keep_default_na=False)
    same_c = c24.reset_index(drop=True).equals(s_rt.reset_index(drop=True))
    chk("I4", "canonical_games_2022_2026.csv rows <= 2024 identical to selection_games()", same_c,
        f"rows {len(c24)} vs {len(s_rt)}")
    for col in ("result", "Result"):
        chk("I5" + col[0], f"no '{col}' column in predictions.csv / canonical csv",
            col not in pred.columns and col not in canon.columns, "columns: " + ",".join(pred.columns))
    ref = E.run_elo(eng, FROZEN_K, FROZEN_LAM, FROZEN_H)
    p_sel = pred[pred["season"] <= 2024].reset_index(drop=True)
    rp = ref.predictions.reset_index(drop=True)
    key_same = p_sel[["season", "date", "game_key", "team_a", "team_b", "y_a", "sidedness", "v"]].astype(str) \
        .equals(rp[["season", "date", "game_key", "team_a", "team_b", "y_a", "sidedness", "v"]].astype(str))
    dmax = float(np.max(np.abs(p_sel[["R_a_before", "R_b_before", "p_a"]].to_numpy() -
                               rp[["R_a_before", "R_b_before", "p_a"]].to_numpy())))
    chk("I6", "predictions.csv 2022-2024 rows == fresh run on selection_games() (K=40, lam=1, h=0)",
        key_same and dmax < 1e-9, f"keys/outcomes identical={key_same}; max |diff| R/p = {dmax:.3g}")
    ck = canon.copy()
    ck["y"] = (ck["goals_lo"].astype(float) > ck["goals_hi"].astype(float)).astype(int)
    ck["v"] = [E.venue_sign(a, b) for a, b in zip(ck["flag_lo"], ck["flag_hi"])]
    m = pred.merge(ck[["game_key", "season", "team_lo", "team_hi", "y", "v", "sidedness"]], on="game_key",
                   how="outer", suffixes=("", "_c"), indicator=True)
    both = m["_merge"] == "both"
    ok = bool(both.all()) and (m["y_a"] == m["y"]).all() and (m["v"] == m["v_c"]).all() and \
        (m["team_a"] == m["team_lo"]).all() and (m["sidedness"] == m["sidedness_c"]).all()
    chk("I7", "predictions.csv 2022-2025 games, y_a (from goals), v, sidedness agree with canonical csv", bool(ok),
        f"pred rows {len(pred)}, canonical<=2025 rows {len(ck)}, unmatched {int((~both).sum())}")
    pmax = float(np.max(np.abs(pred["p_a"] - 1 / (1 + 10 ** (-(pred["R_a_before"] - pred["R_b_before"]) / 400)))))
    chk("I8", "p_a == E(R_a_before, R_b_before) with h=0 on every row (incl. 2025)", pmax < 1e-12,
        f"max |diff| = {pmax:.3g}")
    long = pd.concat([pred[["date", "team_a"]].rename(columns={"team_a": "t"}),
                      pred[["date", "team_b"]].rename(columns={"team_b": "t"})])
    dup = int(long.duplicated().sum())
    chk("I9", "no team appears twice on one date", dup == 0, f"duplicates {dup}")
    p25 = pred[pred["season"] == 2025]
    first25 = pd.concat([p25[["date", "game_key", "team_a", "R_a_before"]].set_axis(["date", "k", "t", "r"], axis=1),
                         p25[["date", "game_key", "team_b", "R_b_before"]].set_axis(["date", "k", "t", "r"], axis=1)]) \
        .sort_values(["date", "k"], kind="mergesort").drop_duplicates("t")
    end24 = ref.ratings
    ret = first25[first25["t"].isin(end24)]
    new = first25[~first25["t"].isin(end24)]
    cont = float(np.max(np.abs(ret["r"].to_numpy() -
                               np.array([E.shrink(end24[t], FROZEN_LAM) for t in ret["t"]]))))
    chk("I10", "first 2025 pre-game rating == shrink(end-2024 rating from selection run); 2025 newcomers at 1500",
        cont < 1e-9 and bool((new["r"] == 1500.0).all()),
        f"returning teams {len(ret)}, max |diff| {cont:.3g}; newcomers {sorted(new['t'])}")
    fz_t, mk_t = frozen["frozen_at_utc"], marker.get("completed_at_utc", "")
    mt = {n: os.stat(OUT / n).st_mtime for n in ("frozen_params.json", "grid_2024.csv", "predictions.csv",
                                                 "reliability_2025.csv", "test_2025_run.json")}
    order_ok = mt["frozen_params.json"] <= mt["predictions.csv"] <= mt["test_2025_run.json"]
    chk("I11", "frozen_at_utc < marker completed_at_utc; mtime(frozen) <= mtime(predictions) <= mtime(marker)",
        fz_t < mk_t and order_ok, f"frozen_at {fz_t}; marker completed {mk_t}; mtime order ok={order_ok}")
    chk("I12", "2022 is warm-up: no 2022 rows in metrics_by_season.csv, no 2022 metric columns in grid",
        2022 not in set(mbs["season"]) and not any("2022" in c for c in grid.columns), "")
    chk("I13", "marker status complete with 409 games; params_sha256 matches frozen",
        marker.get("status") == "complete" and marker.get("n_2025_games") == len(p25) == 409 and
        marker.get("params_sha256") == frozen["params_sha256"], f"n_2025 in predictions.csv {len(p25)}")
    chk("I14", "one update per game overall (2022-2025 game count == recorded updates)",
        len(pred) == RECORDED["n_updates"], f"{len(pred)} games")

    # ---- R: recompute everything recorded and compare
    grid_re = A.run_grid(sel)
    num = [c for c in grid_re.columns if c != "rank"]
    g_csv = grid.sort_values(["K", "lam"]).reset_index(drop=True)
    g_re = grid_re.sort_values(["K", "lam"]).reset_index(drop=True)
    gmax = float(np.max(np.abs(g_csv[num].to_numpy(float) - g_re[num].to_numpy(float))))
    chk("R1", "grid recomputed from selection_games() == grid_2024.csv", gmax < 1e-12 and
        (g_csv["rank"] == g_re["rank"]).all(), f"max |diff| {gmax:.3g}; ranks identical")
    best = A.select_best(grid_re)
    rec("selected K", RECORDED["selected"][0], best["K"], 0)
    rec("selected lam", RECORDED["selected"][1], best["lam"], 0)
    rec("Brier 2024 selected", RECORDED["brier_2024_sel"], round(best["brier_2024"], 6), 5e-6)
    rec("log loss 2024 selected", RECORDED["log_loss_2024_sel"], round(best["log_loss_2024"], 6), 5e-6)
    rec("Brier 2024 grid min", RECORDED["brier_range"][0], round(float(grid_re["brier_2024"].min()), 6), 5e-6)
    rec("Brier 2024 grid max", RECORDED["brier_range"][1], round(float(grid_re["brier_2024"].max()), 6), 5e-6)
    sens_re = A.sensitivity_h(sel, FROZEN_K, FROZEN_LAM)
    sc = ["n_2024", "brier_2024", "log_loss_2024", "brier_2024_two_sided", "log_loss_2024_two_sided"]
    smax = float(np.max(np.abs(sens[sc].to_numpy(float) - sens_re[sc].to_numpy(float))))
    chk("R2", "h sensitivity recomputed == sensitivity_h_2024.csv", smax < 1e-12, f"max |diff| {smax:.3g}")
    for h, v in RECORDED["sens"].items():
        rec(f"sensitivity Brier 2024 h={h}", v, round(float(sens_re.loc[sens_re["h"] == h, "brier_2024"].iloc[0]), 6),
            5e-6)
    met = M.season_metrics(pred[pred["season"] >= 2023])
    mm = mbs.merge(met, on=["season", "subset"], suffixes=("", "_re"))
    mmax = float(np.max(np.abs(mm[["n", "brier", "log_loss"]].to_numpy(float) -
                               mm[["n_re", "brier_re", "log_loss_re"]].to_numpy(float))))
    chk("R3", "metrics recomputed from predictions.csv == metrics_by_season.csv", mmax < 1e-12 and len(mm) == 6,
        f"rows {len(mm)}; max |diff| {mmax:.3g}")
    for (s, sub), (n, b, ll) in RECORDED["metrics"].items():
        r = met[(met["season"] == s) & (met["subset"] == sub)].iloc[0]
        rec(f"{s} {sub} n", n, int(r["n"]), 0)
        rec(f"{s} {sub} Brier", b, round(float(r["brier"]), 6), 5e-5)
        rec(f"{s} {sub} log loss", ll, round(float(r["log_loss"]), 6), 5e-5)
    for s in (2024, 2025):
        d = pred[pred["season"] == s]
        t, nx = M.reliability_table(d["p_a"], d["y_a"])
        rmax = float(np.max(np.abs(t[["n", "mean_predicted", "observed_rate"]].to_numpy(float) -
                                   rel_csv[s][["n", "mean_predicted", "observed_rate"]].to_numpy(float))))
        chk(f"R4.{s}", f"reliability {s} recomputed from predictions.csv == reliability_{s}.csv", rmax < 1e-12,
            f"max |diff| {rmax:.3g}; p=0.5 excluded {nx}")
        rec(f"reliability {s} games with p=0.5 excluded", 0, nx, 0)
        for i, (mp, ob) in enumerate(zip(*RECORDED["rel"][s])):
            rec(f"reliability {s} bin {i + 1} mean predicted", mp, round(float(t["mean_predicted"].iloc[i]), 6), 5e-5)
            rec(f"reliability {s} bin {i + 1} observed", ob, round(float(t["observed_rate"].iloc[i]), 6), 5e-5)
    for s, (n, two, one) in RECORDED["games"].items():
        d = pred[pred["season"] == s]
        rec(f"games {s}", n, len(d), 0)
        rec(f"games {s} two-sided", two, int((d["sidedness"] == "TWO_SIDED").sum()), 0)
        rec(f"games {s} single-sided", one, int((d["sidedness"] == "SINGLE_SIDED").sum()), 0)
    longp = pd.concat([pred[["season", "date", "team_a"]].rename(columns={"team_a": "t"}),
                       pred[["season", "date", "team_b"]].rename(columns={"team_b": "t"})])
    first_season = longp.sort_values(["date"], kind="mergesort").drop_duplicates("t").groupby("season")["t"] \
        .apply(lambda x: sorted(x))
    for s, v in RECORDED["newcomers"].items():
        got = first_season.get(s, [])
        rec(f"newcomers {s}", v, len(got) if isinstance(v, int) else got, 0)

    def season_start(s):
        d = pred[pred["season"] == s]
        x = pd.concat([d[["date", "game_key", "team_a", "R_a_before"]].set_axis(["date", "k", "t", "r"], axis=1),
                       d[["date", "game_key", "team_b", "R_b_before"]].set_axis(["date", "k", "t", "r"], axis=1)])
        return x.sort_values(["date", "k"], kind="mergesort").drop_duplicates("t").set_index("t")["r"]
    st = pd.concat([season_start(2024).rename("start_2024"), season_start(2025).rename("start_2025")],
                   axis=1, join="inner")
    st["change"] = st["start_2025"] - st["start_2024"]
    rec("teams present at both 2024 and 2025 starts", RECORDED["n_both"], len(st), 0)
    up = st.sort_values(["change"], ascending=False, kind="mergesort").head(5)
    dn = st.sort_values(["change"], kind="mergesort").head(5)
    for lab, recl, got in (("up", RECORDED["up"], up), ("down", RECORDED["down"], dn)):
        for i, ((name, ch), (t, r)) in enumerate(zip(recl, got.iterrows())):
            rec(f"top {lab} #{i + 1} team", name, t, 0)
            rec(f"top {lab} #{i + 1} change", ch, round(float(r["change"]), 3), 0.05)
    # Chunk 4 definition (run_experiment.season_start_ratings): every team holding a rating at each season's first
    # date. With lam = 1 the 2025 start is the end-of-2024 rating, so this needs only 2022-2024 (selection run).
    ref_h = E.run_elo(eng, FROZEN_K, FROZEN_LAM, FROZEN_H, keep_history=True)
    d24 = eng.loc[eng["season"] == 2024, "date"].min()
    s24 = {t: E.shrink(r, FROZEN_LAM) for t, r in ref_h.history[max(d for d in ref_h.history if d < d24)].items()}
    s25 = {t: E.shrink(r, FROZEN_LAM) for t, r in ref_h.ratings.items()}
    common = sorted(set(s24) & set(s25))
    gcount = {s: pd.concat([pred.loc[pred["season"] == s, "team_a"], pred.loc[pred["season"] == s, "team_b"]])
              .value_counts() for s in (2024, 2025)}
    c4 = pd.DataFrame({"team": common, "start_2024": [s24[t] for t in common], "start_2025": [s25[t] for t in common]})
    c4["change"] = c4["start_2025"] - c4["start_2024"]
    c4["games_2024"] = [int(gcount[2024].get(t, 0)) for t in common]
    c4["games_2025"] = [int(gcount[2025].get(t, 0)) for t in common]
    rec("Chunk 4 definition: teams holding a rating at both starts", RECORDED["n_both"], len(c4), 0)
    up4 = c4.sort_values(["change", "team"], ascending=[False, True], kind="mergesort").head(5)
    dn4 = c4.sort_values(["change", "team"], ascending=[True, True], kind="mergesort").head(5)
    for lab, recl, got in (("up", RECORDED["up"], up4), ("down", RECORDED["down"], dn4)):
        for i, ((name, ch), r) in enumerate(zip(recl, got.itertuples())):
            rec(f"Chunk 4 definition: top {lab} #{i + 1} team", name, r.team, 0)
            rec(f"Chunk 4 definition: top {lab} #{i + 1} change", ch, round(float(r.change), 3), 0.05)
    played = set(st.index)
    mv = []
    for lab, df in (("top up, Chunk 4 definition", up4), ("top down, Chunk 4 definition", dn4)):
        for r in df.itertuples():
            mv.append({"list": lab, "team": r.team, "start_2024": r.start_2024, "start_2025": r.start_2025,
                       "change": r.change, "games_2024": r.games_2024, "games_2025": r.games_2025})
    for lab, df in (("top up, played both seasons", up), ("top down, played both seasons", dn)):
        for t, r in df.iterrows():
            mv.append({"list": lab, "team": t, "start_2024": r["start_2024"], "start_2025": r["start_2025"],
                       "change": r["change"], "games_2024": int(gcount[2024].get(t, 0)),
                       "games_2025": int(gcount[2025].get(t, 0))})
    for r in c4[~c4["team"].isin(played)].itertuples():
        mv.append({"list": "in Chunk 4 set but no game in 2024 or 2025", "team": r.team, "start_2024": r.start_2024,
                   "start_2025": r.start_2025, "change": r.change, "games_2024": r.games_2024,
                   "games_2025": r.games_2025})
    save(pd.DataFrame(mv), "rating_change_definitions.csv")
    sp = max(abs(s25[t] - ref.ratings[t]) for t in common)                  # shrink with lam = 1 is the identity
    chk("D0", "with lam = 1 the 2024->2025 start change equals the in-season 2024 change (no 2025 data involved)",
        "INFO", f"teams in Chunk 4 set {len(c4)}; teams with games in both seasons {len(st)}; "
                f"Chunk 4 set teams lacking a 2024 or 2025 game: {sorted(set(c4['team']) - played)}; "
                f"max |start_2025 - end-2024 rating| {sp:.3g}")
    rec("frozen table_sha256", RECORDED["table_sha256"], frozen["table_sha256"], 0)
    rec("frozen params_sha256", RECORDED["params_sha256"], frozen["params_sha256"], 0)
    save(pd.DataFrame(recorded).astype({"recorded": str, "recomputed": str}), "recorded_vs_recomputed.csv")

    # ---- D1: grid shape near the selected corner (no widening, no re-selection)
    piv = grid.pivot(index="K", columns="lam", values="brier_2024")
    mono_k = all(piv[c].is_monotonic_decreasing and piv[c].is_unique for c in piv.columns)
    mono_l = all(piv.loc[k].is_monotonic_decreasing and piv.loc[k].is_unique for k in piv.index)
    prof = []
    for lam in piv.columns:
        for k0, k1 in zip(piv.index[:-1], piv.index[1:]):
            prof.append({"axis": "K", "fixed": f"lam={lam}", "from": k0, "to": k1,
                         "brier_2024_from": piv.loc[k0, lam], "brier_2024_to": piv.loc[k1, lam],
                         "delta": piv.loc[k1, lam] - piv.loc[k0, lam]})
    for k in piv.index:
        for l0, l1 in zip(piv.columns[:-1], piv.columns[1:]):
            prof.append({"axis": "lam", "fixed": f"K={k}", "from": l0, "to": l1,
                         "brier_2024_from": piv.loc[k, l0], "brier_2024_to": piv.loc[k, l1],
                         "delta": piv.loc[k, l1] - piv.loc[k, l0]})
    save(pd.DataFrame(prof), "grid_profile_2024.csv")
    chk("D1", "2024 Brier strictly decreasing in K at every lam, and in lam at every K (recorded claim: monotone)",
        "INFO: " + ("monotone on both axes" if mono_k and mono_l else f"monotone in K={mono_k}, in lam={mono_l}"),
        f"last step K 30->40 at lam=1: {piv.loc[40, 1.0] - piv.loc[30, 1.0]:+.5f}; "
        f"last step lam 0.85->1.0 at K=40: {piv.loc[40, 1.0] - piv.loc[40, 0.85]:+.5f}")

    # ---- D2: which cell other criteria would pick (diagnostic only)
    crit = ["brier_2024", "log_loss_2024", "brier_2024_two_sided", "log_loss_2024_two_sided", "brier_2023",
            "log_loss_2023"]
    stab = []
    for c in crit:
        r = grid.sort_values([c, "K", "lam"], kind="mergesort").iloc[0]
        stab.append({"criterion": c, "argmin_K": r["K"], "argmin_lam": r["lam"], "value": r[c],
                     "same_as_frozen": (r["K"], r["lam"]) == (FROZEN_K, FROZEN_LAM)})
    save(pd.DataFrame(stab), "selection_stability.csv")

    # ---- D3: paired bootstrap (game-level resampling; games are not independent, so this is a rough guide)
    def boot(loss_diff, B=N_BOOT):
        idx = rng.integers(0, len(loss_diff), size=(B, len(loss_diff)))
        bs = loss_diff[idx].mean(axis=1)
        return float(loss_diff.mean()), float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))
    bt = []
    for s in (2023, 2024, 2025):
        for sub in ("ALL", "TWO_SIDED"):
            d = M.subset(pred[pred["season"] == s], sub)
            p, y = d["p_a"].to_numpy(float), d["y_a"].to_numpy(float)
            mean, lo, hi = boot((p - y) ** 2 - (0.5 - y) ** 2)
            bt.append({"comparison": "Elo(K=40,lam=1) minus constant 0.5, Brier", "season": s, "subset": sub,
                       "n": len(d), "mean_diff": mean, "ci95_lo": lo, "ci95_hi": hi,
                       "note": "in-sample for K/lam" if s <= 2024 else "out of sample (predictions.csv, not rerun)"})
    p_best = ref.predictions[ref.predictions["season"] == 2024]
    for k, lam in ((40.0, 0.85), (30.0, 1.0), (40.0, 0.7), (30.0, 0.85)):
        alt = E.run_elo(eng, k, lam, 0.0).predictions
        alt = alt[alt["season"] == 2024]
        assert (alt["game_key"].to_numpy() == p_best["game_key"].to_numpy()).all()
        y = p_best["y_a"].to_numpy(float)
        mean, lo, hi = boot((p_best["p_a"].to_numpy() - y) ** 2 - (alt["p_a"].to_numpy() - y) ** 2)
        bt.append({"comparison": f"K=40,lam=1 minus K={k:g},lam={lam:g}, Brier", "season": 2024, "subset": "ALL",
                   "n": len(y), "mean_diff": mean, "ci95_lo": lo, "ci95_hi": hi,
                   "note": "selection season (in-sample); recomputed on selection_games()"})
    save(pd.DataFrame(bt), "bootstrap_brier_diffs.csv")

    # ---- D4: calibration (slope with no intercept is invariant to which team is team_a)
    def slope_no_intercept(p, y):
        x = np.log(p / (1 - p))
        b = 1.0
        for _ in range(50):
            w = 1 / (1 + np.exp(-b * x))
            g, hss = float(np.sum(x * (y - w))), float(np.sum(x * x * w * (1 - w)))
            step = g / hss
            b += step
            if abs(step) < 1e-12:
                break
        return b
    cal = []
    for s in (2023, 2024, 2025):
        d = pred[(pred["season"] == s) & (pred["p_a"] != 0.5)]
        p, y = d["p_a"].to_numpy(float), d["y_a"].to_numpy(float)
        pf, yf = np.where(p > 0.5, p, 1 - p), np.where(p > 0.5, y, 1 - y)
        b = slope_no_intercept(p, y)
        idx = rng.integers(0, len(p), size=(500, len(p)))
        bs = np.array([slope_no_intercept(p[i], y[i]) for i in idx])
        cal.append({"season": s, "n": len(d), "mean_p_fav": pf.mean(), "favourite_win_rate": yf.mean(),
                    "fav_rate_minus_mean_p": yf.mean() - pf.mean(), "calibration_slope": b,
                    "slope_ci95_lo": float(np.quantile(bs, 0.025)), "slope_ci95_hi": float(np.quantile(bs, 0.975)),
                    "reading": "slope > 1: forecasts too close to 0.5; slope < 1: too extreme"})
    save(pd.DataFrame(cal), "calibration.csv")

    # ---- D5: single-sided games separately
    sub_rows = []
    for s in (2023, 2024, 2025):
        d = pred[pred["season"] == s]
        for name, x in (("ALL", d), ("TWO_SIDED", d[d["sidedness"] == "TWO_SIDED"]),
                        ("SINGLE_SIDED", d[d["sidedness"] == "SINGLE_SIDED"])):
            if len(x):
                sub_rows.append({"season": s, "subset": name, "n": len(x), "share": len(x) / len(d),
                                 "brier": M.brier(x["p_a"], x["y_a"]), "log_loss": M.log_loss(x["p_a"], x["y_a"]),
                                 "mean_abs_rating_gap": float(np.mean(np.abs(x["R_a_before"] - x["R_b_before"])))})
    save(pd.DataFrame(sub_rows), "subset_metrics.csv")

    # ---- D6: how much history each game's teams had (counts from predictions.csv order, no Elo recomputation)
    cnt, prior = {}, []
    for date, day in pred.sort_values(["date", "game_key"], kind="mergesort").groupby("date", sort=True):
        prior.append(pd.DataFrame({"game_key": day["game_key"].to_numpy(),
                                   "min_prior": [min(cnt.get(a, 0), cnt.get(b, 0))
                                                 for a, b in zip(day["team_a"], day["team_b"])]}))
        for t in list(day["team_a"]) + list(day["team_b"]):
            cnt[t] = cnt.get(t, 0) + 1
    pr = pred.merge(pd.concat(prior), on="game_key")
    pr["bucket"] = pd.cut(pr["min_prior"], [-1, 4, 9, 19, 10 ** 6], labels=["0-4", "5-9", "10-19", "20+"])
    exp_rows = []
    for s in (2023, 2024, 2025):
        for bk in ["0-4", "5-9", "10-19", "20+"]:
            x = pr[(pr["season"] == s) & (pr["bucket"] == bk)]
            if len(x):
                exp_rows.append({"season": s, "min_prior_games_bucket": bk, "n": len(x),
                                 "brier": M.brier(x["p_a"], x["y_a"]),
                                 "single_sided_in_bucket": int((x["sidedness"] == "SINGLE_SIDED").sum())})
    save(pd.DataFrame(exp_rows), "experience_buckets.csv")

    # ---- D7: venue flags as recorded in the canonical table (results file flags; never the odds feed)
    ck["pair"] = ck["flag_lo"].replace("", "-") + "/" + ck["flag_hi"].replace("", "-")
    ven = ck.groupby(["season", "sidedness", "pair"]).size().rename("n").reset_index()
    ven["share_of_season"] = ven["n"] / ven.groupby("season")["n"].transform("sum")
    save(ven, "venue_flag_pairs.csv")

    # ---- D8: calendar coverage per season
    pd_ = pred.assign(month=pred["date"].str[5:7])
    cov = pd_.groupby(["season", "month"]).size().rename("games").reset_index()
    rng_rows = pred.groupby("season")["date"].agg(["min", "max", "nunique"]).reset_index() \
        .rename(columns={"min": "first_date", "max": "last_date", "nunique": "n_dates"})
    save(cov.merge(rng_rows, on="season"), "calendar_coverage.csv")

    save(pd.DataFrame(checks), "integrity_checks.csv")
    return {"checks": checks, "recorded": recorded, "frames": F, "mono": (mono_k, mono_l), "piv": piv}


def write_report(f: dict, selftest, diff, n_hashed) -> None:
    import pandas as pd

    def md(df, floatfmt="{:.5f}"):
        cols = [c for c in df.columns if c != "label"]
        out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for r in df[cols].itertuples(index=False):
            out.append("| " + " | ".join(floatfmt.format(v) if isinstance(v, float) else str(v) for v in r) + " |")
        return "\n".join(out)
    rec = pd.DataFrame(f["recorded"])
    mis = rec[rec["match"] == "MISMATCH"]
    chk = pd.DataFrame(f["checks"])
    F = f["frames"]
    L = ["# Methodological audit of the Elo feasibility experiment", "",
         f"**{DIAG}.** Read-only audit of `elo_feas/out/`. Nothing here changes K = 40, lam = 1.0, h = 0, widens "
         "the grid, or reruns the 2025 test. Not a model comparison and not a result. The underlying experiment "
         "remains: feasibility experiment — pipeline check, not a research result.", "",
         "## Scope and guards",
         f"- Hash check over {n_hashed} files before and after: "
         f"{'NO DIFFERENCES' if not diff else 'DIFFERENCES - STOP'} (hash_diff.txt).",
         "- Guard self-test: " + "; ".join(f"{n}: {r}" for n, r in selftest),
         "- 2025 information comes only from `elo_feas/out/predictions.csv`, `canonical_games_2022_2026.csv` "
         "(2026 rows dropped on load), `reliability_2025.csv`, `metrics_by_season.csv` and the two JSON files in "
         "`elo_feas/out/`. Elo is recomputed only on `selection_games()` (raw 2022-2024 files, max season "
         "asserted <= 2024). `evaluate_2025` and `games_2022_2025` were replaced by raisers in memory.",
         "- Every path opened through Python file APIs is in files_opened.txt. Module imports go through the "
         "import system and are not logged there; their sources are covered by the hash check.", "",
         "## Recorded vs recomputed",
         f"{len(rec)} recorded numbers compared; **{len(mis)} mismatches**.", ""]
    L += [md(mis), ""] if len(mis) else ["All recorded numbers reproduce at their reported precision "
                                         "(recorded_vs_recomputed.csv).", ""]
    L += ["## Integrity checks", md(chk), "",
          "## D0 Rating-change lists under both definitions", md(F["rating_change_definitions.csv"], "{:.1f}"), "",
          "## D1 Grid shape near the selected corner (2024 Brier; no widening)", md(F["grid_profile_2024.csv"]
                                                                                   [lambda d: (d["fixed"] == "lam=1.0")
                                                                                    | (d["fixed"] == "K=40")]), "",
          "## D2 Cell each criterion would pick (diagnostic only; selection stays K=40, lam=1)",
          md(F["selection_stability.csv"]), "",
          f"## D3 Paired bootstrap of Brier differences ({N_BOOT} game-level resamples, seed {SEED})",
          "Games share teams and dates, so game-level resampling understates uncertainty; read as rough.",
          md(F["bootstrap_brier_diffs.csv"]), "",
          "## D4 Calibration (no-intercept logistic slope on logit p; 500 resamples for the interval)",
          md(F["calibration.csv"]), "",
          "## D5 Single-sided games", md(F["subset_metrics.csv"]), "",
          "## D6 Brier by the smaller prior-game count of the two teams (all seasons counted from 2022)",
          md(F["experience_buckets.csv"]), "",
          "## D7 Home/Away flag pairs in the canonical table (results-file flags)", md(F["venue_flag_pairs.csv"]), "",
          "## D8 Calendar coverage", md(F["calendar_coverage.csv"]), "",
          f"_{DIAG}_", ""]
    (AUDIT_OUT / "audit_report.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        with _orig_open(AUDIT_OUT / "files_opened.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# how\tR/W\tpath  (every open through builtins.open/io.open/pathlib/os.open, in order; "
                     "this file itself is written with the unwrapped open)\n")
            fh.write("\n".join(OPENED) + "\n")
    sys.exit(code)
