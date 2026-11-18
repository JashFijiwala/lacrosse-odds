import ast
import csv
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src import costs as C
from tests.test_analysis import book, ev, make_root, snap


def utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sight(eid, commence, snap_ts, source="B1", kind="BCD"):
    return {"event_id": eid, "commence": utc(commence), "snap_ts": utc(snap_ts), "source": source, "kind": kind}


# ------------------------------------------------------------ formulas / grid
def test_grid_arithmetic():
    assert C.grid_total(40, 6, 30, 2) == 14_400
    assert C.grid_total(40, 6, 30, 2, regions=2) == 28_800                      # regions=us,us2 doubles c
    rows = C.grid_rows()
    assert len(rows) == 3 * 4 * 2 * 3
    r = next(x for x in rows if (x["G"], x["s"], x["c"], x["seasons"]) == (40, 6, 30, 2))
    assert r["us"] == 14_400 and r["us_20k"] and r["us_100k"]
    assert r["us_us2"] == 28_800 and not r["us2_20k"] and r["us2_100k"]
    big = next(x for x in rows if (x["G"], x["s"], x["c"], x["seasons"]) == (60, 9, 30, 2))
    assert big["us"] == 32_400 and not big["us_20k"] and big["us_100k"]
    small = next(x for x in rows if (x["G"], x["s"], x["c"], x["seasons"]) == (25, 1, 10, 1))
    assert small["us"] == 250 and small["us_20k"] and small["us2_20k"]
    assert C.fits(20_000, 20_000) and not C.fits(20_001, 20_000)


def test_credit_formula_constants():
    assert C.odds_credits(3, 1) == 30 and C.odds_credits(1, 2) == 20 and C.odds_credits(3, 2) == 60
    assert C.events_credits(True) == 1 and C.events_credits(False) == 0
    assert C.event_odds_credits(2, 2) == 40 and C.event_odds_credits(3, 1) == 30
    assert C.scan_requests(90, 1) == 90 and C.scan_requests(90, 2) == 45 and C.scan_requests(120, 3) == 40
    assert C.scan_requests(91, 2) == 46                                         # ceil
    assert C.break_even_games_per_slot(3, 1) == 3 and C.break_even_games_per_slot(1, 3) == pytest.approx(1 / 3)


# ------------------------------------------------------------------ calendar
def test_local_date_midnight_utc_belongs_to_previous_day():
    assert C.local_date(utc("2025-02-01T00:00:00Z")) == date(2025, 1, 31)       # 19:00 EST
    assert C.local_date(utc("2025-02-01T04:59:00Z")) == date(2025, 1, 31)
    assert C.local_date(utc("2025-02-01T05:00:00Z")) == date(2025, 2, 1)        # 00:00 EST
    assert C.local_date(utc("2025-07-01T03:59:00Z")) == date(2025, 6, 30)       # EDT: 23:59
    assert C.local_date(utc("2025-07-01T04:00:00Z")) == date(2025, 7, 1)


def test_dst_change_2026_march_8():
    assert C.day_length_hours(date(2026, 3, 8)) == 23
    assert C.day_length_hours(date(2026, 3, 7)) == 24 and C.day_length_hours(date(2025, 11, 2)) == 25
    assert C.utc_offset_hours(utc("2026-03-08T06:59:00Z")) == -5                # 01:59 EST
    assert C.utc_offset_hours(utc("2026-03-08T07:00:00Z")) == -4                # 03:00 EDT
    assert C.local_date(utc("2026-03-08T07:30:00Z")) == date(2026, 3, 8)
    # 04:30Z on 9 Mar is 00:30 EDT on the 9th, but 23:30 on the 8th under a fixed UTC-5 offset
    assert C.local_date(utc("2026-03-09T04:30:00Z")) == date(2026, 3, 9)
    t = C.dst_transitions(date(2026, 3, 1), date(2026, 3, 31))
    assert [x["date"] for x in t] == [date(2026, 3, 8)] and (t[0]["before"], t[0]["after"]) == (-5, -4)
    assert [x["date"] for x in C.dst_transitions(date(2025, 3, 1), date(2025, 3, 31))] == [date(2025, 3, 9)]
    cal = C.build_calendar([sight("a", "2026-03-09T04:30:00Z", "2026-03-08T12:00:00Z"),
                            sight("b", "2026-03-08T04:30:00Z", "2026-03-07T12:00:00Z")])
    assert cal["events"]["a"]["local_date"] == date(2026, 3, 9)
    assert cal["fixed_minus5_differs"] == 1                                      # only 'a'
    assert cal["events"]["b"]["local_date"] == date(2026, 3, 7)                  # 23:30 EST on the 7th
    assert cal["dates"][date(2026, 3, 9)]["offset_h"] == -4


