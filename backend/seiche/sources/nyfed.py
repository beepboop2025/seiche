"""NY Fed Markets Data API collector (keyless JSON).

Pulls:
- Secured reference rates WITH distribution percentiles (SOFR/TGCR/BGCR):
  the percentile tails are the Tail Seismograph's raw input.
- Repo operation results (SRF/SRP usage): the "confession channel" — banks
  paying the ceiling rate to confess they couldn't fund cheaper elsewhere.
"""

from __future__ import annotations

import httpx
import pandas as pd

from seiche import store
from seiche.config import (
    NYFED_RATES_START,
    NYFED_TTL_MIN,
    PD_POSITION_SERIES,
    PD_TTL_MIN,
    USER_AGENT,
)
from seiche.sources.base import SourceFault, utcnow_iso

BASE = "https://markets.newyorkfed.org/api"


async def _get_json(client: httpx.AsyncClient, path: str) -> dict:
    r = await client.get(f"{BASE}{path}", headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.json()


async def fetch_secured_rates(client: httpx.AsyncClient, start: str = NYFED_RATES_START) -> dict:
    """All secured rates with percentiles, as {rate_type: DataFrame}-shaped dict."""
    key = "nyfed_secured_rates"
    cached = store.load_blob(key, NYFED_TTL_MIN)
    if cached is None:
        try:
            import datetime as _dt
            end = _dt.date.today().isoformat()
            raw = await _get_json(
                client, f"/rates/secured/all/search.json?startDate={start}&endDate={end}"
            )
            cached = {"fetched_at": utcnow_iso(), "refRates": raw.get("refRates", [])}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)  # stale fallback
            if cached is None:
                raise SourceFault("nyfed", f"secured rates: {exc}") from exc
    frames: dict[str, pd.DataFrame] = {}
    df = pd.DataFrame(cached["refRates"])
    if df.empty:
        return {"fetched_at": cached["fetched_at"], "frames": frames}
    df["effectiveDate"] = pd.to_datetime(df["effectiveDate"])
    for rate_type, grp in df.groupby("type"):
        g = grp.set_index("effectiveDate").sort_index()
        cols = [c for c in (
            "percentRate", "percentPercentile1", "percentPercentile25",
            "percentPercentile75", "percentPercentile99", "volumeInBillions",
        ) if c in g.columns]
        # API emits literal 'NA' strings on unpublished percentile days.
        frames[rate_type] = g[cols].apply(pd.to_numeric, errors="coerce")
    return {"fetched_at": cached["fetched_at"], "frames": frames}


async def fetch_srf_ops(client: httpx.AsyncClient, n_ops: int = 900) -> dict:
    """Repo operation results -> daily accepted amounts ($B). Zero-usage days count."""
    key = "nyfed_srf_ops"
    cached = store.load_blob(key, NYFED_TTL_MIN)
    if cached is None:
        try:
            raw = await _get_json(client, f"/rp/repo/all/results/last/{n_ops}.json")
            ops = raw.get("repo", {}).get("operations", [])
            cached = {
                "fetched_at": utcnow_iso(),
                "ops": [
                    {
                        "date": o.get("operationDate"),
                        "accepted": float(o.get("totalAmtAccepted") or 0) / 1e9,
                        "submitted": float(o.get("totalAmtSubmitted") or 0) / 1e9,
                        "method": o.get("operationMethod"),
                    }
                    for o in ops
                ],
            }
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("nyfed", f"repo ops: {exc}") from exc
    df = pd.DataFrame(cached["ops"])
    if df.empty:
        return {"fetched_at": cached["fetched_at"], "daily": pd.Series(dtype=float)}
    df["date"] = pd.to_datetime(df["date"])
    daily = df.groupby("date")[["accepted", "submitted"]].sum().sort_index()
    return {"fetched_at": cached["fetched_at"], "daily": daily}


async def fetch_pd_positions(client: httpx.AsyncClient) -> dict:
    """Primary dealer net outright UST positions by maturity bucket ($M -> $B).

    The no-seriesbreak endpoint returns the full spliced weekly history
    (verified 2026-07-07). This is the dealer 'warehouse': how much duration
    the street already holds when the next auction lands.
    """
    key = "nyfed_pd_positions"
    cached = store.load_blob(key, PD_TTL_MIN)
    if cached is None:
        try:
            out: dict[str, list] = {}
            for keyid in PD_POSITION_SERIES:
                raw = await _get_json(client, f"/pd/get/{keyid}.json")
                rows = raw.get("pd", {}).get("timeseries", [])
                out[keyid] = [
                    [r.get("asofdate"), r.get("value")]
                    for r in rows
                    if r.get("asofdate") and r.get("value") not in (None, "", "*")
                ]
            cached = {"fetched_at": utcnow_iso(), "series": out}
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("nyfed", f"pd positions: {exc}") from exc
    frames = {}
    for keyid, rows in cached["series"].items():
        if not rows:
            continue
        s = pd.Series(
            [float(v) for _, v in rows],
            index=pd.DatetimeIndex([d for d, _ in rows]),
            dtype=float,
        ).sort_index() / 1000.0  # $M -> $B
        frames[keyid] = s[~s.index.duplicated(keep="last")]
    return {"fetched_at": cached["fetched_at"], "positions": frames}


async def fetch_fx_swaps(client: httpx.AsyncClient, n_ops: int = 90) -> dict:
    """USD liquidity swap operations — the GLOBAL confession channel. A
    foreign central bank drawing the swap line means a bank in its
    jurisdiction could not find dollars privately at any acceptable rate."""
    key = "nyfed_fx_swaps"
    cached = store.load_blob(key, NYFED_TTL_MIN)
    if cached is None:
        try:
            raw = await _get_json(client, f"/fxs/usdollar/last/{n_ops}.json")
            ops = raw.get("fxSwaps", {}).get("operations", [])
            cached = {
                "fetched_at": utcnow_iso(),
                "ops": [
                    {
                        "trade_date": o.get("tradeDate"),
                        "counterparty": o.get("counterparty"),
                        "amount_m": float(o.get("amount") or 0) / 1e6,
                        "term_days": o.get("termInDays"),
                        "rate": o.get("interestRate"),
                        # arrives as ''/'true'/boolean depending on the day —
                        # bool('false') would be wrongly truthy
                        "is_small_value": str(o.get("isSmallValue") or "").strip().lower()
                                          in ("true", "t", "y", "1"),
                    }
                    for o in ops
                ],
            }
            store.save_blob(key, cached)
        except Exception as exc:
            cached = store.load_blob(key)
            if cached is None:
                raise SourceFault("nyfed", f"fx swaps: {exc}") from exc
    return {"fetched_at": cached["fetched_at"], "ops": cached["ops"]}
