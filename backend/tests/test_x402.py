"""x402 v2 pay-per-call: dormant by default and fail-closed when activated.

No test contacts a real facilitator. The suite exercises the wire contract,
profile boundary, local preflight, current-account bearer bypass, and the
verify-before-settle ordering around one priced MCP call.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from seiche import accounts, api, mcp_server, usage, x402

PAID_TOOL = "funding_stress_forecast"
RESOURCE = "https://api.seiche.info/mcp"
PAY_TO = "0x000000000000000000000000000000000000dEaD"
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
BASE_MAINNET_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
CDP_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"
PAYER = "0x1111111111111111111111111111111111111111"
TRANSACTION = "0x" + "ab" * 32

X402_ENV = (
    "SEICHE_X402_PROFILE",
    "SEICHE_X402_PAY_TO",
    "SEICHE_X402_NETWORK",
    "SEICHE_X402_FACILITATOR",
    "SEICHE_X402_ASSET",
    "SEICHE_X402_FACILITATOR_AUTHORIZATION",
)


@pytest.fixture()
def client(tmp_path, monkeypatch, fake_snap):
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: fake_snap)
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setattr(
        api, "_mcp_limiter", api._RateLimiter(api.MCP_RATE_LIMIT_PER_MIN)
    )
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    # The fully-open default serves everyone the full surface; these tests pin
    # the re-gated configuration where anonymous shaping still applies.
    monkeypatch.setenv("SEICHE_BOARD_AUTH", "1")
    for name in X402_ENV:
        monkeypatch.delenv(name, raising=False)
    accounts.add_user("desk_pro", "correct horse battery", tier="pro")
    return TestClient(api.app)


def _call(tool, msg_id=1, arguments=None):
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": {} if arguments is None else arguments,
        },
    }


def _enable_testnet(monkeypatch):
    monkeypatch.setenv("SEICHE_X402_PROFILE", "base-sepolia-testnet")
    monkeypatch.setenv("SEICHE_X402_PAY_TO", PAY_TO)


def _payment_payload(reqs=None):
    return {
        "x402Version": 2,
        "accepted": (
            reqs if reqs is not None else x402.requirements(PAID_TOOL, RESOURCE)
        ),
        "payload": {"signature": "0xsig", "authorization": {}},
        "extensions": {},
    }


def _payment_header(payload=None):
    document = payload if payload is not None else _payment_payload()
    return base64.b64encode(json.dumps(document).encode()).decode()


def _decode_header(value):
    return json.loads(base64.b64decode(value))


# ---- activation profiles ---------------------------------------------------


def test_disabled_means_old_behavior(client):
    assert x402.activation_attempted() is False
    assert x402.enabled() is False
    response = client.post("/mcp", json=_call(PAID_TOOL))
    assert response.status_code == 200  # JSON-RPC error, not HTTP 402
    assert "error" in response.json()


def test_non_activation_hints_leave_deployment_dormant(client, monkeypatch):
    monkeypatch.setenv("SEICHE_X402_NETWORK", "eip155:84532")
    monkeypatch.setenv("SEICHE_X402_FACILITATOR", "https://x402.org/facilitator")
    assert x402.activation_attempted() is False
    assert x402.enabled() is False
    assert client.post("/mcp", json=_call("funding_stress_now")).status_code == 200


def test_pay_to_only_is_explicitly_rejected_before_facilitator(client, monkeypatch):
    monkeypatch.setenv("SEICHE_X402_PAY_TO", PAY_TO)
    calls = []
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: calls.append((path, body)) or {"isValid": True},
    )

    response = client.post("/mcp", json=_call(PAID_TOOL))

    assert response.status_code == 503
    assert "SEICHE_X402_PROFILE is required" in response.json()["error"]["message"]
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == []
    # A broken dormant rail cannot take down the permanent free surface.
    assert client.post("/mcp", json=_call("funding_stress_now")).status_code == 200


def test_invalid_bearer_precedes_invalid_activation(client, monkeypatch):
    monkeypatch.setenv("SEICHE_X402_PAY_TO", PAY_TO)
    calls = []
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: calls.append((path, body)) or {"isValid": True},
    )

    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={
            "Authorization": "Bearer expired-or-invalid",
            "PAYMENT-SIGNATURE": "opaque-payment-marker",
        },
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="seiche"'
    assert calls == []


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("SEICHE_X402_NETWORK", "eip155:8453", "requires network eip155:84532"),
        ("SEICHE_X402_ASSET", BASE_MAINNET_USDC, "Base Sepolia USDC"),
        ("SEICHE_X402_FACILITATOR", CDP_FACILITATOR, "requires https://x402.org"),
        (
            "SEICHE_X402_FACILITATOR_AUTHORIZATION",
            "Bearer production-secret",
            "must not carry production",
        ),
    ],
)
def test_testnet_profile_rejects_inconsistent_dials(
    client, monkeypatch, name, value, message
):
    _enable_testnet(monkeypatch)
    monkeypatch.setenv(name, value)
    assert message in x402.configuration_error()
    assert client.post("/mcp", json=_call(PAID_TOOL)).status_code == 503


def test_malformed_facilitator_url_is_controlled_configuration_error(
    client, monkeypatch
):
    _enable_testnet(monkeypatch)
    monkeypatch.setenv("SEICHE_X402_FACILITATOR", "https://[")

    response = client.post("/mcp", json=_call(PAID_TOOL))

    assert response.status_code == 503
    assert "must be a valid HTTPS URL" in response.json()["error"]["message"]


def test_profile_without_pay_to_is_explicitly_rejected(client, monkeypatch):
    monkeypatch.setenv("SEICHE_X402_PROFILE", "base-sepolia-testnet")
    response = client.post("/mcp", json=_call(PAID_TOOL))
    assert response.status_code == 503
    assert "20-byte EVM address" in response.json()["error"]["message"]


def test_mainnet_profile_is_explicitly_dormant_before_facilitator(
    client, monkeypatch
):
    monkeypatch.setenv("SEICHE_X402_PROFILE", "base-mainnet-authenticated")
    monkeypatch.setenv("SEICHE_X402_PAY_TO", PAY_TO)
    monkeypatch.setenv("SEICHE_X402_FACILITATOR", CDP_FACILITATOR)
    monkeypatch.setenv(
        "SEICHE_X402_FACILITATOR_AUTHORIZATION", "Bearer production-token"
    )
    calls = []
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: calls.append((path, body)) or {"isValid": True},
    )

    assert "per-request path-bound facilitator JWT signing" in (
        x402.configuration_error()
    )
    response = client.post("/mcp", json=_call(PAID_TOOL))
    assert response.status_code == 503
    assert calls == []


# ---- payment-required path -------------------------------------------------


def test_anon_priced_tool_gets_v2_body_and_matching_required_header(
    client, monkeypatch
):
    _enable_testnet(monkeypatch)
    response = client.post("/mcp", json=_call(PAID_TOOL))

    assert response.status_code == 402
    body = response.json()
    assert _decode_header(response.headers["PAYMENT-REQUIRED"]) == body
    assert body["x402Version"] == 2
    assert body["resource"] == {
        "url": RESOURCE,
        "description": f"Seiche MCP tools/call: {PAID_TOOL}",
        "mimeType": "application/json",
    }
    assert body["extensions"] == {}
    reqs = body["accepts"][0]
    assert reqs["scheme"] == "exact"
    assert reqs["network"] == "eip155:84532"
    assert reqs["asset"] == BASE_SEPOLIA_USDC
    assert reqs["payTo"] == PAY_TO
    assert reqs["amount"] == "20000"  # $0.02 in USDC atomic units
    assert "maxAmountRequired" not in reqs


def test_unpaid_priced_tool_keeps_402_before_argument_disclosure(client, monkeypatch):
    _enable_testnet(monkeypatch)
    response = client.post("/mcp", json=_call("replay_asof"))
    assert response.status_code == 402
    assert response.json()["resource"]["description"].endswith("replay_asof")


def test_public_tools_stay_free(client, monkeypatch):
    _enable_testnet(monkeypatch)
    response = client.post("/mcp", json=_call("funding_stress_now"))
    assert response.status_code == 200
    assert "result" in response.json()


def test_current_subscriber_token_never_sees_402(client, monkeypatch):
    _enable_testnet(monkeypatch)
    token = accounts.issue_token("desk_pro", "pro")["token"]
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert "result" in response.json()


def test_tools_list_advertises_only_priced_analysis_tools(client, monkeypatch):
    _enable_testnet(monkeypatch)
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    names = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert PAID_TOOL in names
    assert "x402 v2" in names[PAID_TOOL]["description"]
    assert not any(name.startswith("agent_room_") for name in names)

    monkeypatch.delenv("SEICHE_X402_PROFILE")
    monkeypatch.delenv("SEICHE_X402_PAY_TO")
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert PAID_TOOL not in {
        tool["name"] for tool in response.json()["result"]["tools"]
    }


# ---- paid path -------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {
            "jsonrpc": "1.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": PAID_TOOL, "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": PAID_TOOL, "arguments": {}},
        },
        _call("replay_asof"),
        _call("replay_asof", arguments={"date": "2026-02-30"}),
        _call("ask_desk", arguments={"question": "   "}),
        _call(PAID_TOOL, arguments={"unexpected": True}),
        _call(PAID_TOOL, arguments=[]),
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": ["not", "an", "object"],
        },
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": [PAID_TOOL], "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "prompts/get",
            "params": {"name": PAID_TOOL, "arguments": {}},
        },
        _call("no_such_tool"),
    ],
    ids=[
        "jsonrpc-version",
        "notification",
        "missing-required",
        "invalid-calendar-date",
        "blank-required",
        "unknown-argument",
        "arguments-not-object",
        "params-not-object",
        "name-not-string",
        "wrong-method",
        "unknown-tool",
    ],
)
def test_invalid_dispatch_preconditions_never_reach_facilitator(
    client, monkeypatch, body
):
    _enable_testnet(monkeypatch)
    calls = []

    def fake_post(path, payload):
        calls.append((path, payload))
        return {
            "isValid": True,
            "success": True,
            "transaction": "must-not-settle",
            "network": "eip155:84532",
        }

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    response = client.post(
        "/mcp",
        json=body,
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )

    assert response.status_code == 400
    assert calls == []
    assert "PAYMENT-RESPONSE" not in response.headers


def test_invalid_bearer_never_falls_through_to_wallet_charge(client, monkeypatch):
    _enable_testnet(monkeypatch)
    calls = []
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: calls.append((path, body)) or {"isValid": True},
    )

    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={
            "Authorization": "Bearer expired-or-invalid",
            "PAYMENT-SIGNATURE": _payment_header(),
        },
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="seiche"'
    assert calls == []


def test_valid_payment_serves_tool_and_returns_v2_receipt(client, monkeypatch):
    _enable_testnet(monkeypatch)
    calls = []

    def fake_post(path, body):
        calls.append((path, body))
        if path == "/verify":
            return {"isValid": True, "payer": PAYER}
        return {
            "success": True,
            "transaction": TRANSACTION,
            "network": "eip155:84532",
            "payer": PAYER,
        }

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )

    assert response.status_code == 200
    assert "result" in response.json()
    assert [path for path, _ in calls] == ["/verify", "/settle"]
    for _, body in calls:
        assert body["x402Version"] == 2
        assert body["paymentPayload"]["x402Version"] == 2
        assert body["paymentRequirements"]["network"] == "eip155:84532"
        assert "amount" in body["paymentRequirements"]
    receipt = _decode_header(response.headers["PAYMENT-RESPONSE"])
    assert receipt["transaction"] == TRANSACTION
    assert receipt["network"] == "eip155:84532"
    assert "X-PAYMENT-RESPONSE" not in response.headers


def test_payment_requirements_mismatch_is_local_rejection(client, monkeypatch):
    _enable_testnet(monkeypatch)
    calls = []
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: calls.append((path, body)) or {"isValid": True},
    )
    wrong = x402.requirements(PAID_TOOL, RESOURCE)
    wrong["amount"] = "1"

    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header(_payment_payload(wrong))},
    )

    assert response.status_code == 402
    assert "does not match" in response.json()["error"]
    assert calls == []


def test_payment_resource_mismatch_is_local_rejection(client, monkeypatch):
    _enable_testnet(monkeypatch)
    calls = []
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: calls.append((path, body)) or {"isValid": True},
    )
    payload = _payment_payload()
    payload["resource"] = {
        "url": "https://attacker.invalid/different-resource",
        "description": "different resource",
        "mimeType": "application/json",
    }

    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header(payload)},
    )

    assert response.status_code == 402
    assert "resource does not match" in response.json()["error"]
    assert calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"x402Version": 1, "accepted": {}, "payload": {}},
        {"x402Version": 2, "accepted": [], "payload": {}},
        {"x402Version": 2, "accepted": {}, "payload": []},
        {"x402Version": 2, "accepted": {}, "payload": {}, "extensions": []},
    ],
)
def test_non_v2_or_malformed_payload_is_rejected(client, monkeypatch, payload):
    _enable_testnet(monkeypatch)
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header(payload)},
    )
    assert response.status_code == 402
    assert "not x402 v2" in response.json()["error"]


def test_legacy_v1_header_is_not_accepted(client, monkeypatch):
    _enable_testnet(monkeypatch)
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"X-PAYMENT": _payment_header()},
    )
    assert response.status_code == 402
    assert "PAYMENT-REQUIRED" in response.headers
    assert "X-PAYMENT-RESPONSE" not in response.headers


def test_invalid_payment_is_refused(client, monkeypatch):
    _enable_testnet(monkeypatch)
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: {
            "isValid": False,
            "invalidReason": "bad signature",
        },
    )
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )
    assert response.status_code == 402
    assert "bad signature" in response.json()["error"]
    assert _decode_header(response.headers["PAYMENT-REQUIRED"]) == response.json()


def test_settle_failure_serves_nothing(client, monkeypatch):
    _enable_testnet(monkeypatch)

    def fake_post(path, body):
        if path == "/verify":
            return {"isValid": True}
        return {
            "success": False,
            "errorReason": "insufficient funds",
            "transaction": "",
            "network": "eip155:84532",
        }

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )
    assert response.status_code == 402
    assert "insufficient funds" in response.json()["error"]
    assert "PAYMENT-RESPONSE" not in response.headers


@pytest.mark.parametrize(
    "settlement",
    [
        {"success": True, "network": "eip155:84532"},
        {
            "success": True,
            "transaction": TRANSACTION,
            "network": "eip155:8453",
        },
        {
            "success": True,
            "transaction": "not-a-transaction-hash",
            "network": "eip155:84532",
        },
        {
            "success": True,
            "transaction": TRANSACTION,
            "network": "eip155:84532",
            "amount": "1",
        },
    ],
)
def test_malformed_success_receipt_never_releases_tool(client, monkeypatch, settlement):
    _enable_testnet(monkeypatch)

    def fake_post(path, body):
        return {"isValid": True} if path == "/verify" else settlement

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )

    assert response.status_code == 402
    assert "settlement response invalid" in response.json()["error"]
    assert "PAYMENT-RESPONSE" not in response.headers


def test_facilitator_reason_is_bounded_before_reflection(client, monkeypatch):
    _enable_testnet(monkeypatch)
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: {"isValid": False, "invalidReason": "x" * 10_000},
    )
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )

    assert response.status_code == 402
    assert response.json()["error"] == "x" * 512
    assert len(response.headers["PAYMENT-REQUIRED"]) < 8192


def test_facilitator_outage_fails_closed(client, monkeypatch):
    _enable_testnet(monkeypatch)

    def fake_post(path, body):
        raise ConnectionError("facilitator down")

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )
    assert response.status_code == 402


def test_malformed_payment_header_is_402(client, monkeypatch):
    _enable_testnet(monkeypatch)
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": "not-base64!!"},
    )
    assert response.status_code == 402
    assert "malformed" in response.json()["error"]


def test_payment_on_batch_or_free_tool_is_rejected(client, monkeypatch):
    _enable_testnet(monkeypatch)
    signature = _payment_header()
    response = client.post(
        "/mcp",
        json=[_call(PAID_TOOL, 1), _call(PAID_TOOL, 2)],
        headers={"PAYMENT-SIGNATURE": signature},
    )
    assert response.status_code == 400
    # A one-message JSON-RPC batch is still a batch and cannot carry x402.
    response = client.post(
        "/mcp",
        json=[_call(PAID_TOOL)],
        headers={"PAYMENT-SIGNATURE": signature},
    )
    assert response.status_code == 400
    response = client.post(
        "/mcp",
        json=_call("funding_stress_now"),
        headers={"PAYMENT-SIGNATURE": signature},
    )
    assert response.status_code == 400


def test_payment_cannot_buy_agent_room_identity(client, monkeypatch):
    _enable_testnet(monkeypatch)
    response = client.post(
        "/mcp",
        json=_call("agent_room_verify", arguments={"room_id": "room_1"}),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )
    assert response.status_code == 400
    assert "priced analysis tool" in response.json()["error"]["message"]


def test_paid_call_does_not_burn_anon_quota(client, monkeypatch):
    _enable_testnet(monkeypatch)
    monkeypatch.setattr(
        x402,
        "_facilitator_post",
        lambda path, body: {
            "isValid": True,
            "success": True,
            "transaction": TRANSACTION,
            "network": "eip155:84532",
        },
    )
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )
    assert response.status_code == 200
    assert "X-MCP-Usage-Used" not in response.headers


def test_post_settlement_tool_failure_keeps_receipt_for_reconciliation(
    client, monkeypatch
):
    _enable_testnet(monkeypatch)
    calls = []

    def fake_post(path, body):
        calls.append(path)
        if path == "/verify":
            return {"isValid": True}
        return {
            "success": True,
            "transaction": TRANSACTION,
            "network": "eip155:84532",
        }

    def fail_snapshot(force=False):
        raise RuntimeError("snapshot failed after settlement")

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    monkeypatch.setattr(mcp_server, "_get_snapshot", fail_snapshot)
    response = client.post(
        "/mcp",
        json=_call(PAID_TOOL),
        headers={"PAYMENT-SIGNATURE": _payment_header()},
    )

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert calls == ["/verify", "/settle"]
    receipt = _decode_header(response.headers["PAYMENT-RESPONSE"])
    assert receipt["transaction"] == TRANSACTION
