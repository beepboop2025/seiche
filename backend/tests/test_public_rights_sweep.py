"""No public route or MCP tool serves a restricted reading, whatever is sealed.

The store is seeded with a v1 board whose reading, Tell, five-day event
probability, stacker members and ICE BofA high-yield OAS carry sentinel values,
sealed both through today's US bridge and as a pre-0.16.12 snapshot falsely
stamped public, plus the FRED HY OAS history. Every GET route in ``app.routes``
and every MCP tool is then called with the network closed, and no response may
contain a sentinel or a non-null ``p_event_5bd``.
"""
from __future__ import annotations

import json
import socket

import pandas as pd
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from seiche import accounts, api, assemble, mcp_server, store, usage
from seiche.markets.materialize import PUBLIC_SNAPSHOT_VISIBILITY
from seiche.markets.us_usd.materialize import build_products, seal_legacy_snapshot
from seiche.sources.base import Series

# Values chosen to occur nowhere else: a hit can only be a restricted value.
INDEX, TELL, P_EVENT, DISPERSION = 46.5317, 13.1729, 0.07213, 0.01977
MEMBERS = {"navigator": 0.06117, "modelcourt": 0.08341, "harbors": 0.05293,
           "sonar": 0.09127, "referee": 0.04419, "estuary": 0.07771}
HY_OAS, COMPONENT, REGIME = 3.8191, 61.2741, "SENTINEL-STRAIN-7Q3"
SENTINELS = (
    "46.5317", "13.1729", "0.07213", "0.01977", *(f"{value}" for value in MEMBERS.values()),
    "3.8191", "381.91", "61.2741", REGIME,
)
# A value for every path parameter in app.routes; a new parameter name fails
# the sweep until it is given one, so no new route escapes it.
PATH_VALUES = {
    "market_id": "US-USD", "timestamp": "2026-11-03T00:00:00Z", "mnemonic": "HY_OAS",
    "date": "2026-10-30", "name": "composite", "slug": "latest", "country": "JP",
    "room_id": "room-1", "stream": "board", "request_id": "x", "member": "x", "sha256": "0" * 64,
}
# Arguments for MCP tools that require some; everything else is called bare.
TOOL_ARGUMENTS = {
    "quantity_kg": "1", "fineness": "0.995", "price_usd_per_oz": "2400", "annual_rate_pct": "5",
    "fx_inr_per_usd": "84", "fees_usd": "0", "days": 30, "public_key_hex": "00" * 32,
    "room_id": "room-1", "event": {}, "client_signature_hex": "00" * 64, "date": "2026-10-30",
    "question": "What is the current funding gauge reading?",
}


def restricted_board() -> dict:
    """A v1 board shaped like assemble.snapshot(), carrying restricted values."""
    return {
        "generated_at": "2026-11-02T12:00:00+00:00",
        "version": "0.16.12-test",
        "headline": {
            "hy_oas_pct": {"value": HY_OAS, "asof": "2026-10-30"},
            "sofr_pct": {"value": 3.94, "asof": "2026-10-30"},
        },
        "engines": {
            "composite": {
                "ok": True, "value": INDEX, "regime": REGIME, "coverage_pct": 96.0,
                "decomposition": [{"component": "repo", "score": COMPONENT, "status": "OK"}],
            },
        },
        "deep": {
            "tell": {"ok": True, "tell": TELL},
            "stacker": {"ok": True, "p_now": P_EVENT, "dispersion_now": DISPERSION,
                        "members_now": dict(MEMBERS)},
        },
        "navigator": {"ok": True, "p_event_5bd": P_EVENT},
        "calendar": {"next_turn": None, "crunch_windows": []},
        "faults": [],
        "provenance": [
            {"mnemonic": "HY_OAS", "source": "fred", "asof": "2026-10-30",
             "fetched_at": "2026-11-02T11:00:00+00:00", "staleness": "fresh",
             "availability": "available", "history_export_allowed": False},
            {"mnemonic": "SOFR", "source": "fred", "asof": "2026-10-30",
             "fetched_at": "2026-11-02T11:00:00+00:00", "staleness": "fresh",
             "availability": "available", "history_export_allowed": False},
        ],
        "data_quality": {"status": "partial", "hy_oas_pct": HY_OAS},
    }


