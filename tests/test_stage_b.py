import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import halt as H
from src import stage_b as SB

from .conftest import TESTKEY, ok

Q_ID, Q_START = "b161009bd49621d1a075fbbd0d85ba48", "2025-02-28T19:00:00Z"
N_ID, N_START = "6524fd16e5b1aa78e411490d4de6f19f", "2026-03-28T16:00:00Z"
Q_HOME, Q_AWAY = "Quinnipiac Bobcats", "Air Force Falcons"
N_HOME, N_AWAY = "Vermont Catamounts", "NJIT Highlanders"


def write_anchors(root, q_start=Q_START, n_start=N_START):
    out = Path(root) / "pilot" / "normalized"
    out.mkdir(parents=True, exist_ok=True)
    (out / "anchors.json").write_text(json.dumps({"anchors": [
        {"anchor_id": "quinnipiac_air_force", "found": True, "event_id": Q_ID, "commence_time_utc": q_start},
        {"anchor_id": "njit_vermont", "found": True, "event_id": N_ID, "commence_time_utc": n_start},
    ]}))


def o(name, price, point=None):
    d = {"name": name, "price": price}
    if point is not None:
        d["point"] = point
    return d


def mkt(key, outcomes, last_update="2025-02-28T18:50:00Z"):
    return {"key": key, "last_update": last_update, "outcomes": outcomes}


def h2h(home, away, lu="2025-02-28T18:50:00Z"):
    return mkt("h2h", [o(home, -150), o(away, 125)], lu)


def full_book(key, home, away, lu="2025-02-28T18:50:00Z"):
    return {"key": key, "title": key, "last_update": "2020-01-01T00:00:00Z", "markets": [
        h2h(home, away, lu),
        mkt("spreads", [o(home, -110, -1.5), o(away, -110, 1.5)], lu),
        mkt("totals", [o("Over", -105, 22.5), o("Under", -115, 22.5)], lu),
    ]}


def event(eid, home, away, commence, books):
    return {"id": eid, "sport_key": "lacrosse_ncaa", "commence_time": commence,
            "home_team": home, "away_team": away, "bookmakers": books}


def payload(events, ts):
    return {"timestamp": ts, "previous_timestamp": "x-prev", "next_timestamp": "x-next", "data": events}


def q_payload(ts="2025-02-28T18:55:38Z", books=None):
    books = books if books is not None else [full_book("draftkings", Q_HOME, Q_AWAY),
                                             {"key": "fanduel", "markets": [h2h(Q_HOME, Q_AWAY)]}]
    other = event("other1", "Duke", "Navy", "2025-02-28T20:00:00Z",
                  [{"key": "betmgm", "markets": [h2h("Duke", "Navy")]}])
    return payload([event(Q_ID, Q_HOME, Q_AWAY, Q_START, books), other], ts)


def n_payload(ts="2026-03-28T15:55:38Z"):
    lu = "2026-03-28T15:50:00Z"
    return payload([event(N_ID, N_HOME, N_AWAY, N_START, [full_book("fanduel", N_HOME, N_AWAY, lu)])], ts)


class Server:
    """Fake odds API. Serves a payload per requested date; cost = 10 x regions x markets when non-empty."""

    def __init__(self, by_date, used=100, cost_override=None):
        self.by_date, self.used, self.calls, self.cost_override = by_date, used, [], cost_override or {}

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params)))
        body = self.by_date.get(params["date"], payload([], params["date"]))
        nregions, nmarkets = len(params["regions"].split(",")), len(params["markets"].split(","))
        cost = 10 * nregions * nmarkets if body["data"] else 0
        cost = self.cost_override.get(params["date"], cost)
        self.used += cost
        return ok(cost, self.used, body=body)


B1_DATE, B2_DATE = "2025-02-28T18:59:00Z", "2026-03-28T15:59:00Z"


@pytest.fixture
def broot(booted):
    write_anchors(booted.root)
    return booted


