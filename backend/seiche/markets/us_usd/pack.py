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
    valid_from_year=1970,
    valid_to_year=2035,
    source_uri="https://www.frbservices.org/about/holiday-schedules",
    holiday_provider=_federal_reserve_holidays,
)

_FRED_DAILY_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=None,
    business_day_lag=0,
    precision=PublicationClockPrecision.UPSTREAM_NATIVE,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
_FED_PRATES_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=None,
    business_day_lag=0,
    precision=PublicationClockPrecision.UNKNOWN,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
# H.4.1 week averages end on Wednesday; the Board releases them on Thursday at
# 16:30 New York, a day later when Thursday is a Fedwire holiday.
_FED_H41_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=time(16, 30),
    business_day_lag=1,
    precision=PublicationClockPrecision.SCHEDULED,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
# Treasury: "Yield curve rates are usually available at Treasury's interest
# rate website by 6:00 PM Eastern Time each trading day" (methodology page).
_TREASURY_PAR_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=time(18, 0),
    business_day_lag=0,
    precision=PublicationClockPrecision.SCHEDULED,
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
_TREASURY_DTS_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=time(16, 0),
    business_day_lag=1,
    precision=PublicationClockPrecision.SCHEDULED,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
_NYFED_UNSECURED_RATE_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=time(9, 0),
    business_day_lag=1,
    precision=PublicationClockPrecision.SCHEDULED,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)

_OPEN = ConnectorClassification.OFFICIAL_OPEN
_ALLOW = RedistributionStatus.ALLOWED
_SIMPLE = RateCompounding.SIMPLE
_COMPOUNDED = RateCompounding.COMPOUNDED
_ACT_360 = DayCountConvention.ACT_360
_BP = CanonicalUnit.BASIS_POINTS
_INDEX = CanonicalUnit.INDEX_POINTS
_LCY_M = CanonicalUnit.LOCAL_CURRENCY_MILLIONS
_NATIVE_COMPOUNDING = RateCompounding.SOURCE_NATIVE
_NATIVE_DAY_COUNT = DayCountConvention.SOURCE_NATIVE

# Treasury par yield tenors read from Treasury's own feed. The source module
# maps each to its feed field; a test keeps the two lists identical.
_TREASURY_PAR_TENORS = ("1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y", "30Y")


def _treasury_par_yields() -> tuple[InstrumentSpec, ...]:
    """Treasury's par yield curve at fixed maturities, bond-equivalent percent.

    Each tenor is a sovereign yield, not the DTB3 discount-basis bill rate that
    ``US.TREASURY.TBILL_3M`` carries, so no existing role binding moves.
    """

    return tuple(
        InstrumentSpec(
            f"US.TREASURY.PAR_YIELD_{suffix}",
            f"UST_PAR_{suffix}",
            SemanticRole.SOVEREIGN_YIELD,
            "treasury_par_yields",
            "percent",
            _BP,
            100,
            _NATIVE_COMPOUNDING,
            _NATIVE_DAY_COUNT,
        )
        for suffix in _TREASURY_PAR_TENORS
    )


