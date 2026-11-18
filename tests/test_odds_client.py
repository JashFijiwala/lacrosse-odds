import json
import logging
import os
import traceback
from unittest.mock import patch

import pytest
import requests

from src import cli
from src import halt as H
from src.ledger import COLUMNS
from src.odds_client import OddsClient, OddsClientError, OddsClientRefused, fingerprint

from .conftest import HIST, TESTKEY, FakeResponse, ok

PARAMS = {"regions": "us", "markets": "h2h", "date": "2026-04-11T16:00:00Z"}


def halt_code(client):
    h = H.read_halt(client.halt_path)
    return h and h["reason_code"]


def rows(client):
    return client.ledger.rows()


# ---------------------------------------------------------------- baseline
def test_historical_before_baseline_refused_without_network(client, mock_get):
    with pytest.raises(OddsClientRefused, match="baseline"):
        client.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert mock_get.call_count == 0
    assert not client.pending_path.exists()


def test_bootstrap_writes_row_then_baseline(client, mock_get):
    mock_get.side_effect = [ok(0, 100, body=[{"key": "lacrosse_ncaa"}])]
    base = client.bootstrap_baseline()
    assert base["used"] == 100 and base["remaining"] == 19900
    assert mock_get.call_count == 1
    url = mock_get.call_args.args[0]
    assert url == "https://api.the-odds-api.com/v4/sports"
    r = rows(client)
    assert len(r) == 1 and r[0]["cumulative_spent"] == "0" and r[0]["actual_cost"] == "0"
    assert json.loads(client.baseline_path.read_text())["used"] == 100


def test_bootstrap_refused_if_baseline_exists(booted, mock_get):
    with pytest.raises(OddsClientRefused, match="already exists"):
        booted.bootstrap_baseline()
    assert mock_get.call_count == 0


def test_free_path_rejects_non_allowlisted_params(client, mock_get):
    with pytest.raises(OddsClientRefused):
        client._free_call("BOOTSTRAP", "x", params={"regions": "us"})
    assert mock_get.call_count == 0


@pytest.mark.parametrize("resp,code", [
    (ok(1, 101, body=[]), "UNEXPECTED_COST"),
    (FakeResponse(200, b"[]"), "MISSING_HEADERS"),
])
def test_bootstrap_nonzero_cost_or_missing_headers_halts(client, mock_get, resp, code):
    mock_get.side_effect = [resp]
    with pytest.raises(OddsClientError):
        client.bootstrap_baseline()
    assert halt_code(client) == code
    assert not client.baseline_path.exists()


# ------------------------------------------------------ cap / stage / reserve
def test_stage_budget_refused_and_reserve_allows(booted, mock_get):
    with pytest.raises(OddsClientRefused, match="budget"):
        booted.request(HIST, PARAMS, expected_cost=(25, 25), stage="A")
    assert mock_get.call_count == 0
    mock_get.side_effect = [ok(25, 125)]
    booted.request(HIST, PARAMS, expected_cost=(25, 25), stage="A", use_reserve=True)
    last = rows(booted)[-1]
    assert "reserve_draw=5" in last["notes"] and last["cumulative_spent"] == "25"
    s = booted.ledger.summary(booted.budgets)
    assert s["reserve_used"] == 5


def test_reserve_insufficient_refused(booted, mock_get):
    with pytest.raises(OddsClientRefused, match="RESERVE insufficient"):
        booted.request(HIST, PARAMS, expected_cost=(91, 91), stage="A", use_reserve=True)
    assert mock_get.call_count == 0


def test_cumulative_reserve_draws_cannot_exceed_70(booted, mock_get):
    # Stage A budget 20. Draws: 5 (25 requested), then 60 (60 requested) = 65 of 70.
    mock_get.side_effect = [ok(25, 125), ok(60, 185)]
    booted.request(HIST, PARAMS, expected_cost=(25, 25), stage="A", use_reserve=True)
    booted.request(HIST, dict(PARAMS, n=2), expected_cost=(60, 60), stage="A", use_reserve=True)
    assert booted.ledger.summary(booted.budgets)["reserve_used"] == 65
    # Only 5 reserve credits remain; a further 10-credit overflow must be refused with no network call.
    with pytest.raises(OddsClientRefused, match="RESERVE insufficient"):
        booted.request(HIST, dict(PARAMS, n=3), expected_cost=(10, 10), stage="A", use_reserve=True)
    assert mock_get.call_count == 2
    assert booted.ledger.summary(booted.budgets)["reserve_used"] == 65


