"""FRED collector via the keyless fredgraph.csv endpoint (single series per
request returns plain CSV; no API key required).

Also provides IORB_SPLICED: IOER (ends 2021-07) spliced with IORB so engines
that need administered-rate history back to 2018 get one continuous series.
"""

from __future__ import annotations

import asyncio
import io
import random

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# fredgraph.csv throttles bursts hard — keep concurrency low and back off.
_sem = asyncio.Semaphore(2)


async def fetch_series(client: httpx.AsyncClient, spec: SeriesSpec, start: str | None = None) -> Series:
    if store.is_fresh(spec.mnemonic, spec.ttl_minutes):
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
    try:
        r = None
        last_exc: Exception | None = None
        for attempt in range(4):  # fredgraph.csv is slow and throttles — retry w/ backoff
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
                last_exc = exc
                r = None
                await asyncio.sleep(1.5 * (2 ** attempt) + random.random())
        if r is None:
            raise last_exc  # type: ignore[misc]
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        pts = pd.Series(df["value"].values, index=pd.DatetimeIndex(df["date"]), dtype=float)
        s = Series(
            spec.mnemonic, "fred", spec.remote_id, spec.label, spec.unit,
            spec.freq, utcnow_iso(), pts,
        )
        store.save_series(s)
        return s
    except Exception as exc:  # serve stale on failure, fail-loud via staleness
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
        raise SourceFault("fred", f"{spec.remote_id}: {type(exc).__name__}: {exc}") from exc


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
