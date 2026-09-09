"""Full published distributions retain family identity, units and evidence clocks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from seiche import store
from seiche.domain.observation import (
    RATE_ROLES,
    CanonicalUnit,
    DayCountConvention,
    QualityState,
    RateCompounding,
    SemanticRole,
)
from seiche.markets.registry import default_registry
from seiche.repository import SQLiteMarketRepository
from seiche.sources.canonical import FetchedDocument
from seiche.sources.official import (
    build_official_adapters,
    parse_nyfed_rates,
    parse_nyfed_unsecured_rates,
)


def _row(benchmark: str, **changes) -> dict:
    return {
        "type": benchmark,
        "effectiveDate": "2026-09-04",
        "percentRate": "3.65",
        "percentPercentile1": "3.55",
        "percentPercentile25": "3.62",
        "percentPercentile75": "3.68",
        "percentPercentile99": "3.75",
        "volumeInBillions": "102.5",
        "revisionIndicator": "",
        **changes,
    }


def _document(rows: list[dict], *, unsecured: bool = False) -> FetchedDocument:
    category = "unsecured" if unsecured else "secured"
    return FetchedDocument(
        f"https://markets.newyorkfed.org/api/rates/{category}/all/search.json",
        "application/json",
        json.dumps({"refRates": rows}).encode(),
        f"nyfed_{category}_rates",
    )


def test_secured_distributions_preserve_family_and_every_source_field() -> None:
    rows = [
        _row("SOFR"),
        _row("TGCR", percentRate="3.63"),
        _row("BGCR", percentRate="3.64"),
    ]
    points = parse_nyfed_rates(_document(rows))
    assert points == parse_nyfed_rates(_document(list(reversed(rows))))
    assert len(points) == 18
    by_id = {point.instrument_id: point for point in points}
    assert by_id["US.NYFED.SOFR_MEDIAN"].raw_value == Decimal("3.65")
    assert by_id["US.NYFED.TGCR_MEDIAN"].raw_value == Decimal("3.63")
    assert by_id["US.NYFED.BGCR_MEDIAN"].raw_value == Decimal("3.64")
    for benchmark in ("SOFR", "TGCR", "BGCR"):
        for suffix, value in (
            ("P01", "3.55"),
            ("P25", "3.62"),
            ("P75", "3.68"),
            ("P99", "3.75"),
            ("VOLUME", "102.5"),
        ):
            point = by_id[f"US.NYFED.{benchmark}_{suffix}"]
            assert point.raw_value == Decimal(value)
            assert json.loads(point.row_evidence)["row"]["type"] == benchmark
        median = by_id[f"US.NYFED.{benchmark}_MEDIAN"]
        prefix = "nyfed:" if benchmark == "SOFR" else f"nyfed:{benchmark}:"
        assert median.revision_id.startswith(f"{prefix}percentRate:2026-09-04:")


@pytest.mark.parametrize("benchmark", ["SOFR", "TGCR", "BGCR"])
def test_secured_distribution_rejects_duplicate_field_date(benchmark: str) -> None:
    with pytest.raises(ValueError, match=f"duplicate {benchmark} percentRate"):
        parse_nyfed_rates(_document([_row(benchmark), _row(benchmark)]))


@pytest.mark.parametrize("benchmark", ["SOFR", "TGCR", "BGCR", "EFFR", "OBFR"])
def test_missing_percentiles_are_unknown_and_zero_volume_is_preserved(
    benchmark: str,
) -> None:
    unsecured = benchmark in {"EFFR", "OBFR"}
    parser = parse_nyfed_unsecured_rates if unsecured else parse_nyfed_rates
    row = _row(
        benchmark,
        percentPercentile1=None,
        percentPercentile25="NaN",
        percentPercentile75="N/A",
        percentPercentile99="Infinity",
        volumeInBillions=0,
    )
    points = parser(_document([row], unsecured=unsecured))
    assert [point.instrument_id for point in points] == [
        f"US.NYFED.{benchmark}_MEDIAN",
        f"US.NYFED.{benchmark}_VOLUME",
    ]
    assert points[1].raw_value == 0


@pytest.mark.parametrize("benchmark", ["SOFR", "TGCR", "BGCR", "EFFR", "OBFR"])
def test_distribution_pack_units_and_roles(benchmark: str) -> None:
    pack = default_registry().get("US-USD")
    for suffix in ("P01", "P25", "MEDIAN", "P75", "P99"):
        instrument = pack.instrument_map[f"US.NYFED.{benchmark}_{suffix}"]
        assert instrument.semantic_role is SemanticRole[f"RATE_{suffix}"]
        assert instrument.semantic_role in RATE_ROLES
        assert instrument.canonical_unit is CanonicalUnit.BASIS_POINTS
        assert instrument.value_multiplier == 100
        assert instrument.rate_compounding is RateCompounding.SIMPLE
        assert instrument.day_count is DayCountConvention.ACT_360
    volume = pack.instrument_map[f"US.NYFED.{benchmark}_VOLUME"]
    assert volume.semantic_role is (
        SemanticRole.UNSECURED_FUNDING_VOLUME
        if benchmark in {"EFFR", "OBFR"}
        else SemanticRole.REPO_VOLUME
    )
    assert volume.semantic_role not in RATE_ROLES
    assert volume.canonical_unit is CanonicalUnit.LOCAL_CURRENCY_MILLIONS
    assert volume.value_multiplier == 1000
    assert volume.rate_compounding is None
    assert volume.day_count is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_id", "benchmarks", "publication_hour"),
    [
        ("nyfed_rates", ("SOFR", "TGCR", "BGCR"), 12),
        ("nyfed_unsecured_rates", ("EFFR", "OBFR"), 13),
    ],
)
async def test_collection_preserves_raw_revision_and_holiday_publication_clocks(
    tmp_path, monkeypatch, adapter_id, benchmarks, publication_hour
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / f"{adapter_id}.sqlite")
    repository = SQLiteMarketRepository()
    captured_at = datetime(2026, 9, 8, 14, tzinfo=UTC)
    document = _document(
        [_row(benchmark) for benchmark in benchmarks],
        unsecured=adapter_id == "nyfed_unsecured_rates",
    )

    async def collect():
        async def fetcher(_client):
            return (document,)

        adapter = next(
            adapter
            for adapter in build_official_adapters(
                repository=repository, clock=lambda: captured_at
            )
            if adapter.adapter_id == adapter_id
        )
        adapter.fetcher = fetcher
        batch = await adapter.collect()
        repository.save_observations(batch.observations)
        return batch

    first = await collect()
    assert len(first.observations) == len(benchmarks) * 6
    assert first.raw_capture.payload == document.payload
    assert first.raw_capture.source_uri == document.source_uri
    for observation in first.observations:
        assert observation.event_time == datetime(2026, 9, 4, tzinfo=UTC)
        # Monday September 7 is Labor Day: Friday publishes on Tuesday.
        assert observation.source_publication_time == datetime(
            2026, 9, 8, publication_hour, tzinfo=UTC
        )
        assert observation.knowledge_time == captured_at
        assert observation.quality is QualityState.VERIFIED
        assert observation.value == (
            Decimal("102500")
            if observation.instrument_id.endswith("_VOLUME")
            else {
                "P01": Decimal("355"),
                "P25": Decimal("362"),
                "MEDIAN": Decimal("365"),
                "P75": Decimal("368"),
                "P99": Decimal("375"),
            }[observation.instrument_id.rsplit("_", 1)[1]]
        )

    captured_at = datetime(2026, 9, 8, 15, tzinfo=UTC)
    repeated = await collect()
    assert [row.revision_id for row in repeated.observations] == [
        row.revision_id for row in first.observations
    ]
    assert {row.knowledge_time for row in repeated.observations} == {
        datetime(2026, 9, 8, 14, tzinfo=UTC)
    }

    # A previously unflagged revision to a newly exposed percentile is
    # retained as a new vintage, with capture-time knowledge rather than
    # retroactive availability at the original benchmark publication time.
    document = _document(
        [_row(benchmark, percentPercentile1="3.56") for benchmark in benchmarks],
        unsecured=adapter_id == "nyfed_unsecured_rates",
    )
    revised = await collect()
    old = {row.instrument_id: row for row in first.observations}
    for row in revised.observations:
        if row.instrument_id.endswith("_P01"):
            assert row.value == Decimal("356")
            assert row.revision_id != old[row.instrument_id].revision_id
            assert row.knowledge_time == captured_at
            assert row.quality is QualityState.REVISED
