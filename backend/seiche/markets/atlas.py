"""Point-in-time global money-market atlas built from canonical observations.

The atlas is deliberately descriptive.  It compares each market with its own
history and preserves the market pack's native units, calendar, policy regime,
publication cadence, and redistribution boundary.  It does not manufacture a
single cross-country stress score and it never upsamples a slow series.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from seiche.domain.observation import (
    RATE_ROLES,
    Observation,
    QualityState,
    RedistributionStatus,
    SemanticRole,
    StalenessState,
)
from seiche.markets.base import MarketPack
from seiche.markets.base import CalendarUnavailableError

ATLAS_SCHEMA = "seiche.global-money-markets.v1"
_DERIVABLE_REDISTRIBUTION = frozenset(
    {RedistributionStatus.ALLOWED, RedistributionStatus.DERIVED_ONLY}
)

_BENCHMARK_PRIORITY = (
    SemanticRole.SECURED_OVERNIGHT,
    SemanticRole.UNSECURED_OVERNIGHT,
    SemanticRole.TERM_1W,
)
_POLICY_PRIORITY = (
    SemanticRole.POLICY_TARGET,
    SemanticRole.POLICY_FLOOR,
    SemanticRole.POLICY_CEILING,
    SemanticRole.CENTRAL_BANK_FACILITY_RATE,
)
_REGIONS = {
    "US-USD": "Americas",
    "EA-EUR": "Europe",
    "UK-GBP": "Europe",
    "IN-INR": "South Asia",
    "CN-CNY": "East Asia",
    "JP-JPY": "East Asia",
    "HK-HKD": "East Asia",
    "KR-KRW": "East Asia",
    "SG-SGD": "Southeast Asia",
    "AU-AUD": "Oceania",
    "NZ-NZD": "Oceania",
}
_SOURCE_URLS = {
    "fred_daily": "https://fred.stlouisfed.org/",
    "fred_weekly": "https://fred.stlouisfed.org/",
    "fiscaldata": "https://fiscaldata.treasury.gov/",
    "nyfed_rates": "https://markets.newyorkfed.org/static/docs/markets-api.html",
    "nyfed_facilities": "https://www.newyorkfed.org/markets/domestic-market-operations/monetary-policy-implementation",
    "ecb_benchmark": "https://data.ecb.europa.eu/data/datasets/EST",
    "ecb_policy": "https://data.ecb.europa.eu/data/datasets/FM",
    "ecb_liquidity": "https://data.ecb.europa.eu/data/datasets/ILM",
    "boe_sonia": "https://www.bankofengland.co.uk/boeapps/database/",
    "boe_policy": "https://www.bankofengland.co.uk/boeapps/database/",
    "boj_rates": "https://www.stat-search.boj.or.jp/",
    "boj_accounts": "https://www.stat-search.boj.or.jp/",
    "bok_ecos_policy": "https://ecos.bok.or.kr/api/",
    "bok_ecos_money_market": "https://ecos.bok.or.kr/api/",
    "bok_facilities": "https://www.bok.or.kr/eng/main/main.do",
    "ksd_kofr": "https://www.kofr.kr/",
    "cfets_rates": "https://www.chinamoney.com.cn/english/bmkshibor/",
    "pbc_operations": "https://www.pbc.gov.cn/en/3688229/index.html",
    "rbi_official": "https://www.rbi.org.in/Scripts/BS_ViewMMO.aspx",
    "hkma_official": "https://api.hkma.gov.hk/public/market-data-and-statistics/",
    "mas_sora": "https://eservices.mas.gov.sg/statistics/dir/DomesticInterestRates.aspx",
    "mas_rates": "https://eservices.mas.gov.sg/statistics/dir/DomesticInterestRates.aspx",
    "rba_cash": "https://www.rba.gov.au/statistics/interest-rates/",
    "rba_policy": "https://www.rba.gov.au/statistics/cash-rate/",
    "rbnz_policy": "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates",
    "rbnz_wholesale": "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates",
}

# Markets outside the registered canonical packs remain visible.  This is a
# source-audited discovery ledger, not a claim that Seiche already serves an
# observation.  `status` describes integration readiness; `access` is a
# separate rights/operational gate.  Currency unions are represented once as
# monetary areas rather than duplicated for every member state.
_EXPANSION_VERIFIED_ON = "2026-08-21"
_EXPANSION_ACCESS = frozenset(
    {
        "COMPLIANCE_BLOCKED",
        "ENDPOINT_RESEARCH",
        "ENDPOINT_REVIEW",
        "LICENSE_REVIEW",
        "METHODOLOGY_RESEARCH",
        "METHODOLOGY_REVIEW",
        "OFFICIAL_PUBLIC",
        "OPEN_GOVERNMENT_DATA",
        "OPERATIONAL_REVIEW",
        "TERMS_REVIEW",
    }
)
_EXPANSION_CONFIDENCE = frozenset({"HIGH", "MEDIUM", "LOW"})
_EXPANSION_STATUS = frozenset(
    {
        "ACCESS_REVIEW",
        "COMPLIANCE_BLOCKED",
        "METHODOLOGY_REVIEW",
        "RESEARCH_QUEUE",
        "SOURCE_VERIFIED",
    }
)
# Deliberately curated rather than regex-only: adding a discovery jurisdiction
# with a new currency requires an explicit reviewed schema change.  These are
# ISO 4217 alpha-3 codes used by the current registered + discovery universe.
_EXPANSION_ISO_4217 = frozenset(
    {
        "AED",
        "ARS",
        "AUD",
        "BDT",
        "BRL",
        "CAD",
        "CHF",
        "CLP",
        "CNY",
        "COP",
        "CRC",
        "CZK",
        "DKK",
        "DOP",
        "EGP",
        "EUR",
        "GBP",
        "GEL",
        "GHS",
        "HKD",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "JPY",
        "KES",
        "KGS",
        "KRW",
        "KWD",
        "KZT",
        "LKR",
        "MAD",
        "MUR",
        "MXN",
        "MYR",
        "NOK",
        "NPR",
        "NZD",
        "OMR",
        "PEN",
        "PHP",
        "PKR",
        "PLN",
        "QAR",
        "RUB",
        "RWF",
        "SAR",
        "SEK",
        "SGD",
        "THB",
        "TRY",
        "TWD",
        "TZS",
        "UAH",
        "UGX",
        "USD",
        "UYU",
        "UZS",
        "VND",
        "XAF",
        "XOF",
        "ZAR",
        "ZMW",
    }
)


def _expansion(
    market_id: str,
    region: str,
    currency: str,
    market: str,
    benchmark: str,
    benchmark_kind: str,
    authority: str,
    source_url: str,
    access: str,
    access_note: str,
    confidence: str,
    status: str,
) -> dict[str, str | None]:
    return {
        "market_id": market_id,
        "region": region,
        "currency": currency,
        "market": market,
        "benchmark": benchmark,
        "benchmark_kind": benchmark_kind,
        "authority": authority,
        "source_url": source_url,
        # `source_url` remains the v1 compatibility field.  The explicit
        # typed URLs prevent a methodology page from being mistaken for a
        # machine-readable endpoint or a grant of redistribution rights.
        "official_reference_url": source_url,
        "methodology_url": None,
        "data_endpoint": None,
        "terms_url": None,
        "access": access,
        "access_note": access_note,
        "confidence": confidence,
        "status": status,
        "verified_on": _EXPANSION_VERIFIED_ON,
    }


EXPANSION_LEDGER: tuple[dict[str, str | None], ...] = (
    _expansion(
        "CA-CAD",
        "Americas",
        "CAD",
        "Canada",
        "CORRA",
        "secured overnight transaction rate",
        "Bank of Canada",
        "https://www.bankofcanada.ca/rates/interest-rates/corra/",
        "OFFICIAL_PUBLIC",
        "Public-good benchmark; attribution and Bank of Canada disclaimers still apply.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "MX-MXN",
        "Latin America",
        "MXN",
        "Mexico",
        "Overnight TIIE Funding Rate",
        "overnight funding transaction rate",
        "Banco de México",
        "https://www.banxico.org.mx/SieInternet/consultarDirectorioInternetAction.do?accion=consultarCuadroAnalitico&idCuadro=CA684&locale=en",
        "TERMS_REVIEW",
        "Official SIE data; API quotas, attribution and redistribution terms need production review.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "BR-BRL",
        "Latin America",
        "BRL",
        "Brazil",
        "Effective Selic Rate",
        "secured overnight government-securities rate",
        "Banco Central do Brasil / Selic",
        "https://www.bcb.gov.br/content/monetarypolicy/selic_en/Resolution-BCB-46.pdf",
        "ENDPOINT_REVIEW",
        "Methodology is official; select and validate the BCB/SGS or Selic production feed and reuse terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "CL-CLP",
        "Latin America",
        "CLP",
        "Chile",
        "TIB one-day interbank rate",
        "unsecured overnight interbank rate",
        "Banco Central de Chile",
        "https://si3.bcentral.cl/siete/ES/Siete/Cuadro/CAP_EST_OMA/MN_EST_OMA/EM_OMA_30?idSerie=F022.TIB.TIP.D001.NO.Z.D",
        "TERMS_REVIEW",
        "Official series; publication requires enough participants and automation/reuse terms need review.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "CO-COP",
        "Latin America",
        "COP",
        "Colombia",
        "IBR Overnight",
        "quote-based overnight interbank reference",
        "Banco de la República",
        "https://www.banrep.gov.co/en/glossary/overnight-interbank-rate-ibr",
        "METHODOLOGY_REVIEW",
        "Official daily fixing, but quote-based rather than transaction-based; retain that distinction.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "PE-PEN",
        "Latin America",
        "PEN",
        "Peru",
        "Average overnight interbank rate / TIBO candidate",
        "overnight interbank series to validate",
        "Banco Central de Reserva del Perú",
        "https://estadisticas.bcrp.gob.pe/estadisticas/series/mensuales/tasas-de-interes",
        "ENDPOINT_RESEARCH",
        "A public interbank series exists; the exact daily overnight endpoint and reuse terms remain unverified.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "UY-UYU",
        "Latin America",
        "UYU",
        "Uruguay",
        "TMM one-day Average Market Rate",
        "one-day eligible funding transaction rate",
        "Banco Central del Uruguay",
        "https://www.bcu.gub.uy/Politica-Economica-y-Mercados/Paginas/Tasa-1-Dia.aspx",
        "TERMS_REVIEW",
        "Official one-day rate; validate export and commercial redistribution conditions.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "AR-ARS",
        "Latin America",
        "ARS",
        "Argentina",
        "One-day BCRA repo / passive-repo candidate",
        "policy or overnight facility proxy to validate",
        "Banco Central de la República Argentina",
        "https://www.bcra.gob.ar/datos-monetarios-diarios/",
        "METHODOLOGY_RESEARCH",
        "Operating instruments change frequently; validate the current target and series before use.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "CR-CRC",
        "Latin America",
        "CRC",
        "Costa Rica",
        "MIL one-day weighted-average rate",
        "one-day interbank transaction rate",
        "Banco Central de Costa Rica",
        "https://gee.bccr.fi.cr/indicadoreseconomicos/Cuadros/frmVerCatCuadro.aspx?CodCuadro=+1599&idioma=1",
        "TERMS_REVIEW",
        "Separate BCCR-inclusive and participant-only measures and validate SINPE reuse restrictions.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "DO-DOP",
        "Latin America",
        "DOP",
        "Dominican Republic",
        "Weighted interbank rate / overnight facilities candidate",
        "mixed-tenor market rate and policy facilities",
        "Banco Central de la República Dominicana",
        "https://www.bancentral.gov.do/",
        "ENDPOINT_RESEARCH",
        "Headline interbank data are not clearly overnight-only; granular endpoint and terms need validation.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "CH-CHF",
        "Europe",
        "CHF",
        "Switzerland",
        "SARON",
        "secured overnight transaction rate",
        "SIX Benchmark AG",
        "https://www.six-group.com/en/market-data/indices/switzerland/saron.html",
        "LICENSE_REVIEW",
        "Intraday and first-24-hour data are restricted; display, redistribution and derived use require rights review.",
        "HIGH",
        "ACCESS_REVIEW",
    ),
    _expansion(
        "SE-SEK",
        "Europe",
        "SEK",
        "Sweden",
        "SWESTR",
        "unsecured overnight transaction rate",
        "Sveriges Riksbank",
        "https://www.riksbank.se/en-gb/statistics/swestr/",
        "OFFICIAL_PUBLIC",
        "Official public benchmark; retain attribution and confirm automated redistribution terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "NO-NOK",
        "Europe",
        "NOK",
        "Norway",
        "NOWA",
        "unsecured overnight transaction rate",
        "Norges Bank",
        "https://www.norges-bank.no/en/topics/statistics/nowa-data/",
        "TERMS_REVIEW",
        "Official central-bank data; validate automated download and commercial republication terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "DK-DKK",
        "Europe",
        "DKK",
        "Denmark",
        "DESTR",
        "unsecured overnight transaction rate",
        "Danmarks Nationalbank",
        "https://www.nationalbanken.dk/en/what-we-do/stable-prices-monetary-policy-and-the-danish-economy/destr/about-destr",
        "OFFICIAL_PUBLIC",
        "The authority states DESTR is free to use; preserve attribution and disclaimers.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "PL-PLN",
        "Europe",
        "PLN",
        "Poland",
        "POLONIA",
        "unsecured overnight transaction rate",
        "Narodowy Bank Polski",
        "https://nbp.pl/statystyka-i-sprawozdawczosc/stawka-referencyjna-polonia/",
        "TERMS_REVIEW",
        "Official NBP fixing; confirm machine-readable endpoint and reuse terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "CZ-CZK",
        "Europe",
        "CZK",
        "Czech Republic",
        "CZEONIA",
        "unsecured overnight transaction rate",
        "Czech National Bank",
        "https://www.cnb.cz/en/financial-markets/money-market/czeonia-reference-interest-rate/calculation-methodology-for-the-czeonia-reference-rate/",
        "TERMS_REVIEW",
        "Use CZEONIA for open official context; PRIBOR redistribution and derived publication are separately restricted.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "HU-HUF",
        "Europe",
        "HUF",
        "Hungary",
        "HUFONIA",
        "unsecured overnight transaction rate",
        "Magyar Nemzeti Bank",
        "https://www.mnb.hu/en/pressroom/press-releases/press-releases-2010/press-release-on-the-introduction-of-the-hufonia-name",
        "ENDPOINT_REVIEW",
        "Official benchmark identity is verified; current machine-readable endpoint and reuse terms need review.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "UA-UAH",
        "Europe",
        "UAH",
        "Ukraine",
        "UONIA",
        "overnight transaction reference rate",
        "National Bank of Ukraine",
        "https://bank.gov.ua/en/open-data/api-dev",
        "OPERATIONAL_REVIEW",
        "Official API; wartime calendars, administrative measures and publication continuity need explicit monitoring.",
        "HIGH",
        "ACCESS_REVIEW",
    ),
    _expansion(
        "TR-TRY",
        "Europe",
        "TRY",
        "Türkiye",
        "TLREF",
        "secured overnight transaction rate",
        "Borsa İstanbul",
        "https://www.borsaistanbul.com/en/indices/tlref",
        "LICENSE_REVIEW",
        "Exchange intellectual-property rights apply to display, redistribution and derived data.",
        "HIGH",
        "ACCESS_REVIEW",
    ),
    _expansion(
        "RU-RUB",
        "Europe",
        "RUB",
        "Russia",
        "RUONIA",
        "unsecured overnight transaction rate",
        "Bank of Russia",
        "https://cbr.ru/eng/hd_base/ruonia/method/",
        "COMPLIANCE_BLOCKED",
        "Do not integrate without sanctions, legal, cybersecurity and endpoint-availability approval.",
        "HIGH",
        "COMPLIANCE_BLOCKED",
    ),
    _expansion(
        "GE-GEL",
        "Europe",
        "GEL",
        "Georgia",
        "TIBR overnight index",
        "overnight interbank reference rate",
        "National Bank of Georgia",
        "https://nbg.gov.ge/en/monetary-policy/tibr",
        "ENDPOINT_REVIEW",
        "Official index; transaction coverage, history endpoint and redistribution terms need review.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "ID-IDR",
        "Southeast Asia",
        "IDR",
        "Indonesia",
        "INDONIA",
        "unsecured overnight transaction rate",
        "Bank Indonesia",
        "https://www.bi.go.id/id/fungsi-utama/moneter/indonia-jibor/Default_x.aspx",
        "TERMS_REVIEW",
        "Official transaction-based benchmark; validate automation and redistribution terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "MY-MYR",
        "Southeast Asia",
        "MYR",
        "Malaysia",
        "MYOR",
        "overnight transaction rate",
        "Bank Negara Malaysia",
        "https://financialmarkets.bnm.gov.my/about-myor-myor-i",
        "TERMS_REVIEW",
        "Model MYOR-i separately for Islamic funding and validate portal reuse terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "TH-THB",
        "Southeast Asia",
        "THB",
        "Thailand",
        "THOR",
        "secured overnight transaction rate",
        "Bank of Thailand / ThaiBMA",
        "https://app.bot.or.th/thor/en",
        "TERMS_REVIEW",
        "Retain revision and fallback flags; historical ThaiBMA rights need separate review.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "PH-PHP",
        "Southeast Asia",
        "PHP",
        "Philippines",
        "Interbank Call Loan Rate",
        "official weekly overnight interbank series",
        "Bangko Sentral ng Pilipinas",
        "https://www.bsp.gov.ph/statistics/Financial%20System%20Accounts/winterestrates_data.aspx",
        "METHODOLOGY_REVIEW",
        "Not a formal named RFR; model the overnight RRP policy rate separately.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "VN-VND",
        "Southeast Asia",
        "VND",
        "Vietnam",
        "Overnight VND interbank rate candidate",
        "overnight interbank series to validate",
        "State Bank of Vietnam",
        "https://www.sbv.gov.vn/documents/d/sbv_portal/590044",
        "ENDPOINT_RESEARCH",
        "No stable official public benchmark/API was verified; do not label this VNIBOR without validation.",
        "LOW",
        "RESEARCH_QUEUE",
    ),
    _expansion(
        "TW-TWD",
        "East Asia",
        "TWD",
        "Taiwan",
        "Weighted-average overnight call-loan rate",
        "unsecured overnight interbank transaction rate",
        "Central Bank of the Republic of China (Taiwan)",
        "https://data.gov.tw/en/datasets/6023",
        "OPEN_GOVERNMENT_DATA",
        "Daily open data under Taiwan's Government Data License; attribution conditions apply.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "PK-PKR",
        "South Asia",
        "PKR",
        "Pakistan",
        "Weighted-average overnight repo rate",
        "secured overnight transaction rate",
        "State Bank of Pakistan",
        "https://www.sbp.org.pk/FIRD/index.htm",
        "ENDPOINT_REVIEW",
        "Official indicator; validate historical files, update schedule and redistribution terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "BD-BDT",
        "South Asia",
        "BDT",
        "Bangladesh",
        "Average overnight interbank call-money rate",
        "unsecured overnight transaction series",
        "Bangladesh Bank",
        "https://www.bb.org.bd/en/index.php/monetaryactivity/call_money_market",
        "ENDPOINT_REVIEW",
        "Official table, not a verified formal RFR; distinguish overnight from short-notice tenors.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "LK-LKR",
        "South Asia",
        "LKR",
        "Sri Lanka",
        "AWCMR",
        "weighted overnight call-money rate",
        "Central Bank of Sri Lanka",
        "https://www.cbsl.gov.lk/en/financial-system/financial-markets/interbank-call-money-market",
        "ENDPOINT_REVIEW",
        "Official daily operating-target series; validate machine-readable reuse terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "NP-NPR",
        "South Asia",
        "NPR",
        "Nepal",
        "Weighted Average Interbank Rate",
        "interbank series with overnight tenor to validate",
        "Nepal Rastra Bank",
        "https://www.nrb.org.np/cmfm_rates/short_term_rates/62/",
        "ENDPOINT_RESEARCH",
        "Official daily graph; overnight definition and reusable endpoint require validation.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "UZ-UZS",
        "Central Asia",
        "UZS",
        "Uzbekistan",
        "UZONIA",
        "secured overnight transaction rate",
        "Central Bank of Uzbekistan",
        "https://cbu.uz/en/press_center/news/2671408/",
        "METHODOLOGY_REVIEW",
        "Methodology changed to overnight-repo input on 2025-08-01; preserve an explicit structural-break flag.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "KZ-KZT",
        "Central Asia",
        "KZT",
        "Kazakhstan",
        "TONIA",
        "overnight transaction index",
        "Kazakhstan Stock Exchange",
        "https://kase.kz/en/information/news/show/134060/",
        "LICENSE_REVIEW",
        "Exchange-administered index; current methodology, live endpoint and redistribution rights need review.",
        "HIGH",
        "ACCESS_REVIEW",
    ),
    _expansion(
        "KG-KGS",
        "Central Asia",
        "KGS",
        "Kyrgyz Republic",
        "Overnight interbank repo candidate",
        "overnight repo series to validate",
        "National Bank of the Kyrgyz Republic",
        "https://www.nbkr.kg/index1.jsp?lang=ENG",
        "METHODOLOGY_RESEARCH",
        "The BIR benchmark is seven-day, not overnight; validate a separate overnight series.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "AE-AED",
        "Middle East",
        "AED",
        "United Arab Emirates",
        "DONIA",
        "mixed secured and unsecured overnight transaction rate",
        "Central Bank of the UAE",
        "https://centralbank.ae/en/our-operations/monetary-policy-and-domestic-markets/",
        "TERMS_REVIEW",
        "Official transaction benchmark; validate historical downloads and redistribution conditions.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "IL-ILS",
        "Middle East",
        "ILS",
        "Israel",
        "SHIR",
        "overnight policy reference, not a transaction fixing",
        "Bank of Israel",
        "https://boi.org.il/en/economic-roles/financial-markets/shir/",
        "TERMS_REVIEW",
        "SHIR equals the policy rate; never describe it as a transaction-weighted market print.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "QA-QAR",
        "Middle East",
        "QAR",
        "Qatar",
        "AOIR / QMR overnight facilities candidate",
        "operating target and central-bank facility rates",
        "Qatar Central Bank",
        "https://www.qcb.gov.qa/en/Pages/MonetaryPolicyTools.aspx",
        "ENDPOINT_RESEARCH",
        "Standing-facility rates are not a transaction RFR; validate the AOIR publication endpoint.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "SA-SAR",
        "Middle East",
        "SAR",
        "Saudi Arabia",
        "Repo / reverse-repo policy rates",
        "central-bank facility proxy",
        "Saudi Central Bank",
        "https://sama.gov.sa/en-US/MonetaryPolicy/MonetaryPolicyTools/Pages/RepoRate.aspx",
        "METHODOLOGY_RESEARCH",
        "No transaction-based overnight RFR was verified; SAIBOR is indicative, not a substitute.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "KW-KWD",
        "Middle East",
        "KWD",
        "Kuwait",
        "KONIA",
        "overnight interest average index",
        "Central Bank of Kuwait",
        "https://www.cbk.gov.kw/en/monetary-policy/market-operations/main-indicators",
        "ENDPOINT_REVIEW",
        "Official Excel/XML options exist; validate automated and commercial redistribution terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "OM-OMR",
        "Middle East",
        "OMR",
        "Oman",
        "OMIBOR O/N",
        "quote-based overnight interbank fixing",
        "Central Bank of Oman",
        "https://cbo.gov.om/Pages/OMIBOR.aspx",
        "METHODOLOGY_REVIEW",
        "Official but indicative and non-binding; keep separate from transaction-based overnight context.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "EG-EGP",
        "Africa",
        "EGP",
        "Egypt",
        "CONIA",
        "unsecured overnight transaction rate",
        "Central Bank of Egypt",
        "https://www.cbe.org.eg/en/economic-research/statistics/conia",
        "TERMS_REVIEW",
        "Official transaction RFR with history and compounded indices; validate bulk reuse terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "MA-MAD",
        "Africa",
        "MAD",
        "Morocco",
        "MONIA",
        "secured overnight transaction rate",
        "Bank Al-Maghrib",
        "https://www.bkam.ma/en/Markets/Key-indicators/Money-market/Monia-index-moroccan-overnight-index-average",
        "TERMS_REVIEW",
        "Official CSV download; retain administrator attribution and validate redistribution terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "ZA-ZAR",
        "Africa",
        "ZAR",
        "South Africa",
        "ZARONIA",
        "unsecured overnight transaction rate",
        "South African Reserve Bank",
        "https://www.resbank.co.za/en/home/what-we-do/financial-markets/south-african-overnight-index-average",
        "OFFICIAL_PUBLIC",
        "Free of charge without a licence agreement; preserve regime flags and SARB disclaimers.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "KE-KES",
        "Africa",
        "KES",
        "Kenya",
        "KESONIA",
        "unsecured overnight transaction rate",
        "Central Bank of Kenya",
        "https://www.centralbank.go.ke/wp-content/uploads/2025/08/KESONIAFAQs.pdf",
        "ENDPOINT_REVIEW",
        "Official transaction benchmark; locate the production feed and validate redistribution terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "GH-GHS",
        "Africa",
        "GHS",
        "Ghana",
        "Overnight interbank rate",
        "overnight interbank series",
        "Bank of Ghana",
        "https://www.bog.gov.gh/treasury-and-the-markets/interbank-interest-rates/",
        "ENDPOINT_RESEARCH",
        "No separately named RFR was verified; validate daily coverage and reuse terms.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "TZ-TZS",
        "Africa",
        "TZS",
        "Tanzania",
        "Overnight IBCM weighted-average rate",
        "overnight interbank transaction rate",
        "Bank of Tanzania",
        "https://www.bot.go.tz/FinancialMarket/ibcm",
        "TERMS_REVIEW",
        "Sparse and no-trade days must remain missing; validate download and redistribution terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "ZM-ZMW",
        "Africa",
        "ZMW",
        "Zambia",
        "Overnight interbank interest rate",
        "overnight interbank series",
        "Bank of Zambia",
        "https://www.boz.zm/markets-securities/overnight-interbank-interest-rates",
        "TERMS_REVIEW",
        "Official download; validate cadence, metadata and redistribution terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "MU-MUR",
        "Africa",
        "MUR",
        "Mauritius",
        "Overnight interbank rate",
        "weighted overnight interbank yield",
        "Bank of Mauritius",
        "https://www.bom.mu/markets/money-markets/overnight-interbank-rate",
        "TERMS_REVIEW",
        "Keep separate from licensed quote-based PLIBOR and validate public-series reuse terms.",
        "HIGH",
        "SOURCE_VERIFIED",
    ),
    _expansion(
        "UG-UGX",
        "Africa",
        "UGX",
        "Uganda",
        "Weighted-average overnight interbank rate",
        "overnight interbank series",
        "Bank of Uganda",
        "https://www.bou.or.ug/uploads/Final_SOE_March_2026_47bb280937.pdf",
        "ENDPOINT_RESEARCH",
        "Official reporting confirms the measure; stable daily endpoint and reuse terms remain unverified.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "RW-RWF",
        "Africa",
        "RWF",
        "Rwanda",
        "Interbank repo rate candidate",
        "overnight repo series to validate",
        "National Bank of Rwanda",
        "https://www.bnr.rw/mminterestrates",
        "ENDPOINT_RESEARCH",
        "Formal overnight-only definition and production endpoint remain unverified.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "WAEMU-XOF",
        "Africa",
        "XOF",
        "West African Economic and Monetary Union",
        "One-day TIMP candidate",
        "currency-union interbank rate to validate",
        "BCEAO",
        "https://www.bceao.int/",
        "ENDPOINT_RESEARCH",
        "Exact daily one-day series, methodology and redistribution terms need validation.",
        "MEDIUM",
        "METHODOLOGY_REVIEW",
    ),
    _expansion(
        "CEMAC-XAF",
        "Africa",
        "XAF",
        "Central African Economic and Monetary Community",
        "Interbank TIMP candidate",
        "currency-union interbank rate to validate",
        "BEAC",
        "https://www.beac.int/base-de-donnees-economiques-monetaires-financieres/",
        "ENDPOINT_RESEARCH",
        "A current daily overnight benchmark and public reuse policy were not verified.",
        "LOW",
        "RESEARCH_QUEUE",
    ),
)


def _validate_expansion_ledger(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Validate the discovery contract before it can reach a public payload."""

    validated = tuple(dict(row) for row in rows)
    seen: set[str] = set()
    required_text = {
        "market_id",
        "region",
        "currency",
        "market",
        "benchmark",
        "benchmark_kind",
        "authority",
        "source_url",
        "access",
        "access_note",
        "confidence",
        "status",
        "verified_on",
    }
    typed_urls = {
        "official_reference_url",
        "methodology_url",
        "data_endpoint",
        "terms_url",
    }
    for index, row in enumerate(validated):
        missing = (required_text | typed_urls) - row.keys()
        if missing:
            raise ValueError(
                f"expansion ledger row {index} lacks fields {sorted(missing)}"
            )
        if any(
            not isinstance(row[field], str) or not str(row[field]).strip()
            for field in required_text
        ):
            raise ValueError(f"expansion ledger row {index} has blank text fields")
        market_id = str(row["market_id"])
        currency = str(row["currency"])
        if not re.fullmatch(r"[A-Z0-9]{2,12}-[A-Z]{3}", market_id):
            raise ValueError(f"invalid expansion market_id {market_id!r}")
        if market_id in seen:
            raise ValueError(f"duplicate expansion market_id {market_id!r}")
        seen.add(market_id)
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError(f"invalid expansion currency {currency!r}")
        if currency not in _EXPANSION_ISO_4217:
            raise ValueError(f"unreviewed ISO 4217 expansion currency {currency!r}")
        if not market_id.endswith(f"-{currency}"):
            raise ValueError(
                f"expansion market_id {market_id!r} does not match currency {currency!r}"
            )
        try:
            date.fromisoformat(str(row["verified_on"]))
        except ValueError as exc:
            raise ValueError(
                f"invalid expansion verified_on {row['verified_on']!r}"
            ) from exc
        if row["access"] not in _EXPANSION_ACCESS:
            raise ValueError(f"invalid expansion access {row['access']!r}")
        if row["confidence"] not in _EXPANSION_CONFIDENCE:
            raise ValueError(f"invalid expansion confidence {row['confidence']!r}")
        if row["status"] not in _EXPANSION_STATUS:
            raise ValueError(f"invalid expansion status {row['status']!r}")
        for field in typed_urls:
            value = row[field]
            if value is None:
                continue
            parsed = urlsplit(str(value))
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(
                    f"expansion {market_id} field {field} must be an HTTPS URL"
                )
        if row["source_url"] != row["official_reference_url"]:
            raise ValueError(
                f"expansion {market_id} source_url must alias official_reference_url"
            )
    return validated


