"""No payment requirement can gate any Seiche route or MCP tool.

Seiche is free forever, so it never carries a payment path, live or dormant.
This guard fails if one comes back:
- the x402 module, its configuration or its activation variables in the code;
- an HTTP 402 status, a payment header (PAYMENT-REQUIRED, PAYMENT-SIGNATURE,
  PAYMENT-RESPONSE, X-PAYMENT) or a payTo or price field on Seiche's own
  routes, its public OpenAPI document or its MCP tool list;
- a provisioning or payment route on the application, or at the edge in the
  api.seiche.info site of ops/Caddyfile;
- any difference in what the five formerly priced tools answer when a caller
  offers a payment header with the old activation variables set.

Out of scope by design: Undertow is a separate, paid product with its own x402
service under /undertow/x402/ (proxied by ops/Caddyfile and listed in the API
catalog), and groundcheck.seiche.info is a separate service. Neither is a
Seiche route or tool. Research values such as oil or gold prices are data, not
payment requirements.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from seiche import accounts, api, config, mcp_server, usage

ROOT = Path(__file__).resolve().parents[2]
FORMERLY_PRICED = ("funding_stress_forecast", "replay_asof", "desk_brief", "positioning_book", "ask_desk")
OLD_ACTIVATION = {
    "SEICHE_X402_PROFILE": "base-sepolia-testnet",
    "SEICHE_X402_PAY_TO": "0x000000000000000000000000000000000000dEaD",
}
PAYMENT_HEADERS = {"PAYMENT-SIGNATURE": "test-only-marker", "X-PAYMENT": "test-only-marker"}
PAYMENT_RESPONSE_HEADERS = ("payment-required", "payment-response", "x-payment-response")
PAYMENT_KEYS = {"price", "prices", "pricing", "payto", "pay_to", "x402", "x402version",
                "maxamountrequired", "paymentrequirements"}
PAYMENT_ROUTE = re.compile(
    r"x402|provision|payment|checkout|billing|invoice|purchase|pricing|upgrade|"
    r"stripe|btcpay|nowpayments|paypal",
    re.IGNORECASE,
)


def _rpc(method, params=None, msg_id=1):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


@pytest.fixture()
def client(tmp_path, monkeypatch, fake_snap):
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: fake_snap)
    monkeypatch.setattr(mcp_server, "_get_completed_snapshot", lambda: fake_snap)

    async def fake_snapshot(force=False):
        return fake_snap

    monkeypatch.setattr(api.assemble, "snapshot", fake_snapshot)
    monkeypatch.setattr(api, "_mcp_limiter", api._RateLimiter(api.MCP_RATE_LIMIT_PER_MIN))
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    monkeypatch.delenv(config.OPERATOR_USERS_ENV, raising=False)
    accounts.add_user("desk_free", "correct horse battery", tier="free")
    return TestClient(api.app)


def test_no_x402_module_or_payment_configuration():
    assert importlib.util.find_spec("seiche.x402") is None
    names = [name for name in dir(config) if re.search(r"X402|PAY_TO|PAYMENT|PRICES?_USD", name)]
    assert names == []


def test_backend_source_has_no_payment_gate():
    gate = re.compile(
        r"status_code\s*=\s*402\b|HTTPException\(\s*402\b|[\"']402[\"']\s*:|"
        r"PAYMENT-(REQUIRED|SIGNATURE|RESPONSE)|X-PAYMENT|\bpayTo\b|SEICHE_X402|x402Version"
    )
    found = []
    for path in sorted((ROOT / "backend" / "seiche").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            undertow_link = re.search(r"undertow", line, re.IGNORECASE)
            if gate.search(line) or (re.search(r"x402", line, re.IGNORECASE) and not undertow_link):
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:120]}")
    assert found == []


def test_no_payment_route_or_openapi_payment_response():
    paths = sorted({getattr(route, "path", "") for route in api.app.routes})
    assert paths
    assert [path for path in paths if PAYMENT_ROUTE.search(path)] == []
    document = json.dumps(api._public_openapi_document())
    assert '"402"' not in document
    assert not re.search(r"PAYMENT-|X-PAYMENT|payTo|x402", document, re.IGNORECASE)


@pytest.mark.parametrize("signed_in", [False, True])
def test_tool_list_carries_no_price_or_pay_to(client, monkeypatch, signed_in):
    for name, value in OLD_ACTIVATION.items():
        monkeypatch.setenv(name, value)
    headers = {}
    if signed_in:
        token = accounts.issue_token("desk_free", "free")["token"]
        headers["Authorization"] = f"Bearer {token}"
    response = client.post("/mcp", json=_rpc("tools/list"), headers={**headers, **PAYMENT_HEADERS})
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert tools
    assert {key.lower() for key in _keys(tools)} & PAYMENT_KEYS == set()
    assert not any(header in response.headers for header in PAYMENT_RESPONSE_HEADERS)


@pytest.mark.parametrize("tool", FORMERLY_PRICED)
def test_offering_payment_changes_nothing(client, monkeypatch, tool):
    call = _rpc("tools/call", {"name": tool, "arguments": {}})
    plain = client.post("/mcp", json=call)
    for name, value in OLD_ACTIVATION.items():
        monkeypatch.setenv(name, value)
    offered = client.post("/mcp", json=call, headers=PAYMENT_HEADERS)
    for response in (plain, offered):
        assert response.status_code != 402
        assert not any(header in response.headers for header in PAYMENT_RESPONSE_HEADERS)
    assert offered.status_code == plain.status_code
    assert offered.json() == plain.json()


def test_edge_allow_list_has_no_payment_path():
    caddyfile = (ROOT / "ops" / "Caddyfile").read_text(encoding="utf-8")
    start = caddyfile.index("\napi.seiche.info {\n")
    end = caddyfile.index("\n}\n", start)
    site = caddyfile[start:end]
    for forbidden in ("/api/provision", "X-Seiche-Signature", "PAYMENT-", "X-PAYMENT"):
        assert forbidden not in site, forbidden
    stray = [line.strip() for line in site.splitlines()
             if re.search(r"x402", line, re.IGNORECASE) and not re.search(r"undertow", line, re.IGNORECASE)]
    assert stray == []
