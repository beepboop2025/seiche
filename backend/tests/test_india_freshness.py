from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from seiche import store
from seiche.collectors import CollectorRunStatus, CollectorSupervisor
from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    Observation,
    QualityState,
    RateCompounding,
    RedistributionStatus,
    SemanticRole,
    StalenessState,
    evidence_sha256,
)
from seiche.markets.atlas import build_global_money_market_atlas
from seiche.markets.india_inr.pack import PACK
from seiche.repository import SQLiteMarketRepository
from seiche.sources.canonical import FetchedDocument, FunctionalCanonicalAdapter
from seiche.sources.official import parse_rbi_html


def _call_row() -> Observation:
    return Observation(
        market_id="IN-INR",
        monetary_area_id="IN",
        jurisdiction_codes=("IN",),
        currency="INR",
        instrument_id="IN.MARKET.CALL_WAR",
        semantic_role=SemanticRole.UNSECURED_OVERNIGHT,
        value=Decimal("508"),
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        rate_compounding=RateCompounding.SIMPLE,
        day_count=DayCountConvention.ACT_365,
        event_time=datetime(2026, 9, 29, tzinfo=UTC),
        source_publication_time=datetime(2026, 9, 29, 18, 29, 59, tzinfo=UTC),
        knowledge_time=datetime(2026, 9, 30, 1, tzinfo=UTC),
        revision_id="rbi-fixture-sep29",
        source="rbi_official",
        evidence_hash=evidence_sha256("rbi-fixture-sep29-5.08"),
        connector_classification=ConnectorClassification.OFFICIAL_OPEN,
        redistribution_status=RedistributionStatus.ALLOWED,
        quality=QualityState.ESTIMATED,
        staleness=StalenessState.FRESH,
    )


@pytest.mark.parametrize(
    ("cutoff", "missed", "status", "next_due"),
    [
        ("2026-09-30T18:29:58+00:00", 0, "FRESH", "2026-10-01"),
        ("2026-09-30T18:30:00+00:00", 0, "FRESH", "2026-10-01"),
        ("2026-10-01T18:30:00+00:00", 1, "AGING", "2026-10-05"),
        # Gandhi Jayanti and the weekend create no extra publication chances.
        ("2026-10-02T19:00:00+00:00", 1, "AGING", "2026-10-05"),
        ("2026-10-04T19:00:00+00:00", 1, "AGING", "2026-10-05"),
        ("2026-10-05T18:30:00+00:00", 2, "STALE", "2026-10-06"),
    ],
)
def test_inr_daily_clock_counts_missed_publications_without_holiday_penalty(
    cutoff, missed, status, next_due
):
    metric = build_global_money_market_atlas(
        (PACK,), {PACK.market_id: (_call_row(),)}, as_of=datetime.fromisoformat(cutoff)
    )["markets"][0]["benchmark"]

    assert metric["missed_publication_opportunities"] == missed
    assert metric["status"] == status
    assert metric["expected_next_update"].startswith(next_due)
    assert metric["asof"] == "2026-09-29"
    assert metric["revision_status"] == "estimated"
    assert "schedule is estimated, not a publication receipt" in metric["freshness_basis"]


def test_inr_successful_new_capture_cannot_freshen_old_observation():
    cutoff = datetime(2026, 10, 5, 19, tzinfo=UTC)
    row = replace(_call_row(), knowledge_time=cutoff)
    metric = build_global_money_market_atlas(
        (PACK,), {PACK.market_id: (row,)}, as_of=cutoff
    )["markets"][0]["benchmark"]
    assert metric["status"] == "STALE"
    assert metric["missed_publication_opportunities"] == 2


def test_latest_mmo_report_stays_current_across_holiday_without_changing_row_clocks():
    row = replace(_call_row(), event_time=datetime(2026, 9, 30, tzinfo=UTC),
                  source_publication_time=datetime(2026, 9, 30, 18, 29, 59, tzinfo=UTC),
                  knowledge_time=datetime(2026, 10, 1, 19, tzinfo=UTC))
    metric = build_global_money_market_atlas(
        (PACK,), {PACK.market_id: (row,)}, as_of=datetime(2026, 10, 4, 19, tzinfo=UTC)
    )["markets"][0]["benchmark"]
    assert metric["status"] == "FRESH"
    assert metric["missed_publication_opportunities"] == 0
    assert metric["expected_next_update"].startswith("2026-10-05")
    assert metric["published_at"] == row.source_publication_time.isoformat()
    assert metric["knowledge_time"] == row.knowledge_time.isoformat()
    assert PACK.instrument_map["IN.RBI.POLICY_REPO"].freshness_clock is None
    assert PACK.instrument_map["IN.RBI.TBILL_3M"].freshness_clock is None
    assert PACK.adapter_map["rbi_official"].publication_clock.business_day_lag == 0


@pytest.mark.asyncio
async def test_rbi_hourly_recheck_acquires_late_row_without_changing_native_clocks(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "rbi.sqlite")
    repository = SQLiteMarketRepository()
    captured_at = datetime(2026, 9, 30, 18, tzinfo=UTC)
    document = FetchedDocument(
        "https://www.rbi.org.in/Scripts/BS_ViewMMO.aspx",
        "text/html",
        b'<b>Money Market Operations as on September 30, 2026</b>'
        b'<table><tr id="OSCallMoney"><td>Call Money</td>'
        b'<td>100.00</td><td>5.08</td></tr></table>',
        "rbi_mmo",
    )

    async def fetcher(_client):
        return (document,)

    adapter = FunctionalCanonicalAdapter(
        pack=PACK,
        adapter_id="rbi_official",
        source="rbi_official",
        fetcher=fetcher,
        parser=parse_rbi_html,
        repository=repository,
        clock=lambda: captured_at,
    )
    async def no_sleep(_seconds):
        return None

    supervisor = CollectorSupervisor(
        observation_writer=repository.save_observations, sleep=no_sleep
    )
    supervisor.register(adapter)
    first = (await supervisor.run_due(now=captured_at))[0]
    # A fetched document whose only row is not yet admissible is a failed
    # observation acquisition, not freshness proof or an empty success.
    assert first.status is CollectorRunStatus.FAILED
    assert first.observations_written == 0
    assert first.next_due == (captured_at + timedelta(hours=1)).isoformat()
    assert PACK.adapter_map["rbi_official"].expected_cadence == "P1D"
    assert await supervisor.run_due(now=captured_at + timedelta(minutes=59)) == []

    captured_at += timedelta(hours=1)
    second = (await supervisor.run_due(now=captured_at))[0]
    assert second.status is CollectorRunStatus.SUCCESS
    assert second.observations_written == 1
    row = repository.load_observations_as_of("IN-INR", captured_at)[0]
    assert row.event_time == datetime(2026, 9, 30, tzinfo=UTC)
    assert row.source_publication_time == datetime(2026, 9, 30, 18, 29, 59, tzinfo=UTC)
    assert row.knowledge_time == captured_at
    assert row.quality is QualityState.ESTIMATED
    assert row.value == Decimal("508")
