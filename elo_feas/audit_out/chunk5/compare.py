"""Chunk 5, script 2: paired comparison R1-R5 (runs only after gates.py has passed).

Refuses to run unless chunk5/gates_passed.json exists and every input sha256 recorded there matches the current file.
Diagnostic, no re-selection. Writes only to elo_feas/audit_out/chunk5/. Imports nothing from elo_feas. Elo p_A is the
stored predictions.csv p_a (never recomputed). Book p_A = proportional de-vig of the book's raw team-keyed prices.
No intervals, standard errors, tests, correlations, plots or extra subsets; no ranking or interpretation.
Run:  $env:PYTHONDONTWRITEBYTECODE=1; .\\.venv\\Scripts\\python.exe -B elo_feas\\audit_out\\chunk5\\compare.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import hashlib  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402

CH5 = pathlib.Path(__file__).resolve().parent
AUD = CH5.parent
sys.path.insert(0, str(AUD))
sys.path.insert(0, str(CH5))

import methodology_audit as G  # noqa: E402  (open/write guards)

G.AUDIT_OUT = CH5

import gates as GT  # noqa: E402  (constants and helpers only; its main() is not called)


def verify_gates() -> dict:
    p = CH5 / "gates_passed.json"
    if not p.exists():
        raise SystemExit("REFUSED: chunk5/gates_passed.json does not exist; no loss computed.")
    gp = json.loads(p.read_text(encoding="utf-8"))
    bad = [k for k, h in gp["input_sha256"].items() if k not in GT.INPUTS or G.sha256_file(GT.INPUTS[k]) != h]
    missing = [k for k in GT.INPUTS if k not in gp["input_sha256"]]
    if gp.get("spec_sha256") != GT.SPEC_SHA or G.sha256_file(GT.SPEC) != GT.SPEC_SHA:
        raise SystemExit("REFUSED: spec v2.1.1 hash does not verify; no loss computed.")
    if bad or missing or any(v != "PASS" for v in gp["gates"].values()):
        raise SystemExit(f"REFUSED: gates_passed.json does not verify (changed: {bad}; unrecorded: {missing}); "
                         "no loss computed.")
    return gp


GP = verify_gates()                       # before numpy/pandas work and before any loss code runs

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

EPS = float(GP["epsilon"])
TIE = 1e-12
DIAG = G.DIAG
BOOKS = GT.BOOKS


def brier(p, y):
    return (p - y) ** 2


def logloss(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def fmt(v, f="{:.6f}"):
    if isinstance(v, (float, np.floating)):
        return "" if v != v else f.format(v)
    return str(v)


def md(df: pd.DataFrame, f="{:.6f}") -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(fmt(v, f) for v in r) + " |")
    return "\n".join(out)


def save(df: pd.DataFrame, name: str) -> None:
    df.assign(label=DIAG).to_csv(CH5 / name, index=False, lineterminator="\n", float_format="%.12g")


def load() -> pd.DataFrame:
    pred = pd.read_csv(GT.INPUTS["predictions.csv"], dtype=str, keep_default_na=False,
                       usecols=["season", "date", "game_key", "team_a", "team_b", "p_a", "y_a"])
    pred = pred[pred.season == "2025"].set_index("game_key")
    canon = pd.read_csv(GT.INPUTS["canonical_games_2022_2026.csv"], dtype=str, keep_default_na=False,
                        usecols=["game_key", "team_lo", "team_hi", "goals_lo", "goals_hi"]).set_index("game_key")
    links = pd.read_csv(GT.INPUTS["event_game_links.csv"], dtype=str, keep_default_na=False,
                        usecols=["event_id", "season", "PRIMARY", "matched_game_key", "matched_date", "n_books_h2h_any"])
    ml = pd.read_csv(GT.INPUTS["team_keyed_moneyline.csv"], dtype=str, keep_default_na=False,
                     usecols=["event_id", "source", "team_a", "team_b", "price_team_a", "price_team_b"])
    tev = pd.read_csv(GT.INPUTS["timing_events.csv"], dtype=str, keep_default_na=False,
                      usecols=["event_id", "T1", "GAMEDAY"]).set_index("event_id")
    prim = links[(links.PRIMARY == "True") & (links.season == "2025")].copy()
    prim["key"] = prim.matched_game_key.str.split("|", n=1).str[1]
    prim = prim.sort_values(["matched_date", "key"]).reset_index(drop=True)
    sel = pred.loc[prim.key]
    sel_txt = sel.reset_index()[["season", "date", "game_key", "team_a", "team_b", "p_a", "y_a"]] \
        .sort_values("game_key").to_csv(index=False, lineterminator="\n")
    if GP["selected_57_rows_sha256"] != hashlib.sha256(sel_txt.encode("utf-8")).hexdigest():
        raise SystemExit("REFUSED: the selected 57 prediction rows differ from the hash recorded by gates.py.")
    rows = []
    for r in prim.itertuples():
        c = canon.loc[r.key]
        rec = {"event_id": r.event_id, "date": r.matched_date, "team_a": c.team_lo, "team_b": c.team_hi,
               "y_A": int(float(c.goals_lo) > float(c.goals_hi)), "n_books": int(r.n_books_h2h_any),
               "p_Elo": float(pred.loc[r.key, "p_a"]),
               "T1": tev.loc[r.event_id, "T1"] == "True", "T2": tev.loc[r.event_id, "GAMEDAY"] == "True"}
        m = ml[(ml.event_id == r.event_id) & (ml.source != "consensus")]
        for b in BOOKS:
            mb = m[m.source == b]
            if len(mb) == 0:
                rec[f"p_{b}"] = np.nan
                continue
            x = mb.iloc[0]
            pa, pb = (x.price_team_a, x.price_team_b) if x.team_a == c.team_lo else (x.price_team_b, x.price_team_a)
            ia, ib = GT.implied(float(pa)), GT.implied(float(pb))
            rec[f"p_{b}"] = ia / (ia + ib)
        rows.append(rec)
    df = pd.DataFrame(rows)
    df["p_consensus"] = df[[f"p_{b}" for b in BOOKS]].mean(axis=1, skipna=True)
    df["p_const05"] = 0.5
    y = df.y_A.astype(float).to_numpy()
    for f in ["Elo", *BOOKS, "consensus", "const05"]:
        p = df[f"p_{f}"].to_numpy(float)
        df[f"brier_{f}"] = brier(p, y)
        df[f"logloss_{f}"] = logloss(p, y)
    for mkt in ["consensus", *BOOKS]:
        for L in ("brier", "logloss"):
            df[f"d_{L}_vs_{mkt}"] = df[f"{L}_Elo"] - df[f"{L}_{mkt}"]
    return df


def df_keys(df: pd.DataFrame) -> list[str]:
    return [f"{d}|{a}|{b}" for d, a, b in zip(df.date, df.team_a, df.team_b)]


def summary(sub: pd.DataFrame, subset: str, mkt: str) -> dict:
    out = {"subset": subset, "market": mkt, "n": len(sub)}
    for L in ("brier", "logloss"):
        out[f"mean_{L}_Elo"] = sub[f"{L}_Elo"].mean()
        out[f"mean_{L}_{'market'}"] = sub[f"{L}_{mkt}"].mean()
        out[f"mean_{L}_const05"] = sub[f"{L}_const05"].mean()
        d = sub[f"d_{L}_vs_{mkt}"]
        out.update({f"d_{L}_mean": d.mean(), f"d_{L}_median": d.median(), f"d_{L}_sd": d.std(ddof=1),
                    f"d_{L}_min": d.min(), f"d_{L}_max": d.max(),
                    f"{L}_Elo_lower": int((d < -TIE).sum()), f"{L}_market_lower": int((d > TIE).sum()),
                    f"{L}_tie": int((d.abs() < TIE).sum())})
    return out


def r2_table(df: pd.DataFrame, extra=None, label="") -> pd.DataFrame:
    m = (lambda x: x) if extra is None else (lambda x: x[extra(x)])
    rows = [summary(m(df), f"S1{label}", "consensus"),
            summary(m(df[df.n_books >= 2]), f"S2{label}", "consensus"),
            summary(m(df[df.n_books == 3]), f"S3{label}", "consensus")]
    rows += [summary(m(df[df.n_books == 3]), f"S3{label}", b) for b in BOOKS]
    if extra is None:
        rows += [summary(df[df[f"p_{b}"].notna()], "S4", b) for b in BOOKS]
    return pd.DataFrame(rows)


def per_date(sub: pd.DataFrame, subset: str) -> pd.DataFrame:
    rows = []
    for d, s in sub.groupby("date"):
        rows.append({"subset": subset, "date": d, "n": len(s), "n_is_2": "n = 2" if len(s) == 2 else "",
                     "mean_brier_Elo": s.brier_Elo.mean(), "mean_brier_consensus": s.brier_consensus.mean(),
                     "mean_brier_const05": s.brier_const05.mean(), "mean_d_brier": s.d_brier_vs_consensus.mean(),
                     "mean_logloss_Elo": s.logloss_Elo.mean(), "mean_logloss_consensus": s.logloss_consensus.mean(),
                     "mean_logloss_const05": s.logloss_const05.mean(), "mean_d_logloss": s.d_logloss_vs_consensus.mean()})
    return pd.DataFrame(rows)


def split_r2(t: pd.DataFrame) -> list[str]:
    a = t[["subset", "market", "n", "mean_brier_Elo", "mean_brier_market", "mean_brier_const05", "d_brier_mean",
           "d_brier_median", "d_brier_sd", "d_brier_min", "d_brier_max", "brier_Elo_lower", "brier_market_lower",
           "brier_tie"]]
    b = t[["subset", "market", "n", "mean_logloss_Elo", "mean_logloss_market", "mean_logloss_const05",
           "d_logloss_mean", "d_logloss_median", "d_logloss_sd", "d_logloss_min", "d_logloss_max",
           "logloss_Elo_lower", "logloss_market_lower", "logloss_tie"]]
    return ["Brier:", md(a), "", "Log loss:", md(b), ""]


def main() -> int:
    GT.install_socket_guard()
    before = GT.snapshot()
    ref = {}
    for line in (CH5 / "hashes_after_rerun.csv").read_text(encoding="utf-8").splitlines()[1:]:
        p, _, h = line.split(",")
        ref[p] = h
    ref_bad = G.compare_reference(before, ref)
    if ref_bad or set(before) != set(ref):
        raise SystemExit(f"REFUSED: files changed since gates.py ({ref_bad}); no loss computed.")
    df = load()
    r1_cols = ["date", "team_a", "team_b", "y_A", "n_books", "p_Elo", *[f"p_{b}" for b in BOOKS], "p_consensus", "T1",
               "T2"]
    r1_cols += [f"{L}_{f}" for L in ("brier", "logloss") for f in ["Elo", *BOOKS, "consensus", "const05"]]
    r1_cols += [f"d_{L}_vs_{m}" for L in ("brier", "logloss") for m in ["consensus", *BOOKS]]
    r1 = df[r1_cols]
    save(r1, "R1_per_game.csv")
    r2 = r2_table(df)
    save(r2, "R2_subset_summaries.csv")
    r3 = per_date(df, "S1")
    save(r3, "R3_per_date_S1.csv")
    tiers = [("S1 (>=1 book)", df.n_books >= 1), ("S2 (>=2 books)", df.n_books >= 2), ("S3 (3 books)", df.n_books == 3)]
    r4 = pd.DataFrame([{"subset": n, "events": int(m.sum()), "T1": int((m & df.T1).sum()),
                        "not_T1": int((m & ~df.T1).sum())} for n, m in tiers])
    save(r4, "R4_T1_counts.csv")
    r5 = r2_table(df, extra=lambda x: x.T2, label=" & T2")
    save(r5, "R5_T2_summaries.csv")
    r5d = pd.concat([per_date(df[df.T2], "S1 & T2"), per_date(df[df.T2 & (df.n_books >= 2)], "S2 & T2"),
                     per_date(df[df.T2 & (df.n_books == 3)], "S3 & T2")], ignore_index=True)
    save(r5d, "R5_T2_per_date.csv")

    r1_show = r1.copy()
    for c in ("T1", "T2"):
        r1_show[c] = r1_show[c].map({True: "Y", False: "N"})
    sel_after, _ = GT.selected_rows_hash(GT.read_pred2025(), df_keys(df))
    after = GT.snapshot()
    diff = [f"{'CHANGED' if k in before and k in after else ('ADDED' if k in after else 'REMOVED')} {k}"
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    (CH5 / "hash_diff_compare.txt").write_text(
        f"files hashed: {len(before)} before, {len(after)} after (same set as gates.py rerun; compare.py outputs "
        "excluded)\n"
        f"before vs gates.py hashes_after_rerun.csv ({len(ref)} entries): ALL MATCH\n"
        f"before vs after: {'NO DIFFERENCES' if not diff else str(len(diff)) + ' DIFFERENCES'}\n" +
        "".join(d + "\n" for d in diff) +
        f"selected 57 prediction rows sha256 after compare.py: {sel_after} "
        f"({'equals' if sel_after == GP['selected_57_rows_sha256'] else 'DIFFERS FROM'} the gates.py value)\n",
        encoding="utf-8")
    S = ["# Chunk 5: paired comparison on the 57 PRIMARY 2025 events (R1-R5)", "",
         f"**{DIAG}.** Specification: market_comparison_spec_v2.1.1.md (Amendment A1), sha256 `{GP['spec_sha256']}`. "
         f"Run only after gates.py passed (gates_passed.json verified: all input sha256 match). Hash check over "
         f"{len(before)} files before/after compare.py: {'NO DIFFERENCES' if not diff else 'DIFFERENCES'}; selected "
         f"57 prediction rows unchanged: {sel_after == GP['selected_57_rows_sha256']}. Forecasters "
         "on canonical team_a: Elo (stored predictions.csv p_a), each book (proportional de-vig of its raw team-keyed "
         "prices), consensus (equal-weight mean of the available books' proportional probabilities), constant 0.5. "
         f"Brier (p - y)^2; log loss -[y ln p + (1 - y) ln(1 - p)] with p clipped to [{EPS:g}, 1 - {EPS:g}] for every "
         f"forecaster ({GP['epsilon_source']}). d_i = loss_Elo - loss_market (negative = Elo loss lower). Tie if "
         "|d| < 1e-12. SD is the sample SD (ddof = 1). No intervals, standard errors, tests or correlations.", "",
         "## R1 Per-game (57 rows)", md(r1_show), "",
         "## R2 Subset summaries",
         "S1: all 57 events, consensus. S2: 55 events with >= 2 books, consensus. S3: 43 events with 3 books, "
         "consensus and each book. S4: each book on the events it quotes.", *split_r2(r2),
         "## R3 Per date, S1 (consensus)", md(r3), "",
         "## R4 T1 (counts only)", md(r4, "{:.0f}"), "",
         "## R5 T2 (snapshot >= 00:00 America/New_York on the game date)", *split_r2(r5),
         "Per date:", md(r5d), "", f"_{DIAG}_", ""]
    (CH5 / "compare_report.md").write_text("\n".join(S), encoding="utf-8")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        with G._orig_open(CH5 / "files_opened_compare.txt", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# compare.py: python opens logged by the guard, in order\n" + "\n".join(G.OPENED) + "\n")
    sys.exit(code)
