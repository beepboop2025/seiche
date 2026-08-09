from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from seiche.domain.observation import SemanticRole
from seiche.kernel.engines import KernelStatus, MarketPanel, cross_basin_coupling
from seiche.markets.registry import default_registry
from seiche.sources.canonical import (
    FetchedDocument,
    FunctionalCanonicalAdapter,
    ParsedPoint,
)


class _EmptyRepository:
    def load_observations_as_of(self, *_args, **_kwargs):
        return []


class _SpyRepository(_EmptyRepository):
    def __init__(self) -> None:
        self.calls = []

    def load_observations_as_of(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return []


def _fx_pack(market_id: str, instrument_id: str, adapter_id: str):
    pack = default_registry().get(market_id)
    template = next(
        item for item in pack.instruments if item.source_adapter_id == adapter_id
    )
    fx_instrument = replace(
        template,
        instrument_id=instrument_id,
        mnemonic=f"{market_id}_TEST_FX_BASIS",
        semantic_role=SemanticRole.FX_SWAP_BASIS,
    )
    return replace(pack, instruments=(*pack.instruments, fx_instrument))


@pytest.mark.asyncio
async def test_date_only_rows_share_one_business_date_across_market_timezones() -> None:
    capture = datetime(2026, 5, 1, 12, tzinfo=UTC)
    configurations = (
        ("AU-AUD", "AU.TEST.FX_BASIS", "rba_cash"),
        ("IN-INR", "IN.TEST.FX_BASIS", "rbi_official"),
        ("US-USD", "US.TEST.FX_BASIS", "fred_daily"),
    )
    packs = tuple(_fx_pack(*configuration) for configuration in configurations)
    session_days: list[date] = []
    cursor = date(2026, 1, 5)
    while len(session_days) < 61:
        if all(pack.settlement_calendar.is_business_day(cursor) for pack in packs):
            session_days.append(cursor)
        cursor += timedelta(days=1)

    series_by_market = {}
    publications: dict[str, datetime] = {}
    for pack, (_, instrument_id, adapter_id) in zip(
        packs,
        configurations,
        strict=True,
    ):
        document = FetchedDocument(
            "https://example.invalid/fx.csv",
            "text/csv",
            b"DATE,VALUE\n",
            instrument_id,
        )

        async def fetcher(_client, item=document):
            return (item,)

        def parser(_document, emitted_instrument=instrument_id):
            level = 0
            points = []
            for offset, event_day in enumerate(session_days):
                level += offset % 5 + 1
                points.append(
                    ParsedPoint(
                        emitted_instrument,
                        event_day,
                        level,
                        f"{emitted_instrument}:{event_day}:{level}".encode(),
                    )
                )
            return tuple(points)

        batch = await FunctionalCanonicalAdapter(
            pack=pack,
            adapter_id=adapter_id,
            source=f"{pack.market_id.lower()}-test",
            fetcher=fetcher,
            parser=parser,
            repository=_EmptyRepository(),
            clock=lambda: capture,
        ).collect()
        expected_events = {
            datetime.combine(event_day, datetime.min.time(), tzinfo=UTC)
            for event_day in session_days
        }

        assert {item.event_time for item in batch.observations} == expected_events
        assert {item.knowledge_time for item in batch.observations} == {capture}
        publications[pack.market_id] = batch.observations[0].source_publication_time
        series = MarketPanel.from_observations(batch.observations).lookup(
            SemanticRole.FX_SWAP_BASIS
        ).series
        assert series is not None
        series_by_market[pack.market_id] = series

    # Publication clocks remain market-local and conservative even though the
    # date-only event key is shared across markets.
    assert publications == {
        "AU-AUD": datetime(2026, 1, 5, 22, 20, tzinfo=UTC),
        "IN-INR": datetime(2026, 1, 5, 18, 29, 59, tzinfo=UTC),
        "US-USD": datetime(2026, 1, 6, 4, 59, 59, tzinfo=UTC),
    }

    result = cross_basin_coupling(series_by_market)

    assert result.status is KernelStatus.READY
    assert result.value == 100.0
    assert result.event_cutoff == "2026-04-15T00:00:00+00:00"


@pytest.mark.asyncio
async def test_prior_vintage_lookup_is_bounded_to_emitted_source_rows() -> None:
    capture = datetime(2026, 5, 1, 12, tzinfo=UTC)
    repository = _SpyRepository()
    document = FetchedDocument(
        "https://example.invalid/rates.csv",
        "text/csv",
        b"DATE,IORB,EFFR\n",
        "fred",
    )

    async def fetcher(_client):
        return (document,)

    def parser(_document):
        return (
            ParsedPoint("US.FED.IORB", date(2026, 1, 7), 3.65, b"iorb:2026-01-07"),
            ParsedPoint("US.NYFED.EFFR", date(2026, 1, 5), 3.64, b"effr:2026-01-05"),
            ParsedPoint("US.NYFED.EFFR", date(2026, 1, 6), 3.63, b"effr:2026-01-06"),
        )

    await FunctionalCanonicalAdapter(
        pack=default_registry().get("US-USD"),
        adapter_id="fred_daily",
        source="fred-test",
        fetcher=fetcher,
        parser=parser,
        repository=repository,
        clock=lambda: capture,
    ).collect()

    assert repository.calls == [
        (
            ("US-USD", capture),
            {
                "event_time": capture,
                "event_time_from": datetime(2026, 1, 5, tzinfo=UTC),
                "instrument_ids": ("US.FED.IORB", "US.NYFED.EFFR"),
                "sources": ("fred-test",),
            },
        )
    ]