def run(root, *argv):
    return SB.main(["--root", str(root), *argv])


def report(root):
    return (Path(root) / "pilot" / "normalized" / "stage_b_fetch_report.md").read_text(encoding="utf-8")


def h2h_row(text, book):
    return next(line for line in text.splitlines() if line.startswith(f"| {book} |"))


# ---------------------------------------------------------------- dates
def test_dates_from_anchors_json(tmp_path):
    write_anchors(tmp_path)
    anchors = SB.load_anchors(tmp_path)
    assert anchors["quinnipiac_air_force"]["event_id"] == Q_ID
    assert anchors["njit_vermont"]["event_id"] == N_ID
    p1, p2, p3 = (SB.step_params(s, anchors) for s in ("B1", "B2", "B3"))
    assert p1 == {"date": B1_DATE, "regions": "us", "markets": "h2h,spreads,totals",
                  "oddsFormat": "american", "dateFormat": "iso"}
    assert p2["date"] == B2_DATE and p2["markets"] == "h2h,spreads,totals" and p2["regions"] == "us"
    assert p3["date"] == B2_DATE and p3["regions"] == "us,us2" and p3["markets"] == "h2h"
    assert SB.step_params("B1", anchors, 15)["date"] == "2025-02-28T18:44:00Z"
    assert SB.step_params("B2", anchors, 30)["date"] == "2026-03-28T15:29:00Z"


def test_dates_follow_anchors_json_not_hard_coded(tmp_path):
    write_anchors(tmp_path, q_start="2025-03-01T17:30:00Z")
    assert SB.step_params("B1", SB.load_anchors(tmp_path))["date"] == "2025-03-01T17:29:00Z"


def test_plan_makes_no_call(broot, mock_get, capsys):
    assert run(broot.root, "--plan") == 0
    out = capsys.readouterr().out
    assert f"date={B1_DATE}" in out and "regions=us,us2" in out and "max cost 30" in out and "max cost 20" in out
    assert "total maximum: 80 credits of 80" in out
    assert mock_get.call_count == 0


def test_run_requires_confirm_paid(broot, mock_get):
    assert run(broot.root, "--run", "--step", "B1") == 2
    assert mock_get.call_count == 0


# --------------------------------------------------------------- ordering
def test_ordering_enforced_and_duplicate_refused(broot, mock_get, capsys):
    server = Server({B1_DATE: q_payload(), B2_DATE: n_payload()})
    mock_get.side_effect = server
    assert run(broot.root, "--run", "--step", "B2", "--confirm-paid") == 2
    assert run(broot.root, "--run", "--step", "B3", "--confirm-paid") == 2
    assert len(server.calls) == 0
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 0
    assert run(broot.root, "--run", "--step", "B3", "--confirm-paid") == 2      # B2 not yet PASS
    assert run(broot.root, "--run", "--step", "B2", "--confirm-paid") == 0
    assert run(broot.root, "--run", "--step", "B3", "--confirm-paid") == 0
    assert len(server.calls) == 3
    url, params = server.calls[2]
    assert url.endswith("/v4/historical/sports/lacrosse_ncaa/odds")
    assert params["regions"] == "us,us2" and params["markets"] == "h2h" and params["oddsFormat"] == "american"
    capsys.readouterr()
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 2      # duplicate guard
    assert "duplicate" in capsys.readouterr().out
    assert len(server.calls) == 3


# -------------------------------------------------------------- cost check
def test_cost_check_function():
    assert SB.cost_ok(5, 30, 30) and SB.cost_ok(0, 0, 30)
    assert not SB.cost_ok(5, 10, 30) and not SB.cost_ok(5, 0, 30)
    assert not SB.cost_ok(0, 30, 30) and not SB.cost_ok(5, "UNKNOWN", 30)


