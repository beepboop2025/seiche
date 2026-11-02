"""U.S. nominal GDP from BEA's own NIPA flat file, not FRED.

Rights: BEA's copyright answer (``RIGHTS_URL``, checked 2026-11-02) states:
"Unless stated otherwise, the information posted on this web site is in the
public domain and may be used or reproduced without specific permission. A
citation such as "Source: U.S. Bureau of Economic Analysis" would be
appreciated." The flat files are posted on that web site; the separate BEA API
terms bind API users and the API is not used here. Every served value carries
``ATTRIBUTION``.

Gate: ``build_official_adapters`` registers this adapter only while the US pack
declares the official-open ``bea_nipa`` adapter with redistribution allowed and
the GDP instrument on it (``rights_gate_open``). The FRED copy (the legacy GDP
series, FRED GDP) keeps its metadata-only policy.

Source: ``DATA_URL``, BEA's quarterly NIPA values (``%SeriesCode,Period,Value``,
35.6 MB on 2026-11-02), and ``REGISTER_URL``, the series register that declares
each code's label, metric, calculation and scale. Series ``A191RC`` is "Gross
domestic product", current dollars, a level with default scale -6, that is
USD millions at a seasonally adjusted annual rate (NIPA table 1.1.5, line 1).
The register row is pinned, so a change of label, metric, calculation or scale
fails the run closed rather than rescaling. Quarters are dated by their last
day. BEA revises GDP with every estimate and at annual updates; each revised
value is a new vintage. The files carry no per-row release time, so publication
time is unknown and acquisition establishes knowledge time.
"""
from __future__ import annotations

import asyncio
import calendar
import csv
import hashlib
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from seiche.domain.observation import ConnectorClassification, RedistributionStatus
from seiche.sources.canonical import FetchedDocument, ParsedPoint, PublicationTimePolicy

DATA_URL = "https://apps.bea.gov/national/Release/TXT/NipaDataQ.txt"
REGISTER_URL = "https://apps.bea.gov/national/Release/TXT/SeriesRegister.txt"
RIGHTS_URL = "https://www.bea.gov/help/faq/147"
RELEASE_URL = "https://www.bea.gov/data/gdp/gross-domestic-product"
ADAPTER_ID = "bea_nipa"
REGISTRY_RECORD = "bea_nipa_flat_files"
SERIES_CODE = "A191RC"
FRED_COPY = "GDP"
INSTRUMENT_ID = "US.BEA.GDP_NOMINAL_SAAR"
ATTRIBUTION = "Source: U.S. Bureau of Economic Analysis, National Income and Product Accounts"
DATA_HEADER = "%SeriesCode,Period,Value"
REGISTER_HEADER = (
    "%SeriesCode,SeriesLabel,MetricName,CalculationType,DefaultScale,TableId:LineNo,SeriesCodeParents"
)
# The register's declaration of A191RC, pinned field by field.
REGISTER_ROW = {
    "SeriesLabel": "Gross domestic product",
    "MetricName": "Current Dollars",
    "CalculationType": "Level",
    "DefaultScale": "-6",
}
REGISTER_TABLE_LINE = "T10105:1"
USER_AGENT = "LiquidityLab-Original-Publisher-Research/1 contact mrinal@liquilens.in"
# 35.6 MB of data and 0.96 MB of register on 2026-11-02.
MAX_DATA_BYTES = 96_000_000
MAX_REGISTER_BYTES = 8_000_000
MAX_GDP_ROWS = 2_000
FETCH_TIMEOUT_SECONDS = 180.0
# USD millions at an annual rate: 1947Q1 was 243,164; a scale change to
# billions or units would leave this range.
MIN_GDP = Decimal("100000")
MAX_GDP = Decimal("1000000000")
_PERIOD = re.compile(r"^(\d{4})Q([1-4])$")
_VALUE = re.compile(r"^\d{1,3}(?:,\d{3})*$")


def declared_instruments(pack) -> frozenset[str]:
    spec = pack.instrument_map.get(INSTRUMENT_ID)
    return frozenset({INSTRUMENT_ID}) if spec is not None and spec.source_adapter_id == ADAPTER_ID else frozenset()


def rights_gate_open(pack) -> bool:
    """True only when the pack itself declares the allowed BEA record and GDP."""

    adapter = pack.adapter_map.get(ADAPTER_ID)
    return (
        adapter is not None
        and bool(declared_instruments(pack))
        and adapter.classification is ConnectorClassification.OFFICIAL_OPEN
        and adapter.redistribution_status is RedistributionStatus.ALLOWED
    )