# Import-time validation makes an invalid discovery record a release failure,
# rather than a malformed row that is noticed only after API publication.
_validate_expansion_ledger(EXPANSION_LEDGER)


def _number(value: Decimal | float | str | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _percentile(values: list[float]) -> float | None:
    if len(values) < 20:
        return None
    latest = values[-1]
    below = sum(value < latest for value in values)
    tied = sum(value == latest for value in values)
    return 100.0 * (below + 0.5 * tied) / len(values)


def _robust_z(values: list[float]) -> float | None:
    if len(values) < 20:
        return None
    center = statistics.median(values)
    mad = statistics.median(abs(value - center) for value in values)
    scale = 1.4826 * mad
    if not math.isfinite(scale) or scale <= 0:
        # A genuinely flat sample is exactly at its own center.  Returning
        # zero is both finite and more truthful than treating "no dispersion"
        # as missing.  A non-central outlier with MAD=0 remains undefined.
        return 0.0 if values[-1] == center else None
    return (values[-1] - center) / scale


def _years_before(moment: datetime, years: int) -> datetime:
    try:
        return moment.replace(year=moment.year - years)
    except ValueError:
        # 29 February maps to the last valid day of February.
        return moment.replace(year=moment.year - years, day=28)


def _calendar_window_values(
    points: Iterable[tuple[datetime, float]], years: int
) -> tuple[list[float], datetime | None, datetime | None]:
    ordered = sorted(points, key=lambda point: point[0])
    if not ordered:
        return [], None, None
    end = ordered[-1][0]
    start = _years_before(end, years)
    return [value for event_time, value in ordered if event_time >= start], start, end


def _periods_per_year(cadence: str) -> int:
    """Translate a pack's simple ISO cadence into native observations/year.

    Daily and intraday official series follow business rather than calendar
    days.  Weekly sources retain weekly windows instead of inheriting the
    daily-market convention of 252 rows.
    """

    if cadence.startswith("PT"):
        amount = max(int(cadence[2:-1]), 1)
        per_day = {"H": 24, "M": 24 * 60, "S": 24 * 60 * 60}[cadence[-1]]
        return max(1, round(252 * per_day / amount))
    amount = max(int(cadence[1:-1]), 1)
    if cadence.endswith("W"):
        return max(1, round(52 / amount))
    return max(1, round(252 / amount))


def _annualized_change_vol(values: list[float], cadence: str) -> float | None:
    if len(values) < 21:
        return None
    changes = [right - left for left, right in zip(values[-21:-1], values[-20:])]
    if len(changes) < 2:
        return None
    return statistics.stdev(changes) * math.sqrt(_periods_per_year(cadence))


def _cadence_seconds(cadence: str) -> float:
    if cadence.startswith("PT"):
        amount = int(cadence[2:-1])
        return amount * {"H": 3600, "M": 60, "S": 1}[cadence[-1]]
    amount = int(cadence[1:-1])
    return amount * {"D": 86400, "W": 7 * 86400}[cadence[-1]]


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _publication_due(
    event_day: date,
    adapter,
    calendar,
    fallback_publication_time: datetime,
) -> datetime:
    clock = adapter.publication_clock
    if clock.calendar_id != calendar.calendar_id:
        raise ValueError("publication clock and pack settlement calendar differ")
    publication_day = calendar.add_business_days(event_day, clock.business_day_lag)
    publication_zone = ZoneInfo(clock.timezone_name)
    local_time = clock.local_time
    if local_time is None:
        observed_local = fallback_publication_time.astimezone(publication_zone)
        local_time = time(
            observed_local.hour,
            observed_local.minute,
            observed_local.second,
        )
    return datetime.combine(
        publication_day,
        local_time,
        tzinfo=publication_zone,
    ).astimezone(UTC)


def _next_event_day(event_day: date, cadence: str, calendar) -> date:
    amount = int(cadence[1:-1])
    if cadence.endswith("D"):
        return calendar.add_business_days(event_day, amount)
    return calendar.roll_forward(event_day + timedelta(weeks=amount))


def _publication_opportunity_clock(
    latest,
    adapter,
    calendar,
    cutoff: datetime,
) -> tuple[StalenessState, int | None, datetime | None, str]:
    """Age a row by source publication opportunities, not wall-clock days."""

    local_zone = calendar.timezone
    event_day = latest.event_time.astimezone(local_zone).date()
    try:
        # These calls validate both ends of the clock even when no opportunity
        # falls between them.  An unreviewed calendar year must fail loud.
        calendar.is_business_day(event_day)
        calendar.is_business_day(cutoff.astimezone(local_zone).date())
        baseline_due = _publication_due(
            event_day,
            adapter,
            calendar,
            latest.source_publication_time,
        )
        cadence = adapter.expected_cadence
        if cadence.startswith("PT"):
            interval = timedelta(seconds=_cadence_seconds(cadence))
            missed = 0
            next_due = baseline_due + interval
            while next_due <= cutoff:
                if calendar.is_business_day(
                    next_due.astimezone(local_zone).date()
                ):
                    missed += 1
                next_due += interval
            while not calendar.is_business_day(
                next_due.astimezone(local_zone).date()
            ):
                next_due += interval
        elif cadence.endswith("W"):
            # Weekly/native files often have no validated weekday schedule.
            # Calendar-aware due resolution handles holidays and publication
            # lag; ceiling preserves the native interval clock between files.
            elapsed = max((cutoff - baseline_due).total_seconds(), 0.0)
            missed = math.ceil(elapsed / _cadence_seconds(cadence)) if elapsed else 0
            next_event = _next_event_day(event_day, cadence, calendar)
            next_due = _publication_due(
                next_event,
                adapter,
                calendar,
                latest.source_publication_time,
            )
            while next_due <= cutoff:
                next_event = _next_event_day(next_event, cadence, calendar)
                next_due = _publication_due(
                    next_event,
                    adapter,
                    calendar,
                    latest.source_publication_time,
                )
        else:
            missed = 0
            next_event = _next_event_day(event_day, cadence, calendar)
            next_due = _publication_due(
                next_event,
                adapter,
                calendar,
                latest.source_publication_time,
            )
            while next_due <= cutoff:
                missed += 1
                next_event = _next_event_day(next_event, cadence, calendar)
                next_due = _publication_due(
                    next_event,
                    adapter,
                    calendar,
                    latest.source_publication_time,
                )
    except (CalendarUnavailableError, ValueError) as exc:
        return (
            StalenessState.UNKNOWN,
            None,
            None,
            f"calendar unavailable ({type(exc).__name__})",
        )

    aged = (
        StalenessState.FRESH
        if missed <= 2
        else StalenessState.AGING
        if missed <= 4
        else StalenessState.STALE
        if missed <= 8
        else StalenessState.DEAD
    )
    rank = {
        StalenessState.FRESH: 0,
        StalenessState.AGING: 1,
        StalenessState.STALE: 2,
        StalenessState.UNKNOWN: 2,
        StalenessState.DEAD: 3,
        StalenessState.UNAVAILABLE: 4,
    }
    effective = max((latest.staleness, aged), key=rank.__getitem__)
    return (
        effective,
        missed,
        next_due,
        "pack business calendar + adapter publication lag/cadence; stored state is a lower bound",
    )


def _latest_by_event(rows: Iterable[Observation]) -> list[Observation]:
    by_event: dict[datetime, Observation] = {}
    for row in rows:
        current = by_event.get(row.event_time)
        if current is None or (
            row.knowledge_time,
            row.source_publication_time,
            row.revision_id,
        ) > (
            current.knowledge_time,
            current.source_publication_time,
            current.revision_id,
        ):
            by_event[row.event_time] = row
    return sorted(by_event.values(), key=lambda item: item.event_time)


def _instrument_rows(
    observations: Iterable[Observation],
) -> dict[str, list[Observation]]:
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.instrument_id].append(observation)
    return {
        instrument_id: _latest_by_event(rows) for instrument_id, rows in grouped.items()
    }


