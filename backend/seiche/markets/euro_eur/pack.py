"""Euro-area reference pack; not promoted to supported coverage."""

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
    ReserveMaintenanceSpec,
    SourceAdapterSpec,
)
from seiche.markets.calendars import target_holidays
from seiche.markets.reference import pre_support_capabilities, rate_instrument


CALENDAR = BusinessCalendar(
    "TARGET",
    "Europe/Berlin",
    valid_from_year=1999,
    valid_to_year=2035,
    source_uri="https://www.ecb.europa.eu/paym/target/target-professional-use-documents-links/target-calendar/html/index.en.html",
    holiday_provider=target_holidays,
)
_BENCHMARK_CLOCK = PublicationClock(
    "Europe/Berlin", time(8, 0), 1, PublicationClockPrecision.SCHEDULED, "TARGET"
)
_POLICY_CLOCK = PublicationClock(
    "Europe/Berlin", None, 0, PublicationClockPrecision.UPSTREAM_NATIVE, "TARGET"
)
_OPEN = ConnectorClassification.OFFICIAL_OPEN
_ALLOWED = RedistributionStatus.ALLOWED


PACK = MarketPack(
    market_id="EA-EUR",
    monetary_area_id="EA",
    display_name="Euro area",
    jurisdiction_codes=(
        "AT", "BE", "BG", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE",
        "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES",
    ),
    currency="EUR",
    local_timezone="Europe/Berlin",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(
        ReserveMaintenanceSpec(
            "EA-MINIMUM-RESERVES",
            "variable maintenance periods published by the monetary authority",
            None,
            "TARGET",
        ),
    ),
    policy_regime=PolicyRegime.FLOOR,
    source_adapters=(
        SourceAdapterSpec("ecb_benchmark", _OPEN, "P1D", _BENCHMARK_CLOCK, _ALLOWED),
        SourceAdapterSpec("ecb_policy", _OPEN, "P1D", _POLICY_CLOCK, _ALLOWED),
        SourceAdapterSpec("ecb_liquidity", _OPEN, "P1W", _POLICY_CLOCK, _ALLOWED),
        SourceAdapterSpec(
            "tenant_market_data",
            ConnectorClassification.TENANT_PROVIDED,
            "P1D",
            _POLICY_CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("EA.ECB.DFR", "ECB_DFR", SemanticRole.POLICY_FLOOR, "ecb_policy", DayCountConvention.ACT_360),
        rate_instrument("EA.ECB.MRO", "ECB_MRO", SemanticRole.POLICY_TARGET, "ecb_policy", DayCountConvention.ACT_360),
        rate_instrument("EA.ECB.MLF", "ECB_MLF", SemanticRole.POLICY_CEILING, "ecb_policy", DayCountConvention.ACT_360),
        rate_instrument("EA.ECB.ESTR", "ESTR", SemanticRole.UNSECURED_OVERNIGHT, "ecb_benchmark", DayCountConvention.ACT_360),
        InstrumentSpec(
            "EA.ECB.ESTR_VOLUME", "ESTR_VOLUME", SemanticRole.REPO_VOLUME,
            "ecb_benchmark", "EUR millions", CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
        ),
        InstrumentSpec(
            "EA.ECB.EXCESS_LIQUIDITY", "ECB_EXCESS_LIQUIDITY",
            SemanticRole.SYSTEM_LIQUIDITY, "ecb_liquidity", "EUR millions",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS,
        ),
    ),
    capabilities=pre_support_capabilities(
        "reference mappings exist, but canonical adapters and validation are not complete"
    ),
    events=(
        EventSpec("RESERVE_MAINTENANCE_END", "reserve-maintenance period end", frozenset({SemanticRole.SYSTEM_LIQUIDITY, SemanticRole.UNSECURED_OVERNIGHT})),
        EventSpec("TARGET_TURN", "settlement-calendar turn", frozenset({SemanticRole.UNSECURED_OVERNIGHT})),
    ),
    calibration_id="ea-eur-local-forward-v1",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
