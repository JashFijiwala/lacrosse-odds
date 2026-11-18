import json
import shutil
import socket
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from src import halt as H

PROJECT = Path(__file__).resolve().parents[1]
TESTKEY = "TESTKEY123"
HIST = "/v4/historical/sports/lacrosse_ncaa/odds"


class FakeResponse:
    def __init__(self, status=200, body=b"[]", last=None, used=None, remaining=None, extra_headers=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        h = {}
        if last is not None:
            h["x-requests-last"] = str(last)
        if used is not None:
            h["x-requests-used"] = str(used)
        if remaining is not None:
            h["x-requests-remaining"] = str(remaining)
        h.update(extra_headers or {})
        self.headers = CaseInsensitiveDict(h)
        # A real response URL contains the key; the client must never persist it.
        self.url = f"https://api.the-odds-api.com/v4/sports?apiKey={TESTKEY}"


def ok(last, used, body=None, status=200):
    return FakeResponse(status=status, body=body if body is not None else {"data": []},
                        last=last, used=used, remaining=20000 - used)


@pytest.fixture(autouse=True)
def _guard(monkeypatch, tmp_path):
    """Block all real network access, set the fake key, isolate the system temp dir."""
    def no_net(*a, **k):
        raise AssertionError("real network access attempted in tests")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", no_net)
    monkeypatch.setenv("ODDS_API_KEY", TESTKEY)
    systemp = tmp_path / "systemp"
    systemp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(systemp))
    H.reset_process_flag_for_tests()
    yield
    H.reset_process_flag_for_tests()


@pytest.fixture
def root(tmp_path):
    cfg = tmp_path / "proj" / "pilot" / "config"
    cfg.mkdir(parents=True)
    shutil.copy(PROJECT / "pilot" / "config" / "pilot.yaml", cfg / "pilot.yaml")
    return tmp_path / "proj"


@pytest.fixture
def mock_get(monkeypatch):
    m = MagicMock(name="Session.get")
    monkeypatch.setattr(requests.Session, "get", m)
    return m


@pytest.fixture
def client(root, mock_get):
    from src.odds_client import OddsClient
    return OddsClient(root=root)


@pytest.fixture
def booted(client, mock_get):
    """Client with a baseline at used=100; mock call count reset afterwards."""
    mock_get.side_effect = [ok(0, 100, body=[{"key": "lacrosse_ncaa"}])]
    client.bootstrap_baseline()
    mock_get.reset_mock(side_effect=True, return_value=True)
    return client
