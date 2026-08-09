"""US-USD market pack.

This is the compatibility pack: its canonical mappings describe the inputs
behind today's US gauge, while ``legacy.py`` keeps the v1 assembler unchanged.
"""

from __future__ import annotations

from datetime import date, time, timedelta

from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    RateCompounding,
    RedistributionStatus,
    SemanticRole,
)
from seiche.markets.base import (
    BusinessCalendar,
    Capability,
    CapabilityStatus,
    EventSpec,
    InstrumentSpec,
    MarketPack,
    MinimumHistory,
    PolicyRegime,
    PublicationClock,
    PublicationClockPrecision,
    ReserveMaintenanceSpec,
    SourceAdapterSpec,
)


def _observed_fixed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1)
    else:
        cursor = date(year, month + 1, 1)
    ordinal = cursor.toordinal() - 1
    last = date.fromordinal(ordinal)
    return date.fromordinal(ordinal - ((last.weekday() - weekday) % 7))


def _federal_reserve_holidays(year: int) -> frozenset[date]:
    fixed = {
        _observed_fixed(date(year, 1, 1)),
        _observed_fixed(date(year, 6, 19)),
        _observed_fixed(date(year, 7, 4)),
        _observed_fixed(date(year, 11, 11)),
        _observed_fixed(date(year, 12, 25)),
    }
    # A Saturday New Year's Day is observed in the preceding calendar year.
    next_new_year = _observed_fixed(date(year + 1, 1, 1))
    if next_new_year.year == year:
        fixed.add(next_new_year)
    return frozenset(
        fixed
        | {
            _nth_weekday(year, 1, 0, 3),
            _nth_weekday(year, 2, 0, 3),
            _last_weekday(year, 5, 0),
            _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 10, 0, 2),
            _nth_weekday(year, 11, 3, 4),
        }
    )


US_SETTLEMENT_CALENDAR = BusinessCalendar(
    calendar_id="US-FEDWIRE",
    timezone_name="America/New_York",
    holiday_provider=_federal_reserve_holidays,
)

_FRED_DAILY_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=None,
    business_day_lag=0,
    precision=PublicationClockPrecision.UPSTREAM_NATIVE,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
_FRED_WEEKLY_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=None,
    business_day_lag=1,
    precision=PublicationClockPrecision.UPSTREAM_NATIVE,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
_NYFED_RATE_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=time(8, 0),
    business_day_lag=1,
    precision=PublicationClockPrecision.SCHEDULED,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)

_OPEN = ConnectorClassification.OFFICIAL_OPEN
_ALLOW = RedistributionStatus.ALLOWED
_SIMPLE = RateCompounding.SIMPLE
_ACT_360 = DayCountConvention.ACT_360
_BP = CanonicalUnit.BASIS_POINTS
_LCY_M = CanonicalUnit.LOCAL_CURRENCY_MILLIONS


