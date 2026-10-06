"""Official mixed secured/unsecured AED overnight reference; no request-path fetch."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from html.parser import HTMLParser
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from threading import Event
import time
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from seiche import store
from seiche.collectors import FileRawCaptureSink
from seiche.sources._async_store import run_store
from seiche.sources.base import RawCapture, Series, SourceFault, utcnow_iso
from seiche.sources.publication import cbuae_donia_freshness

SOURCE = "cbuae_donia"
MNEMONIC = "DONIA"
REMOTE_ID = "UAE_INTEREST_RATES/DONIA"
SOURCE_URL = "https://centralbank.ae/umbraco/surface/interestrate/GetKeyInterestRate"
DATASET_URL = "https://centralbank.ae/en/our-operations/monetary-policy-and-domestic-markets/"
TERMS_URL = "https://centralbank.ae/en/open-data-landing/open-data-policy/"
METHODOLOGY_URL = "https://centralbank.ae/media/kuqd0q5o/attachment-7_donia-term-sheet.pdf"
LATEST_CAPTURE_KEY = "cbuae_donia:latest"
CAPTURE_PREFIX = "cbuae_donia:capture:"
MAX_BODY_BYTES = 2 * 1024 * 1024
TTL_MINUTES = 60
SOURCE_USER_AGENT = "Seiche official-source qualification; https://seiche.info"
DOWNLOAD_DEADLINE_SECONDS = 35
_HTTP_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seiche-donia-http")
_DUBAI = ZoneInfo("Asia/Dubai")
_PANELS = {"oneMonth", "sixMonth", "ytd", "oneYear", "all"}
_ADMITTED_PANELS = _PANELS - {"all"}


class _Chart(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.matches = 0
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        attributes = dict(attrs)
        if attributes.get("id") == "ki-chart-data":
            if self.active or len(attributes) != len(attrs) or attributes.get("type") != "application/json":
                raise ValueError("DONIA chart identity is invalid")
            self.matches += 1
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, value):
        if self.active:
            self.parts.append(value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DONIA chart contains duplicate JSON keys")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("DONIA chart contains a nonfinite number")


def parse_html(payload: bytes, *, fetched_at: str, source_url: str = SOURCE_URL) -> Series:
    if source_url != SOURCE_URL or not 0 < len(payload) <= MAX_BODY_BYTES:
        raise ValueError("DONIA source identity or body bound differs")
    fetched = datetime.fromisoformat(fetched_at)
    if fetched.tzinfo is None or fetched.utcoffset() is None:
        raise ValueError("DONIA capture clock must include a timezone")
    text = payload.decode("utf-8-sig", errors="strict")
    if "\x00" in text:
        raise ValueError("DONIA response contains null bytes")
    parser = _Chart()
    parser.feed(text)
    parser.close()
    if parser.matches != 1 or parser.active:
        raise ValueError("DONIA requires one complete official chart")
    chart = json.loads("".join(parser.parts), object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if not isinstance(chart, dict) or set(chart) != _PANELS:
        raise ValueError("DONIA chart panel identities changed")
    panels = {}
    for name, panel in chart.items():
        # The publisher's all-history panel has unequal label/value counts.
        # Admit only its separately dated, mutually consistent bounded ranges.
        if name not in _ADMITTED_PANELS:
            continue
        if not isinstance(panel, dict):
            raise ValueError("DONIA chart panel is invalid")
        days, values = panel.get("labels"), panel.get("donia")
        if (not isinstance(days, list) or not isinstance(values, list)
                or not 1 <= len(days) <= 10000 or len(days) != len(values)):
            raise ValueError("DONIA chart dates and values are incomplete")
        points = {}
        previous = None
        for day, value in zip(days, values, strict=True):
            if not isinstance(day, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day) is None:
                raise ValueError("DONIA observation date is not canonical")
            observed = date.fromisoformat(day)
            if (observed < date(2021, 12, 7) or observed > fetched.astimezone(_DUBAI).date()
                    or previous is not None and observed <= previous):
                raise ValueError("DONIA observation dates are duplicate, unordered or outside the capture clock")
            previous = observed
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not -10 <= value <= 100):
                raise ValueError("DONIA rate is not a finite percentage")
            points[day] = value
        panels[name] = points
    history = panels["oneYear"]
    last = next(reversed(history))
    if history[last] is None:
        raise ValueError("DONIA latest observation is missing")
    for panel in panels.values():
        if next(reversed(panel)) != last or any(day not in history or history[day] != value for day, value in panel.items()):
            raise ValueError("DONIA dated chart panels disagree")
    valid = {day: float(value) for day, value in history.items() if value is not None}
    return Series(MNEMONIC, SOURCE, REMOTE_ID, "CBUAE DONIA mixed overnight funding reference", "%", "D",
                  fetched_at, pd.Series(list(valid.values()), index=pd.to_datetime(list(valid)), dtype=float))


def _load_cache(*, now: datetime | None = None) -> tuple[Series, dict] | None:
    now = now or datetime.fromisoformat(utcnow_iso())
    manifest = store.load_blob(LATEST_CAPTURE_KEY)
    required = {"schema": "seiche.cbuae-donia-capture.v1", "source": SOURCE,
                "source_url": SOURCE_URL, "terms_url": TERMS_URL, "source_publication_time": None,
                "observation_date_basis": "publisher_chart_date", "unit": "%",
                "history_range": "oneYear", "excluded_chart_ranges": ["all"],
                "benchmark_scope": "mixed secured and unsecured overnight funding", "day_count": "ACT/360"}
    if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in required.items()):
        return None
    if not isinstance(manifest.get("evidence_sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", manifest["evidence_sha256"]) is None:
        return None
    try:
        captured = datetime.fromisoformat(manifest["fetched_at"])
        last = date.fromisoformat(manifest["last_observation_date"])
        if (captured.tzinfo is None or captured > now or last < date(2021, 12, 7)
                or last > captured.astimezone(_DUBAI).date()):
            return None
        key = f"{CAPTURE_PREFIX}{manifest['evidence_sha256']}:{manifest['fetched_at']}"
        if store.load_blob(key) != manifest:
            return None
        item = store.load_series(MNEMONIC)
        if (item is None or (item.mnemonic, item.source, item.remote_id, item.unit, item.freq)
                != (MNEMONIC, SOURCE, REMOTE_ID, "%", "D") or item.points.empty
                or item.fetched_at != manifest["fetched_at"] or item.asof != last.isoformat()
                or not item.points.index.is_monotonic_increasing or not item.points.index.is_unique
                or not all(math.isfinite(float(v)) and -10 <= float(v) <= 100 for v in item.points)
                or float(item.points.iloc[-1]) != manifest["latest_value"]
                or store.load_blob(LATEST_CAPTURE_KEY) != manifest):
            return None
        return item, manifest
    except (KeyError, TypeError, ValueError):
        return None


def _persist(item: Series, capture: RawCapture, raw_root: Path | None, prior: object):
    raw_path = FileRawCaptureSink(raw_root or store.DATA_DIR / "raw").write(capture)
    metadata = {
        "schema": "seiche.cbuae-donia-capture.v1", "source": SOURCE, "source_url": SOURCE_URL,
        "dataset_url": DATASET_URL, "terms_url": TERMS_URL, "methodology_url": METHODOLOGY_URL,
        "fetched_at": item.fetched_at, "source_publication_time": None,
        "observation_date_basis": "publisher_chart_date", "last_observation_date": item.asof,
        "history_range": "oneYear", "excluded_chart_ranges": ["all"],
        "evidence_sha256": capture.evidence_hash, "raw_path": raw_path, "byte_count": len(capture.payload),
        "latest_value": float(item.points.iloc[-1]), "unit": "%", "day_count": "ACT/360",
        "benchmark_scope": "mixed secured and unsecured overnight funding",
        "evidence_class": "observed", "reference_only": True,
        "rights_basis": "CBUAE Open Data Policy: attribution and direct dataset link",
        "attribution": "Source: Central Bank of the UAE. © Central Bank of the UAE.",
        "usage": "Published AED overnight reference; not an executable funding quote or a USD substitute.",
    }
    key = f"{CAPTURE_PREFIX}{capture.evidence_hash}:{item.fetched_at}"
    existing = store.load_blob(key)
    if existing is not None and existing != metadata:
        raise ValueError("DONIA immutable capture identity conflicts")
    blobs = {LATEST_CAPTURE_KEY: metadata}
    if existing is None:
        blobs[key] = metadata
    store.save_series_batch([item], blobs=blobs, expected_blobs={LATEST_CAPTURE_KEY: prior})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download_sync(cancelled: Event):
    # This public endpoint accepts the standard-library transport. Keep its
    # source-specific request out of the API health and store executors.
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
    # Match the public homepage's documented HTML AJAX request. Keep the
    # product identity transparent; no browser cookies or challenge handling.
    request = Request(SOURCE_URL, headers={
        "User-Agent": SOURCE_USER_AGENT, "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html, */*; q=0.01", "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://centralbank.ae/en/",
    })
    if cancelled.is_set():
        raise TimeoutError("DONIA download cancelled")
    with build_opener(_NoRedirect()).open(request, timeout=10) as response:
        if response.status != 200 or response.geturl() != SOURCE_URL:
            raise ValueError("DONIA requires a complete HTTP 200 response")
        declared = response.headers.get("content-length")
        if declared is not None and not 0 < int(declared) <= MAX_BODY_BYTES:
            raise ValueError("DONIA content length outside bound")
        body = bytearray()
        while True:
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("DONIA download deadline exceeded")
            chunk = response.read1(64 * 1024)
            if not chunk:
                break
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise ValueError("DONIA response exceeds byte bound")
            body.extend(chunk)
        if declared is not None and len(body) != int(declared):
            raise ValueError("DONIA response ended before its declared length")
        return bytes(body)


async def _download():
    cancelled = Event()
    try:
        return await asyncio.get_running_loop().run_in_executor(_HTTP_EXECUTOR, _download_sync, cancelled)
    finally:
        cancelled.set()


async def fetch(client: httpx.AsyncClient, faults: list[dict] | None = None, *,
                force: bool = False, raw_root: Path | None = None) -> dict[str, Series]:
    cached = await run_store(_load_cache)
    if not force and cached and await run_store(store.is_fresh, MNEMONIC, TTL_MINUTES):
        return {MNEMONIC: cached[0]}
    prior = await run_store(store.load_blob, LATEST_CAPTURE_KEY)
    try:
        payload = await asyncio.wait_for(_download(), timeout=45)
        fetched_at = utcnow_iso()
        item = parse_html(payload, fetched_at=fetched_at)
        if isinstance(prior, dict) and prior.get("last_observation_date") and item.asof < prior["last_observation_date"]:
            raise ValueError("DONIA publisher observation date regressed")
        capture = RawCapture(market_id="AE-AED", adapter_id=SOURCE, captured_at=datetime.fromisoformat(fetched_at),
                             source_uri=SOURCE_URL, media_type="text/html", payload=payload,
                             evidence_hash=hashlib.sha256(payload).hexdigest())
        await run_store(_persist, item, capture, raw_root, prior)
        committed = await run_store(_load_cache)
        if not committed:
            raise ValueError("DONIA committed generation failed validation")
        return {MNEMONIC: committed[0]}
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if faults is not None:
            faults.append({"source": SOURCE, "detail": detail})
            current = await run_store(_load_cache)
            if current:
                return {MNEMONIC: current[0]}
        raise SourceFault(SOURCE, detail) from exc


def read_reference(*, now: datetime) -> dict:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("DONIA evaluation clock must include a timezone")
    row = {"currency": "AED", "label": "UAE overnight funding", "instrument": MNEMONIC,
           "value": None, "unit": "%", "as_of": None, "status": "UNAVAILABLE",
           "source": "Central Bank of the UAE", "source_url": DATASET_URL,
           "missed_publication_opportunities": None, "reference_only": True,
           "reason": "No validated CBUAE DONIA observation is available. USD rates and the AED peg are not substitutes."}
    try:
        cached = _load_cache(now=now)
        if cached is None:
            return row
        item, manifest = cached
        freshness = cbuae_donia_freshness(item.asof, now=now)
        evidence = {k: v for k, v in manifest.items() if k not in {"raw_path", "latest_value"}}
        evidence.update(freshness)
        row.update(value=float(item.points.iloc[-1]), as_of=item.asof, fetched_at=item.fetched_at,
                   status=freshness["staleness"].upper(), reason=None, evidence=evidence,
                   freshness_basis=freshness["freshness_basis"])
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error):
        pass
    return row
