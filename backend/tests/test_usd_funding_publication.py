"""Regression cases from the September 2026 funding-review handoff."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from seiche.collectors import CollectorSupervisor
from seiche.domain.observation import (
    CanonicalUnit, Observation, QualityState, StalenessState,
)
from seiche.engines.money_market import refresh_for_evaluation
from seiche.markets.atlas import _metric, _publication_opportunity_clock
from seiche.markets.us_usd.pack import PACK
from seiche.sources.base import ObservationBatch
from seiche.sources.publication import treasury_dts_freshness


@pytest.mark.parametrize("now,expected,missed", [
    ("2026-09-28T19:59:59+00:00", "2026-09-24", 0),
    ("2026-09-28T20:00:00+00:00", "2026-09-25", 1),
    ("2026-09-29T20:00:00+00:00", "2026-09-28", 2),
    ("2026-09-27T20:00:00+00:00", "2026-09-24", 0),
])
def test_tga_weekend_and_exact_release_boundary(now, expected, missed):
    result = treasury_dts_freshness("2026-09-24", now=datetime.fromisoformat(now))
    assert result["publication_schedule"]["expected_observation_date"] == expected
    assert result["publication_schedule"]["missed_publication_opportunities"] == missed
    assert result["publication_schedule"]["actual_published_at"] is None
    assert result["freshness"] == ["fresh", "aging", "stale"][missed]


def test_tga_holiday_and_dst_publication_boundary():
    # Veterans Day is a federal holiday; Nov 10's statement is due Nov 12,
    # at 21:00 UTC after New York has moved back to standard time.
    before = treasury_dts_freshness("2026-11-09", now=datetime(2026, 11, 12, 20, 59, tzinfo=UTC))
    after = treasury_dts_freshness("2026-11-09", now=datetime(2026, 11, 12, 21, tzinfo=UTC))
    assert before["freshness"] == "fresh"
    assert after["freshness"] == "aging"
    assert after["publication_schedule"]["expected_observation_date"] == "2026-11-10"


@pytest.mark.asyncio
async def test_sparse_gcf_source_refetches_after_one_hour_without_filling_no_prints(
    tmp_path, monkeypatch,
):
    import httpx
    import pandas as pd
    from seiche import store
    from seiche.config import ALL_SERIES
    from seiche.sources import ofr
    from seiche.sources.base import Series

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "source.sqlite")
    spec = ALL_SERIES["GCF_RATE_OO"]
    store.save_series(Series(
        spec.mnemonic, spec.source, spec.remote_id, spec.label, spec.unit, spec.freq,
        (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        pd.Series([3.91], index=pd.to_datetime(["2026-09-17"])),
    ))
    requests = []

    def publish(request):
        requests.append(request)
        return httpx.Response(200, json=[
            ["2026-09-17", 3.91], ["2026-09-18", None], ["2026-09-25", 3.94],
        ])

    async with httpx.AsyncClient(transport=httpx.MockTransport(publish)) as client:
        result = await ofr.fetch_series(client, spec)
    assert len(requests) == 1
    assert result.points.loc["2026-09-25"] == 3.94
    assert pd.isna(result.points.loc["2026-09-18"])
    assert result.freq == "D"


@pytest.mark.asyncio
@pytest.mark.parametrize("mnemonic", ["SOFR", "EFFR", "IORB", "RRPONTSYD"])
async def test_legacy_funding_cache_rechecks_hourly_and_preserves_prior_vintage(
    tmp_path, monkeypatch, mnemonic,
):
    import httpx
    import pandas as pd
    from seiche import store
    from seiche.config import ALL_SERIES
    from seiche.sources import fred
    from seiche.sources.base import Series

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "funding.sqlite")
    spec = ALL_SERIES[mnemonic]
    previous_capture = (datetime.now(UTC) - timedelta(minutes=61)).isoformat()
    store.save_series(Series(
        spec.mnemonic, spec.source, spec.remote_id, spec.label, spec.unit, spec.freq,
        previous_capture,
        pd.Series([3.9], index=pd.to_datetime(["2026-09-25"])),
    ))
    requests = []

    def publish(request):
        requests.append(request)
        return httpx.Response(200, text=(
            f"observation_date,{spec.remote_id}\n"
            "2026-09-25,3.9\n2026-09-28,3.91\n"
        ))

    async with httpx.AsyncClient(transport=httpx.MockTransport(publish)) as client:
        result = await fred.fetch_series(client, spec)
        cached = await fred.fetch_series(client, spec)

    assert len(requests) == 1  # The next five-minute sweep still uses its cache.
    assert result.asof == cached.asof == "2026-09-28"
    assert result.points.loc["2026-09-28"] == 3.91
    assert result.freq == "D"  # Acquisition frequency is not publication frequency.
    with store._conn() as conn:
        prior = conn.execute(
            "SELECT value FROM observation_vintages "
            "WHERE mnemonic=? AND obs_date=? AND knowledge_time=?",
            (mnemonic, "2026-09-25", store._canonical_utc(previous_capture)),
        ).fetchone()
    assert prior == (3.9,)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["secured_rates", "srf_ops"])
async def test_legacy_nyfed_funding_cache_rechecks_after_one_hour(
    tmp_path, monkeypatch, kind,
):
    import httpx
    from seiche import store
    from seiche.sources import nyfed

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "nyfed.sqlite")
    key = "nyfed_" + kind
    old_clock = (datetime.now(UTC) - timedelta(minutes=61)).isoformat()
    if kind == "secured_rates":
        old = {"fetched_at": old_clock, "refRates": [{
            "effectiveDate": "2026-09-25", "type": "SOFR", "percentRate": 3.9,
        }]}
        response = {"refRates": [{
            "effectiveDate": "2026-09-28", "type": "SOFR", "percentRate": 3.91,
        }]}
    else:
        old = {"fetched_at": old_clock, "ops": [{
            "date": "2026-09-25", "accepted": 0.0, "submitted": 0.0,
        }]}
        response = {"repo": {"operations": [{
            "operationDate": "2026-09-28", "totalAmtAccepted": 1000000,
            "totalAmtSubmitted": 1000000,
        }]}}
    store.save_blob(key, old)
    with store._conn() as conn:
        conn.execute("UPDATE blobs SET fetched_at=? WHERE key=?", (old_clock, key))
    requests = []

    def publish(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    fetch = getattr(nyfed, "fetch_" + kind)
    async with httpx.AsyncClient(transport=httpx.MockTransport(publish)) as client:
        result = await fetch(client)
        await fetch(client)
    assert len(requests) == 1
    frame = result["frames"]["SOFR"] if kind == "secured_rates" else result["daily"]
    assert frame.index[-1].date().isoformat() == "2026-09-28"


@pytest.mark.asyncio
async def test_unavailable_funding_refresh_retains_original_capture_clock(
    tmp_path, monkeypatch,
):
    import httpx
    import pandas as pd
    from seiche import store
    from seiche.config import ALL_SERIES
    from seiche.sources import fred
    from seiche.sources.base import Series

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "outage.sqlite")
    spec = ALL_SERIES["SOFR"]
    old_clock = (datetime.now(UTC) - timedelta(minutes=61)).isoformat()
    store.save_series(Series(
        spec.mnemonic, spec.source, spec.remote_id, spec.label, spec.unit, spec.freq,
        old_clock, pd.Series([3.9], index=pd.to_datetime(["2026-09-25"])),
    ))
    requests = []

    def unavailable(request):
        requests.append(request)
        return httpx.Response(503)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(fred.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        result = await fred.fetch_series(client, spec)
    assert len(requests) == 4
    assert result.fetched_at == old_clock
    assert result.asof == "2026-09-25"
    assert not store.is_fresh("SOFR", spec.ttl_minutes)


def test_tga_read_time_refresh_retains_intraday_clock_and_observations():
    payload = {
        "schema": "seiche.money-market-desk.v1", "ok": True, "asof": "2026-09-25",
        "sections": [{"id": "liquidity_buffers", "metrics": [{
            "id": "liquidity.tga", "status": "available", "asof": "2026-09-24",
            "cadence": "daily", "value": 947.317, "unit": "$B",
        }]}],
        "source_metadata": [{"id": "fiscal_tga", "asof": "2026-09-24", "cadence": "daily"}],
    }
    before = refresh_for_evaluation(payload, evaluation_asof="2026-09-28T19:59:59Z")
    after = refresh_for_evaluation(payload, evaluation_asof="2026-09-28T20:00:00Z")
    a, b = [result["sections"][0]["metrics"][0] for result in (before, after)]
    assert (a["freshness"], b["freshness"]) == ("fresh", "aging")
    assert a["value"] == b["value"] == 947.317
    assert before["sources"][0]["freshness"] == "fresh"
    assert after["sources"][0]["freshness"] == "aging"


def test_nyfed_business_date_is_not_shifted_to_previous_new_york_day():
    spec = PACK.instrument_map["US.NYFED.SOFR_MEDIAN"]
    row = Observation(
        market_id=PACK.market_id, monetary_area_id="US", jurisdiction_codes=("US",),
        currency="USD", instrument_id=spec.instrument_id, semantic_role=spec.semantic_role,
        value=390, canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=spec.rate_compounding, day_count=spec.day_count,
        event_time=datetime(2026, 9, 25, tzinfo=UTC),
        source_publication_time=datetime(2026, 9, 28, 12, tzinfo=UTC),
        knowledge_time=datetime(2026, 9, 28, 12, 1, tzinfo=UTC),
        revision_id="sep25", source="nyfed_rates", evidence_hash="a" * 64,
        connector_classification=PACK.adapter_map["nyfed_rates"].classification,
        redistribution_status=PACK.adapter_map["nyfed_rates"].redistribution_status,
        quality=QualityState.VERIFIED, staleness=StalenessState.FRESH,
    )
    clock = lambda observed, now: _publication_opportunity_clock(
        observed, PACK.adapter_map["nyfed_rates"], PACK.settlement_calendar, now,
    )
    current = clock(row, datetime(2026, 9, 28, 19, tzinfo=UTC))
    assert current[:2] == (StalenessState.FRESH, 0)
    assert current[2] == datetime(2026, 9, 29, 12, tzinfo=UTC)
    previous = replace(row, event_time=datetime(2026, 9, 24, tzinfo=UTC))
    assert clock(previous, datetime(2026, 9, 28, 19, tzinfo=UTC))[:2] == (StalenessState.AGING, 1)

    # The FRED transport mixes same-day policy rates and next-day benchmarks.
    # Its stored publication bound is preserved; currentness uses the
    # benchmark administrator's own expected release schedule.
    fred_spec = PACK.instrument_map["US.NYFED.SOFR"]
    fred = replace(
        row, instrument_id=fred_spec.instrument_id, semantic_role=fred_spec.semantic_role,
        source="fred", source_publication_time=datetime(2026, 9, 26, 3, 59, 59, tzinfo=UTC),
    )
    friday = _metric(PACK, fred_spec, [fred], cutoff=datetime(2026, 9, 28, 19, tzinfo=UTC))
    assert friday["status"] == "FRESH" and friday["missed_publication_opportunities"] == 0
    assert friday["published_at"] == fred.source_publication_time.isoformat()
    thursday = replace(fred, event_time=datetime(2026, 9, 24, tzinfo=UTC))
    before = _metric(PACK, fred_spec, [thursday], cutoff=datetime(2026, 9, 28, 11, 59, tzinfo=UTC))
    after = _metric(PACK, fred_spec, [thursday], cutoff=datetime(2026, 9, 28, 12, tzinfo=UTC))
    assert (before["status"], after["status"]) == ("FRESH", "AGING")


@pytest.mark.asyncio
async def test_hourly_collection_recurs_without_changing_daily_source_cadence():
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)
    class Adapter:
        market_id, adapter_id, calls = "US-USD", "nyfed_rates", 0
        async def collect(self):
            self.calls += 1
            return ObservationBatch(self.market_id, self.adapter_id, now, ())
    adapter = Adapter()
    supervisor = CollectorSupervisor(observation_writer=lambda _: 0)
    supervisor.register(adapter)
    first = await supervisor.run_due(now=now)
    assert first[0].next_due == (now + timedelta(hours=1)).isoformat()
    assert not await supervisor.run_due(now=now + timedelta(minutes=59))
    assert len(await supervisor.run_due(now=now + timedelta(hours=1))) == 1
    assert adapter.calls == 2
    assert PACK.adapter_map["nyfed_rates"].expected_cadence == "P1D"


@pytest.mark.asyncio
async def test_old_daily_deadline_is_bounded_but_active_circuit_is_preserved():
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)
    class Adapter:
        market_id, adapter_id = "US-USD", "nyfed_rates"
        async def collect(self):
            return ObservationBatch(self.market_id, self.adapter_id, now, ())
    for circuit in (None, now + timedelta(hours=3)):
        supervisor = CollectorSupervisor(observation_writer=lambda _: 0, restored_runs=[{
            "market_id": "US-USD", "adapter_id": "nyfed_rates",
            "next_due": (now + timedelta(hours=12)).isoformat(),
            "circuit_open_until": circuit.isoformat() if circuit else None,
        }])
        supervisor.register(Adapter())
        assert not await supervisor.run_due(now=now)
        result = await supervisor.run_due(now=now + timedelta(hours=1))
        assert bool(result) is (circuit is None)
