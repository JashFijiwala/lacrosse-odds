import ast
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from src import analysis as A
from src.ledger import COLUMNS

ENDPOINT = "/v4/historical/sports/lacrosse_ncaa/odds"
H, W = "Home U", "Away U"


# ------------------------------------------------------------------ fixtures
def o(name, price, point=None):
    d = {"name": name, "price": price}
    if point is not None:
        d["point"] = point
    return d


def book(key, h2h=(-150, 125), spread=None, total=None, lu="2025-02-28T10:00:00Z", home=H, away=W):
    mk = []
    if h2h:
        mk.append({"key": "h2h", "last_update": lu, "outcomes": [o(home, h2h[0]), o(away, h2h[1])]})
    if spread:
        hp, pa, pb = spread                    # home point, home price, away price
        mk.append({"key": "spreads", "last_update": lu, "outcomes": [o(home, pa, hp), o(away, pb, -hp)]})
    if total:
        x, po, pu = total
        mk.append({"key": "totals", "last_update": lu, "outcomes": [o("Over", po, x), o("Under", pu, x)]})
    return {"key": key, "markets": mk}


def ev(eid, commence, books, home=H, away=W):
    return {"id": eid, "commence_time": commence, "home_team": home, "away_team": away, "bookmakers": books}


def snap(ts, events):
    return {"timestamp": ts, "previous_timestamp": "p", "next_timestamp": "n", "data": events}


def make_root(base: Path, payloads: dict) -> Path:
    """Ledger + raw files for step -> payload (purpose 'stage X step Sn')."""
    raw = base / "pilot" / "raw"
    raw.mkdir(parents=True)
    (base / "pilot" / "ledger").mkdir(parents=True)
    (base / "pilot" / "config").mkdir(parents=True)
    (base / "pilot" / "config" / "pilot.yaml").write_text("credit_cap: 500\n", encoding="utf-8")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\r\n")
    w.writeheader()
    cum = 0
    for i, (step, body) in enumerate(payloads.items()):
        path = raw / f"{step}_{i}.json"
        path.write_bytes(json.dumps(body).encode("utf-8"))
        cost = 30 if body["data"] else 0
        cum += cost
        w.writerow({c: "" for c in COLUMNS} | {
            "ts_utc": f"2026-11-09T00:00:{i:02d}Z", "stage": step[0], "purpose": f"stage {step[0]} step {step} x",
            "endpoint": ENDPOINT, "params_redacted_json": "{}", "fingerprint": f"fp{i}", "expected_cost_min": 0,
            "expected_cost_max": 30, "header_last": cost, "header_used": cum, "header_remaining": 20000 - cum,
            "actual_cost": cost, "cumulative_spent": cum, "status_code": "200", "raw_file": str(path)})
    (base / "pilot" / "ledger" / "credit_ledger.csv").write_text(buf.getvalue(), encoding="utf-8", newline="")
    return base


def loaded(root):
    inp = A.load_inputs(root)
    return inp, A.observations(inp["snapshots"])


# ------------------------------------------------------------------ formulas
def test_pi_formula_and_margins():
    assert A.pi(100) == pytest.approx(0.5) and A.pi(-100) == pytest.approx(0.5)
    assert A.pi(-120) == pytest.approx(120 / 220) and A.pi(150) == pytest.approx(100 / 250)
    assert A.pi(0) is None and A.pi("x") is None and A.pi(True) is None
    s, ov, hold = A.margin(100, -120)
    assert ov == pytest.approx(0.04545, abs=1e-5) and hold == pytest.approx(0.04348, abs=1e-5)
    assert A.margin(-110, -110)[2] == pytest.approx(0.04545, abs=1e-5)
    assert A.decimal_odds(150) == pytest.approx(2.5) and A.decimal_odds(-200) == pytest.approx(1.5)


def test_bucket_edges():
    assert A.bucket(0) is None and A.bucket(-1) is None
    assert A.bucket(1) == "(0,0.5h]" and A.bucket(1800) == "(0,0.5h]" and A.bucket(1801) == "(0.5,2h]"
    assert A.bucket(7200) == "(0.5,2h]" and A.bucket(21600) == "(2,6h]" and A.bucket(21601) == ">6h"


