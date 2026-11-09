"""Bounded, fixed-origin OECD SDMX collection of monthly interest-rate references.

The OECD Data Explorer API serves the Main Economic Indicators financial
market dataflow OECD.SDD.STES:DSD_STES@DF_FINMARK(4.0). One request covers one
market's declared series through a fully pinned series key and returns
codes-only SDMX-CSV 1.0. Redirects are refused, the final URL must equal the
fixed request, bytes and rows are bounded, and the complete document is
validated before any point is emitted. Reuse rests on the OECD Terms and
Conditions recorded in ``seiche.markets.funding_reference``.

The API admits 60 data downloads per hour per client; one scheduled request
per market stays far below it. Nothing here runs on a request path.
"""
from __future__ import annotations

import asyncio
import calendar
import csv
from collections import Counter
from datetime import date
from decimal import Decimal
import io
import json
import re

import httpx

from seiche.domain.observation import QualityState
from seiche.markets.funding_reference import (
    OECD_DATA_URL, OECD_KEY_TAIL, OECD_STRUCTURE_ID, OecdSeries,
)
from seiche.sources.canonical import FetchedDocument, ParsedPoint, PublicationTimePolicy

MEDIA_TYPE = "application/vnd.sdmx.data+csv"
MAX_BODY_BYTES = 1024 * 1024
MAX_ROWS = 6000
DOWNLOAD_DEADLINE_SECONDS = 60
HEADER = (
    "DATAFLOW", "REF_AREA", "FREQ", "MEASURE", "UNIT_MEASURE", "ACTIVITY", "ADJUSTMENT",
    "TRANSFORMATION", "TIME_HORIZ", "METHODOLOGY", "TIME_PERIOD", "OBS_VALUE",
    "OBS_STATUS", "UNIT_MULT", "DECIMALS", "BASE_PER",
)
FIXED_DIMENSIONS = dict(zip(
    ("FREQ", "UNIT_MEASURE", "ACTIVITY", "ADJUSTMENT", "TRANSFORMATION", "TIME_HORIZ",
     "METHODOLOGY"), ("M", *OECD_KEY_TAIL), strict=True))
# SDMX CL_OBS_STATUS 2.2. Normal, break, estimated and provisional values are
# observations; missing codes must carry no value. Any other status, such as a
# forecast or an imputed value, fails the document for review.
VALUE_STATUSES = frozenset({"A", "B", "E", "P"})
MISSING_STATUSES = frozenset({"H", "L", "M", "O", "Q"})
# Percent per annum. A shift to basis points or to fractions leaves this band.
PERCENT_BOUNDS = (Decimal("-5"), Decimal("50"))
_PERIOD = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
_VALUE = re.compile(r"-?\d{1,3}(?:\.\d{1,12})?")


def series_url(series: tuple[OecdSeries, ...]) -> str:
    """Fixed URL of one reference area's declared measures, without parameters."""
    areas = {item.ref_area for item in series}
    if len(areas) != 1 or len({item.measure for item in series}) != len(series):
        raise ValueError("an OECD request covers distinct measures of exactly one reference area")
    measures = "+".join(sorted(item.measure for item in series))
    return OECD_DATA_URL + ".".join((areas.pop(), "M", measures, *OECD_KEY_TAIL))


def request_url(series: tuple[OecdSeries, ...], *, start: date) -> str:
    return f"{series_url(series)}?startPeriod={start:%Y-%m}&format=csvfile"


async def fetch_documents(client: httpx.AsyncClient, requests) -> tuple[FetchedDocument, ...]:
    """GET each fixed-origin document without redirects, within byte and time bounds."""
    documents = []
    for label, url in requests:
        if not url.startswith(OECD_DATA_URL):
            raise ValueError("OECD request is outside the fixed data origin")
        headers = {"Accept": MEDIA_TYPE + "; charset=utf-8", "Accept-Language": "en"}
        async with asyncio.timeout(DOWNLOAD_DEADLINE_SECONDS), client.stream(
            "GET", url, headers=headers, follow_redirects=False,
        ) as response:
            response.raise_for_status()
            if response.status_code != 200 or str(response.url) != url:
                raise ValueError("OECD response is not the complete fixed-origin document")
            media_type = response.headers.get("content-type", "")
            if media_type.split(";", 1)[0].strip().lower() != MEDIA_TYPE:
                raise ValueError("OECD response is not SDMX-CSV")
            declared = response.headers.get("content-length")
            if declared is not None and not (declared.isdigit() and 0 < int(declared) <= MAX_BODY_BYTES):
                raise ValueError("OECD content length is outside its permitted bounds")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    raise ValueError("OECD response exceeds its byte limit")
                body.extend(chunk)
        documents.append(FetchedDocument(url, media_type, bytes(body), label))
    return tuple(documents)