def _instrument_is_public(pack: MarketPack, instrument) -> bool:
    adapter = pack.adapter_map[instrument.source_adapter_id]
    return (
        adapter.classification.value == "official_open"
        and adapter.redistribution_status is RedistributionStatus.ALLOWED
    )


def _instrument_is_derivable(pack: MarketPack, instrument) -> bool:
    adapter = pack.adapter_map[instrument.source_adapter_id]
    return adapter.redistribution_status in _DERIVABLE_REDISTRIBUTION


def _instrument_is_publicly_describable(pack: MarketPack, instrument) -> bool:
    """Whether an instrument may leave the private pack boundary at all."""

    adapter = pack.adapter_map[instrument.source_adapter_id]
    return adapter.redistribution_status is not RedistributionStatus.PROHIBITED


def _derived_context_projection(metric: Mapping[str, Any]) -> dict[str, Any]:
    """Construct the licensed-input response from an explicit safe allowlist."""

    return {
        "id": metric["id"],
        "mnemonic": metric["mnemonic"],
        "label": metric["label"],
        "semantic_role": metric["semantic_role"],
        "availability": "DERIVED_CONTEXT",
        "value": None,
        "unit": metric["unit"],
        "canonical_value": None,
        "canonical_unit": metric["canonical_unit"],
        "cadence": metric["cadence"],
        "redistribution_status": metric["redistribution_status"],
        "confidence": metric["confidence"],
        "status": metric["status"],
        "change_1_observation": None,
        "change_5_observations": None,
        "change_20_observations": None,
        "change_unit": metric["change_unit"],
        "robust_z_1y": metric["robust_z_1y"],
        "percentile_3y": metric["percentile_3y"],
        "change_vol_20_annualized": None,
        "change_vol_unit": metric["change_vol_unit"],
        "n_observations": metric["n_observations"],
        "statistics_window": {
            "basis": "calendar-time windows ending at the latest retained observation; dates withheld by policy",
            "minimum_observations": 20,
        },
        "day_count": metric["day_count"],
        "compounding": metric["compounding"],
        "formula": "restricted input; only non-reversible own-history statistics are public",
        "formula_version": metric["formula_version"],
        "explanation": metric["explanation"],
        "history_clock": "native event dates; raw dates and values withheld by policy",
        "history": [],
    }