def leaks(text: str) -> list[str]:
    found = [sentinel for sentinel in SENTINELS if sentinel in text]
    try:
        body = json.loads(text)
    except ValueError:
        return found

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "p_event_5bd" and item is not None:
                    found.append("p_event_5bd")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(body)
    return found


@pytest.fixture()
def sealed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "rights-sweep.sqlite")
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    monkeypatch.setattr(api, "_mcp_limiter", api._RateLimiter(10_000))
    board = restricted_board()

    async def snapshot(force=False):
        return board

    monkeypatch.setattr(assemble, "snapshot", snapshot)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: board)
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: board)
    monkeypatch.setattr(mcp_server, "_get_completed_snapshot", lambda: board)
    monkeypatch.setattr(mcp_server, "_get_in_memory_completed_snapshot", lambda: board)

    def closed(*_args, **_kwargs):
        raise OSError("network closed for the rights sweep")

    monkeypatch.setattr(socket.socket, "connect", closed)
    monkeypatch.setattr(socket.socket, "connect_ex", closed)

    # Today's bridge, then a pre-0.16.12 seal of the same reading stamped
    # public, which is the latest record the routes would read.
    receipt = seal_legacy_snapshot(board)
    store.promote_market_snapshots(binding["snapshot_id"] for binding in receipt.values())
    overview, gauge = build_products(board)
    for product, payload in (("overview", overview), ("gauge", gauge)):
        store.seal_market_snapshot(
            market_id="US-USD", product=product, event_cutoff="2026-10-30T00:00:00+00:00",
            knowledge_cutoff="2026-11-02T12:30:00+00:00", calibration_id="us-usd-legacy-parity-v1",
            evidence_eligible=False, payload={**payload, "visibility": PUBLIC_SNAPSHOT_VISIBILITY},
        )
    # The FRED copy of ICE BofA HY OAS in the v1 series store.
    store.save_series(Series(
        "HY_OAS", "fred", "BAMLH0A0HYM2", "ICE BofA US High Yield OAS", "%", "D",
        "2026-11-02T11:00:00+00:00",
        pd.Series([HY_OAS], index=pd.DatetimeIndex([pd.Timestamp("2026-10-30")])),
    ))
    return board


def test_the_fixture_really_holds_the_restricted_values(sealed):
    record = store.load_latest_market_snapshot("US-USD", "gauge")
    assert record["payload"]["visibility"] == PUBLIC_SNAPSHOT_VISIBILITY
    assert set(leaks(json.dumps(record["payload"]))) >= {"46.5317", "13.1729", "0.07213", REGIME}
    assert "3.8191" in json.dumps(store.load_latest_market_snapshot("US-USD", "overview")["payload"])
    assert store.load_series("HY_OAS").points.iloc[-1] == HY_OAS


def test_no_public_get_route_serves_a_restricted_value(sealed):
    # A route that fails returns its error response; it is scanned like any other.
    client = TestClient(api.app, raise_server_exceptions=False)
    routes = [route for route in api.app.routes
              if isinstance(route, APIRoute) and "GET" in route.methods]
    assert len(routes) >= 50
    exposed = {}
    for route in routes:
        path = route.path.format(**{name: PATH_VALUES[name] for name in route.param_convertors})
        response = client.get(path)
        found = leaks(response.text)
        if found:
            exposed[path] = (response.status_code, sorted(set(found)))
    assert exposed == {}


@pytest.mark.parametrize("product", ["overview", "gauge", "asof/2026-11-03T00:00:00Z"])
def test_the_us_reading_is_refused_with_the_source_rights_restriction(sealed, product):
    response = TestClient(api.app, raise_server_exceptions=False).get(f"/api/v2/markets/US-USD/{product}")
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    # The same restriction contract the withheld legacy routes return; the v1
    # gauge itself now serves the per-engine projection instead.
    from seiche import public_rights
    legacy = public_rights.legacy_unavailable("/api/public")
    assert set(body) == set(legacy)
    assert body["schema"] == "seiche.source-rights-restriction.v1"
    assert body["surface"] == "/api/v2/markets/US-USD"
    assert body["category"] == "source_rights_restricted" and body["publication_allowed"] is False


