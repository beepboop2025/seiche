"""South-Korean-won reference pack with a bounded BOK-Wire+ calendar."""

from datetime import date, time

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
    ReserveMaintenanceSpec,
    SourceAdapterSpec,
)
from seiche.markets.calendars import country_holiday_provider
from seiche.markets.reference import pre_support_capabilities, rate_instrument

_korean_public_holidays = country_holiday_provider("KR")


def _bok_wire_holidays(year: int) -> frozenset[date]:
    """Public holidays plus the BOK-Wire+ specific Labor Day closure.

    The BOK-Wire+ operating regulation closes the system on public holidays,
    Saturdays and May 1.  May 1 only became a general Korean public holiday in
    2026, so it must be added explicitly for earlier pack years.
    """

    return _korean_public_holidays(year) | {date(year, 5, 1)}


# The live BOK holiday schedule is reviewed one year at a time.  The calendar
# therefore fails closed after the latest reviewed schedule instead of
# guessing future election and temporary-public-holiday closures.
CALENDAR = BusinessCalendar(
    "KR-BOK-WIRE",
    "Asia/Seoul",
    valid_from_year=1998,
    valid_to_year=2026,
    source_uri="https://www.bok.or.kr/eng/main/contents.do?menuNo=400373",
    holiday_provider=_bok_wire_holidays,
)

_BOK_POLICY_CLOCK = PublicationClock(
    "Asia/Seoul",
    None,
    0,
    PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)
_BOK_MARKET_CLOCK = PublicationClock(
    "Asia/Seoul",
    None,
    1,
    PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)
_KOFR_CLOCK = PublicationClock(
    "Asia/Seoul",
    time(11, 0),
    1,
    PublicationClockPrecision.EXACT,
    CALENDAR.calendar_id,
)


PACK = MarketPack(
    market_id="KR-KRW",
    monetary_area_id="KR",
    display_name="South Korean won",
    jurisdiction_codes=("KR",),
    currency="KRW",
    local_timezone="Asia/Seoul",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(
        ReserveMaintenanceSpec(
            "KR-RESERVE-MAINTENANCE",
            "monthly reserve calculation and subsequent maintenance cycle; "
            "exact dated boundaries are not yet encoded",
            None,
            CALENDAR.calendar_id,
        ),
    ),
    policy_regime=PolicyRegime.CORRIDOR,
    source_adapters=(
        SourceAdapterSpec(
            "bok_ecos_policy",
            ConnectorClassification.OFFICIAL_OPEN,
            "P1D",
            _BOK_POLICY_CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "bok_ecos_money_market",
            ConnectorClassification.OFFICIAL_OPEN,
            "P1D",
            _BOK_MARKET_CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        SourceAdapterSpec(
            "bok_facilities",
            ConnectorClassification.OFFICIAL_OPEN,
            "P1D",
            _BOK_POLICY_CLOCK,
            RedistributionStatus.ALLOWED,
        ),
        # KSD publishes KOFR openly, but its legal notice does not grant raw
        # republication rights.  Keep it descriptive and out of calculations
        # until KSD supplies an affirmative redistribution licence.
        SourceAdapterSpec(
            "ksd_kofr",
            ConnectorClassification.OFFICIAL_OPEN,
            "P1D",
            _KOFR_CLOCK,
            RedistributionStatus.METADATA_ONLY,
        ),
        SourceAdapterSpec(
            "licensed_krw_market",
            ConnectorClassification.LICENSED,
            "P1D",
            _BOK_MARKET_CLOCK,
            RedistributionStatus.DERIVED_ONLY,
        ),
        SourceAdapterSpec(
            "tenant_market_data",
            ConnectorClassification.TENANT_PROVIDED,
            "P1D",
            _BOK_MARKET_CLOCK,
            RedistributionStatus.PROHIBITED,
        ),
    ),
    instruments=(
        rate_instrument(
            "KR.BOK.BASE_RATE",
            "BOK_BASE_RATE",
            SemanticRole.POLICY_TARGET,
            "bok_ecos_policy",
            DayCountConvention.ACT_365,
        ),
        rate_instrument(
            "KR.BOK.LIQUIDITY_ADJUSTMENT_DEPOSIT",
            "BOK_LIQUIDITY_ADJUSTMENT_DEPOSIT",
            SemanticRole.POLICY_FLOOR,
            "bok_facilities",
            DayCountConvention.ACT_365,
        ),
        rate_instrument(
            "KR.BOK.LIQUIDITY_ADJUSTMENT_LOAN",
            "BOK_LIQUIDITY_ADJUSTMENT_LOAN",
            SemanticRole.POLICY_CEILING,
            "bok_facilities",
            DayCountConvention.ACT_365,
        ),
        rate_instrument(
            "KR.BOK.CALL_OVERNIGHT_ALL",
            "CALL_OVERNIGHT_ALL",
            SemanticRole.UNSECURED_OVERNIGHT,
            "bok_ecos_money_market",
            DayCountConvention.ACT_365,
        ),
        rate_instrument(
            "KR.KSD.KOFR",
            "KOFR",
            SemanticRole.SECURED_OVERNIGHT,
            "ksd_kofr",
            DayCountConvention.ACT_365,
        ),
        rate_instrument(
            "KR.KOFIA.CD_91D",
            "CD_91D",
            SemanticRole.CD_3M,
            "licensed_krw_market",
            DayCountConvention.ACT_365,
        ),
        rate_instrument(
            "KR.KOFIA.CP_91D",
            "CP_91D",
            SemanticRole.CP_3M,
            "licensed_krw_market",
            DayCountConvention.ACT_365,
        ),
        rate_instrument(
            "KR.KFB.KORIBOR_3M",
            "KORIBOR_3M",
            SemanticRole.TERM_3M,
            "licensed_krw_market",
            DayCountConvention.ACT_365,
        ),
        rate_instrument(
            "KR.LICENSED.FX_BASIS",
            "KRW_FX_BASIS",
            SemanticRole.FX_SWAP_BASIS,
            "licensed_krw_market",
            DayCountConvention.ACT_365,
        ),
    ),
    capabilities=pre_support_capabilities(
        "canonical collection history, corridor adapters, and local validation are incomplete"
    ),
    events=(
        EventSpec(
            "MONETARY_POLICY_DECISION",
            "Bank of Korea Base Rate decision",
            frozenset(
                {
                    SemanticRole.POLICY_FLOOR,
                    SemanticRole.POLICY_TARGET,
                    SemanticRole.POLICY_CEILING,
                    SemanticRole.UNSECURED_OVERNIGHT,
                    SemanticRole.SECURED_OVERNIGHT,
                }
            ),
        ),
        EventSpec(
            "RESERVE_MAINTENANCE_END",
            "reserve-maintenance period end",
            frozenset(
                {
                    SemanticRole.UNSECURED_OVERNIGHT,
                    SemanticRole.SECURED_OVERNIGHT,
                }
            ),
        ),
        EventSpec(
            "MONTH_END",
            "month-end liquidity turn",
            frozenset(
                {
                    SemanticRole.UNSECURED_OVERNIGHT,
                    SemanticRole.SECURED_OVERNIGHT,
                    SemanticRole.TERM_3M,
                }
            ),
        ),
    ),
    calibration_id="kr-krw-local-forward-v1",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
