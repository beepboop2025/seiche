"""HKD reference pack, explicitly separate from mainland-CNY markets."""

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
from seiche.markets.reference import pre_support_capabilities, rate_instrument


CALENDAR = BusinessCalendar("HK-HKD-CHATS", "Asia/Hong_Kong")
_CLOCK = PublicationClock(
    "Asia/Hong_Kong", None, 0, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


PACK = MarketPack(
    market_id="HK-HKD",
    monetary_area_id="HK",
    display_name="Hong Kong dollar",
    jurisdiction_codes=("HK",),
    currency="HKD",
    local_timezone="Asia/Hong_Kong",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(),
    policy_regime=PolicyRegime.CURRENCY_BOARD,
    source_adapters=(
        SourceAdapterSpec(
            "hkma_official", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "tma_benchmarks", ConnectorClassification.LICENSED, "P1D", _CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "licensed_hkd_market", ConnectorClassification.LICENSED, "P1D", _CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "tenant_market_data", ConnectorClassification.TENANT_PROVIDED, "P1D", _CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("HK.TMA.HONIA", "HONIA", SemanticRole.UNSECURED_OVERNIGHT, "tma_benchmarks", DayCountConvention.ACT_365),
        rate_instrument("HK.HKAB.HIBOR_1W", "HIBOR_1W", SemanticRole.TERM_1W, "licensed_hkd_market", DayCountConvention.ACT_365),
        rate_instrument("HK.HKAB.HIBOR_1M", "HIBOR_1M", SemanticRole.TERM_1M, "licensed_hkd_market", DayCountConvention.ACT_365),
        rate_instrument("HK.HKAB.HIBOR_3M", "HIBOR_3M", SemanticRole.TERM_3M, "licensed_hkd_market", DayCountConvention.ACT_365),
        rate_instrument("HK.HKMA.BASE_RATE", "HKMA_BASE_RATE", SemanticRole.CENTRAL_BANK_FACILITY_RATE, "hkma_official", DayCountConvention.ACT_365),
        rate_instrument("HK.MARKET.FX_SWAP_BASIS", "HKD_FX_BASIS", SemanticRole.FX_SWAP_BASIS, "licensed_hkd_market", DayCountConvention.ACT_365),
        InstrumentSpec(
            "HK.HKMA.AGGREGATE_BALANCE", "HKMA_AGGREGATE_BALANCE",
            SemanticRole.SYSTEM_LIQUIDITY, "hkma_official", "HKD millions",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
        ),
    ),
    capabilities=pre_support_capabilities(
        "currency-board calibration, settlement calendar, adapters, and validation are incomplete"
    ),
    events=(
        EventSpec("CURRENCY_BOARD_OPERATION", "currency-board market operation", frozenset({SemanticRole.SYSTEM_LIQUIDITY, SemanticRole.FX_SWAP_BASIS})),
        EventSpec("REPORTING_TURN", "reporting-period turn", frozenset({SemanticRole.UNSECURED_OVERNIGHT, SemanticRole.TERM_1W})),
    ),
    calibration_id="hk-hkd-reference-v0",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
