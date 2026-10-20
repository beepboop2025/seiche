"""FRED collector via the keyless fredgraph.csv endpoint (single series per
request returns plain CSV; no API key required).

Also provides IORB_SPLICED: IOER (ends 2021-07) spliced with IORB so engines
that need administered-rate history back to 2018 get one continuous series.
"""

from __future__ import annotations

import asyncio
import io
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
import pandas as pd

from seiche import store
from seiche.sources._async_store import run_store
from seiche.config import ALL_SERIES, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso
from seiche.sources.publication import H41_WEEKLY_REMOTE_IDS, publication_refresh_due

BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# fredgraph.csv throttles bursts hard — keep concurrency low and back off.
_sem = asyncio.Semaphore(2)
_MAX_ATTEMPTS = 4
_MAX_RETRY_WAIT_SECONDS = 30.0


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in {408, 425, 429} or 500 <= status < 600
    return False


def _retry_after_seconds(exc: Exception) -> float | None:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    value = exc.response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    if value.isascii() and value.isdecimal():
        # Avoid oversized integer conversion while still deferring a valid
        # provider deadline that cannot fit within this refresh's wait budget.
        if len(value) > 12:
            return _MAX_RETRY_WAIT_SECONDS + 1
        return float(value)
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            return None
        return max(0.0, (deadline - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def _refresh_failure(
    spec: SeriesSpec, exc: Exception, *, cache_used: bool, retry_deferred: bool,
) -> dict:
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    if status in {401, 403, 451}:
        reason = "upstream_access_denied"
    elif status == 429:
        reason = "upstream_rate_limited"
    elif _retryable(exc):
        reason = "upstream_unavailable"
    else:
        reason = "source_refresh_failed"
    # An upstream response body or exception URL is not public diagnostic text.
    detail = f"{spec.remote_id}: {reason} ({f'HTTP {status}' if status else type(exc).__name__})"
    if retry_deferred:
        detail += "; retry delay exceeds this refresh's wait budget"
    if cache_used:
        detail += "; cached observations retained with unchanged observation and retrieval clocks"
    return {
        "source": "fred", "series": spec.mnemonic, "detail": detail,
        "reason": reason, "http_status": status, "refresh_status": "failed",
        "cache_used": cache_used, "retry_deferred": retry_deferred,
    }


async def fetch_series(
    client: httpx.AsyncClient, spec: SeriesSpec, start: str | None = None,
    *, faults: list[dict] | None = None,
) -> Series:
    if await run_store(store.is_fresh, spec.mnemonic, spec.ttl_minutes):
        cached = await run_store(store.load_series, spec.mnemonic)
        if cached is not None:
            scheduled_weekly = spec.freq == "W" and spec.remote_id in H41_WEEKLY_REMOTE_IDS
            if not scheduled_weekly or not publication_refresh_due(
                spec.source, spec.remote_id, spec.freq, cached.asof,
                cached.fetched_at, now=datetime.now(UTC),
            ):
                return cached
    retry_deferred = False
    try:
        r = None
        waited = 0.0
        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with _sem:
                    # No custom User-Agent here — see FRED note in config.py.
                    r = await client.get(
                        BASE,
                        params={"id": spec.remote_id, "cosd": start or spec.start},
                        timeout=45,
                    )
                r.raise_for_status()
                break
            except Exception as exc:
                r = None
                if not _retryable(exc) or attempt == _MAX_ATTEMPTS - 1:
                    raise
                delay = max(
                    1.5 * (2 ** attempt) + random.random(),
                    _retry_after_seconds(exc) or 0.0,
                )
                if delay > _MAX_RETRY_WAIT_SECONDS - waited:
                    retry_deferred = True
                    raise
                await asyncio.sleep(delay)
                waited += delay
        assert r is not None
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        pts = pd.Series(df["value"].values, index=pd.DatetimeIndex(df["date"]), dtype=float)
        s = Series(
            spec.mnemonic, "fred", spec.remote_id, spec.label, spec.unit,
            spec.freq, utcnow_iso(), pts,
        )
        await run_store(store.save_series, s)
        return s
    except Exception as exc:
        cached = await run_store(store.load_series, spec.mnemonic)
        fault = _refresh_failure(
            spec, exc, cache_used=cached is not None, retry_deferred=retry_deferred,
        )
        if faults is not None:
            faults.append(fault)
        if cached is not None:
            return cached
        raise SourceFault("fred", fault["detail"]) from exc


async def fetch_many(
    client: httpx.AsyncClient, mnemonics: list[str], faults: list[dict] | None = None
) -> dict[str, Series]:
    """Concurrent fetch with per-series fault isolation."""
    import asyncio

    out: dict[str, Series] = {}

    async def one(m: str):
        try:
            out[m] = await fetch_series(client, ALL_SERIES[m], faults=faults)
        except SourceFault:
            # fetch_series records both cold failures and retained-cache
            # failures once, keeping failed refreshes visible to the assembler.
            pass

    await asyncio.gather(*(one(m) for m in mnemonics))
    return out


def splice_iorb(iorb: Series, ioer: Series) -> Series:
    """One continuous administered-rate series: IOER through 2021-07, IORB after.

    Degrades to whichever leg exists — a Time Machine replay truncated before
    2021-07 has no IORB observations at all."""
    new = iorb.points.dropna()
    old = ioer.points.dropna()
    if new.empty:
        pts = old
    else:
        pts = pd.concat([old[old.index < new.index.min()], new]).sort_index()
    return Series(
        "IORB_SPLICED", "fred", "IOER+IORB", "Administered rate (IOER/IORB spliced)",
        "%", "D", iorb.fetched_at, pts,
    )
