"""CFETS chinamoney collector (SHIBOR benchmark fixings, keyless JSON).

The English portal serves SHIBOR history as JSON with no key, and accepts
Seiche's honest User-Agent (verified live 2026-07-13). Two hard edges,
observed the same day: the endpoint answers at most ~one month of history
per request, and it burst-throttles — rapid consecutive hits return EMPTY
bodies, not errors. So this collector makes exactly one request per refresh
(the trailing CHINAMONEY_WINDOW_D days) and upserts the points over the
accrued store history, Palimpsest-style: the local record grows past the
API's retention window instead of pretending the window is the history.

An empty body is a throttle, not "no data today" — parse_records fails loud
on it so the fault surfaces and the cached history is served instead.
"""

from __future__ import annotations

import asyncio

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, CHINAMONEY_WINDOW_D, USER_AGENT, SeriesSpec
from seiche.sources.base import Series, SourceFault, utcnow_iso

BASE = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis"
REFERER = "https://www.chinamoney.com.cn/english/bmkshibor/"


def parse_records(payload: dict, tenor: str) -> pd.Series:
    """Pure parse: ShiborHis records -> daily series for one tenor."""
    recs = payload.get("records")
    if not isinstance(recs, list) or not recs:
        raise ValueError("no records in payload (empty body = throttled, not zero)")
    rows: list[tuple[pd.Timestamp, float]] = []
    for r in recs:
        d, v = r.get("showDateCN"), r.get(tenor)
        if d is None or v in (None, ""):
            continue
        rows.append((pd.Timestamp(str(d)[:10]), float(v)))
    if not rows:
        raise ValueError(f"records carry no '{tenor}' values")
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
    tenor = spec.remote_id.split(":", 1)[1]
    try:
        end = pd.Timestamp.now(tz="UTC").date()
        start = end - pd.Timedelta(days=CHINAMONEY_WINDOW_D)
        r = await client.get(
            BASE,
            params={"lang": "en", "startDate": start.isoformat(), "endDate": end.isoformat()},
            headers={"User-Agent": USER_AGENT, "Referer": REFERER},
            timeout=45,
        )
        r.raise_for_status()
        fresh = parse_records(r.json(), tenor)
        prior = store.load_series(spec.mnemonic)
        if prior is not None and not prior.points.empty:
            merged = pd.concat([prior.points, fresh])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = fresh
        s = Series(
            spec.mnemonic, "chinamoney", spec.remote_id, spec.label, spec.unit,
            spec.freq, utcnow_iso(), merged,
        )
        store.save_series(s)
        return s
    except Exception as exc:
        cached = store.load_series(spec.mnemonic)
        if cached is not None:
            return cached
        raise SourceFault("chinamoney", f"{spec.remote_id}: {type(exc).__name__}: {exc}") from exc


async def fetch_many(
    client: httpx.AsyncClient, mnemonics: list[str], faults: list[dict] | None = None
) -> dict[str, Series]:
    # Sequential on purpose: the endpoint burst-throttles concurrent hits.
    out: dict[str, Series] = {}
    for i, m in enumerate(mnemonics):
        if i:
            await asyncio.sleep(2.0)
        try:
            out[m] = await fetch_series(client, ALL_SERIES[m])
        except SourceFault as e:
            if faults is not None:
                faults.append({"source": e.source, "detail": e.detail})
    return out
