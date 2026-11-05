"""Release restrictions run before legacy handlers, exports and paid settlement.

public_rights.ACTIVE_POLICY selects the release. The 28 October policy
withholds every legacy surface. The 3 November research release reopens the
legacy board and tools and keeps only the Book withheld. Both are tested, so
switching back stays a one-constant change.
"""
import pytest
from fastapi.testclient import TestClient
from seiche import api, mcp_server as mcp, methodology, public_rights, x402


def rpc(name):
    return {"jsonrpc": "2.0", "id": 31, "method": "tools/call",
            "params": {"name": name, "arguments": {}}}


# 28 October policy: every legacy surface is withheld.


@pytest.mark.parametrize("public", [None, True, False])
def test_legacy_tools_never_advertised(public, withheld_policy):
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, public=public)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert not names & public_rights.LEGACY_TOOLS
    assert {"source_rights_status", "money_market_context", "market_workbench", "world_markets_context"} <= names
    status = mcp.dispatch(rpc("source_rights_status"), public=public)["result"]
    assert not status.get("isError")
    assert set(status["structuredContent"]["unavailable_tools"]) == public_rights.LEGACY_TOOLS
    assert set(status["structuredContent"]["available_tools"]) <= names


@pytest.mark.parametrize("name", sorted(public_rights.LEGACY_TOOLS))
def test_old_callers_get_unavailable_without_loading_or_payment(name, monkeypatch, withheld_policy):
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
def test_legacy_http_is_explicit_and_no_store(path, monkeypatch, withheld_policy):
    async def fail(*args, **kwargs):
        pytest.fail("legacy snapshot must never load")
    monkeypatch.setattr(api.assemble, "snapshot", fail)
    with TestClient(api.app) as client:
        response = client.get(path)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["availability"] == "unavailable"
    assert response.json()["category"] == "source_rights_restricted"


def test_switching_back_restores_the_withheld_release_status(withheld_policy):
    assert public_rights.rights_status() == {
        "schema": "seiche.source-rights-status.v1", "status": "partial_release",
        "policy": "seiche.public-source-rights.2026-10-28", "context_only": True,
        "available_tools": ["source_rights_status", "money_market_context", "market_workbench",
                            "world_markets_context", "gift_city_context", "gold_inventory_carry",
                            "research_network", "data_health"],
        "unavailable_tools": sorted(public_rights.LEGACY_TOOLS),
        "rights_urls": ["https://fred.stlouisfed.org/legal/terms/"],
        "boundary": "Available tools still report field-specific restrictions, missing values and source clocks. Availability is not a blanket reuse license, freshness certification or investment advice. ECB FX is the default; legacy H.10/FRED values stay withheld. Gold carry is arithmetic over caller-supplied assumptions.",
    }
    for path in public_rights.STATIC_SURFACES:
        assert public_rights.static_restriction(path) == public_rights.legacy_unavailable(path)
    assert "are unavailable pending source qualification" in mcp._legacy_instructions()


def test_an_unknown_policy_withholds_every_legacy_surface(monkeypatch):
    monkeypatch.setattr(public_rights, "ACTIVE_POLICY", "seiche.public-source-rights.unknown")
    assert public_rights.withheld_tools() == public_rights.LEGACY_TOOLS
    assert all(public_rights.legacy_http_restricted(path) for path in public_rights.LEGACY_PATHS)
    assert public_rights.rights_status()["status"] == "partial_release"


# 3 November research release: the legacy board and tools are visible again.


def test_the_research_release_is_the_active_policy():
    assert public_rights.ACTIVE_POLICY == "seiche.public-source-rights.2026-11-03"
    assert public_rights.research_release()
    # Only the Book's own tool stays withheld, with the Book.
    assert public_rights.withheld_tools() == public_rights.BOOK_TOOLS == {"positioning_book"}


