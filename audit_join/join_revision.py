"""Join revision (run 2): reviewed name overrides, sample definitions, team-keyed market probabilities and a
B2/B3 reconciliation. OFFLINE.

Builds on audit_join.join_audit (imported, never modified). Its matching rules stay FROZEN; the only new rule
is a reviewed name override (reviewed_overrides.csv), consulted only for a name the frozen rules leave
UNRESOLVED, and only if its regex selects exactly one results name that has its own team rows.

The odds feed's home_team / away_team are labels. They are never used as the actual home team, and the
team-keyed probabilities map each price to a team by its outcome name, never by feed position.

Reads, read-only: everything join_audit reads, audit_join/out/ (run 1), reviewed_overrides.csv, the B1/B2/B3
raw odds payloads and their .meta.json, pilot/ledger/credit_ledger.csv. Writes, only when run as a script:
  audit_join/out_run1/  (one-time byte copy of audit_join/out/ plus SHA256SUMS.txt; verified, never overwritten)
  audit_join/out_run2/  (refuses to overwrite unless --force)
  .\\.venv\\Scripts\\python.exe -B -m audit_join.join_revision
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.dont_write_bytecode = True

from audit_join import join_audit as J  # noqa: E402

ROOT = J.ROOT
HERE = J.HERE
OUT_RUN1 = HERE / "out_run1"
OUT_RUN2 = HERE / "out_run2"
OVERRIDES_CSV = HERE / "reviewed_overrides.csv"
PROTECTED_HASHES = HERE / "protected_hashes.txt"
LEDGER_CSV = ROOT / "pilot" / "ledger" / "credit_ledger.csv"
SUMS_NAME = "SHA256SUMS.txt"

EXPECTED_EVENTS = 139
EXPECTED_COVERAGE = (131, 127, 105)
EXPECTED_RUN1_MATCHED = 124
OVERRIDE_COLUMNS = ["odds_name", "results_regex", "identity_note", "review_status"]
REVIEWED_OVERRIDE = "REVIEWED_OVERRIDE"
ORIENTATIONS = ("AGREES", "DISAGREES", "CONFLICT", "NEUTRAL_OR_UNKNOWN", "SINGLE_SIDED_NOT_CHECKABLE")
SAMPLE_FLAGS = ["matched", "score_consistent", "reliable_single", "PRIMARY", "SENSITIVITY", "VENUE_QUALIFIED",
                "NEUTRAL_CANDIDATE", "VENUE_CONFLICT"]
RUN2_EXTRA_COLUMNS = ["name_method_home", "name_method_away", "home_team_results",
                      "feed_home_label_agrees_with_results", "team_a", "team_b", "goals_a", "goals_b",
                      "game_ot_mismatch"]
# Files whose bytes must not change while this runs (in addition to the ten protected files).
WATCHED = ["audit_join/join_audit.py", "tests/test_join_audit.py", "audit_join/aliases.csv",
           "audit_join/manual_verification.csv", "audit_join/reviewed_overrides.csv", "tests/conftest.py"]
LOG_CLIP = 1e-6
VERIFICATION_LINE = ("Mechanical matching is not verification; only the four events in manual_verification.csv "
                     "carry independently_verified = yes.")


class HardStop(RuntimeError):
    pass


class ArchiveError(HardStop):
    pass


class OverrideError(HardStop):
    pass


class RunDiffError(HardStop):
    pass


# --------------------------------------------------------------------------- hashing
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_protected_hashes(path: Path = PROTECTED_HASHES) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            h, rel = line.split(None, 1)
            out[rel.strip()] = h
    return out


def hash_paths(rels, root: Path = ROOT) -> dict:
    return {rel: sha256_file(Path(root) / rel) if (Path(root) / rel).exists() else "MISSING" for rel in rels}


def check_protected(root: Path = ROOT, path: Path = PROTECTED_HASHES) -> list:
    """Protected files whose current hash differs from protected_hashes.txt."""
    exp = read_protected_hashes(path)
    now = hash_paths(exp, root)
    return [rel for rel in exp if now[rel] != exp[rel]]


# --------------------------------------------------------------------------- part 1: archive of run 1
def _read_sums(path: Path) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            h, name = line.split(None, 1)
            out[name.strip()] = h
    return out


def archive_run1(src: Path = J.OUT_DIR, dst: Path = OUT_RUN1) -> dict:
    """First call: byte-copy every file of src into dst and write dst/SHA256SUMS.txt. Later calls: verify dst
    against SHA256SUMS.txt and src against dst; raise on any difference. Never overwrites; src is only read."""
    src, dst = Path(src), Path(dst)
    sums_path = dst / SUMS_NAME
    action = "verified existing archive"
    if not dst.exists():
        files = sorted(f for f in src.iterdir() if f.is_file())
        if not files:
            raise ArchiveError(f"{src} has no files to archive")
        dst.mkdir(parents=True)
        lines = []
        for f in files:
            shutil.copyfile(f, dst / f.name)
            hs, hd = sha256_file(f), sha256_file(dst / f.name)
            if hs != hd:
                raise ArchiveError(f"copy of {f.name} differs from its source")
            lines.append(f"{hd}  {f.name}")
        sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        action = "created archive"
    if not sums_path.exists():
        raise ArchiveError(f"{dst} exists without {SUMS_NAME}; refusing to use or overwrite it")
    sums = _read_sums(sums_path)
    bad = [n for n, h in sums.items() if not (dst / n).exists() or sha256_file(dst / n) != h]
    extra = sorted(f.name for f in dst.iterdir() if f.is_file() and f.name != SUMS_NAME and f.name not in sums)
    if bad or extra:
        raise ArchiveError(f"run-1 archive does not match {SUMS_NAME}: changed/missing {bad}, unlisted {extra}")
    src_bad = [n for n, h in sums.items() if not (src / n).exists() or sha256_file(src / n) != h]
    src_extra = sorted(f.name for f in src.iterdir() if f.is_file() and f.name not in sums)
    if src_bad or src_extra:
        raise ArchiveError(f"{src} no longer matches the run-1 archive: changed/missing {src_bad}, new {src_extra}")
    return {"action": action, "dir": dst, "sums": sums}


# --------------------------------------------------------------------------- part 2: reviewed overrides
def load_overrides(path: Path = OVERRIDES_CSV) -> list:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != OVERRIDE_COLUMNS:
            raise OverrideError(f"{Path(path).name}: columns {reader.fieldnames} != {OVERRIDE_COLUMNS}")
        rows = list(reader)
    seen = Counter(J.normalize_name(r["odds_name"]) for r in rows)
    dup = sorted(k for k, n in seen.items() if n > 1)
    if dup:
        raise OverrideError(f"{Path(path).name}: duplicate odds names after normalisation: {dup}")
    for r in rows:
        if not r["results_regex"]:
            raise OverrideError(f"{Path(path).name}: empty results_regex for {r['odds_name']!r}")
        re.compile(r["results_regex"])
    return rows


def resolve_with_overrides(odds_names, results_names, aliases, overrides, team_names):
    """Frozen J.resolve_names first. Then, only for a name that is UNRESOLVED and whose J-normalised form equals
    an override's J-normalised odds_name: the override regex is searched against team_names (strings that have
    their own rows; never opponent-only strings). Exactly one candidate -> RESOLVED via REVIEWED_OVERRIDE;
    zero or several -> OverrideError listing them (nothing is picked).

    Returns (resolutions, override_log). Records the frozen rules produced are returned as J produced them."""
    frozen = J.resolve_names(odds_names, results_names, aliases)
    by_norm = {J.normalize_name(o["odds_name"]): o for o in overrides}
    teams = sorted(set(team_names))
    out, log, errors = dict(frozen), [], []
    for name, rec in frozen.items():
        ov = by_norm.get(rec["normalized"])
        if ov is None:
            continue
        entry = {"odds_name": name, "override_odds_name": ov["odds_name"], "results_regex": ov["results_regex"],
                 "identity_note": ov["identity_note"], "review_status": ov["review_status"],
                 "frozen_status": rec["status"], "frozen_method": rec["method"], "candidates": "", "chosen": "",
                 "outcome": ""}
        if rec["status"] != "UNRESOLVED":
            entry["outcome"] = f"NOT_APPLIED (frozen rules gave {rec['status']})"
            log.append(entry)
            continue
        rx = re.compile(ov["results_regex"])
        cands = [t for t in teams if rx.search(t)]
        entry["candidates"] = "; ".join(cands)
        if len(cands) != 1:
            errors.append(f"{name!r}: regex {ov['results_regex']!r} matched {len(cands)} team-row names: {cands}")
            continue
        new = dict(rec)
        new.update(status="RESOLVED", method=REVIEWED_OVERRIDE, results_name=cands[0],
                   identity_note=ov["identity_note"], review_status=ov["review_status"],
                   override_regex=ov["results_regex"], override_candidates=cands[0])
        out[name] = new
        entry.update(chosen=cands[0], outcome="APPLIED")
        log.append(entry)
    if errors:
        raise OverrideError("override candidates are not exactly one; nothing was picked:\n  " + "\n  ".join(errors))
    for e in log:
        e["also_resolved_from"] = "; ".join(sorted(n for n, r in out.items()
                                                   if e["chosen"] and n != e["odds_name"]
                                                   and r["results_name"] == e["chosen"]))
    return out, log


# --------------------------------------------------------------------------- part 3: run 2 tables
def load_results_all(data_dir: Path | None = None) -> dict:
    """Results games for 2025 and 2026, built exactly as J.run_audit builds them."""
    kept_all, excl_all, games_all, acct, names_by_season, team_names = [], [], [], {}, {}, set()
    for y in J.SEASONS:
        kept, excl = J.load_results(y, J.results_path(y, data_dir))
        kept = J.clean_opponents(kept)
        games, a = J.build_canonical_games(kept, excl)
        kept_all.append(kept)
        excl_all.append(excl)
        games_all.append(games)
        acct[y] = a
        names_by_season[y] = J.results_canonical_names(kept)
        team_names |= set(kept["row_team"])
    return {"kept": pd.concat(kept_all, ignore_index=True), "excluded": pd.concat(excl_all, ignore_index=True),
            "games": pd.concat(games_all, ignore_index=True), "accounting": acct, "names_by_season": names_by_season,
            "results_names": set().union(*names_by_season.values()), "team_names": team_names}


def _feed_label(orientation: str) -> str:
    return orientation if orientation in ("AGREES", "DISAGREES") else "NOT_APPLICABLE"


def add_run2_columns(links: pd.DataFrame, resolutions: dict, games: pd.DataFrame) -> pd.DataFrame:
    """Name methods, the results-side home team, the feed-label check and the matched game's teams/goals.
    home_team_results is set only when both rows exist and exactly one carries Home (A_HOME / B_HOME)."""
    d = links.copy()
    d["name_method_home"] = d["home_team"].map(lambda n: resolutions[n]["method"])
    d["name_method_away"] = d["away_team"].map(lambda n: resolutions[n]["method"])
    g = games.set_index("game_key") if len(games) else pd.DataFrame()
    cols = {"team_a": [], "team_b": [], "goals_a": [], "goals_b": [], "game_ot_mismatch": [], "home_team_results": []}
    for key, vc in zip(d["matched_game_key"].astype(str), d["venue_class"].astype(str)):
        if key and key in g.index:
            r = g.loc[key]
            cols["team_a"].append(r["team_a"])
            cols["team_b"].append(r["team_b"])
            cols["goals_a"].append(int(r["goals_a"]))
            cols["goals_b"].append(int(r["goals_b"]))
            cols["game_ot_mismatch"].append(str(r["ot_mismatch"]).strip().lower() == "true")   # bool or CSV text
            cols["home_team_results"].append({"A_HOME": r["team_a"], "B_HOME": r["team_b"]}.get(vc, ""))
        else:
            for k in cols:
                cols[k].append("")
    for k, v in cols.items():
        d[k] = v
    d["feed_home_label_agrees_with_results"] = d["orientation"].astype(str).map(_feed_label)
    return d


def _truthy(s: pd.Series) -> pd.Series:
    return s.map(lambda v: v is True or str(v).strip().lower() == "true")


def add_sample_flags(links: pd.DataFrame) -> pd.DataFrame:
    """Part 4 membership. Orientation and the feed's home label never enter any rule."""
    d = links.copy()
    st, rq, vc = d["status"].astype(str), d["result_quality"].astype(str), d["venue_class"].astype(str)
    ga = pd.to_numeric(d["goals_a"], errors="coerce")
    gb = pd.to_numeric(d["goals_b"], errors="coerce")
    ot = pd.to_numeric(d["ot_periods"], errors="coerce").fillna(0)
    books = pd.to_numeric(d["n_books_h2h_any"], errors="coerce").fillna(0)
    names_ok = (d["home_name_status"].astype(str) == "RESOLVED") & (d["away_name_status"].astype(str) == "RESOLVED")
    matched = st.isin(J.MATCHED)
    tie = ga.notna() & gb.notna() & (ga == gb)
    d["matched"] = matched
    d["score_consistent"] = ((rq == J.TWO_SIDED_AGREE) & ~_truthy(d["game_ot_mismatch"]) & ~tie
                             & (rq != J.MULTI_ROW))
    d["reliable_single"] = ((rq == J.SINGLE_SIDED) & ga.notna() & gb.notna() & (ga != gb)
                            & ((ot <= 0) | ((ga - gb).abs() == 1)) & matched)
    d["PRIMARY"] = matched & d["score_consistent"] & names_ok & (books >= 1)
    d["SENSITIVITY"] = d["PRIMARY"] | (d["reliable_single"] & names_ok & (books >= 1))
    d["VENUE_QUALIFIED"] = d["PRIMARY"] & vc.isin(["A_HOME", "B_HOME"])
    d["NEUTRAL_CANDIDATE"] = d["PRIMARY"] & (vc == "BOTH_AWAY")
    d["VENUE_CONFLICT"] = d["PRIMARY"] & (vc == "BOTH_HOME")
    return d


