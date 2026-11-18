import csv
import json
from pathlib import Path

import pytest

from src import halt as H
from src import stage0
from src import stage_a as SA
from src.ledger import COLUMNS

from .conftest import TESTKEY, FakeResponse, ok


def ev(eid, home, away, commence="2025-02-28T19:00:00Z"):
    return {"id": eid, "sport_key": "lacrosse_ncaa", "commence_time": commence,
            "home_team": home, "away_team": away}


def wrapper(events, ts="2025-02-27T12:00:00Z"):
    return {"timestamp": ts, "previous_timestamp": "2025-02-26T12:00:00Z",
            "next_timestamp": "2025-02-28T12:00:00Z", "data": events}


QUIN = ev("e-quin", "Quinnipiac Bobcats", "Air Force Falcons", "2025-02-28T19:00:00Z")
NJIT = ev("e-njit", "Vermont Catamounts", "NJIT Highlanders", "2026-03-28T18:00:00Z")
GOOD = {"2025-02-27": [QUIN], "2026-03-27": [NJIT]}


class Server:
    """Fake API. Returns canned events per requested date and keeps x-requests-used consistent."""

    def __init__(self, by_date=None, used=100, bad_status_on_call=None, bad_body_on_call=None):
        self.by_date = by_date or {}
        self.used = used
        self.calls = []
        self.bad_status_on_call = bad_status_on_call
        self.bad_body_on_call = bad_body_on_call

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params)))
        n = len(self.calls)
        if n == self.bad_status_on_call:
            return FakeResponse(500, b'{"message":"boom"}', last=0, used=self.used, remaining=19000)
        if "participants" in url:
            self.used += 1
            return ok(1, self.used, body=[{"id": "p1", "full_name": "Somewhere"}])
        if n == self.bad_body_on_call:
            return ok(0, self.used, body={"data": "oops"})
        date = params["date"]
        events = self.by_date.get(date[:10], [])
        cost = 1 if events else 0
        self.used += cost
        return ok(cost, self.used, body=wrapper(events, ts=date))


def run_cli(root, *argv):
    return SA.main(["--root", str(root), *argv])


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


# ------------------------------------------------------------------ parsing
def test_parse_wrapper_normal_and_empty():
    meta, events = SA.parse_wrapper(wrapper([QUIN]))
    assert meta["timestamp"] == "2025-02-27T12:00:00Z"
    assert events == [{"event_id": "e-quin", "commence_time_utc": "2025-02-28T19:00:00Z",
                       "commence_time_et": "2025-02-28T14:00:00-05:00",
                       "home_team": "Quinnipiac Bobcats", "away_team": "Air Force Falcons"}]
    assert SA.parse_wrapper(wrapper([]))[1] == []


def test_parse_wrapper_is_tolerant_of_missing_optional_fields():
    meta, events = SA.parse_wrapper({"data": [{"id": "x1"}], "next_timestamp": None})
    assert meta == {"timestamp": "", "previous_timestamp": "", "next_timestamp": ""}
    assert events[0]["event_id"] == "x1" and events[0]["commence_time_utc"] == ""
    assert events[0]["commence_time_et"] == "" and events[0]["home_team"] == ""


def test_parse_wrapper_summer_time_uses_edt():
    _, events = SA.parse_wrapper(wrapper([ev("s", "A", "B", "2025-06-01T16:00:00Z")]))
    assert events[0]["commence_time_et"] == "2025-06-01T12:00:00-04:00"


def test_parse_wrapper_keeps_unparseable_time_raw():
    _, events = SA.parse_wrapper(wrapper([ev("s", "A", "B", "not a time")]))
    assert events[0]["commence_time_utc"] == "not a time" and events[0]["commence_time_et"] == ""


@pytest.mark.parametrize("bad", [[], "text", {"timestamp": "x"}, {"data": "oops"}, {"data": [1]},
                                 {"data": [{"home_team": "no id"}]}])
def test_parse_wrapper_rejects_unexpected_structure(bad):
    with pytest.raises(SA.StageAParseError):
        SA.parse_wrapper(bad)


