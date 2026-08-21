"""Contract tests for the credential-free OpenBB provider."""

from __future__ import annotations

import httpx
import pytest

from openbb_core.app.model.abstract.error import OpenBBError
from openbb_seiche import seiche_provider
from openbb_seiche.models import _client
from openbb_seiche.models.data_health import SeicheDataHealthFetcher
from openbb_seiche.models.funding_stress import SeicheFundingStressFetcher
from openbb_seiche.models.world_markets import SeicheWorldMarketsFetcher


def _client_for(payload: dict, *, status: int = 200) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_funding_stress_flattens_public_contract():
    client = _client_for(
        {
            "schema": "seiche.gauge.v1",
            "generated_at": "2026-08-21T20:54:06+00:00",
            "index": 45.3,
            "regime": "STRAIN",
            "coverage_pct": 100.0,
            "tell": 26.3,
            "p_event_5bd": 0.06,
            "p_event_5bd_dispersion": 0.024,
            "p_event_5bd_members": {"rule": 0.07, "ml": 0.05},
            "next_turn": {"date": "2026-08-31", "forecast_bp": 2.2},
            "crunch_windows": [{"date": "2026-08-27"}],
            "faults": 0,
            "notes": "point-in-time; not investment advice",
        }
    )
    try:
        out = await SeicheFundingStressFetcher.fetch_data({}, {}, client=client)
    finally:
        await client.aclose()
    assert out[0].stress_index == 45.3
    assert out[0].regime == "STRAIN"
    assert out[0].crunch_window_count == 1
    assert out[0].ensemble_members == {"rule": 0.07, "ml": 0.05}


@pytest.mark.asyncio
async def test_world_markets_preserves_status_and_evidence_clock():
    client = _client_for(
        {
            "schema": "seiche.world-markets.v1",
            "selection": "summary",
            "generated_at": "2026-08-21T20:54:06+00:00",
            "context_only": True,
            "clocks": {
                "snapshot_generated_at": "2026-08-21T20:54:06+00:00",
                "evaluation_at": "2026-08-21T21:15:51+00:00",
                "selected_evidence_as_of": "2026-08-20",
                "boundary": "Response time never advances an evidence clock.",
            },
            "citation": {
                "canonical_url": "https://seiche.info/markets/",
                "api_url": "https://api.seiche.info/api/v2/world-markets",
            },
            "scope": {"coverage_claim": "curated_partial_non_exhaustive"},
            "disclaimer": "Research context, not investment advice.",
            "summary": {
                "domains": [
                    {
                        "id": "money_markets",
                        "status": "observed",
                        "as_of": "2026-08-20",
                        "reading": "Below watch threshold.",
                        "coverage": {"coverage_pct": 98.8},
                    }
                ]
            },
        }
    )
    try:
        out = await SeicheWorldMarketsFetcher.fetch_data(
            {"selector": "summary"}, {}, client=client
        )
    finally:
        await client.aclose()
    assert out[0].status == "observed"
    assert out[0].as_of == "2026-08-20"
    assert out[0].coverage == {"coverage_pct": 98.8}
    assert out[0].evaluated_at == "2026-08-21T21:15:51+00:00"
    assert out[0].clock_boundary == "Response time never advances an evidence clock."


@pytest.mark.asyncio
async def test_data_health_filters_without_erasing_dead_rows():
    client = _client_for(
        {
            "generated_at": "2026-08-21T20:54:06+00:00",
            "version": "0.11.0",
            "faults": [],
            "provenance": [
                {
                    "mnemonic": "SOFR",
                    "source": "fred",
                    "staleness": "fresh",
                    "n_obs": 10,
                },
                {"mnemonic": "TED", "source": "fred", "staleness": "dead", "n_obs": 20},
                {
                    "mnemonic": "BGCR",
                    "source": "ofr",
                    "staleness": "fresh",
                    "n_obs": 30,
                },
            ],
        }
    )
    try:
        out = await SeicheDataHealthFetcher.fetch_data(
            {"source": "fred", "staleness": "dead"}, {}, client=client
        )
    finally:
        await client.aclose()
    assert [row.mnemonic for row in out] == ["TED"]
    assert out[0].observation_count == 20
    assert out[0].snapshot_generated_at == "2026-08-21T20:54:06+00:00"
    assert out[0].snapshot_fault_count == 0


def test_base_url_rejects_remote_plain_http(monkeypatch):
    monkeypatch.setenv("SEICHE_OPENBB_BASE_URL", "http://example.com")
    with pytest.raises(OpenBBError, match="must use HTTPS"):
        _client.base_url()


def test_base_url_allows_local_http(monkeypatch):
    monkeypatch.setenv("SEICHE_OPENBB_BASE_URL", "http://127.0.0.1:8787")
    assert _client.base_url() == "http://127.0.0.1:8787"


def test_base_url_rejects_path_prefix(monkeypatch):
    monkeypatch.setenv("SEICHE_OPENBB_BASE_URL", "https://example.com/seiche")
    with pytest.raises(OpenBBError, match="bare origin"):
        _client.base_url()


def test_provider_and_router_plugins_load():
    from openbb_seiche.router.seiche_router import router

    assert sorted(seiche_provider.fetcher_dict) == [
        "SeicheDataHealth",
        "SeicheFundingStress",
        "SeicheWorldMarkets",
    ]
    assert seiche_provider.credentials == []
    assert {route.name for route in router.api_router.routes} == {
        "data_health",
        "funding_stress",
        "world_markets",
    }