def run_diff(run1: pd.DataFrame, run2: pd.DataFrame, override_names) -> pd.DataFrame:
    """One row per event. Raises RunDiffError if a run-1 matched event changes status or matched_game_key, if
    an event not involving an override name changes at all, or if the event sets differ."""
    for label, df in (("run 1", run1), ("run 2", run2)):
        if df["event_id"].duplicated().any():
            raise RunDiffError(f"{label} has duplicate event_ids")
    r1 = run1.astype(str).set_index("event_id")
    r2 = run2.astype(str).set_index("event_id")
    if set(r1.index) != set(r2.index):
        raise RunDiffError(f"event sets differ: only run 1 {sorted(set(r1.index) - set(r2.index))}, "
                           f"only run 2 {sorted(set(r2.index) - set(r1.index))}")
    ov = set(override_names)
    rows, bad = [], []
    for eid in r1.index:
        a, b = r1.loc[eid], r2.loc[eid]
        sc, kc = a["status"] != b["status"], a["matched_game_key"] != b["matched_game_key"]
        inv = a["home_team"] in ov or a["away_team"] in ov
        rows.append({"event_id": eid, "season": a["season"], "home_team": a["home_team"], "away_team": a["away_team"],
                     "status_run1": a["status"], "status_run2": b["status"], "reason_run1": a["reason"],
                     "reason_run2": b["reason"], "matched_game_key_run1": a["matched_game_key"],
                     "matched_game_key_run2": b["matched_game_key"], "status_changed": sc,
                     "matched_game_key_changed": kc, "involves_override_name": inv})
        if (sc or kc) and (a["status"] in J.MATCHED or not inv):
            bad.append(f"{eid} ({a['home_team']} vs {a['away_team']}): {a['status']} -> {b['status']}, key "
                       f"{a['matched_game_key']!r} -> {b['matched_game_key']!r}, involves override name: {inv}")
    if bad:
        raise RunDiffError("run-1 events changed:\n  " + "\n  ".join(bad))
    return pd.DataFrame(rows)


