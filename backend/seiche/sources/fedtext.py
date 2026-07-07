"""Fed communiqué collector — the text the market trades, as data.

FOMC statements are the one macro feed that is simultaneously free, keyless,
archived for decades, and stamped with an exact publication time — which
makes text-derived signals BACKTESTABLE WITH VINTAGE DISCIPLINE, something
almost no alternative data can claim. The statement URL pattern
(/newsevents/pressreleases/monetary{yyyymmdd}a.htm) has been stable for
years; each fetch is blob-cached forever (statements never change after
release).

Provenance status: the URL pattern could NOT be probed live from the build
container (network policy); the collector fails loud per date and the
engine's coverage shows exactly which meetings are missing. No text is
faked where none was fetched.
"""

from __future__ import annotations

import re

import httpx
import pandas as pd

from seiche import store
from seiche.config import FEDTEXT_TTL_MIN, FOMC_STATEMENT_DATES, USER_AGENT
from seiche.sources.base import utcnow_iso

BASE = "https://www.federalreserve.gov/newsevents/pressreleases"

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    # the statement body is plain prose; tags out, entities left harmless
    txt = _TAG.sub(" ", html)
    txt = txt.replace("&amp;", "&").replace("&nbsp;", " ")
    return _WS.sub(" ", txt).strip()


async def fetch_statement(client: httpx.AsyncClient, date: str) -> str | None:
    """One statement's text by decision date (YYYY-MM-DD), cached forever."""
    key = f"fedtext:{date}"
    cached = store.load_blob(key)
    if cached is not None:
        return cached.get("text")
    ymd = date.replace("-", "")
    r = await client.get(
        f"{BASE}/monetary{ymd}a.htm", headers={"User-Agent": USER_AGENT}, timeout=30
    )
    r.raise_for_status()
    text = _strip_html(r.text)
    if len(text) < 400:  # a real statement is never this short — treat as a miss
        return None
    store.save_blob(key, {"date": date, "fetched_at": utcnow_iso(), "text": text[:40000]})
    return text


async def fetch_all(client: httpx.AsyncClient, faults: list[dict]) -> dict:
    """Every configured decision date; per-date failure is a fault line, not
    a crash — coverage is published by the engine."""
    key = "fedtext:index"
    cached = store.load_blob(key, FEDTEXT_TTL_MIN)
    if cached is not None:
        return cached
    texts: dict[str, str] = {}
    today = pd.Timestamp.now().date().isoformat()
    for d in FOMC_STATEMENT_DATES:
        if d > today:
            continue  # the meeting hasn't happened; nothing to fetch
        try:
            t = await fetch_statement(client, d)
            if t:
                texts[d] = t
        except Exception as e:  # noqa: BLE001 — fail loud per date
            faults.append({"source": "fedtext", "detail": f"{d}: {type(e).__name__}: {e}"})
    out = {"fetched_at": utcnow_iso(), "texts": texts}
    if texts:
        store.save_blob(key, out)
    return out


def texts_to_frame(texts: dict[str, str]) -> pd.DataFrame:
    rows = [{"date": pd.Timestamp(d), "text": t} for d, t in sorted(texts.items())]
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()
