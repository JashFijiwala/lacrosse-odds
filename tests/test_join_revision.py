"""Tests for audit_join.join_revision.

Synthetic data only, except the REAL tests (11, 12: real files, skipped if absent; 13: file hashes). Every test
installs the socket guard. Nothing is written outside tmp_path; run 2 itself is never executed here.
"""
import copy
import hashlib
import json
import math
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.dont_write_bytecode = True

from audit_join import join_audit as J  # noqa: E402
from audit_join import join_revision as R  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "pilot" / "raw"
LEDGER = ROOT / "pilot" / "ledger" / "credit_ledger.csv"
RUN1_LINKS = J.OUT_DIR / "event_game_links.csv"
RUN1_GAMES = J.OUT_DIR / "canonical_games_2025_2026.csv"
HASHED = list(R.read_protected_hashes()) + ["audit_join/join_audit.py", "tests/test_join_audit.py",
                                            "audit_join/aliases.csv", "audit_join/manual_verification.csv"]
SESSION_START_HASHES = R.hash_paths(HASHED)          # captured when this module is collected


@pytest.fixture(autouse=True)
def socket_guard(monkeypatch):
    J.install_socket_guard(monkeypatch)
    for call in (lambda: socket.create_connection(("example.invalid", 80)),
                 lambda: socket.getaddrinfo("example.invalid", 80),
                 lambda: socket.socket().connect(("127.0.0.1", 9))):
        with pytest.raises(RuntimeError, match="network blocked"):
            call()
    yield


@pytest.fixture
def A():
    return J.import_analysis()[0]


# ----------------------------------------------------------------------------- helpers
NA = "NA"


def build(rows, season=2025):
    raw = pd.DataFrame([dict(zip(J.RESULT_COLUMNS, map(str, r))) for r in rows])
    kept, excl = J.prepare_results(raw, season)
    kept = J.clean_opponents(kept)
    games, _ = J.build_canonical_games(kept, excl)
    return games, kept


def event(home, away, ct, eid, books=3):
    return {"event_id": eid, "season": int(ct[:4]), "home_team": home, "away_team": away,
            "commence_sightings": [ct], "commence_latest": ct, "commence_conflict": False,
            "n_books_h2h_any": books, "n_books_h2h_at_latest_pregame": books, "n_pregame_snapshots_with_h2h": 1,
            "fav_context": ""}


def ov(name, regex):
    return {"odds_name": name, "results_regex": regex, "identity_note": f"note for {name}",
            "review_status": "PROPOSED_PENDING_HUMAN_REVIEW"}


TS = datetime(2026, 3, 28, 15, 55, tzinfo=timezone.utc)


def obs(A, eid, book, outcomes, snap_ts=TS, tts_s=7200, step="B2", home="X Hawks", away="Y Owls"):
    m = {"key": "h2h", "last_update": "2026-03-28T15:50:00Z", "outcomes": outcomes}
    return {"step": step, "season": 2026, "snap_ts": snap_ts, "event": {"id": eid, "home_team": home,
                                                                         "away_team": away},
            "event_id": eid, "book": book, "market": "h2h", "m": m, "cls": A.COMPLETE, "tts_s": tts_s,
            "pregame": tts_s > 0, "last_update": m["last_update"]}


def two(px, py, nx="X Hawks", ny="Y Owls"):
    return [{"name": nx, "price": px}, {"name": ny, "price": py}]


