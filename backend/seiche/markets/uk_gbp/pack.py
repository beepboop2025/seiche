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
from seiche import source_decisions
from seiche.markets.calendars import uk_england_holidays
from seiche.markets.reference import pre_support_capabilities, rate_instrument
from seiche.markets.funding_reference import extend_reference_pack


CALENDAR = BusinessCalendar(
    "GB-STERLING-SETTLEMENT",
    "Europe/London",
    valid_from_year=1998,
    valid_to_year=2035,
    source_uri="https://www.gov.uk/bank-holidays",
    holiday_provider=uk_england_holidays,
)
_SONIA_CLOCK = PublicationClock(
    "Europe/London", time(9, 0), 1, PublicationClockPrecision.EXACT,
    CALENDAR.calendar_id,
)
# Raw SONIA is served under the Bank of England's open licence by the owner's
# 2026-11-08 decision. The open copy is the Bank's Database, available by
# 10:00 London time on the business day after first publication, so its
# clock replaces the 09:00 benchmark publication. Without the recorded
# decision the adapter reverts to licensed, derived-only readings.
_SONIA_OPEN = source_decisions.allows(source_decisions.BOE_SONIA)
_SONIA_DATABASE_CLOCK = PublicationClock(
    "Europe/London", time(10, 0), 1, PublicationClockPrecision.SCHEDULED,
    CALENDAR.calendar_id,
)
_POLICY_CLOCK = PublicationClock(
    "Europe/London", None, 0, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


PACK = extend_reference_pack(MarketPack(
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
            ConnectorClassification.OFFICIAL_OPEN if _SONIA_OPEN else ConnectorClassification.LICENSED,
            "P1D",
            _SONIA_DATABASE_CLOCK if _SONIA_OPEN else _SONIA_CLOCK,
            RedistributionStatus.ALLOWED if _SONIA_OPEN else RedistributionStatus.DERIVED_ONLY,
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
    calibration_id="uk-gbp-local-forward-v1",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
))