def test_nonempty_payload_below_max_cost_halts(broot, mock_get, capsys):
    mock_get.side_effect = Server({B1_DATE: q_payload()}, cost_override={B1_DATE: 10})
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 2
    halt = H.read_halt(broot.halt_path)
    assert halt["reason_code"] == "UNEXPECTED_COST" and halt["fingerprint"]
    assert "Step status: STOP" in report(broot.root) and "cost check: FAILED" in report(broot.root)


def test_nonempty_payload_zero_cost_halts(broot, mock_get):
    mock_get.side_effect = Server({B1_DATE: q_payload()}, cost_override={B1_DATE: 0})
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 2
    assert H.read_halt(broot.halt_path)["reason_code"] == "UNEXPECTED_COST"


def test_empty_payload_zero_cost_is_accepted_but_stops(broot, mock_get):
    mock_get.side_effect = Server({})
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 1
    assert H.read_halt(broot.halt_path) is None
    text = report(broot.root)
    assert "cost check: OK" in text and "anchor event not present" in text and "Events in payload: 0" in text


# ---------------------------------------------------------- PASS / STOP
def test_pass_report_contents(broot, mock_get, capsys):
    mock_get.side_effect = Server({B1_DATE: q_payload()})
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 0
    printed = capsys.readouterr().out
    text = report(broot.root)
    assert "## B1 primary - close-proxy snapshot - anchor quinnipiac_air_force" in text
    assert "## B1 primary - close-proxy snapshot" in printed
    assert "Requested date: 2025-02-28T18:59:00Z" in text
    assert "Returned timestamp: 2025-02-28T18:55:38Z; previous_timestamp: x-prev; next_timestamp: x-next" in text
    assert "Seconds from returned snapshot to anchor start: 262\n" in text
    assert "Events in payload: 2" in text and f"Anchor event {Q_ID} present: yes" in text
    assert h2h_row(text, "draftkings") == "| draftkings | complete_paired | complete_paired | complete_paired |"
    assert h2h_row(text, "fanduel") == "| fanduel | complete_paired | absent | absent |"
    assert "| draftkings | h2h | complete_paired | 2025-02-28T18:50:00Z | 338 |  |" in text
    assert "| betmgm | 1 |" in text and "| draftkings | 1 |" in text
    assert "Actual cost: 30 (maximum 30); cost check: OK; ledger status: 200" in text
    assert "Step status: PASS" in text
    assert "closing line" not in text.lower() and "2020-01-01" not in text   # bookmaker-level last_update unused


def test_stop_when_no_complete_h2h_and_blocks_next_step(broot, mock_get, capsys):
    books = [{"key": "draftkings", "markets": [mkt("h2h", [o(Q_HOME, -150)])]}]
    mock_get.side_effect = Server({B1_DATE: q_payload(books=books), B2_DATE: n_payload()})
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 1
    out = capsys.readouterr().out
    assert "Step status: STOP" in out and "--fallback-minutes 15" in out and "--fallback-minutes 30" in out
    assert run(broot.root, "--run", "--step", "B2", "--confirm-paid") == 2
    assert mock_get.call_count == 1


def test_stop_when_anchor_absent_even_if_others_complete(broot, mock_get):
    p = q_payload()
    p["data"] = p["data"][1:]
    mock_get.side_effect = Server({B1_DATE: p})
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 1
    assert f"Anchor event {Q_ID} present: no" in report(broot.root)


# ------------------------------------------------------------------ flags
START = datetime(2025, 2, 28, 19, 0, tzinfo=timezone.utc)


def test_stale_snapshot_flag():
    a = SB.analyze(q_payload(ts="2025-02-28T18:44:38Z"), Q_ID, START, 30, 30)
    assert a["seconds_to_start"] == 922 and a["snapshot_flags"] == ["STALE"]
    a = SB.analyze(q_payload(ts="2025-02-28T18:45:00Z"), Q_ID, START, 30, 30)
    assert a["seconds_to_start"] == 900 and a["snapshot_flags"] == []


def test_post_start_flag():
    a = SB.analyze(q_payload(ts="2025-02-28T19:00:38Z"), Q_ID, START, 30, 30)
    assert a["seconds_to_start"] == -38 and a["snapshot_flags"] == ["POST-START"]


