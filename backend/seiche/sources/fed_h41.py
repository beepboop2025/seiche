"""Federal Reserve Board H.4.1 reserve balances, distinct from the FRED copy.

GATED, AND OFF TODAY. No existing allowed rights record covers the Board's
H.4.1 release: the ``fed_prates`` record names only the PRATES file, and the
source registry has no Board H.4.1 entry. This adapter is complete and tested,
but ``build_official_adapters`` registers it only when the US pack itself
declares an official-open ``fed_h41`` adapter with redistribution allowed and
the direct instrument on that adapter (``rights_gate_open``). Until the owner
adds that rights record, nothing is fetched, stored or served, and
``US.FED.RESERVE_BALANCES`` (FRED ``WRESBAL``) stays withheld.

Source: the Board's own release package, not FRED and not the Data Download
Program "Build Your Package" tool that the Board is retiring. Series
``RESH4R_XAW_N.WW`` is "Reserve balances with Federal Reserve Banks: week
average" in millions of US dollars (UNIT=Currency, UNIT_MULT=1000000), dated
by the Wednesday that ends each week. It is the same number FRED republishes as
WRESBAL. The Board releases H.4.1 on Thursdays at 16:30 New York time, delayed
by federal holidays; the package carries no per-row publication receipt, so the
pack's declared clock, not this parser, supplies the inferred publication time.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.parsers import expat

from seiche.domain.observation import ConnectorClassification, RedistributionStatus
from seiche.sources.canonical import FetchedDocument, ParsedPoint

SOURCE_URL = "https://www.federalreserve.gov/releases/h41/data/FRB_H41_xml.zip"
RELEASE_URL = "https://www.federalreserve.gov/releases/h41/"
TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
ADAPTER_ID = "fed_h41"
INSTRUMENT_ID = "US.FED.RESERVE_BALANCES_DIRECT"
MEMBER = "H41_data.xml"
SERIES_NAME = "RESH4R_XAW_N.WW"
# Exact publisher declarations; any change fails closed rather than rescaling.
SERIES_ATTRIBUTES = {"FREQ": "19", "UNIT": "Currency", "UNIT_MULT": "1000000", "CURRENCY": "USD"}
# The 2026-11-02 package is 9.1 MB zipped and 127 MB of XML.
MAX_ARCHIVE_BYTES = 32_000_000
MAX_XML_BYTES = 512_000_000
MAX_MEMBERS = 16
_CHUNK_BYTES = 1 << 20
_WEDNESDAY = 2


def rights_gate_open(pack) -> bool:
    """True only when the pack itself declares the direct H.4.1 rights record."""

    adapter = pack.adapter_map.get(ADAPTER_ID)
    instrument = pack.instrument_map.get(INSTRUMENT_ID)
    return (
        adapter is not None
        and instrument is not None
        and instrument.source_adapter_id == ADAPTER_ID
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


def _scan(stream) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Stream the release XML once; keep only the selected series' rows.

    A DOCTYPE or entity declaration is refused before any further parsing.
    """
    found: list[dict[str, str]] = []
    rows: list[dict[str, str]] = []
    inside = False

    def local(name: str) -> str:
        return name.rsplit(" ", 1)[-1]

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal inside
        tag = local(name)
        if tag == "Series":
            inside = attributes.get("SERIES_NAME") == SERIES_NAME
            if inside:
                found.append(dict(attributes))
        elif inside and tag == "Obs":
            rows.append(dict(attributes))

    def end(name: str) -> None:
        nonlocal inside
        if local(name) == "Series":
            inside = False

    def forbidden(*_args) -> None:
        raise ValueError("Federal Reserve H.4.1 XML declarations forbidden")

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    total = 0
    try:
        while chunk := stream.read(_CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_XML_BYTES:
                raise ValueError("Federal Reserve H.4.1 XML exceeds bound")
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except expat.ExpatError as exc:
        raise ValueError("Federal Reserve H.4.1 XML is malformed") from exc
    return found, rows


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    if document.source_uri != SOURCE_URL or len(document.payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("unrecognized or oversized Federal Reserve H.4.1 source")
    with zipfile.ZipFile(io.BytesIO(document.payload)) as archive:
        members = archive.infolist()
        matches = [m for m in members if m.filename == MEMBER]
        if len(members) > MAX_MEMBERS or len(matches) != 1:
            raise ValueError("Federal Reserve H.4.1 XML member changed")
        member = matches[0]
        if member.flag_bits & 1 or not 0 < member.file_size <= MAX_XML_BYTES:
            raise ValueError("Federal Reserve H.4.1 XML size or encryption changed")
        with archive.open(member) as stream:
            found, rows = _scan(stream)
    if len(found) != 1:
        raise ValueError("Federal Reserve H.4.1 reserve-balance series missing or duplicated")
    declared = found[0]
    if any(declared.get(key) != value for key, value in SERIES_ATTRIBUTES.items()):
        raise ValueError("Federal Reserve H.4.1 reserve-balance units or frequency changed")
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
            "source_url": SOURCE_URL, "member": MEMBER, "series": SERIES_NAME,
            "field": "OBS_VALUE", "declared": {key: declared[key] for key in SERIES_ATTRIBUTES},
            "observation": row,
        }, sort_keys=True, separators=(",", ":")).encode()
        points.append(ParsedPoint(
            INSTRUMENT_ID, day, value, evidence,
            revision_id=f"fed-h41:{SERIES_NAME}:{day.isoformat()}:{hashlib.sha256(evidence).hexdigest()[:16]}",
        ))
    if not points:
        raise ValueError("Federal Reserve H.4.1 has no available reserve-balance observations")
    return tuple(sorted(points, key=lambda point: point.event_time))
