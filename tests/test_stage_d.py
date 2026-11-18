import ast
from pathlib import Path

import pytest

from src import halt as H
from src import stage_c as SC
from src import stage_d as SD

from .conftest import TESTKEY
from .test_stage_b import Server, event, h2h, mkt, o, payload
from .test_stage_c import C_DATES, default_server, make_root

D_DATES = {"D1": "2025-03-08T15:50:00Z", "D2": "2026-03-07T16:50:00Z"}
D1_TS, D2_TS = "2025-03-08T15:45:38Z", "2026-03-07T16:45:38Z"


def dk(*pairs):
    """draftkings book with h2h for each (home, away)."""
    return {"key": "draftkings", "markets": [h2h(*pairs)]}


def d1_payload(ts=D1_TS):
    e1 = event("e1", "Duke", "Navy", "2025-03-08T16:00:00Z",
               [{"key": "draftkings", "markets": [h2h("Duke", "Navy")]},
                {"key": "fanduel", "markets": [h2h("Duke", "Navy")]}])
    e2 = event("e2", "Army", "Lehigh", "2025-03-08T17:00:00Z",
               [{"key": "draftkings", "markets": [h2h("Army", "Lehigh")]},
                {"key": "fanduel", "markets": [h2h("Army", "Lehigh")]},
                {"key": "betmgm", "markets": [h2h("Army", "Lehigh")]}])
    e3 = event("e3", "Yale", "Brown", "2025-03-08T17:30:00Z",
               [{"key": "betmgm", "markets": [mkt("totals", [o("Over", -105, 22.5), o("Under", -115, 22.5)])]}])
    e4 = event("e4", "Quinnipiac Bobcats", "Air Force Falcons", "2025-03-09T18:00:00Z", [])
    return payload([e1, e2, e3, e4], ts)


@pytest.fixture
def droot(tmp_path, mock_get):
    server = default_server()
    server.by_date.update({D_DATES["D1"]: d1_payload(), D_DATES["D2"]: payload(
        [event("n1", "Vermont Catamounts", "NJIT Highlanders", "2026-03-07T17:00:00Z", [dk("Vermont Catamounts", "NJIT Highlanders")])],
        D2_TS)})
    root = make_root(tmp_path / "proj", mock_get, server)
    return root, server


def run(root, *argv):
    return SD.main(["--root", str(root), *argv])


def report(root):
    return (Path(root) / "pilot" / "normalized" / "stage_d_report.md").read_text(encoding="utf-8")


def ledger_lines(root):
    return (Path(root) / "pilot" / "ledger" / "credit_ledger.csv").read_text(encoding="utf-8").splitlines()


def step_row(text, step):
    return next(line for line in text.splitlines() if line.startswith(f"| {step} | "))


def section(text, n):
    return text.split(f"## {n}.")[1].split("\n## ")[0]


# ------------------------------------------------------------------ steps
def test_step_dates():
    for s, d in D_DATES.items():
        assert SD.step_params(s) == {"date": d, "regions": "us", "markets": "h2h,spreads,totals",
                                     "oddsFormat": "american", "dateFormat": "iso"}
    assert SD.STEP_ORDER == ["D1", "D2"]


def test_plan_makes_no_call(droot, capsys):
    root, server = droot
    assert run(root, "--plan") == 0
    out = capsys.readouterr().out
    assert f"date={D_DATES['D1']}" in out and f"date={D_DATES['D2']}" in out
    assert "total maximum: 60 credits of 60" in out and "max cost 30" in out and "status=NOT_RUN" in out
    assert server.calls == []


def test_run_and_call_parameters(droot):
    root, server = droot
    assert run(root, "--run", "--step", "D2,D1", "--confirm-paid") == 0
    assert [c[1]["date"] for c in server.calls] == [D_DATES["D2"], D_DATES["D1"]]          # any order
    url, params = server.calls[0]
    assert url.endswith("/v4/historical/sports/lacrosse_ncaa/odds")
    assert params["regions"] == "us" and params["markets"] == "h2h,spreads,totals" and params["oddsFormat"] == "american"
    rows = [r for r in SD._ledger(root).rows() if r["stage"] == "D"]
    assert len(rows) == 2 and all(r["actual_cost"] == "30" and "reserve_draw" not in r["notes"] for r in rows)


# --------------------------------------------------------------- refusals
def test_missing_confirm_paid_refused(droot):
    root, server = droot
    before = ledger_lines(root)
    assert run(root, "--run", "--step", "D1") == 2
    assert server.calls == [] and ledger_lines(root) == before


