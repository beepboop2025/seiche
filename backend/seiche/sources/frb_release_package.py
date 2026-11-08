"""Federal Reserve Board release packages, read from the Board itself, not FRED.

One declarative adapter for the Board's statistical release packages at
``https://www.federalreserve.gov/releases/<rel>/data/FRB_<REL>_xml.zip``: the
PRATES policy rates, H.4.1, H.15, H.10 and G.17. These are the files the Board
links as "XML" on each release page, not the Data Download Program "Build Your
Package" tool that the Board is retiring.

Rights: the Board's notice (``TERMS_URL``, "Copyright/trademark", checked
2026-11-02) states: "Unless otherwise indicated, information on Board's website
is in the public domain and may be copied and distributed without permission.
Please cite to the Board as the source of the information." Every package
therefore carries its Board citation (``ReleasePackage.attribution``), served
with every value. Board seals and logos and any identified non-Board material
are outside that notice. A package whose release indicates third-party
provenance that the owner has not cleared carries a ``rights_hold`` and never
runs, whatever the pack declares: today that is the H.15 commercial paper
rates, which the Board derives from DTCC data.

Shape: each package pins its member, its SDMX data set and, for every series,
the exact publisher declarations (frequency, unit, multiplier and the
dimensions that identify the instrument). Every pinned series must appear
exactly once in the pinned data set, rows must be strictly date ordered on the
package's calendar rule, and every available value must be a plain decimal
inside the package's bounds. Any change fails the whole run closed rather than
rescaling, renaming or guessing. ``NA`` and ``ND`` rows (printed "n.a." or "ND"
on the releases) are explicitly unavailable and are skipped, never zero.

Gate: ``build_official_adapters`` registers a package only while the US pack
itself declares its official-open adapter with redistribution allowed and at
least one of its instruments (``rights_gate_open``), and keeps only rows for
the instruments the pack declares there. Any other declaration makes no request
and stores or serves nothing. FRED copies of the same numbers keep their own
metadata-only policy and are never read here.

Clocks: the packages carry observation dates, not per-row publication receipts.
The pack's declared clock (or, for PRATES and G.17, an explicitly unknown
clock) supplies publication time, and acquisition establishes knowledge time.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from seiche.domain.observation import ConnectorClassification, RedistributionStatus
from seiche.sources.canonical import FetchedDocument, ParsedPoint, PublicationTimePolicy
from seiche.sources.frb_release import RowHandler, stream_release

TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
BOARD = "Board of Governors of the Federal Reserve System"
# T1, verbatim from TERMS_URL ("Copyright/trademark"), checked 2026-11-02.
BOARD_NOTICE = (
    "Unless otherwise indicated, information on Board's website is in the public "
    "domain and may be copied and distributed without permission. Please cite to "
    "the Board as the source of the information."
)
USER_AGENT = "LiquidityLab-Original-Publisher-Research/1 contact mrinal@liquilens.in"
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_KNOWN_STATUSES = frozenset({"A", "NA", "ND"})


class DateRule(StrEnum):
    """The calendar every row of a package must fall on."""

    WEDNESDAY = "wednesday"  # weekly, dated by the Wednesday that ends the week
    WEEKDAY = "weekday"  # business daily; holidays are ND rows on weekdays
    MONTH_END = "month_end"  # monthly, dated by the month's last calendar day
    ANY_DAY = "any_day"  # calendar daily, weekends included


class EvidenceLayout(StrEnum):
    """Row evidence bytes. Revision IDs hash them, so a layout never changes.

    ``H41_V1`` and ``PRATES_V1`` are the layouts ``fed_h41`` (0.16.12) and
    ``fed_prates`` (0.16.11) already stored; keeping them byte-identical keeps
    every stored row's revision identity. New packages use ``STANDARD``.
    """

    STANDARD = "standard"
    H41_V1 = "h41_v1"
    PRATES_V1 = "prates_v1"


_H41_V1_DECLARED = ("FREQ", "UNIT", "UNIT_MULT", "CURRENCY")


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    """One pinned publisher series and the direct instrument it becomes."""

    series_name: str
    instrument_id: str
    # Exact publisher declarations; any difference fails the run closed.
    attributes: Mapping[str, str]
    # The publisher's own description, for documentation and review.
    description: str
    # The FRED copy of the same number, named for parity review only. It is
    # never requested, read or served here.
    fred_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReleasePackage:
    adapter_id: str
    release: str
    title: str
    source_url: str
    release_url: str
    member: str
    dataset_id: str
    date_rule: DateRule
    series: tuple[SeriesSpec, ...]
    attribution: str
    registry_record: str
    revision_prefix: str
    evidence_layout: EvidenceLayout
    publication_time_policy: PublicationTimePolicy
    max_archive_bytes: int
    max_xml_bytes: int
    max_members: int
    # Bounds on available values: inclusive minimum, exclusive minimum and
    # exclusive maximum, each optional. A bound guards against garbage; it is
    # not a market view.
    minimum: Decimal | None = None
    exclusive_minimum: Decimal | None = None
    exclusive_maximum: Decimal | None = None
    max_rows_per_series: int = 60_000
    fetch_timeout_seconds: float = 120.0
    adapter_timeout_seconds: float = 150.0
    window_timezone: str = "America/New_York"
    # Routine collection reads this many days back; a backfill reads from the
    # configured start. Monthly series appear weeks after their labels.
    routine_window_days: int = 45
    # A reason the owner has not cleared these rights. A held package never
    # opens its gate, whatever a pack declares.
    rights_hold: str | None = None
    terms_url: str = TERMS_URL
    label: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", f"Federal Reserve {self.release}")
        names = [item.series_name for item in self.series]
        instruments = [item.instrument_id for item in self.series]
        if not names or len(set(names)) != len(names) or len(set(instruments)) != len(instruments):
            raise ValueError(f"{self.label} package declares no or duplicate series")
        if self.evidence_layout is EvidenceLayout.H41_V1 and any(
            key not in item.attributes for item in self.series for key in _H41_V1_DECLARED
        ):
            raise ValueError("H.4.1 evidence needs every legacy declaration pinned")
        if self.evidence_layout is EvidenceLayout.PRATES_V1 and any(
            key not in item.attributes for item in self.series for key in ("UNIT", "UNIT_MULT")
        ):
            raise ValueError("PRATES evidence needs the unit declarations pinned")

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(item.instrument_id for item in self.series)

    @property
    def series_by_name(self) -> dict[str, SeriesSpec]:
        return {item.series_name: item for item in self.series}


def _h41(series: str, instrument: str, seriestype: str, description: str, fred_id: str) -> SeriesSpec:
    return SeriesSpec(series, instrument, {
        "FREQ": "19", "UNIT": "Currency", "UNIT_MULT": "1000000", "CURRENCY": "USD",
        "SERIESTYPE": seriestype,
    }, description, fred_id)


def _h15(series: str, instrument: str, kind: str, maturity: str, unit: str, description: str,
         fred_id: str) -> SeriesSpec:
    return SeriesSpec(series, instrument, {
        "CURRENCY": "NA", "FREQ": "9", "INSTRUMENT": kind, "MATURITY": maturity,
        "UNIT": unit, "UNIT_MULT": "1",
    }, description, fred_id)


def _h10(series: str, instrument: str, currency: str, fx: str, unit: str, description: str,
         fred_id: str) -> SeriesSpec:
    return SeriesSpec(series, instrument, {
        "CURRENCY": currency, "FREQ": "9", "FX": fx, "UNIT": unit, "UNIT_MULT": "1",
    }, description, fred_id)


PRATES = ReleasePackage(
    adapter_id="fed_prates",
    release="PRATES",
    title="Policy Rates",
    source_url="https://www.federalreserve.gov/releases/prates/data/FRB_PRATES_xml.zip",
    release_url="https://www.federalreserve.gov/monetarypolicy/reserve-balances.htm",
    member="PRATES_data.xml",
    dataset_id="PRATES_POLICY_RATES",
    date_rule=DateRule.ANY_DAY,
    series=(
        SeriesSpec("RESBM_N.D", "US.FED.IORB_DIRECT", {
            "CURRENCY": "NA", "FREQ": "8", "INT_RATES_PAID": "IOR", "UNIT": "Percent", "UNIT_MULT": "1",
        }, "Interest rate on reserve balances (IORB), from 2021-07-29", "IORB"),
        # IOER ran 2008-10-09 to 2021-07-28 and was replaced by IORB on
        # 2021-07-29: the splice leg for pre-2021 history, served beside IORB.
        SeriesSpec("RESBME_N.D", "US.FED.IOER_DIRECT", {
            "CURRENCY": "NA", "FREQ": "8", "INT_RATES_PAID": "IOR", "UNIT": "Percent", "UNIT_MULT": "1",
        }, "Interest rate on excess reserves (IOER), 2008-10-09 to 2021-07-28", "IOER"),
    ),
    attribution=f"Source: {BOARD}, Policy Rates (PRATES)",
    registry_record="frb_prates_release",
    revision_prefix="fed-prates",
    evidence_layout=EvidenceLayout.PRATES_V1,
    publication_time_policy=PublicationTimePolicy.UNKNOWN,
    # The 2026-11-02 package is 56 KB zipped and 0.9 MB of XML.
    max_archive_bytes=2_000_000,
    max_xml_bytes=2_000_000,
    max_members=16,
    exclusive_minimum=Decimal("-50"),
    exclusive_maximum=Decimal("50"),
    fetch_timeout_seconds=30.0,
    adapter_timeout_seconds=30.0,
    window_timezone="UTC",
)

H41 = ReleasePackage(
    adapter_id="fed_h41",
    release="H.4.1",
    title="Factors Affecting Reserve Balances",
    source_url="https://www.federalreserve.gov/releases/h41/data/FRB_H41_xml.zip",
    release_url="https://www.federalreserve.gov/releases/h41/",
    member="H41_data.xml",
    dataset_id="H41",
    date_rule=DateRule.WEDNESDAY,
    series=(
        _h41("RESH4R_XAW_N.WW", "US.FED.RESERVE_BALANCES_DIRECT", "A",
             "Reserve balances with Federal Reserve Banks: week average", "WRESBAL"),
        _h41("RESPPMA_N.WW", "US.FED.TOTAL_ASSETS_DIRECT", "L",
             "Total assets (Less eliminations from consolidation): Wednesday level", "WALCL"),
        _h41("RESPPLLDT_XAW_N.WW", "US.FED.TGA_WEEK_AVERAGE_DIRECT", "A",
             "Deposits with F.R. Banks, other than reserve balances: U.S. Treasury, General Account: "
             "week average", "WTREGEN"),
        _h41("RESTBC_XAW_N.WW", "US.FED.CURRENCY_IN_CIRCULATION_DIRECT", "A",
             "Currency in circulation: week average", "WCURCIR"),
        _h41("RESPPALDP_N.WW", "US.FED.PRIMARY_CREDIT_DIRECT", "L",
             "Loans: Primary credit: Wednesday level", "WLCFLPCL"),
        _h41("RESH4SCS_N.WW", "US.FED.LIQUIDITY_SWAPS_DIRECT", "L",
             "Central bank liquidity swaps: Wednesday level", "SWPT"),
        _h41("RESPPLLRF_N.WW", "US.FED.FOREIGN_OFFICIAL_RRP_DIRECT", "L",
             "Reverse repurchase agreements: Foreign official and international accounts: "
             "Wednesday level", "WLRRAFOIAL"),
        _h41("RESH4FG_N.WW", "US.FED.CUSTODY_TREASURIES_DIRECT", "L",
             "Memorandum: Custody holdings: Marketable U.S. Treasury securities: Wednesday level",
             "WMTSECL1"),
        _h41("RESPPALGTRF_N.WW", "US.FED.FOREIGN_OFFICIAL_REPO_DIRECT", "L",
             "Repurchase agreements: Foreign official: Wednesday level", "H41RESPPALGTRFNWW"),
    ),
    attribution=f"Source: {BOARD}, H.4.1",
    registry_record="frb_h41_release",
    revision_prefix="fed-h41",
    evidence_layout=EvidenceLayout.H41_V1,
    publication_time_policy=PublicationTimePolicy.INFER,
    # The 2026-11-02 package is 9.1 MB zipped and 127 MB of XML.
    max_archive_bytes=32_000_000,
    max_xml_bytes=512_000_000,
    max_members=16,
    minimum=Decimal("0"),
    exclusive_maximum=Decimal("1000000000"),
)

H15 = ReleasePackage(
    adapter_id="fed_h15",
    release="H.15",
    title="Selected Interest Rates",
    source_url="https://www.federalreserve.gov/releases/h15/data/FRB_H15_xml.zip",
    release_url="https://www.federalreserve.gov/releases/h15/",
    member="H15_data.xml",
    dataset_id="H15",
    date_rule=DateRule.WEEKDAY,
    series=(
        _h15("RIFLGFCM03_N.B", "US.FED.H15_CMT_3M", "TCMNOM", "M3", "Percent:_Per_Year",
             "Market yield on U.S. Treasury securities at 3-month constant maturity, investment basis",
             "DGS3MO"),
        _h15("RIFLGFCY02_N.B", "US.FED.H15_CMT_2Y", "TCMNOM", "Y2", "Percent:_Per_Year",
             "Market yield on U.S. Treasury securities at 2-year constant maturity, investment basis",
             "DGS2"),
        _h15("RIFLGFCY10_N.B", "US.FED.H15_CMT_10Y", "TCMNOM", "Y10", "Percent:_Per_Year",
             "Market yield on U.S. Treasury securities at 10-year constant maturity, investment basis",
             "DGS10"),
        _h15("RIFLGFCY30_N.B", "US.FED.H15_CMT_30Y", "TCMNOM", "Y30", "Percent:_Per_Year",
             "Market yield on U.S. Treasury securities at 30-year constant maturity, investment basis",
             "DGS30"),
        _h15("RIFSGFSW04_N.B", "US.FED.H15_TBILL_4W", "TB", "WK4", "Percent:_Per_Year",
             "4-week Treasury bill secondary market rate, discount basis", "DTB4WK"),
        _h15("RIFSGFSM03_N.B", "US.FED.H15_TBILL_3M", "TB", "M3", "Percent:_Per_Year",
             "3-month Treasury bill secondary market rate, discount basis", "DTB3"),
    ),
    attribution=f"Source: {BOARD}, H.15 Selected Interest Rates (Treasury constant maturities: U.S. Treasury)",
    registry_record="frb_h15_release",
    revision_prefix="fed-h15",
    evidence_layout=EvidenceLayout.STANDARD,
    publication_time_policy=PublicationTimePolicy.INFER,
    # The 2026-11-02 package is 4.3 MB zipped and 71 MB of XML.
    max_archive_bytes=24_000_000,
    max_xml_bytes=320_000_000,
    max_members=16,
    exclusive_minimum=Decimal("-50"),
    exclusive_maximum=Decimal("50"),
)

# Built and tested, but held. The owner accepted the DTCC origin on 2026-11-08
# (seiche.source-use.2026-11-08.fed-cp); the US pack serves these same two
# rates from the Board's CP release (fed_cp), so this H.15 copy stays off
# rather than duplicate them.
H15_COMMERCIAL_PAPER = ReleasePackage(
    adapter_id="fed_h15_cp",
    release="H.15",
    title="Selected Interest Rates, commercial paper",
    source_url=H15.source_url,
    release_url=H15.release_url,
    member=H15.member,
    dataset_id=H15.dataset_id,
    date_rule=DateRule.WEEKDAY,
    series=(
        _h15("RIFSPPNAAD90_N.B", "US.FED.H15_CP_AA_NONFINANCIAL_90D", "NFCP", "M3", "Percent",
             "90-Day AA Nonfinancial Commercial Paper Interest Rate", "DCPN3M"),
        _h15("RIFSPPFAAD90_N.B", "US.FED.H15_CP_AA_FINANCIAL_90D", "FCP", "M3", "Percent",
             "90-Day AA Financial Commercial Paper Interest Rate", "DCPF3M"),
    ),
    attribution=(
        f"Source: {BOARD}, H.15 Selected Interest Rates (commercial paper rates interpolated "
        "from trades settled by The Depository Trust Company)"
    ),
    registry_record="frb_h15_commercial_paper",
    revision_prefix="fed-h15-cp",
    evidence_layout=EvidenceLayout.STANDARD,
    publication_time_policy=PublicationTimePolicy.INFER,
    max_archive_bytes=H15.max_archive_bytes,
    max_xml_bytes=H15.max_xml_bytes,
    max_members=H15.max_members,
    exclusive_minimum=Decimal("-50"),
    exclusive_maximum=Decimal("50"),
    label="Federal Reserve H.15 commercial paper",
    rights_hold=(
        "H.15 footnote 5 says these rates are \"interpolated from data on certain commercial "
        "paper trades settled by The Depository Trust Company\". The owner accepted that "
        "DTCC origin on 2026-11-08, and the US pack serves the same two rates from the "
        "Board's CP release (adapter fed_cp), so this H.15 copy is not requested, stored "
        "or served: one source per rate."
    ),
)

_H10_FOREIGN_PER_USD = (
    # (series suffix, ISO currency, H.10 FX code, FRED copy, description)
    ("CA", "CAD", "CAD", "DEXCAUS", "Canadian dollars per U.S. dollar"),
    ("SZ", "CHF", "CHF", "DEXSZUS", "Swiss francs per U.S. dollar"),
    ("MX", "MXN", "MXN", "DEXMXUS", "Mexican pesos per U.S. dollar"),
    ("BZ", "BRL", "BRL", "DEXBZUS", "Brazilian reais per U.S. dollar"),
    ("SF", "ZAR", "ZAL", "DEXSFUS", "South African rand per U.S. dollar"),
    ("DN", "DKK", "DKK", "DEXDNUS", "Danish kroner per U.S. dollar"),
    ("HK", "HKD", "HKD", "DEXHKUS", "Hong Kong dollars per U.S. dollar"),
    ("MA", "MYR", "MYR", "DEXMAUS", "Malaysian ringgit per U.S. dollar"),
    ("NO", "NOK", "NOK", "DEXNOUS", "Norwegian kroner per U.S. dollar"),
    ("SD", "SEK", "SEK", "DEXSDUS", "Swedish kronor per U.S. dollar"),
    ("SI", "SGD", "SGD", "DEXSIUS", "Singapore dollars per U.S. dollar"),
    ("TA", "TWD", "TWD", "DEXTAUS", "New Taiwan dollars per U.S. dollar"),
    ("TH", "THB", "THB", "DEXTHUS", "Thai baht per U.S. dollar"),
    ("SL", "LKR", "LKR", "DEXSLUS", "Sri Lankan rupees per U.S. dollar"),
    ("CH", "CNY", "CNY", "DEXCHUS", "Chinese yuan per U.S. dollar"),
    ("JA", "JPY", "JPY", "DEXJPUS", "Japanese yen per U.S. dollar"),
    ("KO", "KRW", "KRW", "DEXKOUS", "South Korean won per U.S. dollar"),
    ("IN", "INR", "INR", "DEXINUS", "Indian rupees per U.S. dollar"),
)
_H10_USD_PER_FOREIGN = (
    # The H.10 starred quotes: U.S. dollars per currency unit.
    ("EU", "EUR", "DEXUSEU", "U.S. dollars per euro"),
    ("UK", "GBP", "DEXUSUK", "U.S. dollars per pound sterling"),
    ("AL", "AUD", "DEXUSAL", "U.S. dollars per Australian dollar"),
    ("NZ", "NZD", "DEXUSNZ", "U.S. dollars per New Zealand dollar"),
)
_H10_INDEXES = (
    ("JRXWTFB_N.B", "BRD", "US.FED.H10_DOLLAR_INDEX_BROAD", "DTWEXBGS", "Nominal Broad Dollar Index"),
    ("JRXWTFN_N.B", "AFE", "US.FED.H10_DOLLAR_INDEX_AFE", "DTWEXAFEGS",
     "Nominal Advanced Foreign Economies Dollar Index"),
    ("JRXWTFO_N.B", "EME", "US.FED.H10_DOLLAR_INDEX_EME", "DTWEXEMEGS",
     "Nominal Emerging Market Economies Dollar Index"),
)

H10 = ReleasePackage(
    adapter_id="fed_h10",
    release="H.10",
    title="Foreign Exchange Rates",
    source_url="https://www.federalreserve.gov/releases/h10/data/FRB_H10_xml.zip",
    release_url="https://www.federalreserve.gov/releases/h10/",
    member="H10_data.xml",
    dataset_id="H10",
    date_rule=DateRule.WEEKDAY,
    series=(
        *(_h10(f"RXI$US_N.B.{suffix}", f"US.FED.H10_USD_PER_{currency}", currency, currency,
               "Currency", description, fred_id)
          for suffix, currency, fred_id, description in _H10_USD_PER_FOREIGN),
        *(_h10(f"RXI_N.B.{suffix}", f"US.FED.H10_{currency}_PER_USD", currency, fx,
               "Currency", description, fred_id)
          for suffix, currency, fx, fred_id, description in _H10_FOREIGN_PER_USD),
        # The release labels these indexes "JAN06=100"; the package's UNIT
        # attribute still reads "Index:_1997_Jan_100". The attribute is pinned
        # as published so any change faults the run.
        *(_h10(series, instrument, "NA", fx, "Index:_1997_Jan_100", description, fred_id)
          for series, fx, instrument, fred_id, description in _H10_INDEXES),
    ),
    attribution=f"Source: {BOARD}, H.10 Foreign Exchange Rates",
    registry_record="frb_h10_release",
    revision_prefix="fed-h10",
    evidence_layout=EvidenceLayout.STANDARD,
    publication_time_policy=PublicationTimePolicy.INFER,
    # The 2026-11-02 package is 2.1 MB zipped and 27 MB of XML.
    max_archive_bytes=16_000_000,
    max_xml_bytes=160_000_000,
    max_members=16,
    exclusive_minimum=Decimal("0"),
    exclusive_maximum=Decimal("1000000"),
)

G17 = ReleasePackage(
    adapter_id="fed_g17",
    release="G.17",
    title="Industrial Production and Capacity Utilization",
    source_url="https://www.federalreserve.gov/releases/g17/data/FRB_G17_xml.zip",
    release_url="https://www.federalreserve.gov/releases/g17/",
    member="G17_data.xml",
    # The total index appears, identically, in three data sets; one is pinned.
    dataset_id="IP_MAJOR_INDUSTRY_GROUPS",
    date_rule=DateRule.MONTH_END,
    series=(
        SeriesSpec("IP.B50001.S", "US.FED.G17_INDUSTRIAL_PRODUCTION", {
            "CURRENCY": "NA", "FREQ": "129", "SA": "SA", "SERIES_CODE": "B50001",
            "UNIT": "Index:_2017_100", "UNIT_MULT": "1",
        }, "Industrial production: total index, seasonally adjusted, 2017=100", "INDPRO"),
    ),
    attribution=f"Source: {BOARD}, G.17 Industrial Production and Capacity Utilization",
    registry_record="frb_g17_release",
    revision_prefix="fed-g17",
    evidence_layout=EvidenceLayout.STANDARD,
    # Published "around the middle of the month" on a release calendar the
    # package does not carry, so publication time is unknown.
    publication_time_policy=PublicationTimePolicy.UNKNOWN,
    # The 2026-11-02 package is 8.6 MB zipped, 87 MB of XML and 17 members.
    max_archive_bytes=32_000_000,
    max_xml_bytes=400_000_000,
    max_members=32,
    exclusive_minimum=Decimal("0"),
    exclusive_maximum=Decimal("10000"),
    # A monthly index appears about two weeks after its month-end label and is
    # revised for several months after, so routine runs read 400 days back.
    routine_window_days=400,
)

PACKAGES: tuple[ReleasePackage, ...] = (PRATES, H41, H15, H15_COMMERCIAL_PAPER, H10, G17)
PACKAGE_BY_ADAPTER = {package.adapter_id: package for package in PACKAGES}


def declared_instruments(pack, package: ReleasePackage) -> frozenset[str]:
    """The package instruments the pack itself places on the package's adapter."""

    return frozenset(
        instrument_id
        for instrument_id in package.instrument_ids
        if (spec := pack.instrument_map.get(instrument_id)) is not None
        and spec.source_adapter_id == package.adapter_id
    )


