"""The workbench series and the money-market atlas apply one freshness rule.

Live on 2026-11-02 08:29 UTC (Monday), /api/v2/markets/US-USD/series called 31
NY Fed rows dated Thursday 2026-10-29 STALE by wall-clock age while the atlas
called the same rows FRESH: Friday's 08:00 New York print was not due until
13:00 UTC. The atlas rule (missed pack-calendar publication opportunities) is
the documented one; the series endpoint now uses it too.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import Request, Response

from seiche import api, store
from seiche.domain.observation import (
    Observation,
    QualityState,
    RedistributionStatus,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.atlas import _metric, _publication_opportunity_clock
from seiche.markets.registry import default_registry

MONDAY_BEFORE_PRINT = datetime(2026, 11, 2, 8, 29, tzinfo=UTC)


def _request() -> Request:
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/api/v2/markets/US-USD/series", "raw_path": b"/api/v2/markets/US-USD/series",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })


def row(market_id, instrument_id, day, value, *, published=None, knowledge=None):
    pack = default_registry().get(market_id)
    spec = pack.instrument_map[instrument_id]
    adapter = pack.adapter_map[spec.source_adapter_id]
    event = datetime.fromisoformat(day + "T00:00:00+00:00")
    publication = datetime.fromisoformat(published) if published else None
    return Observation(
        market_id=pack.market_id, monetary_area_id=pack.monetary_area_id,
        jurisdiction_codes=pack.jurisdiction_codes, currency=pack.currency,
        instrument_id=instrument_id, semantic_role=spec.semantic_role,
        value=Decimal(value), canonical_unit=spec.canonical_unit,
        rate_compounding=spec.rate_compounding, day_count=spec.day_count,
        event_time=event, source_publication_time=publication,
        knowledge_time=knowledge or (publication or event) + timedelta(minutes=10),
        revision_id=f"parity-{instrument_id}-{day}", source=spec.source_adapter_id,
        evidence_hash=evidence_sha256(f"{instrument_id}:{day}:{value}"),
        connector_classification=adapter.classification,
        redistribution_status=adapter.redistribution_status,
        quality=QualityState.VERIFIED, staleness=StalenessState.FRESH,
    )


def nyfed(instrument_id, day, published, value="394"):
    return row("US-USD", instrument_id, day, value, published=published)


def frozen_now(monkeypatch, moment):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.astimezone(tz)

    monkeypatch.setattr(api, "datetime", Frozen)


def atlas_status(market_id, rows, cutoff):
    pack = default_registry().get(market_id)
    spec = pack.instrument_map[rows[-1].instrument_id]
    return _metric(pack, spec, sorted(rows, key=lambda item: item.event_time), cutoff=cutoff)["status"]


def test_live_monday_case_is_fresh_on_both_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "parity.sqlite")
    frozen_now(monkeypatch, MONDAY_BEFORE_PRINT)
    rows = [
        nyfed("US.NYFED.SOFR_MEDIAN", "2026-10-28", "2026-10-29T12:00:00+00:00", "395"),
        nyfed("US.NYFED.SOFR_MEDIAN", "2026-10-29", "2026-10-30T12:00:00+00:00"),
        nyfed("US.NYFED.BGCR_MEDIAN", "2026-10-29", "2026-10-30T12:00:00+00:00", "392"),
        nyfed("US.NYFED.EFFR_MEDIAN", "2026-10-29", "2026-10-30T13:00:00+00:00", "388"),
    ]
    store.save_observations(rows)
    payload = api.market_series_v2("US-USD", _request(), Response())
    by_key = {(item["instrument_id"], item["event_time"][:10]): item["staleness"]
              for item in payload["observations"]}
    assert by_key[("US.NYFED.SOFR_MEDIAN", "2026-10-29")] == "fresh"
    assert by_key[("US.NYFED.BGCR_MEDIAN", "2026-10-29")] == "fresh"
    assert by_key[("US.NYFED.EFFR_MEDIAN", "2026-10-29")] == "fresh"
    assert by_key[("US.NYFED.SOFR_MEDIAN", "2026-10-28")] == "aging"
    stale_ids = {item["instrument_id"] for item in payload["stale_inputs"]}
    assert not stale_ids & {"US.NYFED.SOFR_MEDIAN", "US.NYFED.BGCR_MEDIAN", "US.NYFED.EFFR_MEDIAN"}
    availability = {item["instrument_id"]: item["availability"] for item in payload["instruments"]}
    assert availability["US.NYFED.SOFR_MEDIAN"] == "READY"
    for instrument_id in ("US.NYFED.SOFR_MEDIAN", "US.NYFED.BGCR_MEDIAN", "US.NYFED.EFFR_MEDIAN"):
        mine = [item for item in rows if item.instrument_id == instrument_id]
        assert atlas_status("US-USD", mine, MONDAY_BEFORE_PRINT) == "FRESH"

    # Once Friday's print is due and absent, both surfaces age the row together.
    after_print = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
    frozen_now(monkeypatch, after_print)
    later = api.market_series_v2("US-USD", _request(), Response())
    later_by_key = {(item["instrument_id"], item["event_time"][:10]): item["staleness"]
                    for item in later["observations"]}
    assert later_by_key[("US.NYFED.SOFR_MEDIAN", "2026-10-29")] == "aging"
    assert atlas_status("US-USD", rows[:2], after_print) == "AGING"


CASES = [
    # Scheduled NY Fed rate clock (08:00, one business day of lag).
    lambda: [nyfed("US.NYFED.SOFR_MEDIAN", "2026-11-25", "2026-11-27T13:00:00+00:00")],
    # Unsecured rate clock (09:00) on an instrument-level freshness clock.
    lambda: [nyfed("US.NYFED.EFFR_MEDIAN", "2026-11-25", "2026-11-27T14:00:00+00:00", "388")],
    # Treasury DTS at 16:00 with one business day of lag.
    lambda: [row("US-USD", "US.TREASURY.GOVERNMENT_CASH", "2026-11-24", "900000",
                 published="2026-11-25T21:00:00+00:00")],
    # Native same-day facility clock with an inferred end-of-day release.
    lambda: [row("US-USD", "US.NYFED.SRF_TAKEUP", "2026-11-24", "0",
                 published="2026-11-25T04:59:59+00:00")],
    # A Board file with no publication receipt stays UNKNOWN on both.
    lambda: [row("US-USD", "US.FED.IORB_DIRECT", "2026-11-24", "390")],
    # Monthly cadence: the old wall-clock rule raised on "P1M" (HTTP 500).
    lambda: [row("IN-INR", "IN.RBI.SGL_MONTHLY_10Y", "2026-09-30", "650",
                 published="2026-10-31T12:29:59+00:00")],
]


@pytest.mark.parametrize("case", range(len(CASES)))
def test_series_rule_equals_the_atlas_rule_through_holidays_and_weekends(case):
    rows = CASES[case]()
    pack = default_registry().get(rows[0].market_id)
    start = datetime(2026, 11, 25, 0, tzinfo=UTC)
    for hours in range(0, 24 * 12, 3):  # Thanksgiving week, a weekend, then nine days
        cutoff = start + timedelta(hours=hours)
        if any(item.knowledge_time > cutoff for item in rows):
            continue
        series_state = api._series_effective_staleness(pack, rows[-1], cutoff)
        assert series_state.value.upper() == atlas_status(pack.market_id, rows, cutoff), cutoff


def test_monthly_market_series_page_no_longer_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "monthly.sqlite")
    moment = datetime(2026, 11, 2, 8, 29, tzinfo=UTC)
    frozen_now(monkeypatch, moment)
    monthly = row("IN-INR", "IN.RBI.SGL_MONTHLY_10Y", "2026-09-30", "650",
                  published="2026-10-31T12:29:59+00:00")
    store.save_observations([monthly])
    payload = api.market_series_v2("IN-INR", _request(), Response())
    (record,) = [item for item in payload["observations"]
                 if item["instrument_id"] == "IN.RBI.SGL_MONTHLY_10Y"]
    assert record["staleness"].upper() == atlas_status("IN-INR", [monthly], moment)


@pytest.mark.parametrize("day", ["2018-04-02", "2025-12-31", "2026-10-01"])
def test_deep_history_shortcut_returns_exactly_the_full_clock(day):
    pack = default_registry().get("US-USD")
    old = nyfed("US.NYFED.SOFR_MEDIAN", day,
                (datetime.fromisoformat(day + "T12:00:00+00:00") + timedelta(days=1)).isoformat())
    spec = pack.instrument_map[old.instrument_id]
    full = _publication_opportunity_clock(
        old, pack.adapter_map[spec.source_adapter_id], pack.settlement_calendar,
        MONDAY_BEFORE_PRINT, publication_clock=spec.freshness_clock)[0]
    assert api._series_effective_staleness(pack, old, MONDAY_BEFORE_PRINT) is full
    assert full is StalenessState.DEAD
    # Rights are untouched by the clock: a metadata-only copy stays restricted.
    assert pack.adapter_map["fred_daily"].redistribution_status is RedistributionStatus.METADATA_ONLY


def series_statuses(market_id, moment, monkeypatch):
    """Each instrument's current staleness on the series page (latest record)."""
    frozen_now(monkeypatch, moment)
    payload = api.market_series_v2(market_id, _request(), Response(), n=500)
    latest = {}
    for record in payload["observations"]:
        if record["event_time"] >= latest.get(record["instrument_id"], {}).get("event_time", ""):
            latest[record["instrument_id"]] = record
    return {key: record["staleness"].upper() for key, record in latest.items()}


