"""India declares only curve tenors that a redistributable source publishes."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from seiche import india_funding
from seiche.domain.observation import Observation, QualityState, StalenessState, evidence_sha256
from seiche.markets.registry import default_registry

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


def test_india_daily_curve_declares_only_published_benchmarks_and_computes_its_spreads():
    india = REGISTRY.get("IN-INR")
    daily = sorted(int(i.instrument_id.rsplit("_", 1)[-1][:-1]) for i in india.instruments
                   if i.instrument_id.startswith("IN.RBI.GSEC_BENCHMARK_"))
    assert daily == [3, 5, 10, 15, 30]
    monthly = {i.instrument_id for i in india.instruments if i.instrument_id.startswith("IN.RBI.SGL_MONTHLY_")}
    assert "IN.RBI.SGL_MONTHLY_40Y" not in monthly
    values = {3: (711.67, 710.0), 5: (720.0, 718.0), 10: (742.43, 741.0), 15: (760.18, 759.0), 30: (784.72, 783.0)}
    bonds = {3: "6.20-GS-2029", 5: "6.36-GS-2031", 10: "6.94-GS-2036", 15: "7.06-GS-2041", 30: "7.63-GS-2056"}
    rows = [replace(row("IN-INR", f"IN.RBI.GSEC_BENCHMARK_{tenor}Y", day, value,
                        published=datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(hours=18, minutes=29)),
                    revision_id=f"bond:{bonds[tenor]}")
            for tenor, (today, before) in values.items()
            for day, value in (("2026-10-29", before), ("2026-10-30", today))]
    desk = india_funding.build(rows, now=datetime(2026, 10, 31, 6, tzinfo=UTC))
    assert [node["tenor_years"] for node in desk["curve"]["nodes"]] == [3, 5, 10, 15, 30]
    assert all(node["availability"] == "AVAILABLE" for node in desk["curve"]["nodes"])
    spreads = {spread["id"]: spread for spread in desk["spreads"]}
    assert list(spreads) == ["3s5s", "3s10s", "5s10s", "10s30s"]
    assert spreads["3s10s"]["value_bp"] == pytest.approx(30.76)
    assert spreads["3s5s"]["status"] == "available"
    assert not [m for m in desk["missing_series"] if m["id"].startswith("IN.RBI.GSEC_BENCHMARK_")]
