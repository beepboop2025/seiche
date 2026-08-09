from __future__ import annotations

from datetime import UTC, date, datetime

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