def test_refused_steps_create_no_ledger_row(droot, capsys):
    root, server = droot
    before = ledger_lines(root)
    assert run(root, "--run", "--step", "D3", "--confirm-paid") == 2
    assert run(root, "--run", "--step", "D1,D1", "--confirm-paid") == 2
    assert run(root, "--run", "--step", "D1,D9", "--confirm-paid") == 2      # all-or-nothing: D1 is not run
    assert "REFUSED" in capsys.readouterr().out
    assert server.calls == [] and ledger_lines(root) == before
    assert not (root / "pilot" / "ledger" / "pending.json").exists()


def test_rerun_refused_without_request_names_ledger_row(droot, capsys):
    root, server = droot
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    n = len(ledger_lines(root))
    capsys.readouterr()
    before = ledger_lines(root)
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 2
    assert f"D1 is already recorded in the ledger (line {n}," in capsys.readouterr().out
    assert run(root, "--run", "--step", "D2,D1", "--confirm-paid") == 2      # D2 not run either
    assert len(server.calls) == 1 and ledger_lines(root) == before


# --------------------------------------------------------------- cost check
def test_cost_check_function():
    from src import stage_b as SB
    assert SB.cost_ok(4, 30, 30) and SB.cost_ok(0, 0, 30)
    assert not SB.cost_ok(4, 10, 30) and not SB.cost_ok(0, 30, 30)


def test_nonempty_wrong_cost_halts(tmp_path, mock_get):
    server = default_server(cost_override={D_DATES["D1"]: 10})
    server.by_date[D_DATES["D1"]] = d1_payload()
    root = make_root(tmp_path / "p", mock_get, server)
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 2
    halt = H.read_halt(root / "pilot" / "ledger" / "HALT")
    assert halt["reason_code"] == "UNEXPECTED_COST" and halt["fingerprint"]
    assert SD.HALTED in step_row(report(root), "D1")


def test_empty_payload_zero_cost_is_a_finding(droot):
    root, server = droot
    server.by_date[D_DATES["D1"]] = payload([], D1_TS)
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    assert H.read_halt(root / "pilot" / "ledger" / "HALT") is None
    assert f"| D1 | {SD.EMPTY_PAYLOAD} |" in report(root)


def test_empty_payload_nonzero_cost_halts(tmp_path, mock_get):
    server = default_server(cost_override={D_DATES["D1"]: 30})
    server.by_date[D_DATES["D1"]] = payload([], D1_TS)
    root = make_root(tmp_path / "p", mock_get, server)
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 2
    assert H.read_halt(root / "pilot" / "ledger" / "HALT")["reason_code"] == "UNEXPECTED_COST"


def test_stop_on_first_halt(tmp_path, mock_get, capsys):
    server = default_server(cost_override={D_DATES["D1"]: 10})
    server.by_date[D_DATES["D1"]] = d1_payload()
    root = make_root(tmp_path / "p", mock_get, server)
    assert run(root, "--run", "--step", "D1,D2", "--confirm-paid") == 2
    out = capsys.readouterr().out
    assert [c[1]["date"] for c in server.calls] == [D_DATES["D1"]]
    assert "Skipped (not requested): D2" in out and "HALT UNEXPECTED_COST" in out
    assert f"| D2 | {SD.NOT_RUN} |" in report(root)
    assert run(root, "--run", "--step", "D2", "--confirm-paid") == 2           # HALT blocks further runs
    assert len(server.calls) == 1


# ------------------------------------------------------------------ report
def test_step_table_and_coverage(droot):
    root, server = droot
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    text = report(root)
    row = step_row(text, "D1")
    assert f"| {SD.EXECUTED} | {D_DATES['D1']} | {D1_TS} | x-prev | x-next | 262 | 4 | 30 | 200 |" in row
    assert f"| D2 | {SD.NOT_RUN} | - |" in step_row(text, "D2")
    sec = section(text, 2)
    assert "| draftkings | 2 | 2 | 0 | 0 |" in sec and "| fanduel | 2 | 2 | 0 | 0 |" in sec
    assert "| betmgm | 2 | 1 | 0 | 1 |" in sec
    assert "| 2 | 0 | 1 | 1 |" in sec            # e3, e4: 0 books; e1: 2 books; e2: 3 books
    assert "(event, book) pairs" in sec and "| e3 |" in sec