def test_stale_quote_flag_uses_market_last_update():
    books = [full_book("draftkings", Q_HOME, Q_AWAY, lu="2025-02-28T18:40:37Z"),
             full_book("fanduel", Q_HOME, Q_AWAY, lu="2025-02-28T18:40:38Z")]
    a = SB.analyze(q_payload(books=books), Q_ID, START, 30, 30)
    dk = [q for q in a["quotes"] if q["bookmaker"] == "draftkings"]
    fd = [q for q in a["quotes"] if q["bookmaker"] == "fanduel"]
    assert all(q["staleness_seconds"] == 901 and q["flag"] == "STALE_QUOTE" for q in dk)
    assert all(q["staleness_seconds"] == 900 and q["flag"] == "" for q in fd)
    assert a["snapshot_flags"] == [] and a["status"] == "PASS"   # stale quotes do not decide PASS


# --------------------------------------------------------------- fallback
def test_fallback_rules_and_limit(broot, mock_get, capsys):
    bad = [{"key": "draftkings", "markets": [mkt("h2h", [o(Q_HOME, -150), o("Someone Else", 120)])]}]
    fb15 = "2025-02-28T18:44:00Z"
    server = Server({B1_DATE: q_payload(books=bad), fb15: q_payload(ts="2025-02-28T18:40:38Z"),
                     B2_DATE: n_payload()})
    mock_get.side_effect = server
    assert run(broot.root, "--run", "--step", "B1", "--fallback-minutes", "15", "--confirm-paid") == 2  # no STOP yet
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 1
    assert run(broot.root, "--run", "--step", "B1", "--fallback-minutes", "20", "--confirm-paid") == 2
    assert len(server.calls) == 1
    assert run(broot.root, "--run", "--step", "B1", "--fallback-minutes", "15", "--confirm-paid") == 0
    assert server.calls[-1][1]["date"] == fb15
    row = broot.ledger.rows()[-1]
    assert row["purpose"] == "stage B fallback B1 15m quinnipiac_air_force"
    text = report(broot.root)
    assert "## B1 fallback (15 min earlier) - close-proxy snapshot" in text
    assert "[STALE]" in text   # 19:00 start vs 18:40:38 snapshot
    assert run(broot.root, "--run", "--step", "B1", "--fallback-minutes", "30", "--confirm-paid") == 2
    assert len(server.calls) == 2
    # Fallback PASS unblocks B2; the primary B2 may now draw RESERVE for the fallback's share.
    assert run(broot.root, "--run", "--step", "B2", "--confirm-paid") == 0
    assert "reserve_draw=10" in broot.ledger.rows()[-1]["notes"]


def test_one_fallback_per_anchor_shared_by_b2_and_b3():
    ev = [{"step": "B2", "kind": "fallback", "anchor_id": "njit_vermont", "status": "PASS", "row200": {"status_code": "200"}},
          {"step": "B2", "kind": "primary", "anchor_id": "njit_vermont", "status": "STOP", "row200": {"status_code": "200"}},
          {"step": "B1", "kind": "primary", "anchor_id": "quinnipiac_air_force", "status": "PASS", "row200": {"status_code": "200"}},
          {"step": "B3", "kind": "primary", "anchor_id": "njit_vermont", "status": "STOP", "row200": {"status_code": "200"}}]
    with pytest.raises(SB.StageBError, match="already been used"):
        SB.check_allowed("B3", 15, ev)


# --------------------------------------------------------- classification
HT, AT = "Home U", "Away U"


def cls(key, outcomes):
    return SB.classify_market(key, {"key": key, "outcomes": outcomes}, HT, AT)