def rights_gate_open(pack, package: ReleasePackage) -> bool:
    """True only for an unheld package whose allowed rights record the pack declares."""

    if package.rights_hold is not None:
        return False
    adapter = pack.adapter_map.get(package.adapter_id)
    return (
        adapter is not None
        and bool(declared_instruments(pack, package))
        and adapter.classification is ConnectorClassification.OFFICIAL_OPEN
        and adapter.redistribution_status is RedistributionStatus.ALLOWED
    )


async def fetch_package(client, package: ReleasePackage) -> tuple[FetchedDocument, ...]:
    """Download only the fixed publisher package with byte and time bounds."""

    label = package.label
    async with asyncio.timeout(package.fetch_timeout_seconds):
        async with client.stream(
            "GET", package.source_url, follow_redirects=False,
            headers={"Accept-Encoding": "identity", "User-Agent": USER_AGENT},
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"{label} HTTP {response.status_code}")
            if str(response.url) != package.source_url:
                raise ValueError(f"{label} source changed")
            encoding = response.headers.get("content-encoding", "").lower().strip()
            if encoding not in {"", "identity"}:
                raise ValueError(f"{label} transport encoding changed")
            declared = response.headers.get("content-length")
            if declared is not None and not 0 <= int(declared) <= package.max_archive_bytes:
                raise ValueError(f"{label} archive exceeds bound")
            payload = bytearray()
            async for chunk in response.aiter_raw():
                payload.extend(chunk)
                if len(payload) > package.max_archive_bytes:
                    raise ValueError(f"{label} archive exceeds bound")
    return (FetchedDocument(package.source_url, "application/zip", bytes(payload), package.adapter_id),)