def test_no_mcp_tool_serves_a_restricted_value(sealed):
    client = TestClient(api.app, raise_server_exceptions=False)
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert not leaks(listed.text)
    # Every registered tool, including the withheld legacy tools that are not
    # listed: a direct call must not reach a restricted value either.
    assert len(mcp_server.TOOLS) >= 27
    exposed = {}
    for name, spec in mcp_server.TOOLS.items():
        schema = spec[2] or {}
        base = {key: TOOL_ARGUMENTS[key] for key in schema.get("required", [])}
        calls = [base]
        for key, spec in (schema.get("properties") or {}).items():
            calls.extend({**base, key: value} for value in spec.get("enum", []) if key not in base)
        for arguments in calls:
            response = client.post("/mcp", json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            })
            found = leaks(response.text)
            if found:
                exposed[f"{name} {json.dumps(arguments, sort_keys=True)}"] = sorted(set(found))
    assert exposed == {}


def test_the_catalog_and_series_keep_their_0_16_11_metadata(sealed):
    client = TestClient(api.app)
    catalog = {item["market_id"]: item for item in client.get("/api/v2/markets").json()["markets"]}
    us = catalog["US-USD"]
    assert us["latest_snapshot"] is not None
    assert us["data_coverage"] == {"component_coverage_pct": 96.0}
    assert us["evidence_eligibility"]["eligible"] is False
    assert "reading" not in us and "gauge" not in us and "headline" not in us
    series = client.get("/api/v2/markets/US-USD/series")
    assert series.status_code == 200
    assert series.json()["schema"] == "seiche.market-series.v2"


def test_the_bridge_computes_its_stamp_from_the_inputs_it_copies():
    from seiche.markets.materialize import RESTRICTED_SNAPSHOT_VISIBILITY
    from seiche.markets.us_usd.materialize import legacy_reading_visibility

    board = restricted_board()
    assert build_products(board)[1]["visibility"] == RESTRICTED_SNAPSHOT_VISIBILITY
    # Only when every admitted input is cleared, and mapped ones by an allowed
    # adapter, would the bridge be public; a missing provenance never is.
    cleared = [{**entry, "mnemonic": "ECB_DFR", "history_export_allowed": True}
               for entry in board["provenance"]]
    assert legacy_reading_visibility({**board, "provenance": cleared}) == PUBLIC_SNAPSHOT_VISIBILITY
    mapped = [{**board["provenance"][0], "mnemonic": "SOFR", "history_export_allowed": True}]
    assert legacy_reading_visibility({**board, "provenance": mapped}) == RESTRICTED_SNAPSHOT_VISIBILITY
    assert legacy_reading_visibility({**board, "provenance": []}) == RESTRICTED_SNAPSHOT_VISIBILITY
    assert legacy_reading_visibility({**board, "provenance": {"SOFR": {}}}) == RESTRICTED_SNAPSHOT_VISIBILITY
    skipped = [{**entry, "availability": "unavailable"} for entry in board["provenance"]]
    assert legacy_reading_visibility({**board, "provenance": skipped}) == PUBLIC_SNAPSHOT_VISIBILITY


def test_route_rights_follow_the_declared_inputs_of_each_pack():
    from seiche.markets.reading_rights import reading_dependencies, reading_restricted
    from seiche.markets.registry import default_registry

    registry = default_registry()
    us = registry.get("US-USD")
    dependencies = reading_dependencies(us)
    # The bridge depends on FRED copies declared metadata-only and on v1
    # inputs the pack does not declare at all, such as HY OAS.
    assert dependencies["SOFR"].value == "metadata_only"
    assert dependencies["HY_OAS"] is None
    assert reading_restricted(us) is True
    # A kernel pack reads only its own derivable rows.
    for pack in registry.list():
        if pack.calibration_id != "us-usd-legacy-parity-v1":
            assert reading_restricted(pack) is False, pack.market_id