def _point(series: OecdSeries, event: date, value: Decimal, record: dict[str, str]) -> ParsedPoint:
    # Stable, source-native row identity. The raw document has its own hash.
    evidence = {"dataflow": OECD_STRUCTURE_ID, "series_key": series.key,
                "time_period": record["TIME_PERIOD"], "obs_value": record["OBS_VALUE"],
                "obs_status": record["OBS_STATUS"], "unit_measure": record["UNIT_MEASURE"],
                "unit_mult": record["UNIT_MULT"]}
    return ParsedPoint(
        series.instrument_id, event, value,
        (json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        quality=QualityState.ESTIMATED,
        publication_time_policy=PublicationTimePolicy.UNKNOWN,
    )


def parse_document(document: FetchedDocument, *, series: tuple[OecdSeries, ...],
                   today: date, start: date | None = None) -> tuple[ParsedPoint, ...]:
    """Validate a complete OECD SDMX-CSV document, then emit month-end points.

    ``today`` is the capture date. A monthly period that has not ended by then
    cannot be a published monthly reference and fails the document.
    """
    expected = {(item.ref_area, item.measure): item for item in series}
    if document.source_uri.split("?", 1)[0] != series_url(series):
        raise ValueError("OECD document is not the pinned series request")
    if document.media_type.split(";", 1)[0].strip().lower() != MEDIA_TYPE:
        raise ValueError("OECD document is not SDMX-CSV")
    if not 0 < len(document.payload) <= MAX_BODY_BYTES:
        raise ValueError("OECD document size is outside its permitted bounds")
    text = document.payload.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("OECD document contains null bytes")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ValueError("OECD SDMX-CSV is malformed") from exc
    if not rows or tuple(rows[0]) != HEADER:
        raise ValueError("OECD SDMX-CSV columns or separator changed")
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError("OECD document exceeds its row limit")
    points, seen, numeric = [], set(), Counter()
    for row in rows[1:]:
        if len(row) != len(HEADER):
            raise ValueError("OECD row is truncated or shifted")
        record = dict(zip(HEADER, row, strict=True))
        identity = (record["REF_AREA"], record["MEASURE"])
        if record["DATAFLOW"] != OECD_STRUCTURE_ID or identity not in expected:
            raise ValueError("OECD row is outside the pinned dataflow and series key")
        if any(record[name] != value for name, value in FIXED_DIMENSIONS.items()):
            raise ValueError("OECD frequency, unit, adjustment or methodology changed")
        if (record["UNIT_MULT"] != "0" or record["BASE_PER"] != ""
                or not re.fullmatch(r"\d{1,2}", record["DECIMALS"])):
            raise ValueError("OECD unit multiplier, base period or decimals changed")
        period = _PERIOD.fullmatch(record["TIME_PERIOD"])
        if period is None:
            raise ValueError("OECD monthly period is invalid")
        year, month = int(period[1]), int(period[2])
        event = date(year, month, calendar.monthrange(year, month)[1])
        if event >= today:
            raise ValueError("OECD monthly period has not ended by the capture date")
        if (*identity, record["TIME_PERIOD"]) in seen:
            raise ValueError("OECD observation is duplicated")
        seen.add((*identity, record["TIME_PERIOD"]))
        status, raw = record["OBS_STATUS"], record["OBS_VALUE"]
        if status in MISSING_STATUSES:
            if raw not in {"", "NaN"}:
                raise ValueError("OECD missing flag conflicts with its value")
            continue
        if status not in VALUE_STATUSES:
            raise ValueError("OECD observation status is not an accepted observed value")
        if not _VALUE.fullmatch(raw):
            raise ValueError("OECD rate is not a finite decimal")
        value = Decimal(raw)
        if not PERCENT_BOUNDS[0] <= value <= PERCENT_BOUNDS[1]:
            raise ValueError("OECD rate is outside the percent-per-annum band")
        numeric[identity] += 1
        if start is None or event >= start:
            points.append(_point(expected[identity], event, value, record))
    absent = sorted(item.instrument_id for key, item in expected.items() if not numeric[key])
    if absent:
        raise ValueError("OECD response lacks declared series: " + ", ".join(absent))
    if not points:
        raise ValueError("OECD document has no observations in the requested window")
    return tuple(sorted(points, key=lambda point: (point.event_time, point.instrument_id)))
