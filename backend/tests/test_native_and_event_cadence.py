"""Instruments age at their own cadence; event-dated series never go dead by schedule."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from seiche.domain.observation import (
    CanonicalUnit, Observation, QualityState, SemanticRole, StalenessState, evidence_sha256,
)
from seiche.markets.atlas import build_global_money_market_atlas
from seiche.markets.base import EVENT_CADENCE, InstrumentSpec
from seiche.markets.materialize import _source_state
from seiche.markets.registry import default_registry
from seiche.sources import rbi
from seiche.sources.canonical import FetchedDocument

REGISTRY = default_registry()
# The live inventory read the board at this instant on 2026-11-02.
AS_OF = datetime(2026, 11, 2, 8, 18, 24, tzinfo=UTC)


def row(market_id, instrument_id, day, value, *, published=None, staleness=StalenessState.FRESH):
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
        quality=QualityState.ESTIMATED, staleness=staleness,
    )


def metrics(market_id, rows, as_of=AS_OF):
    market, = build_global_money_market_atlas((REGISTRY.get(market_id),), {market_id: rows}, as_of=as_of)["markets"]
    return market, {metric["id"]: metric for metric in market["metrics"]}


def business_days(start, end):
    pack = REGISTRY.get("AU-AUD")
    day = datetime.fromisoformat(start).date()
    while day <= datetime.fromisoformat(end).date():
        if pack.settlement_calendar.is_business_day(day):
            yield day.isoformat()
        day += timedelta(days=1)


def test_instrument_cadence_contract():
    with pytest.raises(ValueError, match="observation_cadence"):
        InstrumentSpec("X.Y", "XY", SemanticRole.RESERVE_BALANCES, "rba_policy", "units",
                       CanonicalUnit.LOCAL_CURRENCY_MILLIONS, observation_cadence="fortnightly")
    au, india, jp = (REGISTRY.get(m) for m in ("AU-AUD", "IN-INR", "JP-JPY"))
    assert au.instrument_cadence(au.instrument_map["AU.RBA.ES_FLOOR"]) == EVENT_CADENCE
    assert au.instrument_cadence(au.instrument_map["AU.RBA.CASH_TARGET"]) == "P1D"
    assert india.instrument_cadence(india.instrument_map["IN.RBI.DURABLE_LIQUIDITY"]) == "P2W"
    assert jp.adapter_map["boj_accounts"].expected_cadence == "P1M"
    assert jp.adapter_map["boj_accounts"].collection_cadence == "P1D"


def test_unchanged_rba_policy_setting_follows_its_daily_capture_not_a_dead_clock():
    # Live 2026-11-02: ES floor 4.50 and ceiling 4.85 last changed 2026-09-30
    # (RBA table A2). They were labelled P1D and reported DEAD.
    rows = [row("AU-AUD", "AU.RBA.ES_FLOOR", "2026-09-30", 450), row("AU-AUD", "AU.RBA.STANDING_CEILING", "2026-09-30", 485)]
    rows += [row("AU-AUD", "AU.RBA.CASH_TARGET", day, 460) for day in business_days("2026-10-01", "2026-10-29")]
    _, by_id = metrics("AU-AUD", rows)
    target = by_id["AU.RBA.CASH_TARGET"]
    for key in ("AU.RBA.ES_FLOOR", "AU.RBA.STANDING_CEILING"):
        metric = by_id[key]
        assert metric["cadence"] == "EVENT"
        assert metric["status"] == target["status"] in {"FRESH", "AGING"}
        assert metric["asof"] == "2026-09-30"
        assert metric["confirmed_through"] == "2026-10-29"
        assert metric["confirmed_by"] == "AU.RBA.CASH_TARGET"
        assert metric["missed_publication_opportunities"] is None
        assert metric["change_vol_20_annualized"] is None
        assert "only when the authority acts" in metric["expected_next_update"]
    assert by_id["AU.RBA.ES_FLOOR"]["value"] == 4.5


def test_event_series_is_never_calmer_than_its_capture():
    setting = [row("AU-AUD", "AU.RBA.ES_FLOOR", "2026-09-30", 450)]
    _, alone = metrics("AU-AUD", setting)
    assert alone["AU.RBA.ES_FLOOR"]["status"] == "UNKNOWN"
    assert alone["AU.RBA.ES_FLOOR"]["confirmed_through"] is None
    old_capture = setting + [row("AU-AUD", "AU.RBA.CASH_TARGET", "2026-09-30", 460)]
    _, dead = metrics("AU-AUD", old_capture)
    assert dead["AU.RBA.CASH_TARGET"]["status"] == "DEAD"
    assert dead["AU.RBA.ES_FLOOR"]["status"] == "DEAD"


def test_rbi_operations_and_durable_liquidity_use_their_own_clocks():
    # Live: VRRR last ran 2026-10-15 and VRR 2026-10-26 (RBI DBIE daily LAF
    # report agrees); durable liquidity is dated 15 Oct and appears about two
    # weeks later. All three had been DEAD or STALE on a daily clock.
    rows = [row("IN-INR", "IN.RBI.VRRR_TODAY", "2026-10-15", 95620, published=datetime(2026, 10, 16, 3, 30, tzinfo=UTC)),
            row("IN-INR", "IN.RBI.VRR_TODAY", "2026-10-26", 1500070, published=datetime(2026, 10, 27, 3, 30, tzinfo=UTC)),
            row("IN-INR", "IN.RBI.DURABLE_LIQUIDITY", "2026-10-15", 7934090, published=datetime(2026, 11, 2, 6, 40, tzinfo=UTC)),
            row("IN-INR", "IN.RBI.SDF_TODAY", "2026-11-01", 2058690, published=datetime(2026, 11, 2, 6, 40, tzinfo=UTC))]
    _, by_id = metrics("IN-INR", rows)
    assert by_id["IN.RBI.SDF_TODAY"]["status"] == "FRESH"
    for key in ("IN.RBI.VRRR_TODAY", "IN.RBI.VRR_TODAY"):
        assert by_id[key]["cadence"] == "EVENT"
        assert by_id[key]["status"] == "FRESH"
        assert by_id[key]["confirmed_through"] == "2026-11-01"
    durable = by_id["IN.RBI.DURABLE_LIQUIDITY"]
    assert (durable["cadence"], durable["status"]) == ("P2W", "FRESH")
    _, later = metrics("IN-INR", rows, as_of=datetime(2026, 12, 20, tzinfo=UTC))
    assert later["IN.RBI.DURABLE_LIQUIDITY"]["status"] == "STALE"
    assert later["IN.RBI.VRRR_TODAY"]["status"] == "DEAD"


def test_blank_operation_rows_are_not_parsed_as_zero_or_as_operations():
    text = (Path(__file__).parent / "fixtures" / "rbi" / "money-market-operations-2026-10-29.html").read_bytes()
    points = {p.instrument_id: p for p in rbi.parse_mmo(FetchedDocument(
        "https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63885", "text/html", text, "rbi_mmo"))}
    assert "IN.RBI.VRR_TODAY" not in points and "IN.RBI.VRRR_TODAY" not in points
    assert points["IN.RBI.VRR_OUTSTANDING"].raw_value == Decimal("150007.00")
    assert points["IN.RBI.SDF_TODAY"].raw_value == Decimal("174418.00")
    assert str(points["IN.RBI.DURABLE_LIQUIDITY"].event_time) == "2026-09-30"


def test_rbi_decisions_are_event_dated_and_unknown_without_a_daily_capture():
    _, by_id = metrics("IN-INR", [row("IN-INR", "IN.RBI.POLICY_REPO_DECISION", "2026-10-07", 550, published=False)])
    decision = by_id["IN.RBI.POLICY_REPO_DECISION"]
    assert (decision["cadence"], decision["status"], decision["value"]) == ("EVENT", "UNKNOWN", 5.5)


def test_boj_current_accounts_age_by_calendar_month():
    published = datetime(2026, 10, 1, 15, tzinfo=UTC)
    rows = [row("JP-JPY", "JP.BOJ.CURRENT_ACCOUNTS", "2026-09-30", 413667800, published=published)]
    _, by_id = metrics("JP-JPY", rows)
    assert (by_id["JP.BOJ.CURRENT_ACCOUNTS"]["cadence"], by_id["JP.BOJ.CURRENT_ACCOUNTS"]["status"]) == ("P1M", "FRESH")


def test_materializer_uses_the_same_native_and_event_clocks():
    au, jp = REGISTRY.get("AU-AUD"), REGISTRY.get("JP-JPY")
    floor_day = datetime(2026, 9, 30, tzinfo=UTC)
    for target_day in (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 10, 29, tzinfo=UTC), floor_day):
        companions = {"AU.RBA.CASH_TARGET": (target_day, AS_OF, StalenessState.FRESH)}
        # The setting is exactly as current as the daily row from its capture.
        assert _source_state(au, "AU.RBA.ES_FLOOR", AS_OF, floor_day, companions=companions) is _source_state(
            au, "AU.RBA.CASH_TARGET", AS_OF, target_day)
    companions = {"AU.RBA.CASH_TARGET": (datetime(2026, 11, 1, tzinfo=UTC), AS_OF, StalenessState.FRESH)}
    assert _source_state(au, "AU.RBA.ES_FLOOR", AS_OF, floor_day, companions=companions) is StalenessState.FRESH
    assert _source_state(au, "AU.RBA.ES_FLOOR", AS_OF, floor_day) is StalenessState.UNKNOWN
    assert _source_state(jp, "JP.BOJ.CURRENT_ACCOUNTS", AS_OF, floor_day) is StalenessState.FRESH


def test_series_endpoint_ages_monthly_rows_without_failing():
    # /api/v2/markets/{IN-INR,TW-TWD,AU-AUD}/series returned HTTP 500 on
    # 2026-11-02: its fixed-seconds clock had no calendar-month unit.
    from seiche.api import _series_effective_staleness

    for market, instrument in (("IN-INR", "IN.RBI.SGL_MONTHLY_10Y"), ("TW-TWD", "TW.CBC.OVERNIGHT_CALL_MONTHLY"),
                               ("AU-AUD", "AU.OECD.LONG_TERM_10Y_MONTHLY"), ("JP-JPY", "JP.BOJ.CURRENT_ACCOUNTS")):
        latest = row(market, instrument, "2026-09-30", 500)
        assert _series_effective_staleness(REGISTRY.get(market), latest, AS_OF) is StalenessState.FRESH
    floor = row("AU-AUD", "AU.RBA.ES_FLOOR", "2026-09-30", 450)
    target = row("AU-AUD", "AU.RBA.CASH_TARGET", "2026-11-01", 460)
    latest = {"AU.RBA.ES_FLOOR": floor, "AU.RBA.CASH_TARGET": target}
    assert _series_effective_staleness(REGISTRY.get("AU-AUD"), floor, AS_OF, latest) is StalenessState.FRESH
