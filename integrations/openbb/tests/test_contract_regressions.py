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
    china_only = selection == "china_macro"
    generated_at = None if china_only else "2026-08-21T20:54:06+00:00"
    domain_clocks = (
        {"money_markets": None, "forex": None, "capital_markets": None}
        if china_only
        else {
            "money_markets": "2026-08-20",
            "forex": "2026-08-20",
            "capital_markets": "2026-08-19",
        }
    )
    latest = None if china_only else "2026-08-20"
    if selection in {"summary", "all"}:
        selected = latest
    elif selection in domain_clocks:
        selected = domain_clocks[selection]
    else:
        selected = None
    payload = {
        "schema": "seiche.world-markets.v1",
        "selection": selection,
        "generated_at": generated_at,
        "as_of": selected,
        "context_only": True,
        "clocks": {
            "snapshot_generated_at": generated_at,
            "evaluation_at": "2026-08-21T21:15:51+00:00",
            "latest_domain_as_of": latest,
            "domains": domain_clocks,
            "selected_evidence_as_of": selected,
            "excluded_from_observation_clocks": ["china_macro.knowledge_time"],
            "boundary": "Response time never advances a source clock.",
        },
        "citation": {
            "canonical_url": "https://seiche.info/markets/",
            "api_url": "https://api.seiche.info/api/v2/world-markets",
            "generated_at": generated_at,
            "evidence_as_of": selected,
        },
        "scope": {"coverage_claim": "curated_partial_non_exhaustive"},
        "coverage": {"domains": domains},
        "disclaimer": "Research context, not investment advice.",
    }
    if selection == "summary":
        payload["summary"] = {"domains": domains}
    elif selection in domain_clocks:
        payload[selection] = {}
    elif selection == "china_macro":
        payload["china_macro"] = _china_macro()
    elif selection == "sources":
        payload["sources"] = []
    elif selection == "methodology":
        payload["methodology"] = {}
    else:
        payload.update(
            money_markets={},
            forex={},
            capital_markets={},
            china_macro=_china_macro(),
            sources=[],
            methodology={},
        )
    return payload


CHINA_SERIES_IDS = (
    "CN.NBS.CPI_INDEX",
    "CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY",
    "CN.NBS.MANUFACTURING_PMI",
    "CN.NBS.PPI_INDEX",
)


def _china_series(series_id: str) -> dict:
    return {
        "series_id": series_id,
        "catalogid": "catalog-id",
        "catalog_label": "Catalog label",
        "row_id": "row-id",
        "i": "indicator-id",
        "ek": "export-key",
        "ek_dp": "export-key-dimension",
        "dp": "1",
        "dp_name": "dimension",
        "label": "Series label",
        "reference_release_url": "https://www.stats.gov.cn/english/PressRelease/202608/t20260810_1965018.html",
        "release_url": "https://www.stats.gov.cn/english/PressRelease/202608/t20260810_1965018.html",
        "source_unit_label_exact": "%",
        "source_unit_semantically_authoritative": True,
        "semantic_contract": {
            "value_kind": "index_level",
            "canonical_unit": "index_points",
            "comparison_base": None,
            "transform": None,
            "threshold": None,
        },
        "value_publication": "withheld_pending_rights_review",
    }


