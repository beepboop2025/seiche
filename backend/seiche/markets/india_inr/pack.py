"""INR reference pack implementing the requested semantic mappings."""

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
from seiche.markets.calendars import india_maharashtra_holidays
from seiche.markets.reference import pre_support_capabilities, rate_instrument


CALENDAR = BusinessCalendar(
    "IN-MUMBAI-MONEY-MARKET",
    "Asia/Kolkata",
    valid_from_year=2001,
    valid_to_year=2035,
    source_uri="https://www.rbi.org.in/Scripts/HolidayMatrixDisplay.aspx",
    holiday_provider=india_maharashtra_holidays,
)
_CLOCK = PublicationClock(
    "Asia/Kolkata", None, 0, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)
_ACT_365 = DayCountConvention.ACT_365


PACK = MarketPack(
    market_id="IN-INR",
    monetary_area_id="IN",
    display_name="Indian rupee",
    jurisdiction_codes=("IN",),
    currency="INR",
    local_timezone="Asia/Kolkata",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(
        ReserveMaintenanceSpec(
            "IN-CRR-MAINTENANCE",
            "pack-owned cash-reserve maintenance periods and reporting dates",
            14,
            CALENDAR.calendar_id,
        ),
    ),
    policy_regime=PolicyRegime.CORRIDOR,
    source_adapters=(
        SourceAdapterSpec(
            "rbi_official", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "ccil_market", ConnectorClassification.LICENSED, "P1D", _CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "licensed_inr_market", ConnectorClassification.LICENSED, "P1D", _CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "tenant_market_data", ConnectorClassification.TENANT_PROVIDED, "P1D", _CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("IN.RBI.SDF", "RBI_SDF", SemanticRole.POLICY_FLOOR, "rbi_official", _ACT_365),
        rate_instrument("IN.RBI.POLICY_REPO", "RBI_POLICY_REPO", SemanticRole.POLICY_TARGET, "rbi_official", _ACT_365),
        rate_instrument("IN.RBI.MSF", "RBI_MSF", SemanticRole.POLICY_CEILING, "rbi_official", _ACT_365),
        rate_instrument("IN.MARKET.CALL_WAR", "CALL_WAR", SemanticRole.UNSECURED_OVERNIGHT, "rbi_official", _ACT_365),
        rate_instrument("IN.FBIL.MIBOR", "MIBOR", SemanticRole.UNSECURED_OVERNIGHT, "licensed_inr_market", _ACT_365),
        rate_instrument("IN.CCIL.TREPS", "TREPS", SemanticRole.SECURED_OVERNIGHT, "ccil_market", _ACT_365),
        rate_instrument("IN.MARKET.CP_3M", "IN_CP_3M", SemanticRole.CP_3M, "licensed_inr_market", _ACT_365),
        rate_instrument("IN.MARKET.CD_3M", "IN_CD_3M", SemanticRole.CD_3M, "licensed_inr_market", _ACT_365),
        rate_instrument("IN.RBI.TBILL_3M", "IN_TBILL_3M", SemanticRole.TBILL_3M, "rbi_official", _ACT_365),
        rate_instrument("IN.MARKET.FX_FORWARD_BASIS", "INR_FX_BASIS", SemanticRole.FX_SWAP_BASIS, "licensed_inr_market", _ACT_365),
        InstrumentSpec(
            "IN.RBI.SYSTEM_LIQUIDITY", "RBI_SYSTEM_LIQUIDITY", SemanticRole.SYSTEM_LIQUIDITY,
            "rbi_official", "INR crore", CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
        ),
        InstrumentSpec(
            "IN.RBI.CASH_BALANCES", "RBI_CASH_BALANCES", SemanticRole.RESERVE_BALANCES,
            "rbi_official", "INR crore", CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
        ),
        InstrumentSpec(
            "IN.RBI.TRIPARTY_REPO_VOLUME", "RBI_TRIPARTY_REPO_VOLUME",
            SemanticRole.REPO_VOLUME, "rbi_official", "INR crore",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
        ),
        InstrumentSpec(
            "IN.RBI.FACILITY_TAKEUP", "RBI_FACILITY_TAKEUP",
            SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP, "rbi_official", "INR crore",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
        ),
        InstrumentSpec(
            "IN.GOVERNMENT.CASH_BALANCE", "IN_GOVERNMENT_CASH",
            SemanticRole.GOVERNMENT_CASH_BALANCE, "rbi_official", "INR crore",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
        ),
    ),
    capabilities=pre_support_capabilities(
        "canonical collection history and point-in-time validation are incomplete"
    ),
    events=(
        EventSpec("RESERVE_MAINTENANCE_END", "reserve-maintenance period end", frozenset({SemanticRole.SYSTEM_LIQUIDITY, SemanticRole.UNSECURED_OVERNIGHT})),
        EventSpec("TAX_PAYMENT", "tax-payment liquidity drain", frozenset({SemanticRole.GOVERNMENT_CASH_BALANCE, SemanticRole.SYSTEM_LIQUIDITY})),
        EventSpec("GOVERNMENT_AUCTION", "government-security auction and settlement", frozenset({SemanticRole.GOVERNMENT_CASH_BALANCE, SemanticRole.TBILL_3M})),
        EventSpec("REPORTING_TURN", "reporting-period turn", frozenset({SemanticRole.SECURED_OVERNIGHT, SemanticRole.UNSECURED_OVERNIGHT})),
    ),
    calibration_id="in-inr-local-forward-v1",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
