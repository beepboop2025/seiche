"""Release restrictions run before legacy handlers, exports and paid settlement."""
import pytest
from fastapi.testclient import TestClient
from seiche import api, mcp_server as mcp, methodology, public_rights


def rpc(name):
    return {"jsonrpc": "2.0", "id": 31, "method": "tools/call",
            "params": {"name": name, "arguments": {}}}


@pytest.mark.parametrize("public", [None, True, False])
def test_legacy_tools_never_advertised(public):
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, public=public)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert not names & public_rights.LEGACY_TOOLS
    assert {"source_rights_status", "money_market_context", "market_workbench", "world_markets_context"} <= names
    status = mcp.dispatch(rpc("source_rights_status"), public=public)["result"]
    assert not status.get("isError")
    assert set(status["structuredContent"]["unavailable_tools"]) == public_rights.LEGACY_TOOLS
    assert set(status["structuredContent"]["available_tools"]) <= names


@pytest.mark.parametrize("name", sorted(public_rights.LEGACY_TOOLS))
def test_old_callers_get_unavailable_without_loading_or_payment(name, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("legacy data must never load")
    monkeypatch.setattr(mcp, "_get_snapshot", fail)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", fail)
    result = mcp.dispatch(rpc(name), public=False)["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["availability"] == "unavailable"
    assert result["structuredContent"]["publication_allowed"] is False
    preflight = mcp.preflight_tool_call(rpc(name), public=False)
    assert preflight["error"]["code"] == mcp.INVALID_PARAMS
    assert preflight["error"]["data"]["category"] == "source_rights_restricted"


@pytest.mark.parametrize("path", sorted(public_rights.LEGACY_PATHS) + ["/api/engines/composite", "/api/asof/2026-10-01", "/api/dispatch/latest", "/api/overview/"])
def test_legacy_http_is_explicit_and_no_store(path, monkeypatch):
    async def fail(*args, **kwargs):
        pytest.fail("legacy snapshot must never load")
    monkeypatch.setattr(api.assemble, "snapshot", fail)
    with TestClient(api.app) as client:
        response = client.get(path)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["availability"] == "unavailable"
    assert response.json()["category"] == "source_rights_restricted"


def test_direct_research_routes_remain_outside_legacy_boundary():
    for path in ("/mcp", "/api", "/health", "/api/v2/money-market", "/api/v2/market-workbench", "/api/v2/india-funding"):
        assert not public_rights.legacy_http_restricted(path)


def test_fred_raw_exports_are_restricted_but_qualified_ecb_is_not():
    fred = [key for key, value in methodology.ALL_SERIES.items() if value.source == "fred"]
    assert fred
    assert all(methodology.csv_restriction(key) for key in fred)
    ecb = [key for key, value in methodology.ALL_SERIES.items() if value in methodology.ECB_FX_SERIES]
    assert ecb
    assert all(methodology.csv_restriction(key) is None for key in ecb)


def test_authority_guard_runs_before_legacy_rights_response(monkeypatch):
    monkeypatch.setattr(api.hetzner_runtime, "enabled", lambda: True)
    def revoked():
        raise RuntimeError("authority unavailable")
    monkeypatch.setattr(api.hetzner_runtime, "require_authority", revoked)
    response = TestClient(api.app).get("/api/overview")
    assert response.status_code == 503
    assert response.json()["status"] == "hetzner_authority_unavailable"


@pytest.mark.parametrize("path", ["/api/series/SOFR", "/api/series/SOFR.csv", "/api/series/WRESBAL", "/api/series/WRESBAL.csv"])
def test_fred_routes_deny_before_loading(path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("restricted export must not load cached values")
    monkeypatch.setattr(api.store, "load_series", forbidden)
    response = TestClient(api.app).get(path)
    assert response.status_code == 403
    assert "redistribution" in response.json()["detail"]
