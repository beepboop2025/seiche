"""OFR Short-Term Funding Monitor collector (keyless JSON).

Repo volumes by segment (DVP = where the hedge fund RV complex funds itself),
and MMF holdings by counterparty (concentration of the cash-provision side).
"""

from __future__ import annotations

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, USER_AGENT, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://data.financialresearch.gov/v1"


async def fetch_series(client: httpx.AsyncClient, spec: SeriesSpec, start: str = "2018-01-01") -> Series:
    if store.is_fresh(spec.mnemonic, spec.ttl_minutes):
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
    try:
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
        store.save_series(s)
        return s
    except Exception as exc:
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
        raise SourceFault("ofr", f"{spec.remote_id}: {type(exc).__name__}: {exc}") from exc


async def fetch_many(
    client: httpx.AsyncClient, mnemonics: list[str], faults: list[dict] | None = None
) -> dict[str, Series]:
    """Concurrent fetch with per-series fault isolation."""
    import asyncio

    out: dict[str, Series] = {}

    async def one(m: str):
        try:
            out[m] = await fetch_series(client, ALL_SERIES[m])
        except SourceFault as e:
            if faults is not None:
                faults.append({"source": e.source, "detail": e.detail})

    await asyncio.gather(*(one(m) for m in mnemonics))
    return out
