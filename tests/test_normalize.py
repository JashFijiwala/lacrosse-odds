import ast
import csv
import hashlib
import json
from pathlib import Path

import pytest

from src import normalize as N
from src import stage_b as SB
from src.ledger import COLUMNS

PROJECT = Path(__file__).resolve().parents[1]
ODDS = "/v4/historical/sports/lacrosse_ncaa/odds"
HOME, AWAY = "Home U", "Away U"


def o(name, price, point=None):
    d = {"name": name, "price": price}
    if point is not None:
        d["point"] = point
    return d


def mk(key, outcomes, lu="2025-02-28T18:55:29Z"):
    return {"key": key, "last_update": lu, "outcomes": outcomes}


def h2h(home=HOME, away=AWAY, p1=-150, p2=125):
    return mk("h2h", [o(home, p1), o(away, p2)])


def spreads(p1, p2, home=HOME, away=AWAY):
    return mk("spreads", [o(home, -110, p1), o(away, -110, p2)])


def totals(over_pt=22.5, under_pt=22.5):
    return mk("totals", [o("Over", -105, over_pt), o("Under", -115, under_pt)])


def book(key, markets):
    return {"key": key, "title": key, "last_update": "2000-01-01T00:00:00Z", "markets": markets}


def ev(eid, home, away, commence, books):
    return {"id": eid, "sport_key": "lacrosse_ncaa", "commence_time": commence, "home_team": home,
            "away_team": away, "bookmakers": books}


EVENT = {"home_team": HOME, "away_team": AWAY}


# ------------------------------------------------------------ probabilities
def test_american_to_prob():
    assert N.american_to_prob(100) == pytest.approx(0.5)
    assert N.american_to_prob(-100) == pytest.approx(0.5)
    assert N.american_to_prob(625) == pytest.approx(100 / 725)
    assert N.american_to_prob(-1200) == pytest.approx(1200 / 1300)
    assert N.american_to_prob(150) == pytest.approx(0.4)
    assert N.american_to_prob(-150) == pytest.approx(0.6)
    for bad in (0, None, "x", True, float("nan")):
        assert N.american_to_prob(bad) is None


# ---------------------------------------------------------- classification
def test_classify_h2h():
    c = lambda m: N.classify_market(EVENT, m)  # noqa: E731
    assert c(h2h()) == N.COMPLETE
    assert c(h2h(p1=100, p2=-100)) == N.COMPLETE
    assert c(h2h(away="Wrong U")) == N.INCOMPLETE                  # wrong team name
    assert c(h2h(away=HOME)) == N.INCOMPLETE                       # duplicate team name
    assert c(mk("h2h", [o(HOME, -150)])) == N.INCOMPLETE           # missing outcome
    assert c(mk("h2h", [o(HOME, -150), o(AWAY, 125), o("Draw", 300)])) == N.INCOMPLETE
    assert c(h2h(p1=0)) == N.INCOMPLETE and c(h2h(p1="-150")) == N.INCOMPLETE and c(h2h(p1=-99)) == N.INCOMPLETE
    assert c(mk("h2h", [o(HOME, None), o(AWAY, 125)])) == N.INCOMPLETE
    assert c(None) == N.ABSENT


def test_classify_spreads_sum_to_zero_rule():
    c = lambda m: N.classify_market(EVENT, m)  # noqa: E731
    assert c(spreads(0, 0)) == N.COMPLETE                          # pick'em
    assert c(spreads(0.0, -0.0)) == N.COMPLETE
    assert c(spreads(2.5, -2.5)) == N.COMPLETE
    assert c(spreads(-2.5, 2.5)) == N.COMPLETE
    assert c(spreads(-2.5, -2.5)) == N.MISMATCHED                  # same sign
    assert c(spreads(1.5, -2.5)) == N.MISMATCHED                   # unequal
    assert c(mk("spreads", [o(HOME, -110, -1.5), o(AWAY, -110)])) == N.INCOMPLETE   # missing point
    assert c(spreads(-1.5, 1.5, away="Wrong U")) == N.INCOMPLETE
    assert c(mk("spreads", [o(HOME, -110, -1.5)])) == N.INCOMPLETE                  # missing outcome


def test_classify_totals():
    c = lambda m: N.classify_market(EVENT, m)  # noqa: E731
    assert c(totals()) == N.COMPLETE
    assert c(totals(22.5, 23.5)) == N.INCOMPLETE
    assert c(mk("totals", [o("Over", -105, 22.5), o("Over", -115, 22.5)])) == N.INCOMPLETE