# ------------------------------------------------------------------ anchors
def _result(pid, events):
    return {"probe_id": pid, "status": "done", "events": SA.parse_wrapper(wrapper(events))[1]}


def test_anchor_matching_case_insensitive_with_near_misses():
    results = [_result("P08", [
        ev("a", "QUINNIPIAC BOBCATS", "air force falcons"),
        ev("b", "Air Force Falcons", "Navy"),
        ev("c", "Duke", "Navy"),
    ]), _result("P14", [ev("d", "vermont catamounts", "Njit highlanders", "2026-03-28T18:00:00Z")])]
    q, n = SA.detect_anchors(results)
    assert q["found"] and q["event_id"] == "a" and q["commence_time_et"] == "2025-02-28T14:00:00-05:00"
    assert q["commence_date_et_matches_expected"] is True
    assert [c["event_id"] for c in q["near_miss_candidates"]] == ["b"]
    assert n["found"] and n["event_id"] == "d"


def test_anchor_not_found_lists_near_misses_only():
    q, n = SA.detect_anchors([_result("P01", [ev("x", "Vermont", "Duke"), ev("y", "Quinnipiac", "Yale")])])
    assert not q["found"] and q["event_id"] == "" and q["matched_names"] == []
    assert [c["event_id"] for c in q["near_miss_candidates"]] == ["y"]
    assert not n["found"] and [c["event_id"] for c in n["near_miss_candidates"]] == ["x"]


# --------------------------------------------------------------------- gate
def _gate(dates_nonempty, dates_empty=(), anchors=(True, True), complete=True):
    s = [{"requested_date": d, "n_events": 1} for d in dates_nonempty]
    s += [{"requested_date": d, "n_events": 0} for d in dates_empty]
    return SA.evaluate_gate(s, {"a1": anchors[0], "a2": anchors[1]}, complete)


def test_gate_go():
    g = _gate(["2025-02-27T12:00:00Z", "2026-03-27T12:00:00Z"])
    assert g["status"] == "GO" and g["reasons"] == []


def test_gate_diagnose_missing_anchor():
    g = _gate(["2025-02-27T12:00:00Z", "2026-03-27T12:00:00Z"], anchors=(True, False))
    assert g["status"] == "DIAGNOSE" and any("a2" in r for r in g["reasons"])


def test_gate_diagnose_empty_window_and_edges_are_inclusive():
    g = _gate(["2025-01-25T12:00:00Z", "2025-05-04T12:00:00Z", "2026-03-27T12:00:00Z"])
    assert g["status"] == "DIAGNOSE" and g["window_2025_nonempty_probes"] == 0
    assert _gate(["2025-02-01T12:00:00Z", "2026-05-02T12:00:00Z"])["status"] == "GO"
    assert _gate(["2025-05-03T12:00:00Z", "2026-01-31T12:00:00Z"])["status"] == "GO"


def test_gate_incomplete_when_probes_missing():
    g = _gate(["2025-02-27T12:00:00Z", "2026-03-27T12:00:00Z"], complete=False)
    assert g["status"] == "INCOMPLETE"


# --------------------------------------------------------------- plan / refusals
def test_probe_list_matches_spec():
    probes = SA.build_probes()
    assert [p["params"]["date"][:10] for p in probes] == SA.PROBE_DATES and len(probes) == 15
    assert all(p["params"]["date"].endswith("T12:00:00Z") and p["params"]["dateFormat"] == "iso" for p in probes)
    by = {p["params"]["date"][:10]: p["params"] for p in probes}
    assert by["2025-02-27"]["commenceTimeFrom"] == "2025-02-28T00:00:00Z"
    assert by["2025-02-27"]["commenceTimeTo"] == "2025-03-01T10:00:00Z"
    assert by["2026-03-27"]["commenceTimeFrom"] == "2026-03-28T00:00:00Z"
    assert by["2026-03-27"]["commenceTimeTo"] == "2026-03-29T10:00:00Z"
    assert sum("commenceTimeFrom" in p["params"] for p in probes) == 2