def _nyfed_distribution(
    benchmark: str, *, unsecured: bool = False
) -> tuple[InstrumentSpec, ...]:
    """Declare the published distribution without conflating funding markets."""

    adapter = "nyfed_unsecured_rates" if unsecured else "nyfed_rates"
    rates = tuple(
        InstrumentSpec(
            f"US.NYFED.{benchmark}_{suffix}",
            f"{benchmark}_{suffix}",
            role,
            adapter,
            "percent",
            _BP,
            100,
            _SIMPLE,
            _ACT_360,
        )
        for suffix, role in (
            ("MEDIAN", SemanticRole.RATE_MEDIAN),
            ("P01", SemanticRole.RATE_P01),
            ("P25", SemanticRole.RATE_P25),
            ("P75", SemanticRole.RATE_P75),
            ("P99", SemanticRole.RATE_P99),
        )
    )
    return (
        *rates,
        InstrumentSpec(
            f"US.NYFED.{benchmark}_VOLUME",
            f"{benchmark}_VOLUME",
            SemanticRole.UNSECURED_FUNDING_VOLUME
            if unsecured
            else SemanticRole.REPO_VOLUME,
            adapter,
            "USD billions",
            _LCY_M,
            1000,
        ),
    )


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
        SourceAdapterSpec(
            "fed_prates", _OPEN, "P1D", _FED_PRATES_CLOCK, _ALLOW,
            collection_cadence="PT1H",
        ),
        SourceAdapterSpec(
            # Public-domain provenance does not grant rights to the FRED
            # acquisition/cache. Keep these existing records metadata-only
            # until permission or original-publisher acquisition is qualified.
            "fred_daily", _OPEN, "P1D", _FRED_DAILY_CLOCK, RedistributionStatus.METADATA_ONLY,
            collection_cadence="PT1H",
        ),
        SourceAdapterSpec("fred_weekly", _OPEN, "P1W", _FRED_WEEKLY_CLOCK, RedistributionStatus.METADATA_ONLY),
        SourceAdapterSpec(
            # The Board's own H.4.1 release package, not the FRED copy above.
            # Board information is public domain unless otherwise indicated,
            # citing the Board as the source (federalreserve.gov/disclaimer.htm).
            # Daily polling picks up the weekly release without a week's lag.
            "fed_h41", _OPEN, "P1W", _FED_H41_CLOCK, _ALLOW,
            collection_cadence="P1D",
        ),
        SourceAdapterSpec(
            # Treasury's own Daily Treasury Par Yield Curve Rates, not FRED's
            # DGS copies. A work of the US Government (17 U.S.C. 105), cited
            # to Treasury wherever shown. Polled hourly for the 18:00 posting.
            "treasury_par_yields", _OPEN, "P1D", _TREASURY_PAR_CLOCK, _ALLOW,
            collection_cadence="PT1H",
        ),
        SourceAdapterSpec(
            "nyfed_rates", _OPEN, "P1D", _NYFED_RATE_CLOCK, _ALLOW,
            collection_cadence="PT1H",
        ),
        SourceAdapterSpec(
            "nyfed_unsecured_rates",
            _OPEN,
            "P1D",
            _NYFED_UNSECURED_RATE_CLOCK,
            _ALLOW,
            collection_cadence="PT1H",
        ),
        SourceAdapterSpec(
            "nyfed_facilities", _OPEN, "P1D", _FRED_DAILY_CLOCK, _ALLOW,
            collection_cadence="PT1H",
        ),
        SourceAdapterSpec(
            "fiscaldata", _OPEN, "P1D", _TREASURY_DTS_CLOCK, _ALLOW,
            collection_cadence="PT1H",
        ),
    ),
    instruments=(
        InstrumentSpec(
            "US.FED.IORB_DIRECT", "IORB_DIRECT", SemanticRole.POLICY_TARGET,
            "fed_prates", "percent", _BP, 100, _SIMPLE, _ACT_360,
        ),
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
            freshness_clock=_NYFED_RATE_CLOCK,
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
            freshness_clock=_NYFED_UNSECURED_RATE_CLOCK,
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
        # Declared before the FRED copy, as IORB_DIRECT is before IORB, so pack
        # order prefers the Board's own release wherever a role is selected.
        InstrumentSpec(
            "US.FED.RESERVE_BALANCES_DIRECT",
            "RESERVE_BALANCES_DIRECT",
            SemanticRole.RESERVE_BALANCES,
            "fed_h41",
            "USD millions",
            _LCY_M,
        ),
        InstrumentSpec(
            "US.FED.RESERVE_BALANCES",
            "WRESBAL",
            SemanticRole.RESERVE_BALANCES,
            "fred_weekly",
            "USD millions",
            _LCY_M,
        ),
        # The Board's own H.4.1 total assets (Wednesday level, RESPPMA_N.WW),
        # from the same package and rights record as the reserves above. The
        # FRED WALCL copy is a legacy series only and stays metadata-only.
        InstrumentSpec(
            "US.FED.TOTAL_ASSETS_DIRECT",
            "TOTAL_ASSETS_DIRECT",
            SemanticRole.CENTRAL_BANK_TOTAL_ASSETS,
            "fed_h41",
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
        # Declared after SRF on purpose: pack order is the kernel's role
        # priority, so facility_usage keeps reading SRF take-up. ON RRP comes
        # from the same NY Fed Markets API adapter, rights and clock.
        InstrumentSpec(
            "US.NYFED.ON_RRP_TAKEUP",
            "ON_RRP_ACCEPTED",
            SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP,
            "nyfed_facilities",
            "USD billions",
            _LCY_M,
            1000,
        ),
        *_treasury_par_yields(),
        *_nyfed_distribution("SOFR"),
        InstrumentSpec(
            "US.NYFED.SOFR_AVERAGE_30D",
            "SOFR_AVERAGE_30D",
            SemanticRole.COMPOUNDED_OVERNIGHT_AVERAGE_30D,
            "nyfed_rates",
            "percent",
            _BP,
            100,
            _COMPOUNDED,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.SOFR_AVERAGE_90D",
            "SOFR_AVERAGE_90D",
            SemanticRole.COMPOUNDED_OVERNIGHT_AVERAGE_90D,
            "nyfed_rates",
            "percent",
            _BP,
            100,
            _COMPOUNDED,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.SOFR_AVERAGE_180D",
            "SOFR_AVERAGE_180D",
            SemanticRole.COMPOUNDED_OVERNIGHT_AVERAGE_180D,
            "nyfed_rates",
            "percent",
            _BP,
            100,
            _COMPOUNDED,
            _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.SOFR_INDEX",
            "SOFR_INDEX",
            SemanticRole.COMPOUNDED_OVERNIGHT_RATE_INDEX,
            "nyfed_rates",
            "index points",
            _INDEX,
        ),
        *_nyfed_distribution("EFFR", unsecured=True),
        *_nyfed_distribution("OBFR", unsecured=True),
        *_nyfed_distribution("TGCR"),
        *_nyfed_distribution("BGCR"),
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