ANCHOR_CASES = [h2h(), h2h(away="Wrong U"), h2h(away=HOME), h2h(p1=0), mk("h2h", [o(HOME, -150)]),
                spreads(-1.5, 1.5), spreads(-2.5, -2.5), spreads(1.5, -2.5), spreads(2.5, -2.5),
                totals(), totals(22.5, 23.5), mk("spreads", [o(HOME, -110, -1.5)])]


def test_parity_with_stage_b_except_pickem():
    loaded = N.load_stage_b_classifier()
    for m in ANCHOR_CASES:
        ours = N.classify_market(EVENT, m)
        theirs = SB.classify_market(m["key"], m, HOME, AWAY)
        assert loaded(m["key"], m, HOME, AWAY) == theirs          # the ast loader is faithful
        assert ours == theirs, m
    pickem = spreads(0, 0)
    assert N.classify_market(EVENT, pickem) == N.COMPLETE
    assert SB.classify_market("spreads", pickem, HOME, AWAY) == SB.MISMATCHED   # the one intended difference


def test_parity_report_lists_differing_events_not_hiding_them():
    snap = {"step": "B1", "events": [
        ev("anchor", HOME, AWAY, "2025-02-28T19:00:00Z", [book("dk", [h2h(), spreads(-1.5, 1.5)])]),
        ev("other", HOME, AWAY, "2025-02-28T20:00:00Z", [book("dk", [spreads(0, 0)])])]}
    par = N.parity([snap], {"B1": "anchor"}, N.load_stage_b_classifier())
    assert par["compared"] == 3 and par["anchor_compared"] == 2 and par["anchor_diffs"] == []
    assert [(d["event_id"], d["market"], d["normalize"], d["stage_b"]) for d in par["diffs"]] == [
        ("other", "spreads", N.COMPLETE, N.MISMATCHED)]


# ----------------------------------------------------------------- coverage
def test_any_market_vs_h2h_only_counts_differ():
    events = [ev("e1", HOME, AWAY, "2025-03-01T17:00:00Z", [book("draftkings", [h2h(), spreads(-1.5, 1.5), totals()])]),
              ev("e2", HOME, AWAY, "2025-03-01T18:00:00Z", [book("draftkings", [spreads(-1.5, 1.5), totals()])]),
              ev("e3", HOME, AWAY, "2025-03-01T19:00:00Z", [book("draftkings", [h2h(away="Wrong U")])]),
              ev("e4", HOME, AWAY, "2025-03-01T20:00:00Z", [])]
    cov = N.coverage_table(events)
    assert cov["draftkings"] == {"any": 3, "h2h": 1, "spreads": 2, "totals": 2}
    assert cov["fliff"] == {"any": 0, "h2h": 0, "spreads": 0, "totals": 0}
    assert [x["event_id"] for x in N.spreads_totals_without_h2h({"events": events})] == ["e2"]


def test_target_book_distribution_excludes_fliff():
    events = [ev("e1", HOME, AWAY, "2025-03-01T17:00:00Z",
                 [book(b, [h2h()]) for b in ("draftkings", "fanduel", "betmgm", "fliff")]),
              ev("e2", HOME, AWAY, "2025-03-01T18:00:00Z", [book("fliff", [h2h()])]),
              ev("e3", HOME, AWAY, "2025-03-01T19:00:00Z", [book("fanduel", [h2h()]), book("betmgm", [h2h()])])]
    assert N.target_book_distribution(events) == {0: 1, 1: 0, 2: 1, 3: 1}


def test_b2_b3_reconciliation_reports_data_only():
    e_b2 = [ev("e1", HOME, AWAY, "t", [book("draftkings", [h2h(), totals()]), book("fanduel", [spreads(-1, 1)])]),
            ev("e2", HOME, AWAY, "t", [book("betmgm", [h2h()])])]
    e_b3 = [ev("e1", HOME, AWAY, "t", [book("draftkings", [h2h()]), book("fliff", [h2h()])])]
    b2 = {"meta": {"timestamp": "T"}, "events": e_b2}
    b3 = {"meta": {"timestamp": "T"}, "events": e_b3}
    rec = N.reconcile_b2_b3(b2, b3)
    assert rec["same_timestamp"] and rec["pairs_b2"] == 3 and rec["pairs_b3_h2h"] == 2
    got = {(m["event_id"], m["bookmaker"]): (m["b2_markets"], m["b3_state"]) for m in rec["missing"]}
    assert got == {("e1", "fanduel"): ("spreads", "book has no entry for event in B3"),
                   ("e2", "betmgm"): ("h2h", "event not in B3 payload")}
    assert rec["b3_only"] == {"fliff": 1}