def test_calendar_groups_by_local_date_and_dedupes():
    s = [sight("e1", "2025-02-01T00:00:00Z", "2025-01-31T18:00:00Z", "A:P1", "A"),     # 31 Jan local
         sight("e1", "2025-02-01T00:00:00Z", "2025-01-31T20:00:00Z", "B1"),            # same event, second source
         sight("e2", "2025-02-01T16:00:00Z", "2025-01-31T20:00:00Z", "B1"),
         sight("e3", "2025-02-01T16:00:30Z", "2025-01-31T20:00:00Z", "B1")]
    cal = C.build_calendar(s)
    assert set(cal["events"]) == {"e1", "e2", "e3"}                              # deduped by event_id
    assert cal["events"]["e1"]["kinds"] == {"A", "BCD"}
    d31, d1 = date(2025, 1, 31), date(2025, 2, 1)
    assert cal["dates"][d31]["events"] == ["e1"] and sorted(cal["dates"][d1]["events"]) == ["e2", "e3"]
    assert cal["utc_date_differs"] == 1                                          # e1: UTC 1 Feb, local 31 Jan


def test_distinct_start_times_use_utc_minute():
    s = [sight("a", "2025-02-01T16:00:00Z", "2025-02-01T10:00:00Z"),
         sight("b", "2025-02-01T16:00:40Z", "2025-02-01T10:00:00Z"),            # same minute
         sight("c", "2025-02-01T16:01:00Z", "2025-02-01T10:00:00Z"),
         sight("d", "2025-02-01T18:00:00Z", "2025-02-01T10:00:00Z")]
    info = C.build_calendar(s)["dates"][date(2025, 2, 1)]
    assert len(info["events"]) == 4 and len(info["starts"]) == 3
    assert sorted(info["slot_events"].values()) == [1, 1, 2]
    assert C.hist_starts({date(2025, 2, 1): info}) == {3: 1}
    assert C.slot_sizes({date(2025, 2, 1): info}) == [2, 1, 1] or sorted(C.slot_sizes({date(2025, 2, 1): info})) == [1, 1, 2]


def test_partial_date_flag():
    d = date(2025, 2, 1)                                     # local day starts 05:00Z
    assert C.possibly_partial(d, utc("2025-02-01T11:55:00Z"))
    assert C.possibly_partial(d, utc("2025-02-01T05:00:01Z"))
    assert not C.possibly_partial(d, utc("2025-02-01T05:00:00Z"))
    assert not C.possibly_partial(d, utc("2025-01-31T19:00:00Z"))
    cal = C.build_calendar([sight("a", "2025-02-01T16:00:00Z", "2025-02-01T11:55:00Z", "A:P1", "A"),
                            sight("b", "2025-02-02T16:00:00Z", "2025-02-01T11:55:00Z", "A:P1", "A"),
                            sight("c", "2025-02-02T17:00:00Z", "2025-02-01T20:00:00Z", "B1")])
    assert cal["dates"][date(2025, 2, 1)]["partial"]
    assert not cal["dates"][date(2025, 2, 2)]["partial"]                         # first listed the day before
    assert cal["dates"][date(2025, 2, 2)]["first_cover_src"] == "A:P1"
    assert C.hist_starts(cal["dates"], only_complete=True) == {2: 1}


def test_commence_conflict_uses_latest_snapshot():
    cal = C.build_calendar([sight("x", "2026-03-28T16:00:00Z", "2026-03-27T16:00:00Z", "C6"),
                            sight("x", "2026-03-28T16:02:00Z", "2026-03-28T15:55:00Z", "B2")])
    assert cal["conflicts"] == 1 and cal["events"]["x"]["commence"] == utc("2026-03-28T16:02:00Z")
    assert cal["conflict_rows"][0]["times"] == {"2026-03-28T16:00Z": ["C6"], "2026-03-28T16:02Z": ["B2"]}


