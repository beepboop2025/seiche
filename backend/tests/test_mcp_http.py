"""The hosted MCP-over-HTTP transport and its usage meter.

Exercises the /mcp endpoint through FastAPI's TestClient with a canned snapshot
(no network) and an isolated usage DB (no shared state).
"""

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seiche import api, mcp_server, usage, world_model_delivery, x402


@pytest.fixture()
def client(tmp_path, monkeypatch, fake_snap):
    # no network: every tool reads the canned board (fake_snap from conftest)
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: fake_snap)

    async def fake_snapshot(force=False):
        return fake_snap

    monkeypatch.setattr(api.assemble, "snapshot", fake_snapshot)
    # isolated meter
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    # deterministic auth
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    # The fully-open default serves everyone the full surface; these tests
    # pin the RE-GATED configuration where anonymous shaping still applies.
    monkeypatch.setenv("SEICHE_BOARD_AUTH", "1")
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


def test_edge_allows_undertow_modern_mcp_headers():
    caddy = (Path(__file__).resolve().parents[2] / "ops" / "Caddyfile").read_text(
        encoding="utf-8"
    )
    undertow = caddy.split("handle /undertow/mcp* {", 1)[1].split("\n    }", 1)[0]
    allow_headers = next(
        line for line in undertow.splitlines()
        if "Access-Control-Allow-Headers" in line
    )
    assert "MCP-Protocol-Version" in allow_headers
    assert "Mcp-Method" in allow_headers
    assert "Mcp-Name" in allow_headers


def test_public_api_discovery_is_curated(client):
    r = client.get("/api")
    assert r.status_code == 200
    payload = r.json()
    assert payload["mcp"]["first_tool"] == "latest_article"
    assert payload["delivery"]["url"].endswith("?start=agent_api")
    assert "11:30 UTC" in payload["delivery"]["outcome"]
    assert payload["rest"]["small_gauge"] == "/api/gauge"
    assert payload["rest"]["oil_funding"] == "/api/oil-funding"
    assert payload["rest"]["fx_materials"] == "/api/estuary"
    assert payload["rest"]["openapi"] == "/api/openapi.json"
    assert payload["rest"]["realtime_venue"] == "/undertow/live/quotes.json"
    assert "official macro" in payload["conventions"]["clocks"]


def test_public_openapi_is_curated_and_importable(client):
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"] == "3.1.0"
    assert spec["servers"] == [{"url": "https://api.seiche.info"}]
    assert "/api/gauge" in spec["paths"]
    assert "/api/health" in spec["paths"]
    assert "/api/public" in spec["paths"]
    assert "/api/oil-funding" in spec["paths"]
    assert "/api/estuary" in spec["paths"]
    oil_schema = spec["paths"]["/api/oil-funding"]["get"]["responses"]["200"]
    oil_schema = oil_schema["content"]["application/json"]["schema"]
    estuary_schema = spec["paths"]["/api/estuary"]["get"]["responses"]["200"]
    estuary_schema = estuary_schema["content"]["application/json"]["schema"]
    assert oil_schema["required"] == ["schema"]
    assert oil_schema["properties"]["schema"]["const"] == "seiche.oil-funding.v1"
    assert estuary_schema["required"] == ["schema"]
    assert estuary_schema["properties"]["schema"]["const"] == "seiche.estuary.v1"
    health = spec["paths"]["/api/health"]["get"]
    assert set(health["responses"]) == {"200", "503"}
    assert any(p["name"] == "require_rebuilt" for p in health["parameters"])
    unavailable = health["responses"]["503"]["content"]["application/json"]["schema"]
    assert unavailable["required"] == ["status", "version"]
    assert unavailable["properties"]["status"]["enum"] == [
        "warming_or_unavailable",
        "rebuilding_from_last_known_good",
        "rebuilt_without_market_evidence",
    ]
    assert unavailable["additionalProperties"] is False
    assert set(health["responses"]["503"]["headers"]) == {
        "Cache-Control", "Retry-After",
    }
    assert "never starts or waits" in health["description"]
    oil_description = spec["paths"]["/api/oil-funding"]["get"]["description"]
    assert "live Cushing" in oil_description
    assert "dated capacity" in oil_description
    assert "/undertow/live/quotes.json" in spec["paths"]
    assert "/api/auth/login" not in spec["paths"]
    assert "/api/deep" not in spec["paths"]
    assert world_model_delivery.DELIVERY_ROUTE not in spec["paths"]
    assert "public" in r.headers["cache-control"]


