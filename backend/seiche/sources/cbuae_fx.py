"""CBUAE daily VAT reference FX with AED quote orientation and raw evidence.

The source's table-update date is the observation-date basis; it does not
identify a market fixing time. These informational rates are not tradable FX.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import sqlite3
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from seiche import store
from seiche.collectors import FileRawCaptureSink
from seiche.config import USER_AGENT
from seiche.sources._async_store import run_store
from seiche.sources.base import RawCapture, Series, SourceFault, utcnow_iso
from seiche.sources.publication import cbuae_fx_freshness

SOURCE = "cbuae_fx"
SOURCE_URL = "https://centralbank.ae/umbraco/Surface/Exchange/GetExchangeRateAllCurrency"
DATASET_URL = "https://centralbank.ae/en/forex-eibor/exchange-rates/"
TERMS_URL = "https://centralbank.ae/en/open-data-landing/open-data-policy/"
TTL_MINUTES = 360
MAX_BODY_BYTES = 1024 * 1024
DOWNLOAD_DEADLINE_SECONDS = 45
LATEST_CAPTURE_KEY = "cbuae_fx:latest"
CAPTURE_PREFIX = "cbuae_fx:capture:"
CURRENCIES = ("USD", "INR", "EUR", "GBP", "JPY", "CHF", "SGD")
REQUIRED_CURRENCIES = ("USD", "INR")
CURRENCY_NAMES = {
    "US Dollar": "USD", "Indian Rupee": "INR", "Euro": "EUR",
    "GB Pound": "GBP", "Japanese Yen": "JPY", "Swiss Franc": "CHF",
    "Singapore Dollar": "SGD",
}
_RATE = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?")
_UPDATE = re.compile(
    r"Last updated:\s*([A-Za-z]+ \d{2} [A-Za-z]+ \d{4} \d{2}:\d{2}:\d{2} [AP]M)"
)
_DUBAI = ZoneInfo("Asia/Dubai")
_PURPOSE = "Exchange rates against UAE Dirham for VAT related obligations"


class _TableParser(HTMLParser):
    """Capture text and complete table cells without executing page scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag == "table":
            if self.table is not None:
                raise ValueError("nested CBUAE FX table")
            self.table = []
        elif tag == "tr" and self.table is not None:
            if self.row is not None:
                raise ValueError("unclosed CBUAE FX row")
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            if self.cell is not None:
                raise ValueError("unclosed CBUAE FX cell")
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1
            return
        if self.ignored:
            return
        if tag in {"td", "th"} and self.cell is not None:
            assert self.row is not None
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.cell is not None:
                raise ValueError("unclosed CBUAE FX cell")
            assert self.table is not None
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.row is not None:
                raise ValueError("unclosed CBUAE FX row")
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.text.append(data)
            if self.cell is not None:
                self.cell.append(data)


def remote_id(currency: str) -> str:
    if currency not in CURRENCIES:
        raise ValueError("unregistered CBUAE FX currency")
    return f"VAT/AED_PER_{currency}"