def atlas_statuses(market_id, rows, moment):
    """Each instrument's status in the atlas built from the same rows."""
    from seiche.markets.atlas import build_global_money_market_atlas

    pack = default_registry().get(market_id)
    atlas = build_global_money_market_atlas([pack], {market_id: rows}, as_of=moment)
    return {metric["id"]: metric["status"] for metric in atlas["markets"][0]["metrics"]}


def test_india_event_dated_rows_agree_on_both_surfaces(tmp_path, monkeypatch):
    """VRR/VRRR rows exist only on operation days; each follows the daily capture."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "india-events.sqlite")
    rows = [
        row("IN-INR", "IN.RBI.VRRR_TODAY", "2026-10-15", "95620", published="2026-10-16T03:30:00+00:00"),
        row("IN-INR", "IN.RBI.VRR_TODAY", "2026-10-26", "1500070", published="2026-10-27T03:30:00+00:00"),
        row("IN-INR", "IN.RBI.SDF_TODAY", "2026-11-01", "2058690", published="2026-11-02T06:40:00+00:00"),
        row("IN-INR", "IN.RBI.DURABLE_LIQUIDITY", "2026-10-15", "7934090", published="2026-11-02T06:40:00+00:00"),
        # A decision has no daily companion on its adapter: UNKNOWN, never calm.
        row("IN-INR", "IN.RBI.POLICY_REPO_DECISION", "2026-10-07", "550"),
    ]
    store.save_observations(rows)
    instruments = {item.instrument_id for item in rows}
    for moment in (datetime(2026, 11, 2, 8, 18, 24, tzinfo=UTC), datetime(2026, 12, 20, tzinfo=UTC)):
        series, atlas = series_statuses("IN-INR", moment, monkeypatch), atlas_statuses("IN-INR", rows, moment)
        assert {key: series[key] for key in instruments} == {key: atlas[key] for key in instruments}, moment
    assert series["IN.RBI.VRRR_TODAY"] == "DEAD"  # seven weeks on, with no newer capture
    first = series_statuses("IN-INR", datetime(2026, 11, 2, 8, 18, 24, tzinfo=UTC), monkeypatch)
    assert first["IN.RBI.VRRR_TODAY"] == first["IN.RBI.VRR_TODAY"] == "FRESH"
    assert first["IN.RBI.POLICY_REPO_DECISION"] == "UNKNOWN"


def test_monthly_markets_agree_on_both_surfaces(tmp_path, monkeypatch):
    """BoJ current accounts age by calendar month on the page and in the atlas."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "japan-monthly.sqlite")
    rows = [row("JP-JPY", "JP.BOJ.CURRENT_ACCOUNTS", "2026-09-30", "500",
                published="2026-10-01T15:00:00+00:00")]
    store.save_observations(rows)
    verdicts = []
    for moment in (datetime(2026, 11, 2, 8, 18, 24, tzinfo=UTC), datetime(2027, 2, 10, tzinfo=UTC)):
        series = series_statuses("JP-JPY", moment, monkeypatch)["JP.BOJ.CURRENT_ACCOUNTS"]
        assert series == atlas_statuses("JP-JPY", rows, moment)["JP.BOJ.CURRENT_ACCOUNTS"], moment
        verdicts.append(series)
    assert verdicts[0] == "FRESH" and verdicts[1] != "FRESH"