def _on_calendar(day: date, rule: DateRule) -> bool:
    if rule is DateRule.WEDNESDAY:
        return day.weekday() == 2
    if rule is DateRule.WEEKDAY:
        return day.weekday() < 5
    if rule is DateRule.MONTH_END:
        return day.day == calendar.monthrange(day.year, day.month)[1]
    return True


def _within_bounds(value: Decimal, package: ReleasePackage) -> bool:
    return (
        value.is_finite()
        and (package.minimum is None or value >= package.minimum)
        and (package.exclusive_minimum is None or value > package.exclusive_minimum)
        and (package.exclusive_maximum is None or value < package.exclusive_maximum)
    )


def _evidence(package: ReleasePackage, spec: SeriesSpec, declared: dict[str, str],
              row: dict[str, str]) -> bytes:
    if package.evidence_layout is EvidenceLayout.H41_V1:
        body = {
            "source_url": package.source_url, "member": package.member, "series": spec.series_name,
            "field": "OBS_VALUE", "declared": {key: declared[key] for key in _H41_V1_DECLARED},
            "observation": row,
        }
    elif package.evidence_layout is EvidenceLayout.PRATES_V1:
        body = {
            "source_url": package.source_url, "series": spec.series_name, "field": "OBS_VALUE",
            "unit": declared["UNIT"], "unit_mult": declared["UNIT_MULT"], "observation": row,
        }
    else:
        body = {
            "source_url": package.source_url, "member": package.member,
            "dataset": package.dataset_id, "series": spec.series_name, "field": "OBS_VALUE",
            "declared": {key: declared[key] for key in sorted(spec.attributes)},
            "observation": row,
        }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


