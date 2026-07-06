"""ECB Data Portal collector (keyless CSV via SDMX data API).

One series for now: €STR, the euro basin's overnight anchor rate. The portal
serves `format=csvdata` with TIME_PERIOD/OBS_VALUE columns — same envelope
discipline as every other collector (verified live 2026-07-07).
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, USER_AGENT, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://data-api.ecb.europa.eu/service/data"


async def fetch_series(client: httpx.AsyncClient, spec: SeriesSpec, start: str = "2019-10-01") -> Series:
    if store.is_fresh(spec.mnemonic, spec.ttl_minutes):
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
    try:
        r = await client.get(
            f"{BASE}/{spec.remote_id}",
            params={"format": "csvdata", "startPeriod": start},
            headers={"User-Agent": USER_AGENT},
            timeout=45,
        )
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
            raise ValueError(f"unexpected CSV shape: {list(df.columns)[:6]}")
        pts = pd.Series(
            pd.to_numeric(df["OBS_VALUE"], errors="coerce").values,
            index=pd.DatetimeIndex(df["TIME_PERIOD"]),
            dtype=float,
        ).sort_index()
        s = Series(
            spec.mnemonic, "ecb", spec.remote_id, spec.label, spec.unit,
            spec.freq, utcnow_iso(), pts,
        )
        store.save_series(s)
        return s
    except Exception as exc:
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
        raise SourceFault("ecb", f"{spec.remote_id}: {type(exc).__name__}: {exc}") from exc


async def fetch_many(
    client: httpx.AsyncClient, mnemonics: list[str], faults: list[dict] | None = None
) -> dict[str, Series]:
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
