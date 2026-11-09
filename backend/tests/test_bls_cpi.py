"""BLS CPI from the BLS Public Data API: parsed, bounded, cited, and held off.

The fixture is the API's own response of 2026-11-02 (public domain BLS data).
"""
from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from seiche.domain.observation import ConnectorClassification, RedistributionStatus, SemanticRole
from seiche.markets.us_usd.pack import PACK
from seiche.repository import SQLiteMarketRepository
from seiche.sources import bls_cpi
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import PRODUCTION_ADAPTER_KEYS, build_official_adapters

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "bls" / "cpi-2025-2026.json").read_text())
URI = bls_cpi.request_uri(2025, 2026)


def document(payload: object = None, *, uri: str = URI) -> FetchedDocument:
    body = FIXTURE if payload is None else payload
    return FetchedDocument(uri, "application/json", json.dumps(body).encode(), bls_cpi.ADAPTER_ID)


def mutated(change) -> dict:
    payload = copy.deepcopy(FIXTURE)
    change(payload)
    return payload


def test_parser_reads_both_series_as_month_end_index_levels():
    points = bls_cpi.parse_document(document())
    energy = [(p.event_time, p.raw_value) for p in points if p.instrument_id == "US.BLS.CPI_ENERGY_SA"]
    core = [(p.event_time, p.raw_value) for p in points if p.instrument_id == "US.BLS.CPI_CORE_SA"]
    # 21 months, less October 2025, which BLS marks "-" with the footnote
    # "Data unavailable due to the 2025 lapse in appropriations": absent, never zero.
    assert len(energy) == len(core) == 20
    assert date(2025, 10, 31) not in {day for day, _ in energy + core}
    assert energy[0] == (date(2025, 1, 31), Decimal("282.272"))
    assert energy[-1] == (date(2026, 9, 30), Decimal("337.834"))
    assert core[-1] == (date(2026, 9, 30), Decimal("338.896"))
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)
    evidence = json.loads(points[-1].row_evidence)
    assert evidence["api_url"] == bls_cpi.API_URL and evidence["period"] == "M09"
    assert points[-1].revision_id.startswith("bls:")


def test_annual_averages_are_not_months():
    def add_m13(payload):
        payload["Results"]["series"][0]["data"].append(
            {"year": "2025", "period": "M13", "periodName": "Annual", "value": "290.000", "footnotes": [{}]})
    points = bls_cpi.parse_document(document(mutated(add_m13)))
    assert Decimal("290.000") not in {p.raw_value for p in points}


@pytest.mark.parametrize("change", [
    lambda p: p.update(status="REQUEST_NOT_PROCESSED"),
    lambda p: p["Results"].update(series=p["Results"]["series"][:1]),
    lambda p: p["Results"]["series"].append(copy.deepcopy(p["Results"]["series"][0])),
    lambda p: p["Results"]["series"][0].update(seriesID="CUUR0000SA0E"),
    lambda p: p["Results"]["series"][0]["data"][0].update(period="Q03"),
    lambda p: p["Results"]["series"][0]["data"][0].update(year="26"),
    lambda p: p["Results"]["series"][0]["data"][0].update(value="N/A"),
    lambda p: p["Results"]["series"][0]["data"][0].update(value="1,337.8"),
    lambda p: p["Results"]["series"][0]["data"][0].update(value="0"),
    lambda p: p["Results"]["series"][0]["data"][0].update(value="12000"),
    lambda p: p["Results"]["series"][0]["data"].append(copy.deepcopy(p["Results"]["series"][0]["data"][0])),
    lambda p: p["Results"]["series"][0].update(data=[]),
    lambda p: p["Results"]["series"][0].update(data=[{"year": "2026", "period": "M01", "value": "1"}] * 200),
], ids=["failed request", "series missing", "series duplicated", "unexpected series", "not a month",
        "bad year", "unknown marker", "separator", "zero", "beyond bound", "repeated month", "no months",
        "row bound"])
def test_a_changed_response_fails_closed(change):
    with pytest.raises(ValueError):
        bls_cpi.parse_document(document(mutated(change)))


def test_source_identity_and_size_bounds():
    with pytest.raises(ValueError, match="unrecognized or oversized"):
        bls_cpi.parse_document(document(uri="https://example.invalid/data"))
    big = FetchedDocument(URI, "application/json", b" " * (bls_cpi.MAX_DOCUMENT_BYTES + 1), bls_cpi.ADAPTER_ID)
    with pytest.raises(ValueError, match="unrecognized or oversized"):
        bls_cpi.parse_document(big)
    with pytest.raises(ValueError, match="not JSON"):
        bls_cpi.parse_document(FetchedDocument(URI, "application/json", b"<html>", bls_cpi.ADAPTER_ID))
    with pytest.raises(ValueError, match="more years"):
        bls_cpi.request_uri(2015, 2026)


