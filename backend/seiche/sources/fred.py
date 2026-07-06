"""FRED collector via the keyless fredgraph.csv endpoint (single series per
request returns plain CSV; no API key required).

Also provides IORB_SPLICED: IOER (ends 2021-07) spliced with IORB so engines
that need administered-rate history back to 2018 get one continuous series.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, USER_AGENT, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"


async def fetch_series(client: httpx.AsyncClient, spec: SeriesSpec, start: str = "2017-01-01") -> Series:
    if store.is_fresh(spec.mnemonic, spec.ttl_minutes):
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
    try:
        r = None
        last_exc: Exception | None = None
        for attempt in range(3):  # fredgraph.csv is occasionally slow — retry
            try:
                r = await client.get(
                    BASE,
                    params={"id": spec.remote_id, "cosd": start},
                    headers={"User-Agent": USER_AGENT},
                    timeout=60,
                )
                r.raise_for_status()
                break
            except Exception as exc:
                last_exc = exc
                r = None
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
    """One continuous administered-rate series: IOER through 2021-07, IORB after."""
    cutover = iorb.points.dropna().index.min()
    old = ioer.points.dropna()
    old = old[old.index < cutover]
    pts = pd.concat([old, iorb.points.dropna()]).sort_index()
    return Series(
        "IORB_SPLICED", "fred", "IOER+IORB", "Administered rate (IOER/IORB spliced)",
        "%", "D", iorb.fetched_at, pts,
    )