def test_successful_tool_call_emits_privacy_safe_activation_log(
        client, caplog, monkeypatch):
    monkeypatch.setattr(api._mcp_activation_log, "handlers", [caplog.handler])
    with caplog.at_level(logging.INFO, logger=api._mcp_activation_log.name):
        r = client.post("/mcp", json=_rpc(
            "tools/call", {"name": "data_health", "arguments": {}}),
            headers={
                "Authorization": "Bearer private-token-marker",
                "X-Forwarded-For": "198.51.100.24",
            })
    assert r.status_code == 200
    record = next(record for record in caplog.records
                  if "mcp_activation" in record.getMessage())
    assert record.levelno == logging.INFO
    assert record.getMessage() == (
        "mcp_activation product=seiche surface=public "
        "tool=data_health outcome=success origin=edge"
    )


def test_activation_logger_emits_info_without_root_configuration(monkeypatch):
    """The production event has a local sink; root can stay quiet."""
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    activation_log = api._mcp_activation_log
    root_log = logging.getLogger()
    assert any(isinstance(handler, logging.StreamHandler)
               for handler in activation_log.handlers)
    monkeypatch.setattr(root_log, "handlers", [])
    monkeypatch.setattr(root_log, "level", logging.WARNING)
    monkeypatch.setattr(activation_log, "handlers", [Capture()])

    api._log_mcp_activation(
        _rpc("tools/call", {"name": "data_health", "arguments": {}}),
        {"jsonrpc": "2.0", "id": 1,
         "result": {"content": [], "isError": False}},
        "public", "direct",
    )

    assert activation_log.name == "seiche.mcp.activation"
    assert activation_log.level == logging.INFO
    assert activation_log.propagate is False
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert records[0].getMessage().startswith("mcp_activation product=seiche ")


def test_paid_x402_dispatch_logs_paid_surface(client, monkeypatch, caplog):
    monkeypatch.setenv(
        "SEICHE_X402_PAY_TO", "0x000000000000000000000000000000000000dEaD")
    monkeypatch.setattr(x402, "decode_payment", lambda header: {"payment": "safe"})
    monkeypatch.setattr(x402, "verify", lambda payment, reqs: (True, ""))
    monkeypatch.setattr(
        x402, "settle",
        lambda payment, reqs: (True, {"success": True, "transaction": "0xtx"}),
    )
    dispatched = []

    def dispatch(message, public):
        dispatched.append((message["params"]["name"], public))
        return {"jsonrpc": "2.0", "id": message["id"],
                "result": {"content": [], "isError": False}}

    monkeypatch.setattr(mcp_server, "dispatch", dispatch)
    monkeypatch.setattr(api._mcp_activation_log, "handlers", [caplog.handler])
    with caplog.at_level(logging.INFO, logger=api._mcp_activation_log.name):
        r = client.post(
            "/mcp",
            json=_rpc("tools/call", {
                "name": "funding_stress_forecast", "arguments": {}}),
            headers={"X-PAYMENT": "private-payment-marker"},
        )

    assert r.status_code == 200
    assert dispatched == [("funding_stress_forecast", False)]
    events = [record.getMessage() for record in caplog.records
              if "mcp_activation" in record.getMessage()]
    assert events == [
        "mcp_activation product=seiche surface=paid "
        "tool=funding_stress_forecast outcome=success origin=direct"
    ]


def test_board_gate_never_decides_mcp_entitlements(client, monkeypatch):
    """SEICHE_BOARD_AUTH is about the BROWSER board, not the MCP surface.

    `public = ident is None and _board_gate_enabled()` meant that with the
    gate off — the shipped default — the conjunction was false for everyone,
    so anonymous callers received the FULL surface and the positioning book,
    the desk brief and the flows engine were readable by plain curl.

    Seiche stays free: the conclusion, the analogs, the PROOF scoreboard and
    data health are anonymous either way. What must not move with a browser
    setting is who may read the proprietary engines. Asserted as a FAMILY,
    not as the specific names that happened to leak.
    """
    engines = ("positioning_book", "desk_brief", "replay_asof",
               "funding_stress_forecast", "ask_desk")
    public_good = ("funding_stress_now", "historical_analogs",
                   "proof_backtest", "data_health", "oil_funding_context",
                   "fx_materials_passage", "latest_article")

    for gate in ("1", None):
        if gate is None:
            monkeypatch.delenv("SEICHE_BOARD_AUTH", raising=False)
        else:
            monkeypatch.setenv("SEICHE_BOARD_AUTH", gate)
        names = {t["name"] for t in
                 client.post("/mcp", json=_rpc("tools/list")).json()["result"]["tools"]}
        for engine in engines:
            assert engine not in names, (
                f"{engine} anonymous with SEICHE_BOARD_AUTH={gate!r}")
        for free in public_good:
            assert free in names, (
                f"{free} must stay free with SEICHE_BOARD_AUTH={gate!r}")


