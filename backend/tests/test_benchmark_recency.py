"""Benchmark choice when no candidate has a known-current publication clock."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from seiche.domain.observation import Observation, QualityState, SemanticRole, StalenessState, evidence_sha256
from seiche.markets.atlas import _first_metric, build_global_money_market_atlas
from seiche.markets.registry import default_registry

AS_OF = datetime(2026, 11, 2, 8, 18, 24, tzinfo=UTC)
REGISTRY = default_registry()


def row(market_id, instrument_id, day, value, *, published=None):
    pack = REGISTRY.get(market_id)
    spec = pack.instrument_map[instrument_id]
    adapter = pack.adapter_map[spec.source_adapter_id]
    event = datetime.fromisoformat(day).replace(tzinfo=UTC)
    published = event + timedelta(hours=10) if published is None else published
    return Observation(
        market_id=market_id, monetary_area_id=pack.monetary_area_id,
        jurisdiction_codes=pack.jurisdiction_codes, currency=pack.currency,
        instrument_id=instrument_id, semantic_role=spec.semantic_role, value=Decimal(str(value)),
        canonical_unit=spec.canonical_unit, rate_compounding=spec.rate_compounding, day_count=spec.day_count,
        event_time=event, source_publication_time=published or None,
        knowledge_time=max(event + timedelta(hours=11), (published or event) + timedelta(minutes=5)),
        revision_id=f"{instrument_id}:{day}", source=adapter.adapter_id,
        evidence_hash=evidence_sha256(f"{instrument_id}:{day}:{value}"),
        connector_classification=adapter.classification, redistribution_status=adapter.redistribution_status,
        quality=QualityState.ESTIMATED, staleness=StalenessState.FRESH,
    )


def test_taiwan_benchmark_is_the_newer_daily_print_and_unknown_is_not_called_stale():
    # Live: every TW row lacks a publication time, so the monthly 2026-09-30
    # aggregate was chosen over the daily 2026-10-30 call rate.
    rows = [row("TW-TWD", "TW.CBC.OVERNIGHT_CALL_MONTHLY", "2026-09-30", 81.7, published=False),
            row("TW-TWD", "TW.CBC.DISCOUNT_MONTH_END", "2026-09-30", 200, published=False),
            row("TW-TWD", "TW.CBC.OVERNIGHT_CALL", "2026-10-30", 81.2, published=False),
            row("TW-TWD", "TW.CBC.DISCOUNT_DECISION", "2024-03-22", 200, published=False)]
    market, = build_global_money_market_atlas(
        (REGISTRY.get("TW-TWD"),), {"TW-TWD": rows}, as_of=AS_OF)["markets"]
    by_id = {metric["id"]: metric for metric in market["metrics"]}
    assert market["benchmark"]["id"] == "TW.CBC.OVERNIGHT_CALL"
    assert (market["benchmark"]["value"], market["benchmark"]["asof"]) == (0.812, "2026-10-30")
    assert market["benchmark"]["status"] == "UNKNOWN"
    assert "publication time is unknown" in market["plain_language"]
    assert "stale historical" not in market["plain_language"]
    assert by_id["TW.CBC.DISCOUNT_DECISION"]["cadence"] == "EVENT"
    assert by_id["TW.CBC.DISCOUNT_DECISION"]["status"] == "UNKNOWN"
    # A known-current candidate still outranks a newer unknown one.
    fresh = {"semantic_role": "UNSECURED_OVERNIGHT", "availability": "AVAILABLE", "status": "AGING", "asof": "2026-10-01"}
    unknown = {"semantic_role": "UNSECURED_OVERNIGHT", "availability": "AVAILABLE", "status": "UNKNOWN", "asof": "2026-10-30"}
    assert _first_metric([unknown, fresh], (SemanticRole.UNSECURED_OVERNIGHT,)) is fresh
