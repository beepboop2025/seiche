"""Federal Reserve Board commercial paper rates: implemented, disabled by rights.

Status: OFF. The US pack declares no ``fed_cp`` adapter, so ``rights_gate_open``
is false, ``build_official_adapters`` registers nothing, and no request is made
and nothing is stored or served. The FRED copies (``US.CP.NONFINANCIAL_3M`` on
``fred_daily``, and the legacy CP_FIN_3M and CP_NONFIN_3M series) keep their
own metadata-only policy.

Why it is off: the Board's notice (https://www.federalreserve.gov/disclaimer.htm)
puts Board information in the public domain "Unless otherwise indicated". The
release page (``RELEASE_URL``, checked 2026-11-02) is headed "Derived from data
supplied by The Depository Trust & Clearing Corporation", and the Board's About
page (``ABOUT_URL``) states that the release "is derived from data supplied by
The Depository Trust & Clearing Corporation (DTCC)". That third-party
indication may take these rates outside the public-domain notice, and FRED
marks its H.15 copies of them "Data in this graph are copyrighted". Enabling
needs an owner rights decision recorded in the source registry
(``frb_commercial_paper``) and a pack declaration; this module never decides it.

Source when enabled: the Board's own CP release package (``SOURCE_URL``), not
FRED and not the Data Download Program "Build Your Package" tool the Board is
retiring. Series ``RIFSPPFAAD90_N.B`` and ``RIFSPPNAAD90_N.B`` are the 90-day
AA financial and AA nonfinancial rates in percent on business days. ``ND``
rows, which the Board prints as "n.a." when trades were insufficient, stay
absent. The release is usually posted at 13:00 New York time on the next
business day.
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

SOURCE_URL = "https://www.federalreserve.gov/releases/cp/data/FRB_CP_xml.zip"
RELEASE_URL = "https://www.federalreserve.gov/releases/cp/default.htm"
ABOUT_URL = "https://www.federalreserve.gov/releases/cp/about.htm"
TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
ADAPTER_ID = "fed_cp"
MEMBER = "CP_data.xml"
FINANCIAL_INSTRUMENT_ID = "US.FED.CP_AA_FINANCIAL_90D_DIRECT"
NONFINANCIAL_INSTRUMENT_ID = "US.FED.CP_AA_NONFINANCIAL_90D_DIRECT"
SERIES = {
    "RIFSPPFAAD90_N.B": FINANCIAL_INSTRUMENT_ID,
    "RIFSPPNAAD90_N.B": NONFINANCIAL_INSTRUMENT_ID,
}
# Exact publisher declarations; any change fails closed rather than rescaling.
SERIES_ATTRIBUTES = {
    "RIFSPPFAAD90_N.B": {"FREQ": "9", "UNIT": "Percent", "UNIT_MULT": "1", "CP_TERM": "90", "CP_TYPE": "FAA"},
    "RIFSPPNAAD90_N.B": {"FREQ": "9", "UNIT": "Percent", "UNIT_MULT": "1", "CP_TERM": "90", "CP_TYPE": "NAA"},
}
# The 2026-11-02 package is 6.3 MB zipped and 112 MB of XML.
MAX_ARCHIVE_BYTES = 24_000_000
MAX_XML_BYTES = 512_000_000
MAX_MEMBERS = 16
MAX_ABS_PERCENT = Decimal("50")
USER_AGENT = "LiquidityLab-Original-Publisher-Research/1 contact mrinal@liquilens.in"


def declared_instruments(pack) -> frozenset[str]:
    """The direct CP instruments the pack itself places on this adapter."""

    return frozenset(
        instrument_id
        for instrument_id in SERIES.values()
        if (spec := pack.instrument_map.get(instrument_id)) is not None
        and spec.source_adapter_id == ADAPTER_ID
    )


def rights_gate_open(pack) -> bool:
    """True only when the pack declares an allowed official-open CP record."""

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
            headers={"Accept-Encoding": "identity", "User-Agent": USER_AGENT},
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"Federal Reserve CP HTTP {response.status_code}")
            if str(response.url) != SOURCE_URL:
                raise ValueError("Federal Reserve CP source changed")
            encoding = response.headers.get("content-encoding", "").lower().strip()
            if encoding not in {"", "identity"}:
                raise ValueError("Federal Reserve CP transport encoding changed")
            declared = response.headers.get("content-length")
            if declared is not None and not 0 <= int(declared) <= MAX_ARCHIVE_BYTES:
                raise ValueError("Federal Reserve CP archive exceeds bound")
            payload = bytearray()
            async for chunk in response.aiter_raw():
                payload.extend(chunk)
                if len(payload) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Federal Reserve CP archive exceeds bound")
    return (FetchedDocument(SOURCE_URL, "application/zip", bytes(payload), ADAPTER_ID),)


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    found, rows = read_release_series(
        document, source_url=SOURCE_URL, member=MEMBER, wanted=SERIES,
        label="Federal Reserve CP", max_archive_bytes=MAX_ARCHIVE_BYTES,
        max_xml_bytes=MAX_XML_BYTES, max_members=MAX_MEMBERS,
    )
    points = []
    for series, instrument_id in SERIES.items():
        if len(found[series]) != 1:
            raise ValueError(f"Federal Reserve CP series {series} missing or duplicated")
        declared = found[series][0]
        pinned = SERIES_ATTRIBUTES[series]
        if any(declared.get(key) != value for key, value in pinned.items()):
            raise ValueError(f"Federal Reserve CP series {series} units, tenor or frequency changed")
        seen: set[date] = set()
        for row in rows[series]:
            # ND rows ("n.a." on the release) are unavailable, never zero.
            if row.get("OBS_STATUS") != "A":
                continue
            raw_day = row.get("TIME_PERIOD")
            try:
                day = date.fromisoformat(raw_day or "")
            except ValueError as exc:
                raise ValueError("Federal Reserve CP date invalid") from exc
            if raw_day != day.isoformat() or day in seen or day.weekday() >= 5:
                raise ValueError("Federal Reserve CP date invalid, duplicated or a weekend")
            seen.add(day)
            try:
                value = Decimal(row["OBS_VALUE"])
            except (KeyError, InvalidOperation) as exc:
                raise ValueError("Federal Reserve CP observation invalid") from exc
            if not value.is_finite() or abs(value) >= MAX_ABS_PERCENT:
                raise ValueError("Federal Reserve CP observation out of bounds")
            evidence = json.dumps({
                "source_url": SOURCE_URL, "member": MEMBER, "series": series,
                "field": "OBS_VALUE", "declared": {key: declared[key] for key in pinned},
                "observation": row,
            }, sort_keys=True, separators=(",", ":")).encode()
            points.append(ParsedPoint(
                instrument_id, day, value, evidence,
                revision_id=f"fed-cp:{series}:{day.isoformat()}:{hashlib.sha256(evidence).hexdigest()[:16]}",
            ))
    return tuple(sorted(points, key=lambda point: (point.event_time, point.instrument_id)))
