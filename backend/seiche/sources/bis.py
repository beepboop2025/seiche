"""BIS Data Portal collector (keyless SDMX-CSV).

The Bank for International Settlements publishes the global-liquidity
aggregates (offshore dollar credit, credit-to-GDP gaps) through its Data
Portal API — keyless, CSV, verified live 2026-07-10. These series are
quarterly and arrive roughly two quarters after the reference period BY
DESIGN (freq "QL" carries the wider staleness grace so a current print is
not misread as a dead collector).

remote_id format: "{dataflow}/{version}/{series_key}", e.g.
"WS_GLI/1.0/Q.USD.3P.N.A.I.B.USD". The DBnomics mirror of the same data ran
~4 quarters behind BIS direct on probe day — BIS is primary and there is no
mirror fallback: stale-but-served would be worse than loud absence.
"""

from __future__ import annotations

import csv
import io

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, USER_AGENT, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://stats.bis.org/api/v2/data/dataflow/BIS"


def parse_sdmx_csv(text: str) -> pd.Series:
    """SDMX-CSV -> points. TIME_PERIOD is quarterly ('2025-Q4'); the
    observation is stamped on the QUARTER END date (the reference period's
    last day — the publication lag is carried by the QL staleness class,
    not faked into the date)."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty SDMX-CSV payload")
    idx, vals = [], []
    for r in rows:
        period = (r.get("TIME_PERIOD") or "").strip()
        val = (r.get("OBS_VALUE") or "").strip()
        if not period or not val:
            continue
        idx.append(pd.Period(period.replace("-Q", "Q"), freq="Q").end_time.normalize())
        vals.append(float(val))
    if not idx:
        raise ValueError("no observations in SDMX-CSV payload")
    pts = pd.Series(vals, index=pd.DatetimeIndex(idx), dtype=float).sort_index()
    return pts[~pts.index.duplicated(keep="last")]


async def fetch_series(client: httpx.AsyncClient, spec: SeriesSpec) -> Series:
    if store.is_fresh(spec.mnemonic, spec.ttl_minutes):
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
    try:
        r = await client.get(
            f"{BASE}/{spec.remote_id}",
            params={"format": "csv", "startPeriod": spec.start},
            headers={"User-Agent": USER_AGENT},
            timeout=45,
        )
        r.raise_for_status()
        pts = parse_sdmx_csv(r.text)
        s = Series(
            spec.mnemonic, "bis", spec.remote_id, spec.label, spec.unit,
            spec.freq, utcnow_iso(), pts,
        )
        store.save_series(s)
        return s
    except Exception as exc:
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
        raise SourceFault("bis", f"{spec.remote_id}: {type(exc).__name__}: {exc}") from exc


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
