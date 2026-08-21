from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from seiche.domain.observation import (
    DayCountConvention,
    RedistributionStatus,
    SemanticRole,
)
from seiche.markets.base import (
    CalendarUnavailableError,
    PackSupportStatus,
    PolicyRegime,
    PublicationClockPrecision,
)
from seiche.markets.registry import default_registry


def test_korea_pack_is_registered_as_a_reference_corridor() -> None:
    pack = default_registry().get("kr-krw")

    assert pack.market_id == "KR-KRW"
    assert pack.monetary_area_id == "KR"
    assert pack.currency == "KRW"
    assert pack.jurisdiction_codes == ("KR",)
    assert pack.local_timezone == "Asia/Seoul"
    assert pack.policy_regime is PolicyRegime.CORRIDOR
    assert pack.support_status is PackSupportStatus.REFERENCE


def test_korea_pack_preserves_source_rights_cadence_and_rate_conventions() -> None:
    pack = default_registry().get("KR-KRW")

    assert pack.adapter_map["bok_ecos_policy"].expected_cadence == "P1D"
    assert (
        pack.adapter_map["bok_ecos_policy"].redistribution_status
        is RedistributionStatus.ALLOWED
    )
    assert (
        pack.adapter_map["bok_ecos_money_market"].redistribution_status
        is RedistributionStatus.ALLOWED
    )
    assert (
        pack.adapter_map["ksd_kofr"].redistribution_status
        is RedistributionStatus.METADATA_ONLY
    )
    assert (
        pack.adapter_map["licensed_krw_market"].redistribution_status
        is RedistributionStatus.DERIVED_ONLY
    )
    assert (
        pack.adapter_map["tenant_market_data"].redistribution_status
        is RedistributionStatus.PROHIBITED
    )

    expected_roles = {
        "KR.BOK.BASE_RATE": SemanticRole.POLICY_TARGET,
        "KR.BOK.LIQUIDITY_ADJUSTMENT_DEPOSIT": SemanticRole.POLICY_FLOOR,
        "KR.BOK.LIQUIDITY_ADJUSTMENT_LOAN": SemanticRole.POLICY_CEILING,
        "KR.BOK.CALL_OVERNIGHT_ALL": SemanticRole.UNSECURED_OVERNIGHT,
        "KR.KSD.KOFR": SemanticRole.SECURED_OVERNIGHT,
    }
    assert {
        instrument_id: pack.instrument_map[instrument_id].semantic_role
        for instrument_id in expected_roles
    } == expected_roles
    assert all(
        instrument.day_count is DayCountConvention.ACT_365
        for instrument in pack.instruments
    )
    assert pack.instrument_map["KR.BOK.BASE_RATE"].normalize("2.75") == Decimal(
        "275.00"
    )
    assert pack.instrument_map["KR.BOK.CALL_OVERNIGHT_ALL"].normalize(
        "2.789"
    ) == Decimal("278.900")


def test_kofr_clock_is_exact_next_business_day_at_1100_kst() -> None:
    pack = default_registry().get("KR-KRW")
    clock = pack.adapter_map["ksd_kofr"].publication_clock

    assert clock.precision is PublicationClockPrecision.EXACT
    # Friday 22 May is followed by a weekend and the Buddha's Birthday
    # substitute holiday on Monday 25 May.
    assert clock.resolve(date(2026, 5, 22), pack.settlement_calendar) == datetime(
        2026, 5, 26, 2, 0, tzinfo=UTC
    )


def test_bok_native_clocks_do_not_invent_intraday_publication_times() -> None:
    pack = default_registry().get("KR-KRW")
    policy = pack.adapter_map["bok_ecos_policy"].publication_clock
    call = pack.adapter_map["bok_ecos_money_market"].publication_clock

    assert policy.precision is PublicationClockPrecision.UPSTREAM_NATIVE
    assert policy.local_time is None
    assert policy.business_day_lag == 0
    assert call.precision is PublicationClockPrecision.UPSTREAM_NATIVE
    assert call.local_time is None
    assert call.business_day_lag == 1


def test_korea_calendar_models_bok_wire_closures_and_fails_closed() -> None:
    calendar = default_registry().get("KR-KRW").settlement_calendar

    assert not calendar.is_business_day(date(2025, 5, 1))
    assert not calendar.is_business_day(date(2026, 6, 3))
    assert not calendar.is_business_day(date(2026, 8, 17))
    assert calendar.is_business_day(date(2026, 8, 18))
    with pytest.raises(CalendarUnavailableError):
        calendar.is_business_day(date(2027, 1, 4))
