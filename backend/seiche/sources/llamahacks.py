"""DeFiLlama hacks collector — the crypto-native wreck ledger.

https://api.llama.fi/hacks: keyless JSON, one GET returns the full recorded
history of DeFi exploits (~590 events, ~150KB — verified live 2026-07-20).
Each event: name, date (unix seconds, UTC), amount (USD lost), chain list,
technique, classification. A big exploit is a funding event in the only
dollar market open on weekends: forced unwinds and stablecoin runs hit the
same complex the moorings board watches.

Contract discipline (same as every collector here):
  - ONE request per refresh, TTL-gated at LLAMA_HACKS_TTL_MIN (6h) — events
    are not intraday data, and one call per 6h sits far inside the 1 req/2s
    politeness floor;
  - the payload rides the blob cache as JSON; on fetch failure the stale
    copy is served (its true age visible via the Series' fetched_at), and
    only a cold-start failure raises SourceFault (fail loud, never fake);
  - the daily loss series is a provenance-carrying base.Series like every
    other feed, so staleness grading and the Time Machine work unchanged;
  - the full parsed event list is retained (not just a tail) so a replay
    truncated to any historical date sees exactly the events then known.

Payload shape returned by fetch_all:
    {
        "fetched_at": iso str,
        "daily":  Series  — daily total exploit losses, USD, zero-filled
                            over [first hack, last hack]; DatetimeIndex,
        "events": [ {name, date, amount, chain, technique}, ... ]
                            — date as YYYY-MM-DD, amount float USD or None,
                            chain list[str]; sorted ascending by date.
    }
"""

from __future__ import annotations

import httpx
import pandas as pd

from seiche import store
from seiche.config import LLAMA_HACKS_TTL_MIN, USER_AGENT
from seiche.sources.base import Series, SourceFault, utcnow_iso

API = "https://api.llama.fi/hacks"
BLOB_KEY = "llamahacks:index"

MNEMONIC = "LLAMA_HACK_LOSS_USD"


def parse_hacks(payload: list) -> tuple[pd.Series, list[dict]]:
    """Raw API JSON -> (daily zero-filled loss series, event dicts).

    Pure function — the tests drive it with a recorded synthetic payload.
    Events with an unparseable date are skipped; a missing amount keeps the
    event (amount None) but contributes nothing to the daily sum.
    """
    if not isinstance(payload, list):
        raise ValueError(f"hacks payload must be a list, got {type(payload).__name__}")
    events: list[dict] = []
    for h in payload:
        if not isinstance(h, dict):
            continue
        try:
            day = pd.Timestamp(int(h["date"]), unit="s", tz="UTC").tz_localize(None)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        try:
            amount = float(h["amount"]) if h.get("amount") is not None else None
        except (TypeError, ValueError):
            amount = None
        chain = h.get("chain")
        events.append({
            "name": str(h.get("name") or "unknown"),
            "date": day.date().isoformat(),
            "amount": amount,
            "chain": [str(c) for c in chain] if isinstance(chain, list) else [],
            "technique": str(h.get("technique") or "unknown"),
        })
    events.sort(key=lambda e: e["date"])
    dated = [(pd.Timestamp(e["date"]), e["amount"]) for e in events if e["amount"] is not None]
    if not dated:
        return pd.Series(dtype=float), events
    s = pd.Series(
        [v for _, v in dated], index=pd.DatetimeIndex([d for d, _ in dated]), dtype=float
    )
    daily = s.groupby(s.index).sum()
    # zero-filled over the full observed span: no-hack days are real zeros,
    # and a contiguous index keeps resampling/alignment honest downstream.
    full_idx = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    return daily.reindex(full_idx, fill_value=0.0), events


def _to_blob(out: dict) -> dict:
    """JSON-safe form for the blob cache (Series -> [date, value] pairs)."""
    daily: pd.Series = out.get("daily", pd.Series(dtype=float))
    return {
        "fetched_at": out.get("fetched_at"),
        "daily": [[d.date().isoformat(), float(v)] for d, v in daily.items()],
        "events": out.get("events") or [],
    }


def _from_blob(blob: dict) -> dict:
    """Rebuild the fetch_all payload from its blob-cached JSON form."""
    rows = blob.get("daily") or []
    daily = pd.Series(
        [r[1] for r in rows],
        index=pd.DatetimeIndex([r[0] for r in rows]),
        dtype=float,
    )
    return {
        "fetched_at": blob.get("fetched_at"),
        "daily": daily,
        "events": blob.get("events") or [],
    }


def _envelope(daily: pd.Series, fetched_at: str) -> Series:
    """The daily series in the shared provenance envelope."""
    return Series(
        MNEMONIC, "llamahacks", "api.llama.fi/hacks",
        "DeFi exploit losses (daily total, DeFiLlama hacks)",
        "$", "D", fetched_at, daily,
    )


async def _get(client: httpx.AsyncClient) -> list:
    r = await client.get(API, headers={"User-Agent": USER_AGENT}, timeout=45)
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, list):
        # an error page / throttling notice parsed as JSON is still a failure
        raise ValueError(f"non-list payload: {str(body)[:100]!r}")
    return body


async def fetch_all(client: httpx.AsyncClient, faults: list[dict] | None = None) -> dict:
    """Daily exploit-loss series + the retained event ledger.

    TTL blob first; on upstream failure serve the stale blob; SourceFault
    only when there is no cached copy at all.
    """
    cached = store.load_blob(BLOB_KEY, LLAMA_HACKS_TTL_MIN)
    if cached is not None:
        out = _from_blob(cached)
        out["daily"] = _envelope(out["daily"], out.get("fetched_at") or utcnow_iso())
        return out
    try:
        daily, events = parse_hacks(await _get(client))
        if daily.empty and not events:
            raise ValueError("empty hacks payload")
        out = {"fetched_at": utcnow_iso(), "daily": daily, "events": events}
        store.save_blob(BLOB_KEY, _to_blob(out))
        out["daily"] = _envelope(daily, out["fetched_at"])
        return out
    except Exception as exc:
        stale = store.load_blob(BLOB_KEY)
        if stale is not None:
            out = _from_blob(stale)
            out["daily"] = _envelope(out["daily"], out.get("fetched_at") or utcnow_iso())
            return out
        raise SourceFault("llamahacks", f"{type(exc).__name__}: {exc}") from exc


def truncate(payload: dict, asof: pd.Timestamp) -> dict:
    """Time Machine cut: series and event ledger as known on `asof`.

    Pure — the cached live payload is never mutated. assemble._truncate_sources
    delegates here so the replay stays point-in-time correct on both legs.
    """
    payload = payload or {}
    daily = payload.get("daily")
    cut = pd.Series(dtype=float)
    if isinstance(daily, Series):
        cut = daily.points[daily.points.index <= asof]
    elif isinstance(daily, pd.Series):
        cut = daily[daily.index <= asof]
    day = asof.date().isoformat()
    return {
        "fetched_at": payload.get("fetched_at"),
        "daily": _envelope(cut, payload.get("fetched_at") or utcnow_iso()),
        "events": [e for e in (payload.get("events") or []) if (e.get("date") or "") <= day],
    }