def _metric(
    pack: MarketPack,
    instrument,
    rows: list[Observation],
    *,
    cutoff: datetime,
) -> dict[str, Any]:
    adapter = pack.adapter_map[instrument.source_adapter_id]
    public = _instrument_is_public(pack, instrument)
    derivable = _instrument_is_derivable(pack, instrument)
    derivable_rows = [
        row
        for row in rows
        if row.usable and row.redistribution_status in _DERIVABLE_REDISTRIBUTION
    ]
    if not derivable:
        derivable_rows = []
    public_rows = (
        [
            row
            for row in derivable_rows
            if row.redistribution_status is RedistributionStatus.ALLOWED
        ]
        if public
        else []
    )
    context_latest = derivable_rows[-1] if derivable_rows else None
    raw_latest = public_rows[-1] if public_rows else None
    statistics_rows = (
        [row for row in derivable_rows if row.event_time <= raw_latest.event_time]
        if raw_latest is not None
        else derivable_rows
    )
    statistics_points = [
        (row.event_time, value)
        for row in statistics_rows
        if (value := _number(row.value)) is not None
    ]
    canonical_values = [value for _, value in statistics_points]
    one_year_values, one_year_start, statistics_end = _calendar_window_values(
        statistics_points, 1
    )
    three_year_values, three_year_start, _ = _calendar_window_values(
        statistics_points, 3
    )
    is_rate = instrument.semantic_role in RATE_ROLES
    raw_canonical_values = [
        value for value in (_number(row.value) for row in public_rows) if value is not None
    ]
    display_values = (
        [value / 100.0 for value in raw_canonical_values]
        if is_rate
        else raw_canonical_values
    )
    change_unit = "bp" if is_rate else instrument.canonical_unit.value
    clock_latest = raw_latest if raw_latest is not None else context_latest
    freshness = (
        _publication_opportunity_clock(
            clock_latest,
            adapter,
            pack.settlement_calendar,
            cutoff,
        )
        if clock_latest is not None
        else None
    )
    effective_staleness = freshness[0] if freshness is not None else None
    missed_opportunities = freshness[1] if freshness is not None else None
    next_publication = freshness[2] if freshness is not None else None
    freshness_basis = freshness[3] if freshness is not None else None

    def change(periods: int) -> float | None:
        if len(raw_canonical_values) <= periods:
            return None
        return raw_canonical_values[-1] - raw_canonical_values[-1 - periods]

    availability = (
        "AVAILABLE"
        if raw_latest is not None
        else "DERIVED_CONTEXT"
        if context_latest is not None
        else "RESTRICTED"
        if not public
        else "UNAVAILABLE"
    )
    source_name = (
        raw_latest.source if raw_latest is not None else instrument.source_adapter_id
    )
    status = (
        effective_staleness.value.upper()
        if effective_staleness is not None
        else availability
    )
    metric = {
        "id": instrument.instrument_id,
        "mnemonic": instrument.mnemonic,
        "label": instrument.mnemonic.replace("_", " "),
        "semantic_role": instrument.semantic_role.value,
        "availability": availability,
        "value": _round(display_values[-1], 4)
        if display_values and raw_latest
        else None,
        "unit": "%" if is_rate else instrument.canonical_unit.value,
        "canonical_value": (
            _round(raw_canonical_values[-1], 4)
            if raw_canonical_values and raw_latest
            else None
        ),
        "canonical_unit": instrument.canonical_unit.value,
        "asof": raw_latest.event_time.date().isoformat() if raw_latest else None,
        "event_time": raw_latest.event_time.isoformat() if raw_latest else None,
        "published_at": (
            raw_latest.source_publication_time.isoformat() if raw_latest else None
        ),
        "knowledge_time": raw_latest.knowledge_time.isoformat() if raw_latest else None,
        "cadence": adapter.expected_cadence,
        "expected_next_update": (
            next_publication.isoformat()
            if next_publication is not None and raw_latest is not None
            else f"native {adapter.expected_cadence} publication clock"
            if raw_latest is not None
            else None
        ),
        "missed_publication_opportunities": missed_opportunities,
        "freshness_basis": freshness_basis,
        "observation_age_days": (
            _round((cutoff - raw_latest.event_time).total_seconds() / 86400.0, 2)
            if raw_latest is not None
            else None
        ),
        "source": source_name,
        "source_url": _SOURCE_URLS.get(instrument.source_adapter_id),
        "source_tier": adapter.classification.value,
        "redistribution_status": adapter.redistribution_status.value,
        "revision_status": raw_latest.quality.value if raw_latest else None,
        "revision_id": raw_latest.revision_id if raw_latest else None,
        "evidence_hash": raw_latest.evidence_hash if raw_latest else None,
        "confidence": (
            "high"
            if clock_latest
            and clock_latest.quality in {QualityState.VERIFIED, QualityState.REVISED}
            and effective_staleness in {StalenessState.FRESH, StalenessState.AGING}
            else "reduced"
            if clock_latest
            else "unavailable"
        ),
        "status": status,
        "change_1_observation": _round(change(1), 3) if raw_latest else None,
        "change_5_observations": _round(change(5), 3) if raw_latest else None,
        "change_20_observations": _round(change(20), 3) if raw_latest else None,
        "change_unit": change_unit,
        "robust_z_1y": _round(_robust_z(one_year_values), 3),
        "percentile_3y": _round(_percentile(three_year_values), 1),
        "change_vol_20_annualized": (
            _round(
                _annualized_change_vol(canonical_values, adapter.expected_cadence),
                3,
            )
            if raw_latest
            else None
        ),
        "change_vol_unit": f"{change_unit}/year^0.5",
        "n_observations": len(canonical_values),
        "statistics_window": {
            "one_year": {
                "from": one_year_start.isoformat() if one_year_start else None,
                "through": statistics_end.isoformat() if statistics_end else None,
                "observations": len(one_year_values),
                "minimum_observations": 20,
            },
            "three_year": {
                "from": three_year_start.isoformat() if three_year_start else None,
                "through": statistics_end.isoformat() if statistics_end else None,
                "observations": len(three_year_values),
                "minimum_observations": 20,
            },
            "basis": f"true elapsed calendar-time windows at native cadence {adapter.expected_cadence}; no upsampling",
        },
        "day_count": instrument.day_count.value if instrument.day_count else None,
        "compounding": (
            instrument.rate_compounding.value if instrument.rate_compounding else None
        ),
        "formula": (
            "official observation; rates displayed as canonical basis points / 100"
            if raw_latest
            else "restricted input; only non-reversible own-history statistics are public"
        ),
        "formula_version": "mm.atlas.observation.v1",
        "explanation": _role_explanation(instrument.semantic_role),
        "history_clock": "native event dates; duplicate vintages collapse at the requested knowledge cutoff",
        "history": (
            [
                [
                    row.event_time.date().isoformat(),
                    _round(
                        float(row.value) / 100.0 if is_rate else float(row.value),
                        4,
                    ),
                ]
                for row in public_rows[-180:]
                if row.value is not None
            ]
            if raw_latest
            else []
        ),
    }
    return (
        _derived_context_projection(metric)
        if availability == "DERIVED_CONTEXT"
        else metric
    )