def test_anonymous_flows_carries_the_reading_but_not_the_engine(client,
                                                                monkeypatch):
    """institutional_flows is free on purpose; its method versions are not.

    The handler took `_public` and never read it, so it had no gate of its
    own — a sibling of the book and the brief. The literature-level method
    prose stays public by design; the versioned engine identifiers do not.

    The pack is STUBBED on purpose: without it the tool errors out, every
    assertion below passes vacuously, and removing the trim still goes
    green. Verified by reverting the trim and watching this go red.
    """
    from seiche import wakeflows

    monkeypatch.setattr(wakeflows, "load", lambda *a, **k: {"stub": True})
    monkeypatch.setattr(wakeflows, "readings", lambda pack: {
        "as_of": "2026-08-03",
        "method_versions": {"basis_nowcast": "1.0.0",
                            "kalman_fusion": "1.0.0", "hawkes": "1.0.0"},
        "basis_trade": {"size_usd_bn": 904.5},
    })
    monkeypatch.delenv("SEICHE_BOARD_AUTH", raising=False)

    anon = json.dumps(client.post("/mcp", json=_rpc(
        "tools/call", {"name": "institutional_flows", "arguments": {}})).json())
    # the tool really ran (guards against a vacuous pass on an error result)
    assert "904.5" in anon and "isError" not in anon
    assert "method_versions" not in anon
    assert "kalman_fusion" not in anon

    # ...and a signed-in caller still receives the engine metadata.
    full = json.dumps(client.post(
        "/mcp",
        json=_rpc("tools/call",
                  {"name": "institutional_flows", "arguments": {}}),
        headers={"Authorization": f"Bearer {_pro_token()}"}).json())
    assert "method_versions" in full and "kalman_fusion" in full


def test_anonymous_sees_only_public_tools(client):
    r = client.post("/mcp", json=_rpc("tools/list"))
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {"latest_article", "funding_stress_now", "historical_analogs",
                     "proof_backtest", "data_health", "crypto_stress_record",
                     "institutional_flows", "oil_funding_context",
                     "fx_materials_passage"}
    # the Time Machine, forward forecast, brief, book, assistant stay paid
    for paid in ("replay_asof", "funding_stress_forecast", "desk_brief",
                 "positioning_book", "ask_desk"):
        assert paid not in names


@pytest.mark.parametrize(
    "query_name",
    ("api_key", "api-key", "access_token", "token"),
)
def test_query_credential_stays_anonymous_and_warns(
    client, monkeypatch, query_name
):
    monkeypatch.setattr(api, "_MCP_QUERY_CREDENTIAL_REJECT_AT", float("inf"))
    marker = "synthetic-query-credential"

    r = client.post(
        f"/mcp?{query_name}={marker}",
        json=_rpc("tools/list"),
    )

    assert r.status_code == 200
    names = {tool["name"] for tool in r.json()["result"]["tools"]}
    assert "positioning_book" not in names
    assert r.headers["Warning"].startswith("299 Seiche")
    assert r.headers["Deprecation"] == "@1786665600"
    assert r.headers["Sunset"] == "Tue, 15 Sep 2026 00:00:00 GMT"
    assert marker not in r.text


def test_authorization_header_wins_during_query_transition(client, monkeypatch):
    monkeypatch.setattr(api, "_MCP_QUERY_CREDENTIAL_REJECT_AT", float("inf"))

    r = client.post(
        "/mcp?api_key=synthetic-query-credential",
        json=_rpc("tools/list"),
        headers={"Authorization": f"Bearer {_pro_token()}"},
    )

    assert r.status_code == 200
    names = {tool["name"] for tool in r.json()["result"]["tools"]}
    assert "positioning_book" in names
    assert r.headers["Warning"].startswith("299 Seiche")


def test_noncredential_query_does_not_emit_transition_headers(client):
    r = client.post("/mcp?source=catalog", json=_rpc("tools/list"))

    assert r.status_code == 200
    assert "Warning" not in r.headers
    assert "Deprecation" not in r.headers
    assert "Sunset" not in r.headers


def test_anonymous_cannot_call_a_paid_tool(client):
    # replay_asof is the gated flagship; an anonymous caller must be refused,
    # not served the Time Machine for free.
    r = client.post("/mcp", json=_rpc("tools/call",
                    {"name": "replay_asof", "arguments": {"date": "2019-09-17"}}))
    assert r.json()["error"]["code"] == mcp_server.INVALID_PARAMS   # not in visible set