def test_pregame_rule_and_own_commence_time(tmp_path):
    ts = "2025-02-28T18:00:00Z"
    events = [ev("zero", ts, [book("draftkings")]),                                   # tts = 0
              ev("neg", "2025-02-28T17:00:00Z", [book("draftkings")]),               # tts < 0
              ev("one_s", "2025-02-28T18:00:01Z", [book("draftkings")]),             # tts = 1 s
              ev("later", "2025-02-28T21:00:00Z", [book("draftkings")])]             # tts = 3 h
    root = make_root(tmp_path, {"B1": snap(ts, events)})
    inp, obs = loaded(root)
    tts = {x["event_id"]: x["tts_s"] for x in obs}
    assert tts == {"zero": 0, "neg": -3600, "one_s": 1, "later": 10800}              # each event's own start
    assert {x["event_id"] for x in A.valid(obs)} == {"one_s", "later"}
    assert A.bucket(tts["one_s"]) == "(0,0.5h]" and A.bucket(tts["later"]) == "(2,6h]"
    text, _ = A.build(root)
    inv = next(line for line in text.splitlines() if line.startswith("| B1 | 2025 |"))
    assert inv.endswith("| 2 |")                                                     # post-start events excluded
    post = text.split("Post-start (tts <= 0)")[1]
    assert "| B1 | 2 | 0 | 0 |" in post                                              # per market counts


# ------------------------------------------------------------- event weighting
def test_event_weighted_latest_pregame_and_fallback(tmp_path):
    root = make_root(tmp_path, {
        "C2": snap("2025-02-27T19:00:00Z", [ev("x", "2025-02-28T19:00:00Z", [book("draftkings", h2h=(-150, 125))]),
                                            ev("y", "2025-02-28T12:00:00Z", [book("draftkings", h2h=(-200, 160))])]),
        "C4": snap("2025-02-28T18:00:00Z", [ev("x", "2025-02-28T19:00:00Z", [book("draftkings", h2h=(-110, -110))]),
                                            ev("y", "2025-02-28T12:00:00Z", [book("draftkings", h2h=(-300, 240))])]),
    })
    inp, obs = loaded(root)
    ew = A.event_weighted(obs)
    sel = {(x["event_id"], x["book"], x["market"]): x["step"] for x in ew}
    assert sel == {("x", "draftkings", "h2h"): "C4", ("y", "draftkings", "h2h"): "C2"}   # y post-start in C4
    assert len(ew) == len({A.ebm(x) for x in ew})                                    # exactly one per key
    pooled = [x["overround"] for x in A.valid(obs) if x["event_id"] == "x"]
    ew_x = [x["overround"] for x in ew if x["event_id"] == "x"]
    assert len(pooled) == 2 and len(ew_x) == 1 and sum(pooled) / 2 != pytest.approx(ew_x[0])


# ------------------------------------------------------------------ gap logic
def test_delta_cases():
    g = A.gap_analysis(2.5, 2.5)
    assert g["delta"] == 0 and g["cat"] == "=0" and not g["both_win"] and not g["both_lose"] and not g["pushes"]
    g = A.gap_analysis(6, 6.5)
    assert g["cat"] == ">0" and g["both_win"] == [] and g["pushes"] == [(6, "A pushes, B wins")]
    g = A.gap_analysis(6.5, 6.0)
    assert g["cat"] == "<0" and g["both_lose"] == [] and g["pushes"] == [(6, "B pushes, A loses")]


def test_integer_gap_middle():
    g = A.gap_analysis(2.5, 4.5)
    assert g["both_win"] == [3, 4] and g["both_lose"] == [] and g["delta"] == pytest.approx(2.0)
    assert ("win", "win") in g["categories"]


def test_guaranteed_outcome_lock_and_no_lock():
    cats = A.gap_analysis(2.5, 2.5)["categories"]
    assert sorted(cats) == [("loss", "win"), ("win", "loss")]
    assert A.pi(110) + A.pi(110) < 1 and A.guaranteed_outcome(cats, 110, 110)["locked"]
    assert A.pi(-110) + A.pi(-110) > 1 and not A.guaranteed_outcome(cats, -110, -110)["locked"]


