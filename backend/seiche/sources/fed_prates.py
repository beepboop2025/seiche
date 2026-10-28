"""Original Federal Reserve Board IORB observations, distinct from FRED copies.

The current PRATES file has effective dates, not per-observation publication
receipts. Acquisition time is the knowledge boundary; no historical PIT claim
is inferred from these dates.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from seiche.sources.canonical import FetchedDocument, ParsedPoint, PublicationTimePolicy

SOURCE_URL = "https://www.federalreserve.gov/releases/prates/data/FRB_PRATES_xml.zip"
TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
INSTRUMENT_ID = "US.FED.IORB_DIRECT"
SERIES_NAME = "RESBM_N.D"
MAX_ARCHIVE_BYTES = 2_000_000
MAX_XML_BYTES = 2_000_000


async def fetch_document(client) -> tuple[FetchedDocument, ...]:
    """Download only the fixed publisher endpoint with byte/time bounds."""
    async with asyncio.timeout(30):
        async with client.stream(
            "GET", SOURCE_URL, follow_redirects=False,
            headers={"Accept-Encoding": "identity",
                     "User-Agent": "LiquidityLab-Original-Publisher-Research/1 contact mrinal@liquilens.in"},
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"Federal Reserve PRATES HTTP {response.status_code}")
            if str(response.url) != SOURCE_URL:
                raise ValueError("Federal Reserve PRATES source changed")
            encoding = response.headers.get("content-encoding", "").lower().strip()
            if encoding not in {"", "identity"}:
                raise ValueError("Federal Reserve PRATES transport encoding changed")
            declared = response.headers.get("content-length")
            if declared is not None and not 0 <= int(declared) <= MAX_ARCHIVE_BYTES:
                raise ValueError("Federal Reserve PRATES archive exceeds bound")
            payload = bytearray()
            async for chunk in response.aiter_raw():
                payload.extend(chunk)
                if len(payload) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Federal Reserve PRATES archive exceeds bound")
    return (FetchedDocument(SOURCE_URL, "application/zip", bytes(payload), "fed_prates"),)


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    if document.source_uri != SOURCE_URL or len(document.payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("unrecognized or oversized Federal Reserve PRATES source")
    with zipfile.ZipFile(io.BytesIO(document.payload)) as archive:
        members = archive.infolist()
        matches = [m for m in members if m.filename == "PRATES_data.xml"]
        if len(members) > 16 or len(matches) != 1:
            raise ValueError("Federal Reserve PRATES XML member changed")
        member = matches[0]
        if member.flag_bits & 1 or not 0 < member.file_size <= MAX_XML_BYTES:
            raise ValueError("Federal Reserve PRATES XML size or encryption changed")
        xml = archive.read(member)
    text = xml.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Federal Reserve PRATES XML declarations forbidden")
    root = ET.fromstring(text)
    series = [node for node in root.iter()
              if node.tag.rsplit("}", 1)[-1] == "Series"
              and node.attrib.get("SERIES_NAME") == SERIES_NAME]
    if len(series) != 1:
        raise ValueError("Federal Reserve IORB series missing or duplicated")
    series = series[0]
    if series.attrib.get("UNIT") != "Percent" or series.attrib.get("UNIT_MULT") != "1":
        raise ValueError("Federal Reserve IORB units changed")
    points = []
    seen = set()
    for node in series:
        if node.tag.rsplit("}", 1)[-1] != "Obs" or node.attrib.get("OBS_STATUS") != "A":
            continue
        raw_day = node.attrib.get("TIME_PERIOD")
        day = date.fromisoformat(raw_day)
        if raw_day != day.isoformat() or day in seen:
            raise ValueError("Federal Reserve IORB date invalid or duplicated")
        seen.add(day)
        try:
            value = Decimal(node.attrib["OBS_VALUE"])
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("Federal Reserve IORB observation invalid") from exc
        if not value.is_finite():
            raise ValueError("Federal Reserve IORB observation nonfinite")
        evidence = json.dumps({
            "source_url": SOURCE_URL, "series": SERIES_NAME, "field": "OBS_VALUE",
            "unit": "Percent", "unit_mult": "1", "observation": dict(node.attrib),
        }, sort_keys=True, separators=(",", ":")).encode()
        points.append(ParsedPoint(
            INSTRUMENT_ID, day, value, evidence,
            revision_id=f"fed-prates:{SERIES_NAME}:{day.isoformat()}:{hashlib.sha256(evidence).hexdigest()[:16]}",
            publication_time_policy=PublicationTimePolicy.UNKNOWN,
        ))
    if not points:
        raise ValueError("Federal Reserve IORB has no available observations")
    return tuple(sorted(points, key=lambda point: point.event_time))
