"""GDELT news-attention collector — how loudly the press is talking about
the plumbing.

GDELT DOC 2.0 timeline API: keyless, free, CC-licensed, global news
monitoring — the same class of source as everything else on this board, so
the free-data promise holds. Per configured money-market topic (queries
FROZEN in config) we pull two daily series over the baseline window:

  volume  normalized coverage (share of all global articles that day that
          match the topic, in percent — global-news-cycle confound removed)
  tone    mean GDELT document tone (negative = bad press)

Fair use: GDELT asks for one request per ~5s from bulk users; we make
2 calls per topic (12 total), spaced, at most twice a day (TTL-cached blob),
which is far inside polite. Per-topic failure is a fault line, not a crash —
the engine publishes coverage exactly.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from seiche import store
from seiche.config import (
    GDELT_CALL_SPACING_S,
    GDELT_FAIL_COOLDOWN_MIN,
    GDELT_TIMESPAN,
    GDELT_TTL_MIN,
    SCUTTLEBUTT_TOPICS,
    USER_AGENT,
)
from seiche.sources.base import utcnow_iso

API = "https://api.gdeltproject.org/api/v2/doc/doc"

_DIGITS = re.compile(r"\D")


def _iso(gdelt_date: str) -> str:
    """GDELT timeline dates ('20260716T000000Z' and friends) -> YYYY-MM-DD."""
    d = _DIGITS.sub("", gdelt_date)[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _timeline(payload: dict) -> list[list]:
    tl = payload.get("timeline") or []
    if not tl:
        return []
    return [[_iso(p["date"]), float(p["value"])] for p in tl[0].get("data", [])]


async def _mode(client: httpx.AsyncClient, query: str, mode: str) -> list[list]:
    r = await client.get(
        API,
        params={"query": query, "mode": mode, "timespan": GDELT_TIMESPAN, "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    body = r.text
    if not body.lstrip().startswith("{"):
        # GDELT serves rate-limit notices as HTTP 200 plain text — fail loud
        raise RuntimeError(f"non-JSON reply: {body[:100]!r}")
    return _timeline(json.loads(body))


async def fetch_all(client: httpx.AsyncClient, faults: list[dict]) -> dict:
    """Every configured topic; per-topic failure is a fault, not a crash."""
    key = "gdelt:index"
    cached = store.load_blob(key, GDELT_TTL_MIN)
    if cached is not None:
        return cached
    if store.load_blob(key + ":cooldown", GDELT_FAIL_COOLDOWN_MIN) is not None:
        # last sweep got nothing (typically a 429-blocked IP) — retrying on
        # every snapshot would extend the block; serve the stale blob if any
        stale = store.load_blob(key)
        if stale is not None:
            return stale
        faults.append({"source": "gdelt",
                       "detail": "rate-limit cooldown active and no cached sweep yet"})
        return {"fetched_at": None, "topics": {}}
    topics: dict[str, dict] = {}
    for tkey, label, query in SCUTTLEBUTT_TOPICS:
        try:
            volume = await _mode(client, query, "timelinevol")
            await asyncio.sleep(GDELT_CALL_SPACING_S)
            tone = await _mode(client, query, "timelinetone")
            await asyncio.sleep(GDELT_CALL_SPACING_S)
            if volume:
                topics[tkey] = {"label": label, "query": query,
                                "volume": volume, "tone": tone}
        except Exception as e:  # noqa: BLE001 — fail loud per topic
            faults.append({"source": "gdelt", "detail": f"{tkey}: {type(e).__name__}: {e}"})
            if "429" in str(e) or "limit requests" in str(e):
                # the IP is rate-limited right now — every further call would
                # fail the same way AND extend the block; stop the sweep
                faults.append({"source": "gdelt",
                               "detail": "rate-limited — sweep aborted, cooldown set"})
                break
    out = {"fetched_at": utcnow_iso(), "topics": topics}
    if topics:
        store.save_blob(key, out)
    else:
        store.save_blob(key + ":cooldown", {"at": utcnow_iso()})
    return out
