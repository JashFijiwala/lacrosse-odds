import ast
import copy
import shutil
from pathlib import Path

import pytest

from src import halt as H
from src import stage_b as SB
from src import stage_c as SC

from .conftest import PROJECT, TESTKEY, ok
from .test_stage_b import (B1_DATE, B2_DATE, Q_AWAY, Q_HOME, Q_ID, Server, event, full_book, h2h, mkt, n_payload, o,
                           payload, q_payload, write_anchors)

C_DATES = {"C1": "2025-02-26T19:00:00Z", "C2": "2025-02-27T19:00:00Z", "C3": "2025-02-28T13:00:00Z",
           "C4": "2025-02-28T18:00:00Z", "C5": "2026-03-26T16:00:00Z", "C6": "2026-03-27T16:00:00Z",
           "C7": "2026-03-28T10:00:00Z", "C8": "2026-03-28T15:00:00Z"}
B1_TS, B2_TS = "2025-02-28T18:55:38Z", "2026-03-28T15:55:38Z"


def q_snap(ts, dk_home_price=-150, extra=()):
    books = [full_book("draftkings", Q_HOME, Q_AWAY), {"key": "fanduel", "markets": [h2h(Q_HOME, Q_AWAY)]}]
    books[0]["markets"][0]["outcomes"][0]["price"] = dk_home_price
    other = event("other1", "Duke", "Navy", "2025-02-28T20:00:00Z",
                  [{"key": "betmgm", "markets": [h2h("Duke", "Navy")]}])
    return payload([event(Q_ID, Q_HOME, Q_AWAY, "2025-02-28T19:00:00Z", books), other, *extra], ts)


def n_snap(ts):
    return n_payload(ts)


def default_server(**overrides):
    by_date = {B1_DATE: q_payload(ts=B1_TS), B2_DATE: n_payload(ts=B2_TS),
               C_DATES["C1"]: q_snap("2025-02-26T18:55:39Z"), C_DATES["C2"]: q_snap("2025-02-27T18:55:39Z"),
               C_DATES["C3"]: q_snap("2025-02-28T12:55:39Z"), C_DATES["C4"]: q_snap("2025-02-28T17:55:39Z"),
               C_DATES["C5"]: n_snap("2026-03-26T15:55:37Z"), C_DATES["C6"]: n_snap("2026-03-27T15:55:37Z"),
               C_DATES["C7"]: n_snap("2026-03-28T09:55:37Z"), C_DATES["C8"]: n_snap("2026-03-28T14:55:37Z")}
    cost_override = overrides.pop("cost_override", None)
    by_date.update(overrides)
    return Server(by_date, cost_override=cost_override)


def make_root(base: Path, mock_get, server):
    """Fresh project root with a baseline and Stage B B1/B2 close-proxy snapshots (mocked)."""
    from src.odds_client import OddsClient
    cfg = base / "pilot" / "config"
    cfg.mkdir(parents=True)
    shutil.copy(PROJECT / "pilot" / "config" / "pilot.yaml", cfg / "pilot.yaml")
    write_anchors(base)
    mock_get.side_effect = [ok(0, 100, body=[{"key": "lacrosse_ncaa"}])]
    OddsClient(root=base).bootstrap_baseline()
    mock_get.side_effect = server
    assert SB.main(["--root", str(base), "--run", "--step", "B1", "--confirm-paid"]) == 0
    assert SB.main(["--root", str(base), "--run", "--step", "B2", "--confirm-paid"]) == 0
    server.calls.clear()
    return base


@pytest.fixture
def croot(tmp_path, mock_get):
    server = default_server()
    root = make_root(tmp_path / "proj", mock_get, server)
    return root, server


def run(root, *argv):
    return SC.main(["--root", str(root), *argv])


def report(root):
    return (Path(root) / "pilot" / "normalized" / "stage_c_report.md").read_text(encoding="utf-8")


def ledger_lines(root):
    return (Path(root) / "pilot" / "ledger" / "credit_ledger.csv").read_text(encoding="utf-8").splitlines()


def step_row(text, step):
    return next(line for line in text.splitlines() if line.startswith(f"| {step} | "))


def evaluated(root):
    anchors = SB.load_anchors(root)
    return SC.evaluate_all(root, SC._ledger(root).rows(), anchors), anchors


