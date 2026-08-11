"""NZD reference pack; mixed secured/unsecured cash data is not mislabelled."""

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
from seiche.markets.calendars import new_zealand_wellington_holidays
from seiche.markets.reference import pre_support_capabilities, rate_instrument


CALENDAR = BusinessCalendar(
    "NZ-ESAS",
    "Pacific/Auckland",
    valid_from_year=2001,
    valid_to_year=2035,
    source_uri="https://www.govt.nz/browse/work/public-holidays-and-work/public-holidays-and-anniversary-dates/",
    holiday_provider=new_zealand_wellington_holidays,
)
_POLICY_CLOCK = PublicationClock(
    "Pacific/Auckland", time(14, 0), 0, PublicationClockPrecision.SCHEDULED,
    CALENDAR.calendar_id,
)
_DATA_CLOCK = PublicationClock(
    "Pacific/Auckland", None, 1, PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


PACK = MarketPack(
    market_id="NZ-NZD",
    monetary_area_id="NZ",
    display_name="New Zealand dollar",
    jurisdiction_codes=("NZ",),
    currency="NZD",
    local_timezone="Pacific/Auckland",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(),
    policy_regime=PolicyRegime.CORRIDOR,
    source_adapters=(
        SourceAdapterSpec(
            "rbnz_policy", ConnectorClassification.OFFICIAL_OPEN, "P1D", _POLICY_CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "rbnz_wholesale", ConnectorClassification.OFFICIAL_OPEN, "P1D", _DATA_CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "licensed_nzd_market", ConnectorClassification.LICENSED, "P1D", _DATA_CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "tenant_market_data", ConnectorClassification.TENANT_PROVIDED, "P1D", _DATA_CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument("NZ.RBNZ.OVERNIGHT_DEPOSIT", "RBNZ_OVERNIGHT_DEPOSIT", SemanticRole.POLICY_FLOOR, "rbnz_wholesale", DayCountConvention.ACT_365),
        rate_instrument("NZ.RBNZ.OCR", "RBNZ_OCR", SemanticRole.POLICY_TARGET, "rbnz_policy", DayCountConvention.ACT_365),
        rate_instrument("NZ.RBNZ.OVERNIGHT_REVERSE_REPO", "RBNZ_OVERNIGHT_REVERSE_REPO", SemanticRole.POLICY_CEILING, "rbnz_wholesale", DayCountConvention.ACT_365),
        rate_instrument("NZ.MARKET.BANK_BILL_3M", "NZ_BANK_BILL_3M", SemanticRole.CD_3M, "licensed_nzd_market", DayCountConvention.ACT_365),
    ),
    capabilities=pre_support_capabilities(
        "the official overnight cash series mixes secured and unsecured trades; canonical history and role validation are incomplete"
    ),
    events=(
        EventSpec("GOVERNMENT_SETTLEMENT", "government-security settlement", frozenset({SemanticRole.POLICY_TARGET})),
        EventSpec("REPORTING_TURN", "reporting-period turn", frozenset({SemanticRole.POLICY_TARGET})),
    ),
    # v2 is an evidence-chain generation boundary after the preserved v1
    # topology incident; the model components themselves remain research-only.
    calibration_id="nz-nzd-local-forward-v2",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