# ------------------------------------------------------------------- identity
def test_identity_checks():
    snap = {"meta": {"timestamp": "2025-02-28T18:55:39Z"}, "events": [
        ev("a", "H1", "A1", "2025-02-28T19:00:00Z", []),
        ev("a", "H2", "A2", "2025-02-28T19:00:00Z", []),                          # duplicate event_id
        ev("b", "H1", "A1", "2025-02-28T19:00:00Z", []),                          # duplicate triple with "a"
        ev("c", "H3", "A3", "2025-02-28T18:00:00Z", []),                          # before the snapshot
        ev("d", "H4", "A4", "2025-02-28T19:00:00Z", [book("dk", [h2h(), h2h()]), book("dk", [])])]}
    c = N.identity_checks(snap)
    assert c["dup_event_ids"] == {"a": 2}
    assert list(c["dup_triples"].values()) == [["a", "b"]]
    assert [x["event_id"] for x in c["before_snapshot"]] == ["c"] and c["before_snapshot"][0]["seconds_before"] == 3339
    assert c["dup_event_bookmaker"] == 1 and c["dup_event_bookmaker_market"] == 1


# --------------------------------------------------------------------- Cradl
def snap_for_cradl():
    e1 = ev("q", "Quinnipiac Bobcats", "Air Force Falcons", "2025-02-28T19:00:00Z",
            [book("draftkings", [mk("h2h", [o("Quinnipiac Bobcats", 625), o("Air Force Falcons", -1200)]),
                                 mk("spreads", [o("Quinnipiac Bobcats", -110, 4.5), o("Air Force Falcons", -110, -4.5)]),
                                 mk("totals", [o("Over", -105, 22.5), o("Under", -115, 22.5)])])])
    return [{"step": "B1", "meta": {"timestamp": "2025-02-28T18:55:39Z"}, "events": [e1], "error": ""}]


def cr(book_="draftkings", market="h2h", team="Quinnipiac", price="625", point="", date="2025-02-28",
       label="Quinnipiac vs Air Force"):
    return {"game_label": label, "game_date": date, "book": book_, "market": market, "team": team,
            "american_price": price, "point": point, "source_note": ""}


def test_label_quote():
    assert N.label_quote(625, None, 625, None) == "exact"
    assert N.label_quote(630, None, 625, None) == "near"           # |delta pi| about 0.0009
    assert N.label_quote(500, None, 625, None) == "different"
    assert N.label_quote(625, 4.5, 625, 5.5) == "different"         # same price, different point
    assert N.label_quote(630, 4.5, 625, 5.5) == "different"         # near price but point differs
    assert N.label_quote(630, 4.5, 625, 4.5) == "near"


def test_reconcile_cradl_labels():
    rows = [cr(), cr(price="630"), cr(price="500"), cr(book_="caesars"), cr(team="Duke", label="Duke vs Navy"),
            cr(date="2025-03-01"), cr(team="Air Force", price="-1200"), cr(market="spreads", point="4.5", price="-110"),
            cr(market="spreads", point="3.5", price="-110"), cr(market="totals", team="Over", point="22.5", price="-105"),
            cr(price="not a price"), cr(market="moneyline")]
    res = N.reconcile_cradl(snap_for_cradl(), rows)
    assert [r["label"] for r in res] == ["exact", "near", "different", "not_found", "not_found", "not_found",
                                         "exact", "exact", "different", "exact", "different", "not_found"]
    assert res[0]["gap_s"] == 261 and res[0]["snapshot_step"] == "B1" and res[0]["our_price"] == 625
    assert res[1]["delta_pi"] == "+0.0009" and res[2]["delta_pi"] == "-0.0287"   # our pi minus Cradl's
    assert "no snapshot has this book" in res[3]["note"]
    assert "no event" in res[4]["note"] and "no event" in res[5]["note"]


def test_cradl_unicode_minus_and_plus_prefix():
    assert N.parse_price("−1200") == -1200 and N.parse_price("+625") == 625 and N.parse_price("x") is None
    assert N.parse_point("−4.5") == -4.5 and N.parse_point("") is None