# ------------------------------------------------------------------ steps
def test_step_dates_and_tranches(tmp_path):
    write_anchors(tmp_path)
    anchors = SB.load_anchors(tmp_path)
    for s, d in C_DATES.items():
        assert SC.step_params(s, anchors) == {"date": d, "regions": "us", "markets": "h2h,spreads,totals",
                                               "oddsFormat": "american", "dateFormat": "iso"}
    assert SC.tranche_steps(1) == ["C2", "C4", "C6", "C8"]
    assert SC.tranche_steps(2) == ["C1", "C3", "C5", "C7"]
    write_anchors(tmp_path, q_start="2025-03-01T17:30:00Z")       # follows anchors.json, not hard-coded
    assert SC.step_params("C4", SB.load_anchors(tmp_path))["date"] == "2025-03-01T16:30:00Z"


def test_plan_shows_both_tranches_and_gate(croot, mock_get, capsys):
    root, server = croot
    assert run(root, "--plan") == 0
    out = capsys.readouterr().out
    t1 = out.index("Tranche 1 (recommended first")
    t2 = out.index("Tranche 2 (deferred pending user review; needs --confirm-paid AND --approve-tranche-2)")
    assert t1 < t2
    assert out.count("max 120 credits") == 2 and "total maximum: 240 credits of 240" in out
    assert out.count("max cost 30") == 8 and f"date={C_DATES['C2']}" in out
    assert "status=" + SC.NOT_RUN_T1 in out and "status=" + SC.NOT_RUN_T2 in out
    assert out.index("  C2 ") < t2 < out.index("  C1 ")
    assert server.calls == [] and "No request was made." in out


# --------------------------------------------------------------- refusals
def test_run_without_confirm_paid_refused(croot):
    root, server = croot
    before = ledger_lines(root)
    assert run(root, "--run", "--step", "C2") == 2
    assert server.calls == [] and ledger_lines(root) == before


def test_tranche2_refused_before_any_call_and_no_ledger_row(croot, capsys):
    root, server = croot
    before = ledger_lines(root)
    assert run(root, "--run", "--step", "C1", "--confirm-paid") == 2
    assert run(root, "--run", "--step", "C2,C3", "--confirm-paid") == 2      # mixed list: nothing runs
    out = capsys.readouterr().out
    assert "REFUSED" in out and "--approve-tranche-2" in out and "no ledger row" in out
    assert server.calls == [] and ledger_lines(root) == before
    assert not (root / "pilot" / "ledger" / "pending.json").exists()
    assert run(root, "--run", "--step", "C1", "--confirm-paid", "--approve-tranche-2") == 0
    assert len(server.calls) == 1 and server.calls[0][1]["date"] == C_DATES["C1"]


def test_rerun_of_executed_step_refused_without_request(croot, capsys):
    root, server = croot
    assert run(root, "--run", "--step", "C2", "--confirm-paid") == 0
    n = len(ledger_lines(root))
    capsys.readouterr()
    assert run(root, "--run", "--step", "C2", "--confirm-paid") == 2
    out = capsys.readouterr().out
    assert f"C2 is already recorded in the ledger (line {n}," in out
    assert len(server.calls) == 1 and len(ledger_lines(root)) == n
    assert run(root, "--run", "--step", "C4,C2", "--confirm-paid") == 2      # whole list refused
    assert len(server.calls) == 1


def test_unknown_and_repeated_steps_refused(croot):
    root, server = croot
    assert run(root, "--run", "--step", "C9", "--confirm-paid") == 2
    assert run(root, "--run", "--step", "C2,C2", "--confirm-paid") == 2
    assert server.calls == []


# --------------------------------------------------------------- cost check
def test_nonempty_payload_wrong_cost_halts(tmp_path, mock_get):
    server = default_server(cost_override={C_DATES["C2"]: 10})
    root = make_root(tmp_path / "p", mock_get, server)
    assert run(root, "--run", "--step", "C2", "--confirm-paid") == 2
    halt = H.read_halt(root / "pilot" / "ledger" / "HALT")
    assert halt["reason_code"] == "UNEXPECTED_COST" and halt["fingerprint"]
    assert f"| C2 | 1 | {SC.HALTED} |" in report(root)


def test_empty_payload_zero_cost_is_a_finding(croot):
    root, server = croot
    server.by_date[C_DATES["C6"]] = payload([], "2026-03-27T15:55:37Z")
    assert run(root, "--run", "--step", "C6", "--confirm-paid") == 0
    assert H.read_halt(root / "pilot" / "ledger" / "HALT") is None
    assert f"| C6 | 1 | {SC.EMPTY_PAYLOAD} |" in step_row(report(root), "C6")