def test_plan_prints_requests_without_network(root, mock_get, capsys):
    assert run_cli(root, "--plan") == 0
    out = capsys.readouterr().out
    assert "P15" in out and "commenceTimeFrom=2026-03-28T00:00:00Z" in out
    assert "total maximum: 15 credits of 20" in out
    assert mock_get.call_count == 0


def test_run_without_confirm_paid_refuses(booted, mock_get, capsys):
    assert run_cli(booted.root, "--run") == 2
    assert "--confirm-paid" in capsys.readouterr().out
    assert mock_get.call_count == 0


def test_followup_argument_validation_makes_no_call(booted, mock_get):
    d = "2025-02-10T12:00:00Z"
    assert run_cli(booted.root, "--followup", "--date", d) == 2                       # no --confirm-paid
    assert run_cli(booted.root, "--followup", "--date", d, "--from", d, "--confirm-paid") == 2
    assert run_cli(booted.root, "--followup", "--date", "2025-02-10", "--confirm-paid") == 2
    assert mock_get.call_count == 0


def test_participants_disabled_until_endpoint_confirmed(booted, mock_get, monkeypatch, capsys):
    assert run_cli(booted.root, "--run", "--confirm-paid", "--include-participants") == 2
    assert "PARTICIPANTS_ENDPOINT_CONFIRMED" in capsys.readouterr().out
    assert mock_get.call_count == 0
    monkeypatch.setattr(SA, "PARTICIPANTS_ENDPOINT_CONFIRMED", True)
    server = Server(GOOD)
    mock_get.side_effect = server
    assert run_cli(booted.root, "--run", "--confirm-paid", "--include-participants") == 0
    assert len(server.calls) == 16
    url, params = server.calls[-1]
    assert url.endswith("/v4/sports/lacrosse_ncaa/participants") and "date" not in params


# ---------------------------------------------------------------- full run
def test_run_writes_outputs_and_gate_go(booted, mock_get, capsys):
    server = Server(GOOD)
    mock_get.side_effect = server
    assert run_cli(booted.root, "--run", "--confirm-paid") == 0
    assert len(server.calls) == 15
    assert all(u == "https://api.the-odds-api.com/v4/historical/sports/lacrosse_ncaa/events"
               for u, _ in server.calls)

    out = booted.root / "pilot" / "normalized"
    ev_rows = read_csv(out / "stage_a_events.csv")
    assert ev_rows[0] == ["probe_id", "requested_date", "returned_timestamp", "previous_timestamp",
                          "next_timestamp", "event_id", "commence_time_utc", "commence_time_et",
                          "home_team", "away_team"]
    assert len(ev_rows) == 3 and {r[5] for r in ev_rows[1:]} == {"e-quin", "e-njit"}
    sm_rows = read_csv(out / "probe_summary.csv")
    assert sm_rows[0] == ["probe_id", "requested_date", "returned_timestamp", "n_events", "actual_cost",
                          "season_label", "window_filter"]
    assert len(sm_rows) == 16
    p08 = next(r for r in sm_rows if r[0] == "P08")
    assert p08[3] == "1" and p08[4] == "1" and p08[5] == "2025"
    assert p08[6] == "commenceTimeFrom=2025-02-28T00:00:00Z;commenceTimeTo=2025-03-01T10:00:00Z"

    anchors = json.loads((out / "anchors.json").read_text())["anchors"]
    assert [a["found"] for a in anchors] == [True, True]
    assert anchors[0]["event_id"] == "e-quin" and anchors[1]["commence_time_et"] == "2026-03-28T14:00:00-04:00"

    md = (out / "stage_a_summary.md").read_text()
    assert "## Gate A: GO" in md and "no coverage" not in md.lower()
    assert "| 2025 | 6 | 1 | 1 | 1 |" in md and "| 2026 | 5 | 1 | 1 | 1 |" in md
    assert "| 2023 | 2 | 0 | 0 | 0 |" in md and "| 2024 | 2 | 0 | 0 | 0 |" in md
    assert "2 of 20 budgeted" in md
    assert "Gate A: GO" in capsys.readouterr().out