def test_global_cap_refused(booted, mock_get):
    prior = {c: "" for c in COLUMNS}
    prior.update(stage="C", fingerprint="x" * 64, actual_cost="235", cumulative_spent="495",
                 header_used="595", status_code="200", expected_cost_min="0", expected_cost_max="240")
    booted.ledger.append(prior)
    with pytest.raises(OddsClientRefused, match="cap"):
        booted.request(HIST, PARAMS, expected_cost=(1, 10), stage="D")
    assert mock_get.call_count == 0


def test_pessimistic_accounting_counts_unknown_at_max(booted, mock_get):
    mock_get.side_effect = [requests.exceptions.ConnectTimeout("boom")]
    with pytest.raises(OddsClientError):
        booted.request(HIST, PARAMS, expected_cost=(10, 30), stage="B")
    s = booted.ledger.summary(booted.budgets)
    assert s["committed"] == 30 and s["per_stage"]["B"] == 30


# ------------------------------------------------------------------- HALT
def test_halt_blocks_every_paid_request(booted, mock_get):
    H.write_halt(booted.halt_path, "NON_200", "test")
    for stage in ("A", "B", "C"):
        with pytest.raises(OddsClientRefused, match="HALT"):
            booted.request(HIST, dict(PARAMS, stage=stage), expected_cost=(1, 1), stage=stage)
    with pytest.raises(OddsClientRefused):
        booted.request("/v4/sports", {}, expected_cost=(0, 0), stage="A")
    assert mock_get.call_count == 0


def test_process_flag_blocks_when_halt_unwritable(booted, mock_get, monkeypatch, capsys):
    real_write = H.atomic_write_text

    def fail_on_halt(path, text):
        if os.path.basename(path) == "HALT":
            raise OSError("disk")
        return real_write(path, text)
    monkeypatch.setattr(H, "atomic_write_text", fail_on_halt)
    mock_get.side_effect = [ok(99, 199)]
    with pytest.raises(OddsClientError):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert H.process_halted()
    assert "COULD NOT BE WRITTEN" in capsys.readouterr().err
    with pytest.raises(OddsClientRefused, match="in-process"):
        booted.request(HIST, dict(PARAMS, x=1), expected_cost=(1, 1), stage="A")
    assert mock_get.call_count == 1


def test_unexpected_cost_creates_halt(booted, mock_get):
    mock_get.side_effect = [ok(12, 112)]
    with pytest.raises(OddsClientError, match="UNEXPECTED_COST"):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert halt_code(booted) == "UNEXPECTED_COST"
    assert rows(booted)[-1]["actual_cost"] == "12"
    assert not booted.pending_path.exists()


def test_last_vs_used_disagreement_creates_halt(booted, mock_get):
    mock_get.side_effect = [ok(10, 125)]
    with pytest.raises(OddsClientError, match="disagrees"):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert halt_code(booted) == "UNEXPECTED_COST"


# --------------------------------------------------------------- no retry
@pytest.mark.parametrize("status", [429, 500])
def test_no_retry_on_error_status(booted, mock_get, status):
    mock_get.side_effect = [FakeResponse(status, b'{"message":"x"}', last=0, used=100, remaining=19900)]
    with pytest.raises(OddsClientError, match="NON_200"):
        booted.request(HIST, PARAMS, expected_cost=(0, 10), stage="A")
    assert mock_get.call_count == 1
    assert halt_code(booted) == "NON_200"


def test_no_retry_on_timeout(booted, mock_get):
    mock_get.side_effect = [requests.exceptions.ReadTimeout("t")]
    with pytest.raises(OddsClientError, match="ReadTimeout"):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert mock_get.call_count == 1
    last = rows(booted)[-1]
    assert last["status_code"] == "NO_RESPONSE" and last["actual_cost"] == "UNKNOWN"
    assert halt_code(booted) == "NO_RESPONSE"
    assert mock_get.call_args.kwargs["timeout"] == 30


def test_session_adapters_have_zero_retries(client):
    for prefix in ("https://", "http://"):
        assert client._session.get_adapter(prefix + "x").max_retries.total == 0