def test_timing_section_counts(droot):
    root, server = droot
    p = d1_payload()
    p["data"].append(event("early", "Mit", "Ohio", "2025-03-08T15:30:00Z", []))     # before the snapshot
    server.by_date[D_DATES["D1"]] = p
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    sec = section(report(root), 3)
    assert "Earliest commence_time: 2025-03-08T15:30:00Z; latest: 2025-03-09T18:00:00Z" in sec
    assert "Events by commence date: 2025-03-08 4, 2025-03-09 1" in sec
    assert "Events by commence hour: 15:00 1, 16:00 1, 17:00 2, 18:00 1" in sec
    assert "before the returned snapshot timestamp: 1" in sec
    assert "| early | Mit | Ohio | 2025-03-08T15:30:00Z | 938 |" in sec


def test_timing_none_before_snapshot(droot):
    root, server = droot
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    assert "before the returned snapshot timestamp: 0" in section(report(root), 3)


def test_overlap_counts_same_season_only(droot):
    root, server = droot
    # C2 (2025) shares e1 and Quinnipiac anchor with D1; D1 payload also holds e2..e4
    c2 = payload([event("e1", "Duke", "Navy", "2025-03-08T16:00:00Z", [dk("Duke", "Navy")]),
                  event("c2only", "Rice", "Troy", "2025-03-01T16:00:00Z", [])], "2025-02-27T18:55:39Z")
    server.by_date[C_DATES["C2"]] = c2
    assert SC.main(["--root", str(root), "--run", "--step", "C2", "--confirm-paid"]) == 0
    assert run(root, "--run", "--step", "D1,D2", "--confirm-paid") == 0
    sec = section(report(root), 4)
    d1 = next(line for line in sec.splitlines() if line.startswith("| D1 |"))
    # B1 holds the Quinnipiac anchor + other1; none of those are in D1 except nothing; C2 shares e1
    assert "| D1 | 2025 | 4 | B1 0, C2 1 | 1 | 3 | C4 (NOT_RUN (tranche 1, ready)) |" in d1
    d2 = next(line for line in sec.splitlines() if line.startswith("| D2 |"))
    assert "| D2 | 2026 | 1 | B2 0 | 0 | 1 |" in d2 and "C6 (" in d2 and "C8 (" in d2
    assert "B2" not in d1.split("|")[5] and "B1" not in d2.split("|")[5]      # no cross-season references


def test_overlap_with_close_proxy_event(droot):
    root, server = droot
    p = d1_payload()
    p["data"].append(event("other1", "Duke", "Navy", "2025-03-08T20:00:00Z", []))   # same id as the B1 payload event
    server.by_date[D_DATES["D1"]] = p
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    d1 = next(line for line in section(report(root), 4).splitlines() if line.startswith("| D1 |"))
    assert "B1 1" in d1 and "| 1 | 4 |" in d1


def test_new_team_name_detection(droot):
    root, server = droot
    norm = root / "pilot" / "normalized"
    norm.mkdir(parents=True, exist_ok=True)
    (norm / "team_names.csv").write_text("team_name,in_stage_a,in_stage_b\nDuke,no,yes\nNavy,no,yes\n", encoding="utf-8")
    before = (norm / "team_names.csv").read_bytes()
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    sec = section(report(root), 5)
    assert "D1: 8 unique team names; not in team_names.csv: 6 (Air Force Falcons; Army; Brown; Lehigh; Quinnipiac Bobcats; Yale)" in sec
    assert "No division or gender is decided" in sec
    assert (norm / "team_names.csv").read_bytes() == before                    # read-only
    assert not (norm / "odds_long.csv").exists()


def test_missing_team_names_file_is_not_fatal(droot):
    root, server = droot
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    assert "team_names.csv not found" in section(report(root), 5)


def test_credits_section_and_wording(droot):
    root, server = droot
    assert run(root, "--run", "--step", "D1", "--confirm-paid") == 0
    text = report(root)
    sec = section(text, 6)
    assert "Stage D spent (pessimistic, from the ledger): 30 of 60" in sec
    assert "Remaining against the 500 cap: 410" in sec and "Maximum still unspent in Stage D: 30" in sec
    assert "not games" in text and "No results data is joined" in text and "T1 to T3" in text
    assert "closing line" not in text.lower() and "no coverage" not in text.lower()


# ------------------------------------------------------------------ hygiene
def test_stage_d_imports_no_http_library():
    tree = ast.parse(Path(SD.__file__).read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not mods & {"requests", "httpx", "urllib", "urllib3", "socket", "http", "aiohttp"}


def test_key_never_written_by_stage_d(droot):
    root, server = droot
    assert run(root, "--run", "--step", "D1,D2", "--confirm-paid") == 0
    files = [p for p in root.rglob("*") if p.is_file()]
    assert [p for p in files if TESTKEY.encode() in p.read_bytes()] == []
