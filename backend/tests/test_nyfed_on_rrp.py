"""ON RRP take-up from the NY Fed's own reverse repo results, not FRED RRPONTSYD."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from seiche import store
from seiche.domain.observation import CanonicalUnit, RedistributionStatus, SemanticRole
from seiche.markets.atlas import build_global_money_market_atlas
from seiche.markets.registry import default_registry
from seiche.markets.us_usd.pack import PACK
from seiche.repository import SQLiteMarketRepository
from seiche.sources.canonical import FetchedDocument
from seiche.sources.official import (
    NYFED_REPO_RESULTS_URI,
    NYFED_REVERSE_REPO_RESULTS_URI,
    build_official_adapters,
    parse_nyfed_facilities,
)

FIXTURES = Path(__file__).parent / "fixtures" / "us_usd"
REVERSE_URI = NYFED_REVERSE_REPO_RESULTS_URI.format(count=5)
REPO_URI = NYFED_REPO_RESULTS_URI.format(count=5)


def fixture_document(name: str, uri: str, label: str) -> FetchedDocument:
    return FetchedDocument(uri, "application/json", (FIXTURES / name).read_bytes(), label)


def reverse_document(payload: object | None = None) -> FetchedDocument:
    if payload is None:
        return fixture_document(
            "nyfed-reverserepo-results-last5.json", REVERSE_URI, "nyfed_reverse_repo_operations"
        )
    return FetchedDocument(REVERSE_URI, "application/json", json.dumps(payload).encode(),
                           "nyfed_reverse_repo_operations")


def repo_document() -> FetchedDocument:
    return fixture_document("nyfed-repo-results-last5.json", REPO_URI, "nyfed_repo_operations")


def operation(day: str, amount: object, *, kind="Reverse Repo", term="Overnight", note=""):
    return {"operationDate": day, "operationType": kind, "term": term,
            "operationMethod": "Fixed Rate", "auctionStatus": "Results", "note": note,
            "totalAmtAccepted": amount}


def test_pack_declares_on_rrp_on_the_allowed_facility_adapter_after_srf():
    spec = PACK.instrument_map["US.NYFED.ON_RRP_TAKEUP"]
    assert spec.source_adapter_id == "nyfed_facilities"
    assert spec.semantic_role is SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP
    assert spec.canonical_unit is CanonicalUnit.LOCAL_CURRENCY_MILLIONS
    assert spec.normalize(Decimal("4.375")) == Decimal("4375")
    # Same rights record as SRF; nothing about the adapter was widened.
    assert PACK.adapter_map["nyfed_facilities"].redistribution_status is RedistributionStatus.ALLOWED
    ids = [item.instrument_id for item in PACK.instruments]
    # Pack order is kernel role priority: facility_usage keeps reading SRF.
    assert ids.index("US.NYFED.SRF_TAKEUP") < ids.index("US.NYFED.ON_RRP_TAKEUP")


def test_reverse_repo_fixture_parses_daily_take_up_in_billions():
    points = parse_nyfed_facilities(reverse_document())
    assert [(p.instrument_id, p.event_time, p.raw_value) for p in points] == [
        ("US.NYFED.ON_RRP_TAKEUP", date(2026, 10, 26), Decimal("0.2")),
        ("US.NYFED.ON_RRP_TAKEUP", date(2026, 10, 27), Decimal("0.226")),
        ("US.NYFED.ON_RRP_TAKEUP", date(2026, 10, 28), Decimal("0.176")),
        ("US.NYFED.ON_RRP_TAKEUP", date(2026, 10, 29), Decimal("0.353")),
        ("US.NYFED.ON_RRP_TAKEUP", date(2026, 10, 30), Decimal("4.375")),
    ]
    evidence = json.loads(points[-1].row_evidence)
    assert evidence["label"] == "reverseRepo.totalAmtAccepted"
    assert evidence["row"][0]["operationId"] == "RP 103026 26"


def test_repo_document_still_parses_only_srf_take_up():
    points = parse_nyfed_facilities(repo_document())
    assert {p.instrument_id for p in points} == {"US.NYFED.SRF_TAKEUP"}
    by_day = {p.event_time: p.raw_value for p in points}
    assert by_day[date(2026, 10, 29)] == Decimal("0.031")
    assert by_day[date(2026, 10, 30)] == Decimal("0")


def test_small_value_exercise_is_summed_and_term_or_other_operations_are_not():
    # 2025-01-24: the 10:30 small value exercise plus the 12:45 daily operation
    # is the published daily total of overnight reverse repos (104.972 $B).
    payload = {"repo": {"operations": [
        operation("2025-01-24", 95000000, note="This operation is a Small Value Exercise (SVE)"),
        operation("2025-01-24", 104877000000),
        operation("2025-01-24", 5000000000, term="Term"),
        operation("2025-01-24", 7000000000, kind="Repo"),
    ]}}
    (point,) = parse_nyfed_facilities(reverse_document(payload))
    assert point.raw_value == Decimal("104.972")
    assert point.event_time == date(2025, 1, 24)


def test_missing_or_invalid_amount_is_withheld_never_zero_filled():
    payload = {"repo": {"operations": [
        operation("2026-10-29", None),
        operation("2026-10-29", 353000000),
        operation("2026-10-28", "-1"),
        operation("2026-10-27", "NaN"),
        operation("2026-10-26", 0),
    ]}}
    points = parse_nyfed_facilities(reverse_document(payload))
    # Only the explicit zero survives; a partly unreported date is not a total.
    assert [(p.event_time, p.raw_value) for p in points] == [(date(2026, 10, 26), Decimal("0"))]


@pytest.mark.parametrize("payload", [
    {"repo": {"operations": []}},
    {"repo": {"operations": [operation("2026-10-29", 1, kind="Repo")]}},
    {"repo": {"operations": [operation("not-a-date", 1)]}},
    {"operations": []},
])
def test_reverse_repo_response_without_overnight_results_fails_closed(payload):
    with pytest.raises(ValueError):
        parse_nyfed_facilities(reverse_document(payload))


@pytest.mark.asyncio
async def test_facility_fetch_reads_repo_and_reverse_repo_from_the_publisher():
    requested = []

    def publish(request):
        requested.append(str(request.url))
        name = ("nyfed-reverserepo-results-last5.json" if "/reverserepo/" in request.url.path
                else "nyfed-repo-results-last5.json")
        return httpx.Response(200, content=(FIXTURES / name).read_bytes(),
                              headers={"content-type": "application/json"})

    adapter = next(a for a in build_official_adapters(repository=SQLiteMarketRepository())
                   if a.adapter_id == "nyfed_facilities")
    async with httpx.AsyncClient(transport=httpx.MockTransport(publish)) as client:
        documents = await adapter.fetcher(client)
    assert requested == [NYFED_REPO_RESULTS_URI.format(count=120),
                         NYFED_REVERSE_REPO_RESULTS_URI.format(count=120)]
    assert [d.label for d in documents] == ["nyfed_repo_operations", "nyfed_reverse_repo_operations"]


@pytest.mark.asyncio
async def test_collected_on_rrp_reaches_the_atlas_with_allowed_rights_and_native_clock(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "nyfed-on-rrp.sqlite")
    # Monday 2026-11-02 06:00 UTC: Friday's results are the latest published.
    captured = datetime(2026, 11, 2, 6, tzinfo=UTC)
    adapter = next(a for a in build_official_adapters(
        repository=SQLiteMarketRepository(), clock=lambda: captured)
        if a.adapter_id == "nyfed_facilities")

    async def fetcher(client):
        return (repo_document(), reverse_document())

    adapter.fetcher = fetcher
    batch = await adapter.collect()
    rows = [row for row in batch.observations if row.instrument_id == "US.NYFED.ON_RRP_TAKEUP"]
    assert [(row.event_time.date().isoformat(), row.value) for row in rows] == [
        ("2026-10-26", Decimal("200")), ("2026-10-27", Decimal("226")),
        ("2026-10-28", Decimal("176")), ("2026-10-29", Decimal("353")),
        ("2026-10-30", Decimal("4375")),
    ]
    assert all(row.source == "nyfed_facilities" for row in rows)
    assert all(row.redistribution_status is RedistributionStatus.ALLOWED for row in rows)
    assert all(row.knowledge_time == captured for row in rows)
    assert batch.raw_capture.source_uri.startswith("bundle:")
    assert {row.instrument_id for row in batch.observations} == {
        "US.NYFED.SRF_TAKEUP", "US.NYFED.ON_RRP_TAKEUP"}

    pack = default_registry().get("US-USD")
    atlas = build_global_money_market_atlas([pack], {"US-USD": batch.observations}, as_of=captured)
    market = atlas["markets"][0]
    metric = next(m for m in market["metrics"] if m["id"] == "US.NYFED.ON_RRP_TAKEUP")
    assert metric["availability"] == "AVAILABLE"
    assert metric["redistribution_status"] == "allowed"
    assert metric["value"] == 4375.0 and metric["unit"] == "local_currency_millions"
    assert metric["asof"] == "2026-10-30"
    assert metric["status"] == "FRESH"
    assert metric["source"] == "nyfed_facilities"
    srf = next(m for m in market["metrics"] if m["id"] == "US.NYFED.SRF_TAKEUP")
    assert srf["value"] == 0.0 and srf["asof"] == "2026-10-30"
    assert "ON RRP ACCEPTED" not in market["known_gaps"]
