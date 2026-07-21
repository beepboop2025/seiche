"""Undertow Fetch pack — the lab's current-affairs wind, read back into Seiche.

FETCH is the lab's upstream current-affairs layer (built in the Undertow
repo): GDELT DOC 2.0 attention volume across seven cited transmission
channels, each routed to the lab surface it plausibly shocks. The pack is
published to the public window at api.seiche.info/undertow/fetch.json by
the same box that publishes the Undertow board. This source reads the pack
back into Seiche so the funding-routed channels can ride the board as an
overlay: the wind that raises the water this terminal measures.

Contract discipline (same as every collector here):
  - ONE request per refresh, TTL-gated at WINDFETCH_TTL_MIN — the pack
    refreshes on the Undertow collect cadence, not intraday;
  - TTL blob first; on upstream failure the stale blob is served (its age
    visible via fetched_at); SourceFault only when there is no cached copy
    at all (fail loud, never fake);
  - the pack is served VERBATIM with provenance — this source never
    reinterprets the pack's own accrual honesty (a withheld percentile
    stays withheld; the engine states it, never fills it).

Payload shape returned by fetch_all:
    {"fetched_at": iso str, "pack": <the fetch.json dict, verbatim>}
"""

from __future__ import annotations

import httpx

from seiche import store
from seiche.config import USER_AGENT, WINDFETCH_TTL_MIN, WINDFETCH_URL
from seiche.sources.base import SourceFault, utcnow_iso

BLOB_KEY = "windfetch:pack"


def parse_pack(payload: object) -> dict:
    """Validate the pack shape. Pure function — tests drive it directly."""
    if not isinstance(payload, dict):
        raise ValueError(f"fetch pack must be a dict, got {type(payload).__name__}")
    if not isinstance(payload.get("channels"), list):
        raise ValueError("fetch pack missing 'channels' list")
    return payload


async def fetch_all(client: httpx.AsyncClient, faults: list[dict] | None = None) -> dict:
    cached = store.load_blob(BLOB_KEY, WINDFETCH_TTL_MIN)
    if cached is not None:
        return cached
    try:
        r = await client.get(
            WINDFETCH_URL, headers={"User-Agent": USER_AGENT}, timeout=30.0
        )
        r.raise_for_status()
        out = {"fetched_at": utcnow_iso(), "pack": parse_pack(r.json())}
        store.save_blob(BLOB_KEY, out)
        return out
    except Exception as exc:  # noqa: BLE001
        stale = store.load_blob(BLOB_KEY)
        if stale is not None:
            if faults is not None:
                faults.append({"source": "windfetch",
                               "detail": f"refresh failed, serving stale: {exc}"})
            return stale
        raise SourceFault("windfetch", f"{type(exc).__name__}: {exc}") from exc