def _china_macro(*, available: bool = True) -> dict:
    common = {
        "schema": "seiche.nbs-macro-context.v1",
        "dataset": "CN.NBS.MACRO_CONTEXT",
        "publisher": "National Bureau of Statistics of China",
        "source_url": "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData",
        "terms_url": "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html",
        "status": "restricted" if available else "structural",
        "evidence_status": "restricted" if available else "unavailable",
        "available": available,
        "as_of": None,
        "context_only": True,
        "scoring_eligible": False,
        "cn_cny_gauge_eligible": False,
        "values_published": False,
        "raw_evidence_included": False,
        "history_included": False,
        "public_distribution": "metadata_only",
        "rights_status": "redistribution_review_required",
        "series_catalog": [_china_series(series_id) for series_id in CHINA_SERIES_IDS],
        "series_count": 4,
        "reading": "Signed identities and provenance; values withheld.",
        "boundaries": ["owner", "values", "scoring"],
    }
    if not available:
        return {**common, "reason_code": "signed_owner_export_required"}
    return {
        **common,
        "revision_id": "nbs-2026-07-r1",
        "predecessor_revision_id": None,
        "knowledge_time": "2026-08-10T02:00:00Z",
        "source_registry_ids": [
            "nbs_monthly_data_browser",
            "nbs_terms_of_service",
        ],
        "provenance": {
            "manifest_sha256": "a" * 64,
            "owner_attestation": "ed25519",
        },
        "attestation": {
            "schema": "seiche.nbs-owner-export-signature.v1",
            "algorithm": "ed25519",
            "domain": "seiche-nbs-owner-export-v1",
            "export_id": "nbs-2026-07-r1",
            "signer_key_id": "c" * 64,
            "signed_at": "2026-08-10T02:05:00Z",
            "manifest_sha256": "a" * 64,
            "public_projection_sha256": "d" * 64,
            "signature": "e" * 128,
        },
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
async def test_china_macro_is_a_metadata_row_with_a_separate_knowledge_clock():
    payload = _world_payload("china_macro")
    payload.update(generated_at=None, as_of=None, china_macro=_china_macro())
    payload["clocks"].update(
        snapshot_generated_at=None,
        selected_evidence_as_of=None,
    )
    client = _mock_client(payload)
    try:
        rows = await SeicheWorldMarketsFetcher.fetch_data(
            {"selector": "china_macro"}, {}, client=client
        )
    finally:
        await client.aclose()

    assert [row.domain for row in rows] == ["china_macro"]
    assert rows[0].status == "restricted"
    assert rows[0].as_of is None
    assert rows[0].selected_evidence_as_of is None
    assert rows[0].generated_at is None
    assert rows[0].knowledge_time == "2026-08-10T02:00:00Z"
    assert rows[0].coverage == {
        "available": True,
        "evidence_status": "restricted",
        "public_distribution": "metadata_only",
        "rights_status": "redistribution_review_required",
        "series_count": 4,
        "values_published": False,
    }


@pytest.mark.asyncio
async def test_all_appends_china_without_borrowing_the_market_observation_clock():
    payload = _world_payload("all")
    payload["china_macro"] = _china_macro()
    client = _mock_client(payload)
    try:
        rows = await SeicheWorldMarketsFetcher.fetch_data(
            {"selector": "all"}, {}, client=client
        )
    finally:
        await client.aclose()

    assert [row.domain for row in rows] == [
        "money_markets",
        "forex",
        "capital_markets",
        "china_macro",
    ]
    china = rows[-1]
    assert china.selected_evidence_as_of is None
    assert china.knowledge_time == "2026-08-10T02:00:00Z"
    assert rows[0].selected_evidence_as_of == "2026-08-20"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload["china_macro"].update(values_published=True),
        lambda payload: payload["china_macro"]["series_catalog"][0].update(
            latest_value="100.5"
        ),
        lambda payload: payload["china_macro"]["series_catalog"][0].update(
            value="100.5"
        ),
        lambda payload: payload["china_macro"]["series_catalog"][0].update(
            harmless_metric=100.5
        ),
        lambda payload: payload["china_macro"].pop("knowledge_time"),
        lambda payload: payload["china_macro"].pop("provenance"),
        lambda payload: payload["china_macro"].pop("attestation"),
        lambda payload: payload["china_macro"]["series_catalog"].reverse(),
        lambda payload: payload["china_macro"]["provenance"].update(
            raw_sha256="b" * 64
        ),
        lambda payload: payload["china_macro"]["attestation"].update(
            raw_sha256="b" * 64
        ),
        lambda payload: payload["china_macro"]["attestation"].update(
            signed_at="2026-08-10T01:59:59Z"
        ),
        lambda payload: (
            payload["china_macro"].update(knowledge_time="2026-08-10T02:00:00.000001Z"),
            payload["china_macro"]["attestation"].update(
                signed_at="2026-08-10T02:00:00Z"
            ),
        ),
        lambda payload: payload["clocks"].update(
            selected_evidence_as_of="2026-08-10T02:00:00Z"
        ),
        lambda payload: payload["citation"].update(
            evidence_as_of="2026-08-10T02:00:00Z"
        ),
        lambda payload: payload["clocks"]["domains"].update(
            china_macro="2026-08-10T02:00:00Z"
        ),
        lambda payload: payload["citation"].pop("evidence_as_of"),
        lambda payload: payload["clocks"].pop("selected_evidence_as_of"),
        lambda payload: payload["clocks"].update(excluded_from_observation_clocks=[]),
        lambda payload: (
            payload.update(generated_at="2026-08-10T02:00:00Z"),
            payload["clocks"].update(snapshot_generated_at="2026-08-10T02:00:00Z"),
            payload["citation"].update(generated_at="2026-08-10T02:00:00Z"),
        ),
    ),
)
async def test_china_macro_rejects_observation_promotion(mutate):
    payload = _world_payload("china_macro")
    mutate(payload)
    client = _mock_client(payload)
    try:
        with pytest.raises(OpenBBError):
            await SeicheWorldMarketsFetcher.fetch_data(
                {"selector": "china_macro"}, {}, client=client
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unavailable_china_state_is_exact_and_cannot_carry_signed_metadata():
    payload = _world_payload("china_macro")
    payload["china_macro"] = _china_macro(available=False)
    client = _mock_client(payload)
    try:
        rows = await SeicheWorldMarketsFetcher.fetch_data(
            {"selector": "china_macro"}, {}, client=client
        )
    finally:
        await client.aclose()
    assert rows[0].status == "structural"
    assert rows[0].knowledge_time is None
    assert rows[0].coverage["available"] is False

    for field, value in (
        ("knowledge_time", "2026-08-10T02:00:00Z"),
        ("revision_id", "nbs-forged"),
        ("provenance", {}),
        ("attestation", {}),
    ):
        forged = _world_payload("china_macro")
        forged["china_macro"] = _china_macro(available=False)
        forged["china_macro"][field] = value
        client = _mock_client(forged)
        try:
            with pytest.raises(OpenBBError):
                await SeicheWorldMarketsFetcher.fetch_data(
                    {"selector": "china_macro"}, {}, client=client
                )
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_all_requires_china_and_named_selectors_reject_extra_projections():
    missing_china = _world_payload("all")
    missing_china.pop("china_macro")
    client = _mock_client(missing_china)
    try:
        with pytest.raises(OpenBBError, match="requested selector"):
            await SeicheWorldMarketsFetcher.fetch_data(
                {"selector": "all"}, {}, client=client
            )
    finally:
        await client.aclose()

    named = _world_payload("forex")
    named["china_macro"] = _china_macro()
    client = _mock_client(named)
    try:
        with pytest.raises(OpenBBError, match="requested selector"):
            await SeicheWorldMarketsFetcher.fetch_data(
                {"selector": "forex"}, {}, client=client
            )
    finally:
        await client.aclose()


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