def test_h2h_classification():
    assert cls("h2h", [o(HT, -150), o(AT, 130)]) == SB.COMPLETE
    assert cls("h2h", [o(AT, 100), o(HT, -100)]) == SB.COMPLETE
    assert cls("h2h", [o(HT, -150), o("Wrong U", 130)]) == SB.INCOMPLETE       # wrong team name
    assert cls("h2h", [o(HT, -150), o(HT, 130)]) == SB.INCOMPLETE               # duplicate team name
    assert cls("h2h", [o(HT, -150), o(AT, 130), o("Draw", 300)]) == SB.INCOMPLETE
    assert cls("h2h", [o(HT, 0), o(AT, 130)]) == SB.INCOMPLETE                  # zero price
    assert cls("h2h", [o(HT, "-150"), o(AT, 130)]) == SB.INCOMPLETE             # non-numeric price
    assert cls("h2h", [o(HT, None), o(AT, 130)]) == SB.INCOMPLETE
    assert cls("h2h", [o(HT, True), o(AT, 130)]) == SB.INCOMPLETE
    assert cls("h2h", [o(HT, -99), o(AT, 130)]) == SB.INCOMPLETE                # abs < 100
    assert cls("h2h", [o(HT, -150)]) == SB.INCOMPLETE


def test_spreads_classification():
    assert cls("spreads", [o(HT, -110, -1.5), o(AT, -110, 1.5)]) == SB.COMPLETE
    assert cls("spreads", [o(HT, -110, -1.5), o(AT, -110, -1.5)]) == SB.MISMATCHED   # same sign
    assert cls("spreads", [o(HT, -110, -1.5), o(AT, -110, 2.5)]) == SB.MISMATCHED    # unequal
    assert cls("spreads", [o(HT, -110, -1.5), o(AT, -110)]) == SB.INCOMPLETE         # missing point
    assert cls("spreads", [o(HT, -110, -1.5), o("Wrong U", -110, 1.5)]) == SB.INCOMPLETE


def test_totals_classification():
    assert cls("totals", [o("Over", -105, 22.5), o("Under", -115, 22.5)]) == SB.COMPLETE
    assert cls("totals", [o("Over", -105, 22.5), o("Under", -115, 23.5)]) != SB.COMPLETE   # different points
    assert cls("totals", [o("Over", -105, 22.5), o("Under", -115, 23.5)]) == SB.INCOMPLETE
    assert cls("totals", [o("Over", -105, 22.5), o("Over", -115, 22.5)]) == SB.INCOMPLETE
    assert cls("totals", [o("Over", "x", 22.5), o("Under", -115, 22.5)]) == SB.INCOMPLETE


def test_missing_market_is_absent():
    book = {"key": "dk", "markets": [{"key": "h2h", "outcomes": [o(HT, -150), o(AT, 130)]}]}
    assert SB.classify_market("spreads", SB.find_market(book, "spreads"), HT, AT) == SB.ABSENT
    assert SB.classify_market("totals", SB.find_market(book, "totals"), HT, AT) == SB.ABSENT


def test_unexpected_structure_rejected():
    for bad in ([], {"data": "x"}, {"data": [{"id": "a", "bookmakers": "x"}]},
                {"data": [{"id": "a", "bookmakers": [{"key": "dk", "markets": [{"no": "key"}]}]}]}):
        with pytest.raises(SB.StageBParseError):
            SB.parse_odds_wrapper(bad)


# ------------------------------------------------------------------ hygiene
def test_stage_b_uses_oddsclient_only():
    text = Path(SB.__file__).read_text(encoding="utf-8")
    assert "import requests" not in text and "from requests" not in text
    assert "urllib" not in text and "socket" not in text and "httpx" not in text


def test_key_never_written_by_stage_b(broot, mock_get):
    mock_get.side_effect = Server({B1_DATE: q_payload(), B2_DATE: n_payload()})
    assert run(broot.root, "--run", "--step", "B1", "--confirm-paid") == 0
    assert run(broot.root, "--run", "--step", "B2", "--confirm-paid") == 0
    files = [p for p in broot.root.rglob("*") if p.is_file()]
    assert [p for p in files if TESTKEY.encode() in p.read_bytes()] == []
