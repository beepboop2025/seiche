"""Bank of Japan collector (keyless CSV from the stat-search flat files).

The BOJ time-series portal serves its "Main Time-series Statistics" as plain
CSV at stable URLs, keyless, refreshed ~09:00 JST daily (verified live
2026-07-13, found by the OpenManus mm-japan sweep the same day). One series
for now: TONA, the uncollateralized overnight call rate — Japan's overnight
anchor, daily back to 1998. This supersedes the OECD MEI monthly mirror
(CALL_JP), which runs ~2 months late; on discovery day the monthly mirror
still showed 0.727% against TONA's 0.978%.

Format: ~9 metadata lines ("Series code", "Unit", ...) then YYYY/MM/DD,value
rows, with NA on non-trading days. The parser keys on the date shape rather
than line position so metadata drift doesn't break it.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, USER_AGENT, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://www.stat-search.boj.or.jp/ssi/mtshtml/csv"

_ROW = re.compile(r"^(\d{4}/\d{2}/\d{2}),(.+)$")


def parse_csv(text: str) -> pd.Series:
    """BOJ flat-file CSV -> daily series. Fails loud on a payload with no
    data rows (an error page must not read as 'no data')."""
    rows: list[tuple[pd.Timestamp, float]] = []
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        raw = m.group(2).strip().strip('"')
        if raw in ("NA", ""):
            continue
        try:
            rows.append((pd.Timestamp(m.group(1).replace("/", "-")), float(raw)))
        except ValueError:
            continue
    if not rows:
        raise ValueError("no data rows in BOJ CSV (error page or format change)")
    s = pd.Series(
        [v for _, v in rows],
        index=pd.DatetimeIndex([d for d, _ in rows]),
        dtype=float,
    )
    return s[~s.index.duplicated(keep="last")].sort_index()


async def fetch_series(client: httpx.AsyncClient, spec: SeriesSpec) -> Series:
    if store.is_fresh(spec.mnemonic, spec.ttl_minutes):
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
    filename = spec.remote_id.split(":", 1)[0]
    try:
        r = await client.get(
            f"{BASE}/{filename}",
            headers={"User-Agent": USER_AGENT},
            timeout=45,
        )
        r.raise_for_status()
        pts = parse_csv(r.text)
        s = Series(
            spec.mnemonic, "boj", spec.remote_id, spec.label, spec.unit,
            spec.freq, utcnow_iso(), pts,
        )
        store.save_series(s)
        return s
    except Exception as exc:
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
        raise SourceFault("boj", f"{spec.remote_id}: {type(exc).__name__}: {exc}") from exc


async def fetch_many(
    client: httpx.AsyncClient, mnemonics: list[str], faults: list[dict] | None = None
) -> dict[str, Series]:
    out: dict[str, Series] = {}

    async def one(m: str):
        try:
            out[m] = await fetch_series(client, ALL_SERIES[m])
        except SourceFault as e:
            if faults is not None:
                faults.append({"source": e.source, "detail": e.detail})

    await asyncio.gather(*(one(m) for m in mnemonics))
    return out