def test_guaranteed_outcome_push_returns_stake():
    g = A.gap_analysis(3, 3.5)
    assert ("push", "win") in g["categories"]
    dA, dB = A.decimal_odds(110), A.decimal_odds(110)
    assert A.net_return(("push", "win"), 0.5, dA, dB) == pytest.approx(0.5 * (dB - 1))   # push leg returns its stake
    res = A.guaranteed_outcome(g["categories"], 110, 110)
    assert res["locked"] and res["best_worst"] > 0
    both = A.gap_analysis(3, 3)                                                       # both push at m = 3: net 0
    assert ("push", "push") in both["categories"]
    assert not A.guaranteed_outcome(both["categories"], 110, 110)["locked"]


# ------------------------------------------------------------- cross-book pairs
def test_cross_book_pairs_use_one_snapshot_only(tmp_path):
    root = make_root(tmp_path, {
        "C2": snap("2025-02-27T19:00:00Z", [ev("x", "2025-02-28T19:00:00Z", [book("draftkings", spread=(-2.5, 110, -130))])]),
        "C4": snap("2025-02-28T18:00:00Z", [ev("x", "2025-02-28T19:00:00Z", [book("fanduel", spread=(-2.5, -130, 110))])]),
    })
    inp, obs = loaded(root)
    assert A.cross_pairs(obs, "spreads") == []                                        # books never in one snapshot


def test_cross_book_pair_orientation_values(tmp_path):
    root = make_root(tmp_path, {"B1": snap("2025-02-28T18:55:00Z", [ev("x", "2025-02-28T19:00:00Z", [
        book("draftkings", spread=(-2.5, 110, -130), total=(20.5, -110, -110)),
        book("fanduel", spread=(-4.5, -130, 110), total=(21.5, 105, 105))])])})
    inp, obs = loaded(root)
    pairs = {(p["book1"], p["book2"]): p for p in A.cross_pairs(obs, "spreads")}
    p = pairs[("draftkings", "fanduel")]                  # home -2.5 at dk (a = 2.5), away +4.5 at fd (b = 4.5)
    assert (p["a"], p["b"], p["both_win"], p["step"]) == (2.5, 4.5, [3, 4], "B1") and p["group"] == "(0,2h]"
    q = pairs[("fanduel", "draftkings")]                  # home -4.5 at fd (a = 4.5), away +2.5 at dk (b = 2.5)
    assert q["both_lose"] == [3, 4] and q["cat"] == "<0"
    t = {(p["book1"], p["book2"]): p for p in A.cross_pairs(obs, "totals")}[("draftkings", "fanduel")]
    assert (t["a"], t["b"], t["both_win"]) == (20.5, 21.5, [21])                       # Over 20.5 dk, Under 21.5 fd


# ------------------------------------------------------------------ movement
def chain_root(tmp_path, c4_ts="2025-02-28T18:00:00Z"):
    start = "2025-02-28T19:00:00Z"
    return make_root(tmp_path, {
        "B1": snap("2025-02-28T18:55:00Z", [ev("x", start, [book("draftkings", h2h=(-200, 160)), book("fanduel")]),
                                            ev("z", start, [book("betmgm")])]),
        "C2": snap("2025-02-27T19:00:00Z", [ev("x", start, [book("draftkings", h2h=(-150, 125)), book("fanduel")])]),
        "C4": snap(c4_ts, [ev("x", start, [book("draftkings", h2h=(-150, 125)), book("fanduel")])]),
    })


