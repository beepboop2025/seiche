"""Mainland-CNY reference pack, intentionally separate from the HKD market."""

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
from seiche.markets.reference import pre_support_capabilities, rate_instrument


# Mainland settlement weekends/holidays are announced annually and can include
# working weekends. A generic Monday-Friday fallback would be wrong, so the
# provider remains absent until dated official schedules are loaded.
CALENDAR = BusinessCalendar("CN-CFETS-SETTLEMENT", "Asia/Shanghai")
_CLOCK = PublicationClock(
    "Asia/Shanghai", None, 0, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


PACK = MarketPack(
    market_id="CN-CNY",
    monetary_area_id="CN",
    display_name="Mainland Chinese renminbi",
    jurisdiction_codes=("CN",),
    currency="CNY",
    local_timezone="Asia/Shanghai",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(
        ReserveMaintenanceSpec(
            "CN-RRR-ASSESSMENT",
            "pack-owned reserve-requirement assessment periods",
            None,
            CALENDAR.calendar_id,
        ),
    ),
    policy_regime=PolicyRegime.QUANTITY_TARGETING,
    source_adapters=(
        SourceAdapterSpec(
            "pbc_operations", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "cfets_rates", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.METADATA_ONLY,
        ),
        SourceAdapterSpec(
            "licensed_cny_market", ConnectorClassification.LICENSED, "P1D", _CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "tenant_market_data", ConnectorClassification.TENANT_PROVIDED, "P1D", _CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("CN.PBC.OMO_7D", "PBC_OMO_7D", SemanticRole.POLICY_TARGET, "pbc_operations", DayCountConvention.ACT_365),
        rate_instrument("CN.CFETS.DR007", "DR007", SemanticRole.TERM_1W, "cfets_rates", DayCountConvention.ACT_365),
        rate_instrument("CN.CFETS.SHIBOR_ON", "SHIBOR_ON", SemanticRole.UNSECURED_OVERNIGHT, "cfets_rates", DayCountConvention.ACT_365),
        rate_instrument("CN.PBC.SLF_RATE", "PBC_SLF_RATE", SemanticRole.CENTRAL_BANK_FACILITY_RATE, "pbc_operations", DayCountConvention.ACT_365),
        InstrumentSpec(
            "CN.PBC.NET_LIQUIDITY", "PBC_NET_LIQUIDITY", SemanticRole.SYSTEM_LIQUIDITY,
            "pbc_operations", "CNY millions", CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
        ),
        InstrumentSpec(
            "CN.PBC.FACILITY_TAKEUP", "PBC_FACILITY_TAKEUP",
            SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP, "pbc_operations", "CNY millions",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
        ),
    ),
    capabilities=pre_support_capabilities(
        "annual working-weekend calendar, canonical adapters, and local validation are incomplete"
    ),
    events=(
        EventSpec("MONTH_END", "month-end liquidity turn", frozenset({SemanticRole.SYSTEM_LIQUIDITY, SemanticRole.TERM_1W})),
        EventSpec("HOLIDAY_LIQUIDITY", "holiday liquidity operation", frozenset({SemanticRole.SYSTEM_LIQUIDITY, SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP})),
        EventSpec("TAX_PAYMENT", "tax-payment liquidity drain", frozenset({SemanticRole.SYSTEM_LIQUIDITY})),
    ),
    calibration_id="cn-cny-reference-v0",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
