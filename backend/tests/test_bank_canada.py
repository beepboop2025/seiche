import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import pytest
from seiche.domain.observation import QualityState
from seiche.markets.canada_cad import PACK, SERIES_MAP
from seiche.sources.bank_canada import parse_valet
from seiche.sources.canonical import (
    FetchedDocument,
    FunctionalCanonicalAdapter,
    PublicationTimePolicy,
)

FIXTURE = Path(__file__).parent / "fixtures/rates/boc-valet-2026-11-09-to-13.json"


def payload():
    return json.loads(FIXTURE.read_bytes())


def document(data):
    return FetchedDocument(
        "https://www.bankofcanada.ca/valet/observations/fixture/json",
        "application/json",
        json.dumps(data).encode(),
        "boc_valet",
    )


def test_publisher_fixture_keeps_missing_holiday_and_source_values():
    points = parse_valet(document(payload()))
    assert len(points) == 43
    corra = [p for p in points if p.instrument_id == "CA.BOC.CORRA"]
    assert all(p.event_time != date(2026, 11, 11) for p in corra)
    assert corra[-1].raw_value == Decimal("2.53")
    assert all(
        p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points
    )
    assert PACK.holiday_calendar.holiday_provider is None


@pytest.mark.parametrize(
    "change", ["unit", "identity", "frequency", "terms", "duplicate", "invalid"]
)
def test_changed_contracts_fail_closed(change):
    data = payload()
    if change == "unit":
        data["seriesDetail"]["AVG.INTWO"]["metadata"]["unit"] = "Basis points"
    if change == "identity":
        data["seriesDetail"].pop("AVG.INTWO")
    if change == "frequency":
        data["seriesDetail"]["AVG.INTWO"]["dateFormat"] = "monthly"
    if change == "terms":
        data["terms"]["url"] = "https://example.com/terms"
    if change == "duplicate":
        data["observations"].append(data["observations"][0])
    if change == "invalid":
        next(r for r in data["observations"] if "AVG.INTWO" in r)["AVG.INTWO"]["v"] = (
            "NaN"
        )
    with pytest.raises(ValueError):
        parse_valet(document(data))


def test_dedup_revision_unit_normalization_and_unknown_publication(
    tmp_path, monkeypatch
):
    from seiche import store
    from seiche.repository import SQLiteMarketRepository

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "canonical.sqlite")
    repository = SQLiteMarketRepository()
    data = payload()
    now = datetime(2026, 11, 14, 1, tzinfo=UTC)

    async def fetch(client):
        return (document(data),)

    adapter = FunctionalCanonicalAdapter(
        pack=PACK,
        adapter_id="boc_valet",
        source="boc_valet",
        fetcher=fetch,
        parser=parse_valet,
        repository=repository,
        clock=lambda: now,
        historical_backfill=True,
    )
    first = asyncio.run(adapter.collect())
    assert repository.save_observations(first.observations) == 43
    assert all(
        o.source_publication_time is None and o.quality is QualityState.PROVISIONAL
        for o in first.observations
    )
    assert (
        repository.save_observations(asyncio.run(adapter.collect()).observations) == 0
    )
    raw = next(r for r in data["observations"] if "CORRA_TOTAL_VOLUME" in r)
    volume = next(
        o
        for o in first.observations
        if o.instrument_id == "CA.BOC.CORRA_VOLUME"
        and o.event_time.date().isoformat() == raw["d"]
    )
    assert volume.value == Decimal(raw["CORRA_TOTAL_VOLUME"]["v"]) / Decimal(1000000)
    row = next(r for r in data["observations"] if "AVG.INTWO" in r)
    row["AVG.INTWO"]["v"] = str(Decimal(row["AVG.INTWO"]["v"]) + Decimal(".01"))
    now += timedelta(days=1)
    revised = asyncio.run(adapter.collect())
    assert sum(o.quality is QualityState.REVISED for o in revised.observations) == 1
    assert repository.save_observations(revised.observations) == 1