NEWLY_COLUMNS = ["event_id", "season", "home_team", "away_team", "home_results_name", "away_results_name",
                 "name_method_home", "name_method_away", "matched_date", "matched_via", "team_a", "team_b",
                 "goals_a", "goals_b", "ot_periods", "venue_class", "feed_home_label_agrees_with_results",
                 "n_books_h2h_any"]


def newly_matched(diff: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    ids = set(diff.loc[(diff["status_run1"] == "FAILED") & diff["status_run2"].isin(J.MATCHED), "event_id"])
    return links[links["event_id"].isin(ids)][NEWLY_COLUMNS].reset_index(drop=True)


# --------------------------------------------------------------------------- part 4: sample tables
def _scopes(df: pd.DataFrame):
    s = pd.to_numeric(df["season"], errors="coerce")
    return [("all", df)] + [(str(y), df[s == y]) for y in J.SEASONS]


BOOK_LEVELS = ((">=1", lambda n: n >= 1), (">=2", lambda n: n >= 2), ("3", lambda n: n == 3))


def sample_table_rows(links: pd.DataFrame, flag: str) -> list:
    d = links[_truthy(links[flag])]
    rows = []
    for lab, cond in BOOK_LEVELS:
        for s, ds in _scopes(d):
            x = ds[cond(pd.to_numeric(ds["n_books_h2h_any"]).astype(int))]
            days = x["matched_date"].nunique()
            oc = x["orientation"].value_counts()
            rows.append([flag, lab, s, len(x), days, f"{len(x) / days:.2f}" if days else "-"]
                        + [int(oc.get(o, 0)) for o in ORIENTATIONS])
    return rows


def legacy_tier_rows(links: pd.DataFrame) -> list:
    """STRICT / LENIENT exactly as J.build_report section G computes them (comparison only)."""
    matched = links[links["status"].isin(J.MATCHED)]
    ok_orient = ~matched["orientation"].isin(["DISAGREES", "CONFLICT"])
    tiers = {"STRICT": matched[ok_orient & (matched["result_quality"] == J.TWO_SIDED_AGREE)],
             "LENIENT": matched[ok_orient & matched["result_quality"].isin([J.TWO_SIDED_AGREE, J.SINGLE_SIDED])]}
    rows = []
    for t, d in tiers.items():
        for lab, cond in BOOK_LEVELS:
            for s, ds in _scopes(d):
                x = ds[cond(ds["n_books_h2h_any"].astype(int))]
                days = x["matched_date"].nunique()
                rows.append([t, lab, s, len(x), days, f"{len(x) / days:.2f}" if days else "-"])
    return rows


# --------------------------------------------------------------------------- part 5: team-keyed probabilities
def implied_prob(price):
    """pi = 100/(p+100) for p > 0, |p|/(|p|+100) for p < 0. None unless a finite American price with |p| >= 100."""
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or abs(price) < 100:
        return None
    return 100.0 / (price + 100.0) if price > 0 else abs(price) / (abs(price) + 100.0)


def team_keyed_moneyline(event: dict, observations, name_map: dict, A=None) -> dict:
    """Per target book: the latest valid pregame h2h observation (analysis.event_weighted), with each price
    keyed to a canonical team through its OUTCOME NAME and name_map (odds name -> results name).

    event home_team / away_team are used only as the set of names an outcome must equal; position never
    decides which price belongs to which team. A book whose outcomes do not map cleanly goes to mismatches
    and is excluded (never guessed)."""
    if A is None:
        A, _ = J.import_analysis()
    eid = event["event_id"]
    event_teams = {event["home_team"], event["away_team"]}
    sel = A.event_weighted([o for o in observations if o["event_id"] == eid and o["market"] == "h2h"])
    books, mismatches = {}, []
    for o in sorted(sel, key=lambda o: o["book"]):
        outs = (o["m"] or {}).get("outcomes") or []
        names = [x.get("name") for x in outs]
        canon = [name_map.get(n, "") for n in names]
        pis = [implied_prob(x.get("price")) for x in outs]
        reason = ""
        if len(outs) != 2:
            reason = "NOT_TWO_OUTCOMES"
        elif any(n not in event_teams for n in names):
            reason = "OUTCOME_NAME_NOT_AN_EVENT_TEAM"
        elif names[0] == names[1]:
            reason = "DUPLICATE_OUTCOME_NAME"
        elif "" in canon or canon[0] == canon[1]:
            reason = "OUTCOME_NOT_MAPPED_TO_TWO_CANONICAL_TEAMS"
        elif None in pis:
            reason = "PRICE_INVALID"
        if reason:
            mismatches.append({"event_id": eid, "book": o["book"], "outcome_names": "; ".join(map(str, names)),
                               "event_team_strings": "; ".join(sorted(event_teams)), "reason": reason})
            continue
        s = pis[0] + pis[1]
        books[o["book"]] = {"p": {canon[i]: pis[i] / s for i in (0, 1)},
                            "pi": {canon[i]: pis[i] for i in (0, 1)},
                            "price": {canon[i]: outs[i]["price"] for i in (0, 1)},
                            "hold": 1 - 1 / s, "snap_ts": o["snap_ts"], "step": o["step"],
                            "tts_hours": None if o["tts_s"] is None else o["tts_s"] / 3600.0}
    return {"event_id": eid, "books": books, "mismatches": mismatches}


def consensus(tk: dict):
    """Mean of the per-book proportional probabilities per team; None unless >= 2 books quote."""
    bs = list(tk["books"].values())
    if len(bs) < 2:
        return None
    teams = sorted(bs[0]["p"])
    return {t: sum(b["p"][t] for b in bs) / len(bs) for t in teams}


def cutoff_check(tk: dict):
    """For >= 2 books: whether every selected observation has the same snapshot timestamp, and max tts gap (h)."""
    bs = list(tk["books"].values())
    if len(bs) < 2:
        return None
    tts = [b["tts_hours"] for b in bs if b["tts_hours"] is not None]
    return {"same_snapshot": len({b["snap_ts"] for b in bs}) == 1,
            "max_tts_diff_hours": (max(tts) - min(tts)) if tts else None}


def brier(p: float, y: int) -> float:
    return (p - y) ** 2


def log_loss(p: float, y: int) -> float:
    q = min(max(p, LOG_CLIP), 1 - LOG_CLIP)
    return -(y * math.log(q) + (1 - y) * math.log(1 - q))


def team_keyed_all(links: pd.DataFrame, observations, resolutions: dict, A) -> dict:
    """team_keyed_moneyline for every event whose two names are RESOLVED."""
    name_map = {n: r["results_name"] for n, r in resolutions.items() if r["status"] == "RESOLVED"}
    out = {}
    for ev in links.to_dict("records"):
        if ev["home_name_status"] == "RESOLVED" and ev["away_name_status"] == "RESOLVED":
            out[ev["event_id"]] = team_keyed_moneyline(ev, observations, name_map, A)
    return out


def pipeline_check(primary: pd.DataFrame, tk_by_event: dict) -> list:
    """Descriptive only. y_team_a from the canonical game's goals (never a result column)."""
    rows = []
    for src in J.TARGET_BOOKS + ("consensus",):
        br, ll, fav_n, fav_w, picks, days = [], [], 0, 0, 0, set()
        for ev in primary.to_dict("records"):
            tk = tk_by_event.get(ev["event_id"])
            if tk is None:
                continue
            p = consensus(tk) if src == "consensus" else (tk["books"].get(src) or {}).get("p")
            if not p:
                continue
            a, b = ev["team_a"], ev["team_b"]
            ya = 1 if int(ev["goals_a"]) > int(ev["goals_b"]) else 0
            pa = p[a]
            br.append(brier(pa, ya))
            ll.append(log_loss(pa, ya))
            days.add(ev["matched_date"])
            if pa == 0.5:
                picks += 1
            else:
                fav_n += 1
                fav_w += int((pa > 0.5) == (ya == 1))
        n = len(br)
        rows.append([src, n, len(days), f"{sum(br) / n:.4f}" if n else "-", f"{sum(ll) / n:.4f}" if n else "-",
                     f"{fav_w}/{fav_n} ({fav_w / fav_n:.3f})" if fav_n else "-", picks, "0.2500", "0.6931"])
    return rows


def team_keyed_table(links: pd.DataFrame, tk_by_event: dict) -> pd.DataFrame:
    rows = []
    for ev in links.to_dict("records"):
        tk = tk_by_event.get(ev["event_id"])
        if tk is None:
            continue
        a, b = sorted({ev["home_results_name"], ev["away_results_name"]})
        for book, x in tk["books"].items():
            rows.append({"event_id": ev["event_id"], "source": book, "team_a": a, "team_b": b,
                         "price_team_a": x["price"][a], "price_team_b": x["price"][b],
                         "p_team_a": round(x["p"][a], 6), "p_team_b": round(x["p"][b], 6),
                         "hold": round(x["hold"], 6), "step": x["step"],
                         "snapshot_timestamp": x["snap_ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "tts_hours": None if x["tts_hours"] is None else round(x["tts_hours"], 4),
                         "PRIMARY": bool(ev["PRIMARY"])})
        c = consensus(tk)
        if c:
            rows.append({"event_id": ev["event_id"], "source": "consensus", "team_a": a, "team_b": b,
                         "price_team_a": "", "price_team_b": "", "p_team_a": round(c[a], 6),
                         "p_team_b": round(c[b], 6), "hold": "", "step": "", "snapshot_timestamp": "",
                         "tts_hours": "", "PRIMARY": bool(ev["PRIMARY"])})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- part 6: B2 / B3 reconciliation
def _mask_api_key(params):
    if isinstance(params, dict):
        return {k: ("<REDACTED>" if k.lower() == "apikey" else v) for k, v in params.items()}
    return re.sub(r"(?i)(apikey[\"'=:\s]+)[^&\"',}\s]+", r"\1<REDACTED>", str(params))


def ledger_b_files(root: Path = ROOT) -> dict:
    """step -> ledger row, raw file and meta for stage B steps B1..B3 (status 200, odds endpoint; last row wins,
    as in analysis.load_inputs)."""
    with open(Path(root) / "pilot" / "ledger" / "credit_ledger.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = {}
    for r in rows:
        m = re.match(r"^stage B step (B[123])\b", r["purpose"])
        if m and r["stage"] == "B" and r["status_code"] == "200" and r["endpoint"].endswith("/odds"):
            raw = Path(root) / "pilot" / "raw" / Path(r["raw_file"]).name
            out[m.group(1)] = {"row": r, "raw": raw, "meta": raw.with_name(raw.name[:-len(".json")] + ".meta.json")}
    return out


def _read_json(path: Path):
    with open(path, "rb") as fh:                                          # read-only
        return json.loads(fh.read().decode("utf-8"))


def quote_index(obj) -> tuple:
    """(event_id, bookmaker, market, outcome_name) -> price, point, market and bookmaker last_update."""
    idx, dups = {}, 0
    for e in obj.get("data") or []:
        for b in e.get("bookmakers") or []:
            for m in b.get("markets") or []:
                for o in m.get("outcomes") or []:
                    k = (e.get("id"), b.get("key"), m.get("key"), o.get("name"))
                    if k in idx:
                        dups += 1
                        continue
                    idx[k] = {"price": o.get("price"), "point": o.get("point"),
                              "market_last_update": m.get("last_update"), "book_last_update": b.get("last_update")}
    return idx, dups


def compare_b2_b3(b2_obj, b3_obj, books_by_event: dict, no_ml_events, A=None) -> dict:
    """Report only. books_by_event (frozen coverage: event -> target books with a valid h2h) is never modified;
    B3 enters no coverage figure or sample."""
    if A is None:
        A, _ = J.import_analysis()
    i2, d2 = quote_index(b2_obj)
    i3, d3 = quote_index(b3_obj)
    fields = ("price", "point", "market_last_update", "book_last_update")
    rows, disc = [], []
    eq = Counter()
    for k in sorted(set(i2) | set(i3), key=lambda k: tuple(map(str, k))):
        a, b = i2.get(k), i3.get(k)
        row = {"event_id": k[0], "bookmaker": k[1], "market": k[2], "outcome_name": k[3],
               "target_book": k[1] in J.TARGET_BOOKS, "in_b2": a is not None, "in_b3": b is not None}
        for f in fields:
            row[f"{f}_b2"] = "" if a is None or a[f] is None else a[f]
            row[f"{f}_b3"] = "" if b is None or b[f] is None else b[f]
        if a is not None and b is not None:
            for f in fields:
                same = a[f] == b[f]
                row[f"{f}_equal"] = same
                eq[f] += int(same)
                if not same:
                    disc.append({"key": k, "field": f, "b2": a[f], "b3": b[f]})
        rows.append(row)
    both = [k for k in i2 if k in i3]
    ebm2, ebm3 = {k[:3] for k in i2}, {k[:3] for k in i3}
    added = sorted(ebm3 - ebm2, key=lambda k: tuple(map(str, k)))
    no_ml = set(no_ml_events)
    added_target = [k for k in added if k[1] in J.TARGET_BOOKS]
    # Coverage effect: the project's own observation rules applied to B3 alone, compared, never merged.
    ts3 = A.parse_ts(b3_obj.get("timestamp"))
    coverage_error = "" if ts3 is not None else "B3 returned timestamp unparseable; coverage effect not computed"
    pseudo = {"step": "B3", "season": 2026, "ts": ts3, "events": b3_obj.get("data") or [], "error": coverage_error,
              "dup_of": ""}                                       # an error makes analysis.usable() skip it
    b3_books = defaultdict(set)
    for o in A.valid(A.observations([pseudo]), "h2h"):
        b3_books[o["event_id"]].add(o["book"])
    changes = []
    for eid, bs in sorted(b3_books.items()):
        old = set(books_by_event.get(eid, set()))
        if len(old | bs) != len(old):
            changes.append({"event_id": eid, "n_books_h2h_any": len(old), "with_b3": len(old | bs),
                            "books_added": sorted(bs - old)})
    b3_events = {e.get("id") for e in b3_obj.get("data") or []}
    return {
        "timestamp_b2": b2_obj.get("timestamp"), "timestamp_b3": b3_obj.get("timestamp"),
        "same_returned_timestamp": b2_obj.get("timestamp") == b3_obj.get("timestamp"),
        "n_keys_b2": len(i2), "n_keys_b3": len(i3), "duplicate_keys_b2": d2, "duplicate_keys_b3": d3,
        "keys_both": len(both), "keys_only_b2": len(set(i2) - set(i3)), "keys_only_b3": len(set(i3) - set(i2)),
        "keys_only_b2_by_market": dict(Counter(k[2] for k in set(i2) - set(i3))),
        "keys_only_b3_by_book": dict(Counter(k[1] for k in set(i3) - set(i2))),
        "identical": {f: eq[f] for f in fields}, "discrepancies": disc,
        "added_observations": added, "added_target": added_target,
        "added_non_target": [k for k in added if k[1] not in J.TARGET_BOOKS],
        "b3_only_target_quotes": sorted((k for k in i3 if k not in i2 and k[1] in J.TARGET_BOOKS),
                                        key=lambda k: tuple(map(str, k))),
        "no_ml_events_in_b3": sorted(no_ml & b3_events),
        "no_ml_target_h2h_added": [k for k in added_target if k[2] == "h2h" and k[0] in no_ml],
        "coverage_changes": changes, "coverage_error": coverage_error, "rows": pd.DataFrame(rows)}


def b2_b3_reconciliation(root: Path, A, observations, inventory) -> dict:
    files = ledger_b_files(root)
    meta_rows = []
    for step in ("B1", "B2", "B3"):
        f = files.get(step)
        if f is None:
            meta_rows.append([step, "NOT IN LEDGER"] + ["-"] * 8)
            continue
        meta, body, r = _read_json(f["meta"]), _read_json(f["raw"]), f["row"]
        pr = meta.get("params_redacted") or {}
        if isinstance(pr, str):
            try:
                pr = json.loads(pr)
            except ValueError:
                pr = {}
        pr = _mask_api_key(pr if isinstance(pr, dict) else {})
        meta_rows.append([step, f["raw"].name, body.get("timestamp"), pr.get("markets", ""), pr.get("regions", ""),
                          meta.get("body_bytes"), meta.get("body_sha256"), meta.get("expected_cost"),
                          f"{r['expected_cost_min']}-{r['expected_cost_max']}", r["actual_cost"]])
    books_by_event = defaultdict(set)
    for o in A.valid(observations, "h2h"):
        books_by_event[o["event_id"]].add(o["book"])
    no_ml = [e["event_id"] for e in inventory if int(e["n_books_h2h_any"]) == 0]
    if "B2" not in files or "B3" not in files:
        return {"meta_rows": meta_rows, "comparison": None, "no_ml_events": no_ml}
    cmp_ = compare_b2_b3(_read_json(files["B2"]["raw"]), _read_json(files["B3"]["raw"]), books_by_event, no_ml, A)
    return {"meta_rows": meta_rows, "comparison": cmp_, "no_ml_events": no_ml}


# --------------------------------------------------------------------------- run 2
def compute_run2(run1_links: pd.DataFrame, root: Path = ROOT, data_dir: Path | None = None,
                 overrides_path: Path = OVERRIDES_CSV) -> dict:
    """Everything in memory; writes nothing."""
    A, info = J.import_analysis()
    res = load_results_all(data_dir)
    inv, snaps = J.build_event_inventory(A, root)
    obs = A.observations(snaps)
    overrides = load_overrides(overrides_path)
    odds_names = [e["home_team"] for e in inv] + [e["away_team"] for e in inv]
    resolutions, olog = resolve_with_overrides(odds_names, res["results_names"], J.load_aliases(), overrides,
                                               res["team_names"])
    applied = {e["odds_name"] for e in olog if e["outcome"] == "APPLIED"}
    links = J.match_events(inv, resolutions, res["games"], res["results_names"])
    links, manual_unmatched = J.apply_manual_verification(links)
    links = add_sample_flags(add_run2_columns(links, resolutions, res["games"]))
    diff = run_diff(run1_links, links, applied)
    xw = J.build_crosswalk(inv, resolutions, res["names_by_season"])
    for c in ("identity_note", "review_status", "override_regex"):
        xw[c] = xw["odds_name"].map(lambda n: resolutions[n].get(c, ""))
    tk = team_keyed_all(links, obs, resolutions, A)
    return {"analysis_info": info, "results": res, "inventory": inv, "observations": obs,
            "resolutions": resolutions, "override_log": olog, "override_names_applied": applied, "links": links,
            "manual_unmatched": manual_unmatched, "run1_links": run1_links, "run_diff": diff,
            "newly_matched": newly_matched(diff, links), "crosswalk": xw,
            "collisions": J.collision_log(resolutions), "team_keyed": tk,
            "team_keyed_table": team_keyed_table(links, tk), "b2b3": b2_b3_reconciliation(root, A, obs, inv)}


def coverage_triplet(inventory) -> tuple:
    n = [int(e["n_books_h2h_any"]) for e in inventory]
    return sum(x >= 1 for x in n), sum(x >= 2 for x in n), sum(x == 3 for x in n)


# --------------------------------------------------------------------------- part 7: report
def _status_rows(links: pd.DataFrame) -> list:
    return [[st] + [int((d["status"] == st).sum()) for _, d in _scopes(links)] for st in J.STATUSES]


def build_revision_report(ctx: dict) -> str:
    T = J.md_table
    links, run1, diff, olog = ctx["links"], ctx["run1_links"], ctx["run_diff"], ctx["override_log"]
    matched = links[links["status"].isin(J.MATCHED)]
    yes = int((links["independently_verified"] == "yes").sum())
    L = ["# Join revision (run 2): pilot odds events vs NCAALacrosseData results (2025, 2026)", "",
         f"- Results repo commit: {ctx['pin']} (pinned, verified). Run 1 archive: {ctx['archive']['action']} "
         f"({len(ctx['archive']['sums'])} files, {SUMS_NAME}).",
         f"- Frozen join_audit rules (EXACT / ALIAS / PREFIX, matching, orientation) unchanged; the only new rule is "
         f"the reviewed name override, applied after the frozen rules leave a name UNRESOLVED. src.ledger in this "
         f"process: {ctx['analysis_info'].get('ledger_module')}.",
         "- The odds feed's home_team / away_team are labels only; they never decide the actual home team or which "
         "price belongs to which team.",
         f"- {VERIFICATION_LINE} (computed: {yes} of {len(links)} links are yes.)",
         "- Every number below is computed by code from the run-2 tables.", ""]

    # A
    L += ["## A. Run 1 vs run 2 status", ""]
    r1, r2 = _status_rows(run1), _status_rows(links)
    L += [T(["Status", "run 1 all", "run 2 all", "run 1 2025", "run 2 2025", "run 1 2026", "run 2 2026"],
            [[a[0], a[1], b[1], a[2], b[2], a[3], b[3]] for a, b in zip(r1, r2)]), ""]
    rr = links[links["status"].isin(["FAILED", "AMBIGUOUS"])].groupby(["status", "reason"]).size()
    L += ["Run 2 FAILED / AMBIGUOUS reasons:", "",
          T(["Status", "Reason", "Events"], [[s, r, int(n)] for (s, r), n in rr.items()]) if len(rr)
          else "No FAILED or AMBIGUOUS events in run 2.", ""]
    n1m = int(run1["status"].isin(J.MATCHED).sum())
    ch = diff[diff["status_changed"] | diff["matched_game_key_changed"]]
    L += [f"run_diff assertion: PASS. All {n1m} run-1 matched events have identical status and matched_game_key in "
          f"run 2; {len(ch)} events changed, all involving an override name "
          f"({int(ch['involves_override_name'].sum())} of {len(ch)}).", ""]
    run1_cols = list(run1.columns)
    L += [f"Run-1 link columns reproduced as the first {len(run1_cols)} run-2 columns: "
          f"{list(links.columns[:len(run1_cols)]) == run1_cols}.", ""]

    # B
    L += ["## B. Reviewed name overrides (review_status as in reviewed_overrides.csv)", ""]
    rows = []
    for e in olog:
        ev_all = diff[(diff["home_team"] == e["odds_name"]) | (diff["away_team"] == e["odds_name"])]
        unblocked = ev_all[(ev_all["status_run1"] == "FAILED") & ev_all["status_run2"].isin(J.MATCHED)]
        rows.append([e["odds_name"], e["results_regex"], e["candidates"] or "-", e["chosen"] or "-", e["outcome"],
                     e["identity_note"], e["review_status"], len(ev_all), len(unblocked),
                     e.get("also_resolved_from") or "-"])
    L += [T(["Odds name", "results_regex", "Candidates (team-row names)", "Chosen", "Outcome", "identity_note",
             "review_status", "Events with this name", "Events unblocked", "Same results name also from"], rows)
          if rows else "No override applied.", ""]
    xw = ctx["crosswalk"]
    L += [T(["Name status (run 2)", "Names"], [[s, int((xw["status"] == s).sum())] for s in J.NAME_STATUSES]), "",
          T(["Method", "Resolved names"], [[m, int(((xw["status"] == "RESOLVED") & (xw["method"] == m)).sum())]
                                           for m in ("EXACT", "ALIAS", "PREFIX", REVIEWED_OVERRIDE)]), ""]

    # C
    nm = ctx["newly_matched"]
    L += ["## C. Newly matched events (FAILED in run 1, matched in run 2)", ""]
    L += [T(["event_id", "Feed home", "Feed away", "Results names", "Methods", "Date", "via", "team_a goals",
             "team_b goals", "OT", "venue_class", "Feed label", "Books"],
            [[r.event_id[:8], r.home_team, r.away_team, f"{r.home_results_name} / {r.away_results_name}",
              f"{r.name_method_home} / {r.name_method_away}", r.matched_date, r.matched_via,
              f"{r.team_a} {r.goals_a}", f"{r.team_b} {r.goals_b}", r.ot_periods, r.venue_class,
              r.feed_home_label_agrees_with_results, r.n_books_h2h_any] for r in nm.itertuples()])
          if len(nm) else "None.", ""]
    L += [f"Newly matched: {len(nm)}; of these in PRIMARY: "
          f"{int(links[links['event_id'].isin(nm['event_id'])]['PRIMARY'].sum())}.", ""]

    # D
    L += ["## D. Samples (books = n_books_h2h_any; orientation never enters membership)", "",
          "- matched = UNIQUE_MATCH or DATE_SHIFTED. score_consistent = TWO_SIDED_AGREE, no OT mismatch, no tie, not "
          "MULTI_ROW. reliable_single = SINGLE_SIDED, both scores, goals differ, OT implies margin 1, matched.",
          "- PRIMARY = matched, score_consistent, both names RESOLVED, >= 1 book. SENSITIVITY = PRIMARY or "
          "(reliable_single, both names RESOLVED, >= 1 book). VENUE_QUALIFIED = PRIMARY and venue_class A_HOME or "
          "B_HOME.", ""]
    hdr = ["Sample", "Books", "Season", "Events", "Game days", "Events per game day"] + list(ORIENTATIONS)
    rows = []
    for f in ("PRIMARY", "SENSITIVITY", "VENUE_QUALIFIED"):
        rows += sample_table_rows(links, f)
    L += [T(hdr, rows), ""]
    L += [T(["Flag", "Events", "Of which via a REVIEWED_OVERRIDE name"],
            [[f, int(links[f].sum()), int((links[f] & ((links["name_method_home"] == REVIEWED_OVERRIDE)
                                                       | (links["name_method_away"] == REVIEWED_OVERRIDE))).sum())]
             for f in SAMPLE_FLAGS]), ""]
    leg1, leg2 = legacy_tier_rows(run1), legacy_tier_rows(links)
    L += ["Legacy STRICT / LENIENT (join_audit section G definitions, which exclude DISAGREES / CONFLICT; "
          "comparison only):", "",
          T(["Tier", "Books", "Season", "run 1 events", "run 1 days", "run 2 events", "run 2 days"],
            [[a[0], a[1], a[2], a[3], a[4], b[3], b[4]] for a, b in zip(leg1, leg2)]), "",
          f"Run-1 legacy rows recomputed here reproduce out_run1/join_audit.md section G line for line: "
          f"{ctx['legacy_reproduces_run1']}.", ""]

    # E
    L += ["## E. Feed home-label disagreement flags (visible, never a filter)", ""]
    rows = []
    for f in ("PRIMARY", "SENSITIVITY", "VENUE_QUALIFIED", "NEUTRAL_CANDIDATE", "VENUE_CONFLICT"):
        d = links[links[f]]
        rows.append([f, len(d), int((d["orientation"] == "DISAGREES").sum()),
                     int((d["orientation"] == "CONFLICT").sum()),
                     int((d["feed_home_label_agrees_with_results"] == "AGREES").sum()),
                     int((d["feed_home_label_agrees_with_results"] == "NOT_APPLICABLE").sum())])
    L += [T(["Sample", "Events", "DISAGREES", "CONFLICT", "feed label AGREES", "feed label NOT_APPLICABLE"], rows), ""]
    bad = matched[matched["orientation"].isin(["DISAGREES", "CONFLICT"])]
    L += [f"Events with orientation DISAGREES or CONFLICT ({len(bad)}):", "",
          T(["event_id", "Feed home", "Feed away", "Results date", "venue_class", "home_team_results",
             "Orientation", "PRIMARY", "SENSITIVITY", "VENUE_QUALIFIED", "VENUE_CONFLICT"],
            [[r.event_id[:8], r.home_team, r.away_team, r.matched_date, r.venue_class, r.home_team_results or "-",
              r.orientation, r.PRIMARY, r.SENSITIVITY, r.VENUE_QUALIFIED, r.VENUE_CONFLICT]
             for r in bad.itertuples()]) if len(bad) else "None.", ""]

    # F
    tk = ctx["team_keyed"]
    mism = [m for t in tk.values() for m in t["mismatches"]]
    L += ["## F. Team-keyed market probabilities", "",
          f"- Events with both names resolved (team-keyed computed): {len(tk)} of {len(links)}. Book observations "
          f"mapped: {sum(len(t['books']) for t in tk.values())}. Selection: analysis.event_weighted (latest valid "
          "pregame h2h per event and book).",
          f"- Outcome-name mismatches (excluded, not guessed): {len(mism)}.", ""]
    if mism:
        L += [T(list(mism[0]), [list(m.values()) for m in mism]), ""]
    cc = {eid: cutoff_check(t) for eid, t in tk.items()}
    cc = {k: v for k, v in cc.items() if v is not None}
    same = sum(v["same_snapshot"] for v in cc.values())
    gaps = [v["max_tts_diff_hours"] for v in cc.values() if v["max_tts_diff_hours"] is not None]
    L += [f"- Cutoff check, events with >= 2 books: {len(cc)}; all books from the same snapshot timestamp: {same}; "
          f"different: {len(cc) - same}; maximum time-to-start difference across books: "
          f"{max(gaps) if gaps else 0:.4f} h.", ""]
    if len(cc) - same:
        L += [T(["event_id", "max tts difference (h)"],
                [[k[:8], f"{v['max_tts_diff_hours']:.4f}"] for k, v in cc.items() if not v["same_snapshot"]]), ""]
    prim = links[links["PRIMARY"]]
    with_c = sum(1 for eid in prim["event_id"] if eid in tk and consensus(tk[eid]) is not None)
    L += [f"- Consensus (mean proportional p, >= 2 books): {with_c} of {len(prim)} PRIMARY events "
          f"({with_c / len(prim):.3f})." if len(prim) else "- No PRIMARY events.", ""]

    # G
    L += ["## G. Pipeline check (descriptive pipeline check, small number of game days)", "",
          "Unit: PRIMARY games. p = proportional de-vig probability for team_a; y_team_a from the canonical game's "
          "goals. Log loss clips p to [1e-6, 1-1e-6]. Reference values are for a constant 0.5 forecast. Not a "
          "research result.", "",
          T(["Source", "n games", "Game days", "Mean Brier", "Mean log loss", "Favorite won", "Pick'em (p=0.5)",
             "Brier at 0.5", "Log loss at 0.5"], pipeline_check(prim, tk)), ""]

    # H
    b = ctx["b2b3"]
    L += ["## H. B2 / B3 reconciliation (read-only; B3 stays out of every coverage figure and sample)", "",
          T(["Step", "Raw file", "Returned timestamp", "markets", "regions", "body_bytes", "body_sha256",
             "meta expected_cost", "ledger expected min-max", "ledger actual_cost"], b["meta_rows"]), ""]
    c = b["comparison"]
    if c is None:
        L += ["B2 or B3 not found in the ledger; comparison skipped.", ""]
    else:
        L += [f"- Returned timestamp B2 {c['timestamp_b2']}, B3 {c['timestamp_b3']}; same: "
              f"{c['same_returned_timestamp']} (same timestamp = one temporal observation).",
              f"- Quote keys (event, bookmaker, market, outcome): B2 {c['n_keys_b2']}, B3 {c['n_keys_b3']}; in both "
              f"{c['keys_both']}; only B2 {c['keys_only_b2']} by market {c['keys_only_b2_by_market']}; only B3 "
              f"{c['keys_only_b3']} by bookmaker {c['keys_only_b3_by_book']}. Duplicate keys inside a file: B2 "
              f"{c['duplicate_keys_b2']}, B3 {c['duplicate_keys_b3']}.",
              f"- Among keys in both: identical price {c['identical']['price']}, point {c['identical']['point']}, "
              f"market last_update {c['identical']['market_last_update']}, bookmaker last_update "
              f"{c['identical']['book_last_update']} (of {c['keys_both']}). Discrepancies: {len(c['discrepancies'])}.",
              f"- (event, book, market) observations B3 would add: {len(c['added_observations'])}; target books "
              f"{len(c['added_target'])}, non-target {len(c['added_non_target'])} "
              f"({dict(Counter(k[1] for k in c['added_non_target']))}).",
              f"- (a) Target-book quotes in B3 with no B2 counterpart: {len(c['b3_only_target_quotes'])}. Events with "
              f"no target-book moneyline ({len(b['no_ml_events'])}): {len(c['no_ml_events_in_b3'])} appear in B3; "
              f"target-book h2h B3 would add for them: {len(c['no_ml_target_h2h_added'])}.",
              f"- (b) Events whose n_books_h2h_any would change if B3 were added: {len(c['coverage_changes'])} "
              f"(report only; nothing merged).{' ' + c['coverage_error'] if c['coverage_error'] else ''}", ""]
        if c["discrepancies"]:
            L += [T(["event_id", "bookmaker", "market", "outcome", "field", "B2", "B3"],
                    [[d["key"][0][:8], d["key"][1], d["key"][2], d["key"][3], d["field"], d["b2"], d["b3"]]
                     for d in c["discrepancies"]]), ""]
        if c["b3_only_target_quotes"]:
            L += [T(["event_id", "bookmaker", "market", "outcome"],
                    [[k[0][:8], k[1], k[2], k[3]] for k in c["b3_only_target_quotes"]]), ""]
        if c["coverage_changes"]:
            L += [T(["event_id", "n_books_h2h_any", "with B3", "books added"],
                    [[x["event_id"][:8], x["n_books_h2h_any"], x["with_b3"], ", ".join(x["books_added"])]
                     for x in c["coverage_changes"]]), ""]
    L += ["## Integrity", "",
          f"- Hard stops checked before writing: pin, {EXPECTED_EVENTS} events ({len(ctx['inventory'])}), coverage "
          f"{EXPECTED_COVERAGE} ({coverage_triplet(ctx['inventory'])}), run-1 archive, run_diff, overrides, "
          f"protected and watched hashes unchanged since start: {ctx['hashes_unchanged_before_write']}.", ""]
    return "\n".join(L)


def legacy_reproduces(run1_links: pd.DataFrame, run1_md: str) -> bool:
    lines = set(run1_md.splitlines())
    return all(J.md_table(["x"] * 6, [r]).splitlines()[2] in lines for r in legacy_tier_rows(run1_links))


# --------------------------------------------------------------------------- writing and main
def write_run2(ctx: dict, out_dir: Path = OUT_RUN2, force: bool = False) -> list:
    out_dir = Path(out_dir)
    if out_dir.exists() and not force:
        raise HardStop(f"{out_dir} exists; refusing to overwrite (pass --force)")
    out_dir.mkdir(parents=True, exist_ok=force)
    b = ctx["b2b3"]["comparison"]
    files = {"event_game_links.csv": ctx["links"], "team_crosswalk_proposed.csv": ctx["crosswalk"],
             "name_collisions.csv": ctx["collisions"], "newly_matched_events.csv": ctx["newly_matched"],
             "run_diff.csv": ctx["run_diff"], "team_keyed_moneyline.csv": ctx["team_keyed_table"],
             "b2_b3_overlap.csv": b["rows"] if b is not None else pd.DataFrame()}
    written = []
    for name, df in files.items():
        p = out_dir / name
        df.to_csv(p, index=False, lineterminator="\n")
        written.append(p)
    p = out_dir / "join_revision.md"
    p.write_text(ctx["report"], encoding="utf-8", newline="\n")
    written.append(p)
    return written


def run2(force: bool = False, root: Path = ROOT, data_dir: Path | None = None, out_dir: Path = OUT_RUN2,
         run1_src: Path = J.OUT_DIR, run1_dir: Path = OUT_RUN1, overrides_path: Path = OVERRIDES_CSV) -> dict:
    """Parts 1-7 in order. Every hard stop is checked before anything is written to out_run2."""
    J.install_socket_guard()
    if Path(out_dir).exists() and not force:
        raise HardStop(f"{out_dir} exists; refusing to overwrite (pass --force)")
    try:
        pin = J.verify_pin()
    except Exception as exc:                                          # noqa: BLE001 - any failure is a stop
        raise HardStop(f"results repo pin check failed: {exc}") from exc
    bad = check_protected(root)
    if bad:
        raise HardStop(f"protected files differ from protected_hashes.txt: {bad}")
    watch = list(read_protected_hashes()) + WATCHED
    start = hash_paths(watch, root)
    archive = archive_run1(run1_src, run1_dir)
    run1_links = pd.read_csv(Path(run1_dir) / "event_game_links.csv", dtype=str, keep_default_na=False)
    n1 = int(run1_links["status"].isin(J.MATCHED).sum())
    if n1 != EXPECTED_RUN1_MATCHED:
        raise HardStop(f"run-1 archive has {n1} matched events, expected {EXPECTED_RUN1_MATCHED}")
    ctx = compute_run2(run1_links, root, data_dir, overrides_path)
    if len(ctx["inventory"]) != EXPECTED_EVENTS:
        raise HardStop(f"event count {len(ctx['inventory'])} != {EXPECTED_EVENTS}")
    if coverage_triplet(ctx["inventory"]) != EXPECTED_COVERAGE:
        raise HardStop(f"coverage {coverage_triplet(ctx['inventory'])} != {EXPECTED_COVERAGE}")
    now = hash_paths(watch, root)
    changed = [k for k in watch if now[k] != start[k]]
    if changed:
        raise HardStop(f"files changed during the run: {changed}")
    ctx.update(pin=pin, archive=archive, hashes_unchanged_before_write=not changed,
               legacy_reproduces_run1=legacy_reproduces(
                   run1_links, (Path(run1_dir) / "join_audit.md").read_text(encoding="utf-8")))
    ctx["report"] = build_revision_report(ctx)
    ctx["written"] = write_run2(ctx, out_dir, force)
    after = hash_paths(watch, root)
    changed = [k for k in watch if after[k] != start[k]]
    if changed:
        raise HardStop(f"files changed by writing out_run2 (outputs were written): {changed}")
    return ctx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Offline join revision (run 2): writes audit_join/out_run1 (once) "
                                             "and audit_join/out_run2.")
    ap.add_argument("--force", action="store_true", help="allow overwriting an existing audit_join/out_run2")
    args = ap.parse_args(argv)
    try:
        ctx = run2(force=args.force)
    except HardStop as exc:
        print(f"HARD STOP ({type(exc).__name__}): {exc}")
        return 2
    print(ctx["report"])
    for p in ctx["written"]:
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