def test_empty_payload_nonzero_cost_halts(tmp_path, mock_get):
    server = default_server(cost_override={C_DATES["C6"]: 30})
    server.by_date[C_DATES["C6"]] = payload([], "2026-03-27T15:55:37Z")
    root = make_root(tmp_path / "p", mock_get, server)
    assert run(root, "--run", "--step", "C6", "--confirm-paid") == 2
    assert H.read_halt(root / "pilot" / "ledger" / "HALT")["reason_code"] == "UNEXPECTED_COST"


def test_anchor_absent_recorded_as_finding(croot):
    root, server = croot
    p = q_snap("2025-02-27T18:55:39Z")
    p["data"] = p["data"][1:]
    server.by_date[C_DATES["C2"]] = p
    assert run(root, "--run", "--step", "C2", "--confirm-paid") == 0
    assert H.read_halt(root / "pilot" / "ledger" / "HALT") is None
    row = step_row(report(root), "C2")
    assert f"| {SC.ANCHOR_ABSENT} |" in row and "| absent |" in row


def test_stop_on_first_halt_in_comma_list(tmp_path, mock_get, capsys):
    server = default_server(cost_override={C_DATES["C4"]: 10})
    root = make_root(tmp_path / "p", mock_get, server)
    assert run(root, "--run", "--step", "C2,C4,C6", "--confirm-paid") == 2
    out = capsys.readouterr().out
    assert [c[1]["date"] for c in server.calls] == [C_DATES["C2"], C_DATES["C4"]]
    assert "Skipped (not requested): C6" in out and "HALT UNEXPECTED_COST" in out
    text = report(root)
    assert f"| {SC.ANCHOR_PRESENT} |" in step_row(text, "C2")       # completed step stays recorded
    assert f"| {SC.HALTED} |" in step_row(text, "C4")
    assert SC.NOT_RUN_T1 in step_row(text, "C6")


# -------------------------------------------------------------- report logic
def tables(text):
    """Sections 1 to 4 of the report (everything before Credits)."""
    return text.split("## 5. Credits")[0]


def test_order_independence(tmp_path, mock_get):
    changed = {C_DATES["C2"]: q_snap("2025-02-27T18:55:39Z", dk_home_price=-200),
               C_DATES["C8"]: n_snap("2026-03-28T14:55:37Z")}
    a = make_root(tmp_path / "a", mock_get, default_server(**copy.deepcopy(changed)))
    assert run(a, "--run", "--step", "C8,C2", "--confirm-paid") == 0
    b = make_root(tmp_path / "b", mock_get, default_server(**copy.deepcopy(changed)))
    assert run(b, "--run", "--step", "C2", "--confirm-paid") == 0
    assert run(b, "--run", "--step", "C8", "--confirm-paid") == 0
    ta, tb = report(a), report(b)
    assert tables(ta) == tables(tb) and ta == tb
    # columns are in returned-timestamp order: C2 (24h before) precedes the B1 close-proxy
    assert ta.index("| C2 2025-02-27T18:55:39Z |") < ta.index("| B1 close-proxy 2025-02-28T18:55:38Z |")


def test_identical_snapshot_adds_one_observation_per_event(croot):
    root, server = croot
    same = "2025-02-28T12:55:39Z"
    only_here = event("other2", "Army", "Navy", "2025-02-28T21:00:00Z",
                      [{"key": "draftkings", "markets": [h2h("Army", "Navy")]}])
    server.by_date[C_DATES["C3"]] = q_snap(same, extra=[only_here])
    server.by_date[C_DATES["C4"]] = q_snap(same, extra=[only_here])
    assert run(root, "--run", "--step", "C4", "--confirm-paid") == 0           # executed first
    assert run(root, "--run", "--step", "C3", "--confirm-paid", "--approve-tranche-2") == 0
    ev, anchors = evaluated(root)
    ch = SC.chain("quinnipiac_air_force", ev, SC.close_proxy_snapshots(root))
    assert [s["label"] for s in ch["snapshots"]] == ["C3", "B1 close-proxy"]
    assert ch["dup_of"] == {"C4": "C3"}                                         # priority, not execution order
    summ = SC.series_summary(ch["snapshots"])
    # anchor: dk h2h/spreads/totals + fd h2h; other1 betmgm h2h. other2 has one observation: not a series.
    assert summ["all"]["observed"] == 5
    text = report(root)
    assert "same snapshot as C3 (adds no observation)" in step_row(text, "C4")
    assert f"| {SC.ANCHOR_PRESENT} |" in step_row(text, "C3")
    sec4 = text.split("## 4.")[1].split("## 5.")[0]
    assert "| C4 | same snapshot as C3 (adds no observation) |" in sec4
    assert "| C3 | B1 close-proxy | 3 | 2 |" in sec4                            # other2 only in this snapshot
    sec2 = text.split("## 2.")[1].split("## 3.")[0]
    assert "C4 2025" not in sec2 and "C4: same snapshot as C3" in sec2


