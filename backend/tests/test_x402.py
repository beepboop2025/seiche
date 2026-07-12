"""x402 pay-per-call on /mcp: off by default, fail-closed everywhere else.

The facilitator is monkeypatched — no network. What's under test is the
gating logic: who gets 402, who gets served, and that nothing is ever served
on an unverified or unsettled payment.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from seiche import api, mcp_server, usage, x402

PAID_TOOL = "funding_stress_forecast"


@pytest.fixture()
def client(tmp_path, monkeypatch, fake_snap):
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: fake_snap)
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    # The fully-open default serves everyone the full surface; these tests
    # pin the RE-GATED configuration where anonymous shaping still applies.
    monkeypatch.setenv("SEICHE_BOARD_AUTH", "1")
    monkeypatch.delenv("SEICHE_X402_PAY_TO", raising=False)
    return TestClient(api.app)


def _call(tool, msg_id=1):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": tool, "arguments": {}}}


def _payment_header(payload=None):
    return base64.b64encode(json.dumps(
        payload if payload is not None else
        {"x402Version": 1, "scheme": "exact", "network": "base",
         "payload": {"signature": "0xsig", "authorization": {}}}
    ).encode()).decode()


def _enable(monkeypatch):
    monkeypatch.setenv("SEICHE_X402_PAY_TO", "0x000000000000000000000000000000000000dEaD")


# ---- off by default ---------------------------------------------------------

def test_disabled_means_old_behavior(client):
    r = client.post("/mcp", json=_call(PAID_TOOL))
    assert r.status_code == 200            # JSON-RPC error result, not HTTP 402
    assert "error" in r.json()


# ---- payment-required path --------------------------------------------------

def test_anon_priced_tool_gets_402_with_requirements(client, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/mcp", json=_call(PAID_TOOL))
    assert r.status_code == 402
    body = r.json()
    assert body["x402Version"] == 1
    req = body["accepts"][0]
    assert req["scheme"] == "exact"
    assert req["payTo"].endswith("dEaD")
    assert req["maxAmountRequired"] == "20000"   # $0.02 in USDC atomic units


def test_public_tools_stay_free(client, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/mcp", json=_call("funding_stress_now"))
    assert r.status_code == 200
    assert "result" in r.json()


def test_subscriber_token_never_sees_402(client, monkeypatch):
    _enable(monkeypatch)
    from seiche import accounts
    token = accounts.issue_token("desk_pro", "pro")["token"]
    r = client.post("/mcp", json=_call(PAID_TOOL),
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "result" in r.json()


def test_tools_list_advertises_paid_tools(client, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert PAID_TOOL in names
    assert "x402" in names[PAID_TOOL]["description"]
    # and without x402 the same tool stays hidden
    monkeypatch.delenv("SEICHE_X402_PAY_TO")
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert PAID_TOOL not in {t["name"] for t in r.json()["result"]["tools"]}


# ---- paid path (facilitator mocked) ------------------------------------------

def test_valid_payment_serves_tool_and_returns_receipt(client, monkeypatch):
    _enable(monkeypatch)
    calls = []

    def fake_post(path, body):
        calls.append(path)
        if path == "/verify":
            return {"isValid": True}
        return {"success": True, "transaction": "0xtx", "network": "base"}

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    r = client.post("/mcp", json=_call(PAID_TOOL),
                    headers={"X-PAYMENT": _payment_header()})
    assert r.status_code == 200
    assert "result" in r.json()
    assert calls == ["/verify", "/settle"]
    receipt = json.loads(base64.b64decode(r.headers["X-PAYMENT-RESPONSE"]))
    assert receipt["transaction"] == "0xtx"


def test_invalid_payment_is_refused(client, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(x402, "_facilitator_post",
                        lambda path, body: {"isValid": False, "invalidReason": "bad signature"})
    r = client.post("/mcp", json=_call(PAID_TOOL),
                    headers={"X-PAYMENT": _payment_header()})
    assert r.status_code == 402
    assert "bad signature" in r.json()["error"]


def test_settle_failure_serves_nothing(client, monkeypatch):
    _enable(monkeypatch)

    def fake_post(path, body):
        if path == "/verify":
            return {"isValid": True}
        return {"success": False, "errorReason": "insufficient funds"}

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    r = client.post("/mcp", json=_call(PAID_TOOL),
                    headers={"X-PAYMENT": _payment_header()})
    assert r.status_code == 402
    assert "insufficient funds" in r.json()["error"]


def test_facilitator_outage_fails_closed(client, monkeypatch):
    _enable(monkeypatch)

    def fake_post(path, body):
        raise ConnectionError("facilitator down")

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    r = client.post("/mcp", json=_call(PAID_TOOL),
                    headers={"X-PAYMENT": _payment_header()})
    assert r.status_code == 402


def test_malformed_payment_header_is_402(client, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/mcp", json=_call(PAID_TOOL),
                    headers={"X-PAYMENT": "not-base64!!"})
    assert r.status_code == 402
    assert "malformed" in r.json()["error"]


def test_payment_on_batch_or_free_tool_is_rejected(client, monkeypatch):
    _enable(monkeypatch)
    # batch + payment: refused outright
    r = client.post("/mcp", json=[_call(PAID_TOOL, 1), _call(PAID_TOOL, 2)],
                    headers={"X-PAYMENT": _payment_header()})
    assert r.status_code == 400
    # payment attached to a free tool: also refused (nothing to buy)
    r = client.post("/mcp", json=_call("funding_stress_now"),
                    headers={"X-PAYMENT": _payment_header()})
    assert r.status_code == 400


def test_paid_call_does_not_burn_anon_quota(client, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(x402, "_facilitator_post",
                        lambda path, body: {"isValid": True, "success": True,
                                            "transaction": "0xtx", "network": "base"})
    r = client.post("/mcp", json=_call(PAID_TOOL),
                    headers={"X-PAYMENT": _payment_header()})
    assert r.status_code == 200
    assert "X-MCP-Usage-Used" not in r.headers