def test_event_histogram_bins():
    dates = {date(2025, 2, d): {"events": list(range(n)), "partial": False, "starts": set()}
             for d, n in ((1, 1), (2, 5), (3, 6), (4, 31), (5, 40))}
    h = C.hist_events(dates)
    assert h["1-5"] == 2 and h["6-10"] == 1 and h["31+"] == 2


# --------------------------------------------------------- pipeline fixture
def costs_root(tmp_path):
    ts1, ts2 = "2025-02-28T18:00:00Z", "2025-02-28T21:00:00Z"
    root = make_root(tmp_path, {
        "B1": snap(ts1, [ev("x", "2025-02-28T19:00:00Z", [book("draftkings"), book("fanduel")]),
                         ev("y", "2025-03-01T00:00:00Z", [book("betmgm", h2h=None, spread=(-2.5, -110, -110))]),
                         ev("z", "2025-02-28T20:00:00Z", [])]),
        "C4": snap(ts2, [ev("w", "2025-03-01T17:00:00Z", [book("draftkings", total=(10.5, -110, -110))])])})
    (root / "pilot" / "normalized").mkdir(parents=True)
    with open(root / "pilot" / "normalized" / "stage_a_events.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["probe_id", "requested_date", "returned_timestamp", "previous_timestamp", "next_timestamp", "event_id",
                    "commence_time_utc", "commence_time_et", "home_team", "away_team"])
        w.writerow(["P01", "x", "2025-02-28T11:55:38Z", "", "", "x", "2025-02-28T19:00:00Z", "", "H", "A"])
        w.writerow(["P01", "x", "2025-02-28T11:55:38Z", "", "", "q", "2025-02-28T22:00:00Z", "", "H", "A"])
    return root


def raw_digest(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / "pilot" / "raw").iterdir())}


def md_tables(text):
    """Yield (header cells, [row cells]) for every pipe table in the markdown."""
    lines, i = text.splitlines(), 0
    while i < len(lines):
        if lines[i].startswith("| ") and i + 1 < len(lines) and lines[i + 1].startswith("|---"):
            head = [c.strip() for c in lines[i].strip("|").split("|")]
            rows, i = [], i + 2
            while i < len(lines) and lines[i].startswith("| "):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            yield head, rows
        else:
            i += 1


def test_every_figure_has_one_of_three_labels(tmp_path):
    text, out = C.build(costs_root(tmp_path))
    n = 0
    for head, rows in md_tables(text):
        assert head[-1] == "Label", head
        for r in rows:
            assert r[-1] in C.LABELS, (head, r)
            n += 1
    assert n > 50
    assert set(C.LABELS) == {"MEASURED", "PROVIDER-DOCUMENTED", "ASSUMPTION"}
    for key, (headers, rows) in out["tables"].items():
        assert headers[-1] == "Label" and all(r[-1] in C.LABELS for r in rows), key
    # assumptions are never presented as measured: grid, scan, event-odds and illustration tables
    for key in ("t4", "t5", "t6a", "t6b", "t7"):
        assert {r[-1] for r in out["tables"][key][1]} == {"ASSUMPTION"}, key
    assert "G (game days per season) is NOT measured" in text and "UNVERIFIED" in text
    assert "illustrative, not a forecast" in text


def test_report_content_on_fixture(tmp_path):
    root = costs_root(tmp_path)
    text, out = C.build(root)
    cal = out["calendar"]
    assert set(cal["events"]) == {"x", "y", "z", "w", "q"}                       # deduped across A and B/C/D
    assert cal["events"]["y"]["local_date"] == date(2025, 2, 28)                 # 00:00Z -> previous local date
    d28, d1 = date(2025, 2, 28), date(2025, 3, 1)
    assert sorted(cal["dates"][d28]["events"]) == ["q", "x", "y", "z"] and cal["dates"][d1]["events"] == ["w"]
    assert cal["dates"][d28]["partial"]                                          # earliest listing 11:55Z, day began 05:00Z
    cov = out["coverage"]["all"]
    assert cov["n"] == 4 and cov["h2h1"] == 2 and cov["h2h2"] == 1 and cov["h2h3"] == 0
    assert cov["spreads1"] == 1 and cov["totals1"] == 1
    assert "Table 2b" in text and "Table 3b" in text and "Table 7" in text
    assert "Nothing here establishes season coverage or sufficiency" in text