def _role_explanation(role: SemanticRole) -> str:
    explanations = {
        SemanticRole.POLICY_FLOOR: "The lower administered rate intended to support the overnight corridor.",
        SemanticRole.POLICY_TARGET: "The central bank's operational policy reference for local overnight cash.",
        SemanticRole.POLICY_CEILING: "The upper administered or standing-facility rate for overnight cash.",
        SemanticRole.UNSECURED_OVERNIGHT: "The rate on overnight cash without pledged collateral.",
        SemanticRole.SECURED_OVERNIGHT: "The rate on overnight cash exchanged against collateral.",
        SemanticRole.TERM_1W: "A one-week funding rate; tenor makes it different from overnight cash.",
        SemanticRole.TERM_1M: "A one-month funding rate that includes term and credit/liquidity premia.",
        SemanticRole.TERM_3M: "A three-month funding rate that carries expectations and term premia.",
        SemanticRole.TBILL_3M: "The sovereign three-month cash benchmark in this local market.",
        SemanticRole.CP_3M: "Three-month corporate short-term borrowing cost.",
        SemanticRole.CD_3M: "Three-month bank certificate-of-deposit funding cost.",
        SemanticRole.RESERVE_BALANCES: "Settlement cash held by banks at the central bank.",
        SemanticRole.SYSTEM_LIQUIDITY: "A local stock or flow describing cash available to the banking system.",
        SemanticRole.CENTRAL_BANK_FACILITY_RATE: "The price of an explicit central-bank liquidity facility.",
        SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP: "Actual use of a central-bank liquidity facility.",
        SemanticRole.GOVERNMENT_CASH_BALANCE: "Government cash that can add or remove settlement balances when it moves.",
        SemanticRole.REPO_VOLUME: "Cash exchanged against collateral in the covered repo segment.",
        SemanticRole.RATE_MEDIAN: "The transaction-weighted middle of the benchmark distribution.",
        SemanticRole.RATE_P99: "The upper tail of transaction rates, useful for detecting concentrated pressure.",
        SemanticRole.FX_SWAP_BASIS: "The relative price of obtaining this currency or dollars through an FX swap.",
        SemanticRole.COLLATERAL_HAIRCUT: "The collateral discount applied by the covered funding market.",
    }
    return explanations.get(role, "Official local money-market observation.")


