from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from seiche import store
from seiche.domain.observation import QualityState
from seiche.markets.registry import default_registry
from seiche.repository import SQLiteMarketRepository
from seiche.sources.canonical import (
    FetchedDocument,
    FunctionalCanonicalAdapter,
    ParsedPoint,
)
from seiche.sources.official import (
    PRODUCTION_ADAPTER_KEYS,
    bounded_date_windows,
    build_official_adapters,
    parse_nyfed_rates,
)


def test_bounded_date_windows_are_complete_non_overlapping_and_inclusive() -> None:
    windows = bounded_date_windows(
        date(2024, 1, 1),
        date(2026, 1, 5),
        maximum_days=360,
    )

    assert windows[0][0] == date(2024, 1, 1)
    assert windows[-1][1] == date(2026, 1, 5)
    assert all((end - start).days < 360 for start, end in windows)
    assert all(
        current[1].toordinal() + 1 == following[0].toordinal()
        for current, following in zip(windows, windows[1:])
    )


def test_nyfed_sofr_median_uses_percent_rate_and_binds_field_date_lineage() -> None:
    document = FetchedDocument(
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
        "application/json",
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-07",'
        b'"percentRate":5.31,"percentPercentile25":4.87,'
        b'"percentPercentile99":5.45,"volumeInBillions":2100}]}',
        "nyfed_secured_rates",
    )

    points = parse_nyfed_rates(document)
    by_instrument = {point.instrument_id: point for point in points}
    median = by_instrument["US.NYFED.SOFR_MEDIAN"]

    assert median.raw_value == Decimal("5.31")
    assert median.raw_value != Decimal("4.87")
    assert re.fullmatch(
        r"nyfed:percentRate:2026-08-07:unrevised-[0-9a-f]{16}",
        str(median.revision_id),
    )
    assert re.fullmatch(
        r"nyfed:percentPercentile99:2026-08-07:unrevised-[0-9a-f]{16}",
        str(by_instrument["US.NYFED.SOFR_P99"].revision_id),
    )
    assert re.fullmatch(
        r"nyfed:volumeInBillions:2026-08-07:unrevised-[0-9a-f]{16}",
        str(by_instrument["US.NYFED.SOFR_VOLUME"].revision_id),
    )


def test_nyfed_revision_indicator_cannot_replace_field_and_event_lineage() -> None:
    document = FetchedDocument(
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
        "application/json",
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-06",'
        b'"percentRate":5.3,"percentPercentile25":5.2,'
        b'"percentPercentile99":5.4,"volumeInBillions":2000,'
        b'"revisionIndicator":"R1"}]}',
        "nyfed_secured_rates",
    )

    points = parse_nyfed_rates(document)

    assert len({point.revision_id for point in points}) == 3
    assert all(
        re.fullmatch(
            r"nyfed:(?:percentRate|percentPercentile99|volumeInBillions):"
            r"2026-08-06:R1-[0-9a-f]{16}",
            str(point.revision_id),
        )
        for point in points
    )


def test_nyfed_changed_unflagged_row_gets_distinct_revision_lineage() -> None:
    first = FetchedDocument(
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
        "application/json",
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-06",'
        b'"percentRate":5.30,"percentPercentile99":5.40,'
        b'"volumeInBillions":2000}]}',
        "nyfed_secured_rates",
    )
    changed = FetchedDocument(
        first.source_uri,
        first.media_type,
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-06",'
        b'"percentRate":5.31,"percentPercentile99":5.40,'
        b'"volumeInBillions":2000}]}',
        first.label,
    )

    first_median = next(
        point
        for point in parse_nyfed_rates(first)
        if point.instrument_id == "US.NYFED.SOFR_MEDIAN"
    )
    changed_median = next(
        point
        for point in parse_nyfed_rates(changed)
        if point.instrument_id == "US.NYFED.SOFR_MEDIAN"
    )

    assert first_median.revision_id != changed_median.revision_id
    assert str(first_median.revision_id).startswith(
        "nyfed:percentRate:2026-08-06:unrevised-"
    )


def test_every_production_adapter_is_pack_declared_without_network_io(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "adapters.sqlite")
    adapters = build_official_adapters(
        repository=SQLiteMarketRepository(),
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )
    keys = {(item.market_id, item.adapter_id) for item in adapters}

    assert keys == PRODUCTION_ADAPTER_KEYS
    registry = default_registry()
    assert all(
        adapter.adapter_id in registry.get(adapter.market_id).adapter_map
        for adapter in adapters
    )