class _SeriesReader:
    """Validate one pinned series row by row, keeping only the requested points."""

    def __init__(self, package: ReleasePackage, spec: SeriesSpec, declared: dict[str, str], *,
                 emit: bool, start: date | None, end: date | None) -> None:
        self.package, self.spec, self.declared = package, spec, declared
        self.emit, self.start, self.end = emit, start, end
        self.rows = 0
        self.available = 0
        self.previous: date | None = None
        self.points: list[ParsedPoint] = []

    def __call__(self, row: dict[str, str]) -> None:
        label, name = self.package.label, self.spec.series_name
        self.rows += 1
        if self.rows > self.package.max_rows_per_series:
            raise ValueError(f"{label} series {name} exceeds its row bound")
        status = row.get("OBS_STATUS")
        if status not in _KNOWN_STATUSES:
            raise ValueError(f"{label} series {name} observation status changed")
        raw_day = row.get("TIME_PERIOD") or ""
        try:
            day = date.fromisoformat(raw_day)
        except ValueError as exc:
            raise ValueError(f"{label} series {name} date invalid") from exc
        if raw_day != day.isoformat():
            raise ValueError(f"{label} series {name} date invalid")
        # Strictly increasing dates also rule out duplicates.
        if self.previous is not None and day <= self.previous:
            raise ValueError(f"{label} series {name} dates duplicated or out of order")
        if not _on_calendar(day, self.package.date_rule):
            raise ValueError(f"{label} series {name} date off its {self.package.date_rule.value} calendar")
        self.previous = day
        # NA and ND rows are explicitly unavailable, never zero.
        if status != "A":
            return
        raw_value = row.get("OBS_VALUE") or ""
        if not _NUMBER.match(raw_value):
            raise ValueError(f"{label} series {name} observation invalid")
        try:
            value = Decimal(raw_value)
        except InvalidOperation as exc:
            raise ValueError(f"{label} series {name} observation invalid") from exc
        if not _within_bounds(value, self.package):
            raise ValueError(f"{label} series {name} observation out of bounds")
        self.available += 1
        if not self.emit or (self.start is not None and day < self.start) or (
                self.end is not None and day > self.end):
            return
        evidence = _evidence(self.package, self.spec, self.declared, row)
        self.points.append(ParsedPoint(
            self.spec.instrument_id, day, value, evidence,
            revision_id=(
                f"{self.package.revision_prefix}:{name}:{day.isoformat()}:"
                f"{hashlib.sha256(evidence).hexdigest()[:16]}"
            ),
            publication_time_policy=self.package.publication_time_policy,
        ))


