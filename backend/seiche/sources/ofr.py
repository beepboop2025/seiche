"""OFR Short-Term Funding Monitor collector (keyless JSON).

Repo volumes by segment (DVP = where the hedge fund RV complex funds itself),
and MMF holdings by counterparty (concentration of the cash-provision side).
"""

from __future__ import annotations

import logging

import httpx
import pandas as pd

from seiche import store
from seiche.sources._async_store import run_store
from seiche.config import ALL_SERIES, USER_AGENT, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://data.financialresearch.gov/v1"


async def fetch_series(
    client: httpx.AsyncClient, spec: SeriesSpec, start: str = "2018-01-01",
    *, faults: list[dict] | None = None,
) -> Series:
    try:
        if await run_store(store.is_fresh, spec.mnemonic, spec.ttl_minutes):
            cached = await run_store(store.load_series, spec.mnemonic)
            if cached is not None:
                return cached
        r = await client.get(
            f"{BASE}/series/timeseries",
            params={"mnemonic": spec.remote_id, "start_date": start},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        r.raise_for_status()
        rows = r.json()  # [["YYYY-MM-DD", value|null], ...]
        idx = pd.DatetimeIndex([row[0] for row in rows])
        vals = [row[1] for row in rows]
        pts = pd.Series(vals, index=idx, dtype=float)
        s = Series(
            spec.mnemonic, "ofr", spec.remote_id, spec.label, spec.unit,
            spec.freq, utcnow_iso(), pts,
        )
        await run_store(store.save_series, s)
        return s
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Source refresh failed: series=%s error_type=%s",
            spec.mnemonic, type(exc).__name__,
        )
        try:
            cached = await run_store(store.load_series, spec.mnemonic)
        except Exception as cache_error:
            logging.getLogger(__name__).warning(
                "Source cache unavailable: series=%s error_type=%s",
                spec.mnemonic, type(cache_error).__name__,
            )
            cached = None
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        detail = f"{spec.remote_id}: {f'HTTP {status}' if status else type(exc).__name__}"
        if faults is not None:
            faults.append({
                "source": "ofr", "series": spec.mnemonic, "detail": detail,
                "http_status": status, "refresh_status": "failed",
                "cache_used": cached is not None,
            })
        if cached is not None:
            return cached
        raise SourceFault("ofr", detail) from exc


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
            pass  # fetch_series recorded the failed refresh, including cached fallbacks.
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Series collection failed: series=%s error_type=%s", m, type(exc).__name__,
            )
            if faults is not None:
                faults.append({
                    "source": "ofr", "series": m, "detail": type(exc).__name__,
                    "refresh_status": "failed", "cache_used": False,
                })

    await asyncio.gather(*(one(m) for m in mnemonics))
    return out