# ------------------------------------------------------ persistence failures
def test_raw_save_failure_records_emergency_and_blocks_fingerprint(booted, mock_get, monkeypatch, tmp_path):
    mock_get.side_effect = [ok(10, 110)]
    with patch.object(booted, "_save_raw", side_effect=OSError("disk full")):
        with pytest.raises(OddsClientError, match="Credits were consumed") as ei:
            booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert "Do NOT retry" in str(ei.value)
    emergency = list(booted.ledger_dir.glob("emergency_*.json"))
    assert len(emergency) == 1
    rec = json.loads(emergency[0].read_text())
    assert rec["quota_headers"]["x-requests-last"] == "10" and rec["body_bytes"] > 0
    assert os.path.exists(rec["temp_copy"])
    last = rows(booted)[-1]
    assert last["raw_file"] == "SAVE_FAILED" and last["status_code"] == "PERSIST_FAILED"
    assert halt_code(booted) == "PERSISTENCE_FAILED"

    # Clearing without reconcile is refused.
    with pytest.raises(OddsClientRefused, match="RECONCILED"):
        booted.clear_halt("PERSISTENCE_FAILED", "body recovered from emergency temp copy")
    # Even if HALT disappears, the fingerprint stays blocked until reconciled.
    os.remove(booted.halt_path)
    with pytest.raises(OddsClientRefused, match="reconcile"):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert mock_get.call_count == 1

    mock_get.side_effect = [ok(0, 110, body=[])]
    out = booted.reconcile(fingerprint(HIST, PARAMS, "A"))
    assert out["unexplained_diff"] == 0
    mock_get.side_effect = [ok(10, 120)]
    booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert mock_get.call_count == 3


def test_ledger_append_failure_halts_and_keeps_raw_and_pending(booted, mock_get, monkeypatch):
    real_append = booted.ledger.append

    def boom(row):
        raise OSError("ledger locked")
    monkeypatch.setattr(booted.ledger, "append", boom)
    mock_get.side_effect = [ok(10, 110)]
    with pytest.raises(OddsClientError, match="LEDGER_WRITE_FAILED"):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert halt_code(booted) == "LEDGER_WRITE_FAILED"
    raws = [p for p in booted.raw_dir.glob("A_*.json") if not p.name.endswith(".meta.json")]
    assert len(raws) == 1 and json.loads(raws[0].read_text()) == {"data": []}
    assert booted.pending_path.exists()

    monkeypatch.setattr(booted.ledger, "append", real_append)
    fp = fingerprint(HIST, PARAMS, "A")
    mock_get.side_effect = [ok(0, 110, body=[])]
    out = booted.reconcile(fp)
    assert out["unexplained_diff"] == 10 and not out["unrecoverable"]
    assert not booted.pending_path.exists()
    booted.clear_halt("LEDGER_WRITE_FAILED", "raw file intact; reconciled 10 credits to fp")
    assert not booted.halt_path.exists()


def test_pending_at_startup_creates_halt(root, mock_get):
    led = root / "pilot" / "ledger"
    led.mkdir(parents=True)
    (led / "pending.json").write_text(json.dumps({"fingerprint": "f" * 64}))
    c = OddsClient(root=root)
    h = H.read_halt(c.halt_path)
    assert h["reason_code"] == "PENDING_FOUND" and h["fingerprint"] == "f" * 64


def test_corrupt_ledger_tail_creates_halt(booted, mock_get):
    with open(booted.ledger_path, "a", encoding="utf-8") as fh:
        fh.write('2026-01-01,A,"unterminated')
    c = OddsClient(root=booted.root)
    assert halt_code(c) == "LEDGER_CORRUPT"


def test_reconcile_mismatch_flags_unexpected_cost(booted, mock_get):
    mock_get.side_effect = [requests.exceptions.ConnectionError("c")]
    with pytest.raises(OddsClientError):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    mock_get.side_effect = [ok(0, 160, body=[])]
    with pytest.raises(OddsClientError, match="mismatch"):
        booted.reconcile(fingerprint(HIST, PARAMS, "A"))
    assert halt_code(booted) == "UNEXPECTED_COST"
    assert rows(booted)[-1]["status_code"] == "RECONCILE_MISMATCH"


# ---------------------------------------------------------------- duplicates
def test_duplicate_fingerprint_refused_unless_allowed(booted, mock_get):
    mock_get.side_effect = [ok(10, 110)]
    booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    with pytest.raises(OddsClientRefused, match="duplicate"):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert mock_get.call_count == 1
    mock_get.side_effect = [ok(10, 120)]
    booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A", allow_duplicate=True)
    assert "allow_duplicate=1" in rows(booted)[-1]["notes"]
    # Same params in a different stage is a different fingerprint.
    assert fingerprint(HIST, PARAMS, "A") != fingerprint(HIST, PARAMS, "B")