# ----------------------------------------------------------------- end to end
def build_root(tmp_path, cradl=True):
    root = tmp_path / "proj"
    for d in ("pilot/ledger", "pilot/raw", "pilot/normalized"):
        (root / d).mkdir(parents=True)
    q = ev("qid", "Quinnipiac Bobcats", "Air Force Falcons", "2025-02-28T19:00:00Z", [
        book("draftkings", [mk("h2h", [o("Quinnipiac Bobcats", 625), o("Air Force Falcons", -1200)]),
                            mk("spreads", [o("Quinnipiac Bobcats", -110, 0), o("Air Force Falcons", -110, 0)]),
                            totals()]),
        book("fanduel", [spreads(-1, 1, "Quinnipiac Bobcats", "Air Force Falcons")])])
    other = ev("oid", "Duke Blue Devils", "Navy Midshipmen", "2025-02-28T20:00:00Z", [])
    n = ev("nid", "Vermont Catamounts", "NJIT Highlanders", "2026-03-28T16:00:00Z", [
        book("draftkings", [mk("h2h", [o("Vermont Catamounts", -810), o("NJIT Highlanders", 500)])]),
        book("fanduel", [mk("h2h", [o("Vermont Catamounts", -900), o("NJIT Highlanders", 600)])])])
    n3 = ev("nid", "Vermont Catamounts", "NJIT Highlanders", "2026-03-28T16:00:00Z", [
        book("draftkings", [mk("h2h", [o("Vermont Catamounts", -810), o("NJIT Highlanders", 500)])]),
        book("fliff", [mk("h2h", [o("Vermont Catamounts", -800), o("NJIT Highlanders", 480)])])])
    payloads = {
        "B1": ("2025-02-28T18:59:00Z", "us", "h2h,spreads,totals", "2025-02-28T18:55:39Z", [q, other]),
        "B2": ("2026-03-28T15:59:00Z", "us", "h2h,spreads,totals", "2026-03-28T15:55:37Z", [n]),
        "B3": ("2026-03-28T15:59:00Z", "us,us2", "h2h", "2026-03-28T15:55:37Z", [n3]),
    }
    rows = []
    for i, (step, (date, regions, markets, ts, events)) in enumerate(payloads.items(), start=1):
        raw = root / "pilot" / "raw" / f"B_2026110{i}_{step}.json"
        raw.write_text(json.dumps({"timestamp": ts, "previous_timestamp": "prev-" + step, "next_timestamp": "next-" + step,
                                   "data": events}), encoding="utf-8")
        row = {c: "" for c in COLUMNS}
        row.update(stage="B", purpose=f"stage B step {step} anchor", endpoint=ODDS, status_code="200",
                   params_redacted_json=json.dumps({"date": date, "regions": regions, "markets": markets}),
                   raw_file=str(raw), actual_cost="30")
        rows.append(row)
    with open(root / "pilot" / "ledger" / "credit_ledger.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    (root / "pilot" / "normalized" / "anchors.json").write_text(json.dumps({"anchors": [
        {"anchor_id": "quinnipiac_air_force", "found": True, "event_id": "qid"},
        {"anchor_id": "njit_vermont", "found": True, "event_id": "nid"}]}))
    (root / "pilot" / "normalized" / "stage_a_events.csv").write_text(
        "probe_id,home_team,away_team\nP01,Zed U,Alpha U\nP02,Duke Blue Devils,Yale Bulldogs\n", encoding="utf-8")
    if cradl:
        (root / "pilot" / "cradl_observations.csv").write_text(
            "game_label,game_date,book,market,team,american_price,point,source_note\n"
            "Quinnipiac vs Air Force,2025-02-28,draftkings,h2h,Quinnipiac,625,,x\n"
            "NJIT vs Vermont,2026-03-28,draftkings,h2h,NJIT,495,,x\n"
            "NJIT vs Vermont,2026-03-28,draftkings,h2h,Vermont,-700,,x\n", encoding="utf-8")
    return root


def raw_hashes(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "pilot" / "raw").iterdir()}