def parse_package(
    document: FetchedDocument,
    package: ReleasePackage,
    *,
    instruments: Collection[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> tuple[ParsedPoint, ...]:
    """Validate every pinned series of one package; return the requested points.

    Every pinned series is read and validated whatever is requested, so a
    changed package fails closed even when its changed series is not served.
    ``instruments`` limits the points returned; ``start`` and ``end`` limit
    their dates, inclusive.
    """

    wanted = set(package.instrument_ids if instruments is None else instruments)
    unknown = wanted - set(package.instrument_ids)
    if unknown:
        raise ValueError(f"{package.label} has no series for {sorted(unknown)}")
    specs = package.series_by_name
    readers: dict[str, _SeriesReader] = {}

    def on_series(dataset: str | None, attributes: dict[str, str]) -> RowHandler | None:
        name = attributes.get("SERIES_NAME")
        if dataset != package.dataset_id or name not in specs:
            return None
        if name in readers:
            raise ValueError(f"{package.label} series {name} duplicated")
        spec = specs[name]
        if any(attributes.get(key) != value for key, value in spec.attributes.items()):
            raise ValueError(f"{package.label} series {name} units, identity or frequency changed")
        readers[name] = _SeriesReader(
            package, spec, attributes, emit=spec.instrument_id in wanted, start=start, end=end,
        )
        return readers[name]

    stream_release(
        document, on_series, source_url=package.source_url, member=package.member,
        label=package.label, max_archive_bytes=package.max_archive_bytes,
        max_xml_bytes=package.max_xml_bytes, max_members=package.max_members,
    )
    points: list[ParsedPoint] = []
    for name in specs:
        reader = readers.get(name)
        if reader is None:
            raise ValueError(f"{package.label} series {name} missing")
        if reader.available == 0:
            raise ValueError(f"{package.label} series {name} has no available observations")
        points.extend(reader.points)
    return tuple(sorted(points, key=lambda point: (point.event_time, point.instrument_id)))
