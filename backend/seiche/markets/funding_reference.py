"""Declarative, public-source funding and sovereign-reference coverage.

National sovereign issuers are distinct from their monetary authority. These
reference packs do not promote any country to calibrated engine support.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time

from seiche.domain.observation import (
    ConnectorClassification, DayCountConvention, RateCompounding,
    RedistributionStatus, SemanticRole,
)
from seiche.markets.base import (
    BusinessCalendar, MarketPack, MinimumHistory, PackSupportStatus, PolicyRegime,
    PublicationClock, PublicationClockPrecision, SourceAdapterSpec,
)
from seiche.markets.calendars import target_holidays
from seiche.markets.reference import pre_support_capabilities, rate_instrument


EURO_COUNTRIES = {
    "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy",
    "NL": "Netherlands", "BE": "Belgium", "AT": "Austria", "PT": "Portugal",
    "IE": "Ireland", "FI": "Finland", "GR": "Greece", "SK": "Slovakia",
    "SI": "Slovenia", "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia",
    "HR": "Croatia", "CY": "Cyprus", "MT": "Malta", "LU": "Luxembourg",
    "BG": "Bulgaria",
}
OTHER_EUROPE = {
    "CH": ("Switzerland", "CHF", "Europe/Zurich"),
    "SE": ("Sweden", "SEK", "Europe/Stockholm"),
    "NO": ("Norway", "NOK", "Europe/Oslo"),
    "DK": ("Denmark", "DKK", "Europe/Copenhagen"),
    "PL": ("Poland", "PLN", "Europe/Warsaw"),
    "CZ": ("Czechia", "CZK", "Europe/Prague"),
    "HU": ("Hungary", "HUF", "Europe/Budapest"),
}


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    market_id: str
    funding_market_id: str


COUNTRIES = (
    Country("CN", "China", "CN-CNY", "CN-CNY"),
    Country("JP", "Japan", "JP-JPY", "JP-JPY"),
    Country("KR", "South Korea", "KR-KRW", "KR-KRW"),
    Country("TW", "Taiwan", "TW-TWD", "TW-TWD"),
    Country("AU", "Australia", "AU-AUD", "AU-AUD"),
    Country("GB", "United Kingdom", "UK-GBP", "UK-GBP"),
    *(Country(code, name, code + "-EUR", "EA-EUR") for code, name in EURO_COUNTRIES.items()),
    *(Country(code, name, code + "-" + currency, code + "-" + currency)
      for code, (name, currency, _) in OTHER_EUROPE.items()),
)
COUNTRY_MAP = {country.code: country for country in COUNTRIES}
JP_TENORS = (*range(1, 11), 15, 20, 25, 30, 40)
BOE_TENORS = {"IUDSNZC": 5, "IUDMNZC": 10, "IUDLNZC": 20}

# An alternate publisher is a separately identified series. It never changes
# the rights of the existing CFETS, BOK ECOS, SONIA or licensed-market rows.
BIS_COUNTRIES = ("CN", "KR", *OTHER_EUROPE)
BIS_MARKETS = {code: COUNTRY_MAP[code].market_id for code in BIS_COUNTRIES}
BIS_AUTHORITIES = {
    "CN": "People's Bank of China", "KR": "Bank of Korea", "CH": "Swiss National Bank",
    "SE": "Sveriges Riksbank", "NO": "Norges Bank", "DK": "Danmarks Nationalbank",
    "PL": "National Bank of Poland", "CZ": "Czech National Bank", "HU": "Hungarian National Bank",
}
OECD_COUNTRIES = {"AU": "AUS", "KR": "KOR", "CH": "CHE", "SE": "SWE", "NO": "NOR",
                  "DK": "DNK", "PL": "POL", "CZ": "CZE", "HU": "HUN"}
OECD_TERMS = {"CN": "CHN", "KR": "KOR"}
FRED_SERIES = {
    **{f"IRLTLT01{code}M156N": (code, iso3, "IRLT", code + ".OECD.LONG_TERM_10Y_MONTHLY")
       for code, iso3 in OECD_COUNTRIES.items()},
    **{f"IR3TIB01{code}M156N": (code, iso3, "IR3TIB", code + ".OECD.INTERBANK_3M_MONTHLY")
       for code, iso3 in OECD_TERMS.items()},
}

SOURCE_REFERENCES = {
    "oecd_fred_reference": {
        "publisher": "OECD Main Economic Indicators, distributed by Federal Reserve Bank of St. Louis FRED",
        "source_url": "https://fred.stlouisfed.org/release?rid=175",
        "rights_url": "https://www.oecd.org/en/about/terms-conditions.html",
        "terms": "OECD Main Economic Indicators; attribute OECD, the dataset, FRED series and access date. These named monthly series carry citation requirements; they do not change any separate provider entitlement.",
    },
    "jp_mof_curve": {
        "publisher": "Ministry of Finance Japan",
        "source_url": "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/index.htm",
        "rights_url": "https://www.mof.go.jp/english/about_mof/notice/index.html",
        "terms": "Public Data License 1.0; attribute Japan Ministry of Finance and identify transformations.",
    },
    "boe_sovereign_curve": {
        "publisher": "Bank of England",
        "source_url": "https://www.bankofengland.co.uk/boeapps/database/FromShowColumns.asp?SearchText=IUDSNZC,IUDMNZC,IUDLNZC",
        "rights_url": "https://www.bankofengland.co.uk/legal",
        "terms": "Bank of England Database, UK Open Government Licence; attribute the Bank and identify transformations. This adapter contains only the three named nominal spot-yield series.",
    },
    "ecb_sovereign_monthly": {
        "publisher": "European Central Bank and national statistical contributors",
        "source_url": "https://data.ecb.europa.eu/data/datasets/IRS",
        "rights_url": "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "terms": "Attribute the ECB; identify transformations; the original statistics are available free from the ECB.",
    },
    "bis_policy_reference": {
        "publisher": "Bank for International Settlements and reporting national central banks",
        "source_url": "https://data.bis.org/topics/CBPOL",
        "rights_url": "https://data.bis.org/help/legal",
        "terms": "Attribute BIS and the national central bank; no additional charge for these statistics; no endorsement implied.",
    },
    "cbc_public_rates": {
        "publisher": "Central Bank of the Republic of China (Taiwan)",
        "source_url": "https://www.cbc.gov.tw/en/mp-2.html",
        "rights_url": "https://www.cbc.gov.tw/en/cp-958-40419-F8209-2.html",
        "terms": "Open Government Data License Taiwan 1.0; attribution required.",
    },
    "cbc_monthly_rates": {
        "publisher": "Central Bank of the Republic of China (Taiwan)",
        "source_url": "https://www.cbc.gov.tw/en/cp-511-1876-0EF53-2.html",
        "rights_url": "https://www.cbc.gov.tw/en/cp-958-40419-F8209-2.html",
        "terms": "Selected Interest Rates, CBC Taiwan; Open Government Data License Taiwan 1.0. Attribute CBC and identify transformations.",
    },
}

CBC_MONTHLY = {
    1: ("TW.CBC.DISCOUNT_MONTH_END", SemanticRole.POLICY_TARGET),
    2: ("TW.CBC.COLLATERAL_RATE_MONTH_END", SemanticRole.CENTRAL_BANK_FACILITY_RATE),
    3: ("TW.CBC.BANK_DEPOSIT_1M_MONTH_END", SemanticRole.TERM_1M),
    4: ("TW.CBC.BANK_DEPOSIT_1Y_MONTH_END", SemanticRole.TERM_FUNDING_RATE),
    5: ("TW.CBC.BANK_BASE_LENDING_MONTH_END", SemanticRole.TERM_FUNDING_RATE),
    6: ("TW.CBC.OVERNIGHT_CALL_MONTHLY", SemanticRole.UNSECURED_OVERNIGHT),
    7: ("TW.CBC.CP_31_90D_MONTHLY", SemanticRole.TERM_FUNDING_RATE),
}


@dataclass(frozen=True)
class Curve:
    country: str
    market_id: str
    adapter_id: str
    kind: str
    cadence: str
    nodes: tuple[tuple[int, str], ...]
    method: str


CURVES = (
    Curve("TW", "TW-TWD", "cbc_monthly_rates", "monthly_long_term_reference", "P1M",
          ((10, "TW.CBC.GOVERNMENT_10Y_MONTHLY"),),
          "CBC monthly weighted-average yield of the most actively traded ten-year government bond in the secondary market. This is one monthly benchmark, not a fixed-maturity daily curve."),
    *(Curve(code, COUNTRY_MAP[code].market_id, "oecd_fred_reference", "monthly_long_term_reference", "P1M",
            ((10, code + ".OECD.LONG_TERM_10Y_MONTHLY"),),
            "OECD monthly ten-year main or benchmark government-bond yield, distributed by FRED. National definitions remain source-specific; this is a monthly reference, not a daily curve or executable quote.")
      for code in OECD_COUNTRIES),
    Curve("JP", "JP-JPY", "jp_mof_curve", "constant_maturity_yield", "P1D",
          tuple((t, f"JP.MOF.JGB_{t}Y") for t in JP_TENORS),
          "MOF constant-maturity JGB yields, semiannually compounded. These are source-calculated maturity references, not individual bond quotes."),
    Curve("GB", "UK-GBP", "boe_sovereign_curve", "nominal_zero_coupon_spot", "P1D",
          tuple((t, f"GB.BOE.ZERO_COUPON_{t}Y") for t in BOE_TENORS.values()),
          "Bank of England estimated nominal zero-coupon spot yields. Keep them separate from par yields and implied forward rates."),
    *(Curve(code, code + "-EUR", "ecb_sovereign_monthly", "monthly_long_term_reference", "P1M",
            ((10, f"{code}.ECB.LONG_TERM_10Y_MONTHLY"),),
            "ECB monthly convergence reference, approximately ten-year national government-bond yields. A monthly average is not a daily curve or a month-end traded quote; national methodology and proxies remain source-defined.")
      for code in EURO_COUNTRIES),
)


def _adapter(pack, identifier, cadence="P1D"):
    clock = PublicationClock(pack.local_timezone, None, 0,
                             PublicationClockPrecision.UNKNOWN,
                             pack.settlement_calendar.calendar_id)
    if identifier == "jp_mof_curve":
        clock = PublicationClock("Asia/Tokyo", time(9, 30), 1,
                                 PublicationClockPrecision.SCHEDULED,
                                 pack.settlement_calendar.calendar_id)
    return SourceAdapterSpec(identifier, ConnectorClassification.OFFICIAL_OPEN,
                             cadence, clock, RedistributionStatus.ALLOWED,
                             collection_cadence="P1D", retry_limit=1)


def extend_reference_pack(pack: MarketPack) -> MarketPack:
    """Add explicit public series without changing any existing source policy."""
    instruments = list(pack.instruments)
    adapters = list(pack.source_adapters)
    for curve in CURVES:
        if curve.market_id != pack.market_id:
            continue
        adapters.append(_adapter(pack, curve.adapter_id, curve.cadence))
        instruments.extend(rate_instrument(
            identifier, identifier.replace(".", "_"), SemanticRole.SOVEREIGN_YIELD,
            curve.adapter_id, DayCountConvention.SOURCE_NATIVE,
            compounding=RateCompounding.SOURCE_NATIVE,
        ) for _, identifier in curve.nodes)
    code = next((c for c, market in BIS_MARKETS.items() if market == pack.market_id), None)
    if code:
        adapters.append(_adapter(pack, "bis_policy_reference"))
        instruments.append(rate_instrument(
            code + ".BIS.POLICY_REFERENCE", "BIS_POLICY_REFERENCE",
            SemanticRole.POLICY_TARGET, "bis_policy_reference",
            DayCountConvention.SOURCE_NATIVE, compounding=RateCompounding.SOURCE_NATIVE,
        ))
    code = next((c for c in OECD_TERMS if COUNTRY_MAP[c].market_id == pack.market_id), None)
    if code:
        if not any(a.adapter_id == "oecd_fred_reference" for a in adapters):
            adapters.append(_adapter(pack, "oecd_fred_reference", "P1M"))
        instruments.append(rate_instrument(
            code + ".OECD.INTERBANK_3M_MONTHLY", "OECD_INTERBANK_3M_MONTHLY",
            SemanticRole.TERM_3M, "oecd_fred_reference", DayCountConvention.SOURCE_NATIVE,
            compounding=RateCompounding.SOURCE_NATIVE,
        ))
    if pack.market_id == "TW-TWD":
        instruments.extend(rate_instrument(
            identifier, identifier.replace(".", "_"), role, "cbc_monthly_rates",
            DayCountConvention.SOURCE_NATIVE, compounding=RateCompounding.SOURCE_NATIVE,
        ) for identifier, role in CBC_MONTHLY.values())
        adapters.append(_adapter(pack, "cbc_public_rates"))
        instruments.extend((
            rate_instrument("TW.CBC.OVERNIGHT_CALL", "CBC_OVERNIGHT_CALL",
                            SemanticRole.UNSECURED_OVERNIGHT, "cbc_public_rates",
                            DayCountConvention.SOURCE_NATIVE, compounding=RateCompounding.SOURCE_NATIVE),
            rate_instrument("TW.CBC.DISCOUNT_DECISION", "CBC_DISCOUNT_DECISION",
                            SemanticRole.POLICY_TARGET, "cbc_public_rates",
                            DayCountConvention.SOURCE_NATIVE, compounding=RateCompounding.SOURCE_NATIVE),
        ))
    return replace(pack, instruments=tuple(instruments), source_adapters=tuple(adapters))


def additional_reference_packs() -> tuple[MarketPack, ...]:
    result = []
    specs = [(c, n, "EUR", "Europe/Berlin", "EA") for c, n in EURO_COUNTRIES.items()]
    specs += [(c, n, currency, zone, c) for c, (n, currency, zone) in OTHER_EUROPE.items()]
    specs.append(("TW", "Taiwan", "TWD", "Asia/Taipei", "TW"))
    for code, name, currency, zone, area in specs:
        calendar = BusinessCalendar(
            "TARGET" if area == "EA" else code + "-REFERENCE-UNREVIEWED", zone,
            valid_from_year=1999 if area == "EA" else None,
            valid_to_year=2035 if area == "EA" else None,
            source_uri=("https://www.ecb.europa.eu/paym/target/target-professional-use-documents-links/target-calendar/html/index.en.html"
                        if area == "EA" else None),
            holiday_provider=target_holidays if area == "EA" else None,
        )
        pack = MarketPack(
            market_id=code + "-" + currency, monetary_area_id=area,
            display_name=name + " public references", jurisdiction_codes=(code,),
            currency=currency, local_timezone=zone, holiday_calendar=calendar,
            settlement_calendar=calendar, reserve_maintenance=(),
            policy_regime=PolicyRegime.FLOOR if area == "EA" else PolicyRegime.UNKNOWN,
            instruments=(), source_adapters=(),
            capabilities=pre_support_capabilities("Reference observations only; local funding coverage and temporal validation are incomplete."),
            events=(), calibration_id=code.lower() + "-funding-reference-v1",
            minimum_history=MinimumHistory(750, 1095), support_status=PackSupportStatus.REFERENCE,
        )
        result.append(extend_reference_pack(pack))
    return tuple(result)
