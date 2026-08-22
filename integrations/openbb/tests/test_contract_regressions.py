"""Regressions for selector fidelity, clocks, and the public URL boundary."""

from __future__ import annotations

import httpx
import pytest

from openbb_core.app.model.abstract.error import OpenBBError
from openbb_seiche.models import _client
from openbb_seiche.models.funding_stress import SeicheFundingStressFetcher
from openbb_seiche.models.world_markets import SeicheWorldMarketsFetcher


def _mock_client(payload: object, *, status: int = 200) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _world_payload(selection: str) -> dict:
    domains = [
        {
            "id": "money_markets",
            "status": "observed",
            "as_of": "2026-08-20",
            "reading": "Money reading.",
            "coverage": {"coverage_pct": 98.8},
        },
        {
            "id": "forex",
            "status": "derived",
            "as_of": "2026-08-20",
            "reading": "FX reading.",
            "coverage": {"currency_leaders": 22},
        },
        {
            "id": "capital_markets",
            "status": "derived",
            "as_of": "2026-08-19",
            "reading": "Capital reading.",
            "coverage": {"positioning_blocks": 3},
        },
    ]
    return {
        "schema": "seiche.world-markets.v1",
        "selection": selection,
        "generated_at": "2026-08-21T20:54:06+00:00",
        "as_of": "2026-08-20",
        "context_only": True,
        "clocks": {
            "snapshot_generated_at": "2026-08-21T20:54:06+00:00",
            "evaluation_at": "2026-08-21T21:15:51+00:00",
            "selected_evidence_as_of": "2026-08-20",
            "boundary": "Response time never advances a source clock.",
        },
        "citation": {
            "canonical_url": "https://seiche.info/markets/",
            "api_url": "https://api.seiche.info/api/v2/world-markets",
        },
        "scope": {"coverage_claim": "curated_partial_non_exhaustive"},
        "coverage": {"domains": domains},
        "disclaimer": "Research context, not investment advice.",
    }


@pytest.mark.asyncio
async def test_domain_selector_returns_only_the_selected_projection():
    payload = _world_payload("forex")
    payload["forex"] = {"status": "derived", "currencies": [{"id": "EUR"}]}
    client = _mock_client(payload)
    try:
        rows = await SeicheWorldMarketsFetcher.fetch_data(
            {"selector": "forex"}, {}, client=client
        )
    finally:
        await client.aclose()
    assert [row.domain for row in rows] == ["forex"]
    assert rows[0].details == payload["forex"]
    assert rows[0].generated_at == payload["clocks"]["snapshot_generated_at"]
    assert rows[0].evaluated_at == payload["clocks"]["evaluation_at"]
    assert rows[0].selected_evidence_as_of == "2026-08-20"


@pytest.mark.asyncio
async def test_source_and_methodology_selectors_preserve_selected_content():
    source_payload = _world_payload("sources")
    source_payload["sources"] = [
        {
            "id": "ofr_short_term_funding_data_api",
            "publisher": "Office of Financial Research",
            "domains": ["money_markets"],
            "url": "https://data.financialresearch.gov/v1",
            "status": "structural",
            "used_in_snapshot": True,
            "projection_paths": ["money_markets.sections[]"],
            "catalog_role": "linked_projected_evidence",
        }
    ]
    source_client = _mock_client(source_payload)
    try:
        sources = await SeicheWorldMarketsFetcher.fetch_data(
            {"selector": "sources"}, {}, client=source_client
        )
    finally:
        await source_client.aclose()
    assert [row.domain for row in sources] == ["source:ofr_short_term_funding_data_api"]
    assert sources[0].details["url"] == "https://data.financialresearch.gov/v1"
    assert sources[0].coverage["used_in_snapshot"] is True

    methodology_payload = _world_payload("methodology")
    methodology_payload["methodology"] = {
        "status": "structural",
        "projection": "Pure bounded projection.",
        "boundedness": {"metrics_per_section_max": 8},
    }
    methodology_client = _mock_client(methodology_payload)
    try:
        methodology = await SeicheWorldMarketsFetcher.fetch_data(
            {"selector": "methodology"}, {}, client=methodology_client
        )
    finally:
        await methodology_client.aclose()
    assert [row.domain for row in methodology] == ["methodology"]
    assert methodology[0].coverage == {"metrics_per_section_max": 8}
    assert methodology[0].details == methodology_payload["methodology"]