def test_main_end_to_end(tmp_path, capsys):
    root = build_root(tmp_path)
    before = raw_hashes(root)
    assert N.main(["--root", str(root)]) == 0
    assert raw_hashes(root) == before                                   # raw files untouched
    norm = root / "pilot" / "normalized"

    with open(norm / "odds_long.csv", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        long_rows = list(reader)
    assert reader.fieldnames == ["snapshot_requested_date", "snapshot_timestamp", "previous_timestamp", "next_timestamp",
                                 "step", "event_id", "commence_time", "home_team", "away_team", "bookmaker",
                                 "region_requested", "market", "market_last_update", "outcome_name", "price_american",
                                 "point"]
    # B1: dk h2h 2 + spreads 2 + totals 2, fd spreads 2 = 8; B2: 4; B3: 4
    assert len(long_rows) == 16 and sum(r["step"] == "B1" for r in long_rows) == 8
    r = next(x for x in long_rows if x["step"] == "B1" and x["market"] == "h2h" and x["outcome_name"] == "Quinnipiac Bobcats")
    assert (r["snapshot_requested_date"], r["snapshot_timestamp"], r["market_last_update"], r["commence_time"]) == (
        "2025-02-28T18:59:00Z", "2025-02-28T18:55:39Z", "2025-02-28T18:55:29Z", "2025-02-28T19:00:00Z")
    assert (r["previous_timestamp"], r["next_timestamp"], r["region_requested"], r["price_american"], r["point"]) == (
        "prev-B1", "next-B1", "us", "625", "")
    pick = next(x for x in long_rows if x["step"] == "B1" and x["market"] == "spreads" and x["bookmaker"] == "draftkings")
    assert pick["point"] == "0" or pick["point"] == "0.0"

    teams = list(csv.DictReader(open(norm / "team_names.csv", encoding="utf-8", newline="")))
    names = [t["team_name"] for t in teams]
    assert names == sorted(names, key=lambda s: (s.casefold(), s)) and len(set(names)) == len(names)
    assert {"Zed U", "Navy Midshipmen"} <= set(names)
    assert next(t for t in teams if t["team_name"] == "Zed U") == {"team_name": "Zed U", "in_stage_a": "yes", "in_stage_b": "no"}

    text = (norm / "stage_b_normalization_report.md").read_text(encoding="utf-8")
    assert text.strip() in capsys.readouterr().out                      # the same text is printed
    for s in ("No results data is joined", "not games", "## Cradl reconciliation", "## B2 vs B3 same-snapshot"):
        assert s in text
    # the anchor's 0/0 spread is the one intended rule difference and is listed, not hidden
    assert "Anchor events differing: 1. All differences: 1." in text
    assert "| B1 | qid | yes | draftkings | spreads | complete_paired | present_mismatched |" in text
    assert "| draftkings | 1 | 1 | 1 | 1 |" in text                      # B1: any, h2h, spreads (0/0), totals
    assert "exact 1, near (|delta pi| <= 0.005, same point) 1, different 1, not_found 0" in text


def test_main_without_cradl_file_prints_skipped(tmp_path, capsys):
    root = build_root(tmp_path, cradl=False)
    assert N.main(["--root", str(root)]) == 0
    assert "no Cradl file; skipped" in capsys.readouterr().out


def test_main_without_ledger_errors_cleanly(tmp_path, capsys):
    assert N.main(["--root", str(tmp_path)]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_unreadable_raw_file_is_reported_not_fatal(tmp_path, capsys):
    root = build_root(tmp_path)
    next(iter((root / "pilot" / "raw").glob("*_B3.json"))).write_text("{not json")
    assert N.main(["--root", str(root)]) == 0
    assert "UNREADABLE" in capsys.readouterr().out


# ------------------------------------------------------------------ hygiene
def test_normalize_imports_no_network_or_client_modules():
    tree = ast.parse((PROJECT / "src" / "normalize.py").read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0] or "." + "".join(a.name for a in node.names))
    banned = {"requests", "httpx", "urllib", "urllib3", "socket", "http", "aiohttp", "dotenv",
              "odds_client", "stage_a", "stage_b", "ledger", "halt"}
    assert not (mods & banned), mods & banned
    for node in ast.walk(tree):                                        # no reference to the client in code
        assert not (isinstance(node, ast.Name) and node.id == "OddsClient")
        assert not (isinstance(node, ast.Attribute) and node.attr == "OddsClient")
        assert not (isinstance(node, ast.ImportFrom) and any(a.name == "OddsClient" for a in node.names))


@pytest.mark.skipif(not (PROJECT / "pilot" / "ledger" / "credit_ledger.csv").exists(), reason="no saved pilot data")
def test_parity_on_saved_anchor_events():
    snaps = N.good(N.load_snapshots(PROJECT))
    if not snaps:
        pytest.skip("no readable Stage B snapshots")
    par = N.parity(snaps, N.anchor_event_ids(PROJECT), N.load_stage_b_classifier())
    assert par["anchor_diffs"] == []