def test_malformed_params_does_not_500(client):
    # params as an array (valid JSON, wrong shape) must not crash the endpoint
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                  "method": "tools/call", "params": [1, 2, 3]})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == mcp_server.INVALID_PARAMS


def test_oversized_batch_is_rejected(client):
    batch = [_rpc("ping", msg_id=i) for i in range(200)]
    r = client.post("/mcp", json=batch)
    assert r.status_code == 413


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
    delivery = r.json()["result"]["structuredContent"]["delivery"]
    assert delivery["url"].endswith("?start=agent_mcp")
    # the billable call was metered
    assert r.headers["X-MCP-Usage-Used"] == "1"
    assert r.headers["X-MCP-Usage-Limit"] == str(usage.MCP_ANON_DAILY)


def test_public_context_routes_share_the_mcp_contract(client):
    oil = client.get("/api/oil-funding")
    estuary = client.get("/api/estuary")

    assert oil.status_code == 200
    assert oil.json()["schema"] == "seiche.oil-funding.v1"
    assert oil.json()["scenario"]["status"] == "scenario_only"
    assert estuary.status_code == 200
    assert estuary.json()["schema"] == "seiche.estuary.v1"
    assert estuary.json()["passage"]["earned"] == 1
    assert "public" in oil.headers["cache-control"]


def test_non_billable_methods_are_not_metered(client):
    client.post("/mcp", json=_rpc("tools/list"))
    r = client.get("/mcp/usage")
    assert r.json()["used_today"] == 0        # tools/list is free


def test_quota_exceeded_returns_upgrade_prompt(client, monkeypatch, caplog):
    monkeypatch.setattr(usage, "MCP_ANON_DAILY", 1)
    monkeypatch.setattr(api._mcp_activation_log, "handlers", [caplog.handler])
    call = _rpc("tools/call", {"name": "data_health", "arguments": {}})
    with caplog.at_level(logging.INFO, logger=api._mcp_activation_log.name):
        first = client.post("/mcp", json=call)
        assert first.json()["result"].get("isError") is not True
        caplog.clear()
        second = client.post("/mcp", json=call)
    res = second.json()["result"]
    assert res["isError"] is True
    assert "quota reached" in res["content"][0]["text"]
    assert "seiche.info" in res["content"][0]["text"]
    assert not any("mcp_activation" in record.getMessage()
                   for record in caplog.records)


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


def test_get_opens_sse_channel(client):
    r = client.get("/mcp")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    # SSE comment line only: the stateless transport never sends messages.
    assert r.text.startswith(":")


def test_get_mcp_route_is_unique():
    """Registration order must not silently shadow the SSE contract."""
    routes = [
        route for route in api.app.routes
        if getattr(route, "path", None) == "/mcp"
        and "GET" in (getattr(route, "methods", None) or set())
    ]
    assert len(routes) == 1


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


def test_usage_query_credential_stays_anonymous_and_warns(client, monkeypatch):
    monkeypatch.setattr(api, "_MCP_QUERY_CREDENTIAL_REJECT_AT", float("inf"))

    r = client.get("/mcp/usage?access_token=synthetic-query-credential")

    assert r.status_code == 200
    assert r.json()["tier"] == "anon"
    assert r.headers["Warning"].startswith("299 Seiche")
    assert r.headers["Deprecation"] == "@1786665600"
    assert r.headers["Sunset"] == "Tue, 15 Sep 2026 00:00:00 GMT"


def test_query_credentials_are_rejected_after_sunset(client, monkeypatch):
    monkeypatch.setattr(api, "_MCP_QUERY_CREDENTIAL_REJECT_AT", 0)
    marker = "synthetic-query-credential"

    mcp = client.post(
        f"/mcp?token={marker}",
        json=_rpc("tools/list"),
        headers={"Authorization": f"Bearer {_pro_token()}"},
    )
    usage_report = client.get(f"/mcp/usage?api-key={marker}")

    for response in (mcp, usage_report):
        assert response.status_code == 400
        assert response.headers["WWW-Authenticate"] == 'Bearer realm="seiche"'
        assert response.headers["Sunset"] == "Tue, 15 Sep 2026 00:00:00 GMT"
        assert marker not in response.text


def test_usage_report_reflects_calls(client):
    client.post("/mcp", json=_rpc("tools/call", {"name": "data_health", "arguments": {}}))
    r = client.get("/mcp/usage")
    assert r.json()["used_today"] == 1