def test_rerun_skips_successful_probes(booted, mock_get, capsys):
    server = Server(GOOD)
    mock_get.side_effect = server
    assert run_cli(booted.root, "--run", "--confirm-paid") == 0
    capsys.readouterr()
    assert run_cli(booted.root, "--run", "--confirm-paid") == 0
    out = capsys.readouterr().out
    assert len(server.calls) == 15
    assert out.count("skipped (already successful)") == 15 and "Executed 0, skipped 15" in out
    assert (booted.root / "pilot" / "normalized" / "probe_summary.csv").exists()


def test_diagnose_when_anchor_missing_lists_suggestions_only(booted, mock_get):
    server = Server({"2025-02-27": [ev("z", "Duke", "Navy")], "2026-03-27": [NJIT]})
    mock_get.side_effect = server
    assert run_cli(booted.root, "--run", "--confirm-paid") == 0
    md = (booted.root / "pilot" / "normalized" / "stage_a_summary.md").read_text()
    assert "## Gate A: DIAGNOSE" in md and "Suggested follow-ups" in md
    assert "same probe 24h earlier: --followup --date 2025-02-26T12:00:00Z" in md
    assert "same probe 48h earlier: --followup --date 2025-02-25T12:00:00Z" in md
    assert "wider commence window" in md and "name variants" in md
    assert "no coverage" not in md.lower()
    assert len(server.calls) == 15  # suggestions are never executed


def test_error_status_halts_run_and_leaves_gate_incomplete(booted, mock_get):
    server = Server(GOOD, bad_status_on_call=3)
    mock_get.side_effect = server
    assert run_cli(booted.root, "--run", "--confirm-paid") == 2
    assert len(server.calls) == 3
    assert H.read_halt(booted.halt_path)["reason_code"] == "NON_200"
    md = (booted.root / "pilot" / "normalized" / "stage_a_summary.md").read_text()
    assert "## Gate A: INCOMPLETE" in md
    assert run_cli(booted.root, "--run", "--confirm-paid") == 2   # HALT blocks the rest
    assert len(server.calls) == 3


def test_unexpected_structure_keeps_raw_file_and_halts(booted, mock_get):
    server = Server(GOOD, bad_body_on_call=1)
    mock_get.side_effect = server
    assert run_cli(booted.root, "--run", "--confirm-paid") == 2
    assert len(server.calls) == 1
    halt = H.read_halt(booted.halt_path)
    assert halt["reason_code"] == "MALFORMED_BODY" and halt["fingerprint"]
    raws = [p for p in (booted.root / "pilot" / "raw").glob("A_*.json") if not p.name.endswith(".meta.json")]
    assert len(raws) == 1 and json.loads(raws[0].read_text()) == {"data": "oops"}


# ---------------------------------------------------------------- follow-ups
def test_followup_limit_is_four(booted, mock_get):
    server = Server({})
    mock_get.side_effect = server
    for i in range(4):
        assert run_cli(booted.root, "--followup", "--date", f"2025-02-1{i}T12:00:00Z", "--confirm-paid") == 0
    assert len(server.calls) == 4
    assert run_cli(booted.root, "--followup", "--date", "2025-02-14T12:00:00Z", "--confirm-paid") == 2
    assert len(server.calls) == 4
    rows = booted.ledger.rows()
    assert sum(r["purpose"].startswith("stage A followup") for r in rows) == 4
    assert "RESERVE-designated" in rows[-1]["purpose"]
    probe_rows = read_csv(booted.root / "pilot" / "normalized" / "probe_summary.csv")
    assert [r[0] for r in probe_rows[1:]] == ["F1", "F2", "F3", "F4"]