NAME_MAP = {"X Hawks": "X", "Y Owls": "Y"}
EV = {"event_id": "e1", "home_team": "X Hawks", "away_team": "Y Owls"}
EV_FLIPPED = {"event_id": "e1", "home_team": "Y Owls", "away_team": "X Hawks"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ----------------------------------------------------------------------------- 1
def test_01_archive_run1_copies_hashes_and_refuses(tmp_path):
    src, dst = tmp_path / "out", tmp_path / "out_run1"
    src.mkdir()
    a, b = b"x,y\n1,2\n", b"# report\n"
    (src / "a.csv").write_bytes(a)
    (src / "b.md").write_bytes(b)
    info = R.archive_run1(src, dst)
    assert info["action"] == "created archive"
    assert (dst / "a.csv").read_bytes() == a and (dst / "b.md").read_bytes() == b
    assert (dst / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines() == [f"{sha(a)}  a.csv", f"{sha(b)}  b.md"]
    assert R.archive_run1(src, dst)["action"] == "verified existing archive"
    (src / "a.csv").write_bytes(b"changed")                      # run-1 output changed -> refuse, never overwrite
    with pytest.raises(R.ArchiveError, match="no longer matches"):
        R.archive_run1(src, dst)
    assert (dst / "a.csv").read_bytes() == a
    (src / "a.csv").write_bytes(a)
    (dst / "b.md").write_bytes(b"tampered")                      # tampered archive -> refuse
    with pytest.raises(R.ArchiveError, match="does not match"):
        R.archive_run1(src, dst)
    (dst / "b.md").write_bytes(b)
    (dst / "extra.txt").write_bytes(b"x")                        # unlisted file in the archive -> refuse
    with pytest.raises(R.ArchiveError, match="unlisted"):
        R.archive_run1(src, dst)
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(R.ArchiveError, match="without SHA256SUMS"):
        R.archive_run1(src, bare)
    assert (src / "a.csv").read_bytes() == a and (src / "b.md").read_bytes() == b


# ----------------------------------------------------------------------------- 2
def test_02_resolve_with_overrides():
    teams = {"Army West Point", "Alpha", "Beta U", "Gamma St.", "Gamma St"}
    results = teams | {"Army Prep"}                              # Army Prep is an opponent-only string
    aliases = [(J.normalize_name("univ"), J.normalize_name("u"))]
    odds = ["Army Knights", "Alpha Wolves", "Beta U", "Beta Univ. Bears", "Gamma St"]
    frozen = J.resolve_names(odds, results, aliases)
    assert [frozen[n]["method"] for n in odds] == ["", "PREFIX", "EXACT", "ALIAS", "EXACT"]
    assert frozen["Gamma St"]["status"] == "AMBIGUOUS_NAME" and frozen["Army Knights"]["status"] == "UNRESOLVED"
    table = [ov("Army Knights", "^Army"), ov("Alpha Wolves", "^Beta"), ov("Beta U", "^Alpha"),
             ov("Beta Univ. Bears", "^Alpha"), ov("Gamma St", "^Gamma St\\.$")]
    out, log = R.resolve_with_overrides(odds, results, aliases, table, teams)
    r = out["Army Knights"]
    assert (r["status"], r["method"], r["results_name"]) == ("RESOLVED", "REVIEWED_OVERRIDE", "Army West Point")
    assert r["identity_note"] == "note for Army Knights"
    for n in ("Alpha Wolves", "Beta U", "Beta Univ. Bears", "Gamma St"):     # frozen output untouched
        assert out[n] == frozen[n]
    assert out["Gamma St"]["status"] == "AMBIGUOUS_NAME"
    by = {e["odds_name"]: e["outcome"] for e in log}
    assert by["Army Knights"] == "APPLIED" and all(v.startswith("NOT_APPLIED") for k, v in by.items() if k != "Army Knights")
    # opponent-only strings are never candidates: counted as a team, Army Prep would make two candidates
    with pytest.raises(R.OverrideError, match="matched 2 team-row names"):
        R.resolve_with_overrides(odds, results, aliases, table, teams | {"Army Prep"})
    # matching after J's own normalisation
    out2, _ = R.resolve_with_overrides(odds, results, aliases, [ov("ARMY  knights", "^Army")], teams)
    assert out2["Army Knights"]["method"] == "REVIEWED_OVERRIDE"
    # zero candidates -> error, nothing picked
    with pytest.raises(R.OverrideError, match="matched 0 team-row names"):
        R.resolve_with_overrides(["Queens University Royals"], results, aliases,
                                 [ov("Queens University Royals", "^Queens")], teams)
    # two candidates -> error listing both
    t2 = teams | {"St. John's (NY)", "St John Fisher"}
    with pytest.raises(R.OverrideError, match=r"St John Fisher.*St\. John's \(NY\)"):
        R.resolve_with_overrides(["St. John's Red Storm"], t2, aliases, [ov("St. John's Red Storm", r"^St\.? John")], t2)
    # the committed table has exactly the four specified rows
    rows = R.load_overrides()
    assert [(x["odds_name"], x["results_regex"], x["review_status"]) for x in rows] == [
        ("Army Knights", "^Army", "PROPOSED_PENDING_HUMAN_REVIEW"),
        ("Loyola (MD) Greyhounds", "^Loyola", "PROPOSED_PENDING_HUMAN_REVIEW"),
        ("Queens University Royals", "^Queens", "PROPOSED_PENDING_HUMAN_REVIEW"),
        ("St. John's Red Storm", r"^St\.? John", "PROPOSED_PENDING_HUMAN_REVIEW")]


# ----------------------------------------------------------------------------- 3
def _links(rows):
    return pd.DataFrame(rows, columns=["event_id", "season", "home_team", "away_team", "status", "reason",
                                       "matched_game_key"])


def test_03_run_diff_assertion():
    run1 = _links([["e1", 2025, "Alpha", "Beta", "UNIQUE_MATCH", "", "2025|2025-03-01|Alpha|Beta"],
                   ["e2", 2025, "Army Knights", "Beta", "FAILED", "NAME_UNRESOLVED", ""],
                   ["e3", 2025, "Gamma", "Delta", "FAILED", "NO_GAME_FOR_PAIR", ""]])
    ok = run1.copy()
    ok.loc[1, ["status", "reason", "matched_game_key"]] = ["UNIQUE_MATCH", "", "2025|2025-03-01|Army West Point|Beta"]
    d = R.run_diff(run1, ok, {"Army Knights"})
    assert d["status_changed"].tolist() == [False, True, False]
    moved = ok.copy()                                             # a previously matched event changes key
    moved.loc[0, "matched_game_key"] = "2025|2025-03-02|Alpha|Beta"
    with pytest.raises(R.RunDiffError, match="e1"):
        R.run_diff(run1, moved, {"Army Knights"})
    lost = ok.copy()                                              # ... or status
    lost.loc[0, "status"] = "FAILED"
    with pytest.raises(R.RunDiffError, match="e1"):
        R.run_diff(run1, lost, {"Army Knights"})
    other = ok.copy()                                             # a non-override event changes
    other.loc[2, ["status", "matched_game_key"]] = ["UNIQUE_MATCH", "2025|2025-03-01|Delta|Gamma"]
    with pytest.raises(R.RunDiffError, match="e3"):
        R.run_diff(run1, other, {"Army Knights"})
    with pytest.raises(R.RunDiffError, match="e2"):               # change without the name being an override
        R.run_diff(run1, ok, set())
    with pytest.raises(R.RunDiffError, match="event sets differ"):
        R.run_diff(run1, ok.iloc[:2], {"Army Knights"})


# ----------------------------------------------------------------------------- 4
D = "2025-03-01"
SAMPLE_ROWS = [
    (D, "Ash", "Birch", "Home", 10, 8, NA), (D, "Birch", "Ash", "Away", 8, 10, NA),            # A_HOME
    (D, "Cedar", "Dogwood", "Home", 7, 6, NA), (D, "Dogwood", "Cedar", "Home", 6, 7, NA),      # BOTH_HOME
    (D, "Elm", "Fir", "Away", 9, 5, NA), (D, "Fir", "Elm", "Away", 5, 9, NA),                  # BOTH_AWAY
    (D, "Gum", "Hazel", "Home", 9, 7, NA),                                                    # single, reliable
    (D, "Ivy", "Juniper", "Away", 12, 9, 1),                                                  # single, OT margin 3
    (D, "Kauri", "Larch", "Home", 5, 5, NA),                                                  # single, tie
    (D, "Maple", "Nutmeg", "Home", 10, 8, NA), (D, "Nutmeg", "Maple", "Away", 9, 10, NA),      # SCORE_CONFLICT
    (D, "Oak", "Pine", "Home", 11, 4, NA), (D, "Pine", "Oak", "Away", 4, 11, NA),              # 0 books
    (D, "Quince", "Rowan", "Home", 6, 3, NA), (D, "Rowan", "Quince", "Away", 3, 6, NA)]        # control
SAMPLE_EVENTS = [("Birch", "Ash", "dis", 3), ("Cedar", "Dogwood", "conf", 3), ("Elm", "Fir", "neu", 2),
                 ("Gum", "Hazel", "rel", 1), ("Ivy", "Juniper", "unrel_ot", 3), ("Kauri", "Larch", "unrel_tie", 3),
                 ("Maple", "Nutmeg", "sc", 3), ("Oak", "Pine", "zero", 0), ("Quince", "Rowan", "ctl", 3)]


def _sample(flip=False):
    games, kept = build(SAMPLE_ROWS)
    names = J.results_canonical_names(kept)
    evs = [event(a, h, f"{D}T18:00:00Z", eid, n) if flip else event(h, a, f"{D}T18:00:00Z", eid, n)
           for h, a, eid, n in SAMPLE_EVENTS]
    res = J.resolve_names([e["home_team"] for e in evs] + [e["away_team"] for e in evs], names, [])
    links = J.match_events(evs, res, games, names)
    return R.add_sample_flags(R.add_run2_columns(links, res, games)).set_index("event_id")


def test_04_sample_logic():
    s = _sample()
    assert s.loc["dis", "orientation"] == "DISAGREES" and s.loc["conf", "orientation"] == "CONFLICT"
    assert s.loc["sc", "result_quality"] == "SCORE_CONFLICT"
    assert s.loc["dis", "home_team_results"] == "Ash" and s.loc["dis", "feed_home_label_agrees_with_results"] == "DISAGREES"
    assert s.loc["conf", "home_team_results"] == "" and s.loc["rel", "home_team_results"] == ""
    prim = set(s.index[s["PRIMARY"]])
    assert prim == {"dis", "conf", "neu", "ctl"}                  # DISAGREES and CONFLICT are IN
    assert "sc" not in prim and "zero" not in prim
    sens = set(s.index[s["SENSITIVITY"]])
    assert sens == prim | {"rel"} and "rel" not in prim
    assert not ({"unrel_ot", "unrel_tie"} & sens)
    vq = set(s.index[s["VENUE_QUALIFIED"]])
    assert vq == {"dis", "ctl"}                                   # excluded only through venue_class
    assert all(s.loc[e, "venue_class"] in ("A_HOME", "B_HOME") for e in vq)
    assert (s["VENUE_QUALIFIED"] == (s["PRIMARY"] & s["venue_class"].isin(["A_HOME", "B_HOME"]))).all()
    assert set(s.index[s["NEUTRAL_CANDIDATE"]]) == {"neu"} and set(s.index[s["VENUE_CONFLICT"]]) == {"conf"}
    # the feed's home label never changes membership
    f = _sample(flip=True)
    assert f.loc["dis", "orientation"] == "AGREES" and f.loc["ctl", "orientation"] == "DISAGREES"
    assert f.loc["dis", "home_team_results"] == "Ash"
    pd.testing.assert_frame_equal(s[R.SAMPLE_FLAGS], f[R.SAMPLE_FLAGS])


# ----------------------------------------------------------------------------- 5
def test_05_team_keyed_hand_calculation(A):
    tk = R.team_keyed_moneyline(EV, [obs(A, "e1", "draftkings", two(150, -180))], NAME_MAP, A)
    b = tk["books"]["draftkings"]
    assert b["pi"]["X"] == pytest.approx(0.4) and b["pi"]["Y"] == pytest.approx(180 / 280)
    assert round(b["pi"]["Y"], 4) == 0.6429
    assert round(b["p"]["X"], 4) == 0.3836 and round(b["p"]["Y"], 4) == 0.6164
    assert b["p"]["X"] + b["p"]["Y"] == pytest.approx(1.0)
    # hold = 1 - 1/(pi_A + pi_B) = 1 - 1/1.042857 = 0.0411 (the spec's "3.97%" does not follow from its formula)
    assert b["hold"] == pytest.approx(1 - 1 / (0.4 + 180 / 280)) and round(b["hold"], 4) == 0.0411
    assert b["hold"] == pytest.approx(A.margin(150, -180)[2])
    assert b["tts_hours"] == pytest.approx(2.0) and b["snap_ts"] == TS and tk["mismatches"] == []


# ----------------------------------------------------------------------------- 6
def test_06_flip_test(A):
    o = [obs(A, "e1", "draftkings", two(150, -180)), obs(A, "e1", "fanduel", list(reversed(two(140, -170))))]
    t1 = R.team_keyed_moneyline(EV, o, NAME_MAP, A)
    t2 = R.team_keyed_moneyline(EV_FLIPPED, o, NAME_MAP, A)       # wrong odds home label, same outcome names
    assert {k: v["p"] for k, v in t1["books"].items()} == {k: v["p"] for k, v in t2["books"].items()}
    assert t1["books"]["draftkings"]["p"]["X"] == pytest.approx(0.383562, abs=1e-6)
    assert R.consensus(t1) == R.consensus(t2)
    for y in (0, 1):
        assert R.brier(t1["books"]["draftkings"]["p"]["X"], y) == R.brier(t2["books"]["draftkings"]["p"]["X"], y)
        assert R.log_loss(t1["books"]["draftkings"]["p"]["X"], y) == R.log_loss(t2["books"]["draftkings"]["p"]["X"], y)


# ----------------------------------------------------------------------------- 7
def test_07_outcome_name_mismatch_reported_not_guessed(A):
    o = [obs(A, "e1", "draftkings", two(150, -180)), obs(A, "e1", "fanduel", two(150, -180, nx="Z Bears"))]
    tk = R.team_keyed_moneyline(EV, o, NAME_MAP, A)
    assert set(tk["books"]) == {"draftkings"}
    assert [(m["book"], m["reason"]) for m in tk["mismatches"]] == [("fanduel", "OUTCOME_NAME_NOT_AN_EVENT_TEAM")]
    assert R.consensus(tk) is None


# ----------------------------------------------------------------------------- 8
def test_08_antisymmetry_of_losses():
    for pa in (0.01, 0.2, 0.3836, 0.5, 0.77, 0.999):
        for ya in (0, 1):
            assert R.brier(pa, ya) == pytest.approx(R.brier(1 - pa, 1 - ya))
            assert R.log_loss(pa, ya) == pytest.approx(R.log_loss(1 - pa, 1 - ya))
    assert R.log_loss(0.0, 1) == pytest.approx(-math.log(1e-6))       # clipped only for the log


# ----------------------------------------------------------------------------- 9
def test_09_consensus_and_coverage(A):
    multi = R.team_keyed_moneyline(EV, [obs(A, "e1", "draftkings", two(150, -180)),
                                        obs(A, "e1", "fanduel", two(140, -170))], NAME_MAP, A)
    single = R.team_keyed_moneyline({**EV, "event_id": "e2"}, [obs(A, "e2", "betmgm", two(150, -180))], NAME_MAP, A)
    none = R.team_keyed_moneyline({**EV, "event_id": "e3"}, [], NAME_MAP, A)
    c = R.consensus(multi)
    p1, p2 = multi["books"]["draftkings"]["p"]["X"], multi["books"]["fanduel"]["p"]["X"]
    assert c["X"] == pytest.approx((p1 + p2) / 2) and c["X"] + c["Y"] == pytest.approx(1.0)
    assert R.consensus(single) is None and R.consensus(none) is None
    assert sum(R.consensus(t) is not None for t in (multi, single, none)) == 1
    assert R.cutoff_check(multi) == {"same_snapshot": True, "max_tts_diff_hours": 0.0}
    assert R.cutoff_check(single) is None
    # event_weighted picks the latest pregame snapshot per book; a different cutoff is reported
    late = R.team_keyed_moneyline(EV, [obs(A, "e1", "draftkings", two(150, -180)),
                                       obs(A, "e1", "fanduel", two(140, -170), snap_ts=TS - timedelta(hours=3),
                                           tts_s=7200 + 3 * 3600)], NAME_MAP, A)
    assert R.cutoff_check(late) == {"same_snapshot": False, "max_tts_diff_hours": pytest.approx(3.0)}


# ----------------------------------------------------------------------------- 10
def _ev(eid, books):
    return {"id": eid, "sport_key": "lacrosse_ncaa", "commence_time": "2026-03-28T18:00:00Z", "home_team": "X Hawks",
            "away_team": "Y Owls", "bookmakers": books}


def _bk(key, px, py, lu="2026-03-28T15:50:00Z"):
    return {"key": key, "title": key, "last_update": lu,
            "markets": [{"key": "h2h", "last_update": lu, "outcomes": two(px, py)}]}


def test_10_b2_b3_comparison(tmp_path, A):
    ts = "2026-03-28T15:55:00Z"
    b2 = {"timestamp": ts, "data": [_ev("e1", [_bk("draftkings", 150, -180), _bk("betmgm", 145, -175)]),
                                    _ev("e2", [])]}
    b3 = {"timestamp": ts, "data": [_ev("e1", [_bk("draftkings", 150, -180), _bk("betmgm", 145, -190),
                                               _bk("fliff", 155, -185)]),
                                    _ev("e2", [_bk("fanduel", 120, -150)])]}
    for name, obj in (("b2.json", b2), ("b3.json", b3)):
        (tmp_path / name).write_text(json.dumps(obj), encoding="utf-8")
    b2r, b3r = R._read_json(tmp_path / "b2.json"), R._read_json(tmp_path / "b3.json")
    books = {"e1": {"draftkings", "betmgm"}}
    before = copy.deepcopy(books)
    c = R.compare_b2_b3(b2r, b3r, books, ["e2"], A)
    assert c["same_returned_timestamp"] is True
    assert (c["keys_both"], c["keys_only_b2"], c["keys_only_b3"]) == (4, 0, 4)
    assert c["identical"]["price"] == 3 and c["identical"]["market_last_update"] == 4
    assert [(d["key"], d["field"], d["b2"], d["b3"]) for d in c["discrepancies"]] == [
        (("e1", "betmgm", "h2h", "Y Owls"), "price", -175, -190)]
    assert "fliff" not in J.TARGET_BOOKS
    assert c["added_non_target"] == [("e1", "fliff", "h2h")] and c["added_target"] == [("e2", "fanduel", "h2h")]
    assert c["b3_only_target_quotes"] == [("e2", "fanduel", "h2h", "X Hawks"), ("e2", "fanduel", "h2h", "Y Owls")]
    assert c["no_ml_events_in_b3"] == ["e2"] and c["no_ml_target_h2h_added"] == [("e2", "fanduel", "h2h")]
    assert c["coverage_changes"] == [{"event_id": "e2", "n_books_h2h_any": 0, "with_b3": 1, "books_added": ["fanduel"]}]
    assert books == before                                        # flagged, never merged into coverage
    rows = c["rows"]
    assert len(rows) == 8 and rows.loc[rows["bookmaker"] == "fliff", "target_book"].eq(False).all()


# ----------------------------------------------------------------------------- 14 (extra, synthetic smoke)
def test_14_report_and_writer_smoke(tmp_path, A):
    games, kept = build(SAMPLE_ROWS)
    names = J.results_canonical_names(kept)
    evs = [event(h, a, f"{D}T18:00:00Z", eid, n) for h, a, eid, n in SAMPLE_EVENTS]
    res = J.resolve_names([e["home_team"] for e in evs] + [e["away_team"] for e in evs], names, [])
    links, unmatched = J.apply_manual_verification(J.match_events(evs, res, games, names), tmp_path / "none.csv")
    links = R.add_sample_flags(R.add_run2_columns(links, res, games))
    run1 = links[J.LINK_COLUMNS + J.LINK_EXTRA_COLUMNS].astype(str)
    run1.loc[run1["event_id"] == "rel", ["status", "reason", "matched_game_key"]] = ["FAILED", "NAME_UNRESOLVED", ""]
    diff = R.run_diff(run1, links, {"Gum"})
    tk = {"ctl": R.team_keyed_moneyline({"event_id": "ctl", "home_team": "Quince", "away_team": "Rowan"},
                                        [obs(A, "ctl", "draftkings", two(-200, 160, "Quince", "Rowan")),
                                         obs(A, "ctl", "fanduel", two(-190, 150, "Quince", "Rowan"))],
                                        {"Quince": "Quince", "Rowan": "Rowan"}, A)}
    b2 = {"timestamp": "t", "data": [_ev("e1", [_bk("draftkings", 150, -180)])]}
    b3 = {"timestamp": "t", "data": [_ev("e1", [_bk("draftkings", 150, -185), _bk("fliff", 150, -180)])]}
    ctx = {"pin": "synthetic", "archive": {"action": "created archive", "sums": {"a": "b"}},
           "analysis_info": {"ledger_module": "stand-in"}, "links": links, "run1_links": run1, "run_diff": diff,
           "override_log": [{"odds_name": "Gum", "results_regex": "^Gum", "candidates": "Gum", "chosen": "Gum",
                             "outcome": "APPLIED", "identity_note": "n", "review_status": "PROPOSED_PENDING_HUMAN_REVIEW",
                             "also_resolved_from": ""}],
           "crosswalk": J.build_crosswalk(evs, res, {2025: names}), "collisions": J.collision_log(res),
           "newly_matched": R.newly_matched(diff, links), "team_keyed": tk, "team_keyed_table": R.team_keyed_table(links, tk),
           "b2b3": {"meta_rows": [["B1", "f.json", "t", "h2h", "us", 1, "abc", 30, "0-30", "30"]],
                    "comparison": R.compare_b2_b3(b2, b3, {"e1": {"draftkings"}}, [], A), "no_ml_events": []},
           "inventory": evs, "manual_unmatched": unmatched, "legacy_reproduces_run1": True,
           "hashes_unchanged_before_write": True}
    rep = R.build_revision_report(ctx)
    for h in ("## A.", "## B.", "## C.", "## D.", "## E.", "## F.", "## G.", "## H.", R.VERIFICATION_LINE,
              "descriptive pipeline check, small number of game days"):
        assert h in rep
    assert len(ctx["newly_matched"]) == 1 and "Discrepancies: 1" in rep
    assert "coverage effect not computed" in rep                  # unparseable timestamp is reported, not crashed on
    ctx["report"] = rep
    out = tmp_path / "out_run2"
    written = R.write_run2(ctx, out)
    assert sorted(p.name for p in written) == sorted(
        ["event_game_links.csv", "team_crosswalk_proposed.csv", "name_collisions.csv", "newly_matched_events.csv",
         "run_diff.csv", "team_keyed_moneyline.csv", "b2_b3_overlap.csv", "join_revision.md"])
    with pytest.raises(R.HardStop, match="refusing to overwrite"):
        R.write_run2(ctx, out)
    assert len(R.write_run2(ctx, out, force=True)) == 8
    head = pd.read_csv(out / "event_game_links.csv", nrows=0).columns.tolist()
    assert head[:len(J.LINK_COLUMNS + J.LINK_EXTRA_COLUMNS)] == J.LINK_COLUMNS + J.LINK_EXTRA_COLUMNS
    assert set(R.RUN2_EXTRA_COLUMNS + R.SAMPLE_FLAGS) <= set(head)


# ----------------------------------------------------------------------------- REAL
def _real_available():
    return (J.results_path(2025).exists() and J.results_path(2026).exists() and RAW_DIR.exists() and LEDGER.exists()
            and RUN1_LINKS.exists() and RUN1_GAMES.exists())


@pytest.fixture(scope="module")
def real():
    if not _real_available():
        pytest.skip("results repo, pilot raw/ledger or audit_join/out run-1 files are absent; real-data test skipped")
    J.install_socket_guard()
    J.verify_pin()
    A, info = J.import_analysis()
    res = R.load_results_all()
    inv, snaps = J.build_event_inventory(A, ROOT)
    return {"A": A, "info": info, "res": res, "inv": inv, "obs": A.observations(snaps)}


def test_11_real_four_known_events_run2_path(real):
    assert real["info"]["new_forbidden_modules"] == []
    res, inv = real["res"], real["inv"]
    aliases = J.load_aliases()
    frozen = J.resolve_names([e["home_team"] for e in inv] + [e["away_team"] for e in inv], res["results_names"], aliases)
    ok = [e for e in inv if frozen[e["home_team"]]["status"] == frozen[e["away_team"]]["status"] == "RESOLVED"]
    names = [e["home_team"] for e in ok] + [e["away_team"] for e in ok]
    resolutions, log = R.resolve_with_overrides(names, res["results_names"], aliases, R.load_overrides(),
                                                res["team_names"])
    assert log == [] and all(resolutions[n] == frozen[n] for n in set(names))
    links = J.match_events(ok, resolutions, res["games"], res["results_names"])
    links, unmatched = J.apply_manual_verification(links)
    links = R.add_sample_flags(R.add_run2_columns(links, resolutions, res["games"]))
    picks = [("Air Force", "Quinnipiac"), ("Denver", "Marist"), ("Syracuse", "Duke"), ("Navy", "Colgate")]
    four = pd.concat([links[links["home_team"].str.contains(h, regex=False)
                            & links["away_team"].str.contains(a, regex=False)] for h, a in picks])
    assert len(four) == 4 and unmatched == []
    got = [(r.status, r.matched_date, int(r.home_goals), int(r.away_goals), int(r.ot_periods)) for r in four.itertuples()]
    assert got == [("UNIQUE_MATCH", "2025-02-28", 21, 8, 0), ("DATE_SHIFTED", "2025-02-28", 18, 5, 0),
                   ("UNIQUE_MATCH", "2026-03-28", 16, 15, 0), ("UNIQUE_MATCH", "2026-03-28", 15, 16, 2)]
    navy = four.iloc[3]
    assert (navy.team_a, navy.goals_a, navy.team_b, navy.goals_b) == ("Colgate", 16, "Navy", 15)
    assert set(links.loc[links["independently_verified"] == "yes", "event_id"]) == set(four["event_id"])
    for m in ("src.odds_client", "src.halt"):
        assert m not in sys.modules
    assert not any(m.startswith("src.stage") for m in sys.modules)


def test_12_real_flip_invariance_on_run1_primary(real):
    A, res = real["A"], real["res"]
    run1 = pd.read_csv(RUN1_LINKS, dtype=str, keep_default_na=False)
    games1 = pd.read_csv(RUN1_GAMES, dtype=str, keep_default_na=False)
    frozen = J.resolve_names(list(run1["home_team"]) + list(run1["away_team"]), res["results_names"], J.load_aliases())
    flags = R.add_sample_flags(R.add_run2_columns(run1, frozen, games1))
    prim = flags[flags["PRIMARY"]]
    assert len(prim) > 0
    name_map = {n: r["results_name"] for n, r in frozen.items() if r["status"] == "RESOLVED"}
    mismatches = 0
    for ev in prim.to_dict("records"):
        t1 = R.team_keyed_moneyline(ev, real["obs"], name_map, A)
        t2 = R.team_keyed_moneyline({**ev, "home_team": ev["away_team"], "away_team": ev["home_team"]},
                                    real["obs"], name_map, A)
        assert {k: v["p"] for k, v in t1["books"].items()} == {k: v["p"] for k, v in t2["books"].items()}, ev["event_id"]
        assert len(t1["books"]) == int(ev["n_books_h2h_any"]) - len(t1["mismatches"])
        mismatches += len(t1["mismatches"])
    assert mismatches == 0, f"outcome-name mismatches on run-1 PRIMARY: {mismatches}"


def test_13_real_hashes_unchanged_since_session_start():
    now = R.hash_paths(HASHED)
    assert now == SESSION_START_HASHES
    exp = R.read_protected_hashes()
    assert len(exp) == 10 and all(now[k] == v for k, v in exp.items())
    assert "MISSING" not in now.values()