# ---------------------------------------------------------------- clear-halt
def test_clear_halt_rules(booted, mock_get, capsys):
    mock_get.side_effect = [ok(12, 112)]
    with pytest.raises(OddsClientError):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    good = "book charged 12 not 10; expected range widened for next call"
    with pytest.raises(OddsClientRefused, match="20 characters"):
        booted.clear_halt("UNEXPECTED_COST", "too short")
    with pytest.raises(OddsClientRefused, match="does not match"):
        booted.clear_halt("NON_200", good)
    with pytest.raises(OddsClientRefused, match="RECONCILED"):
        booted.clear_halt("UNEXPECTED_COST", good)
    assert halt_code(booted) == "UNEXPECTED_COST"

    mock_get.side_effect = [ok(0, 112, body=[])]
    assert cli.main(["--root", str(booted.root), "reconcile", "--fingerprint", fingerprint(HIST, PARAMS, "A")]) == 0
    assert cli.main(["--root", str(booted.root), "clear-halt", "--reason-code", "UNEXPECTED_COST",
                     "--resolution", good]) == 0
    assert not booted.halt_path.exists()
    hist = [json.loads(l) for l in booted.history_path.read_text().splitlines()]
    assert hist[-1]["resolution"] == good and hist[-1]["halt"]["reason_code"] == "UNEXPECTED_COST"
    assert "halt_cleared" in booted.events_path.read_text()


def test_clear_halt_corrupt_baseline_requires_valid_file(booted, mock_get):
    good_text = booted.baseline_path.read_text()
    booted.baseline_path.write_text("{not json")
    c = OddsClient(root=booted.root)
    assert halt_code(c) == "BASELINE_CORRUPT"
    with pytest.raises(OddsClientRefused, match="still does not validate"):
        c.clear_halt("BASELINE_CORRUPT", "restored baseline.json from backup copy")
    booted.baseline_path.write_text(good_text)
    c.clear_halt("BASELINE_CORRUPT", "restored baseline.json from backup copy")
    assert not c.halt_path.exists()


def test_cli_plan_and_status(booted, capsys):
    assert cli.main(["--root", str(booted.root), "plan"]) == 0
    out = capsys.readouterr().out
    assert "RESERVE" in out and "credit cap: 500" in out
    assert cli.main(["--root", str(booted.root), "ledger-status"]) == 0
    assert "pessimistic committed: 0 / cap 500" in capsys.readouterr().out


def test_missing_key_errors_without_value_or_network(booted, mock_get, monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY")
    with pytest.raises(OddsClientError, match="ODDS_API_KEY is not set"):
        booted.request(HIST, PARAMS, expected_cost=(10, 10), stage="A")
    assert mock_get.call_count == 0 and not booted.pending_path.exists()


def test_key_in_params_refused(booted, mock_get):
    with pytest.raises(OddsClientRefused, match="credentials"):
        booted.request(HIST, dict(PARAMS, apiKey="zzz"), expected_cost=(10, 10), stage="A")
    assert mock_get.call_count == 0


# ------------------------------------------------------------------ key leak
def test_key_never_written_logged_or_raised(root, mock_get, monkeypatch, caplog, capsys, tmp_path):
    caplog.set_level(logging.DEBUG)
    messages = []
    c = OddsClient(root=root)
    mock_get.side_effect = [ok(0, 100, body=[])]
    c.bootstrap_baseline()
    assert mock_get.call_args.kwargs["params"]["apiKey"] == TESTKEY  # sent, but only on the wire

    scenarios = [
        ok(10, 110),
        FakeResponse(500, b"oops", last=0, used=110, remaining=1),
        requests.exceptions.ConnectionError(f"Max retries for url /v4/x?apiKey={TESTKEY}"),
    ]
    for i, resp in enumerate(scenarios):
        if os.path.exists(c.halt_path):
            os.remove(c.halt_path)
        mock_get.side_effect = [resp]
        try:
            c.request(HIST, dict(PARAMS, n=i), expected_cost=(10, 10), stage="B")
        except OddsClientError as exc:
            messages.append(str(exc))
            messages.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            assert exc.__cause__ is None and exc.__context__ is None

    # raw-save failure path
    os.remove(c.halt_path)
    monkeypatch.setattr(c, "_save_raw", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    mock_get.side_effect = [ok(10, 120)]
    with pytest.raises(OddsClientError) as ei:
        c.request(HIST, dict(PARAMS, n="save"), expected_cost=(10, 10), stage="B")
    messages.append(str(ei.value))

    captured = capsys.readouterr()
    assert all(TESTKEY not in m for m in messages)
    assert TESTKEY not in caplog.text
    assert TESTKEY not in captured.out + captured.err
    hits = [p for p in tmp_path.rglob("*") if p.is_file() and TESTKEY.encode() in p.read_bytes()]
    assert hits == []
    assert any(tmp_path.rglob("*.json"))
