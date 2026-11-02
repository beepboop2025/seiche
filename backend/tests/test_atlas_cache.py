"""The money-market atlas cache changes how often the server rebuilds, never what it serves.

Every cached response here is compared byte for byte with the uncached
builder evaluated at the same instant.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from seiche import api, atlas_cache, store
from seiche.domain.observation import (
    Observation,
    QualityState,
    RedistributionStatus,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.atlas import _publication_opportunity_clock, freshness_transitions
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.repository import SQLiteMarketRepository

# Monday 2026-11-02: Friday's SOFR is due at 08:00 New York (13:00 UTC).
BEFORE_DUE = datetime(2026, 11, 2, 12, 59, 59, tzinfo=UTC)
AT_DUE = datetime(2026, 11, 2, 13, 0, 0, tzinfo=UTC)


def row(instrument_id, day, value, published, *, market_id="US-USD", revision="v1", knowledge=None):
    pack = default_registry().get(market_id)
    spec = pack.instrument_map[instrument_id]
    adapter = pack.adapter_map[spec.source_adapter_id]
    publication = datetime.fromisoformat(published)
    return Observation(
        market_id=pack.market_id, monetary_area_id=pack.monetary_area_id,
        jurisdiction_codes=pack.jurisdiction_codes, currency=pack.currency,
        instrument_id=instrument_id, semantic_role=spec.semantic_role,
        value=Decimal(value), canonical_unit=spec.canonical_unit,
        rate_compounding=spec.rate_compounding, day_count=spec.day_count,
        event_time=datetime.fromisoformat(day + "T00:00:00+00:00"),
        source_publication_time=publication,
        knowledge_time=knowledge or publication + timedelta(minutes=10),
        revision_id=f"cache-{instrument_id}-{day}-{revision}", source=spec.source_adapter_id,
        evidence_hash=evidence_sha256(f"{instrument_id}:{day}:{value}:{revision}"),
        connector_classification=adapter.classification,
        redistribution_status=adapter.redistribution_status,
        quality=QualityState.VERIFIED, staleness=StalenessState.FRESH,
    )


SOFR_1029 = row("US.NYFED.SOFR_MEDIAN", "2026-10-29", "394", "2026-10-30T12:00:00+00:00")
SOFR_1030 = row("US.NYFED.SOFR_MEDIAN", "2026-10-30", "396", "2026-11-02T13:00:00+00:00")
TGA_1029 = row("US.TREASURY.GOVERNMENT_CASH", "2026-10-29", "1015632", "2026-10-30T20:00:00+00:00")


def run(adapter_id, status, finished, *, fault=None, market_id="US-USD"):
    return {
        "market_id": market_id, "adapter_id": adapter_id, "status": status,
        "started_at": (finished - timedelta(minutes=1)).isoformat(),
        "finished_at": finished.isoformat(), "observations_written": 0, "attempts": 1,
        "next_due": (finished + timedelta(hours=1)).isoformat(), "fault": fault,
    }


@pytest.fixture
def atlas(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "atlas-cache.sqlite")
    monkeypatch.setattr(api, "_ATLAS_CACHE", atlas_cache.AtlasCache())
    moment = {"now": BEFORE_DUE}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment["now"] if tz is None else moment["now"].astimezone(tz)

    monkeypatch.setattr(api, "datetime", Clock)
    store.save_observations([SOFR_1029, TGA_1029])
    store.save_collector_run(run("nyfed_rates", "SUCCESS", BEFORE_DUE - timedelta(minutes=20)))
    client = TestClient(api.app)

    def get(**headers):
        return client.get("/api/v2/money-markets", headers={"Accept-Encoding": "identity", **headers})

    def uncached():
        return atlas_cache.encode(api.global_money_markets_v2(Response()))

    def at(when):
        moment["now"] = when

    return get, uncached, at


def us(body: bytes) -> dict:
    return next(m for m in json.loads(body)["markets"] if m["market_id"] == "US-USD")


def test_cached_atlas_is_byte_identical_to_an_uncached_build(atlas):
    get, uncached, _ = atlas
    first = get()
    assert first.status_code == 200
    assert first.headers["x-seiche-atlas-cache"] == "miss"
    assert first.content == uncached()
    second = get()
    assert second.headers["x-seiche-atlas-cache"] == "hit"
    assert second.content == first.content == uncached()
    assert second.headers["etag"] == first.headers["etag"]
    assert second.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=240"
    assert us(second.content)["benchmark"]["asof"] == "2026-10-29"
    assert us(second.content)["benchmark"]["status"] == "FRESH"


def test_a_new_row_or_vintage_is_never_hidden_by_the_cache(atlas):
    get, uncached, _ = atlas
    assert get().headers["x-seiche-atlas-cache"] == "miss"
    assert get().headers["x-seiche-atlas-cache"] == "hit"
    # A revised vintage of an already-cached date must rebuild before serving.
    store.save_observations([replace(SOFR_1029, value=Decimal("395"),
                                     knowledge_time=SOFR_1029.knowledge_time + timedelta(hours=1),
                                     revision_id="cache-revised")])
    revised = get()
    assert revised.headers["x-seiche-atlas-cache"] == "miss"
    assert us(revised.content)["benchmark"]["value"] == 3.95
    assert revised.content == uncached()


def test_collector_runs_are_current_on_every_hit_without_a_rebuild(atlas):
    get, uncached, _ = atlas
    before = get()
    secret = "postgresql://user:password@private.invalid/db failed"
    store.save_collector_run(run("nyfed_rates", "FAILED", BEFORE_DUE - timedelta(minutes=5), fault=secret))
    after = get()
    assert after.headers["x-seiche-atlas-cache"] == "hit"
    assert after.headers["etag"] != before.headers["etag"]
    adapter = next(a for a in us(after.content)["adapters"] if a["adapter_id"] == "nyfed_rates")
    assert adapter["last_run_status"] == "FAILED"
    assert [f["source"] for f in us(after.content)["faults"]] == ["nyfed_rates"]
    assert secret.encode() not in after.content
    assert after.content == uncached()


def test_an_entry_expires_exactly_when_a_freshness_state_can_change(atlas):
    get, uncached, at = atlas
    assert get().headers["x-seiche-atlas-cache"] == "miss"
    assert us(get().content)["benchmark"]["status"] == "FRESH"
    at(AT_DUE)
    due = get()
    assert due.headers["x-seiche-atlas-cache"] == "miss"
    assert us(due.content)["benchmark"]["status"] == "AGING"
    assert due.content == uncached()


def test_the_cache_never_serves_across_a_rights_change(atlas, monkeypatch):
    get, uncached, _ = atlas
    assert us(get().content)["benchmark"]["value"] == 3.94
    registry = default_registry()
    pack = registry.get("US-USD")
    restricted = replace(pack, source_adapters=tuple(
        replace(adapter, redistribution_status=RedistributionStatus.METADATA_ONLY)
        if adapter.adapter_id == "nyfed_rates" else adapter
        for adapter in pack.source_adapters))
    monkeypatch.setattr(api, "default_registry", lambda: MarketRegistry(tuple(
        restricted if item.market_id == "US-USD" else item for item in registry.list())))
    withheld = get()
    assert withheld.headers["x-seiche-atlas-cache"] == "miss"
    assert us(withheld.content)["benchmark"] is None
    assert b'"value":3.94' not in withheld.content
    assert withheld.content == uncached()


def test_entries_never_outlive_the_next_utc_midnight(atlas):
    get, uncached, at = atlas
    at(datetime(2026, 11, 2, 23, 59, 59, tzinfo=UTC))
    get()
    assert api._ATLAS_CACHE._build.valid_until <= datetime(2026, 11, 3, tzinfo=UTC)
    at(datetime(2026, 11, 3, 0, 0, 0, tzinfo=UTC))
    assert get().headers["x-seiche-atlas-cache"] == "miss"


def test_conditional_requests_and_gzip(atlas):
    get, uncached, _ = atlas
    first = get()
    etag = first.headers["etag"]
    for header in (etag, "W/" + etag, '"other", ' + etag, "*"):
        conditional = get(**{"If-None-Match": header})
        assert conditional.status_code == 304
        assert conditional.content == b""
        assert conditional.headers["etag"] == etag
    assert get(**{"If-None-Match": '"other"'}).status_code == 200
    zipped = get(**{"Accept-Encoding": "gzip"})
    assert zipped.headers["content-encoding"] == "gzip"
    assert zipped.headers["vary"] == "Accept-Encoding"
    assert zipped.content == first.content  # the client decodes the gzip body


def test_a_read_fault_is_served_uncached_and_never_stored(atlas, monkeypatch):
    get, uncached, _ = atlas
    real = SQLiteMarketRepository()

    class FaultyEuro(SQLiteMarketRepository):
        @staticmethod
        def load_observations_as_of(market_id, *args, **kwargs):
            if market_id == "EA-EUR":
                raise RuntimeError("private read failure")
            return real.load_observations_as_of(market_id, *args, **kwargs)

    monkeypatch.setattr(api, "get_repository", lambda: FaultyEuro())
    response = get()
    assert response.headers["x-seiche-atlas-cache"] == "bypass"
    payload = json.loads(response.content)
    assert [fault["market_id"] for fault in payload["read_faults"]] == ["EA-EUR"]
    assert payload["status"] == "PARTIAL"
    assert api._ATLAS_CACHE._build is None
    assert get().headers["x-seiche-atlas-cache"] == "bypass"


def test_a_repository_without_a_fingerprint_bypasses_the_cache(atlas, monkeypatch):
    get, uncached, _ = atlas
    real = SQLiteMarketRepository()

    class Plain:
        load_observations_as_of = staticmethod(real.load_observations_as_of)
        latest_collector_runs = staticmethod(real.latest_collector_runs)

    monkeypatch.setattr(api, "get_repository", lambda: Plain())
    response = get()
    assert response.headers["x-seiche-atlas-cache"] == "bypass"
    assert response.content == uncached()
    assert api._ATLAS_CACHE._build is None


def test_sqlite_fingerprint_tracks_exactly_the_selected_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "fingerprint.sqlite")
    selection = {"us-usd": ("US.NYFED.SOFR_MEDIAN",), "EA-EUR": ()}
    cutoff, floor = BEFORE_DUE, BEFORE_DUE - timedelta(days=30)

    def fingerprint(**kwargs):
        return store.observation_fingerprint_batch_as_of(
            selection, cutoff, event_time=cutoff, event_time_from=kwargs.get("floor", floor))

    store.save_observations([SOFR_1029])
    first = fingerprint()
    assert first[0] == 1
    store.save_observations([SOFR_1029, TGA_1029, SOFR_1030,
                             replace(SOFR_1029, event_time=floor - timedelta(days=1), revision_id="old")])
    assert fingerprint() == first  # identical, unselected, unknown-yet and out-of-window rows
    store.save_observations([replace(SOFR_1029, value=Decimal("395"), revision_id="v2",
                                     knowledge_time=SOFR_1029.knowledge_time + timedelta(hours=1))])
    assert fingerprint()[0] == 2 and fingerprint() != first
    assert fingerprint(floor=SOFR_1029.event_time + timedelta(seconds=1))[0] == 0


@pytest.mark.parametrize("cadence", ["P1D", "P1W", "P2W", "P1M"])
def test_no_state_changes_before_the_recorded_transition(cadence):
    pack = default_registry().get("US-USD")
    adapter = replace(pack.adapter_map["nyfed_rates"], expected_cadence=cadence)
    spec = pack.instrument_map["US.NYFED.SOFR_MEDIAN"]
    calendar = pack.settlement_calendar
    start = datetime(2026, 11, 20, 3, tzinfo=UTC)
    for hours in range(0, 24 * 75, 13):  # Thanksgiving, month ends and the year end
        cutoff = start + timedelta(hours=hours)
        with freshness_transitions() as transitions:
            state, missed, _, _ = _publication_opportunity_clock(
                SOFR_1029, adapter, calendar, cutoff, publication_clock=spec.freshness_clock)
        horizon = min(transitions)
        # At an exact weekly boundary the state changes at the next instant,
        # so the entry is simply not stored; otherwise it holds until horizon.
        assert horizon >= cutoff
        if horizon == cutoff:
            continue
        for probe in (cutoff + (horizon - cutoff) / 3, horizon - timedelta(seconds=1)):
            later = _publication_opportunity_clock(
                SOFR_1029, adapter, calendar, probe, publication_clock=spec.freshness_clock)
            assert (later[0], later[1]) == (state, missed), (cadence, cutoff, probe)
