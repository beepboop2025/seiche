"""U.S. Treasury Daily Treasury Par Yield Curve Rates, read from Treasury itself.

Rights: the par yield curve is Treasury's own computation. Treasury's Yield
Curve Methodology page (``METHOD_URL``, checked 2026-11-02) reads: "The
Treasury's official yield curve is a par yield curve derived using a monotone
convex method." A work prepared by Treasury staff as part of their official
duties is a work of the United States Government, and 17 U.S.C. 105(a) reads:
"Copyright protection under this title is not available for any work of the
United States Government". Treasury's Site Policies and Notices page
(``TERMS_URL``) and the Privacy and Legal Notices page it links were checked on
2026-11-02: neither carries a copyright statement or a reuse restriction. Every
served value carries ``ATTRIBUTION``. Treasury seals and names (31 U.S.C. 333)
are outside that basis, and the Federal Reserve Bank of New York indicative
quotations that feed the curve are neither published nor stored here.

Gate: ``build_official_adapters`` registers this adapter only while the US pack
declares the official-open ``treasury_par_yields`` adapter with redistribution
allowed and at least one tenor on it (``rights_gate_open``), and keeps only
rows for the tenors the pack declares there. Any other declaration makes no
request and stores or serves nothing. FRED copies (the legacy DGS3M, DGS2,
DGS10 and DGS30 series, and the DTB3 bill rate behind
``US.TREASURY.TBILL_3M``) keep their own metadata-only policy.

Source: Treasury's documented XML feed
(https://home.treasury.gov/treasury-daily-interest-rate-xml-feed), dataset
``daily_treasury_yield_curve``, requested by month for routine collection and
by year for a backfill. Values are bond-equivalent par yields in percent at
fixed maturities, dated by the trading day. Treasury's methodology page says:
"Yield curve rates are usually available at Treasury's interest rate website by
6:00 PM Eastern Time each trading day, but may be delayed due to system
problems or other issues." The feed carries no publication receipt, so the
pack's declared clock supplies the inferred publication time.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.parsers import expat

from seiche.domain.observation import ConnectorClassification, RedistributionStatus
from seiche.sources.canonical import FetchedDocument, ParsedPoint

FEED_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
DATASET = "daily_treasury_yield_curve"
SOURCE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "TextView?type=daily_treasury_yield_curve"
)
TERMS_URL = "https://home.treasury.gov/subfooter/site-policies-and-notices"
METHOD_URL = (
    "https://home.treasury.gov/policy-issues/financing-the-government/"
    "interest-rate-statistics/treasury-yield-curve-methodology"
)
ATTRIBUTION = "Source: U.S. Department of the Treasury, Daily Treasury Par Yield Curve Rates"
ADAPTER_ID = "treasury_par_yields"
FEED_TITLE = "DailyTreasuryYieldCurveRateData"
# Treasury field to direct instrument. Fields absent from an entry (a tenor
# Treasury did not publish that day or in that era) are absent, never zero.
TENORS = (
    ("BC_1MONTH", "US.TREASURY.PAR_YIELD_1M"),
    ("BC_3MONTH", "US.TREASURY.PAR_YIELD_3M"),
    ("BC_6MONTH", "US.TREASURY.PAR_YIELD_6M"),
    ("BC_1YEAR", "US.TREASURY.PAR_YIELD_1Y"),
    ("BC_2YEAR", "US.TREASURY.PAR_YIELD_2Y"),
    ("BC_5YEAR", "US.TREASURY.PAR_YIELD_5Y"),
    ("BC_10YEAR", "US.TREASURY.PAR_YIELD_10Y"),
    ("BC_30YEAR", "US.TREASURY.PAR_YIELD_30Y"),
)
FIELDS = dict(TENORS)
# A full year was 0.35 MB on 2026-11-02 and a month 33 KB.
MAX_DOCUMENT_BYTES = 4_000_000
MAX_ENTRIES = 400
MAX_MONTH_DOCUMENTS = 24
MAX_YEAR_DOCUMENTS = 40
# The feed took 17 to 21 seconds to first byte on 2026-11-02. Three requests at
# a time keep a nine-year backfill well inside the collector's 300 s deadline.
MAX_CONCURRENT_DOCUMENTS = 3
DOCUMENT_TIMEOUT_SECONDS = 90
# A bound against garbage, not a market view: no Treasury par yield on record
# is near it, and a value outside it faults the run instead of being stored.
MAX_ABS_PERCENT = Decimal("50")
USER_AGENT = "LiquidityLab-Original-Publisher-Research/1 contact mrinal@liquilens.in"

_ATOM = "http://www.w3.org/2005/Atom"
_DATA = "http://schemas.microsoft.com/ado/2007/08/dataservices"
_META = "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
_PERIOD_URI = re.compile(
    "^" + re.escape(f"{FEED_URL}?data={DATASET}&field_tdr_date_value")
    + r"(?:_month=(?P<month>\d{6})|=(?P<year>\d{4}))$"
)
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00:00$")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def month_url(year: int, month: int) -> str:
    return f"{FEED_URL}?data={DATASET}&field_tdr_date_value_month={year:04d}{month:02d}"


def year_url(year: int) -> str:
    return f"{FEED_URL}?data={DATASET}&field_tdr_date_value={year:04d}"


def document_urls(start: date, end: date, *, by_year: bool) -> tuple[str, ...]:
    """The feed documents covering ``start`` to ``end``, by month or by year."""

    if start > end:
        raise ValueError("Treasury par yield window is inverted")
    if by_year:
        urls = tuple(year_url(year) for year in range(start.year, end.year + 1))
        if len(urls) > MAX_YEAR_DOCUMENTS:
            raise ValueError("Treasury par yield backfill exceeds bound")
        return urls
    urls = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        urls.append(month_url(year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if len(urls) > MAX_MONTH_DOCUMENTS:
        raise ValueError("Treasury par yield window exceeds bound")
    return tuple(urls)


def declared_instruments(pack) -> frozenset[str]:
    """The direct tenors the pack itself places on this adapter."""

    return frozenset(
        instrument_id
        for instrument_id in FIELDS.values()
        if (spec := pack.instrument_map.get(instrument_id)) is not None
        and spec.source_adapter_id == ADAPTER_ID
    )


def rights_gate_open(pack) -> bool:
    """True only when the pack itself declares the direct Treasury rights record."""

    adapter = pack.adapter_map.get(ADAPTER_ID)
    return (
        adapter is not None
        and bool(declared_instruments(pack))
        and adapter.classification is ConnectorClassification.OFFICIAL_OPEN
        and adapter.redistribution_status is RedistributionStatus.ALLOWED
    )


async def _fetch_one(client, url: str) -> FetchedDocument:
    async with asyncio.timeout(DOCUMENT_TIMEOUT_SECONDS):
        async with client.stream(
            "GET", url, follow_redirects=False,
            headers={"Accept-Encoding": "identity", "Accept": "application/xml,text/xml",
                     "User-Agent": USER_AGENT},
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"Treasury par yield HTTP {response.status_code}")
            if str(response.url) != url:
                raise ValueError("Treasury par yield source changed")
            encoding = response.headers.get("content-encoding", "").lower().strip()
            if encoding not in {"", "identity"}:
                raise ValueError("Treasury par yield transport encoding changed")
            media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media not in {"text/xml", "application/xml", "application/atom+xml"}:
                raise ValueError("Treasury par yield media type changed")
            declared = response.headers.get("content-length")
            if declared is not None and not 0 <= int(declared) <= MAX_DOCUMENT_BYTES:
                raise ValueError("Treasury par yield document exceeds bound")
            payload = bytearray()
            async for chunk in response.aiter_raw():
                payload.extend(chunk)
                if len(payload) > MAX_DOCUMENT_BYTES:
                    raise ValueError("Treasury par yield document exceeds bound")
    return FetchedDocument(url, "text/xml", bytes(payload), ADAPTER_ID)


async def fetch_documents(client, urls: tuple[str, ...]) -> tuple[FetchedDocument, ...]:
    """Download only the named Treasury feed documents with byte/time bounds.

    Every URL is checked before any request. Up to ``MAX_CONCURRENT_DOCUMENTS``
    requests run at once; the first failure cancels the rest and is raised.
    """

    if any(not _PERIOD_URI.match(url) for url in urls):
        raise ValueError("unrecognized Treasury par yield request")
    slots = asyncio.Semaphore(MAX_CONCURRENT_DOCUMENTS)

    async def bounded(url: str) -> FetchedDocument:
        async with slots:
            return await _fetch_one(client, url)

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(bounded(url)) for url in urls]
    except ExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return tuple(task.result() for task in tasks)


def _scan(payload: bytes) -> tuple[str, list[dict[str, tuple[dict[str, str], str]]]]:
    """Parse the Atom feed; return its title and each entry's data properties.

    A DOCTYPE or entity declaration is refused before any further parsing.
    """
    title: list[str] = []
    entries: list[dict[str, tuple[dict[str, str], str]]] = []
    stack: list[str] = []
    properties: dict[str, tuple[dict[str, str], str]] | None = None
    field: tuple[str, dict[str, str]] | None = None
    text: list[str] = []

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal properties, field
        stack.append(name)
        if name == f"{_META} properties":
            if properties is not None:
                raise ValueError("Treasury par yield properties nested")
            properties = {}
        elif properties is not None and name.startswith(_DATA + " "):
            if field is not None:
                raise ValueError("Treasury par yield field nested")
            field = (name.split(" ", 1)[1], dict(attributes))
            text.clear()
        elif name == f"{_ATOM} title" and len(stack) == 2:
            text.clear()

    def end(name: str) -> None:
        nonlocal properties, field
        if properties is not None and field is not None and name == f"{_DATA} {field[0]}":
            key, attributes = field
            if key in properties:
                raise ValueError(f"Treasury par yield field {key} duplicated")
            properties[key] = (attributes, "".join(text).strip())
            field = None
        elif name == f"{_META} properties" and properties is not None:
            entries.append(properties)
            properties = None
            if len(entries) > MAX_ENTRIES:
                raise ValueError("Treasury par yield entry count exceeds bound")
        elif name == f"{_ATOM} title" and len(stack) == 2:
            title.append("".join(text).strip())
        stack.pop()

    def characters(data: str) -> None:
        text.append(data)

    def forbidden(*_args) -> None:
        raise ValueError("Treasury par yield XML declarations forbidden")

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    try:
        parser.Parse(payload, True)
    except expat.ExpatError as exc:
        raise ValueError("Treasury par yield XML is malformed") from exc
    if len(title) != 1:
        raise ValueError("Treasury par yield feed title missing or duplicated")
    return title[0], entries


def _period(document: FetchedDocument) -> tuple[date, date]:
    match = _PERIOD_URI.match(document.source_uri)
    if match is None:
        raise ValueError("unrecognized Treasury par yield source")
    if match["year"] is not None:
        year = int(match["year"])
        return date(year, 1, 1), date(year, 12, 31)
    year, month = int(match["month"][:4]), int(match["month"][4:])
    if not 1 <= month <= 12:
        raise ValueError("unrecognized Treasury par yield month")
    first = date(year, month, 1)
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first, date.fromordinal(following.toordinal() - 1)


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    first, last = _period(document)
    if len(document.payload) > MAX_DOCUMENT_BYTES:
        raise ValueError("oversized Treasury par yield document")
    title, entries = _scan(document.payload)
    if title != FEED_TITLE:
        raise ValueError("Treasury par yield dataset changed")
    points = []
    seen: set[date] = set()
    for properties in entries:
        attributes, raw_date = properties.get("NEW_DATE", ({}, ""))
        match = _DATE.match(raw_date)
        if attributes.get(f"{_META} type") != "Edm.DateTime" or match is None:
            raise ValueError("Treasury par yield date invalid")
        day = date.fromisoformat(match[1])
        # A response outside the requested period is a changed feed, not data.
        if not first <= day <= last or day in seen or day.weekday() >= 5:
            raise ValueError("Treasury par yield date outside period, duplicated or a weekend")
        seen.add(day)
        for field, instrument_id in TENORS:
            if field not in properties:
                continue
            attributes, raw_value = properties[field]
            if attributes.get(f"{_META} type") != "Edm.Double":
                raise ValueError(f"Treasury par yield {field} type changed")
            if attributes.get(f"{_META} null") == "true":
                continue
            if not _NUMBER.match(raw_value):
                raise ValueError(f"Treasury par yield {field} value invalid")
            try:
                value = Decimal(raw_value)
            except InvalidOperation as exc:
                raise ValueError(f"Treasury par yield {field} value invalid") from exc
            if not value.is_finite() or abs(value) >= MAX_ABS_PERCENT:
                raise ValueError(f"Treasury par yield {field} value out of bounds")
            # The month or year request URL is left out on purpose, so a
            # backfill re-reading the same published row is not a revision.
            evidence = json.dumps({
                "feed_url": FEED_URL, "dataset": DATASET, "field": field,
                "type": "Edm.Double", "new_date": raw_date, "value": raw_value,
            }, sort_keys=True, separators=(",", ":")).encode()
            points.append(ParsedPoint(
                instrument_id, day, value, evidence,
                revision_id=f"ust-par:{field}:{day.isoformat()}:{hashlib.sha256(evidence).hexdigest()[:16]}",
            ))
    return tuple(sorted(points, key=lambda point: (point.event_time, point.instrument_id)))
