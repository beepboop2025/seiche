"""INR reference pack implementing the requested semantic mappings."""

from dataclasses import replace

from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    RedistributionStatus,
    RateCompounding,
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
# RBI Communication Policy, Annex 5 (Daily Releases), distinguishes MMO's
# previous-day observations from same-day liquidity-operation announcements:
# https://www.rbi.org.in/Scripts/CommunicationPolicy.aspx
# This is an expected release lag on the Mumbai calendar, not a publication
# receipt. Intraday timing stays native/estimated; historical row clocks stay
# untouched. RBI-home policy repo/T-bill observations keep their own clock.
_MMO_FRESHNESS_CLOCK = PublicationClock(
    "Asia/Kolkata", None, 1, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)
_ACT_365 = DayCountConvention.ACT_365

CURVE_TENORS = (1, 2, 3, 5, 7, 10, 14, 30, 40)
BENCHMARK_TENORS = (1, 2, 3, 5, 7, 10, 14, 15, 30, 40)
RBI_HOME_TENORS = (3, 5, 10, 15, 30)
LIQUIDITY_SERIES = (
    "SDF_TODAY", "MSF_TODAY", "VRR_TODAY", "VRRR_TODAY",
    "FIXED_REPO_TODAY", "FIXED_REVERSE_REPO_TODAY",
    "SDF_OUTSTANDING", "MSF_OUTSTANDING", "VRR_OUTSTANDING", "VRRR_OUTSTANDING",
    "FIXED_REPO_OUTSTANDING", "FIXED_REVERSE_REPO_OUTSTANDING",
    "NET_INJECTION_TODAY", "NET_INJECTION_OUTSTANDING", "SLF",
    "DURABLE_LIQUIDITY", "RESERVE_REQUIREMENT",
)


def _amount(name: str, *, adapter: str = "rbi_official") -> InstrumentSpec:
    role = SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP
    if name.startswith(("NET_INJECTION", "WSS_NET_INJECTION")):
        role = SemanticRole.NET_LIQUIDITY_OPERATIONS
    elif name == "DURABLE_LIQUIDITY":
        role = SemanticRole.DURABLE_LIQUIDITY
    elif name == "RESERVE_REQUIREMENT":
        role = SemanticRole.RESERVE_REQUIREMENT
    elif name.startswith("OMO_"):
        role = SemanticRole.CENTRAL_BANK_ASSET_TRANSACTIONS
    elif name.startswith(("CD_", "CP_")):
        role = SemanticRole.TERM_FUNDING_ISSUANCE if name.endswith("ISSUED") else SemanticRole.TERM_FUNDING_OUTSTANDING
    return InstrumentSpec(
        f"IN.RBI.{name}", f"RBI_{name}", role,
        adapter, "INR crore", CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
        freshness_clock=_MMO_FRESHNESS_CLOCK if adapter == "rbi_official" else None,
    )


