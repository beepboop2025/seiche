"""Unified World Markets projection, REST cache boundary, and MCP surface."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from seiche import api
from seiche import mcp_server as mcp
from seiche.markets.world import (
    CANONICAL_URLS,
    WORLD_MARKETS_SCHEMA,
    WORLD_MARKETS_SELECTORS,
    WORLD_MARKETS_STATUSES,
    project_world_markets,
)


def _snapshot() -> dict:
    return {
        "generated_at": "2026-08-21T11:30:00+00:00",
        "headline": {
            "vix": {"value": 19.4, "asof": "2026-08-20"},
            "hy_oas_pct": {"value": 3.2, "asof": "2026-08-20"},
        },
        "engines": {
            "composite": {
                "value": 43.0,
                "regime": "EROSION",
                "coverage_pct": 96.0,
            },
            "money_market": {
                "ok": True,
                "schema": "seiche.money-market-desk.v1",
                "asof": "2026-08-20",
                "plain_language": "Dollar cash is orderly but repo tails are elevated.",
                "quant_read": "The worst current channel is at its own-history p73.",
                "regime": {"state": "WATCH", "worst_stress_percentile": 73.0},
                "coverage": {"coverage_pct": 91.0, "status": "partial"},
                "freshness": {"desk_asof": "2026-08-20", "status": "fresh"},
                "sections": [
                    {
                        "id": "repo_segments",
                        "label": "Repo segments",
                        "metrics": [
                            {
                                "id": "repo_segments.synthetic",
                                "label": "SOFR minus IORB",
                                "value": 2.5,
                                "unit": "bp",
                                "asof": "2026-08-20",
                                "source": "Federal Reserve Bank of New York",
                                "formula": "100 x (SOFR - IORB)",
                                "history": [["LICENSED-SENTINEL", 1.0]],
                            }
                        ],
                    }
                ],
                "charts": {"LICENSED-SENTINEL": [1, 2, 3]},
                "caveats": ["Descriptive context only."],
            },
            "estuary": {
                "ok": True,
                "asof": "2026-08-20",
                "headline": {
                    "regime": "PRESSURE HELD UPSTREAM",
                    "verdict": "FX cash pressure is ahead of funding pricing.",
                    "fx_pressure": 72.0,
                    "materials_pressure": 61.0,
                    "funding_priced": 48.0,
                    "transmission_gap": 24.0,
                    "coverage_pct": 90.0,
                    "context_only": True,
                },
                "fx": {
                    "broad": {
                        "index": 120.4,
                        "pressure_percentile": 77.0,
                        "asof": "2026-08-20",
                    },
                    "currencies": [
                        {
                            "key": "INR",
                            "label": "Indian rupee",
                            "bucket": "EM",
                            "last_local_per_usd": 87.1,
                            "unit": "INR per USD",
                            "asof": "2026-08-20",
                            "source_id": "DEXINUS",
                            "change_20d_pct": 1.2,
                            "depreciation_percentile": 79.0,
                            "volatility_percentile": 65.0,
                            "pressure": 82.0,
                        }
                    ],
                },
                "passage": {
                    "earned": 1,
                    "tentative": 0,
                    "not_earned": 2,
                    "edges": [
                        {
                            "source": "EM dollar",
                            "target": "SOFR-IORB",
                            "status": "earned",
                            "corr_holdout": 0.31,
                        }
                    ],
                },
                "dollar_system": {
                    "swap_lines": {
                        "outstanding_usd_m": 120.0,
                        "asof": "2026-08-19",
                    },
                    "foreign_official_rrp": {
                        "outstanding_usd_b": 310.0,
                        "asof": "2026-08-19",
                    },
                    "fima_repo": {
                        "outstanding_usd_m": 5.0,
                        "asof": "2026-08-19",
                    },
                    "offshore_dollar_credit": {
                        "outstanding_usd_t": 13.2,
                        "asof": "2026-03-31",
                    },
                },
                "charts": {"LICENSED-SENTINEL": [1, 2, 3]},
                "coverage_matrix": [
                    {"aspect": "cross-currency basis", "status": "out_of_scope"}
                ],
                "caveats": ["Association is not causation."],
            },
            "rvxray": {
                "ok": True,
                "asof": "2026-08-18",
                "pair_proxy_b": 810.0,
                "series": [["LICENSED-SENTINEL", 1.0]],
            },
            "warehouse": {
                "ok": True,
                "asof": "2026-08-13",
                "total_net_b": 410.0,
                "total_pctl": 88.0,
                "chg_13w_b": 22.0,
                "long_end_share_pct": 41.0,
                "buckets": [{"bucket": "7y+", "net_b": 120.0, "pctl": 91.0}],
            },
            "auctions": {
                "ok": True,
                "asof": "2026-08-20",
                "digestion_index": 0.4,
                "recent_auctions": [
                    {"date": "2026-08-20", "tenor": "10-Year", "score": 0.1}
                ],
                "index_series": [["LICENSED-SENTINEL", 0.4]],
            },
        },
        "deep": {
            "tell": {
                "ok": True,
                "asof": "2026-08-20",
                "tell": 12.0,
                "plumbing_pctl": 58.0,
                "market_pctl": 46.0,
                "reading": "aligned",
                "series": [["LICENSED-SENTINEL", 12.0]],
            }
        },
    }


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _keys(child)}
    return set()


def _mcp_payload(response: dict) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


def test_projection_is_versioned_citable_bounded_and_honest() -> None:
    payload = project_world_markets(_snapshot())

    assert payload["schema"] == WORLD_MARKETS_SCHEMA
    assert payload["generated_at"] == "2026-08-21T11:30:00+00:00"
    assert payload["as_of"] == "2026-08-20"
    assert payload["clocks"]["domains"] == {
        "money_markets": "2026-08-20",
        "forex": "2026-08-20",
        "capital_markets": "2026-08-20",
    }
    assert set(payload["status_definitions"]) == set(WORLD_MARKETS_STATUSES)
    assert payload["scope"]["coverage_claim"] == "curated_partial_non_exhaustive"
    assert payload["canonical_urls"] == CANONICAL_URLS
    assert payload["canonical_urls"]["money_markets"] == (
        "https://seiche.info/money-markets/"
    )
    assert payload["citation"]["canonical_url"] == "https://seiche.info/markets/"
    assert payload["citation"]["generated_at"] == payload["generated_at"]
    assert payload["citation"]["evidence_as_of"] == payload["as_of"]
    assert "evidence_clock" not in payload["citation"]
    assert payload["money_markets"]["status"] == "observed"
    assert payload["forex"]["status"] == "derived"
    currency = payload["forex"]["currencies"][0]
    assert currency["spot"] == {
        "status": "observed",
        "value": 87.1,
        "unit": "INR per USD",
        "as_of": "2026-08-20",
        "source_id": "DEXINUS",
        "source_registry_ids": ["federal_reserve_h10", "fred"],
    }
    assert currency["analytics"]["status"] == "derived"
    assert currency["analytics"]["pressure"] == 82.0
    dollar_system = payload["forex"]["dollar_system"]
    assert dollar_system["foreign_official_rrp"]["outstanding_usd_b"] == 310.0
    assert dollar_system["fima_repo"]["outstanding_usd_m"] == 5.0
    assert dollar_system["offshore_dollar_credit"]["outstanding_usd_t"] == 13.2
    warehouse = next(
        item
        for item in payload["capital_markets"]["positioning"]
        if item["id"] == "warehouse"
    )
    assert warehouse["evidence_statuses"] == ["observed", "derived"]
    assert warehouse["observed_facts"]["total_net_b"] == 410.0
    assert warehouse["analytics"]["total_pctl"] == 88.0
    assert payload["capital_markets"]["execution_liquidity"]["status"] == (
        "structural"
    )
    assert "LICENSED-SENTINEL" not in json.dumps(payload)
    assert not ({"charts", "history", "series", "index_series"} & _keys(payload))
    assert payload["methodology"]["boundedness"] == {
        "chart_history_included": False,
        "raw_history_arrays_included": False,
        "money_market_sections_max": 7,
        "metrics_per_section_max": 8,
        "forex_leaders_max": 22,
        "network_edges_max": 12,
        "capital_cards_max": 8,
    }


def test_official_registry_contains_verified_primary_urls() -> None:
    sources = project_world_markets(_snapshot(), selector="sources")["sources"]
    urls = {item["url"] for item in sources}

    assert {
        "https://stats.bis.org/api-doc/v2/",
        "https://www.bis.org/statistics/dataportal/exr.htm",
        "https://data.ecb.europa.eu/help/api/data",
        "https://home.treasury.gov/treasury-daily-interest-rate-xml-feed",
        "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
        "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        "https://api.fiscaldata.treasury.gov/services/api/fiscal_service",
        "https://www.federalreserve.gov/releases/h41/",
        "https://data.financialresearch.gov/v1",
        "https://www.cboe.com/tradable_products/vix/",
        "https://www.eia.gov/opendata/",
    } <= urls
    assert all(item["status"] == "structural" for item in sources)
    by_id = {item["id"]: item for item in sources}
    assert by_id["cftc_commitments_of_traders"]["used_in_snapshot"] is True
    assert "capital_markets.positioning[]" in by_id[
        "cftc_commitments_of_traders"
    ]["projection_paths"]
    assert by_id["sec_edgar_api"]["used_in_snapshot"] is False
    assert by_id["sec_edgar_api"]["catalog_role"] == "official_reference_only"


def test_forex_projection_includes_the_full_registered_panel_but_no_more() -> None:
    snapshot = _snapshot()
    snapshot["engines"]["estuary"]["fx"]["currencies"] = [
        {
            "key": f"FX{index:02d}",
            "last_local_per_usd": float(index),
            "unit": "local per USD",
            "asof": "2026-08-20",
            "source_id": f"H10-{index:02d}",
            "pressure": float(index),
        }
        for index in range(25)
    ]

    currencies = project_world_markets(snapshot, selector="forex")["forex"][
        "currencies"
    ]

    assert len(currencies) == 22
    assert currencies[-1]["key"] == "FX21"
    assert all(item["spot"]["status"] == "observed" for item in currencies)
    assert all(item["analytics"]["status"] == "derived" for item in currencies)


def test_every_selector_is_bounded_to_its_named_projection() -> None:
    domains = {"money_markets", "forex", "capital_markets"}
    for selector in WORLD_MARKETS_SELECTORS:
        payload = project_world_markets(_snapshot(), selector=selector)
        assert payload["selection"] == selector
        assert payload["available_selectors"] == list(WORLD_MARKETS_SELECTORS)
        present = set(payload) & domains
        if selector in domains:
            assert present == {selector}
        elif selector == "all":
            assert present == domains
        else:
            assert not present


def test_selected_unavailable_domain_cannot_inherit_other_domain_success() -> None:
    snapshot = _snapshot()
    snapshot["engines"]["estuary"] = {
        "ok": False,
        "asof": "2026-08-19",
        "reason": "no eligible FX evidence",
    }

    payload = project_world_markets(snapshot, selector="forex")

    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["as_of"] == "2026-08-19"
    assert payload["forex"]["status"] == "unavailable"
    assert payload["coverage"]["available_domains"] == 2


def test_capital_only_headline_uses_its_direct_observation_clock() -> None:
    snapshot = {
        "generated_at": "2026-08-21T11:30:00+00:00",
        "headline": {"vix": {"value": 19.4, "asof": "2026-08-20"}},
        "engines": {},
        "deep": {},
    }

    payload = project_world_markets(snapshot, selector="capital_markets")

    assert payload["ok"] is True
    assert payload["status"] == "observed"
    assert payload["as_of"] == "2026-08-20"
    assert payload["capital_markets"]["status"] == "observed"
    assert payload["capital_markets"]["risk_context"]["as_of"] == "2026-08-20"


def test_supplydesk_forward_dates_do_not_advance_the_evidence_clock() -> None:
    snapshot = _snapshot()
    snapshot["engines"]["supplydesk"] = {
        "ok": True,
        "asof": "2026-08-21",
        "announced_through": "2026-08-31",
        "horizon_end": "2026-09-18",
        "totals": {"net_new_cash_b": 120.0},
        "heaviest_day": {"date": "2026-09-16", "net_new_cash_b": 95.0},
    }

    payload = project_world_markets(snapshot, selector="capital_markets")
    supply = next(
        item
        for item in payload["capital_markets"]["primary_market"]
        if item["id"] == "supplydesk"
    )

    assert payload["as_of"] == "2026-08-20"
    assert payload["citation"]["evidence_as_of"] == "2026-08-20"
    assert supply["announced_through"] == "2026-08-31"
    assert supply["horizon_end"] == "2026-09-18"
    assert supply["heaviest_day"]["date"] == "2026-09-16"
    assert supply["clock_role"] == "scenario_evaluation_not_evidence_clock"


def test_supplydesk_only_snapshot_has_no_evidence_clock() -> None:
    snapshot = {
        "generated_at": "2026-08-21T11:30:00+00:00",
        "headline": {},
        "deep": {},
        "engines": {
            "supplydesk": {
                "ok": True,
                "asof": "2026-08-21",
                "announced_through": "2026-08-31",
                "horizon_end": "2026-09-18",
            }
        },
    }

    payload = project_world_markets(snapshot, selector="capital_markets")

    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["as_of"] is None
    assert payload["citation"]["evidence_as_of"] is None
    assert payload["capital_markets"]["as_of"] is None


def test_rest_route_reads_memory_cache_without_building(monkeypatch) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(api.assemble, "cached_snapshot", lambda: snapshot)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("world-markets reads must never build")

    monkeypatch.setattr(api.assemble, "snapshot", forbidden)
    monkeypatch.setattr(api.assemble, "restore_cached_snapshot", forbidden)
    response = TestClient(api.app).get("/api/v2/world-markets")

    assert response.status_code == 200
    assert response.json()["schema"] == WORLD_MARKETS_SCHEMA
    assert "public" in response.headers["cache-control"]


def test_rest_route_restores_persisted_state_without_building(monkeypatch) -> None:
    state = {"snapshot": None}
    calls: list[str] = []
    monkeypatch.setattr(
        api.assemble,
        "cached_snapshot",
        lambda: state["snapshot"],
    )

    def restore():
        calls.append("restore")
        state["snapshot"] = _snapshot()
        return "durable"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("world-markets reads must never build")

    monkeypatch.setattr(api.assemble, "restore_cached_snapshot", restore)
    monkeypatch.setattr(api.assemble, "snapshot", forbidden)
    response = TestClient(api.app).get("/api/v2/world-markets")

    assert response.status_code == 200
    assert calls == ["restore"]
    assert response.json()["generated_at"] == _snapshot()["generated_at"]


def test_restored_money_market_freshness_is_reaged_without_building(monkeypatch) -> None:
    snapshot = _snapshot()
    desk = snapshot["engines"]["money_market"]
    desk["asof"] = "2000-01-03"
    metric = desk["sections"][0]["metrics"][0]
    metric.update(
        status="available",
        asof="2000-01-03",
        cadence="daily",
        freshness="fresh",
    )
    desk["source_metadata"] = [
        {
            "id": "nyfed_sofr_rate",
            "asof": "2000-01-03",
            "cadence": "daily",
            "freshness": "fresh",
        }
    ]
    desk["sources"] = list(desk["source_metadata"])
    state = {"snapshot": None}
    monkeypatch.setattr(api.assemble, "cached_snapshot", lambda: state["snapshot"])

    def restore():
        state["snapshot"] = snapshot

    monkeypatch.setattr(api.assemble, "restore_cached_snapshot", restore)
    monkeypatch.setattr(
        api.assemble,
        "snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("world-markets reads must never build")
        ),
    )

    response = TestClient(api.app).get(
        "/api/v2/world-markets?section=money_markets"
    )
    payload = response.json()
    refreshed = next(
        item
        for section in payload["money_markets"]["sections"]
        for item in section["metrics"]
        if item.get("id") == "repo_segments.synthetic"
    )

    assert response.status_code == 200
    assert refreshed["freshness"] == "stale"
    assert payload["money_markets"]["freshness"]["evaluation_asof"] != (
        "2000-01-03"
    )


def test_rest_section_selector_matches_mcp_projection_shape(monkeypatch) -> None:
    monkeypatch.setattr(api.assemble, "cached_snapshot", _snapshot)
    client = TestClient(api.app)

    response = client.get("/api/v2/world-markets?section=forex")
    payload = response.json()

    assert response.status_code == 200
    assert payload["selection"] == "forex"
    assert payload["status"] == payload["forex"]["status"]
    assert "money_markets" not in payload
    assert "capital_markets" not in payload
    assert client.get("/api/v2/world-markets?section=unknown").status_code == 422


def test_rest_cold_cache_returns_typed_unavailable_without_building(monkeypatch) -> None:
    monkeypatch.setattr(api.assemble, "cached_snapshot", lambda: None)
    monkeypatch.setattr(api.assemble, "restore_cached_snapshot", lambda: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("world-markets reads must never build")

    monkeypatch.setattr(api.assemble, "snapshot", forbidden)
    response = TestClient(api.app).get("/api/v2/world-markets")
    payload = response.json()

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert payload["ok"] is False
    assert payload["schema"] == WORLD_MARKETS_SCHEMA
    assert payload["status"] == "unavailable"
    assert "never starts collection or model fitting" in payload["reason"]


def test_mcp_tool_selectors_are_public_chartless_and_cache_only(monkeypatch) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: snapshot)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("world_markets_context must stay cache-only")

    monkeypatch.setattr(mcp, "_get_snapshot", forbidden)
    for selector in WORLD_MARKETS_SELECTORS:
        response = mcp.dispatch(
            {
                "jsonrpc": "2.0",
                "id": selector,
                "method": "tools/call",
                "params": {
                    "name": "world_markets_context",
                    "arguments": {"section": selector},
                },
            },
            public=True,
        )
        payload = _mcp_payload(response)
        assert payload["selection"] == selector
        assert payload["chart_history_included"] is False
        assert "LICENSED-SENTINEL" not in json.dumps(payload)

    listed = mcp.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        public=True,
    )["result"]["tools"]
    descriptor = next(item for item in listed if item["name"] == "world_markets_context")
    assert descriptor["inputSchema"]["properties"]["section"]["enum"] == list(
        WORLD_MARKETS_SELECTORS
    )
    assert descriptor["outputSchema"]["properties"]["schema"] == {
        "type": "string"
    }
    assert "world_markets_context" in mcp.SERVER_INSTRUCTIONS
    assert "Undertow" in mcp.SERVER_INSTRUCTIONS


def test_mcp_cold_cache_is_data_unavailable_not_a_tool_failure(monkeypatch) -> None:
    monkeypatch.setattr(mcp, "_get_completed_snapshot", lambda: None)
    response = mcp.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "world_markets_context",
                "arguments": {"section": "forex"},
            },
        },
        public=True,
    )
    payload = _mcp_payload(response)

    assert "isError" not in response["result"]
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["selection"] == "forex"
    assert "never triggers collection" in payload["reason"]


def test_api_discovery_and_openapi_publish_world_markets() -> None:
    discovery = api.api_index()
    spec = api._public_openapi_document()

    assert discovery["rest"]["world_markets_v2"] == "/api/v2/world-markets"
    assert discovery["mcp"]["authentication"] == "none for the eleven public tools"
    route = spec["paths"]["/api/v2/world-markets"]["get"]
    assert set(route["responses"]) == {"200", "503"}
    assert route["parameters"][0]["name"] == "section"
    assert route["parameters"][0]["schema"]["enum"] == list(
        WORLD_MARKETS_SELECTORS
    )
    success = route["responses"]["200"]["content"]["application/json"]["schema"]
    assert success["properties"]["schema"]["const"] == WORLD_MARKETS_SCHEMA
    assert "never starts collection or model fitting" in route["description"]