def _parse(payload: bytes, fetched_at: str, source_url: str) -> tuple[dict[str, Series], datetime]:
    if not payload or len(payload) > MAX_BODY_BYTES:
        raise ValueError("CBUAE FX response size outside permitted bounds")
    if source_url != SOURCE_URL:
        raise ValueError("unexpected CBUAE FX source URL")
    captured = datetime.fromisoformat(fetched_at)
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("CBUAE FX fetched_at must include a timezone")
    captured = captured.astimezone(UTC)
    text = payload.decode("utf-8-sig", errors="strict")
    if "\x00" in text:
        raise ValueError("CBUAE FX response contains null bytes")
    parser = _TableParser()
    parser.feed(text)
    parser.close()
    if parser.table is not None or parser.row is not None or parser.cell is not None:
        raise ValueError("incomplete CBUAE FX table")
    plain = " ".join(" ".join(parser.text).split())
    if _PURPOSE not in plain:
        raise ValueError("CBUAE FX dataset heading missing")
    updates = _UPDATE.findall(plain)
    if len(updates) != 1:
        raise ValueError("CBUAE FX requires one dated publisher update")
    updated = datetime.strptime(updates[0], "%A %d %B %Y %I:%M:%S %p").replace(tzinfo=_DUBAI)
    if updated.strftime("%A %d %B %Y %I:%M:%S %p") != updates[0]:
        raise ValueError("CBUAE FX publisher date and weekday disagree")
    if updated.date() < date(2018, 1, 1) or updated > captured:
        raise ValueError("invalid or future CBUAE FX publisher update")
    tables = [table for table in parser.tables if table and table[0] == ["", "Currency", "Rates"]]
    if len(tables) != 1 or len(tables[0]) < 2:
        raise ValueError("CBUAE FX requires one complete currency table")
    seen: set[str] = set()
    rates: dict[str, float] = {}
    for row in tables[0][1:]:
        if len(row) != 3 or row[0] or not row[1] or row[1] in seen:
            raise ValueError("duplicate or malformed CBUAE FX currency row")
        seen.add(row[1])
        if not _RATE.fullmatch(row[2]):
            raise ValueError("invalid CBUAE FX rate")
        value = float(row[2])
        if not math.isfinite(value) or value <= 0:
            raise ValueError("CBUAE FX rate must be finite and positive")
        currency = CURRENCY_NAMES.get(row[1])
        if currency in CURRENCIES:
            rates[currency] = value
    if not set(REQUIRED_CURRENCIES).issubset(rates):
        raise ValueError("CBUAE FX lacks required USD/INR coverage")
    result = {}
    for currency, value in sorted(rates.items()):
        name = f"CBUAEFX_{currency}"
        result[name] = Series(
            mnemonic=name, source=SOURCE, remote_id=remote_id(currency),
            label=f"AED per {currency} (CBUAE daily VAT reference rate)",
            unit=f"AED/{currency}", freq="D", fetched_at=captured.isoformat(timespec="seconds"),
            points=pd.Series([value], index=pd.DatetimeIndex([updated.date()]), dtype=float),
        )
    return result, updated


def parse_html(payload: bytes, *, fetched_at: str, source_url: str = SOURCE_URL) -> dict[str, Series]:
    """Validate all table rows before exposing supported currency observations."""
    return _parse(payload, fetched_at, source_url)[0]


async def _download(client: httpx.AsyncClient) -> bytes:
    async with asyncio.timeout(DOWNLOAD_DEADLINE_SECONDS), client.stream(
        "GET", SOURCE_URL, headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        timeout=30, follow_redirects=False,
    ) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("CBUAE FX requires a complete HTTP 200 response")
        declared = response.headers.get("content-length")
        if declared is not None and not 0 < int(declared) <= MAX_BODY_BYTES:
            raise ValueError("CBUAE FX content length outside permitted bounds")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise ValueError("CBUAE FX response exceeds byte limit")
            body.extend(chunk)
        return bytes(body)