async def _fetch_one(client, url: str, bound: int) -> FetchedDocument:
    async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
        async with client.stream(
            "GET", url, follow_redirects=False,
            headers={"Accept-Encoding": "identity", "User-Agent": USER_AGENT},
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"BEA NIPA HTTP {response.status_code}")
            if str(response.url) != url:
                raise ValueError("BEA NIPA source changed")
            if response.headers.get("content-encoding", "").lower().strip() not in {"", "identity"}:
                raise ValueError("BEA NIPA transport encoding changed")
            media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media != "text/plain":
                raise ValueError("BEA NIPA media type changed")
            declared = response.headers.get("content-length")
            if declared is not None and not 0 <= int(declared) <= bound:
                raise ValueError("BEA NIPA file exceeds bound")
            payload = bytearray()
            async for chunk in response.aiter_raw():
                payload.extend(chunk)
                if len(payload) > bound:
                    raise ValueError("BEA NIPA file exceeds bound")
    return FetchedDocument(url, "text/plain", bytes(payload), ADAPTER_ID)


async def fetch_documents(client) -> tuple[FetchedDocument, ...]:
    """The series register and the quarterly values, each bounded; no redirects."""

    register = await _fetch_one(client, REGISTER_URL, MAX_REGISTER_BYTES)
    data = await _fetch_one(client, DATA_URL, MAX_DATA_BYTES)
    return (register, data)


def _lines(payload: bytes, label: str):
    """Decode one line at a time, so a 36 MB file is never held twice as text."""

    for raw in io.BytesIO(payload):
        try:
            yield raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"BEA {label} is not UTF-8 text") from exc


def check_register(document: FetchedDocument) -> dict[str, str]:
    """The pinned register row for A191RC; any change fails closed."""

    if document.source_uri != REGISTER_URL or len(document.payload) > MAX_REGISTER_BYTES:
        raise ValueError("unrecognized or oversized BEA series register")
    stream = _lines(document.payload, "series register")
    if next(stream, "").rstrip("\r\n") != REGISTER_HEADER:
        raise ValueError("BEA series register header changed")
    rows = [row for row in csv.reader(line for line in stream if line.startswith(SERIES_CODE + ","))
            if row and row[0] == SERIES_CODE]
    if len(rows) != 1 or len(rows[0]) != len(REGISTER_HEADER.split(",")):
        raise ValueError("BEA series register entry for A191RC missing, duplicated or reshaped")
    fields = dict(zip(REGISTER_HEADER.lstrip("%").split(","), rows[0]))
    if any(fields.get(key) != value for key, value in REGISTER_ROW.items()):
        raise ValueError("BEA series register declaration for A191RC changed")
    if REGISTER_TABLE_LINE not in fields["TableId:LineNo"].split("|"):
        raise ValueError("BEA series register no longer places A191RC on table 1.1.5 line 1")
    return {key: fields[key] for key in REGISTER_ROW}


def parse_data(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    """Every A191RC quarter in strictly increasing order, in USD millions."""

    if document.source_uri != DATA_URL or len(document.payload) > MAX_DATA_BYTES:
        raise ValueError("unrecognized or oversized BEA NIPA data file")
    stream = _lines(document.payload, "NIPA data file")
    if next(stream, "").rstrip("\r\n") != DATA_HEADER:
        raise ValueError("BEA NIPA data header changed")
    points: list[ParsedPoint] = []
    previous: tuple[int, int] | None = None
    for row in csv.reader(line for line in stream if line.startswith(SERIES_CODE + ",")):
        if not row or row[0] != SERIES_CODE:
            continue
        if len(row) != 3:
            raise ValueError("BEA NIPA A191RC row reshaped")
        _, period, raw = row
        match = _PERIOD.match(period)
        if match is None:
            raise ValueError(f"BEA NIPA A191RC period {period!r} is not a quarter")
        key = (int(match[1]), int(match[2]))
        if previous is not None and key <= previous:
            raise ValueError("BEA NIPA A191RC quarters duplicated or out of order")
        previous = key
        if len(points) >= MAX_GDP_ROWS:
            raise ValueError("BEA NIPA A191RC exceeds its row bound")
        if not _VALUE.match(raw):
            raise ValueError(f"BEA NIPA A191RC value for {period} invalid")
        try:
            value = Decimal(raw.replace(",", ""))
        except InvalidOperation as exc:
            raise ValueError(f"BEA NIPA A191RC value for {period} invalid") from exc
        if not MIN_GDP <= value < MAX_GDP:
            raise ValueError(f"BEA NIPA A191RC value for {period} out of bounds")
        year, quarter = key
        day = date(year, 3 * quarter, calendar.monthrange(year, 3 * quarter)[1])
        evidence = json.dumps({
            "data_url": DATA_URL, "series": SERIES_CODE, "period": period, "value": raw,
            "unit": "USD millions, seasonally adjusted annual rate",
        }, sort_keys=True, separators=(",", ":")).encode()
        points.append(ParsedPoint(
            INSTRUMENT_ID, day, value, evidence,
            revision_id=f"bea:{SERIES_CODE}:{period}:{hashlib.sha256(evidence).hexdigest()[:16]}",
            publication_time_policy=PublicationTimePolicy.UNKNOWN,
        ))
    if not points:
        raise ValueError("BEA NIPA data file has no A191RC quarter")
    return tuple(points)


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    """The register returns no points but fails the run if its declaration changed."""

    if document.source_uri == REGISTER_URL:
        check_register(document)
        return ()
    return parse_data(document)
