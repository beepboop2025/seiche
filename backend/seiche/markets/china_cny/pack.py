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
from seiche.markets.calendars import china_public_holidays, china_working_weekends
from seiche.markets.reference import pre_support_capabilities, rate_instrument


# Mainland schedules include explicitly designated working weekends. 2026 is
# the last officially reviewed pack year; 2027 fails loud until its notice is
# loaded rather than silently reverting to Monday-Friday.
CALENDAR = BusinessCalendar(
    "CN-CFETS-SETTLEMENT",
    "Asia/Shanghai",
    valid_from_year=2001,
    valid_to_year=2026,
    source_uri="https://english.www.gov.cn/policies/latestreleases/",
    holiday_provider=china_public_holidays,
    working_day_provider=china_working_weekends,
)
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
        "canonical collection history and local validation are incomplete"
    ),
    events=(
        EventSpec("MONTH_END", "month-end liquidity turn", frozenset({SemanticRole.SYSTEM_LIQUIDITY, SemanticRole.TERM_1W})),
        EventSpec("HOLIDAY_LIQUIDITY", "holiday liquidity operation", frozenset({SemanticRole.SYSTEM_LIQUIDITY, SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP})),
        EventSpec("TAX_PAYMENT", "tax-payment liquidity drain", frozenset({SemanticRole.SYSTEM_LIQUIDITY})),
    ),
    calibration_id="cn-cny-local-forward-v1",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
