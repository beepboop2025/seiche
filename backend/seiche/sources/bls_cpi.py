"""BLS Consumer Price Index series from the BLS Public Data API, not FRED: built, held.

Status: OFF. ``RIGHTS_HOLD`` keeps ``rights_gate_open`` false whatever a pack
declares, the US pack declares no ``bls_cpi`` adapter, and
``build_official_adapters`` registers nothing, so no request is made and
nothing is stored or served. The FRED copies (the legacy CORE_CPI and
ENERGY_CPI series, FRED CPILFESL and CPIENGSL) keep their metadata-only policy.

Rights: the BLS copyright page (``RIGHTS_URL``, checked 2026-11-02) states
that "everything that we publish, both in hard copy and electronically, is in
the public domain, except for previously copyrighted photographs and
illustrations. You are free to use our public domain material without specific
permission, although we do ask that you cite the Bureau of Labor Statistics as
the source."

Why it is held: the BLS.gov Terms of Service (``TERMS_URL``, checked
2026-11-02) bind every user of the API and of BLS.gov content: "Access to or
use of BLS.gov services or its content constitutes acceptance to this
Agreement." They require "Users of the public API should cite the date that
data were accessed or retrieved using the API" and "Users must clearly state
that "BLS.gov cannot vouch for the data or analyses derived from these data
after the data have been retrieved from BLS.gov."", forbid presenting modified
content as BLS's, and include an indemnity for non-government users. Accepting
that agreement for the company is an owner decision. ``ATTRIBUTION`` already
carries the required statement and points at each row's retrieval date
(knowledge time), so enabling needs only that decision, the hold removed and
an allowed pack declaration, in one reviewed change.

Source when enabled: ``API_URL``, one POST for both series and at most
``MAX_YEARS_PER_REQUEST`` years (the unregistered limit), no key. Series
``CUSR0000SA0E`` (energy) and ``CUSR0000SA0L1E`` (all items less food and
energy) are seasonally adjusted U.S. city average index levels, 1982-84=100,
monthly, dated here by the month's last day. ``M13`` rows are annual averages,
not months, and are skipped. A month BLS did not publish carries the value
"-" with a footnote (October 2025, lost to the lapse in appropriations) and
stays absent. Seasonally adjusted values are revised each
February, so a revised value is a new vintage. The API carries no release
time, so publication time is unknown and acquisition establishes knowledge
time.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from seiche.domain.observation import ConnectorClassification, RedistributionStatus
from seiche.sources.canonical import FetchedDocument, ParsedPoint, PublicationTimePolicy

API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
TERMS_URL = "https://www.bls.gov/developers/termsOfService.htm"
RIGHTS_URL = "https://www.bls.gov/opub/copyright-information.htm"
RELEASE_URL = "https://www.bls.gov/cpi/"
ADAPTER_ID = "bls_cpi"
REGISTRY_RECORD = "bls_public_data_api"
SERIES = {
    "CUSR0000SA0E": "US.BLS.CPI_ENERGY_SA",
    "CUSR0000SA0L1E": "US.BLS.CPI_CORE_SA",
}
FRED_COPIES = {"CUSR0000SA0E": "CPIENGSL", "CUSR0000SA0L1E": "CPILFESL"}
# The two statements the BLS.gov Terms of Service require of API users.
VOUCH_STATEMENT = (
    "BLS.gov cannot vouch for the data or analyses derived from these data after "
    "the data have been retrieved from BLS.gov."
)
ATTRIBUTION = (
    "Source: U.S. Bureau of Labor Statistics, Consumer Price Index, retrieved through "
    "the BLS Public Data API on each value's knowledge date. " + VOUCH_STATEMENT
)
RIGHTS_HOLD = (
    "The BLS.gov Terms of Service bind every API user (\"Access to or use of BLS.gov "
    "services or its content constitutes acceptance to this Agreement.\") and include an "
    "indemnity; accepting them for the company is an owner decision not yet made, so "
    "nothing is requested, stored or served."
)
USER_AGENT = "LiquidityLab-Original-Publisher-Research/1 contact mrinal@liquilens.in"
# Both series for 2025 and 2026 were 4 KB on 2026-11-02.
MAX_DOCUMENT_BYTES = 1_000_000
MAX_YEARS_PER_REQUEST = 10
MAX_ROWS_PER_SERIES = 13 * MAX_YEARS_PER_REQUEST
FETCH_TIMEOUT_SECONDS = 60.0
_YEAR = re.compile(r"^\d{4}$")
_MONTH = re.compile(r"^M(0[1-9]|1[0-2])$")
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_UNAVAILABLE = "-"
_MAX_INDEX = Decimal("10000")


def declared_instruments(pack) -> frozenset[str]:
    """The CPI instruments the pack itself places on this adapter."""

    return frozenset(
        instrument_id
        for instrument_id in SERIES.values()
        if (spec := pack.instrument_map.get(instrument_id)) is not None
        and spec.source_adapter_id == ADAPTER_ID
    )


def rights_gate_open(pack) -> bool:
    """Always false while ``RIGHTS_HOLD`` stands, whatever the pack declares."""

    if RIGHTS_HOLD:
        return False
    adapter = pack.adapter_map.get(ADAPTER_ID)
    return (
        adapter is not None
        and bool(declared_instruments(pack))
        and adapter.classification is ConnectorClassification.OFFICIAL_OPEN
        and adapter.redistribution_status is RedistributionStatus.ALLOWED
    )


def request_uri(start_year: int, end_year: int) -> str:
    """A stable identity for one POST: the endpoint, the series and the years."""

    if not 0 <= end_year - start_year < MAX_YEARS_PER_REQUEST:
        raise ValueError("BLS CPI request spans more years than one unregistered request allows")
    return f"{API_URL}?seriesid={','.join(SERIES)}&startyear={start_year:04d}&endyear={end_year:04d}"


def request_body(start_year: int, end_year: int) -> bytes:
    return json.dumps({
        "seriesid": list(SERIES), "startyear": f"{start_year:04d}", "endyear": f"{end_year:04d}",
    }, separators=(",", ":")).encode()


async def fetch_documents(client, start_year: int, end_year: int) -> tuple[FetchedDocument, ...]:
    """POST once for both series with byte and time bounds; no redirects."""

    uri = request_uri(start_year, end_year)
    async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
        async with client.stream(
            "POST", API_URL, content=request_body(start_year, end_year), follow_redirects=False,
            headers={"Content-Type": "application/json", "Accept-Encoding": "identity",
                     "User-Agent": USER_AGENT},
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"BLS CPI HTTP {response.status_code}")
            if str(response.url) != API_URL:
                raise ValueError("BLS CPI source changed")
            if response.headers.get("content-encoding", "").lower().strip() not in {"", "identity"}:
                raise ValueError("BLS CPI transport encoding changed")
            media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media != "application/json":
                raise ValueError("BLS CPI media type changed")
            declared = response.headers.get("content-length")
            if declared is not None and not 0 <= int(declared) <= MAX_DOCUMENT_BYTES:
                raise ValueError("BLS CPI document exceeds bound")
            payload = bytearray()
            async for chunk in response.aiter_raw():
                payload.extend(chunk)
                if len(payload) > MAX_DOCUMENT_BYTES:
                    raise ValueError("BLS CPI document exceeds bound")
    return (FetchedDocument(uri, "application/json", bytes(payload), ADAPTER_ID),)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    """Both pinned series, each exactly once, with plain positive index levels."""

    if not document.source_uri.startswith(API_URL + "?") or len(document.payload) > MAX_DOCUMENT_BYTES:
        raise ValueError("unrecognized or oversized BLS CPI source")
    try:
        payload = json.loads(document.payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("BLS CPI response is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS CPI request did not succeed")
    series_list = (payload.get("Results") or {}).get("series")
    if not isinstance(series_list, list):
        raise ValueError("BLS CPI response has no series list")
    found = [item.get("seriesID") for item in series_list if isinstance(item, dict)]
    if sorted(found) != sorted(SERIES) or len(found) != len(series_list):
        raise ValueError("BLS CPI series missing, duplicated or unexpected")
    points: list[ParsedPoint] = []
    for item in series_list:
        series = item["seriesID"]
        rows = item.get("data")
        if not isinstance(rows, list) or len(rows) > MAX_ROWS_PER_SERIES:
            raise ValueError(f"BLS CPI series {series} rows missing or beyond bound")
        seen: set[date] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"BLS CPI series {series} row invalid")
            year, period, raw = str(row.get("year", "")), str(row.get("period", "")), str(row.get("value", ""))
            if not _YEAR.match(year):
                raise ValueError(f"BLS CPI series {series} year invalid")
            if period == "M13":
                continue  # an annual average, not a month
            month = _MONTH.match(period)
            if month is None:
                raise ValueError(f"BLS CPI series {series} period {period!r} is not a month")
            day = _month_end(int(year), int(month[1]))
            if day in seen:
                raise ValueError(f"BLS CPI series {series} repeats {year} {period}")
            seen.add(day)
            if raw == _UNAVAILABLE:
                # BLS marks a month it did not publish with "-" and a footnote,
                # such as October 2025 ("Data unavailable due to the 2025 lapse
                # in appropriations"). It stays absent, never zero or filled.
                continue
            if not _NUMBER.match(raw):
                raise ValueError(f"BLS CPI series {series} value invalid")
            try:
                value = Decimal(raw)
            except InvalidOperation as exc:
                raise ValueError(f"BLS CPI series {series} value invalid") from exc
            if not Decimal(0) < value < _MAX_INDEX:
                raise ValueError(f"BLS CPI series {series} value out of bounds")
            evidence = json.dumps({
                "api_url": API_URL, "series": series, "year": year, "period": period,
                "value": raw, "footnotes": row.get("footnotes"),
            }, sort_keys=True, separators=(",", ":"), default=str).encode()
            points.append(ParsedPoint(
                SERIES[series], day, value, evidence,
                revision_id=f"bls:{series}:{year}-{period}:{hashlib.sha256(evidence).hexdigest()[:16]}",
                publication_time_policy=PublicationTimePolicy.UNKNOWN,
            ))
        if not any(point.instrument_id == SERIES[series] for point in points):
            raise ValueError(f"BLS CPI series {series} has no available monthly observations")
    return tuple(sorted(points, key=lambda point: (point.event_time, point.instrument_id)))
