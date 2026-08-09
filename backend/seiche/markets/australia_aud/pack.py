"""AUD reference pack; secured benchmark remains explicitly provisional."""

from datetime import time

from seiche.domain.observation import (
    ConnectorClassification,
    DayCountConvention,
    RedistributionStatus,
    SemanticRole,
)
from seiche.markets.base import (
    BusinessCalendar,
    EventSpec,
    MarketPack,
    MinimumHistory,
    PackSupportStatus,
    PolicyRegime,
    PublicationClock,
    PublicationClockPrecision,
    SourceAdapterSpec,
)
from seiche.markets.calendars import australia_nsw_holidays
from seiche.markets.reference import pre_support_capabilities, rate_instrument


CALENDAR = BusinessCalendar(
    "AU-RITS",
    "Australia/Sydney",
    valid_from_year=2001,
    valid_to_year=2035,
    source_uri="https://www.rba.gov.au/schedules-events/bank-holidays/",
    holiday_provider=australia_nsw_holidays,
)
_CASH_CLOCK = PublicationClock(
    "Australia/Sydney", time(9, 20), 1, PublicationClockPrecision.SCHEDULED,
    CALENDAR.calendar_id,
)
_CLOCK = PublicationClock(
    "Australia/Sydney", None, 0, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


PACK = MarketPack(
    market_id="AU-AUD",
    monetary_area_id="AU",
    display_name="Australian dollar",
    jurisdiction_codes=("AU",),
    currency="AUD",
    local_timezone="Australia/Sydney",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(),
    policy_regime=PolicyRegime.CORRIDOR,
    source_adapters=(
        SourceAdapterSpec(
            "rba_cash", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CASH_CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "rba_policy", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "licensed_aud_market", ConnectorClassification.LICENSED, "P1D", _CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "tenant_market_data", ConnectorClassification.TENANT_PROVIDED, "P1D", _CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("AU.RBA.ES_FLOOR", "RBA_ES_FLOOR", SemanticRole.POLICY_FLOOR, "rba_policy", DayCountConvention.ACT_365),
        rate_instrument("AU.RBA.CASH_TARGET", "RBA_CASH_TARGET", SemanticRole.POLICY_TARGET, "rba_policy", DayCountConvention.ACT_365),
        rate_instrument("AU.RBA.STANDING_CEILING", "RBA_STANDING_CEILING", SemanticRole.POLICY_CEILING, "rba_policy", DayCountConvention.ACT_365),
        rate_instrument("AU.RBA.AONIA", "AONIA", SemanticRole.UNSECURED_OVERNIGHT, "rba_cash", DayCountConvention.ACT_365),
        rate_instrument("AU.MARKET.SECURED_OVERNIGHT_BETA", "SOFIA_BETA", SemanticRole.SECURED_OVERNIGHT, "licensed_aud_market", DayCountConvention.ACT_365),
        rate_instrument("AU.MARKET.BBSW_3M", "BBSW_3M", SemanticRole.CD_3M, "licensed_aud_market", DayCountConvention.ACT_365),
    ),
    capabilities=pre_support_capabilities(
        "secured-rate coverage and local validation are incomplete"
    ),
    events=(
        EventSpec("QUARTER_END", "quarter-end reporting turn", frozenset({SemanticRole.SECURED_OVERNIGHT, SemanticRole.UNSECURED_OVERNIGHT})),
        EventSpec("GOVERNMENT_SETTLEMENT", "government-security settlement", frozenset({SemanticRole.UNSECURED_OVERNIGHT})),
    ),
    calibration_id="au-aud-local-forward-v1",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