def test_formula_confirmation_from_ledger(tmp_path):
    root = costs_root(tmp_path)
    rows = C.A.read_ledger_rows(root)
    for r in rows:
        r["params_redacted_json"] = json.dumps({"markets": "h2h,spreads,totals", "regions": "us"})
        r["actual_cost"] = "30"
    rows[0]["actual_cost"] = "25"                                                # a mismatch must be reported
    ev_ = C.ledger_evidence(root, rows)
    table = {r[0]: r for r in C.formula_rows(ev_)}
    assert table["odds, 3 market(s) x 1 region(s)"][3] == "MISMATCH"
    assert table["odds, 1 market(s) x 2 region(s)"][3] == "NOT YET OBSERVED"
    assert table["event-odds (any shape)"][3] == "NOT YET OBSERVED"
    rows = C.A.read_ledger_rows(root)
    table = {r[0]: r for r in C.formula_rows(C.ledger_evidence(root, rows))}
    assert table["odds, 3 market(s) x 1 region(s)"][3] == "NOT YET OBSERVED"       # fixture params are '{}'
    assert C.endpoint_kind("/v4/historical/sports/lacrosse_ncaa/events") == "events"
    assert C.endpoint_kind("/v4/historical/sports/lacrosse_ncaa/odds") == "odds"
    assert C.endpoint_kind("/v4/historical/sports/lacrosse_ncaa/events/abc/odds") == "event_odds"
    assert C.endpoint_kind("/v4/sports") is None


def test_formula_confirmed_with_real_shaped_rows(tmp_path):
    root = costs_root(tmp_path)
    rows = C.A.read_ledger_rows(root)
    for r in rows:
        r["params_redacted_json"] = json.dumps({"markets": "h2h,spreads,totals", "regions": "us"})
        r["actual_cost"] = "30"
    t = {r[0]: r for r in C.formula_rows(C.ledger_evidence(root, rows))}
    assert t["odds, 3 market(s) x 1 region(s)"][3] == "CONFIRMED"
    assert t["odds, 3 market(s) x 1 region(s)"][2].startswith("2 rows")


def test_outputs_written_and_inputs_untouched(tmp_path, capsys):
    root = costs_root(tmp_path)
    before_raw = raw_digest(root)
    ledger = (root / "pilot" / "ledger" / "credit_ledger.csv").read_bytes()
    sa = (root / "pilot" / "normalized" / "stage_a_events.csv").read_bytes()
    assert C.main(["--root", str(root)]) == 0
    out = (root / "pilot" / "normalized" / "cost_report.md").read_text(encoding="utf-8")
    assert out.startswith("# Calendar and full-acquisition cost estimator") and "Table 4" in out
    assert capsys.readouterr().out.startswith("# Calendar")
    assert raw_digest(root) == before_raw
    assert (root / "pilot" / "ledger" / "credit_ledger.csv").read_bytes() == ledger
    assert (root / "pilot" / "normalized" / "stage_a_events.csv").read_bytes() == sa
    assert not (root / "pilot" / "ledger" / "HALT").exists()


def test_missing_stage_a_is_tolerated(tmp_path):
    root = costs_root(tmp_path)
    (root / "pilot" / "normalized" / "stage_a_events.csv").unlink()
    text, out = C.build(root)
    assert "stage_a_events.csv not found" in text and set(out["calendar"]["events"]) == {"x", "y", "z", "w"}


def test_no_network_imports():
    tree = ast.parse(Path(C.__file__).read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0] or "." + ",".join(a.name for a in node.names))
    banned = {"requests", "httpx", "urllib", "urllib3", "http", "socket", "aiohttp", "ssl"}
    assert not (mods & banned), mods
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree)
                                                                       if isinstance(n, ast.Attribute)}
    assert "OddsClient" not in names
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported & {"odds_client", "stage_a", "stage_b", "stage_c", "stage_d", "OddsClient"} == set()
