"""Japanese-yen reference pack with dated official settlement holidays."""

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
    ReserveMaintenanceSpec,
    SourceAdapterSpec,
)
from seiche.markets.calendars import japan_bank_holidays
from seiche.markets.reference import pre_support_capabilities, rate_instrument


CALENDAR = BusinessCalendar(
    "JP-BOJ-NET",
    "Asia/Tokyo",
    valid_from_year=1998,
    valid_to_year=2035,
    source_uri="https://www.boj.or.jp/en/about/outline/holi.htm",
    holiday_provider=japan_bank_holidays,
)
_CLOCK = PublicationClock(
    "Asia/Tokyo", None, 1, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


PACK = MarketPack(
    market_id="JP-JPY",
    monetary_area_id="JP",
    display_name="Japanese yen",
    jurisdiction_codes=("JP",),
    currency="JPY",
    local_timezone="Asia/Tokyo",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(
        ReserveMaintenanceSpec(
            "JP-RESERVE-MAINTENANCE",
            "monthly reserve-maintenance periods; exact boundaries come from the pack calendar",
            None,
            CALENDAR.calendar_id,
        ),
    ),
    policy_regime=PolicyRegime.TIERED,
    source_adapters=(
        SourceAdapterSpec(
            "boj_rates", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "boj_accounts", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "tenant_market_data", ConnectorClassification.TENANT_PROVIDED, "P1D", _CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("JP.BOJ.POLICY_TARGET", "BOJ_POLICY_TARGET", SemanticRole.POLICY_TARGET, "boj_rates", DayCountConvention.ACT_365),
        rate_instrument("JP.BOJ.COMPLEMENTARY_DEPOSIT", "BOJ_COMPLEMENTARY_DEPOSIT", SemanticRole.POLICY_FLOOR, "boj_rates", DayCountConvention.ACT_365),
        rate_instrument("JP.BOJ.BASIC_LOAN", "BOJ_BASIC_LOAN", SemanticRole.POLICY_CEILING, "boj_rates", DayCountConvention.ACT_365),
        rate_instrument("JP.BOJ.TONA", "TONA", SemanticRole.UNSECURED_OVERNIGHT, "boj_rates", DayCountConvention.ACT_365),
        InstrumentSpec(
            "JP.BOJ.CURRENT_ACCOUNTS", "BOJ_CURRENT_ACCOUNTS", SemanticRole.RESERVE_BALANCES,
            "boj_accounts", "JPY 100 millions", CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
            100,
        ),
    ),
    capabilities=pre_support_capabilities(
        "tier-aware normalization, canonical adapters, and validation are incomplete"
    ),
    events=(
        EventSpec("RESERVE_MAINTENANCE_END", "reserve-maintenance period end", frozenset({SemanticRole.RESERVE_BALANCES, SemanticRole.UNSECURED_OVERNIGHT})),
        EventSpec("FISCAL_YEAR_END", "fiscal-year reporting turn", frozenset({SemanticRole.UNSECURED_OVERNIGHT})),
    ),
    calibration_id="jp-jpy-local-forward-v1",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