def test_movement_event_vs_series_and_unchanged_repeats(tmp_path):
    root = chain_root(tmp_path)
    inp, obs = loaded(root)
    mv = A.movement(obs, set(A.CHAINS["2025 chain"]))
    ser = {(s["event_id"], s["book"]): s for s in mv["series"]}
    assert set(ser) == {("x", "draftkings"), ("x", "fanduel")}                       # z has one observation
    assert ser[("x", "draftkings")]["n_obs"] == 3 and ser[("x", "draftkings")]["price_changed"]
    assert not ser[("x", "fanduel")]["price_changed"]                                # identical repeats
    assert mv["events"] == {"x": {"price": True, "point": False}}                    # 1 event vs 2 series
    dk_pairs = [p for p in mv["pairs"] if p["book"] == "draftkings"]
    assert [p["changed"] for p in dk_pairs] == [False, True]                         # C2->C4 same, C4->B1 changed
    assert [p["bucket_from"] for p in dk_pairs] == [">6h", "(0.5,2h]"]


def test_duplicate_timestamps_collapse(tmp_path):
    root = chain_root(tmp_path, c4_ts="2025-02-27T19:00:00Z")                        # C4 returns C2's snapshot
    inp, obs = loaded(root)
    c4 = next(s for s in inp["snapshots"] if s["step"] == "C4")
    assert c4["dup_of"] == "C2" and all(x["step"] != "C4" for x in obs)
    mv = A.movement(obs, set(A.CHAINS["2025 chain"]))
    assert {s["n_obs"] for s in mv["series"]} == {2}


def test_movement_excludes_post_start_observations(tmp_path):
    start = "2025-02-28T18:30:00Z"
    root = make_root(tmp_path, {
        "C4": snap("2025-02-28T18:00:00Z", [ev("x", start, [book("draftkings", h2h=(-150, 125))])]),
        "B1": snap("2025-02-28T18:55:00Z", [ev("x", start, [book("draftkings", h2h=(-400, 300))])]),   # post-start
    })
    inp, obs = loaded(root)
    assert A.movement(obs, set(A.CHAINS["2025 chain"]))["series"] == []


# --------------------------------------------------------------- inputs, e2e
def test_b3_excluded(tmp_path):
    payload = snap("2026-03-28T15:55:00Z", [ev("n", "2026-03-28T16:00:00Z", [book("draftkings"), book("fliff")])])
    root = make_root(tmp_path, {"B2": payload, "B3": payload})
    inp = A.load_inputs(root)
    assert [s["step"] for s in inp["snapshots"]] == ["B2"] and inp["excluded"] == {"B3": 1}
    text, _ = A.build(root)
    assert "Excluded: B3" in text and "fliff 1" in text                              # fliff in inventory only


def test_end_to_end_outputs_and_raw_untouched(tmp_path, capsys):
    root = chain_root(tmp_path)
    raws = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "pilot" / "raw").iterdir()}
    assert A.main(["--root", str(root)]) == 0
    norm = root / "pilot" / "normalized"
    for name in ("analysis_report.md", "event_snapshots.csv", "margins.csv", "spread_pairs.csv", "totals_pairs.csv",
                 "movement_pairs.csv"):
        assert (norm / name).exists()
    text = (norm / "analysis_report.md").read_text(encoding="utf-8")
    assert "2 independent chains (one game day each)" in text and "THEORETICAL" in text
    assert "pi(p) = 100/(p+100)" in text and "hold = 1 - 1/(pi_A + pi_B)" in text
    for line in text.splitlines():
        if line.startswith("## ") and line[3].isdigit() and not line.startswith(("## 5", "## 6", "## 8")):
            assert "unit" in line.lower() or "pregame observations" in line, line
    assert "closing line" not in text.lower() and "no coverage" not in text.lower()
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "pilot" / "raw").iterdir()} == raws
    assert "## 7. Movement" in capsys.readouterr().out
    rows = list(csv.DictReader(open(norm / "margins.csv", encoding="utf-8")))
    assert sum(r["event_weighted_selected"] == "yes" for r in rows) == len({(r["event_id"], r["book"], r["market"]) for r in rows})


def test_no_network_imports():
    tree = ast.parse(Path(A.__file__).read_text(encoding="utf-8"))
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {("." * n.level) + (n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    roots = {m.lstrip(".").split(".")[0] for m in mods}
    assert not roots & {"requests", "httpx", "urllib", "urllib3", "socket", "http", "aiohttp", "odds_client",
                        "stage_b", "stage_c", "stage_d"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "OddsClient" not in names
