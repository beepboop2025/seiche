"""Source-native Bank of Canada reference observations; no invented holiday rows."""

from decimal import Decimal
from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    RedistributionStatus,
    SemanticRole as R,
)
from seiche.markets.base import (
    BusinessCalendar,
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

TERMS = "https://www.bankofcanada.ca/terms/"
ATTRIBUTION = (
    "Source: Bank of Canada. Original information is available free of charge on the Bank of Canada website. "
    "Seiche normalizes units and calculates spreads and statistics; these modifications are not Bank of Canada analysis or endorsement."
)
SOURCE = {
    "publisher": "Bank of Canada",
    "source_url": "https://www.bankofcanada.ca/rates/interest-rates/corra/",
    "terms_url": TERMS,
    "rights_url": TERMS,
    "terms_read_on": "2026-11-14",
    "attribution": ATTRIBUTION,
    "terms": "Free use and distribution with attribution, accuracy, changes identified and notice of free source availability before sale.",
    "publication_rule": "Valet gives observation dates, not per-row publication receipts. Publication time remains unknown; capture time is retained.",
}
SERIES = (
    (
        "AVG.INTWO",
        "CORRA",
        R.SECURED_OVERNIGHT,
        "rate",
        "Canadian Overnight Repo Rate Average (CORRA)",
    ),
    (
        "V39079",
        "POLICY_RATE",
        R.POLICY_TARGET,
        "rate",
        "Bank of Canada overnight policy target",
    ),
    ("BR.CDN", "BANK_RATE", R.POLICY_CEILING, "rate", "Bank of Canada Bank Rate"),
    (
        "OPERATING.BAND.LO",
        "DEPOSIT_RATE",
        R.POLICY_FLOOR,
        "rate",
        "Bank of Canada operating band lower limit",
    ),
    (
        "CORRA_RATE_AT_PERCENTILE_25",
        "CORRA_P25",
        R.RATE_P25,
        "rate",
        "CORRA trimmed-volume 25th percentile",
    ),
    (
        "CORRA_RATE_AT_PERCENTILE_75",
        "CORRA_P75",
        R.RATE_P75,
        "rate",
        "CORRA trimmed-volume 75th percentile",
    ),
    (
        "CORRA_TOTAL_VOLUME",
        "CORRA_VOLUME",
        R.REPO_VOLUME,
        "amount",
        "CORRA eligible volume before trimming",
    ),
    (
        "CORRA_NUMBER_OF_SUBMITTERS",
        "CORRA_SUBMITTERS",
        R.BENCHMARK_SUBMITTERS,
        "count",
        "CORRA unique data submitters",
    ),
    *(
        (
            f"BD.CDN.{t}YR.DQ.YLD",
            f"GOC_{t}Y",
            R.SOVEREIGN_YIELD,
            "rate",
            f"Government of Canada {t}-year benchmark bond yield",
        )
        for t in (2, 3, 5, 7, 10)
    ),
    (
        "TB.CDN.90D.MID",
        "TBILL_3M",
        R.TBILL_3M,
        "rate",
        "Government of Canada 3-month Treasury bill yield",
    ),
    (
        "TB.CDN.180D.MID",
        "TBILL_6M",
        R.TBILL_6M,
        "rate",
        "Government of Canada 6-month Treasury bill yield",
    ),
    (
        "TB.CDN.1Y.MID",
        "TBILL_12M",
        R.TBILL_12M,
        "rate",
        "Government of Canada 1-year Treasury bill yield",
    ),
)
SERIES_MAP = {
    remote: ("CA.BOC." + suffix, role, kind, label)
    for remote, suffix, role, kind, label in SERIES
}
# A provincial civil-holiday proxy is not a validated Bank settlement calendar.
# Until reviewed, freshness remains UNKNOWN; capture never asserts currency.
CALENDAR = BusinessCalendar(
    "CA-BOC-UNVERIFIED",
    "America/Toronto",
    source_uri="https://www.bankofcanada.ca/press/upcoming-events/bank-of-canada-holiday-schedule/",
)
CLOCK = PublicationClock(
    "America/Toronto",
    None,
    0,
    PublicationClockPrecision.UPSTREAM_NATIVE,
    CALENDAR.calendar_id,
)


def _instrument(remote, suffix, role, kind, label):
    identifier = "CA.BOC." + suffix
    if kind == "rate":
        return rate_instrument(
            identifier,
            "BOC_" + suffix,
            role,
            "boc_valet",
            DayCountConvention.SOURCE_NATIVE,
        )
    return InstrumentSpec(
        identifier,
        "BOC_" + suffix,
        role,
        "boc_valet",
        "CAD" if kind == "amount" else "submitters",
        CanonicalUnit.LOCAL_CURRENCY_MILLIONS
        if kind == "amount"
        else CanonicalUnit.COUNT,
        value_multiplier=Decimal("0.000001") if kind == "amount" else Decimal(1),
    )


PACK = MarketPack(
    market_id="CA-CAD",
    monetary_area_id="CA",
    display_name="Canadian dollar",
    jurisdiction_codes=("CA",),
    currency="CAD",
    local_timezone="America/Toronto",
    holiday_calendar=CALENDAR,
    settlement_calendar=CALENDAR,
    reserve_maintenance=(),
    policy_regime=PolicyRegime.CORRIDOR,
    source_adapters=(
        SourceAdapterSpec(
            "boc_valet",
            ConnectorClassification.OFFICIAL_OPEN,
            "P1D",
            CLOCK,
            RedistributionStatus.ALLOWED,
        ),
    ),
    instruments=tuple(_instrument(*row) for row in SERIES),
    capabilities=pre_support_capabilities(
        "Reference data only; calendar and local engine validation remain incomplete."
    ),
    events=(),
    calibration_id="ca-cad-reference-v1",
    minimum_history=MinimumHistory(750, 1095),
    support_status=PackSupportStatus.REFERENCE,
)
