"""Scheduled, keyless ECB FX history with explicit EUR-base provenance.

All rates are quote-currency units per one euro. They are daily reference
observations, never executable quotes or the central parity of another bank.
The complete XML response is validated before any persistent write occurs.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

import httpx
import pandas as pd

from seiche import store
from seiche.collectors import FileRawCaptureSink
from seiche.config import ECB_FX_CURRENCIES, USER_AGENT
from seiche.sources.base import RawCapture, Series, SourceFault, utcnow_iso

SOURCE = "ecb_fx"
TTL_MINUTES = 360
FULL_REFRESH_DAYS = 30
MAX_BODY_BYTES = 16 * 1024 * 1024
DOWNLOAD_DEADLINE_SECONDS = 90
LATEST_CAPTURE_KEY = "ecb_fx:latest"
FULL_CAPTURE_KEY = "ecb_fx:full-history"
CAPTURE_PREFIX = "ecb_fx:capture:"
TERMS_URL = "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html"
SOURCE_URLS = {
    "daily": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
    "history90d": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml",
    "history": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml",
}
# Registered current coverage; historical XML also contains retired currencies.
CURRENCIES = ECB_FX_CURRENCIES
_ECB_NS = "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"
_GESMES_NS = "http://www.gesmes.org/xml/2002-08-01"
_CUBE = f"{{{_ECB_NS}}}Cube"
_CURRENCY = re.compile(r"[A-Z]{3}")
_RATE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
Mode = Literal["auto", "daily", "history90d", "history"]


def remote_id(currency: str) -> str:
    """Stable ECB SDMX identity corresponding to the XML observation."""
    if not _CURRENCY.fullmatch(currency) or currency == "EUR":
        raise ValueError("invalid ECB quote currency")
    return f"EXR/D.{currency}.EUR.SP00.A"


def parse_xml(
    payload: bytes, *, fetched_at: str, source_url: str
) -> dict[str, Series]:
    """Validate an entire ECB document, then construct sorted daily series.

    Missing currencies stay absent: suspended and retired series are never
    forward-filled. The byte limit also applies to direct parser callers.
    """
    if not payload or len(payload) > MAX_BODY_BYTES:
        raise ValueError("ECB FX response size outside permitted bounds")
    if source_url not in SOURCE_URLS.values():
        raise ValueError("unexpected ECB FX source URL")
    captured = datetime.fromisoformat(fetched_at)
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("ECB FX fetched_at must include a timezone")
    captured = captured.astimezone(UTC)
    text = payload.decode("utf-8-sig", errors="strict")
    # Reject declarations before ElementTree sees them, including the UTF-16
    # null-byte route that could otherwise evade a textual declaration check.
    if "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError("ECB FX XML declarations or null bytes are forbidden")
    declaration = re.match(r"\s*<\?xml\s+([^?]+)\?>", text)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)", declaration[1], re.I)
        if encoding and encoding[1].lower() not in {"utf-8", "utf8"}:
            raise ValueError("ECB FX XML must use UTF-8")
    root = ElementTree.fromstring(text)
    text_nodes = {f"{{{_GESMES_NS}}}subject", f"{{{_GESMES_NS}}}name"}
    for node in root.iter():
        if (node.tag not in text_nodes and (node.text or "").strip()) or (node.tail or "").strip():
            raise ValueError("unexpected text in ECB FX structure")
    if root.tag != f"{{{_GESMES_NS}}}Envelope" or root.attrib:
        raise ValueError("unexpected ECB FX envelope")
    expected = [f"{{{_GESMES_NS}}}subject", f"{{{_GESMES_NS}}}Sender", _CUBE]
    if [child.tag for child in root] != expected:
        raise ValueError("unexpected ECB FX envelope children")
    subject, sender, container = root
    if subject.attrib or len(subject) or not (subject.text or "").strip():
        raise ValueError("invalid ECB FX subject")
    if sender.attrib or len(sender) != 1 or sender[0].tag != f"{{{_GESMES_NS}}}name":
        raise ValueError("invalid ECB FX sender")
    if sender[0].attrib or len(sender[0]) or (sender[0].text or "").strip() != "European Central Bank":
        raise ValueError("unexpected ECB FX publisher")
    if container.attrib or not len(container):
        raise ValueError("ECB FX has no dated observations")

    rows: dict[str, list[tuple[date, float]]] = {}
    days: set[date] = set()
    for day_node in container:
        if day_node.tag != _CUBE or set(day_node.attrib) != {"time"}:
            raise ValueError("unexpected ECB FX date node")
        raw_day = day_node.attrib["time"]
        day = date.fromisoformat(raw_day)
        if day.isoformat() != raw_day or not date(1999, 1, 1) <= day <= captured.date():
            raise ValueError("invalid or future ECB FX observation date")
        if day in days or not len(day_node):
            raise ValueError("duplicate or empty ECB FX observation date")
        days.add(day)
        currencies: set[str] = set()
        for node in day_node:
            if node.tag != _CUBE or set(node.attrib) != {"currency", "rate"} or len(node):
                raise ValueError("unexpected ECB FX rate node")
            currency, raw_rate = node.attrib["currency"], node.attrib["rate"]
            remote_id(currency)
            if currency in currencies or not _RATE.fullmatch(raw_rate):
                raise ValueError("duplicate currency or invalid ECB FX rate")
            value = float(raw_rate)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("ECB FX rate must be finite and positive")
            currencies.add(currency)
            rows.setdefault(currency, []).append((day, value))

    result = {}
    for currency, points in sorted(rows.items()):
        points.sort()
        mnemonic = f"ECBFX_{currency}"
        result[mnemonic] = Series(
            mnemonic=mnemonic,
            source=SOURCE,
            remote_id=remote_id(currency),
            label=f"{currency} per euro (ECB daily reference rate)",
            unit=f"{currency}/EUR",
            freq="D",
            fetched_at=captured.isoformat(timespec="seconds"),
            points=pd.Series(
                [value for _, value in points],
                index=pd.DatetimeIndex([day for day, _ in points]),
                dtype=float,
            ),
        )
    return result


async def _download(client: httpx.AsyncClient, url: str) -> bytes:
    """Bound decompressed bytes as they arrive, without accepting redirects."""
    async with asyncio.timeout(DOWNLOAD_DEADLINE_SECONDS), client.stream(
        "GET", url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"},
        timeout=60, follow_redirects=False,
    ) as response:
        response.raise_for_status()
        declared = response.headers.get("content-length")
        if declared is not None and not 0 < int(declared) <= MAX_BODY_BYTES:
            raise ValueError("ECB FX content length outside permitted bounds")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise ValueError("ECB FX response exceeds byte limit")
            body.extend(chunk)
        return bytes(body)


def _load_cache() -> dict[str, Series]:
    """Return one complete, registered generation, never a mixed-clock cache."""
    manifest = store.load_blob(LATEST_CAPTURE_KEY)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "seiche.ecb-fx-capture.v1"
        or manifest.get("source") != SOURCE
        or manifest.get("base_currency") != "EUR"
        or not isinstance(manifest.get("mode"), str)
        or manifest.get("mode") not in SOURCE_URLS
        or manifest.get("source_url") != SOURCE_URLS[manifest["mode"]]
        or manifest.get("source_publication_time") is not None
    ):
        return {}
    names = manifest.get("series")
    current = manifest.get("current_currencies")
    evidence_hash = manifest.get("evidence_sha256")
    if (
        not isinstance(names, list) or not names
        or any(not isinstance(name, str) or not re.fullmatch(r"ECBFX_[A-Z]{3}", name) for name in names)
        or len(names) != len(set(names))
        or not isinstance(current, list)
        or any(not isinstance(currency, str) or not _CURRENCY.fullmatch(currency) for currency in current)
        or len(current) != len(set(current))
        or not set(CURRENCIES).issubset(current)
        or not {f"ECBFX_{currency}" for currency in CURRENCIES}.issubset(names)
        or not isinstance(evidence_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", evidence_hash)
    ):
        return {}
    try:
        captured = datetime.fromisoformat(manifest["fetched_at"])
        first = date.fromisoformat(manifest["first_observation_date"])
        last = date.fromisoformat(manifest["last_observation_date"])
        now = datetime.fromisoformat(utcnow_iso())
        if (
            captured.tzinfo is None or captured.utcoffset() is None
            or captured > now
            or not date(1999, 1, 1) <= first <= last <= captured.astimezone(UTC).date()
        ):
            return {}
    except (KeyError, TypeError, ValueError):
        return {}
    capture_key = f"{CAPTURE_PREFIX}{evidence_hash}:{manifest['mode']}:{manifest['fetched_at']}"
    if store.load_blob(capture_key) != manifest:
        return {}
    result = {}
    for currency in CURRENCIES:
        name = f"ECBFX_{currency}"
        series = store.load_series(name)
        if (
            series is None or series.mnemonic != name or series.source != SOURCE
            or series.remote_id != remote_id(currency) or series.unit != f"{currency}/EUR"
            or series.freq != "D" or series.points.empty
            or series.asof != last.isoformat()
        ):
            return {}
        try:
            series_capture = datetime.fromisoformat(series.fetched_at)
            if series_capture.tzinfo is None or series_capture.utcoffset() is None or series_capture != captured:
                return {}
        except (TypeError, ValueError):
            return {}
        result[name] = series
    return result


def _mode(mode: Mode, now: datetime) -> str:
    if mode != "auto":
        if mode not in SOURCE_URLS:
            raise ValueError("invalid ECB FX download mode")
        return mode
    full = store.load_blob(FULL_CAPTURE_KEY)
    if isinstance(full, dict):
        try:
            captured = datetime.fromisoformat(full["fetched_at"])
            age = now - captured
            if timedelta(0) <= age < timedelta(days=FULL_REFRESH_DAYS):
                return "history90d"
        except (KeyError, TypeError, ValueError):
            pass
    return "history"


def _persist(
    series: dict[str, Series], capture: RawCapture, mode: str, raw_root: Path | None,
    prior_latest: object,
) -> None:
    raw_path = FileRawCaptureSink(raw_root or store.DATA_DIR / "raw").write(capture)
    latest_day = max(s.asof for s in series.values())
    metadata = {
        "schema": "seiche.ecb-fx-capture.v1",
        "source": SOURCE,
        "source_url": capture.source_uri,
        "fetched_at": capture.captured_at.isoformat(timespec="seconds"),
        "source_publication_time": None,
        "evidence_sha256": capture.evidence_hash,
        "raw_path": raw_path,
        "byte_count": len(capture.payload),
        "base_currency": "EUR",
        "quote_convention": "quote currency units per one EUR",
        "first_observation_date": min(s.points.index[0].date().isoformat() for s in series.values()),
        "last_observation_date": latest_day,
        "series": sorted(series),
        "current_currencies": sorted(s.mnemonic[6:] for s in series.values() if s.asof == latest_day),
        "observation_count": sum(len(s.points) for s in series.values()),
        "mode": mode,
        "terms_url": TERMS_URL,
        "evidence_class": "observed",
        "usage": "Daily informational reference rates; not executable quotes.",
    }
    capture_key = f"{CAPTURE_PREFIX}{capture.evidence_hash}:{mode}:{metadata['fetched_at']}"
    previous = store.load_blob(capture_key)
    if previous is not None and previous != metadata:
        raise ValueError("ECB FX capture metadata conflicts with immutable identity")
    manifests = {LATEST_CAPTURE_KEY: metadata}
    if previous is None:
        manifests[capture_key] = metadata
    if mode == "history":
        manifests[FULL_CAPTURE_KEY] = metadata
    # All observations, vintages, and completed manifests form one generation.
    # A failed transaction may leave an unreferenced immutable raw archive.
    store.save_series_batch(
        series.values(), blobs=manifests, expected_blobs={LATEST_CAPTURE_KEY: prior_latest},
    )


async def fetch(
    client: httpx.AsyncClient,
    faults: list[dict] | None = None,
    *,
    mode: Mode = "auto",
    force: bool = False,
    raw_root: Path | None = None,
) -> dict[str, Series]:
    """Collect on a scheduler; auto bootstraps history then refreshes 90 days.

    Historical captures use today's knowledge clock, never an invented old
    publication/capture time. A failed refresh retains the prior cache and
    reports the source fault; without a fault sink it raises instead.
    """
    if mode not in {"auto", *SOURCE_URLS}:
        raise ValueError("invalid ECB FX download mode")
    cached = await asyncio.to_thread(_load_cache)
    if not force and mode == "auto" and cached:
        if all(store.is_fresh(name, TTL_MINUTES) for name in cached):
            return cached
    prior_latest = await asyncio.to_thread(store.load_blob, LATEST_CAPTURE_KEY)
    selected_mode = _mode(mode, datetime.fromisoformat(utcnow_iso()))
    try:
        url = SOURCE_URLS[selected_mode]
        payload = await _download(client, url)
        fetched_at = utcnow_iso()
        parsed = parse_xml(payload, fetched_at=fetched_at, source_url=url)
        if selected_mode == "daily" and len({day for s in parsed.values() for day in s.points.index}) != 1:
            raise ValueError("ECB daily XML contains multiple observation dates")
        latest_day = max(series.asof for series in parsed.values())
        if any(
            f"ECBFX_{currency}" not in parsed or parsed[f"ECBFX_{currency}"].asof != latest_day
            for currency in CURRENCIES
        ):
            raise ValueError("ECB FX latest date lacks registered current currency coverage")
        if isinstance(prior_latest, dict):
            prior_day = prior_latest.get("last_observation_date")
            if isinstance(prior_day, str) and date.fromisoformat(latest_day) < date.fromisoformat(prior_day):
                raise ValueError(f"ECB FX latest observation date regressed from {prior_day} to {latest_day}")
        capture = RawCapture(
            market_id="GLOBAL-FX", adapter_id=SOURCE,
            captured_at=datetime.fromisoformat(fetched_at), source_uri=url,
            media_type="application/xml", payload=payload,
            evidence_hash=hashlib.sha256(payload).hexdigest(),
        )
        await asyncio.to_thread(_persist, parsed, capture, selected_mode, raw_root, prior_latest)
        # Store merges rolling windows with the existing full history.
        completed = await asyncio.to_thread(_load_cache)
        if not completed:
            raise ValueError("ECB FX committed capture failed cache generation validation")
        return completed
    except Exception as exc:
        detail = f"{selected_mode}: {type(exc).__name__}: {exc}"
        if faults is not None:
            faults.append({"source": SOURCE, "detail": detail})
            # Another collector may have committed while this request was in
            # flight. Return only the currently validated persisted generation.
            current = await asyncio.to_thread(_load_cache)
            if current:
                return current
        raise SourceFault(SOURCE, detail) from exc