def _first_metric(metrics: list[dict], roles: tuple[SemanticRole, ...]) -> dict | None:
    # Prefer a current lower-priority role to a dead higher-priority role.  A
    # stale secured print, for example, must not displace a fresh unsecured
    # overnight benchmark merely because secured appears first in the tuple.
    for current_only in (True, False):
        for role in roles:
            for metric in metrics:
                if (
                    metric["semantic_role"] == role.value
                    and metric["availability"] == "AVAILABLE"
                    and (
                        metric.get("status") in {"FRESH", "AGING"}
                    )
                    is current_only
                ):
                    return metric
    return None


def _first_derived_metric(
    metrics: list[dict], roles: tuple[SemanticRole, ...]
) -> dict | None:
    for current_only in (True, False):
        for role in roles:
            for metric in metrics:
                if (
                    metric["semantic_role"] == role.value
                    and metric["availability"] == "DERIVED_CONTEXT"
                    and (
                        metric.get("status") in {"FRESH", "AGING"}
                    )
                    is current_only
                ):
                    return metric
    return None


def _spread_metric(
    pack: MarketPack,
    benchmark,
    anchor,
    rows_by_instrument: Mapping[str, list[Observation]],
    *,
    cutoff: datetime,
) -> dict[str, Any] | None:
    benchmark_adapter = pack.adapter_map[benchmark.source_adapter_id]
    anchor_adapter = pack.adapter_map[anchor.source_adapter_id]
    benchmark_rows = {
        row.event_time: row
        for row in rows_by_instrument.get(benchmark.instrument_id, [])
        if row.usable and row.redistribution_status is RedistributionStatus.ALLOWED
    }
    anchor_rows = {
        row.event_time: row
        for row in rows_by_instrument.get(anchor.instrument_id, [])
        if row.usable and row.redistribution_status is RedistributionStatus.ALLOWED
    }
    common = sorted(set(benchmark_rows) & set(anchor_rows))
    if not common:
        return None
    points: list[tuple[datetime, float]] = []
    for event_time in common:
        left = _number(benchmark_rows[event_time].value)
        right = _number(anchor_rows[event_time].value)
        if left is None or right is None:
            continue
        points.append((event_time, left - right))
    if not points:
        return None
    latest_time, latest_value = points[-1]
    latest_benchmark = benchmark_rows[latest_time]
    latest_anchor = anchor_rows[latest_time]
    benchmark_clock = _publication_opportunity_clock(
        latest_benchmark,
        benchmark_adapter,
        pack.settlement_calendar,
        cutoff,
    )
    anchor_clock = _publication_opportunity_clock(
        latest_anchor,
        anchor_adapter,
        pack.settlement_calendar,
        cutoff,
    )
    states = (benchmark_clock[0], anchor_clock[0])
    state_rank = {
        StalenessState.FRESH: 0,
        StalenessState.AGING: 1,
        StalenessState.STALE: 2,
        StalenessState.DEAD: 3,
        StalenessState.UNAVAILABLE: 4,
    }
    spread_state = (
        StalenessState.UNKNOWN
        if StalenessState.UNKNOWN in states
        else max(states, key=state_rank.__getitem__)
    )
    missed_values = [
        missed
        for missed in (benchmark_clock[1], anchor_clock[1])
        if isinstance(missed, int)
    ]
    one_year_values, one_year_start, statistics_end = _calendar_window_values(
        points, 1
    )
    three_year_values, three_year_start, _ = _calendar_window_values(points, 3)
    values = [value for _, value in points]
    history = [
        [event_time.date().isoformat(), _round(value, 3)]
        for event_time, value in points
    ]
    return {
        "id": f"{benchmark.instrument_id}_MINUS_{anchor.instrument_id}",
        "label": f"{benchmark.mnemonic.replace('_', ' ')} minus {anchor.mnemonic.replace('_', ' ')}",
        "value": _round(latest_value, 3),
        "unit": "bp",
        "asof": latest_time.date().isoformat(),
        "event_time": latest_time.isoformat(),
        "published_at": max(
            latest_benchmark.source_publication_time,
            latest_anchor.source_publication_time,
        ).isoformat(),
        "knowledge_time": max(
            latest_benchmark.knowledge_time,
            latest_anchor.knowledge_time,
        ).isoformat(),
        "semantic_role": "POLICY_RELATIVE_SPREAD",
        "availability": "AVAILABLE",
        "status": spread_state.value.upper(),
        "current_for_prose": spread_state
        in {StalenessState.FRESH, StalenessState.AGING},
        "observation_age_days": _round(
            max((cutoff - latest_time).total_seconds(), 0.0) / 86400.0,
            2,
        ),
        "missed_publication_opportunities": (
            max(missed_values) if len(missed_values) == 2 else None
        ),
        "freshness_basis": (
            "worse of the two input publication-opportunity clocks; the faster/stricter leg governs"
            if spread_state is not StalenessState.UNKNOWN
            else "UNKNOWN because at least one input calendar is outside its validated range"
        ),
        "cadence": "exact common event dates",
        "source": f"{benchmark.source_adapter_id} + {anchor.source_adapter_id}",
        "source_url": _SOURCE_URLS.get(benchmark.source_adapter_id),
        "formula": f"{benchmark.instrument_id} - {anchor.instrument_id}, canonical basis points",
        "formula_version": "mm.atlas.policy-spread.v1",
        "alignment": "exact event_time intersection; no forward-fill or upsampling",
        "input_lineage": [
            {
                "instrument_id": row.instrument_id,
                "event_time": row.event_time.isoformat(),
                "published_at": row.source_publication_time.isoformat(),
                "knowledge_time": row.knowledge_time.isoformat(),
                "revision_id": row.revision_id,
                "evidence_hash": row.evidence_hash,
            }
            for row in (latest_benchmark, latest_anchor)
        ],
        "change_1_observation": _round(values[-1] - values[-2], 3)
        if len(values) > 1
        else None,
        "change_5_observations": _round(values[-1] - values[-6], 3)
        if len(values) > 5
        else None,
        "change_20_observations": _round(values[-1] - values[-21], 3)
        if len(values) > 20
        else None,
        "robust_z_1y": _round(_robust_z(one_year_values), 3),
        "percentile_3y": _round(_percentile(three_year_values), 1),
        "n_observations": len(values),
        "statistics_window": {
            "one_year": {
                "from": one_year_start.isoformat() if one_year_start else None,
                "through": statistics_end.isoformat() if statistics_end else None,
                "observations": len(one_year_values),
                "minimum_observations": 20,
            },
            "three_year": {
                "from": three_year_start.isoformat() if three_year_start else None,
                "through": statistics_end.isoformat() if statistics_end else None,
                "observations": len(three_year_values),
                "minimum_observations": 20,
            },
            "basis": "true elapsed calendar-time windows; exact common event dates only",
        },
        "explanation": "Positive means the local market benchmark cleared above its compatible policy anchor on the same event date.",
        "history": history[-180:],
    }


