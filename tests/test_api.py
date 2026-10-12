import base64
import hashlib
import importlib
import re
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTH_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("SYNTH_POLL_SECONDS", "300")
    monkeypatch.setenv("SYNTH_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    import synth.api
    mod = importlib.reload(synth.api)
    yield mod, TestClient(mod.app)          # not entered as a context: no background loop in tests


def test_health_says_when_the_poller_has_stalled(api):
    mod, c = api
    assert c.get("/health").status_code == 200                       # just started: not stale yet
    mod.agent.last_cycle = {"ts": time.time() - 3600}
    r = c.get("/health")
    assert r.status_code == 503 and r.json()["ok"] is False and r.json()["since_last_cycle_s"] >= 3600
    mod.agent.last_cycle = {"ts": time.time()}
    mod.LOOP_ERRORS["persona"] = {"ts": time.time(), "error": "RuntimeError: x"}
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["errors"]["persona"]["error"] == "RuntimeError: x"


def test_dashboard_runs_only_its_own_script(api):
    mod, c = api
    r = c.get("/")
    csp = r.headers["content-security-policy"]
    script = re.search(r"<script>(.*?)</script>", r.text, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}';" in csp and "unsafe-inline'; img" in csp and "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


def test_no_route_list_and_odd_keys_are_refused_not_crashed(api):
    mod, c = api
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404
    assert c.get("/picks", headers={"x-api-key": "clé".encode("latin-1")}).status_code == 401
    assert c.get("/picks", headers={"x-api-key": "k"}).status_code == 200


def test_ask_refuses_an_overlong_question(api):
    mod, c = api
    r = c.post("/ask", json={"question": "x" * 501}, headers={"x-api-key": "k"})
    assert r.status_code == 422