def test_followup_uses_reserve_when_stage_budget_is_spent(booted, mock_get):
    prior = {c: "" for c in COLUMNS}
    prior.update(stage="A", purpose="prior", endpoint="/v4/x", fingerprint="x" * 64, actual_cost="20",
                 cumulative_spent="20", header_used="120", status_code="200", expected_cost_min="0",
                 expected_cost_max="20")
    booted.ledger.append(prior)
    mock_get.side_effect = Server({"2025-02-10": [ev("f", "Duke", "Navy")]}, used=120)
    assert run_cli(booted.root, "--followup", "--date", "2025-02-10T12:00:00Z", "--confirm-paid") == 0
    assert "reserve_draw=1" in booted.ledger.rows()[-1]["notes"]
    assert booted.ledger.summary(booted.budgets)["reserve_by_stage"]["A"] == 1


def test_followup_events_feed_anchor_detection(booted, mock_get):
    mock_get.side_effect = Server({"2025-02-26": [QUIN]})
    assert run_cli(booted.root, "--followup", "--date", "2025-02-26T12:00:00Z", "--from",
                   "2025-02-28T00:00:00Z", "--to", "2025-03-01T10:00:00Z", "--confirm-paid") == 0
    anchors = json.loads((booted.root / "pilot" / "normalized" / "anchors.json").read_text())["anchors"]
    assert anchors[0]["found"] and anchors[0]["all_matches"][0]["probe_ids"] == ["F1"]


# -------------------------------------------------------------------- stage 0
def test_stage0_without_baseline_says_to_bootstrap(root, mock_get, capsys):
    assert stage0.main(["--root", str(root)]) == 1
    assert capsys.readouterr().out.strip() == "run bootstrap-baseline first"
    assert mock_get.call_count == 0


def test_stage0_reads_saved_response_without_network(root, mock_get, capsys):
    from src.odds_client import OddsClient
    sports = [{"key": "lacrosse_ncaa", "title": "NCAA Lacrosse", "active": False},
              {"key": "lacrosse_pll", "title": "PLL", "active": True},
              {"key": "americanfootball_nfl", "title": "NFL", "active": True}]
    mock_get.side_effect = [ok(0, 100, body=sports)]
    OddsClient(root=root).bootstrap_baseline()
    mock_get.reset_mock(side_effect=True)
    rc = stage0.main(["--root", str(root)])
    out = capsys.readouterr().out
    assert rc == 0                                       # listed, so PASS even though all=true was not sent
    assert "lacrosse_ncaa: LISTED (key=lacrosse_ncaa title=NCAA Lacrosse active=false)" in out
    assert "key=lacrosse_pll title=PLL active=true" in out
    assert "bootstrap_baseline() sent GET /v4/sports without all=true" in out
    assert "Check remaining >= 500: PASS (19900)" in out
    assert mock_get.call_count == 0


SPORTS = [{"key": "lacrosse_ncaa", "title": "NCAA Lacrosse", "active": False}]
BASE = {"used": 100, "remaining": 19900, "ts": "t"}


def test_stage0_analyze_statuses():
    assert stage0.analyze(BASE, SPORTS, True)[1] == "PASS"
    assert stage0.analyze(BASE, [], True)[1] == "FAIL"
    assert stage0.analyze(BASE, [], False)[1] == "INCONCLUSIVE"
    assert stage0.analyze(dict(BASE, remaining=499), SPORTS, True)[1] == "FAIL"
    assert stage0.analyze(dict(BASE, remaining=500), SPORTS, True)[1] == "PASS"
    assert stage0.analyze(BASE, "junk", True)[1] == "FAIL"


# ------------------------------------------------------------------- hygiene
def test_modules_use_oddsclient_only():
    for name in ("stage_a.py", "stage0.py"):
        text = (Path(SA.__file__).parent / name).read_text(encoding="utf-8")
        assert "import requests" not in text and "from requests" not in text and "httpx" not in text
        assert "urllib" not in text and "socket" not in text


def test_key_never_written_by_stage_a(booted, mock_get):
    mock_get.side_effect = Server(GOOD)
    assert run_cli(booted.root, "--run", "--confirm-paid") == 0
    files = [p for p in booted.root.rglob("*") if p.is_file()]
    assert len(files) > 20
    assert [p for p in files if TESTKEY.encode() in p.read_bytes()] == []
