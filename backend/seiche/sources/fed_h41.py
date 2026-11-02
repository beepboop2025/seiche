"""Federal Reserve Board H.4.1 reserve balances and total assets, distinct from FRED.

Rights: the Board's notice (https://www.federalreserve.gov/disclaimer.htm,
"Copyright/trademark") states: "Unless otherwise indicated, information on
Board's website is in the public domain and may be copied and distributed
without permission. Please cite to the Board as the source of the
information." Every served value therefore carries ``ATTRIBUTION``. Board seals
and logos and any identified non-Board material are outside that notice.

Gate: ``build_official_adapters`` registers this adapter only while the US pack
itself declares the official-open ``fed_h41`` adapter with redistribution
allowed and at least one direct instrument on it (``rights_gate_open``), and it
keeps only rows for the instruments the pack declares there. Any other
declaration makes no request and stores or serves nothing. The FRED copies
(``US.FED.RESERVE_BALANCES``/WRESBAL and the legacy WALCL series) keep their own
metadata-only policy.

Source: the Board's own release package, not FRED and not the Data Download
Program "Build Your Package" tool that the Board is retiring. Series
``RESH4R_XAW_N.WW`` is "Reserve balances with Federal Reserve Banks: week
average" in millions of US dollars (UNIT=Currency, UNIT_MULT=1000000), dated
by the Wednesday that ends each week. It is the same number FRED republishes as
WRESBAL. Series ``RESPPMA_N.WW`` is "Total assets (Less eliminations from
consolidation): Wednesday level", in the same unit and on the same Wednesday
dates, which FRED republishes as WALCL. The Board releases H.4.1 on Thursdays at
16:30 New York time, delayed by federal holidays; the package carries no per-row
publication receipt, so the pack's declared clock, not this parser, supplies the
inferred publication time.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from seiche.domain.observation import ConnectorClassification, RedistributionStatus
from seiche.sources.canonical import FetchedDocument, ParsedPoint
from seiche.sources.frb_release import read_release_series

SOURCE_URL = "https://www.federalreserve.gov/releases/h41/data/FRB_H41_xml.zip"
RELEASE_URL = "https://www.federalreserve.gov/releases/h41/"
TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
ATTRIBUTION = "Source: Board of Governors of the Federal Reserve System, H.4.1"
ADAPTER_ID = "fed_h41"
INSTRUMENT_ID = "US.FED.RESERVE_BALANCES_DIRECT"
MEMBER = "H41_data.xml"
SERIES_NAME = "RESH4R_XAW_N.WW"
TOTAL_ASSETS_INSTRUMENT_ID = "US.FED.TOTAL_ASSETS_DIRECT"
TOTAL_ASSETS_SERIES_NAME = "RESPPMA_N.WW"
# Publisher series to direct instrument. Both must be present in every package.
SERIES = {SERIES_NAME: INSTRUMENT_ID, TOTAL_ASSETS_SERIES_NAME: TOTAL_ASSETS_INSTRUMENT_ID}
# Exact publisher declarations; any change fails closed rather than rescaling.
SERIES_ATTRIBUTES = {"FREQ": "19", "UNIT": "Currency", "UNIT_MULT": "1000000", "CURRENCY": "USD"}
# The 2026-11-02 package is 9.1 MB zipped and 127 MB of XML.
MAX_ARCHIVE_BYTES = 32_000_000
MAX_XML_BYTES = 512_000_000
MAX_MEMBERS = 16
_WEDNESDAY = 2


def declared_instruments(pack) -> frozenset[str]:
    """The direct H.4.1 instruments the pack itself places on this adapter."""

    return frozenset(
        instrument_id
        for instrument_id in SERIES.values()
        if (spec := pack.instrument_map.get(instrument_id)) is not None
        and spec.source_adapter_id == ADAPTER_ID
    )


def rights_gate_open(pack) -> bool:
    """True only when the pack itself declares the direct H.4.1 rights record."""

    adapter = pack.adapter_map.get(ADAPTER_ID)
    return (
        adapter is not None
        and bool(declared_instruments(pack))
        and adapter.classification is ConnectorClassification.OFFICIAL_OPEN
        and adapter.redistribution_status is RedistributionStatus.ALLOWED
    )


async def fetch_document(client) -> tuple[FetchedDocument, ...]:
    """Download only the fixed publisher package with byte/time bounds."""
    async with asyncio.timeout(120):
        async with client.stream(
            "GET", SOURCE_URL, follow_redirects=False,
            headers={"Accept-Encoding": "identity",
                     "User-Agent": "LiquidityLab-Original-Publisher-Research/1 contact mrinal@liquilens.in"},
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"Federal Reserve H.4.1 HTTP {response.status_code}")
            if str(response.url) != SOURCE_URL:
                raise ValueError("Federal Reserve H.4.1 source changed")
            encoding = response.headers.get("content-encoding", "").lower().strip()
            if encoding not in {"", "identity"}:
                raise ValueError("Federal Reserve H.4.1 transport encoding changed")
            declared = response.headers.get("content-length")
            if declared is not None and not 0 <= int(declared) <= MAX_ARCHIVE_BYTES:
                raise ValueError("Federal Reserve H.4.1 archive exceeds bound")
            payload = bytearray()
            async for chunk in response.aiter_raw():
                payload.extend(chunk)
                if len(payload) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Federal Reserve H.4.1 archive exceeds bound")
    return (FetchedDocument(SOURCE_URL, "application/zip", bytes(payload), ADAPTER_ID),)


def _series_points(series: str, declared: dict[str, str], rows: list[dict[str, str]]) -> list[ParsedPoint]:
    instrument_id = SERIES[series]
    points = []
    seen: set[date] = set()
    for row in rows:
        # NA/ND rows are explicitly unavailable, never zero.
        if row.get("OBS_STATUS") != "A":
            continue
        raw_day = row.get("TIME_PERIOD")
        try:
            day = date.fromisoformat(raw_day or "")
        except ValueError as exc:
            raise ValueError("Federal Reserve H.4.1 date invalid") from exc
        if raw_day != day.isoformat() or day in seen or day.weekday() != _WEDNESDAY:
            raise ValueError("Federal Reserve H.4.1 date invalid, duplicated or not a Wednesday")
        seen.add(day)
        try:
            value = Decimal(row["OBS_VALUE"])
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("Federal Reserve H.4.1 observation invalid") from exc
        if not value.is_finite() or value < 0:
            raise ValueError("Federal Reserve H.4.1 observation nonfinite or negative")
        evidence = json.dumps({
            "source_url": SOURCE_URL, "member": MEMBER, "series": series,
            "field": "OBS_VALUE", "declared": {key: declared[key] for key in SERIES_ATTRIBUTES},
            "observation": row,
        }, sort_keys=True, separators=(",", ":")).encode()
        points.append(ParsedPoint(
            instrument_id, day, value, evidence,
            revision_id=f"fed-h41:{series}:{day.isoformat()}:{hashlib.sha256(evidence).hexdigest()[:16]}",
        ))
    if not points:
        raise ValueError(f"Federal Reserve H.4.1 series {series} has no available observations")
    return points


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    found, rows = read_release_series(
        document, source_url=SOURCE_URL, member=MEMBER, wanted=SERIES,
        label="Federal Reserve H.4.1", max_archive_bytes=MAX_ARCHIVE_BYTES,
        max_xml_bytes=MAX_XML_BYTES, max_members=MAX_MEMBERS,
    )
    points: list[ParsedPoint] = []
    # Every mapped series must be present exactly once with the pinned
    # declarations; a renamed or rescaled series faults the whole run.
    for series in SERIES:
        if len(found[series]) != 1:
            raise ValueError(f"Federal Reserve H.4.1 series {series} missing or duplicated")
        declared = found[series][0]
        if any(declared.get(key) != value for key, value in SERIES_ATTRIBUTES.items()):
            raise ValueError(f"Federal Reserve H.4.1 series {series} units or frequency changed")
        points.extend(_series_points(series, declared, rows[series]))
    return tuple(sorted(points, key=lambda point: (point.event_time, point.instrument_id)))
