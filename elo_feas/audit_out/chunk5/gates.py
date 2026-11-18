"""Chunk 5, script 1: pre-comparison gates G1-G9 (no loss of any kind). Rerun under Amendment A1 (spec v2.1.1).

Diagnostic, no re-selection. Writes only to elo_feas/audit_out/chunk5/ (rerun outputs carry the suffix _rerun where a
first-run name exists). Computes no Brier, no log loss and no Elo-vs-market quantity. Imports nothing from the
elo_feas package (the path guard comes from ../methodology_audit.py, loaded as the top-level module
`methodology_audit`; it imports no experiment code). Never loads home_team, the odds `result` column or the results
file's `result` column (usecols everywhere); never opens the raw 2025/2026 results files.
Writes gates_rerun.md always; gates_passed.json only if every gate passes, otherwise gates_failed_rerun.md.
Run:  $env:PYTHONDONTWRITEBYTECODE=1; .\\.venv\\Scripts\\python.exe -B elo_feas\\audit_out\\chunk5\\gates.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import pathlib  # noqa: E402

CH5 = pathlib.Path(__file__).resolve().parent
AUD = CH5.parent
CH2, CH3, CH4 = AUD / "chunk2", AUD / "chunk3", AUD / "chunk4"
sys.path.insert(0, str(AUD))

import methodology_audit as G  # noqa: E402  (installs the open/write guards on import)

G.AUDIT_OUT = CH5

import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import socket  # noqa: E402
from datetime import date as Date, datetime, timedelta, timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT, OUT, DIAG = G.ROOT, G.OUT, G.DIAG
RUN2 = ROOT / "audit_join" / "out_run2"
PILOT = ROOT / "pilot"
ET, UTC = ZoneInfo("America/New_York"), timezone.utc
BOOKS = ("betmgm", "draftkings", "fanduel")
SPEC = CH5 / "market_comparison_spec_v2.1.1.md"
SPEC_SHA = "9d7f7b8abb6a41aa2278ab68434b22b473747af177cc8b0a4a997e1c46481221"
SPEC_BYTES = 10450
TOL_STORED = 5e-7                           # Amendment A1: stored (6-decimal) probabilities and holds

INPUTS = {
    "predictions.csv": OUT / "predictions.csv",
    "canonical_games_2022_2026.csv": OUT / "canonical_games_2022_2026.csv",
    "frozen_params.json": OUT / "frozen_params.json",
    "test_2025_run.json": OUT / "test_2025_run.json",
    "event_game_links.csv": RUN2 / "event_game_links.csv",
    "team_keyed_moneyline.csv": RUN2 / "team_keyed_moneyline.csv",
    "timing_events.csv": CH4 / "timing_events.csv",
    "metrics.py (epsilon source)": ROOT / "elo_feas" / "metrics.py",
    "market_comparison_spec_v2.1.1.md": SPEC,
    "gates.py": CH5 / "gates.py",
    "compare.py": CH5 / "compare.py",
}


def produced_by_this_run(p: pathlib.Path) -> bool:
    """Files this rerun (gates.py or compare.py) writes; excluded from the before/after hash set."""
    n = p.name
    return ("_rerun" in n or n == "gates_passed.json" or n.startswith("compare_report") or
            n.startswith("files_opened_compare") or n.startswith("hash_diff_compare") or
            n.startswith("hashes_compare") or (n.startswith("R") and n.endswith(".csv")) or "__pycache__" in p.parts)


# Reads made by me through tools (not Python) for this rerun; listed by hand (they bypass the guard).
SHELL_READS = [
    "tool Read (failed, file did not exist yet): elo_feas/audit_out/chunk5/market_comparison_spec_v2.1.1.md",
    "tool Glob: **/*spec* over the repository (file names only)",
    "shell PowerShell Get-FileHash + Length: chunk5/market_comparison_spec_v2.1.1.md (first check: CRLF version, "
    "2daced8f..., 10508 bytes; hash only)",
    "shell PowerShell ReadAllBytes: chunk5/market_comparison_spec_v2.1.1.md (counted LF/CRLF/BOM and hashed an "
    "in-memory LF copy; no text printed)",
    "shell PowerShell Get-FileHash + Length: chunk5/market_comparison_spec_v2.1.1.md (second check after the user "
    "replaced it: 9d7f7b8a..., 10450 bytes)",
    "tool Read: chunk5/market_comparison_spec_v2.1.1.md (whole file)",
    "(first-run tool/shell reads are listed in chunk5/files_opened.txt)",
]


def sha(p: pathlib.Path) -> str:
    return G.sha256_file(p)


def md(df: pd.DataFrame, fmt: str = "{:.4g}") -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(
            ("" if v != v else fmt.format(v)) if isinstance(v, (float, np.floating)) else str(v) for v in r) + " |")
    return "\n".join(out)


def _blocked(*_a, **_k):
    raise RuntimeError("network blocked in this audit")


def install_socket_guard() -> None:
    for obj, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
                      (socket, "getaddrinfo")):
        setattr(obj, name, _blocked)


def hash_targets() -> list[pathlib.Path]:
    files = G.hash_targets()
    files += sorted((p for p in AUD.iterdir() if p.is_file()), key=lambda p: p.as_posix())
    for d in (CH2, CH3, CH4):
        files += sorted((p for p in d.rglob("*") if p.is_file()), key=lambda p: p.as_posix())
    files += sorted((p for p in CH5.rglob("*") if p.is_file() and not produced_by_this_run(p)),
                    key=lambda p: p.as_posix())
    return files


def snapshot() -> dict:
    return {G._rel(p): (p.stat().st_size, sha(p)) for p in hash_targets()}


def implied(price: float) -> float:
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def et_utc(d: Date, hour: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=ET).astimezone(UTC)


def selected_rows_hash(pred2025: pd.DataFrame, keys) -> tuple[str, int]:
    sel = pred2025[pred2025.game_key.isin(set(keys))].sort_values("game_key")
    txt = sel[["season", "date", "game_key", "team_a", "team_b", "p_a", "y_a"]].to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(txt.encode("utf-8")).hexdigest(), len(sel)


def read_pred2025() -> pd.DataFrame:
    pred = pd.read_csv(INPUTS["predictions.csv"], dtype=str, keep_default_na=False,
                       usecols=["season", "date", "game_key", "team_a", "team_b", "p_a", "y_a"])
    return pred[pred.season == "2025"].copy()


def q(x) -> dict:
    x = np.asarray(list(x), float)
    if not len(x):
        return {}
    return {"min": float(x.min()), "q25": float(np.quantile(x, .25)), "median": float(np.median(x)),
            "q75": float(np.quantile(x, .75)), "max": float(x.max())}


def main() -> int:
    install_socket_guard()
    if sha(SPEC) != SPEC_SHA or SPEC.stat().st_size != SPEC_BYTES:
        (CH5 / "gates_failed_rerun.md").write_text(
            f"# Chunk 5 rerun stopped\n\nSpec sha256 {sha(SPEC)} / {SPEC.stat().st_size} bytes differs from the "
            f"expected {SPEC_SHA} / {SPEC_BYTES}. No gate run, no loss computed.\n", encoding="utf-8")
        return 4
    selftest = G.guard_selftest()
    for label, d in (("write to audit_out outside chunk5", AUD), ("write to chunk4 from chunk5", CH4)):
        try:
            open(d / "__no_such_dir__" / "x.txt", "w")
            selftest.append((label, "FAIL: no exception"))
        except PermissionError:
            selftest.append((label, "PASS"))
        except Exception as e:  # noqa: BLE001
            selftest.append((label, f"FAIL: {type(e).__name__}"))
    try:
        socket.create_connection(("127.0.0.1", 9))
        selftest.append(("network call", "FAIL: no exception"))
    except RuntimeError:
        selftest.append(("network call", "PASS"))
    if any(r != "PASS" for _, r in selftest):
        raise RuntimeError(f"guard self-test failed: {selftest}")

    before = snapshot()
    G.write_snapshot(before, "hashes_before_rerun.csv")
    prev = {}
    for line in (CH5 / "hashes_after.csv").read_text(encoding="utf-8").splitlines()[1:]:
        p, _, h = line.split(",")
        prev[p] = h
    ref_prev = G.compare_reference(before, prev)
    ref_prot = G.compare_reference(before, G.read_reference(ROOT / "audit_join" / "protected_hashes.txt"))

    res = run_gates(ref_prev, ref_prot, len(prev))

    after = snapshot()
    G.write_snapshot(after, "hashes_after_rerun.csv")
    diff = [f"{'CHANGED' if k in before and k in after else ('ADDED' if k in after else 'REMOVED')} {k}"
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    L = [f"files hashed: {len(before)} before, {len(after)} after (previous audit set + audit_out top-level + "
         "chunk2/ + chunk3/ + chunk4/ files + chunk5/ files not written by this rerun)",
         f"before vs after: {'NO DIFFERENCES' if not diff else str(len(diff)) + ' DIFFERENCES - STOP'}", *diff,
         f"before vs Chunk 5 first-run hashes_after.csv ({len(prev)} entries): "
         f"{'ALL MATCH' if not ref_prev else str(len(ref_prev)) + ' MISMATCHES'}", *ref_prev,
         f"before vs audit_join/protected_hashes.txt (10 entries): "
         f"{'ALL MATCH' if not ref_prot else str(len(ref_prot)) + ' MISMATCHES'}", *ref_prot,
         "guard self-test: " + "; ".join(f"{n}={r}" for n, r in selftest)]
    (CH5 / "hash_diff_rerun.txt").write_text("\n".join(L) + "\n", encoding="utf-8")

    all_pass = res["all_pass"] and not diff
    text = res["md"].replace("@@HASH@@", f"Hash check over {len(before)} files before/after this script: "
                                         f"{'NO DIFFERENCES' if not diff else 'DIFFERENCES'} (hash_diff_rerun.txt). "
                                         "Guard self-test: " + "; ".join(f"{n}: {r}" for n, r in selftest) + ".")
    (CH5 / "gates_rerun.md").write_text(text, encoding="utf-8")
    if all_pass:
        (CH5 / "gates_passed.json").write_text(json.dumps(res["passed"], indent=2, sort_keys=True) + "\n",
                                               encoding="utf-8")
    else:
        (CH5 / "gates_failed_rerun.md").write_text(res["failed_md"], encoding="utf-8")
    return 0 if all_pass else 3


def run_gates(ref_prev: list, ref_prot: list, n_prev: int) -> dict:
    R: dict[str, dict] = {}            # gate -> {"pass": bool, "lines": [...]}

    def gate(name, ok, lines):
        R[name] = {"pass": bool(ok), "lines": lines}

    # ------------------------------------------------------------------ inputs (usecols only)
    pred = read_pred2025()
    canon = pd.read_csv(INPUTS["canonical_games_2022_2026.csv"], dtype=str, keep_default_na=False,
                        usecols=["season", "date", "game_key", "team_lo", "team_hi", "goals_lo", "goals_hi"])
    links = pd.read_csv(INPUTS["event_game_links.csv"], dtype=str, keep_default_na=False,
                        usecols=["event_id", "season", "PRIMARY", "matched_game_key", "matched_date",
                                 "n_books_h2h_any"])
    with open(INPUTS["team_keyed_moneyline.csv"], encoding="utf-8") as fh:
        ml_cols = fh.readline().strip().split(",")
    ml = pd.read_csv(INPUTS["team_keyed_moneyline.csv"], dtype=str, keep_default_na=False,
                     usecols=["event_id", "source", "team_a", "team_b", "price_team_a", "price_team_b", "p_team_a",
                              "p_team_b", "hold", "step", "snapshot_timestamp", "PRIMARY"])
    tev = pd.read_csv(INPUTS["timing_events.csv"], dtype=str, keep_default_na=False)

    prim = links[(links.PRIMARY == "True") & (links.season == "2025")].copy()
    prim["key"] = prim.matched_game_key.str.split("|", n=1).str[1]
    prim = prim.sort_values(["matched_date", "key"]).reset_index(drop=True)
    cm = canon.set_index("game_key")
    mlb = ml[(ml.source != "consensus") & ml.event_id.isin(prim.event_id)].copy()
    sel_hash_before, n_sel = selected_rows_hash(pred, prim.key)
    pkey_hashes_before = {k: sha(INPUTS[k]) for k in ("predictions.csv", "frozen_params.json", "test_2025_run.json")}

    # ------------------------------------------------------------------ G1
    n_events = len(prim)
    one_pred = 0
    name_mismatch = 0
    rows_per_pair_bad = 0
    pairs = []
    for r in prim.itertuples():
        pm = pred[pred.game_key == r.key]
        if len(pm) == 1 and (pm.iloc[0].team_a, pm.iloc[0].team_b) == (cm.loc[r.key].team_lo, cm.loc[r.key].team_hi):
            one_pred += 1
        lo, hi = cm.loc[r.key].team_lo, cm.loc[r.key].team_hi
        m = mlb[mlb.event_id == r.event_id]
        for b, mb in m.groupby("source"):
            if len(mb) != 1:
                rows_per_pair_bad += 1
            x = mb.iloc[-1]
            if {x.team_a, x.team_b} != {lo, hi}:
                name_mismatch += 1
                continue
            a_first = x.team_a == lo
            pairs.append({"event_id": r.event_id, "game_key": r.key, "book": b, "a_is_row_team_a": a_first,
                          "price_A": float(x.price_team_a if a_first else x.price_team_b),
                          "price_B": float(x.price_team_b if a_first else x.price_team_a),
                          "stored_p_A": x.p_team_a if a_first else x.p_team_b,
                          "stored_p_B": x.p_team_b if a_first else x.p_team_a,
                          "stored_hold": x.hold, "snapshot": x.snapshot_timestamp, "step": x.step})
    pairs = pd.DataFrame(pairs)
    swapped = int((~pairs.a_is_row_team_a).sum()) if len(pairs) else 0
    gate("G1", n_events == 57 and one_pred == 57 and name_mismatch == 0 and rows_per_pair_bad == 0,
         [f"PRIMARY 2025 events: {n_events} (expected 57).",
          f"Events mapping to exactly one 2025 predictions.csv row by game_key (matched_game_key without 'season|'), "
          f"with team_a/team_b equal to canonical team_lo/team_hi: {one_pred} of {n_events}.",
          f"(event, book) market rows: {len(pairs) + name_mismatch}; rows whose two team names are not the two canonical "
          f"teams: {name_mismatch}; (event, book) pairs with other than exactly one row: {rows_per_pair_bad}.",
          f"Reference team = canonical team_a (team_lo). Market rows whose row team_a is the canonical team_b (sides "
          f"swapped to orient on team_a): {swapped}. Elo p_A = predictions.csv p_a (read, not recomputed); each book's "
          "p_A comes from that book's team-keyed raw prices; y_A from canonical goals."])

    # ------------------------------------------------------------------ G2
    g2 = 0
    for r in prim.itertuples():
        pm = pred[pred.game_key == r.key]
        if len(pm) == 1:
            c = cm.loc[r.key]
            g2 += int(int(float(c.goals_lo) > float(c.goals_hi)) == int(pm.iloc[0].y_a))
    gate("G2", g2 == 57, [f"y_A from canonical goals (goals_lo > goals_hi) equals predictions.csv y_a: {g2} of 57."])

    # ------------------------------------------------------------------ G3 / G4 (Amendment A1)
    pairs["pi_A"] = pairs.price_A.map(implied)
    pairs["pi_B"] = pairs.price_B.map(implied)
    pairs["over"] = pairs.pi_A + pairs.pi_B
    pairs["prop_A"] = pairs.pi_A / pairs.over
    pairs["prop_B"] = pairs.pi_B / pairs.over
    pairs["hold_re"] = 1 - 1 / pairs.over
    pairs["dA"] = (pairs.prop_A - pairs.stored_p_A.astype(float)).abs()
    pairs["dB"] = (pairs.prop_B - pairs.stored_p_B.astype(float)).abs()
    pairs["dH"] = (pairs.hold_re - pairs.stored_hold.astype(float)).abs()
    np_ = len(pairs)
    c_price = int(((pairs.price_A.abs() >= 100) & (pairs.price_B.abs() >= 100)).sum())
    c_imp = int(((pairs.pi_A > 0) & (pairs.pi_A < 1) & (pairs.pi_B > 0) & (pairs.pi_B < 1)).sum())
    c_over = int((pairs.over > 1).sum())
    c_prop = int((((pairs.prop_A + pairs.prop_B) - 1).abs() <= 1e-12).sum())
    c_prop01 = int(((pairs.prop_A > 0) & (pairs.prop_A < 1) & (pairs.prop_B > 0) & (pairs.prop_B < 1)).sum())
    c_store = int(((pairs.dA <= TOL_STORED) & (pairs.dB <= TOL_STORED)).sum())
    g3_ok = all(c == np_ for c in (c_price, c_imp, c_over, c_prop, c_prop01, c_store))
    gate("G3", g3_ok,
         [f"Amendment A1. (event, book) pairs: {np_}.",
          f"- both raw prices (price_team_a, price_team_b) valid American odds (|price| >= 100): {c_price} of {np_}",
          f"- implied probabilities in (0,1): {c_imp} of {np_}; implied sum > 1: {c_over} of {np_}",
          f"- proportional probabilities sum to 1 within 1e-12: {c_prop} of {np_} (max |sum - 1| "
          f"{((pairs.prop_A + pairs.prop_B) - 1).abs().max():.3g}); both in (0,1): {c_prop01} of {np_}",
          f"- consistency check only: stored team-keyed probabilities within {TOL_STORED:g} of the recomputed ones: "
          f"{c_store} of {np_}; max |diff| team_a side {pairs.dA.max():.6g}, team_b side {pairs.dB.max():.6g}.",
          "- Analysis probabilities are recomputed from the raw prices at full floating-point precision; the stored "
          "rounded columns are not used by compare.py."])

    hstats = []
    for b in BOOKS:
        s = pairs[pairs.book == b]
        hstats.append({"book": b, "pairs": len(s), "mean": s.hold_re.mean(), "min": s.hold_re.min(),
                       "max": s.hold_re.max(), "n_outside_(0,0.25)": int(((s.hold_re <= 0) | (s.hold_re >= 0.25)).sum()),
                       "max_abs_diff_stored_hold": s.dH.max(), "n_stored_hold_within_5e-7": int((s.dH <= TOL_STORED).sum())})
    hst = pd.DataFrame(hstats)
    g4_in = bool(((pairs.hold_re > 0) & (pairs.hold_re < 0.25)).all())
    g4_st = bool((pairs.dH <= TOL_STORED).all())
    gate("G4", g4_in and g4_st,
         ["Hold = 1 - 1/(pi_A + pi_B) from each book's raw prices, every included (event, book) pair; all strictly in "
          f"(0, 0.25): {g4_in}. Stored hold within {TOL_STORED:g} of the recomputed hold: "
          f"{int((pairs.dH <= TOL_STORED).sum())} of {np_} (max |diff| {pairs.dH.max():.6g}). Descriptive; not compared "
          "to the Section 6 hold table.", "", md(hst, "{:.6g}")])

    # ------------------------------------------------------------------ G5
    g5_ok = not ref_prev and not ref_prot
    gate("G5", g5_ok,
         [f"Before the run: earlier hash set vs chunk5 first-run hashes_after.csv ({n_prev} entries; includes the 136 "
          f"entries of chunk4/hashes_after.csv): {'ALL MATCH' if not ref_prev else '; '.join(ref_prev)}; vs "
          f"protected_hashes.txt (10): {'ALL MATCH' if not ref_prot else '; '.join(ref_prot)}.",
          *[f"- sha256 {k}: `{v}`" for k, v in pkey_hashes_before.items()],
          f"- sha256 of the selected 57 prediction rows (columns season,date,game_key,team_a,team_b,p_a,y_a as stored "
          f"strings, sorted by game_key, CSV with header, LF): `{sel_hash_before}` ({n_sel} rows)",
          "- Same as the first run: predictions.csv `70cc70ed...`, frozen_params.json `f25db522...`, test_2025_run.json "
          "`739f5ef9...`, selected rows `a329b504...`: " +
          str(pkey_hashes_before["predictions.csv"].startswith("70cc70ed")
              and pkey_hashes_before["frozen_params.json"].startswith("f25db522")
              and pkey_hashes_before["test_2025_run.json"].startswith("739f5ef9")
              and sel_hash_before.startswith("a329b504"))])
    if not R["G5"]["lines"][-1].endswith("True"):
        R["G5"]["pass"] = False

    # ------------------------------------------------------------------ G6
    pat = re.compile(r"^\s*(from|import)\s+elo_feas\b", re.M)
    scans = {n: pat.search(INPUTS[n].read_text(encoding="utf-8")) is None for n in ("gates.py", "compare.py")
             if INPUTS[n].exists()}
    loaded = sorted(m for m in sys.modules if m == "elo_feas" or m.startswith("elo_feas."))
    gate("G6", all(scans.values()) and not loaded and len(scans) == 2,
         ["Source scan for an elo_feas import: " + "; ".join(f"{n}: {'none' if ok else 'FOUND'}" for n, ok in scans.items())
          + f". elo_feas modules in sys.modules at gate time: {loaded or 'none'}.",
          "The 57 Elo probabilities are the stored p_a strings of predictions.csv; nothing recomputes them. The guard "
          "module methodology_audit.py (elo_feas/audit_out/) is loaded as a top-level module and imports no "
          "experiment code. The selected-rows hash is recorded before (G5) and after (end of this script)."])

    # ------------------------------------------------------------------ G7
    tiers = [(">=1", prim.n_books_h2h_any.astype(int) >= 1), (">=2", prim.n_books_h2h_any.astype(int) >= 2),
             ("3", prim.n_books_h2h_any.astype(int) == 3)]
    dates_of: dict[str, set] = {}
    for r in canon.itertuples():
        for t in (r.team_lo, r.team_hi):
            dates_of.setdefault(t, set()).add(r.date)
    snap_ev = {}
    one_snap = 0
    for r in prim.itertuples():
        s = pd.to_datetime(mlb.loc[mlb.event_id == r.event_id, "snapshot_timestamp"], utc=True)
        one_snap += int(s.nunique() == 1)
        snap_ev[r.event_id] = s.min()
    t1, t2, te_of, d0_of = [], [], {}, {}
    for r in prim.itertuples():
        lo, hi, d = cm.loc[r.key].team_lo, cm.loc[r.key].team_hi, r.matched_date
        last = max(x for t in (lo, hi) for x in dates_of[t] if x < d)
        te_of[r.event_id] = et_utc(Date.fromisoformat(last) + timedelta(days=1))
        d0_of[r.event_id] = et_utc(Date.fromisoformat(d))
        t1.append(bool(snap_ev[r.event_id] >= te_of[r.event_id]))
        t2.append(bool(snap_ev[r.event_id] >= d0_of[r.event_id]))
    prim["T1"], prim["T2"] = t1, t2
    tv = tev.set_index("event_id")
    agree_t1 = int(sum(prim.T1[i] == (tv.loc[e, "T1"] == "True") for i, e in enumerate(prim.event_id)))
    agree_t2 = int(sum(prim.T2[i] == (tv.loc[e, "GAMEDAY"] == "True") for i, e in enumerate(prim.event_id)))
    per_book = [int(mlb.loc[mlb.source == b, "event_id"].nunique()) for b in BOOKS]

    def t2d(m):
        return [int((m & prim.T2 & (prim.matched_date == d)).sum()) for d in ("2025-02-28", "2025-03-08")]
    obs = {
        "events by tier (>=1/>=2/3)": ([57, 55, 43], [int(m.sum()) for _, m in tiers]),
        "per-book events (betmgm/draftkings/fanduel)": ([48, 56, 51], per_book),
        "per-date events (02-28, 03-01, 03-02, 03-08, 03-09)": ([9, 20, 2, 24, 2],
                                                                prim.groupby("matched_date").size().tolist()),
        "T1 by tier": ([57, 55, 43], [int((m & prim.T1).sum()) for _, m in tiers]),
        "T2 by tier": ([33, 33, 27], [int((m & prim.T2).sum()) for _, m in tiers]),
        "T2 dates": (["2025-02-28", "2025-03-08"], sorted(prim.loc[prim.T2, "matched_date"].unique().tolist())),
        "T2 per date at >=1 (02-28, 03-08)": ([9, 24], t2d(tiers[0][1])),
        "T2 per date at >=2 (02-28, 03-08)": ([9, 24], t2d(tiers[1][1])),
        "T2 per date at 3 books (02-28, 03-08)": ([7, 20], t2d(tiers[2][1])),
        "agreement with chunk4 timing_events.csv (T1, T2=GAMEDAY)": ([57, 57], [agree_t1, agree_t2]),
    }
    g7 = pd.DataFrame([{"item": k, "recorded": str(a), "observed": str(b), "match": "MATCH" if a == b else "DIFFERS"}
                       for k, (a, b) in obs.items()])
    gate("G7", bool((g7.match == "MATCH").all()),
         ["T1: snapshot >= 00:00 America/New_York on the day after the later of the two teams' previous canonical games "
          "(any season). T2: snapshot >= 00:00 America/New_York on d. Tiers are n_books_h2h_any.", "", md(g7)])

    # ------------------------------------------------------------------ G8 (2025 PRIMARY snapshot files only)
    lu_lines = [f"One common snapshot timestamp per event across its books: {one_snap} of {n_events}.",
                f"team_keyed_moneyline.csv columns ({len(ml_cols)}): {', '.join(ml_cols)}. No last-update column.",
                "Stored per-book last-update fields (found by the first run's header search, chunk5/gates.md): "
                "audit_join/out_run2/b2_b3_overlap.csv, pilot/normalized/{margins,odds_long,spread_pairs,totals_pairs}.csv "
                "and bookmaker-/h2h-market-level `last_update` in the raw odds JSON."]
    steps = sorted(set(pairs.step))
    snaps = sorted(set(pairs.snapshot))
    letters = {s[0] for s in steps}
    raw_files = sorted(p for p in (PILOT / "raw").glob("*odds_2025*.json")
                       if not p.name.endswith(".meta.json") and p.name[0] in letters)
    raw = {}
    file_ts = {}
    for p in raw_files:
        with open(p, encoding="utf-8") as fh:
            j = json.load(fh)
        ts = j.get("timestamp")
        file_ts[p.name] = ts
        for ev in j.get("data") or []:
            for bk in ev.get("bookmakers", []):
                h2h = [m for m in bk.get("markets", []) if m.get("key") == "h2h"]
                raw.setdefault((ev.get("id"), bk.get("key"), ts), []).append(
                    (p.name, bk.get("last_update"), h2h[0].get("last_update") if h2h else None))
    lu_lines.append(f"Steps of the 155 PRIMARY 2025 (event, book) rows: {', '.join(steps)}; their snapshot timestamps: "
                    f"{', '.join(snaps)}. Raw files opened (pilot/raw, request date 2025, prefix in "
                    f"{sorted(letters)}, *.meta.json and all 2026 snapshot files not opened): " +
                    "; ".join(f"{n} (snapshot {t})" for n, t in file_ts.items()) + ".")
    lur = []
    for r in pairs.itertuples():
        hit = raw.get((r.event_id, r.book, r.snapshot))
        rec = {"book": r.book, "event_id": r.event_id, "found": bool(hit)}
        if hit:
            vals = {(h[1], h[2]) for h in hit}
            bk_lu, mk_lu = sorted(vals)[0]
            snap = pd.Timestamp(r.snapshot)
            for lab, v in (("market", mk_lu), ("book", bk_lu)):
                t = pd.Timestamp(v) if v else pd.NaT
                rec[f"{lab}_lag_min"] = (snap - t).total_seconds() / 60 if v else np.nan
                rec[f"{lab}_before_t_e"] = bool(t < te_of[r.event_id]) if v else False
                rec[f"{lab}_before_00ET_d"] = bool(t < d0_of[r.event_id]) if v else False
                rec[f"{lab}_after_snapshot"] = bool(t > snap) if v else False
            rec["consistent"] = len(vals) == 1
        lur.append(rec)
    lur = pd.DataFrame(lur)
    rows = []
    for lab, field in (("market", "h2h market-level last_update"), ("book", "bookmaker-level last_update")):
        for b in BOOKS:
            s = lur[(lur.book == b) & lur.found]
            lag = s[f"{lab}_lag_min"].dropna()
            qq = q(lag)
            rows.append({"field": field, "book": b, "pairs": int((lur.book == b).sum()), "found_in_raw": len(s),
                         "with_value": len(lag), "consistent_across_files": int(s.consistent.sum()),
                         "lag_min_minutes": qq.get("min"), "lag_q25_minutes": qq.get("q25"),
                         "lag_median_minutes": qq.get("median"), "lag_q75_minutes": qq.get("q75"),
                         "lag_max_minutes": qq.get("max"), "lag_max_hours": qq.get("max", np.nan) / 60,
                         "n_last_update_after_snapshot": int(s[f"{lab}_after_snapshot"].sum()),
                         "pairs_last_update_before_t_e": int(s[f"{lab}_before_t_e"].sum()),
                         "pairs_last_update_before_00ET_d": int(s[f"{lab}_before_00ET_d"].sum()),
                         "events_last_update_before_t_e": int(s.loc[s[f"{lab}_before_t_e"], "event_id"].nunique()),
                         "events_last_update_before_00ET_d": int(s.loc[s[f"{lab}_before_00ET_d"], "event_id"].nunique())})
    g8_tab = pd.DataFrame(rows)
    mk_max_h = g8_tab.loc[g8_tab.field.str.startswith("h2h"), "lag_max_hours"].max()
    lu_lines += ["Matched on (event id, book, snapshot timestamp). Lag = snapshot - last_update. Diagnostic only; no "
                 "loss is stratified by it.", "", md(g8_tab, "{:.4f}"), "",
                 f"Spec v2.1.1 section 7 describes the first-run lag as 'at most about 0.01 hours for every book'; the "
                 f"maximum h2h market-level lag observed here is {mk_max_h:.4f} hours "
                 f"({mk_max_h * 60:.2f} minutes)" + (" (difference from the spec's description; reported as a finding)."
                                                    if mk_max_h > 0.015 else ".")]
    g8_found = int(lur.found.sum())
    gate("G8", one_snap == n_events, lu_lines + [f"(event, book) pairs found in the raw files: {g8_found} of {np_}."])

    # ------------------------------------------------------------------ G9
    pa = pd.to_numeric(pred[pred.game_key.isin(set(prim.key))].p_a, errors="coerce")
    g9_ok = len(pa) == 57 and pa.notna().all() and ((pa > 0) & (pa < 1)).all()
    eps, eps_line = None, None
    for i, line in enumerate(INPUTS["metrics.py (epsilon source)"].read_text(encoding="utf-8").splitlines(), 1):
        m = re.match(r"^EPS\s*=\s*([0-9.eE+-]+)\s*$", line)
        if m:
            eps, eps_line = float(m.group(1)), i
            break
    eps_note = (f"Log-loss clipping epsilon read as text from elo_feas/metrics.py line {eps_line}: EPS = {eps:g} "
                "(file read, not imported)." if eps is not None else
                "EPS could not be read from elo_feas/metrics.py; using 1e-15.")
    if eps is None:
        eps = 1e-15
    mk_all = pd.concat([pairs.prop_A, pairs.prop_B])
    gate("G9", g9_ok, [f"57 Elo p_a values: {len(pa)} rows, missing {int(pa.isna().sum())}, all in (0,1): "
                       f"{bool(((pa > 0) & (pa < 1)).all())}; min {pa.min():.6f}, max {pa.max():.6f}.", eps_note,
                       f"Spec section 5 states no Elo or market probability is within 0.1 of 0 or 1: Elo range "
                       f"[{pa.min():.6f}, {pa.max():.6f}], per-book proportional probability range (both sides) "
                       f"[{mk_all.min():.6f}, {mk_all.max():.6f}] -> "
                       f"{'consistent' if min(pa.min(), mk_all.min()) > 0.1 and max(pa.max(), mk_all.max()) < 0.9 else 'NOT consistent (finding)'}."])

    # ------------------------------------------------------------------ after: selected rows and key files unchanged
    sel_hash_after, _ = selected_rows_hash(read_pred2025(), prim.key)
    pkey_after = {k: sha(INPUTS[k]) for k in pkey_hashes_before}
    same_after = sel_hash_after == sel_hash_before and pkey_after == pkey_hashes_before
    R["G6"]["lines"].append(f"After the gates: selected-rows hash `{sel_hash_after}` and the three key-file hashes "
                            f"unchanged: {same_after}.")
    if not same_after:
        R["G6"]["pass"] = False

    # ------------------------------------------------------------------ report
    spec_sha = sha(SPEC)
    all_pass = all(v["pass"] for v in R.values())
    S = ["# Chunk 5 gates (G1-G9), rerun under Amendment A1", "",
         f"**{DIAG}.** No loss of any kind is computed here (no Brier, no log loss, no Elo-vs-market comparison). "
         "Inputs are read with usecols; home_team and both `result` columns are never loaded; no raw 2025/2026 "
         "results file is opened; nothing is imported from elo_feas.", "@@HASH@@", "",
         f"Specification: `chunk5/market_comparison_spec_v2.1.1.md`, sha256 `{spec_sha}`, {SPEC.stat().st_size} bytes "
         f"(expected `{SPEC_SHA}`, {SPEC_BYTES} bytes: {'MATCH' if spec_sha == SPEC_SHA else 'DIFFERS'}). The first "
         "gate run (G3 failed at 1e-9) is kept as chunk5/gates_failed.md and chunk5/gates.md.", "",
         "| gate | result |", "|---|---|"]
    S += [f"| {k} | {'PASS' if v['pass'] else '**FAIL**'} |" for k, v in R.items()]
    S += ["", f"**Overall: {'ALL GATES PASS' if all_pass else 'GATES FAILED - no loss computed, compare.py not run'}**",
          ""]
    for k, v in R.items():
        S += [f"## {k} - {'PASS' if v['pass'] else 'FAIL'}", *v["lines"], ""]
    S += [f"_{DIAG}_", ""]
    failed = [k for k, v in R.items() if not v["pass"]]
    fmd = "\n".join(["# Chunk 5 rerun: gates failed", "", f"**{DIAG}.** Failed gates: {', '.join(failed)}. No loss was "
                     "computed; compare.py was not imported or run; gates_passed.json was not written.", ""] +
                    sum([[f"## {k}", *R[k]["lines"], ""] for k in failed], [])) + "\n"
    passed = {"gates": {k: "PASS" for k in R}, "amendment": "A1", "spec_file": "market_comparison_spec_v2.1.1.md",
              "spec_sha256": spec_sha, "epsilon": eps, "epsilon_source": eps_note,
              "selected_57_rows_sha256": sel_hash_before,
              "input_sha256": {k: sha(p) for k, p in INPUTS.items() if p.exists()}}
    return {"all_pass": all_pass, "md": "\n".join(S), "failed_md": fmd, "passed": passed}


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        with G._orig_open(CH5 / "files_opened_rerun.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# shell/tool reads first (listed by hand), then every open through builtins.open/io.open/"
                     "pathlib/os.open logged by the guard, in order\n")
            fh.write("\n".join(SHELL_READS) + "\n# --- gates.py (rerun): python opens logged by the guard ---\n")
            fh.write("\n".join(G.OPENED) + "\n")
    sys.exit(code)