@pytest.mark.parametrize("public", [None, True, False])
def test_research_release_lists_legacy_tools_by_their_public_flag(public):
    tools = mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, public=public)["result"]["tools"]
    names = {t["name"] for t in tools}
    anonymous = mcp._resolve_public(public)
    assert names & public_rights.LEGACY_TOOLS == {
        name for name in public_rights.LEGACY_TOOLS - public_rights.BOOK_TOOLS
        if not anonymous or mcp.TOOLS[name][4] or name in public_rights.RESEARCH_PUBLIC_TOOLS}
    status = mcp.dispatch(rpc("source_rights_status"), public=public)["result"]
    assert not status.get("isError")
    content = status["structuredContent"]
    assert content["status"] == "research_release"
    assert content["policy"] == public_rights.RESEARCH_RELEASE_POLICY
    assert content["unavailable_tools"] == ["positioning_book"]
    assert set(content["available_tools"]) <= names
    assert set(content["legacy_tools"]) == public_rights.LEGACY_TOOLS
    # The account list is the historical is_public flag, not a second policy.
    assert set(content["account_tools"]) == {
        name for name in public_rights.LEGACY_TOOLS if not mcp.TOOLS[name][4]} - {
        "positioning_book", "funding_stress_forecast"} == {"ask_desk", "desk_brief", "replay_asof"}
    assert content["account_routes"] == ["/api/ask", "/api/asof/{date}", "/api/brief"]
    assert "funding_stress_forecast" in content["available_tools"]
    assert content["withheld_surfaces"] == ["/api/book", "/data/book_history.json"]
    assert content["withheld_fields"] == ["deep.book", "pit.records[].book", "alerts[rule=book_flip]"]
    for text in (content["boundary"], mcp.SERVER_INSTRUCTIONS):
        assert "\u2014" not in text and "\u2013" not in text
    assert "removal at any time" in content["boundary"]
    assert "shown for free research use" in mcp.SERVER_INSTRUCTIONS


def test_research_release_runs_the_legacy_handler(monkeypatch, fake_snap):
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: fake_snap)
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: fake_snap)
    assert mcp.preflight_tool_call(rpc("funding_stress_now"), public=True) is None
    result = mcp.dispatch(rpc("funding_stress_now"), public=True)["result"]
    assert not result.get("isError")
    assert "source_rights_restricted" not in str(result)


@pytest.mark.parametrize("name", sorted(public_rights.ACCOUNT_TOOLS))
def test_account_legacy_tools_keep_identity_and_are_never_priced(name):
    preflight = mcp.preflight_tool_call(rpc(name), public=True)
    if name in public_rights.RESEARCH_PUBLIC_TOOLS:
        assert preflight is None  # opened by the research release
    elif name in public_rights.BOOK_TOOLS:
        assert preflight["error"]["code"] == mcp.INVALID_PARAMS
        assert preflight["error"]["data"] == public_rights.legacy_unavailable(name)
    else:
        assert preflight["error"]["code"] == mcp.INVALID_PARAMS
        assert "unknown tool" in preflight["error"]["message"]
    assert x402.price_usd(name) is None


def test_the_forecast_is_public_only_under_the_research_release(fake_snap, monkeypatch):
    monkeypatch.setattr(mcp, "_get_snapshot", lambda force=False: fake_snap)
    result = mcp.dispatch(rpc("funding_stress_forecast"), public=True)["result"]
    assert not result.get("isError")
    assert "Levels are for ranking, not literal odds" in result["structuredContent"]["reading"]
    assert "historical_evidence" in result["structuredContent"]
    # Switching back restores the 28 October refusal exactly.
    monkeypatch.setattr(public_rights, "ACTIVE_POLICY", public_rights.POLICY)
    assert mcp.preflight_tool_call(rpc("funding_stress_forecast"), public=True)["error"]["data"][
        "category"] == "source_rights_restricted"
    with pytest.raises(mcp.ToolError, match="subscriber tool"):
        mcp.tool_forecast({}, public=True)


@pytest.mark.parametrize("path", sorted(public_rights.LEGACY_PATHS - {"/api/book"}) + ["/api/engines/composite", "/api/asof/2026-10-01", "/api/dispatch/latest", "/api/overview/"])
def test_research_release_opens_every_legacy_route_but_the_book(path):
    assert not public_rights.legacy_http_restricted(path)


def test_research_release_serves_the_computed_board_unchanged(monkeypatch, fake_snap):
    async def snapshot(*args, **kwargs):
        return fake_snap
    monkeypatch.setattr(api.assemble, "snapshot", snapshot)
    with TestClient(api.app) as client:
        response = client.get("/api/overview")
    assert response.status_code == 200
    board = response.json()
    assert board["generated_at"] == fake_snap["generated_at"]
    assert board["engines"]["composite"] == fake_snap["engines"]["composite"]
    assert board["faults"] == fake_snap["faults"]


@pytest.mark.parametrize("path", ["/api/book", "/api/book/"])
def test_research_release_keeps_the_book_exactly_as_withheld(path, monkeypatch):
    async def fail(*args, **kwargs):
        pytest.fail("the Book must not load")
    monkeypatch.setattr(api.assemble, "snapshot", fail)
    with TestClient(api.app) as client:
        response = client.get(path)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == public_rights.legacy_unavailable(path)
    assert response.json()["policy"] == "seiche.public-source-rights.2026-10-28"
    ledger = "/data/book_history.json"
    assert public_rights.static_restriction(ledger) == public_rights.legacy_unavailable(ledger)
    assert public_rights.static_restriction("/data/overview.json") is None
    assert public_rights.static_restriction("/data/public.json") is None


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


def test_authority_guard_runs_before_legacy_rights_response(monkeypatch, withheld_policy):
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