def _market_read(
    pack: MarketPack,
    metrics: list[dict[str, Any]],
    spread: dict[str, Any] | None,
) -> tuple[str, str, str]:
    benchmark = _first_metric(metrics, _BENCHMARK_PRIORITY)
    if benchmark is None:
        derived_benchmark = _first_derived_metric(metrics, _BENCHMARK_PRIORITY)
        if derived_benchmark is not None:
            percentile = derived_benchmark.get("percentile_3y")
            derived_is_stale = derived_benchmark.get("status") not in {
                "FRESH",
                "AGING",
            }
            quant = (
                f"The withheld benchmark is at the {percentile:.1f}th percentile "
                f"of its own available history; robust z={derived_benchmark.get('robust_z_1y')}."
                if isinstance(percentile, (int, float))
                else "The licensed benchmark has insufficient retained history for a stable public percentile."
            )
            return (
                (
                    f"{pack.display_name} has derived context for "
                    f"{derived_benchmark['label']}, but its raw level and history "
                    "are withheld by redistribution policy."
                    + (
                        " The derived context is stale and cannot enter the current global ranking."
                        if derived_is_stale
                        else ""
                    )
                ),
                (
                    "Stale historical context only. " + quant
                    if derived_is_stale
                    else quant
                ),
                (
                    "A normalized licensed-input statistic is context, not a tradable "
                    "quote; confirm the level and market microstructure with an entitled source."
                ),
            )
        policy_anchor = _first_metric(metrics, _POLICY_PRIORITY)
        if policy_anchor is not None:
            value = policy_anchor.get("value")
            policy_is_stale = policy_anchor.get("status") not in {
                "FRESH",
                "AGING",
            }
            value_text = (
                f"{value:.4f}%" if isinstance(value, (int, float)) else "unavailable"
            )
            return (
                (
                    f"{pack.display_name} has a public {policy_anchor['label']} "
                    f"policy anchor at {value_text}, but no redistributable "
                    "traded benchmark."
                    + (" The policy observation is stale." if policy_is_stale else "")
                ),
                "Policy-only coverage cannot measure where local cash actually cleared or rank funding pressure.",
                "The missing benchmark may be licensed, delayed, or not yet collected; the policy level is not used as its substitute.",
            )
        plain = (
            f"{pack.display_name} is declared, but no public benchmark observation "
            "is available in the canonical store."
        )
        return (
            plain,
            "The market stays visible as an evidence gap; absence is not a calm signal.",
            "A collector may be healthy while licensed or insufficient-history inputs remain unavailable.",
        )
    level = benchmark.get("value")
    level_text = (
        f"{level:.4f}%" if isinstance(level, (int, float)) else "an unavailable level"
    )
    benchmark_is_stale = benchmark.get("status") not in {"FRESH", "AGING"}
    if (
        spread is not None
        and spread.get("current_for_prose") is True
        and isinstance(spread.get("value"), (int, float))
    ):
        signed = f"{spread['value']:+.1f} bp"
        plain = (
            f"{benchmark['label']} is {level_text}, {signed} versus its compatible "
            f"policy anchor on {spread['asof']}."
        )
    else:
        plain = f"{benchmark['label']} is {level_text} as of {benchmark['asof']}."
    if benchmark_is_stale:
        plain += " This is a stale historical observation and cannot enter the current global ranking."
    percentile = benchmark.get("percentile_3y")
    quant = (
        f"The {'stale historical ' if benchmark_is_stale else ''}benchmark is at the {percentile:.1f}th percentile of its own available history; "
        f"robust z={benchmark.get('robust_z_1y')}."
        if isinstance(percentile, (int, float))
        else "History is still too short for a stable own-market percentile; no cross-market level score is inferred."
    )
    countercase = (
        "A policy-relative level alone does not establish stress; confirm it with local volume, "
        "distribution, facility, liquidity, collateral, and calendar evidence."
    )
    return plain, quant, countercase