def _load_cache() -> dict[str, Series]:
    manifest = store.load_blob(LATEST_CAPTURE_KEY)
    if not isinstance(manifest, dict):
        return {}
    required = {
        "schema": "seiche.cbuae-fx-capture.v1", "source": SOURCE, "source_url": SOURCE_URL,
        "terms_url": TERMS_URL, "quote_currency": "AED", "source_publication_time": None,
        "observation_date_basis": "publisher_table_last_updated",
    }
    if any(manifest.get(key) != value for key, value in required.items()):
        return {}
    names = manifest.get("series")
    if (
        not isinstance(names, list) or not names
        or any(not isinstance(name, str) for name in names) or len(names) != len(set(names))
        or not set(names).issubset({f"CBUAEFX_{c}" for c in CURRENCIES})
        or not {f"CBUAEFX_{c}" for c in REQUIRED_CURRENCIES}.issubset(names)
        or not isinstance(manifest.get("evidence_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest["evidence_sha256"])
    ):
        return {}
    try:
        captured = datetime.fromisoformat(manifest["fetched_at"])
        updated = datetime.fromisoformat(manifest["publisher_updated_at"])
        last = date.fromisoformat(manifest["last_observation_date"])
        if (
            captured.tzinfo is None or updated.tzinfo is None
            or captured > datetime.fromisoformat(utcnow_iso())
            or updated > captured or last != updated.astimezone(_DUBAI).date()
            or last < date(2018, 1, 1)
        ):
            return {}
    except (KeyError, TypeError, ValueError):
        return {}
    capture_key = f"{CAPTURE_PREFIX}{manifest['evidence_sha256']}:{manifest['fetched_at']}"
    if store.load_blob(capture_key) != manifest:
        return {}
    values = manifest.get("latest_values")
    if not isinstance(values, dict) or set(values) != set(names):
        return {}
    result = {}
    for name in names:
        item = store.load_series(name)
        currency = name.removeprefix("CBUAEFX_")
        if (
            item is None or item.mnemonic != name or item.source != SOURCE
            or item.remote_id != remote_id(currency) or item.unit != f"AED/{currency}"
            or item.freq != "D" or item.points.empty or item.asof != last.isoformat()
            or item.fetched_at != manifest["fetched_at"]
            or not all(math.isfinite(float(v)) and float(v) > 0 for v in item.points)
            or float(item.points.iloc[-1]) != values[name]
        ):
            return {}
        result[name] = item
    return result


def _persist(series: dict[str, Series], updated: datetime, capture: RawCapture,
             raw_root: Path | None, prior_latest: object) -> None:
    raw_path = FileRawCaptureSink(raw_root or store.DATA_DIR / "raw").write(capture)
    metadata = {
        "schema": "seiche.cbuae-fx-capture.v1", "source": SOURCE, "source_url": SOURCE_URL,
        "dataset_url": DATASET_URL, "terms_url": TERMS_URL,
        "fetched_at": capture.captured_at.isoformat(timespec="seconds"),
        "publisher_updated_at": updated.isoformat(timespec="seconds"),
        "publisher_timezone_basis": "Asia/Dubai assumed from publisher locality",
        "source_publication_time": None,
        "observation_date_basis": "publisher_table_last_updated",
        "last_observation_date": updated.date().isoformat(),
        "evidence_sha256": capture.evidence_hash, "raw_path": raw_path,
        "byte_count": len(capture.payload), "series": sorted(series),
        "latest_values": {name: float(item.points.iloc[-1]) for name, item in series.items()},
        "quote_currency": "AED", "quote_convention": "AED per one foreign currency unit",
        "evidence_class": "observed", "usage": "Daily VAT reference context; not executable quotes.",
        "rights_basis": "CBUAE Open Data Policy: attribution and direct dataset link",
        "attribution": "Source: Central Bank of the UAE. © Central Bank of the UAE.",
    }
    capture_key = f"{CAPTURE_PREFIX}{capture.evidence_hash}:{metadata['fetched_at']}"
    previous = store.load_blob(capture_key)
    if previous is not None and previous != metadata:
        raise ValueError("CBUAE FX capture metadata conflicts with immutable identity")
    blobs = {LATEST_CAPTURE_KEY: metadata}
    if previous is None:
        blobs[capture_key] = metadata
    store.save_series_batch(series.values(), blobs=blobs, expected_blobs={LATEST_CAPTURE_KEY: prior_latest})


def read_references(*, now: datetime) -> dict:
    """Read three matching-date corridor references without network access.

    Freshness is a conservative calendar-age indicator because the source does
    not provide a verified holiday/release calendar. It is not a release SLA.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("CBUAE FX evaluation time must include a timezone")
    pairs = (("USD", "AED"), ("AED", "INR"), ("USD", "INR"))
    rows = [{
        "pair": f"{base}/{quote}", "provider": "cbuae", "base_currency": base,
        "quote_currency": quote, "value": None, "unit": f"{quote} per {base}",
        "as_of": None, "status": "unavailable", "sources": [], "source_url": DATASET_URL,
        "evidence_status": "unavailable", "reference_only": True,
        "reason": "No validated matching-date CBUAE VAT reference generation is available.",
    } for base, quote in pairs]
    try:
        manifest = store.load_blob(LATEST_CAPTURE_KEY)
        series = _load_cache()
        if not series or not isinstance(manifest, dict) or store.load_blob(LATEST_CAPTURE_KEY) != manifest:
            return {"rows": rows, "capture": None}
        if datetime.fromisoformat(manifest["fetched_at"]) > now:
            return {"rows": rows, "capture": None}
        observed = date.fromisoformat(manifest["last_observation_date"])
        age = (now.astimezone(_DUBAI).date() - observed).days
        if age < 0 or any(item.fetched_at != manifest["fetched_at"] for item in series.values()):
            return {"rows": rows, "capture": None}
        freshness = cbuae_fx_freshness(observed.isoformat(), now=now)
        for row, (base, quote) in zip(rows, pairs):
            legs = [currency for currency in (base, quote) if currency != "AED"]
            inputs = [series[f"CBUAEFX_{currency}"] for currency in legs]
            if any(item.asof != observed.isoformat() for item in inputs):
                continue
            base_value = 1.0 if base == "AED" else float(series[f"CBUAEFX_{base}"].points.iloc[-1])
            quote_value = 1.0 if quote == "AED" else float(series[f"CBUAEFX_{quote}"].points.iloc[-1])
            value = base_value / quote_value
            if not math.isfinite(value) or value <= 0:
                continue
            row.update(
                value=value, as_of=observed.isoformat(), status=freshness["staleness"], age_days=age,
                evidence_status="observed" if quote == "AED" else "derived",
                reason=None, fetched_at=manifest["fetched_at"],
                freshness_basis=freshness["freshness_basis"],
                freshness_policy=freshness["freshness_policy"],
                publication_schedule=freshness["publication_schedule"],
                observation_date_basis=manifest["observation_date_basis"],
                calculation="AED per base divided by AED per quote; AED identity is 1.",
                sources=[{
                    "mnemonic": item.mnemonic, "source": SOURCE, "source_url": DATASET_URL,
                    "remote_id": item.remote_id, "as_of": item.asof,
                    "fetched_at": item.fetched_at, "value": float(item.points.iloc[-1]), "unit": item.unit,
                    "evidence_sha256": manifest["evidence_sha256"], "terms_url": TERMS_URL,
                } for item in inputs],
            )
        # Public metadata intentionally excludes the private raw-archive path.
        capture = {key: value for key, value in manifest.items() if key not in {"raw_path", "latest_values"}}
        return {"rows": rows, "capture": capture}
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error):
        return {"rows": rows, "capture": None}


async def fetch(client: httpx.AsyncClient, faults: list[dict] | None = None, *,
                force: bool = False, raw_root: Path | None = None) -> dict[str, Series]:
    """Refresh at most every six hours; preserve original clocks on failure.

    One whole generation is committed atomically, retaining daily history and
    same-day vintages. Missing optional currencies are never forward-filled.
    """
    cached = await run_store(_load_cache)
    if not force and cached and await run_store(
        lambda: all(store.is_fresh(name, TTL_MINUTES) for name in cached)
    ):
        return cached
    prior_latest = await run_store(store.load_blob, LATEST_CAPTURE_KEY)
    try:
        payload = await _download(client)
        fetched_at = utcnow_iso()
        parsed, updated = _parse(payload, fetched_at, SOURCE_URL)
        if isinstance(prior_latest, dict) and prior_latest.get("publisher_updated_at"):
            if updated < datetime.fromisoformat(prior_latest["publisher_updated_at"]):
                raise ValueError("CBUAE FX publisher update regressed")
        capture = RawCapture(
            market_id="GLOBAL-FX", adapter_id=SOURCE,
            captured_at=datetime.fromisoformat(fetched_at), source_uri=SOURCE_URL,
            media_type="text/html", payload=payload, evidence_hash=hashlib.sha256(payload).hexdigest(),
        )
        await run_store(_persist, parsed, updated, capture, raw_root, prior_latest)
        completed = await run_store(_load_cache)
        if not completed:
            raise ValueError("CBUAE FX committed generation failed validation")
        return completed
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if faults is not None:
            faults.append({"source": SOURCE, "detail": detail})
            current = await run_store(_load_cache)
            if current:
                return current
        raise SourceFault(SOURCE, detail) from exc