@pytest.mark.asyncio
async def test_world_markets_rejects_mismatched_selection_and_missing_clock_boundary():
    payload = _world_payload("summary")
    client = _mock_client(payload)
    try:
        with pytest.raises(OpenBBError, match="selection does not match"):
            await SeicheWorldMarketsFetcher.fetch_data(
                {"selector": "forex"}, {}, client=client
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_world_markets_rejects_an_exhaustive_coverage_claim():
    payload = _world_payload("summary")
    payload["scope"]["coverage_claim"] = "exhaustive"
    client = _mock_client(payload)
    try:
        with pytest.raises(OpenBBError, match="unsupported coverage claim"):
            await SeicheWorldMarketsFetcher.fetch_data(
                {"selector": "summary"}, {}, client=client
            )
    finally:
        await client.aclose()

    payload = _world_payload("summary")
    payload["clocks"].pop("boundary")
    client = _mock_client(payload)
    try:
        with pytest.raises(OpenBBError, match="clock boundary"):
            await SeicheWorldMarketsFetcher.fetch_data(
                {"selector": "summary"}, {}, client=client
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_funding_stress_rejects_wrong_schema():
    client = _mock_client(
        {
            "schema": "unexpected",
            "generated_at": "2026-08-21T20:54:06+00:00",
            "index": 45.3,
            "regime": "STRAIN",
            "coverage_pct": 100.0,
        }
    )
    try:
        with pytest.raises(OpenBBError, match="unsupported schema"):
            await SeicheFundingStressFetcher.fetch_data({}, {}, client=client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_transport_rejects_non_json_oversize_and_redirects():
    async def non_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="not json",
            headers={"Content-Type": "text/plain"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(non_json))
    try:
        with pytest.raises(OpenBBError, match="non-JSON"):
            await _client.get_json("/api/gauge", client=client)
    finally:
        await client.aclose()

    async def oversize(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{}" + b" " * _client.MAX_RESPONSE_BYTES,
            headers={"Content-Type": "application/json"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(oversize))
    try:
        with pytest.raises(OpenBBError, match="exceeded"):
            await _client.get_json("/api/gauge", client=client)
    finally:
        await client.aclose()

    requests = 0

    async def redirect(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            302,
            headers={"Location": "https://example.com/untrusted"},
            request=request,
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(redirect), follow_redirects=True
    )
    try:
        with pytest.raises(OpenBBError, match="302"):
            await _client.get_json("/api/gauge", client=client)
    finally:
        await client.aclose()
    assert requests == 1


@pytest.mark.parametrize(
    "path",
    ["https://example.com/api/gauge", "//example.com/api/gauge", "/api/gauge?x=1"],
)
def test_canonical_url_rejects_non_origin_paths(path):
    with pytest.raises(OpenBBError, match="absolute-origin paths"):
        _client.canonical_url(path)


def test_base_url_rejects_invalid_port_and_unsafe_characters(monkeypatch):
    monkeypatch.setenv("SEICHE_OPENBB_BASE_URL", "https://example.com:not-a-port")
    with pytest.raises(OpenBBError, match="invalid port"):
        _client.base_url()

    monkeypatch.setenv("SEICHE_OPENBB_BASE_URL", "https://example.com\\unsafe")
    with pytest.raises(OpenBBError, match="unsafe URL characters"):
        _client.base_url()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("https://[::1", "malformed"),
        ("https://exa mple.com", "unsafe URL characters"),
        ("https://%zz", "invalid hostname"),
        ("https://.", "invalid hostname"),
        ("https://example.com:0", "invalid port"),
    ],
)
def test_base_url_rejects_malformed_origins(monkeypatch, value, message):
    monkeypatch.setenv("SEICHE_OPENBB_BASE_URL", value)
    with pytest.raises(OpenBBError, match=message):
        _client.base_url()