def test_unchanged_repeated_prices_are_not_movement(croot):
    root, server = croot
    server.by_date[C_DATES["C4"]] = q_snap("2025-02-28T17:55:39Z")          # same prices as B1
    assert run(root, "--run", "--step", "C4", "--confirm-paid") == 0
    ev, _ = evaluated(root)
    ch = SC.chain("quinnipiac_air_force", ev, SC.close_proxy_snapshots(root))
    summ = SC.series_summary(ch["snapshots"])
    assert summ["all"]["observed"] == 5 and summ["all"]["price_changed"] == 0 and summ["all"]["point_changed"] == 0
    assert " [changed] (lu " not in report(root).split("## 2.")[1].split("## 3.")[0]


def test_changed_price_marked_in_movement_table(croot):
    root, server = croot
    server.by_date[C_DATES["C4"]] = q_snap("2025-02-28T17:55:39Z", dk_home_price=-200)
    assert run(root, "--run", "--step", "C4", "--confirm-paid") == 0
    sec2 = report(root).split("## 2.")[1].split("## 3.")[0]
    # C4 (-200) precedes B1 (-150) in time, so the B1 cell carries the mark
    assert sec2.count(" [changed] (lu ") == 1
    assert f"| draftkings | h2h | {Q_HOME} | -200 (lu 2025-02-28T18:50:00Z) | -150 [changed] (lu " in sec2


def test_price_change_counted_once_with_delta_pi():
    snaps = [{"events": q_snap("t1")["data"]}, {"events": q_snap("t2", dk_home_price=-200)["data"]},
             {"events": q_snap("t3", dk_home_price=-200)["data"]}]
    summ = SC.series_summary(snaps)
    assert summ["h2h"]["observed"] == 3 and summ["h2h"]["price_changed"] == 1
    assert summ["h2h"]["dpi"] == [pytest.approx(200 / 300 - 150 / 250)]
    rows = SC.anchor_movement_rows(Q_ID, [{"events": s["events"]} for s in snaps])
    dk_home = next(r for r in rows if r["book"] == "draftkings" and r["market"] == "h2h" and r["outcome"] == Q_HOME)
    assert [x["changed"] for x in dk_home["obs"]] == [False, True, False]


def test_report_tranche2_not_run_zero_credits(croot):
    root, server = croot
    assert run(root, "--run", "--step", "C2,C4,C6,C8", "--confirm-paid") == 0
    assert len(server.calls) == 4
    text = report(root)
    for s in ("C1", "C3", "C5", "C7"):
        row = step_row(text, s)
        assert f"| {s} | 2 | {SC.NOT_RUN_T2} |" in row and "| 0 | - |" in row
    assert "Stage C spent (pessimistic, from the ledger): 120 of 240" in text
    assert "tranche 1 0, tranche 2 120" in text
    assert "Remaining against the 500 cap: 320" in text
    assert "closing line" not in text.lower() and "no coverage" not in text.lower()


def test_anchor_classification_uses_sum_to_zero_spreads(croot):
    root, server = croot
    p = q_snap("2025-02-27T18:55:39Z")
    p["data"][0]["bookmakers"][0]["markets"][1] = mkt("spreads", [o(Q_HOME, -110, 0), o(Q_AWAY, -110, 0)])
    server.by_date[C_DATES["C2"]] = p
    assert run(root, "--run", "--step", "C2", "--confirm-paid") == 0
    ev, _ = evaluated(root)
    dk = next(b for b in ev["C2"]["books"] if b["key"] == "draftkings")
    assert dk["spreads"] == SC.N.COMPLETE


# ------------------------------------------------------------------ hygiene
def test_stage_c_imports_no_http_library():
    tree = ast.parse(Path(SC.__file__).read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not mods & {"requests", "httpx", "urllib", "urllib3", "socket", "http", "aiohttp"}


def test_key_never_written_by_stage_c(croot):
    root, server = croot
    assert run(root, "--run", "--step", "C2,C6", "--confirm-paid") == 0
    files = [p for p in root.rglob("*") if p.is_file()]
    assert [p for p in files if TESTKEY.encode() in p.read_bytes()] == []
