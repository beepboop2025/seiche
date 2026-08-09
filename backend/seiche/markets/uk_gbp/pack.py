"""Sterling reference pack; benchmark redistribution remains licence-aware."""

from datetime import time

from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    RedistributionStatus,
    SemanticRole,
)
from seiche.markets.base import (
    BusinessCalendar,
    EventSpec,
    InstrumentSpec,
    MarketPack,
    MinimumHistory,
    PackSupportStatus,
    PolicyRegime,
    PublicationClock,
    PublicationClockPrecision,
    SourceAdapterSpec,
)
from seiche.markets.calendars import england_wales_bank_holidays
from seiche.markets.reference import pre_support_capabilities, rate_instrument


CALENDAR = BusinessCalendar(
    "GB-STERLING-SETTLEMENT",
    "Europe/London",
    holiday_provider=england_wales_bank_holidays,
)
_SONIA_CLOCK = PublicationClock(
    "Europe/London", time(9, 0), 1, PublicationClockPrecision.EXACT,
    CALENDAR.calendar_id,
)
_POLICY_CLOCK = PublicationClock(
    "Europe/London", None, 0, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


PACK = MarketPack(
    market_id="UK-GBP",
    monetary_area_id="UK",
    display_name="United Kingdom sterling",
    jurisdiction_codes=("GB",),
    currency="GBP",
    local_timezone="Europe/London",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(),
    policy_regime=PolicyRegime.FLOOR,
    source_adapters=(
        SourceAdapterSpec(
            "boe_sonia",
            ConnectorClassification.LICENSED,
            "P1D",
            _SONIA_CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "boe_policy", ConnectorClassification.OFFICIAL_OPEN, "P1D",
            _POLICY_CLOCK, RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "licensed_sterling_market", ConnectorClassification.LICENSED, "P1D",
            _POLICY_CLOCK, RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "tenant_market_data", ConnectorClassification.TENANT_PROVIDED, "P1D",
            _POLICY_CLOCK, RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("GB.BOE.OSF_DEPOSIT", "BOE_OSF_DEPOSIT", SemanticRole.POLICY_FLOOR, "boe_policy", DayCountConvention.ACT_365),
        rate_instrument("GB.BOE.BANK_RATE", "BOE_BANK_RATE", SemanticRole.POLICY_TARGET, "boe_policy", DayCountConvention.ACT_365),
        rate_instrument("GB.BOE.OSF_LENDING", "BOE_OSF_LENDING", SemanticRole.POLICY_CEILING, "boe_policy", DayCountConvention.ACT_365),
        rate_instrument("GB.BOE.SONIA", "SONIA", SemanticRole.UNSECURED_OVERNIGHT, "boe_sonia", DayCountConvention.ACT_365),
        rate_instrument("GB.MARKET.RONIA", "RONIA", SemanticRole.SECURED_OVERNIGHT, "licensed_sterling_market", DayCountConvention.ACT_365),
        InstrumentSpec(
            "GB.BOE.RESERVE_BALANCES", "BOE_RESERVES", SemanticRole.RESERVE_BALANCES,
            "boe_policy", "GBP millions", CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
        ),
    ),
    capabilities=pre_support_capabilities(
        "reference mappings exist, but licensed/canonical adapters and validation are incomplete"
    ),
    events=(
        EventSpec("REPORTING_TURN", "reporting-period turn", frozenset({SemanticRole.SECURED_OVERNIGHT, SemanticRole.UNSECURED_OVERNIGHT})),
        EventSpec("GILT_SETTLEMENT", "government-security settlement", frozenset({SemanticRole.RESERVE_BALANCES})),
    ),
    calibration_id="gb-gbp-reference-v0",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