def build_global_money_market_atlas(
    packs: Iterable[MarketPack],
    observations_by_market: Mapping[str, Iterable[Observation]],
    *,
    collector_runs: Iterable[Mapping[str, Any]] = (),
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build a public, native-frequency atlas from already policy-filtered rows."""

    cutoff = (as_of or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    run_map: dict[tuple[str, str], dict[str, Any]] = {}
    run_order: dict[tuple[str, str], tuple[datetime, str]] = {}
    for run in collector_runs:
        finished_at = _parse_utc(run.get("finished_at"))
        if finished_at is None or finished_at > cutoff:
            continue
        key = (
            str(run.get("market_id", "")).upper(),
            str(run.get("adapter_id", "")),
        )
        # The lexical tie-break makes equal-finished_at duplicates stable as
        # well; ordinary duplicates are selected by the latest finished_at.
        canonical = repr(sorted((str(name), repr(value)) for name, value in run.items()))
        order = (finished_at, canonical)
        if key not in run_order or order > run_order[key]:
            run_map[key] = dict(run)
            run_order[key] = order
    markets: list[dict[str, Any]] = []
    deviations: list[tuple[float, str, str, float]] = []

    for pack in sorted(packs, key=lambda item: item.market_id):
        observations = [
            row
            for row in observations_by_market.get(pack.market_id, ())
            if row.market_id == pack.market_id
            and row.knowledge_time <= cutoff
            and row.event_time <= cutoff
        ]
        rows_by_instrument = _instrument_rows(observations)
        projected_instruments = [
            instrument
            for instrument in pack.instruments
            if _instrument_is_publicly_describable(pack, instrument)
        ]
        metrics = [
            _metric(
                pack,
                instrument,
                rows_by_instrument.get(instrument.instrument_id, []),
                cutoff=cutoff,
            )
            for instrument in projected_instruments
        ]
        benchmark = _first_metric(metrics, _BENCHMARK_PRIORITY)
        derived_benchmark = _first_derived_metric(metrics, _BENCHMARK_PRIORITY)
        policy_anchor = _first_metric(metrics, _POLICY_PRIORITY)
        benchmark_spec = (
            pack.instrument_map.get(str(benchmark["id"]))
            if benchmark is not None
            else None
        )
        anchor_spec = (
            pack.instrument_map.get(str(policy_anchor["id"]))
            if policy_anchor is not None
            else None
        )
        spread = (
            _spread_metric(
                pack,
                benchmark_spec,
                anchor_spec,
                rows_by_instrument,
                cutoff=cutoff,
            )
            if benchmark_spec is not None
            and anchor_spec is not None
            and benchmark_spec.instrument_id != anchor_spec.instrument_id
            else None
        )
        plain, quant, countercase = _market_read(pack, metrics, spread)
        comparison_benchmark = benchmark or derived_benchmark
        if (
            comparison_benchmark
            and comparison_benchmark.get("status") in {"FRESH", "AGING"}
            and isinstance(comparison_benchmark.get("robust_z_1y"), (int, float))
        ):
            deviations.append(
                (
                    abs(float(comparison_benchmark["robust_z_1y"])),
                    pack.market_id,
                    str(comparison_benchmark["label"]),
                    float(comparison_benchmark["robust_z_1y"]),
                )
            )
        public_available = sum(
            metric["availability"] == "AVAILABLE" for metric in metrics
        )
        derived_only = sum(
            metric["availability"] == "DERIVED_CONTEXT" for metric in metrics
        )
        restricted = sum(metric["availability"] == "RESTRICTED" for metric in metrics)
        faults = []
        adapters = []
        for adapter in pack.source_adapters:
            # A prohibited connector is outside the public projection entirely:
            # even its identifier, classification, and schedule are source
            # metadata that the pack contract forbids redistributing.
            if adapter.redistribution_status is RedistributionStatus.PROHIBITED:
                continue
            run = run_map.get((pack.market_id, adapter.adapter_id))
            run_metadata_public = (
                adapter.classification.value == "official_open"
                and adapter.redistribution_status is RedistributionStatus.ALLOWED
            )
            record = {
                "adapter_id": adapter.adapter_id,
                "classification": adapter.classification.value,
                "redistribution_status": adapter.redistribution_status.value,
                "expected_cadence": adapter.expected_cadence,
                "source_url": _SOURCE_URLS.get(adapter.adapter_id),
                "last_run_status": (
                    run.get("status")
                    if run is not None and run_metadata_public
                    else "NO_RUN_RECORDED"
                    if run is None and run_metadata_public
                    else "WITHHELD_BY_POLICY"
                ),
                "last_finished_at": (
                    run.get("finished_at")
                    if run is not None and run_metadata_public
                    else None
                ),
                "next_due": (
                    run.get("next_due")
                    if run is not None and run_metadata_public
                    else None
                ),
                "fault": (
                    run.get("fault")
                    if run is not None and run_metadata_public
                    else None
                ),
            }
            adapters.append(record)
            if record["last_run_status"] not in {
                "SUCCESS",
                "NO_RUN_RECORDED",
                "WITHHELD_BY_POLICY",
            }:
                faults.append(record)
        status = (
            "LIVE_REFERENCE"
            if benchmark is not None and benchmark.get("status") in {"FRESH", "AGING"}
            else "STALE_REFERENCE"
            if benchmark is not None
            else "DERIVED_CONTEXT"
            if derived_benchmark is not None
            else "POLICY_ONLY"
            if policy_anchor is not None
            else "DECLARED_UNAVAILABLE"
        )
        markets.append(
            {
                "market_id": pack.market_id,
                "monetary_area_id": pack.monetary_area_id,
                "region": _REGIONS.get(pack.market_id, "Other"),
                "display_name": pack.display_name,
                "jurisdiction_codes": list(pack.jurisdiction_codes),
                "currency": pack.currency,
                "timezone": pack.local_timezone,
                "settlement_calendar": pack.settlement_calendar.calendar_id,
                "policy_regime": pack.policy_regime.value,
                "support_status": pack.support_status.value,
                "status": status,
                "plain_language": plain,
                "quant_read": quant,
                "countercase": countercase,
                "benchmark": benchmark,
                "derived_benchmark": derived_benchmark,
                "policy_anchor": policy_anchor,
                "policy_relative_spread": spread,
                "metrics": metrics,
                "coverage": {
                    "declared_instruments": len(pack.instruments),
                    "public_projected_instruments": len(projected_instruments),
                    "omitted_by_policy": len(pack.instruments)
                    - len(projected_instruments),
                    "declared_adapters": len(pack.source_adapters),
                    "public_projected_adapters": len(adapters),
                    "omitted_adapters_by_policy": len(pack.source_adapters)
                    - len(adapters),
                    "public_available": public_available,
                    "derived_context": derived_only,
                    "restricted": restricted,
                    "unavailable": (
                        len(projected_instruments)
                        - public_available
                        - derived_only
                        - restricted
                    ),
                    "coverage_pct": _round(
                        100.0 * public_available / len(pack.instruments), 1
                    ),
                },
                "adapters": adapters,
                "faults": faults,
                "events": [
                    {"event_id": event.event_id, "label": event.label}
                    for event in pack.events
                ],
                "known_gaps": [
                    metric["label"]
                    for metric in metrics
                    if metric["availability"] != "AVAILABLE"
                ],
            }
        )

    strongest = max(deviations, default=None)
    available_count = sum(market["benchmark"] is not None for market in markets)
    live_count = sum(market["status"] == "LIVE_REFERENCE" for market in markets)
    stale_count = sum(market["status"] == "STALE_REFERENCE" for market in markets)
    derived_count = sum(market["status"] == "DERIVED_CONTEXT" for market in markets)
    policy_only_count = sum(market["status"] == "POLICY_ONLY" for market in markets)
    if strongest is not None:
        _, market_id, label, z_value = strongest
        strongest_read = {
            "market_id": market_id,
            "metric": label,
            "robust_z_1y": _round(z_value, 3),
            "explanation": "Largest absolute own-history benchmark deviation among non-stale markets with enough observations; not a cross-market stress score.",
        }
        global_plain = (
            f"{live_count} of {len(markets)} declared markets have a non-stale public traded benchmark; "
            f"{derived_count} have licensed-input derived benchmark context. "
            f"{market_id} has the largest benchmark deviation from its own recent history."
        )
    else:
        strongest_read = None
        global_plain = (
            f"{live_count} of {len(markets)} declared markets have a non-stale public traded benchmark and "
            f"{derived_count} have licensed-input derived benchmark context; "
            "none has both enough canonical history and a non-stale benchmark for current comparison."
        )

    expansion_ledger = _validate_expansion_ledger(EXPANSION_LEDGER)
    expansion_statuses: defaultdict[str, int] = defaultdict(int)
    for item in expansion_ledger:
        expansion_statuses[item["status"]] += 1
    expansion_regions = {item["region"] for item in expansion_ledger}

    return {
        "ok": bool(markets),
        "schema": ATLAS_SCHEMA,
        "generated_at": cutoff.isoformat(),
        "status": "PARTIAL" if live_count < len(markets) else "READY",
        "plain_language": global_plain,
        "quant_read": (
            "Cross-market ordering uses only non-stale benchmarks and the absolute robust z-score of each against its own elapsed calendar-year window. "
            "Raw rate levels, mixed tenors, and slow proxies are never ranked together."
        ),
        "strongest_divergence": strongest_read,
        "countercase": (
            "A local-history deviation can reflect a policy change, calendar turn, or methodology break. "
            "Read the local policy-relative spread, volumes, facilities, liquidity stocks, and source clock before inferring stress."
        ),
        "coverage": {
            "declared_markets": len(markets),
            "live_benchmarks": live_count,
            "available_benchmarks": available_count,
            "stale_benchmarks": stale_count,
            "derived_context_benchmarks": derived_count,
            "policy_only_markets": policy_only_count,
            "discovery_candidates": len(expansion_ledger),
            # Deprecated numeric aliases retained for v1 clients.  They are
            # discovery records, not delivery commitments or live coverage.
            "planned_markets": len(expansion_ledger),
            "expansion_markets": len(expansion_ledger),
            "global_discovery_universe": len(markets) + len(expansion_ledger),
            "expansion_regions": len(expansion_regions),
            "source_verified_candidates": expansion_statuses["SOURCE_VERIFIED"],
            "access_review_candidates": expansion_statuses["ACCESS_REVIEW"],
            "methodology_review_candidates": expansion_statuses["METHODOLOGY_REVIEW"],
            "research_queue_candidates": expansion_statuses["RESEARCH_QUEUE"],
            "compliance_blocked_candidates": expansion_statuses["COMPLIANCE_BLOCKED"],
            "legacy_aliases": {
                "planned_markets": "deprecated alias of discovery_candidates; not a roadmap commitment",
                "expansion_markets": "deprecated alias of discovery_candidates; not live coverage",
            },
        },
        "markets": markets,
        "expansion_ledger": [dict(item) for item in expansion_ledger],
        "expansion_scope": {
            "definition": (
                "Monetary areas with an identifiable official policy, overnight, "
                "repo, call-money or interbank reference reviewed as of 2026-08-21."
            ),
            "exclusions": (
                "Territories sharing a monetary authority are not duplicated; a "
                "ledger row is metadata discovery, not live data coverage."
            ),
            "promotion_gate": (
                "A candidate becomes a canonical pack only after methodology, "
                "point-in-time behavior, calendar, source reliability, legal rights, "
                "and production operations pass review."
            ),
            "compatibility_note": (
                "coverage.planned_markets and coverage.expansion_markets are deprecated aliases of "
                "coverage.discovery_candidates; neither means live or committed coverage."
            ),
        },
        "methodology": {
            "comparison": "own-market robust z and empirical percentile only",
            "robust_z": "(latest - median(observations in the trailing elapsed calendar year)) / (1.4826 * MAD); minimum 20 observations; a flat series at its median is 0",
            "percentile": "tie-aware empirical midrank over observations in the trailing three elapsed calendar years; minimum 20 observations",
            "policy_spread": "exact event-time intersection in canonical basis points, aged by the worse input publication clock",
            "frequency": "changes are observation-count based and retain each adapter's native cadence",
            "freshness": "observation currentness counts missed pack-calendar publication opportunities using adapter lag/time; FRESH <=2, AGING 3-4, STALE 5-8, DEAD >=9; invalid calendars are UNKNOWN and collector health never refreshes an old observation",
            "publication_boundary": "already collected canonical observations; no collection on request",
            "role": "context-only; does not enter the Seiche composite or constitute investment advice",
        },
        "caveats": [
            "Every market uses local conventions and its own historical distribution.",
            "Metadata-only and derived-only inputs may remain visible under their public policy, but prohibited sources and their metadata are omitted entirely.",
            "Unavailable and stale evidence never becomes a zero or a calm reading.",
            "Reference packs are not promoted to supported until their point-in-time validation gates pass.",
            "The expansion ledger is a dated source-discovery universe, not an exhaustive claim about every jurisdiction or a promise of live observations.",
        ],
        "legal_notices": [
            {
                "notice": (
                    "Authority names and source links identify data origin. Derived "
                    "spreads, statistics, and explanations are Seiche modifications, "
                    "not official central-bank analysis or endorsement."
                )
            },
            {
                "source": "Federal Reserve Bank of New York reference rates",
                "terms_url": "https://www.newyorkfed.org/privacy/termsofuse.html",
                "notice": (
                    "Seiche is independent of and not endorsed by the New York Fed "
                    "and is responsible for its republication and analysis."
                ),
            },
            {
                "source": "Bank of Korea ECOS",
                "terms_url": "https://www.bok.or.kr/portal/main/contents.do?menuNo=200228",
                "notice": (
                    "Bank of Korea source labels identify official observations. "
                    "Seiche-derived statistics and explanations are modifications, "
                    "not Bank of Korea analysis or endorsement."
                ),
            },
            {
                "source": "Korea Overnight Financing Repo Rate",
                "terms_url": "https://www.kofr.kr/info/legal-disclainer.jsp?sMenuId=003004&sLangCd=02",
                "notice": (
                    "KOFR is cataloged as metadata-only until affirmative raw-value "
                    "redistribution permission is established."
                ),
            },
        ],
    }
