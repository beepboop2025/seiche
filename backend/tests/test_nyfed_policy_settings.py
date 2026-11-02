"""The FOMC target range and the SRF rate, read direct from the NY Fed Markets API.

Context series only: no capability reads their roles, they never displace the
IORB policy anchor, the signed NY Fed history archive keeps its instrument set,
and the FRED target-range copy stays metadata-only.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from seiche.config import ALL_SERIES
from seiche.domain.observation import (
    RATE_ROLES,
    CanonicalUnit,
    ConnectorClassification,
    Observation,
    QualityState,
    RedistributionStatus,
    SemanticRole,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.atlas import _BENCHMARK_PRIORITY, _POLICY_PRIORITY, build_global_money_market_atlas
from seiche.markets.reading_rights import reading_restricted
from seiche.markets.registry import default_registry
from seiche.nyfed_history import INSTRUMENT_IDS as HISTORY_INSTRUMENT_IDS
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import (
    NYFED_REPO_RESULTS_URI,
    NYFED_TARGET_RANGE_INSTRUMENTS,
    parse_nyfed_facilities,
    parse_nyfed_target_range,
    parse_nyfed_unsecured_rates,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "us_usd"
UNSECURED_URI = "https://markets.newyorkfed.org/api/rates/unsecured/all/search.json"
REPO_URI = NYFED_REPO_RESULTS_URI.format(count=5)
LOWER, UPPER, SRF = "US.NYFED.FFR_TARGET_RANGE_LOWER", "US.NYFED.FFR_TARGET_RANGE_UPPER", "US.NYFED.SRF_RATE"
NEW_ROLES = {SemanticRole.POLICY_RANGE_LOWER, SemanticRole.POLICY_RANGE_UPPER, SemanticRole.STANDING_REPO_RATE}


def effr(day: str, lower=3.75, upper=4.0, **extra) -> dict:
    return {"effectiveDate": day, "type": "EFFR", "percentRate": 3.88, "percentPercentile1": 3.83,
            "percentPercentile25": 3.87, "percentPercentile75": 3.89, "percentPercentile99": 3.94,
            "targetRateFrom": lower, "targetRateTo": upper, "volumeInBillions": 117,
            "revisionIndicator": "", **extra}


def obfr(day: str) -> dict:
    return {"effectiveDate": day, "type": "OBFR", "percentRate": 3.87, "percentPercentile1": 3.81,
            "percentPercentile25": 3.86, "percentPercentile75": 3.89, "percentPercentile99": 3.95,
            "volumeInBillions": 201, "revisionIndicator": ""}


def unsecured(*rows: dict) -> FetchedDocument:
    return FetchedDocument(UNSECURED_URI, "application/json", json.dumps({"refRates": list(rows)}).encode(),
                           "nyfed_unsecured_rates")


def repo(*operations: dict) -> FetchedDocument:
    return FetchedDocument(REPO_URI, "application/json",
                           json.dumps({"repo": {"operations": list(operations)}}).encode(), "nyfed_repo_operations")


def srf(day: str, operation_id: str, *, method="Full Allotment", term="Overnight", rate=4.0, field=None) -> dict:
    field = field or ("percentOfferingRate" if method == "Full Allotment" else "minimumBidRate")
    return {"operationId": operation_id, "operationDate": day, "operationType": "Repo", "term": term,
            "operationMethod": method, "totalAmtAccepted": 0,
            "details": [{"securityType": kind, "amtSubmitted": 0, "amtAccepted": 0, field: rate}
                        for kind in ("Treasury", "Agency", "Mortgage-Backed")]}


# --------------------------------------------------------------------------
# Target range
# --------------------------------------------------------------------------

def test_target_range_reads_both_bounds_from_effr_rows_only():
    points = parse_nyfed_target_range(unsecured(effr("2026-10-29"), obfr("2026-10-29"),
                                                effr("2026-10-30", 3.5, 3.75)))
    assert [(p.instrument_id, p.event_time.isoformat(), p.raw_value) for p in points] == [
        (LOWER, "2026-10-29", Decimal("3.75")), (UPPER, "2026-10-29", Decimal("4.0")),
        (LOWER, "2026-10-30", Decimal("3.5")), (UPPER, "2026-10-30", Decimal("3.75")),
    ]
    assert all(p.publication_time_policy is PublicationTimePolicy.INFER for p in points)
    evidence = json.loads(points[1].row_evidence)
    assert evidence == {"label": "EFFR.targetRateTo",
                        "row": {"effectiveDate": "2026-10-29", "targetRateTo": 4.0, "type": "EFFR"}}
    assert points[1].revision_id.startswith("nyfed:EFFR:targetRateTo:2026-10-29:")
    assert [field for field, _ in NYFED_TARGET_RANGE_INSTRUMENTS] == ["targetRateFrom", "targetRateTo"]


def test_a_revised_rate_on_the_same_row_is_not_a_revised_range():
    first = parse_nyfed_target_range(unsecured(effr("2026-10-29")))
    revised = parse_nyfed_target_range(unsecured(effr("2026-10-29", percentRate=3.89, volumeInBillions=120,
                                                      revisionIndicator="R")))
    assert [p.revision_id for p in first] == [p.revision_id for p in revised]
    changed = parse_nyfed_target_range(unsecured(effr("2026-10-29", 3.5, 3.75)))
    assert [p.revision_id for p in changed] != [p.revision_id for p in first]


def test_a_row_without_a_reported_range_carries_none():
    row = effr("2026-10-29")
    del row["targetRateFrom"], row["targetRateTo"]
    assert parse_nyfed_target_range(unsecured(row, obfr("2026-10-29"))) == ()
    # The distribution parser is unaffected by the missing range.
    assert parse_nyfed_unsecured_rates(unsecured(row))


@pytest.mark.parametrize("rows", [
    [effr("2026-10-29", lower=None)],
    [effr("2026-10-29", upper="")],
    [effr("2026-10-29", 4.0, 3.75)],
    [effr("2026-10-29", 1.0, 2.25)],
    [effr("2026-10-29"), effr("2026-10-29")],
], ids=["lower missing", "upper blank", "inverted", "wider than 100 bp", "duplicate day"])
def test_an_incomplete_inverted_wide_or_repeated_range_fails_closed(rows):
    with pytest.raises(ValueError):
        parse_nyfed_target_range(unsecured(*rows))


def test_the_signed_history_archive_keeps_its_instrument_set():
    document = unsecured(effr("2026-10-29"), obfr("2026-10-29"))
    archive = parse_nyfed_unsecured_rates(document, publication_time_policy=PublicationTimePolicy.UNKNOWN)
    assert {p.instrument_id for p in archive} <= HISTORY_INSTRUMENT_IDS
    assert not {LOWER, UPPER, SRF} & HISTORY_INSTRUMENT_IDS


# --------------------------------------------------------------------------
# SRF rate
# --------------------------------------------------------------------------

def test_srf_rate_is_the_one_rate_every_overnight_operation_states():
    points = parse_nyfed_facilities(repo(
        srf("2026-10-30", "RP 103026 25"), srf("2026-10-30", "RP 103026 26"),
        # Multiple-price auctions state a minimum bid rate instead.
        srf("2025-11-03", "RP 110325 1", method="Multiple Price", rate=4.0),
        srf("2025-11-03", "RP 110325 2", method="Multiple Price", rate=4.0),
        # A term operation is not the SRF; alone it gives no SRF rate.
        srf("2026-10-20", "RP 102026 9", method="Multiple Price", term="Term", rate=3.9),
        # Disagreeing operations, an unstated rate or an unknown method withhold the day.
        srf("2026-10-21", "RP 102126 1"), srf("2026-10-21", "RP 102126 2", rate=3.75),
        srf("2026-10-22", "RP 102226 1"), srf("2026-10-22", "RP 102226 2", field="percentAwardRate"),
        srf("2026-10-23", "RP 102326 1", method="Fixed Rate"),
        # Repo operations before the FOMC established the SRF are not the SRF.
        srf("2021-07-28", "RP 072821 1", method="Multiple Price", rate=0.25),
    ))
    rates = [(p.event_time.isoformat(), p.raw_value) for p in points if p.instrument_id == SRF]
    assert rates == [("2025-11-03", Decimal("4.0")), ("2026-10-30", Decimal("4.0"))]
    evidence = json.loads(next(p for p in points if p.instrument_id == SRF and p.event_time == date(2026, 10, 30))
                          .row_evidence)
    assert evidence["label"] == "repo.overnight.srf_rate"
    assert [item["operationId"] for item in evidence["row"]["operations"]] == ["RP 103026 25", "RP 103026 26"]
    assert {item["field"] for item in evidence["row"]["operations"]} == {"percentOfferingRate"}


def test_srf_rate_out_of_bounds_fails_closed():
    with pytest.raises(ValueError, match="out of bounds"):
        parse_nyfed_facilities(repo(srf("2026-10-30", "RP 1", rate=75)))


def test_the_published_fixture_gives_the_srf_rate_beside_take_up():
    payload = (FIXTURES / "nyfed-repo-results-last5.json").read_bytes()
    points = parse_nyfed_facilities(FetchedDocument(REPO_URI, "application/json", payload, "nyfed_repo_operations"))
    rates = {p.event_time: p.raw_value for p in points if p.instrument_id == SRF}
    days = {p.event_time for p in points if p.instrument_id == "US.NYFED.SRF_TAKEUP"}
    assert rates and set(rates) <= days
    assert set(rates.values()) == {Decimal("4.0")}


# --------------------------------------------------------------------------
# Pack, registry and serving: context only.
# --------------------------------------------------------------------------

def test_the_pack_declares_three_context_instruments_no_engine_reads():
    registry = default_registry()
    pack = registry.get("US-USD")
    spec = pack.instrument_map
    assert (spec[LOWER].source_adapter_id, spec[LOWER].semantic_role) == (
        "nyfed_unsecured_rates", SemanticRole.POLICY_RANGE_LOWER)
    assert (spec[UPPER].source_adapter_id, spec[UPPER].semantic_role) == (
        "nyfed_unsecured_rates", SemanticRole.POLICY_RANGE_UPPER)
    assert (spec[SRF].source_adapter_id, spec[SRF].semantic_role) == (
        "nyfed_facilities", SemanticRole.STANDING_REPO_RATE)
    for instrument_id in (LOWER, UPPER, SRF):
        assert spec[instrument_id].semantic_role in RATE_ROLES
        assert spec[instrument_id].canonical_unit is CanonicalUnit.BASIS_POINTS
        assert spec[instrument_id].normalize(Decimal("4.0")) == Decimal("400")
    assert NEW_ROLES.isdisjoint(_POLICY_PRIORITY) and NEW_ROLES.isdisjoint(_BENCHMARK_PRIORITY)
    for each in registry.list():
        for capability in each.capabilities:
            assert NEW_ROLES.isdisjoint(capability.required_roles)
        for event in each.events:
            assert NEW_ROLES.isdisjoint(event.affected_roles)
    assert not {spec[i].mnemonic for i in (LOWER, UPPER, SRF)} & set(ALL_SERIES)
    assert reading_restricted(pack) is True
    # The FRED target-range copy is unchanged and withheld.
    assert spec["US.FED.POLICY_CEILING"].source_adapter_id == "fred_daily"
    assert pack.adapter_map["fred_daily"].redistribution_status is RedistributionStatus.METADATA_ONLY


def test_the_registry_record_names_the_new_series():
    ledger = json.loads((ROOT / "docs" / "data-source-expansion.json").read_text())
    record = next(item for item in ledger["candidates"] if item["id"] == "nyfed_markets_api")
    assert {"POLICY_RANGE_LOWER", "POLICY_RANGE_UPPER", "STANDING_REPO_RATE"} <= set(record["semantic_roles"])
    for text in (LOWER, UPPER, SRF, "targetRateFrom", "targetRateTo", "percentOfferingRate", "minimumBidRate",
                 "DFEDTARL", "DFEDTARU"):
        assert text in record["blocker"]
    assert record["rights_evidence_url"] == "https://www.newyorkfed.org/privacy/termsofuse.html"


def _row(instrument_id: str, day: date, percent: str, *, published: datetime | None, captured: datetime) -> Observation:
    pack = default_registry().get("US-USD")
    spec = pack.instrument_map[instrument_id]
    adapter = pack.adapter_map[spec.source_adapter_id]
    event = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return Observation(
        market_id="US-USD", monetary_area_id="US", jurisdiction_codes=("US",), currency="USD",
        instrument_id=instrument_id, semantic_role=spec.semantic_role, value=spec.normalize(Decimal(percent)),
        canonical_unit=spec.canonical_unit, rate_compounding=spec.rate_compounding, day_count=spec.day_count,
        event_time=event, knowledge_time=captured, source_publication_time=published,
        revision_id=f"{instrument_id}:{day.isoformat()}", source=spec.source_adapter_id,
        evidence_hash=evidence_sha256(f"{instrument_id}:{day.isoformat()}:{percent}"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=adapter.redistribution_status,
        quality=QualityState.VERIFIED if published is not None else QualityState.ESTIMATED,
        staleness=StalenessState.FRESH,
    )


def test_fresh_policy_settings_never_displace_the_iorb_anchor():
    captured = datetime(2026, 10, 30, 18, tzinfo=UTC)
    days = [date(2026, 10, d) for d in (26, 27, 28, 29)]
    rows = []
    for day in days:
        nine = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=UTC) + timedelta(hours=13)
        rows += [
            _row("US.NYFED.SOFR_MEDIAN", day, "3.94", published=nine, captured=captured),
            _row("US.FED.IORB_DIRECT", day, "3.90", published=None, captured=captured),
            _row(LOWER, day, "3.75", published=nine, captured=captured),
            _row(UPPER, day, "4.0", published=nine, captured=captured),
            _row(SRF, day, "4.0", published=nine, captured=captured),
        ]
    atlas = build_global_money_market_atlas([default_registry().get("US-USD")], {"US-USD": rows}, as_of=captured)
    market = atlas["markets"][0]
    # IORB's publication clock is unknown, and these settings are fresh, yet the
    # anchor and the SOFR spread stay on IORB.
    assert market["policy_anchor"]["id"] == "US.FED.IORB_DIRECT"
    assert market["policy_relative_spread"]["id"] == "US.NYFED.SOFR_MEDIAN_MINUS_US.FED.IORB_DIRECT"
    metrics = {item["id"]: item for item in market["metrics"]}
    for instrument_id, value in ((LOWER, 3.75), (UPPER, 4.0), (SRF, 4.0)):
        metric = metrics[instrument_id]
        assert metric["availability"] == "AVAILABLE" and metric["value"] == value and metric["unit"] == "%"
        assert metric["redistribution_status"] == "allowed"
        assert metric["source_url"].startswith("https://")
