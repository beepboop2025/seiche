"""The hosted MCP-over-HTTP transport and its usage meter.

Exercises the /mcp endpoint through FastAPI's TestClient with a canned snapshot
(no network) and an isolated usage DB (no shared state).
"""

import pytest
from fastapi.testclient import TestClient

from seiche import api, mcp_server, usage
from tests.test_mcp_server import FAKE_SNAP


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # no network: every tool reads the canned board
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: FAKE_SNAP)
    # isolated meter
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    # deterministic auth
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    monkeypatch.delenv("SEICHE_BOARD_AUTH", raising=False)
    return TestClient(api.app)


def _pro_token():
    from seiche import accounts

    return accounts.issue_token("desk_pro", "pro")["token"]


def _rpc(method, params=None, msg_id=1):
    m = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        m["id"] = msg_id
    if params is not None:
        m["params"] = params
    return m


# ---- handshake & surface ----------------------------------------------------

def test_initialize_returns_session_header(client):
    r = client.post("/mcp", json=_rpc("initialize", {"protocolVersion": "2025-06-18"}))
    assert r.status_code == 200
    assert r.json()["result"]["serverInfo"]["name"] == "seiche"
    assert r.headers.get("Mcp-Session-Id")


def test_anonymous_sees_only_public_tools(client):
    r = client.post("/mcp", json=_rpc("tools/list"))
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert "funding_stress_now" in names
    assert "positioning_book" not in names   # subscriber-only
    assert "ask_desk" not in names


def test_authenticated_sees_full_surface(client):
    r = client.post("/mcp", json=_rpc("tools/list"),
                    headers={"Authorization": f"Bearer {_pro_token()}"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert "positioning_book" in names


# ---- tool calls & metering --------------------------------------------------

def test_tool_call_returns_content_and_meters(client):
    r = client.post("/mcp", json=_rpc("tools/call",
                    {"name": "funding_stress_now", "arguments": {}}))
    assert r.status_code == 200
    assert "EROSION" in r.json()["result"]["content"][0]["text"]
    # the billable call was metered
    assert r.headers["X-MCP-Usage-Used"] == "1"
    assert r.headers["X-MCP-Usage-Limit"] == str(usage.MCP_ANON_DAILY)


def test_non_billable_methods_are_not_metered(client):
    client.post("/mcp", json=_rpc("tools/list"))
    r = client.get("/mcp/usage")
    assert r.json()["used_today"] == 0        # tools/list is free


def test_quota_exceeded_returns_upgrade_prompt(client, monkeypatch):
    monkeypatch.setattr(usage, "MCP_ANON_DAILY", 1)
    call = _rpc("tools/call", {"name": "data_health", "arguments": {}})
    first = client.post("/mcp", json=call)
    assert first.json()["result"].get("isError") is not True
    second = client.post("/mcp", json=call)
    res = second.json()["result"]
    assert res["isError"] is True
    assert "quota reached" in res["content"][0]["text"]
    assert "seiche.info" in res["content"][0]["text"]


def test_unlimited_tier_has_no_remaining_header(client):
    from seiche import accounts

    tok = accounts.issue_token("founder_1", "founder")["token"]
    r = client.post("/mcp", json=_rpc("tools/call",
                    {"name": "data_health", "arguments": {}}),
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.headers["X-MCP-Usage-Used"] == "1"
    assert "X-MCP-Usage-Limit" not in r.headers    # None => unlimited


# ---- protocol edges ---------------------------------------------------------

def test_notification_only_body_returns_202(client):
    r = client.post("/mcp", json=_rpc("notifications/initialized", msg_id=None))
    assert r.status_code == 202


def test_empty_body_is_400(client):
    r = client.post("/mcp")
    assert r.status_code == 400


def test_get_is_405(client):
    r = client.get("/mcp")
    assert r.status_code == 405
    assert r.headers["Allow"] == "POST"


def test_batch_returns_array(client):
    r = client.post("/mcp", json=[_rpc("ping", msg_id=1), _rpc("ping", msg_id=2)])
    body = r.json()
    assert isinstance(body, list) and len(body) == 2


# ---- usage report -----------------------------------------------------------

def test_usage_report_anonymous(client):
    r = client.get("/mcp/usage")
    j = r.json()
    assert j["tier"] == "anon"
    assert j["daily_limit"] == usage.MCP_ANON_DAILY
    assert "upgrade_url" in j


def test_usage_report_reflects_calls(client):
    client.post("/mcp", json=_rpc("tools/call", {"name": "data_health", "arguments": {}}))
    r = client.get("/mcp/usage")
    assert r.json()["used_today"] == 1