PACK = MarketPack(
    market_id="US-USD",
    monetary_area_id="US",
    display_name="United States dollar",
    jurisdiction_codes=("US",),
    currency="USD",
    local_timezone="America/New_York",
    holiday_calendar=US_SETTLEMENT_CALENDAR,
    settlement_calendar=US_SETTLEMENT_CALENDAR,
    reserve_maintenance=(
        ReserveMaintenanceSpec(
            rule_id="US-RESERVE-AVERAGING",
            description="pack-owned reserve-maintenance calendar; adapter supplies period boundaries",
            period_days=14,
            calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
        ),
    ),
    policy_regime=PolicyRegime.FLOOR,
    source_adapters=(
        SourceAdapterSpec("fred_daily", _OPEN, "P1D", _FRED_DAILY_CLOCK, _ALLOW),
        SourceAdapterSpec("fred_weekly", _OPEN, "P1W", _FRED_WEEKLY_CLOCK, _ALLOW),
        SourceAdapterSpec("nyfed_rates", _OPEN, "P1D", _NYFED_RATE_CLOCK, _ALLOW),
        SourceAdapterSpec("nyfed_facilities", _OPEN, "P1D", _FRED_DAILY_CLOCK, _ALLOW),
        SourceAdapterSpec("fiscaldata", _OPEN, "P1D", _FRED_DAILY_CLOCK, _ALLOW),
    ),
    instruments=(
        InstrumentSpec(
            "US.FED.IORB",
            "IORB",
            SemanticRole.POLICY_TARGET,
            "fred_daily",
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.SOFR",
            "SOFR",
            SemanticRole.SECURED_OVERNIGHT,
            "fred_daily",
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.EFFR",
            "EFFR",
            SemanticRole.UNSECURED_OVERNIGHT,
            "fred_daily",
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.FED.POLICY_CEILING",
            "SRF_CEILING",
            SemanticRole.POLICY_CEILING,
            "fred_daily",
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.TREASURY.TBILL_3M",
            "TB3M",
            SemanticRole.TBILL_3M,
            "fred_daily",
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.CP.NONFINANCIAL_3M",
            "CP_NONFIN_3M",
            SemanticRole.CP_3M,
            "fred_daily",
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.FED.RESERVE_BALANCES",
            "WRESBAL",
            SemanticRole.RESERVE_BALANCES,
            "fred_weekly",
            "USD millions",
            _LCY_M,
        ),
        InstrumentSpec(
            "US.TREASURY.GOVERNMENT_CASH",
            "TGA_DAILY",
            SemanticRole.GOVERNMENT_CASH_BALANCE,
            "fiscaldata",
            "USD billions",
            _LCY_M,
            1000,
        ),
        InstrumentSpec(
            "US.NYFED.SRF_TAKEUP",
            "SRF_ACCEPTED",
            SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP,
            "nyfed_facilities",
            "USD billions",
            _LCY_M,
            1000,
        ),
        InstrumentSpec(
            "US.NYFED.SOFR_MEDIAN",
            "SOFR_MEDIAN",
            SemanticRole.RATE_MEDIAN,
            "nyfed_rates",
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.SOFR_P99",
            "SOFR_P99",
            SemanticRole.RATE_P99,
            "nyfed_rates",
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.SOFR_VOLUME",
            "SOFR_VOLUME",
            SemanticRole.REPO_VOLUME,
            "nyfed_rates",
            "USD billions",
            _LCY_M,
            1000,
        ),
    ),
    capabilities=(
        Capability(
            "policy_relative_overnight",
            CapabilityStatus.READY,
            frozenset({SemanticRole.POLICY_TARGET, SemanticRole.SECURED_OVERNIGHT}),
            minimum_history=MinimumHistory(250, 365),
        ),
        Capability(
            "corridor_pressure",
            CapabilityStatus.UNAVAILABLE,
            reason="canonical policy-floor series is not yet mapped",
        ),
        Capability(
            "secured_unsecured",
            CapabilityStatus.READY,
            frozenset(
                {SemanticRole.SECURED_OVERNIGHT, SemanticRole.UNSECURED_OVERNIGHT}
            ),
            minimum_history=MinimumHistory(250, 365),
        ),
        Capability(
            "term_funding",
            CapabilityStatus.READY,
            frozenset({SemanticRole.CP_3M, SemanticRole.TBILL_3M}),
            minimum_history=MinimumHistory(250, 365),
        ),
        Capability(
            "liquidity_buffer_drain",
            CapabilityStatus.READY,
            frozenset(
                {SemanticRole.RESERVE_BALANCES, SemanticRole.GOVERNMENT_CASH_BALANCE}
            ),
            minimum_history=MinimumHistory(52, 365),
        ),
        Capability(
            "facility_usage",
            CapabilityStatus.READY,
            frozenset(
                {
                    SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP,
                    SemanticRole.RESERVE_BALANCES,
                }
            ),
            minimum_history=MinimumHistory(60, 90),
        ),
        Capability(
            "tail_dispersion",
            CapabilityStatus.READY,
            frozenset({SemanticRole.RATE_MEDIAN, SemanticRole.RATE_P99}),
            minimum_history=MinimumHistory(250, 365),
        ),
        Capability(
            "volume_dislocation",
            CapabilityStatus.READY,
            frozenset({SemanticRole.REPO_VOLUME}),
            minimum_history=MinimumHistory(60, 90),
        ),
        Capability(
            "reserve_kink",
            CapabilityStatus.READY,
            frozenset(
                {
                    SemanticRole.RESERVE_BALANCES,
                    SemanticRole.POLICY_TARGET,
                    SemanticRole.SECURED_OVERNIGHT,
                }
            ),
            minimum_history=MinimumHistory(156, 1095),
        ),
        Capability(
            "cross_basin_coupling",
            CapabilityStatus.UNAVAILABLE,
            reason="no canonical FX-swap-basis observation is mapped yet",
        ),
        Capability(
            "historical_prediction",
            CapabilityStatus.FORWARD_ONLY,
            reason="legacy captures do not preserve per-row historical knowledge clocks",
        ),
    ),
    events=(
        EventSpec(
            "RESERVE_MAINTENANCE_END",
            "reserve-maintenance period end",
            frozenset(
                {SemanticRole.RESERVE_BALANCES, SemanticRole.UNSECURED_OVERNIGHT}
            ),
        ),
        EventSpec(
            "GOVERNMENT_SETTLEMENT",
            "government cash settlement",
            frozenset(
                {
                    SemanticRole.GOVERNMENT_CASH_BALANCE,
                    SemanticRole.RESERVE_BALANCES,
                }
            ),
        ),
        EventSpec(
            "REPORTING_TURN",
            "reporting-period turn",
            frozenset(
                {SemanticRole.SECURED_OVERNIGHT, SemanticRole.REPO_VOLUME}
            ),
        ),
    ),
    calibration_id="us-usd-legacy-parity-v1",
    minimum_history=MinimumHistory(750, 1095),
)
