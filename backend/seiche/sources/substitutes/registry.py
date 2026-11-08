"""Registry of free substitutes for the FRED series Seiche cannot republish.

Owner approval: 2026-11-08, "go with them all just make sure everything
starts working". Evidence: the withheld-data inventory of 2026-11-09,
section 4.1 (catalogue), 4.2 (per-series mapping and class) and 5 (terms).

This module is the runtime source of truth. ``registry_document()`` renders
it as the machine-readable file ``docs/rights/free-substitutes-20261109.json``
(regenerate with ``python -m seiche.sources.substitutes.registry``); a test
keeps the two identical.

Every served value goes through :func:`label`, which returns
:class:`~seiche.sources.substitutes._common.SubstituteRow` objects that carry
``substitute_for`` and ``substitute_class``. Adapters only return unlabelled
``SourceObservation`` objects, which no surface should serve.

Where this plugs in (not wired yet, by design): the engines and the public
rights projection belong to other in-flight work. Once that lands, a caller
asks ``await rows_for("VIXCLS")`` (for example inside the legacy assembler,
in place of the withheld FRED read) and gates publication on each row's
``redistribution_status``. Nothing here edits an engine, ``config.py`` or
``public_rights.py``.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from types import ModuleType

import httpx

from seiche.sources.substitutes._common import (
    REDISTRIBUTION_STATUSES,
    SUBSTITUTE_CLASSES,
    USER_AGENT,
    Clock,
    SourceObservation,
    SubstituteRow,
    validate_fixed_url,
)

SCHEMA = "seiche.free-substitutes.v1"
DOCUMENT_PATH = "docs/rights/free-substitutes-20261109.json"
APPROVAL = {
    "approved_by": "owner",
    "approved_on": "2026-11-08",
    "approval_text": "go with them all just make sure everything starts working",
    "evidence": "Seiche withheld-data inventory, 2026-11-09, sections 4.1, 4.2 and 5",
}
CLASS_DEFINITIONS = {
    "close substitute": "the same concept with a different method, cadence or publisher",
    "proxy": "a related signal that can stand in for the analytical role, but not the same concept",
}
LABEL_RULE = (
    "Every served row carries substitute_for and substitute_class. A substitute is "
    "never the FRED original: never show it under the FRED id, title, publisher or "
    "units, and never splice it into the FRED history."
)


@dataclass(frozen=True, slots=True)
class FredOriginal:
    fred_id: str
    title: str
    original_publisher: str
    cadence: str
    role: str
    blocked_because: str
    condition: str | None = None


@dataclass(frozen=True, slots=True)
class SubstituteSource:
    substitute_id: str
    module: str
    title: str
    publisher: str
    endpoints: tuple[str, ...]
    terms_url: str
    terms_quote: str
    terms_checked: str
    terms_status: str
    redistribution_status: str
    cadence: str
    history: str
    attribution: str
    residual_risk: str
    owner_decision: str | None = None
    charge_rule: str | None = None
    endpoint_pattern: str | None = None


@dataclass(frozen=True, slots=True)
class SubstituteEntry:
    substitute_for: str
    substitute_id: str
    measure: str
    substitute_class: str
    rank: int
    cadence_difference: str
    note: str | None = None


_LICENSED = "licensed index; no free republishable source of the same data"
_LSEG = "EIA republishes LSEG (Refinitiv) spot prices; permission needed"
_IMF = "IMF permission needed for automated download and commercial reuse"

FRED_ORIGINALS: dict[str, FredOriginal] = {item.fred_id: item for item in (
    FredOriginal("VIXCLS", "CBOE Volatility Index: VIX", "Cboe", "daily close",
                 "TELL market-stress leg (weight 0.35), playbook, ML feature, capital risk context",
                 _LICENSED),
    FredOriginal("BAMLH0A0HYM2", "ICE BofA US High Yield Index Option-Adjusted Spread",
                 "ICE Data Indices", "daily", "TELL (weight 0.30), playbook, ML, capital risk context",
                 _LICENSED),
    FredOriginal("BAMLC0A0CM", "ICE BofA US Corporate Index Option-Adjusted Spread",
                 "ICE Data Indices", "daily", "TELL (weight 0.15), playbook", _LICENSED),
    FredOriginal("SP500", "S&P 500", "S&P Dow Jones Indices", "daily close",
                 "playbook outcome, book returns", _LICENSED),
    FredOriginal("NASDAQCOM", "NASDAQ Composite Index", "Nasdaq", "daily close",
                 "refereegli equity leg", _LICENSED),
    FredOriginal("DCOILWTICO", "Crude Oil Prices: West Texas Intermediate (WTI)",
                 "LSEG data republished by EIA", "daily spot",
                 "oilfunding, ballast price leg, estuary energy", _LSEG),
    FredOriginal("DCOILBRENTEU", "Crude Oil Prices: Brent, Europe",
                 "LSEG data republished by EIA", "daily spot", "oilfunding, estuary energy", _LSEG),
    FredOriginal("DHHNGSP", "Henry Hub Natural Gas Spot Price",
                 "LSEG data republished by EIA", "daily spot",
                 "ballast price leg, estuary energy", _LSEG),
    FredOriginal("TEDRATE", "TED Spread", "computed by FRED from ICE LIBOR",
                 "daily, discontinued in 2022", "ML pretraining rows only",
                 "computed from ICE LIBOR, discontinued, no other publisher"),
    FredOriginal("PALLFNFINDEXM", "Global price index of All Commodities", "IMF", "monthly",
                 "estuary materials (broad)", _IMF),
    FredOriginal("PCOPPUSDM", "Global price of Copper", "IMF", "monthly", "estuary industrial", _IMF),
    FredOriginal("PALUMUSDM", "Global price of Aluminum", "IMF", "monthly", "estuary industrial", _IMF),
    FredOriginal("PNICKUSDM", "Global price of Nickel", "IMF", "monthly", "estuary industrial", _IMF),
    FredOriginal("PCOALAUUSDM", "Global price of Coal, Australia", "IMF", "monthly",
                 "estuary energy", _IMF),
    FredOriginal("PWHEAMTUSDM", "Global price of Wheat", "IMF", "monthly",
                 "estuary agriculture", _IMF),
    FredOriginal("PMAIZMTUSDM", "Global price of Corn", "IMF", "monthly",
                 "estuary agriculture", _IMF),
    FredOriginal("NFCI", "Chicago Fed National Financial Conditions Index", "Federal Reserve Bank of Chicago",
                 "weekly", "SONAR and series export only",
                 "republication or distribution needs Chicago Fed written permission"),
    FredOriginal("JPNASSETS", "Bank of Japan: Total Assets for Japan", "Bank of Japan", "monthly",
                 "refereegli", "BoJ notice excepts copying for commercial purposes",
                 condition="only if the owner reads the BoJ non-commercial clause against Seiche"),
    FredOriginal("DCPN3M", "3-Month AA Nonfinancial Commercial Paper Rate",
                 "Federal Reserve Board, from DTCC data", "daily",
                 "money desk unsecured section, cpsentinel, term_funding",
                 "DTCC provenance under the Board notice is an open owner decision",
                 condition="only if the owner decides DTCC provenance blocks the Board CP rates"),
    FredOriginal("DCPF3M", "3-Month AA Financial Commercial Paper Rate",
                 "Federal Reserve Board, from DTCC data", "daily",
                 "money desk unsecured section, cpsentinel, term_funding",
                 "DTCC provenance under the Board notice is an open owner decision",
                 condition="only if the owner decides DTCC provenance blocks the Board CP rates"),
)}
BLOCKED_FRED_IDS = frozenset(
    fred_id for fred_id, item in FRED_ORIGINALS.items() if item.condition is None
)
CONDITIONAL_FRED_IDS = frozenset(FRED_ORIGINALS) - BLOCKED_FRED_IDS

_FRB_QUOTE = (
    "Unless otherwise indicated, information on Board's website is in the public domain "
    "and may be copied and distributed without permission. Please cite to the Board as "
    "the source of the information."
)
_FRB_TERMS = "https://www.federalreserve.gov/disclaimer.htm"
_FRB = "Board of Governors of the Federal Reserve System"

SOURCES: dict[str, SubstituteSource] = {item.substitute_id: item for item in (
    SubstituteSource(
        "ALT-OFRFSI", "seiche.sources.substitutes.ofr_fsi", "OFR Financial Stress Index",
        "Office of Financial Research, U.S. Department of the Treasury",
        ("https://www.financialresearch.gov/financial-stress-index/data/fsi.csv",),
        "https://www.financialresearch.gov/legal-notices/",
        "No copyright may be claimed for any work on this website that was created by a "
        "federal employee in the course of his or her duties. However, credit is requested "
        "if you reproduce or copy any such work.",
        "2026-11-09", "public_domain_credit_requested", "allowed",
        "daily; published with data current from two business days prior", "2000-01-03 onward",
        "Source: Office of Financial Research, OFR Financial Stress Index "
        "(https://www.financialresearch.gov/financial-stress-index/).",
        "Low. OFR computes the index from licensed market inputs (Refinitiv Datastream and "
        "Bloomberg per its working paper); only OFR's published index values are read.",
    ),
    SubstituteSource(
        "ALT-CISS", "seiche.sources.substitutes.ecb_ciss",
        "New Composite Indicator of Systemic Stress (CISS), United States",
        "European Central Bank",
        ("https://data-api.ecb.europa.eu/service/data/CISS/D.US.Z0Z.4F.EC.SS_CIN.IDX?format=csvdata",),
        "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "Subject to the exception below, users of this website may make free use of the "
        "information obtained directly from it subject to the following conditions: When "
        "such information is distributed or reproduced, it must appear accurately and the "
        "ECB must be cited as the source.",
        "2026-11-09", "free_use_with_citation", "allowed",
        "daily; updated with the previous day's data", "1980-01-02 onward",
        "Source: European Central Bank, New CISS for the United States, series "
        "CISS.D.US.Z0Z.4F.EC.SS_CIN.IDX. Values unmodified.",
        "Low. ECB statistics; any modification (resampling, growth rates) must be stated.",
        charge_rule=(
            "If ever placed in a sold document, buyers must be told before paying and on each "
            "access that the information may be obtained free of charge from the ECB website."
        ),
    ),
    SubstituteSource(
        "ALT-GZ", "seiche.sources.substitutes.frb_gz",
        "GZ credit spread and excess bond premium", _FRB,
        ("https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv",),
        _FRB_TERMS, _FRB_QUOTE, "2026-11-09", "public_domain_with_citation", "allowed",
        "monthly; about the fourth business day of the next month", "1973-01 onward",
        "Source: Board of Governors of the Federal Reserve System, GZ credit spread and "
        "excess bond premium (FEDS Notes data).",
        "Residual. The FEDS Note names commercial bond-price inputs; the CSV carries no "
        "restriction notice and only the Board's published calculations are read.",
    ),
    SubstituteSource(
        "ALT-PINK", "seiche.sources.substitutes.wb_pink",
        "World Bank Commodity Price Data (The Pink Sheet), monthly", "World Bank",
        ("https://www.worldbank.org/en/research/commodity-markets",),
        "https://data.worldbank.org/summary-terms-of-use",
        "Unless indicated otherwise in the data or indicator metadata, you are free to copy, "
        "distribute, adapt, display or include the data in other products for commercial or "
        "noncommercial purposes at no cost under a Creative Commons Attribution 4.0 "
        "International License",
        "2026-11-09", "cc_by_4_0", "allowed",
        "monthly averages; first week of the next month", "1960-01 onward (WTI from 1982-01, "
        "Australian coal from 1970-01)",
        "Source: World Bank, Commodity Price Data (The Pink Sheet), licensed under CC BY 4.0 "
        "(https://creativecommons.org/licenses/by/4.0/). Values unchanged; Seiche dates each "
        "month at its last calendar day.",
        "Medium. The workbook's own source notes list Bloomberg Finance L.P. and Thomson "
        "Reuters Datastream (now LSEG) for crude oil, Henry Hub gas and LME metals. The World "
        "Bank terms warn that third-party data may need the original provider's consent "
        "\"Where applicable, these conditions are included in the dataset or indicator "
        "metadata\"; none was found on 2026-11-09.",
        endpoint_pattern=(
            r"https://thedocs\.worldbank\.org/en/doc/[0-9a-z]+(?:-[0-9a-z]+){0,4}"
            r"/related/CMO-Historical-Data-Monthly\.xlsx"
        ),
    ),
    SubstituteSource(
        "ALT-HQM", "seiche.sources.substitutes.ust_hqm",
        "Treasury HQM corporate bond par yields, with Treasury spreads",
        "U.S. Department of the Treasury",
        ("https://home.treasury.gov/system/files/226/hqm_qh_pars.xls",
         "https://www.federalreserve.gov/releases/h15/data/FRB_H15_xml.zip"),
        "https://www.copyright.gov/title17/92chap1.html",
        "17 U.S.C. 105(a): \"Copyright protection under this title is not available for any "
        "work of the United States Government\". Treasury leg: " + _FRB_QUOTE,
        "2026-11-09", "public_domain_us_government_work", "allowed",
        "monthly averages", "1984-01 onward",
        "Source: U.S. Department of the Treasury, HQM Corporate Bond Yield Curve, monthly "
        "average par yields; Treasury constant maturity yields from the Board of Governors "
        "of the Federal Reserve System, H.15. Spread computed by Seiche.",
        "Low. HQM input vendor not checked.",
    ),
    SubstituteSource(
        "ALT-CPBILL", "seiche.sources.substitutes.frb_cp_bill",
        "AA financial 3-month commercial paper minus 3-month Treasury bill", _FRB,
        ("https://www.federalreserve.gov/releases/cp/data/FRB_CP_xml.zip",
         "https://www.federalreserve.gov/releases/h15/data/FRB_H15_xml.zip"),
        _FRB_TERMS,
        _FRB_QUOTE + " CP release: \"The Federal Reserve Board's CP release is derived from "
        "data supplied by The Depository Trust & Clearing Corporation (DTCC)\" "
        "(https://www.federalreserve.gov/releases/cp/about.htm).",
        "2026-11-09", "unclear_dtcc_provenance", "metadata_only",
        "daily, business days", "1997-01-02 onward",
        "Source: Board of Governors of the Federal Reserve System, Commercial Paper release "
        "(AA financial, 90 days) and H.15 (3-month Treasury bill, secondary market). Spread "
        "computed by Seiche. The Board derives the CP rates from DTCC data.",
        "Open. The CP leg is the same DTCC-derived data as DCPF3M.",
        owner_decision=(
            "Whether DTCC provenance makes the Board CP rates an \"otherwise indicated\" case "
            "(the same decision that holds DCPN3M and DCPF3M). Until she decides, use it only "
            "for its internal role (ML pretraining) and publish no values."
        ),
    ),
    SubstituteSource(
        "ALT-BISCBTA", "seiche.sources.substitutes.bis_cbta",
        "BIS central bank total assets, Japan", "Bank for International Settlements",
        ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBTA/1.0/M.JP.B.XDC.JPY.N?format=csv",),
        "https://www.bis.org/about/legal/permitted-use-statistics",
        "The use of the statistics is unrestricted, provided that: 1. if the statistics are "
        "reproduced, the BIS must be cited in your publication or product as the source of "
        "the statistics; ... 3. your use of the statistics must not be potentially "
        "misleading, for example by implying endorsement or affiliation with the BIS or any "
        "body affiliated with the BIS; 4. if the statistics will be used in a commercial "
        "publication or product, their inclusion in the publication or product will not "
        "result in any additional charge to subscribers or other users",
        "2026-11-09 (page refuses scripted clients; read through a fetch tool)",
        "unrestricted_with_conditions_free_only", "allowed",
        "monthly end-of-month values, released quarterly", "1953-01 onward",
        "Source: Bank for International Settlements, central bank total assets (WS_CBTA), "
        "Japan, BIS-spliced from Bank of Japan data. No BIS endorsement implied.",
        "Low. BIS splices national central bank data.",
        charge_rule=(
            "Free surfaces only: never include in a product or tier that charges subscribers "
            "or users (BIS condition 4)."
        ),
    ),
    SubstituteSource(
        "ALT-FCIG", "seiche.sources.substitutes.frb_fcig",
        "FCI-G financial conditions index (baseline, three-year lookback)", _FRB,
        ("https://www.federalreserve.gov/econres/notes/feds-notes/fci_g_public_monthly_3yr.csv",),
        _FRB_TERMS, _FRB_QUOTE, "2026-11-09", "public_domain_with_citation", "allowed",
        "monthly; research product with no fixed release calendar", "1990-01 onward",
        "Source: Board of Governors of the Federal Reserve System, FCI-G (baseline, "
        "three-year lookback), a FEDS Notes research product.",
        "Residual. Inputs include commercial series; only the Board's published index and "
        "factor contributions are read. The Board says the index may be revised or "
        "discontinued without notice.",
    ),
)}

_OFR_LAG = "OFR FSI is daily, about two business days behind"
_CISS_LAG = "CISS is daily, one day behind"

ENTRIES: tuple[SubstituteEntry, ...] = (
    SubstituteEntry("VIXCLS", "ALT-OFRFSI", "volatility", "proxy", 1,
                    f"{_OFR_LAG}; VIXCLS was a same-day daily close"),
    SubstituteEntry("VIXCLS", "ALT-CISS", "ciss", "proxy", 2,
                    f"{_CISS_LAG}; VIXCLS was a same-day daily close"),
    SubstituteEntry("BAMLH0A0HYM2", "ALT-GZ", "gz_spread", "proxy", 1,
                    "GZ is monthly; the ICE spread was daily",
                    "A close substitute for a corporate credit spread, but not high-yield "
                    "specific, so a proxy for this series (section 4.2)."),
    SubstituteEntry("BAMLH0A0HYM2", "ALT-OFRFSI", "credit", "proxy", 2,
                    f"{_OFR_LAG}; the ICE spread was daily"),
    SubstituteEntry("BAMLH0A0HYM2", "ALT-CISS", "ciss", "proxy", 3,
                    f"{_CISS_LAG}; the ICE spread was daily"),
    SubstituteEntry("BAMLC0A0CM", "ALT-GZ", "gz_spread", "close substitute", 1,
                    "GZ is monthly; the ICE spread was daily"),
    SubstituteEntry("BAMLC0A0CM", "ALT-HQM", "spread_10y", "proxy", 2,
                    "HQM is a monthly average; the ICE spread was daily",
                    "HQM 10-year par yield minus the 10-year Treasury constant maturity "
                    "yield, both monthly averages; 2, 5 and 30 years are also parsed."),
    SubstituteEntry("BAMLC0A0CM", "ALT-OFRFSI", "credit", "proxy", 3,
                    f"{_OFR_LAG}; the ICE spread was daily"),
    SubstituteEntry("SP500", "ALT-OFRFSI", "equity_valuation", "proxy", 1,
                    f"{_OFR_LAG}; SP500 was a same-day daily close",
                    "A stress contribution, not an index level or a return."),
    SubstituteEntry("SP500", "ALT-CISS", "ciss", "proxy", 2,
                    f"{_CISS_LAG}; SP500 was a same-day daily close"),
    SubstituteEntry("NASDAQCOM", "ALT-OFRFSI", "equity_valuation", "proxy", 1,
                    f"{_OFR_LAG}; NASDAQCOM was a same-day daily close",
                    "A stress contribution, not an index level or a return."),
    SubstituteEntry("NASDAQCOM", "ALT-CISS", "ciss", "proxy", 2,
                    f"{_CISS_LAG}; NASDAQCOM was a same-day daily close"),
    SubstituteEntry("DCOILWTICO", "ALT-PINK", "crude_oil_wti", "close substitute", 1,
                    "monthly average; DCOILWTICO was a daily spot price",
                    "Same benchmark (WTI, 40 API), monthly average."),
    SubstituteEntry("DCOILBRENTEU", "ALT-PINK", "crude_oil_brent", "close substitute", 1,
                    "monthly average; DCOILBRENTEU was a daily spot price",
                    "Same benchmark (UK Brent, 38 API), monthly average."),
    SubstituteEntry("DHHNGSP", "ALT-PINK", "natural_gas_us", "close substitute", 1,
                    "monthly average; DHHNGSP was a daily spot price",
                    "Pink Sheet definition: spot price at Henry Hub, Louisiana."),
    SubstituteEntry("TEDRATE", "ALT-CPBILL", "cp_aa_financial_3m_minus_tbill_3m",
                    "close substitute", 1,
                    "daily, both legs on the Board's business-day calendar; TEDRATE was daily "
                    "until its 2022 discontinuation",
                    "History starts 1997-01-02, so TEDRATE's earlier history is not covered. "
                    "Both legs are on a discount basis with a 360-day year (H.15 footnotes 3 "
                    "and 4)."),
    SubstituteEntry("TEDRATE", "ALT-OFRFSI", "funding", "proxy", 2,
                    f"{_OFR_LAG}; TEDRATE was daily"),
    SubstituteEntry("PALLFNFINDEXM", "ALT-PINK", "index_total", "close substitute", 1,
                    "monthly, as the FRED series",
                    "Different weights and base year (World Bank 2010=100)."),
    SubstituteEntry("PALLFNFINDEXM", "ALT-PINK", "index_energy", "close substitute", 2,
                    "monthly, as the FRED series",
                    "Use with the non-energy index; different weights and base year."),
    SubstituteEntry("PALLFNFINDEXM", "ALT-PINK", "index_non_energy", "close substitute", 2,
                    "monthly, as the FRED series",
                    "Use with the energy index; different weights and base year."),
    SubstituteEntry("PCOPPUSDM", "ALT-PINK", "copper", "close substitute", 1,
                    "monthly, as the FRED series", "LME grade A copper, settlement price."),
    SubstituteEntry("PALUMUSDM", "ALT-PINK", "aluminum", "close substitute", 1,
                    "monthly, as the FRED series", "LME primary aluminum, settlement price."),
    SubstituteEntry("PNICKUSDM", "ALT-PINK", "nickel", "close substitute", 1,
                    "monthly, as the FRED series", "LME nickel cathodes, settlement price."),
    SubstituteEntry("PCOALAUUSDM", "ALT-PINK", "coal_australia", "close substitute", 1,
                    "monthly, as the FRED series",
                    "Newcastle 6000 kcal/kg; futures price from February 2022."),
    SubstituteEntry("PWHEAMTUSDM", "ALT-PINK", "wheat_us_hrw", "close substitute", 1,
                    "monthly, as the FRED series", "US no. 2 hard red winter, Gulf export price."),
    SubstituteEntry("PMAIZMTUSDM", "ALT-PINK", "maize", "close substitute", 1,
                    "monthly, as the FRED series", "US no. 2 yellow, f.o.b. US Gulf ports."),
    SubstituteEntry("NFCI", "ALT-OFRFSI", "fsi", "proxy", 1,
                    f"{_OFR_LAG}; NFCI was weekly"),
    SubstituteEntry("NFCI", "ALT-CISS", "ciss", "proxy", 2, f"{_CISS_LAG}; NFCI was weekly"),
    SubstituteEntry("NFCI", "ALT-FCIG", "fci_g", "proxy", 3,
                    "FCI-G is monthly; NFCI was weekly"),
    SubstituteEntry("JPNASSETS", "ALT-BISCBTA", "total_assets_jpy", "close substitute", 1,
                    "monthly end-of-month values released quarterly, so up to about four "
                    "months behind; JPNASSETS was monthly",
                    "BIS units are JPY billions; FRED shows JPNASSETS in 100 million yen."),
    SubstituteEntry("DCPN3M", "ALT-OFRFSI", "funding", "proxy", 1,
                    f"{_OFR_LAG}; DCPN3M was daily"),
    SubstituteEntry("DCPF3M", "ALT-OFRFSI", "funding", "proxy", 1,
                    f"{_OFR_LAG}; DCPF3M was daily"),
)


def adapter(substitute_id: str) -> ModuleType:
    """Import the adapter module of one substitute."""

    return importlib.import_module(SOURCES[substitute_id].module)


def entries_for(fred_id: str) -> tuple[SubstituteEntry, ...]:
    if fred_id not in FRED_ORIGINALS:
        raise KeyError(f"{fred_id} is not a blocked FRED series in this registry")
    return tuple(sorted(
        (entry for entry in ENTRIES if entry.substitute_for == fred_id),
        key=lambda entry: (entry.rank, entry.substitute_id, entry.measure),
    ))


def notice(entry: SubstituteEntry) -> str:
    original = FRED_ORIGINALS[entry.substitute_for]
    source = SOURCES[entry.substitute_id]
    return (
        f"Seiche substitute for FRED {entry.substitute_for}, class: {entry.substitute_class}. "
        f"This is {source.title} ({entry.measure}) from {source.publisher}, not "
        f"{entry.substitute_for} itself (original source: {original.original_publisher})."
    )


def attribution(entry: SubstituteEntry) -> str:
    return (
        f"{SOURCES[entry.substitute_id].attribution} Shown by Seiche as a "
        f"{entry.substitute_class} for FRED {entry.substitute_for}, not as "
        f"{entry.substitute_for}."
    )


def label(
    entry: SubstituteEntry, observations: Iterable[SourceObservation]
) -> tuple[SubstituteRow, ...]:
    """Turn adapter output into servable rows for one registry entry."""

    source = SOURCES[entry.substitute_id]
    measure = adapter(entry.substitute_id).MEASURES[entry.measure]
    text_notice = notice(entry)
    text_attribution = attribution(entry)
    rows = []
    for item in observations:
        if item.substitute_id != entry.substitute_id or item.measure != entry.measure:
            continue
        rows.append(SubstituteRow(
            substitute_for=entry.substitute_for,
            substitute_class=entry.substitute_class,
            substitute_id=entry.substitute_id,
            measure=entry.measure,
            label=f"{source.title}: {measure.description}",
            notice=text_notice,
            event_date=item.event_date,
            period=item.period,
            value=item.value,
            unit=item.unit,
            frequency=item.frequency,
            source=item.source,
            publisher=item.publisher,
            source_url=item.source_url,
            terms_url=item.terms_url,
            attribution=text_attribution,
            redistribution_status=source.redistribution_status,
            knowledge_time=item.knowledge_time,
            raw_documents=item.raw_documents,
            row_sha256=item.row_sha256,
        ))
    return tuple(rows)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=60.0, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    )


async def collect(
    substitute_id: str,
    client: httpx.AsyncClient | None = None,
    *,
    clock: Clock | None = None,
) -> tuple[SourceObservation, ...]:
    """Fetch and validate one substitute source (unlabelled)."""

    module = adapter(substitute_id)
    if client is None:
        async with _client() as owned:
            documents = await module.fetch(owned, clock=clock)
    else:
        documents = await module.fetch(client, clock=clock)
    return module.parse(documents)


async def rows_for(
    fred_id: str,
    client: httpx.AsyncClient | None = None,
    *,
    clock: Clock | None = None,
    include_conditional: bool = False,
) -> tuple[SubstituteRow, ...]:
    """Labelled substitute rows for one blocked FRED id, best rank first."""

    if fred_id in CONDITIONAL_FRED_IDS and not include_conditional:
        raise LookupError(
            f"{fred_id} substitutes apply {FRED_ORIGINALS[fred_id].condition}"
        )
    entries = entries_for(fred_id)
    collected: dict[str, tuple[SourceObservation, ...]] = {}
    rows: list[SubstituteRow] = []
    for entry in entries:
        if entry.substitute_id not in collected:
            collected[entry.substitute_id] = await collect(
                entry.substitute_id, client, clock=clock
            )
        rows.extend(label(entry, collected[entry.substitute_id]))
    return tuple(rows)


_DASHES = re.compile("[\u2012\u2013\u2014\u2015]")


def validate_registry() -> None:
    """Fail on any inconsistency between the registry and the adapters."""

    for fred_id in FRED_ORIGINALS:
        if not re.fullmatch(r"[A-Z0-9]{3,20}", fred_id):
            raise ValueError(f"invalid FRED id {fred_id!r}")
        if not entries_for(fred_id):
            raise ValueError(f"{fred_id} has no substitute")
    for substitute_id, source in SOURCES.items():
        module = adapter(substitute_id)
        if module.SUBSTITUTE_ID != substitute_id or module.PUBLISHER != source.publisher:
            raise ValueError(f"{substitute_id} module identity differs from the registry")
        if module.TERMS_URL != source.terms_url:
            raise ValueError(f"{substitute_id} terms URL differs from the registry")
        if source.redistribution_status not in REDISTRIBUTION_STATUSES:
            raise ValueError(f"{substitute_id} redistribution status unknown")
        for endpoint in source.endpoints:
            validate_fixed_url(endpoint)
        pattern = getattr(module, "LINK_PATTERN", None)
        if (pattern.pattern if pattern else None) != source.endpoint_pattern:
            raise ValueError(f"{substitute_id} endpoint pattern differs from the adapter")
        validate_fixed_url(source.terms_url)
    seen = set()
    for entry in ENTRIES:
        key = (entry.substitute_for, entry.substitute_id, entry.measure)
        if key in seen:
            raise ValueError(f"duplicate substitute entry {key}")
        seen.add(key)
        if entry.substitute_for not in FRED_ORIGINALS or entry.substitute_id not in SOURCES:
            raise ValueError(f"entry {key} references an unknown id")
        if entry.substitute_class not in SUBSTITUTE_CLASSES or entry.rank < 1:
            raise ValueError(f"entry {key} has an invalid class or rank")
        if entry.measure not in adapter(entry.substitute_id).MEASURES:
            raise ValueError(f"entry {key} names a measure its adapter does not emit")
    if _DASHES.search(json.dumps(registry_document(), ensure_ascii=False)):
        raise ValueError("registry text contains an em or en dash")


def registry_document() -> dict[str, object]:
    """The machine-readable registry, as written to ``DOCUMENT_PATH``."""

    return {
        "schema": SCHEMA,
        "approval": APPROVAL,
        "class_definitions": CLASS_DEFINITIONS,
        "label_rule": LABEL_RULE,
        "row_fields": [item.name for item in fields(SubstituteRow)],
        "blocked_fred_ids": sorted(BLOCKED_FRED_IDS),
        "conditional_fred_ids": sorted(CONDITIONAL_FRED_IDS),
        "fred_originals": {key: asdict(value) for key, value in FRED_ORIGINALS.items()},
        "sources": {
            key: {**asdict(value), "endpoints": list(value.endpoints),
                  "measures": {name: asdict(spec) for name, spec in adapter(key).MEASURES.items()}}
            for key, value in SOURCES.items()
        },
        "substitutes": [
            {**asdict(entry),
             "role": FRED_ORIGINALS[entry.substitute_for].role,
             "attribution": attribution(entry),
             "notice": notice(entry),
             "redistribution_status": SOURCES[entry.substitute_id].redistribution_status,
             "condition": FRED_ORIGINALS[entry.substitute_for].condition}
            for entry in ENTRIES
        ],
        "not_built": {
            "ALT-EIASURV": "EIA survey prices (proxies for the oil and gas spot series); not in this build's priority list",
            "ALT-Z1": "Board Z.1 corporate equities market value (quarterly equity proxy); not in this build's priority list",
            "realized_rates_vol": "Seiche-computed 10-year Treasury realized volatility (VIXCLS proxy); a computation, not a source adapter",
        },
    }


def main() -> None:
    validate_registry()
    print(json.dumps(registry_document(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
