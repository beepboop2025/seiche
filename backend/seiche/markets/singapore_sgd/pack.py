"""SGD reference pack for an exchange-rate-centred monetary regime."""

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
from seiche.markets.reference import pre_support_capabilities, rate_instrument


CALENDAR = BusinessCalendar("SG-MEPS-PLUS", "Asia/Singapore")
_SORA_CLOCK = PublicationClock(
    "Asia/Singapore", time(9, 0), 1, PublicationClockPrecision.SCHEDULED,
    CALENDAR.calendar_id,
)
_CLOCK = PublicationClock(
    "Asia/Singapore", None, 0, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


PACK = MarketPack(
    market_id="SG-SGD",
    monetary_area_id="SG",
    display_name="Singapore dollar",
    jurisdiction_codes=("SG",),
    currency="SGD",
    local_timezone="Asia/Singapore",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(),
    policy_regime=PolicyRegime.EXCHANGE_RATE_TARGETING,
    source_adapters=(
        SourceAdapterSpec(
            "mas_sora", ConnectorClassification.OFFICIAL_OPEN, "P1D", _SORA_CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "mas_rates", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "licensed_sgd_market", ConnectorClassification.LICENSED, "P1D", _CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "tenant_market_data", ConnectorClassification.TENANT_PROVIDED, "P1D", _CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("SG.MAS.STANDING_DEPOSIT", "MAS_STANDING_DEPOSIT", SemanticRole.POLICY_FLOOR, "mas_rates", DayCountConvention.ACT_365),
        rate_instrument("SG.MAS.STANDING_BORROWING", "MAS_STANDING_BORROWING", SemanticRole.POLICY_CEILING, "mas_rates", DayCountConvention.ACT_365),
        rate_instrument("SG.MAS.SORA", "SORA", SemanticRole.UNSECURED_OVERNIGHT, "mas_sora", DayCountConvention.ACT_365),
        rate_instrument("SG.MAS.TBILL_3M", "SG_TBILL_3M", SemanticRole.TBILL_3M, "mas_rates", DayCountConvention.ACT_365),
        rate_instrument("SG.MARKET.CP_3M", "SG_CP_3M", SemanticRole.CP_3M, "licensed_sgd_market", DayCountConvention.ACT_365),
        rate_instrument("SG.MARKET.FX_SWAP_BASIS", "SGD_FX_BASIS", SemanticRole.FX_SWAP_BASIS, "licensed_sgd_market", DayCountConvention.ACT_365),
    ),
    capabilities=pre_support_capabilities(
        "exchange-rate-regime calibration, settlement calendar, adapters, and validation are incomplete"
    ),
    events=(
        EventSpec("MAS_POLICY_REVIEW", "exchange-rate policy review", frozenset({SemanticRole.FX_SWAP_BASIS, SemanticRole.UNSECURED_OVERNIGHT})),
        EventSpec("REPORTING_TURN", "reporting-period turn", frozenset({SemanticRole.UNSECURED_OVERNIGHT})),
    ),
    calibration_id="sg-sgd-reference-v0",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