def _yield(instrument: str, mnemonic: str, adapter: str) -> InstrumentSpec:
    return rate_instrument(instrument, mnemonic, SemanticRole.SOVEREIGN_YIELD,
                           adapter, DayCountConvention.SOURCE_NATIVE,
                           compounding=RateCompounding.SOURCE_NATIVE)


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
            # The rolling MMO page may arrive after a collection attempt.
            # Retry acquisition hourly without changing its daily observation
            # cadence, inferred publication clock, or first-seen knowledge time.
            collection_cadence="PT1H",
        ),
        SourceAdapterSpec(
            "ccil_market", ConnectorClassification.LICENSED, "P1D", _CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "rbi_rates_weekly", ConnectorClassification.OFFICIAL_OPEN, "P1W", _CLOCK,
            RedistributionStatus.ALLOWED, collection_cadence="P1D",
        ),
        SourceAdapterSpec(
            "rbi_policy", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "rbi_liquidity_weekly", ConnectorClassification.OFFICIAL_OPEN, "P1W", _CLOCK,
            RedistributionStatus.ALLOWED, collection_cadence="P1D",
        ),
        SourceAdapterSpec(
            "rbi_credit_fortnightly", ConnectorClassification.OFFICIAL_OPEN, "P2W", _CLOCK,
            RedistributionStatus.ALLOWED, collection_cadence="P1D",
        ),
        SourceAdapterSpec(
            "rbi_sovereign", ConnectorClassification.OFFICIAL_OPEN, "P1D", _CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "rbi_monthly_curve", ConnectorClassification.OFFICIAL_OPEN, "P1M", _CLOCK,
            RedistributionStatus.ALLOWED, collection_cadence="P1W",
        ),
        SourceAdapterSpec(
            "rbi_auctions", ConnectorClassification.OFFICIAL_OPEN, "P1W", _CLOCK,
            RedistributionStatus.ALLOWED, collection_cadence="P1D",
        ),
        SourceAdapterSpec(
            "ccil_curve", ConnectorClassification.LICENSED, "P1D", _CLOCK,
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
        replace(rate_instrument("IN.RBI.SDF", "RBI_SDF", SemanticRole.POLICY_FLOOR, "rbi_official", _ACT_365), freshness_clock=_MMO_FRESHNESS_CLOCK),
        rate_instrument("IN.RBI.POLICY_REPO", "RBI_POLICY_REPO", SemanticRole.POLICY_TARGET, "rbi_official", _ACT_365),
        replace(rate_instrument("IN.RBI.MSF", "RBI_MSF", SemanticRole.POLICY_CEILING, "rbi_official", _ACT_365), freshness_clock=_MMO_FRESHNESS_CLOCK),
        replace(rate_instrument("IN.MARKET.CALL_WAR", "CALL_WAR", SemanticRole.UNSECURED_OVERNIGHT, "rbi_official", _ACT_365), freshness_clock=_MMO_FRESHNESS_CLOCK),
        rate_instrument("IN.FBIL.MIBOR", "MIBOR", SemanticRole.UNSECURED_OVERNIGHT, "licensed_inr_market", _ACT_365),
        rate_instrument("IN.CCIL.TREPS", "TREPS", SemanticRole.SECURED_OVERNIGHT, "ccil_market", _ACT_365),
        rate_instrument("IN.MARKET.CP_3M", "IN_CP_3M", SemanticRole.CP_3M, "licensed_inr_market", _ACT_365),
        rate_instrument("IN.MARKET.CD_3M", "IN_CD_3M", SemanticRole.CD_3M, "licensed_inr_market", _ACT_365),
        rate_instrument("IN.RBI.TBILL_3M", "IN_TBILL_3M", SemanticRole.TBILL_3M, "rbi_official", _ACT_365),
        replace(rate_instrument("IN.RBI.TREPS_WAR", "RBI_TREPS_WAR", SemanticRole.SECURED_OVERNIGHT, "rbi_official", _ACT_365), freshness_clock=_MMO_FRESHNESS_CLOCK),
        replace(rate_instrument("IN.RBI.MARKET_REPO_WAR", "RBI_MARKET_REPO_WAR", SemanticRole.SECURED_OVERNIGHT, "rbi_official", _ACT_365), freshness_clock=_MMO_FRESHNESS_CLOCK),
        *(_amount(name) for name in LIQUIDITY_SERIES),
        *(_amount(name, adapter="rbi_liquidity_weekly") for name in
          ("OMO_SALES", "OMO_PURCHASES", "WSS_NET_INJECTION", "WSS_FIXED_REPO", "WSS_FIXED_REVERSE_REPO",
           "WSS_VRR", "WSS_VRRR", "WSS_MSF", "WSS_SDF", "WSS_SLF")),
        *(rate_instrument(f"IN.RBI.{kind}_RATE_{bound}", f"RBI_{kind}_RATE_{bound}", SemanticRole.TERM_FUNDING_RATE,
                          "rbi_credit_fortnightly", DayCountConvention.SOURCE_NATIVE, compounding=RateCompounding.SOURCE_NATIVE)
          for kind in ("CD", "CP") for bound in ("LOW", "HIGH")),
        *(_amount(f"{kind}_{measure}", adapter="rbi_credit_fortnightly")
          for kind in ("CD", "CP") for measure in ("ISSUED", "OUTSTANDING")),
        rate_instrument("IN.RBI.POLICY_REPO_DECISION", "RBI_POLICY_REPO_DECISION", SemanticRole.POLICY_TARGET, "rbi_policy", _ACT_365),
        rate_instrument("IN.RBI.SDF_DECISION", "RBI_SDF_DECISION", SemanticRole.POLICY_FLOOR, "rbi_policy", _ACT_365),
        rate_instrument("IN.RBI.MSF_DECISION", "RBI_MSF_DECISION", SemanticRole.POLICY_CEILING, "rbi_policy", _ACT_365),
        rate_instrument("IN.RBI.POLICY_REPO_WEEKLY", "RBI_POLICY_REPO_WEEKLY", SemanticRole.POLICY_TARGET, "rbi_rates_weekly", _ACT_365),
        InstrumentSpec("IN.RBI.CRR", "RBI_CRR", SemanticRole.RESERVE_REQUIREMENT_RATIO,
                       "rbi_rates_weekly", "percent", CanonicalUnit.BASIS_POINTS, 100),
        *(rate_instrument(f"IN.RBI.TBILL_{tenor}D_WEEKLY", f"RBI_TBILL_{tenor}D_WEEKLY", role,
                          "rbi_rates_weekly", _ACT_365)
          for tenor, role in ((91, SemanticRole.TBILL_3M), (182, SemanticRole.TBILL_6M), (364, SemanticRole.TBILL_12M))),
        *(_yield(f"IN.RBI.GSEC_BENCHMARK_{tenor}Y", f"RBI_GSEC_BENCHMARK_{tenor}Y", "rbi_sovereign")
          for tenor in BENCHMARK_TENORS),
        *(_yield(f"IN.RBI.SGL_MONTHLY_{tenor}Y", f"RBI_SGL_MONTHLY_{tenor}Y", "rbi_monthly_curve")
          for tenor in CURVE_TENORS),
        *(_yield(f"IN.RBI.GSEC_AUCTION_{tenor}Y", f"RBI_GSEC_AUCTION_{tenor}Y", "rbi_auctions")
          for tenor in CURVE_TENORS),
        *(_yield(f"IN.CCIL.ZERO_{tenor}Y", f"CCIL_ZERO_{tenor}Y", "ccil_curve")
          for tenor in CURVE_TENORS),
        rate_instrument("IN.MARKET.FX_FORWARD_BASIS", "INR_FX_BASIS", SemanticRole.FX_SWAP_BASIS, "licensed_inr_market", _ACT_365),
        InstrumentSpec(
            "IN.RBI.SYSTEM_LIQUIDITY", "RBI_SYSTEM_LIQUIDITY", SemanticRole.SYSTEM_LIQUIDITY,
            "rbi_official", "INR crore", CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
            freshness_clock=_MMO_FRESHNESS_CLOCK,
        ),
        InstrumentSpec(
            "IN.RBI.CASH_BALANCES", "RBI_CASH_BALANCES", SemanticRole.RESERVE_BALANCES,
            "rbi_official", "INR crore", CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
            freshness_clock=_MMO_FRESHNESS_CLOCK,
        ),
        InstrumentSpec(
            "IN.RBI.TRIPARTY_REPO_VOLUME", "RBI_TRIPARTY_REPO_VOLUME",
            SemanticRole.REPO_VOLUME, "rbi_official", "INR crore",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
            freshness_clock=_MMO_FRESHNESS_CLOCK,
        ),
        InstrumentSpec(
            "IN.RBI.FACILITY_TAKEUP", "RBI_FACILITY_TAKEUP",
            SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP, "rbi_official", "INR crore",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
            freshness_clock=_MMO_FRESHNESS_CLOCK,
        ),
        InstrumentSpec(
            "IN.GOVERNMENT.CASH_BALANCE", "IN_GOVERNMENT_CASH",
            SemanticRole.GOVERNMENT_CASH_BALANCE, "rbi_official", "INR crore",
            CanonicalUnit.LOCAL_CURRENCY_MILLIONS, 10,
            freshness_clock=_MMO_FRESHNESS_CLOCK,
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