@pytest.mark.asyncio
async def test_bounded_fetch_posts_once_for_both_series():
    body = json.dumps(FIXTURE).encode()
    requests = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream(), headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        (doc,) = await bls_cpi.fetch_documents(client, 2025, 2026)
    assert doc.source_uri == URI and doc.payload == body
    assert len(requests) == 1 and requests[0].method == "POST" and str(requests[0].url) == bls_cpi.API_URL
    assert json.loads(requests[0].content) == {
        "seriesid": ["CUSR0000SA0E", "CUSR0000SA0L1E"], "startyear": "2025", "endyear": "2026"}
    assert requests[0].headers["user-agent"] == bls_cpi.USER_AGENT
    for response, match in (
        (httpx.Response(302, headers={"location": "https://example.invalid/"}), "HTTP 302"),
        (httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}), "media type"),
        (httpx.Response(200, content=b"{}", headers={"content-type": "application/json",
                                                     "content-length": str(bls_cpi.MAX_DOCUMENT_BYTES + 1)}),
         "exceeds bound"),
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request, r=response: r)) as client:
            with pytest.raises(ValueError, match=match):
                await bls_cpi.fetch_documents(client, 2025, 2026)


def test_the_owner_decision_clears_the_hold_and_the_pack_declares_bls():
    from seiche import source_decisions

    assert source_decisions.allows(source_decisions.BLS_API) and bls_cpi.RIGHTS_HOLD is None
    adapter = PACK.adapter_map[bls_cpi.ADAPTER_ID]
    assert adapter.classification is ConnectorClassification.OFFICIAL_OPEN
    assert adapter.redistribution_status is RedistributionStatus.ALLOWED
    assert adapter.collection_cadence == "P1D"
    assert bls_cpi.declared_instruments(PACK) == set(bls_cpi.SERIES.values())
    assert {PACK.instrument_map[i].semantic_role for i in bls_cpi.SERIES.values()} == {
        SemanticRole.CONSUMER_PRICE_INDEX}
    assert ("US-USD", bls_cpi.ADAPTER_ID) in PRODUCTION_ADAPTER_KEYS
    assert bls_cpi.rights_gate_open(PACK) is True
    adapters = build_official_adapters(repository=SQLiteMarketRepository())
    assert bls_cpi.ADAPTER_ID in {item.adapter_id for item in adapters}


def test_a_restored_hold_keeps_bls_off_even_though_the_pack_declares_it(monkeypatch):
    monkeypatch.setattr(bls_cpi, "RIGHTS_HOLD", "Terms of Service not accepted")
    assert bls_cpi.declared_instruments(PACK) == set(bls_cpi.SERIES.values())
    assert bls_cpi.rights_gate_open(PACK) is False
    adapters = build_official_adapters(repository=SQLiteMarketRepository())
    assert bls_cpi.ADAPTER_ID not in {item.adapter_id for item in adapters}


def test_the_registry_record_quotes_the_rights_and_the_terms():
    ledger = json.loads((ROOT / "docs" / "data-source-expansion.json").read_text())
    record = next(item for item in ledger["candidates"] if item["id"] == bls_cpi.REGISTRY_RECORD)
    assert record["access_url"] == bls_cpi.API_URL and record["publisher_url"] == bls_cpi.RELEASE_URL
    assert record["rights_status"] == "allowed_in_current_pack" and record["implementation_status"] == "implemented"
    assert "seiche.source-use.2026-11-08.bls-api" in record["blocker"]
    assert record["rights_evidence_url"] == bls_cpi.TERMS_URL
    assert bls_cpi.VOUCH_STATEMENT in record["blocker"] and bls_cpi.ATTRIBUTION in record["blocker"]
    assert "everything that we publish, both in hard copy and electronically, is in the public domain" in record["blocker"]
    assert "constitutes acceptance to this Agreement" in record["blocker"]
    for series, fred_id in bls_cpi.FRED_COPIES.items():
        assert series in record["blocker"] and fred_id in record["blocker"]
    assert bls_cpi.VOUCH_STATEMENT in bls_cpi.ATTRIBUTION
