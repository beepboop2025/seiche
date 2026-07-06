"""Crypto collectors — DeFiLlama stablecoins + Coinbase Exchange candles.

Why crypto belongs in a funding terminal: the large stablecoins are money
market funds in all but name (USDT/USDC hold $200B+ of T-bills between them),
a peg deviation is a dollar-funding event in the offshore-crypto basin, and
crypto is the only dollar market that trades on weekends — a free 24/7 canary
for Monday-morning stress. Both APIs are keyless (verified 2026-07-07).

Provenance discipline unchanged: candle series live in the same SQLite store
and Series envelope as FRED data; DeFiLlama current pegs are point-in-time
spot values and are labeled as such (no history claimed where none exists).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from seiche import store
from seiche.config import (
    ALL_SERIES,
    CRYPTO_CANDLE_YEARS,
    CRYPTO_TTL_MIN,
    STABLE_TOP_N,
    USER_AGENT,
)
from seiche.sources.base import Series, SourceFault, utcnow_iso

COINBASE = "https://api.exchange.coinbase.com"
LLAMA = "https://stablecoins.llama.fi"

_sem = asyncio.Semaphore(3)  # Coinbase public rate limit is generous but finite


async def _candles_page(client: httpx.AsyncClient, product: str, start: datetime, end: datetime) -> list:
    async with _sem:
        r = await client.get(
            f"{COINBASE}/products/{product}/candles",
            params={
                "granularity": 86400,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
    r.raise_for_status()
    return r.json()  # rows: [time, low, high, open, close, volume], newest first


async def fetch_candles(client: httpx.AsyncClient, product: str) -> Series:
    """Daily closes for a Coinbase product, paginated back CRYPTO_CANDLE_YEARS.

    Mnemonic = product with '-' -> '_' (BTC-USD -> BTC_USD), registered in
    config.CRYPTO_SERIES so staleness/provenance work like any other series.
    """
    mnemonic = product.replace("-", "_")
    spec = ALL_SERIES[mnemonic]
    if store.is_fresh(mnemonic, spec.ttl_minutes):
        cached = store.load_series(mnemonic)
        if cached is not None:
            return cached
    try:
        now = datetime.now(timezone.utc)
        rows: list = []
        # 300 daily candles per page, newest window first.
        for page in range(int(CRYPTO_CANDLE_YEARS * 365 / 300) + 1):
            end = now - timedelta(days=300 * page)
            start = end - timedelta(days=300)
            batch = await _candles_page(client, product, start, end)
            if not batch:
                break
            rows.extend(batch)
        if not rows:
            raise ValueError("no candles returned")
        idx = pd.DatetimeIndex(
            [datetime.fromtimestamp(r[0], tz=timezone.utc).date().isoformat() for r in rows]
        )
        pts = pd.Series([float(r[4]) for r in rows], index=idx, dtype=float)
        pts = pts[~pts.index.duplicated(keep="first")].sort_index()
        s = Series(mnemonic, "crypto", spec.remote_id, spec.label, spec.unit,
                   spec.freq, utcnow_iso(), pts)
        store.save_series(s)
        return s
    except Exception as exc:
        cached = store.load_series(mnemonic)
        if cached is not None:
            return cached
        raise SourceFault("crypto", f"{product}: {type(exc).__name__}: {exc}") from exc


async def fetch_stablecoins(client: httpx.AsyncClient) -> dict:
    """Current peg board (top N by circulation) + daily total-circulation
    history (DeFiLlama, ~8y). The total also lands in the store as
    STABLE_TOTAL so SONAR and provenance see it."""
    key = "llama_stablecoins"
    cached = store.load_blob(key, CRYPTO_TTL_MIN)
    if cached is None:
        try:
            r = await client.get(f"{LLAMA}/stablecoins", params={"includePrices": "true"},
                                 headers={"User-Agent": USER_AGENT}, timeout=30)
            r.raise_for_status()
            assets = r.json().get("peggedAssets", [])
            usd_pegged = [
                a for a in assets
                if a.get("pegType") == "peggedUSD" and (a.get("circulating") or {}).get("peggedUSD")
            ]
            usd_pegged.sort(key=lambda a: -(a["circulating"]["peggedUSD"] or 0))
            board = [
                {
                    "symbol": a.get("symbol"),
                    "name": a.get("name"),
                    "circulating_b": round(float(a["circulating"]["peggedUSD"]) / 1e9, 1),
                    "price": float(a["price"]) if a.get("price") is not None else None,
                }
                for a in usd_pegged[:STABLE_TOP_N]
            ]
            r2 = await client.get(f"{LLAMA}/stablecoincharts/all",
                                  headers={"User-Agent": USER_AGENT}, timeout=30)
            r2.raise_for_status()
            hist_rows = [
                [int(row["date"]), float((row.get("totalCirculatingUSD") or {}).get("peggedUSD") or 0) / 1e9]
                for row in r2.json()
                if row.get("date")
            ]
            cached = {"fetched_at": utcnow_iso(), "board": board, "total_hist": hist_rows}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("crypto", f"defillama: {type(exc).__name__}: {exc}") from exc

    idx = pd.DatetimeIndex(
        [datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() for ts, _ in cached["total_hist"]]
    )
    total = pd.Series([v for _, v in cached["total_hist"]], index=idx, dtype=float)
    total = total[~total.index.duplicated(keep="last")].sort_index()
    spec = ALL_SERIES["STABLE_TOTAL"]
    store.save_series(Series("STABLE_TOTAL", "crypto", spec.remote_id, spec.label,
                             spec.unit, spec.freq, cached["fetched_at"], total))
    return {"fetched_at": cached["fetched_at"], "board": cached["board"], "total": total}


async def fetch_all(client: httpx.AsyncClient, products: list[str], faults: list[dict] | None = None) -> dict:
    """Candles for every product + the stablecoin board, fault-isolated."""
    out: dict = {"candles": {}}

    async def one(p: str):
        try:
            out["candles"][p.replace("-", "_")] = await fetch_candles(client, p)
        except SourceFault as e:
            if faults is not None:
                faults.append({"source": e.source, "detail": e.detail})

    async def stables():
        try:
            out["stable"] = await fetch_stablecoins(client)
        except SourceFault as e:
            if faults is not None:
                faults.append({"source": e.source, "detail": e.detail})

    await asyncio.gather(*(one(p) for p in products), stables())
    out["fetched_at"] = utcnow_iso()
    return out