@pytest.mark.asyncio
async def test_historical_current_vintage_is_not_leaked_into_the_past(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "backfill.sqlite")
    capture = datetime(2026, 8, 9, 12, tzinfo=UTC)
    document = FetchedDocument(
        "https://example.invalid/official.csv",
        "text/csv",
        b"DATE,VALUE\n2020-01-02,1.50\n",
        "US.NYFED.SOFR",
    )

    async def fetcher(_client):
        return (document,)

    def parser(_document):
        return (
            ParsedPoint(
                "US.NYFED.SOFR",
                date(2020, 1, 2),
                "1.50",
                b"2020-01-02,1.50",
            ),
        )

    adapter = FunctionalCanonicalAdapter(
        pack=default_registry().get("US-USD"),
        adapter_id="fred_daily",
        source="official-test",
        fetcher=fetcher,
        parser=parser,
        repository=SQLiteMarketRepository(),
        clock=lambda: capture,
        historical_backfill=True,
    )
    batch = await adapter.collect()
    observation = batch.observations[0]

    assert observation.source_publication_time < capture
    assert observation.knowledge_time == capture
    assert observation.quality is QualityState.PROVISIONAL
    assert observation.value == 150


@pytest.mark.asyncio
async def test_changed_source_content_can_revert_without_revision_id_collision(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "revision-reversion.sqlite")
    repository = SQLiteMarketRepository()
    base = (
        b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-08-06",'
        b'"percentRate":5.30,"percentPercentile99":5.40,'
        b'"volumeInBillions":2000}]}'
    )
    changed = base.replace(b'"percentRate":5.30', b'"percentRate":5.31')

    async def collect(payload: bytes, captured_at: datetime):
        document = FetchedDocument(
            "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
            "application/json",
            payload,
            "nyfed_secured_rates",
        )

        async def fetcher(_client):
            return (document,)

        adapter = FunctionalCanonicalAdapter(
            pack=default_registry().get("US-USD"),
            adapter_id="nyfed_rates",
            source="nyfed_rates",
            fetcher=fetcher,
            parser=parse_nyfed_rates,
            repository=repository,
            clock=lambda: captured_at,
        )
        batch = await adapter.collect()
        repository.save_observations(batch.observations)
        return next(
            row
            for row in batch.observations
            if row.instrument_id == "US.NYFED.SOFR_MEDIAN"
        )

    first = await collect(base, datetime(2026, 8, 10, 10, tzinfo=UTC))
    second = await collect(changed, datetime(2026, 8, 10, 11, tzinfo=UTC))
    reverted = await collect(base, datetime(2026, 8, 10, 12, tzinfo=UTC))

    assert len({first.revision_id, second.revision_id, reverted.revision_id}) == 3
    assert "@capture-20260810T110000Z" in second.revision_id
    assert "@capture-20260810T120000Z" in reverted.revision_id


@pytest.mark.asyncio
async def test_identical_content_gains_explicit_field_lineage_once(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "lineage-upgrade.sqlite")
    repository = SQLiteMarketRepository()
    document = FetchedDocument(
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json",
        "application/json",
        b"{}",
        "nyfed_secured_rates",
    )
    evidence = b"same canonical P99 source row"
    explicit_revision = "nyfed:percentPercentile99:2026-08-06:unrevised-content"

    async def collect(*, captured_at: datetime, revision_id: str | None):
        async def fetcher(_client):
            return (document,)

        def parser(_document):
            return (
                ParsedPoint(
                    "US.NYFED.SOFR_P99",
                    date(2026, 8, 6),
                    Decimal("5.40"),
                    evidence,
                    revision_id=revision_id,
                ),
            )

        adapter = FunctionalCanonicalAdapter(
            pack=default_registry().get("US-USD"),
            adapter_id="nyfed_rates",
            source="nyfed_rates",
            fetcher=fetcher,
            parser=parser,
            repository=repository,
            clock=lambda: captured_at,
        )
        batch = await adapter.collect()
        repository.save_observations(batch.observations)
        return batch.observations[0]

    legacy = await collect(
        captured_at=datetime(2026, 8, 10, 10, tzinfo=UTC),
        revision_id=None,
    )
    upgraded = await collect(
        captured_at=datetime(2026, 8, 10, 11, tzinfo=UTC),
        revision_id=explicit_revision,
    )
    repeated = await collect(
        captured_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
        revision_id=explicit_revision,
    )

    assert legacy.revision_id.startswith("sha256:")
    assert upgraded.revision_id.startswith(f"{explicit_revision}@capture-")
    assert upgraded.evidence_hash == legacy.evidence_hash
    assert repeated.revision_id == upgraded.revision_id
    assert repeated.knowledge_time == upgraded.knowledge_time
