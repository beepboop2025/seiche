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
# H.15 is "posted daily Monday through Friday at 4:15pm" New York with the
# previous business day's rates (H.15 release page, checked 2026-11-02).
_FED_H15_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=time(16, 15),
    business_day_lag=1,
    precision=PublicationClockPrecision.SCHEDULED,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
# H.10 posts the previous week's daily rates once a week: "updated weekly on
# Mondays at 4:15 p.m." (H.10 About page, checked 2026-11-02). A business-day
# lag cannot say "next Monday", so two clocks are declared. The adapter clock,
# the next business day at 16:15, is the earliest a row could be public: exact
# for a Friday row, earlier than the weekly posting for the others, and never a
# publication receipt; knowledge time is always the actual capture. Currentness
# uses the instrument clock, five business days at 16:15, under which the day
# after the last published row falls due at the next Monday posting (a business
# day later when a holiday falls in between, never earlier).
_FED_H10_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=time(16, 15),
    business_day_lag=1,
    precision=PublicationClockPrecision.SCHEDULED,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
_FED_H10_CURRENTNESS_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=time(16, 15),
    business_day_lag=5,
    precision=PublicationClockPrecision.SCHEDULED,
    calendar_id=US_SETTLEMENT_CALENDAR.calendar_id,
)
# G.17 is published "around the middle of the month" on a release calendar the
# package does not carry. As for PRATES, the publication clock is unknown and
# acquisition establishes knowledge time.
_FED_G17_CLOCK = PublicationClock(
    timezone_name="America/New_York",
    local_time=None,
    business_day_lag=0,
    precision=PublicationClockPrecision.UNKNOWN,
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
_RATIO = CanonicalUnit.RATIO
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


# Direct Board H.4.1 balance-sheet lines beyond reserves and total assets, from
# the same package and rights record. The series each one reads is pinned in
# seiche.sources.frb_release_package.H41; the FRED copy of each stays
# metadata-only and is not declared here. All are in USD millions.
_FED_H41_ITEMS = (
    "TGA_WEEK_AVERAGE_DIRECT",  # RESPPLLDT_XAW_N.WW, week average
    "CURRENCY_IN_CIRCULATION_DIRECT",  # RESTBC_XAW_N.WW, week average
    "PRIMARY_CREDIT_DIRECT",  # RESPPALDP_N.WW, Wednesday level
    "LIQUIDITY_SWAPS_DIRECT",  # RESH4SCS_N.WW, Wednesday level
    "FOREIGN_OFFICIAL_RRP_DIRECT",  # RESPPLLRF_N.WW, Wednesday level
    "CUSTODY_TREASURIES_DIRECT",  # RESH4FG_N.WW, Wednesday level (memorandum)
    "FOREIGN_OFFICIAL_REPO_DIRECT",  # RESPPALGTRF_N.WW, Wednesday level
)


def _fed_h41_items() -> tuple[InstrumentSpec, ...]:
    """H.4.1 lines as published, under a role no capability or engine reads."""

    return tuple(
        InstrumentSpec(
            f"US.FED.{mnemonic}",
            mnemonic,
            SemanticRole.CENTRAL_BANK_BALANCE_SHEET_ITEM,
            "fed_h41",
            "USD millions",
            _LCY_M,
        )
        for mnemonic in _FED_H41_ITEMS
    )


def _fed_h15() -> tuple[InstrumentSpec, ...]:
    """H.15 Treasury constant maturities and bill rates from the Board's package.

    Constant maturity yields are Treasury's interpolations, bond-equivalent like
    the par yields above, so they are SOVEREIGN_YIELD. Bill rates are on a
    bank-discount basis with a 360-day year, so they take their own role,
    TBILL_DISCOUNT_RATE, and do not displace the DTB3 binding of ``TBILL_3M``.
    """

    yields = tuple(
        InstrumentSpec(
            f"US.FED.H15_CMT_{tenor}", f"H15_CMT_{tenor}", SemanticRole.SOVEREIGN_YIELD,
            "fed_h15", "percent", _BP, 100, _NATIVE_COMPOUNDING, _NATIVE_DAY_COUNT,
        )
        for tenor in ("3M", "2Y", "10Y", "30Y")
    )
    bills = tuple(
        InstrumentSpec(
            f"US.FED.H15_TBILL_{tenor}", f"H15_TBILL_{tenor}", SemanticRole.TBILL_DISCOUNT_RATE,
            "fed_h15", "percent, discount basis", _BP, 100, _NATIVE_COMPOUNDING, _ACT_360,
        )
        for tenor in ("4W", "3M")
    )
    return (*yields, *bills)


# H.10 bilateral rates, in the direction the release quotes them: the starred
# currencies as U.S. dollars per unit, the rest as units per U.S. dollar.
_FED_H10_USD_PER = ("EUR", "GBP", "AUD", "NZD")
_FED_H10_PER_USD = (
    "CAD", "CHF", "MXN", "BRL", "ZAR", "DKK", "HKD", "MYR", "NOK",
    "SEK", "SGD", "TWD", "THB", "LKR", "CNY", "JPY", "KRW", "INR",
)
_FED_H10_INDEXES = ("BROAD", "AFE", "EME")


def _fed_h10() -> tuple[InstrumentSpec, ...]:
    """H.10 noon buying rates and nominal dollar indexes, as reference context."""

    def spec(mnemonic: str, role: SemanticRole, source_unit: str, unit: CanonicalUnit) -> InstrumentSpec:
        return InstrumentSpec(
            f"US.FED.{mnemonic}", mnemonic, role, "fed_h10", source_unit, unit,
            freshness_clock=_FED_H10_CURRENTNESS_CLOCK,
        )

    return (
        *(spec(f"H10_USD_PER_{code}", SemanticRole.EXCHANGE_RATE, f"USD per {code}", _RATIO)
          for code in _FED_H10_USD_PER),
        *(spec(f"H10_{code}_PER_USD", SemanticRole.EXCHANGE_RATE, f"{code} per USD", _RATIO)
          for code in _FED_H10_PER_USD),
        *(spec(f"H10_DOLLAR_INDEX_{name}", SemanticRole.CURRENCY_INDEX,
               "index, January 2006 = 100", _INDEX)
          for name in _FED_H10_INDEXES),
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
        # The Board's own H.15, H.10 and G.17 release packages, not FRED and
        # not the Data Download Program tool the Board is retiring. Board
        # information is public domain unless otherwise indicated, citing the
        # Board as the source (federalreserve.gov/disclaimer.htm). The H.15
        # commercial paper rates are held off (seiche.sources.
        # frb_release_package.H15_COMMERCIAL_PAPER) and are not declared here.
        SourceAdapterSpec(
            "fed_h15", _OPEN, "P1D", _FED_H15_CLOCK, _ALLOW,
            collection_cadence="PT3H",
        ),
        SourceAdapterSpec(
            "fed_h10", _OPEN, "P1D", _FED_H10_CLOCK, _ALLOW,
            collection_cadence="PT6H",
        ),
        SourceAdapterSpec(
            "fed_g17", _OPEN, "P1M", _FED_G17_CLOCK, _ALLOW,
            collection_cadence="P1D",
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
        # IOER (PRATES RESBME_N.D, 2008-10-09 to 2021-07-28), the splice leg
        # that IORB replaced on 2021-07-29. Its own role keeps it out of every
        # policy-anchor selection: it is dated history, never a current anchor.
        InstrumentSpec(
            "US.FED.IOER_DIRECT", "IOER_DIRECT", SemanticRole.RETIRED_ADMINISTERED_RATE,
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
        *_fed_h41_items(),
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
        # Policy settings as the NY Fed publishes them, read direct: the FOMC
        # target range from every EFFR row and the SRF rate from the overnight
        # repo results. Their roles are context only, so no capability reads
        # them and IORB stays the policy anchor. The FRED target-range copy
        # (US.FED.POLICY_CEILING, DFEDTARU) stays metadata-only.
        InstrumentSpec(
            "US.NYFED.FFR_TARGET_RANGE_LOWER", "FFR_TARGET_RANGE_LOWER",
            SemanticRole.POLICY_RANGE_LOWER, "nyfed_unsecured_rates",
            "percent", _BP, 100, _SIMPLE, _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.FFR_TARGET_RANGE_UPPER", "FFR_TARGET_RANGE_UPPER",
            SemanticRole.POLICY_RANGE_UPPER, "nyfed_unsecured_rates",
            "percent", _BP, 100, _SIMPLE, _ACT_360,
        ),
        InstrumentSpec(
            "US.NYFED.SRF_RATE", "SRF_RATE", SemanticRole.STANDING_REPO_RATE,
            "nyfed_facilities", "percent", _BP, 100, _SIMPLE, _ACT_360,
        ),
        *_treasury_par_yields(),
        *_fed_h15(),
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
        # Reference context from the Board's H.10 and G.17 packages: exchange
        # rates, dollar indexes and industrial production. No capability or
        # engine reads their roles.
        *_fed_h10(),
        InstrumentSpec(
            "US.FED.G17_INDUSTRIAL_PRODUCTION", "G17_INDUSTRIAL_PRODUCTION",
            SemanticRole.INDUSTRIAL_PRODUCTION, "fed_g17", "index, 2017 = 100", _INDEX,
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
