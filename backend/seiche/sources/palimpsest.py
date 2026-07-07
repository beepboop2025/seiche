"""Palimpsest collector — the policy-fear channel of the far basin.

Palimpsest (palimpsest.info) measures Chinese internet censorship by treating
deletion itself as data, and publishes the readings as keyless CI-refreshed
JSON: the DDTI deletion-threat index (3h cadence), the newly-targeted term
count, and the Generative Firewall Index (state-aligned-LLM refusal
tomography, daily). What an authoritarian state rushes to delete is a
real-time stress read no market data vendor carries at any price — and
Seiche's own README declares China out of scope for lack of a qualifying
free feed. This is that feed.

Contract discipline unchanged:
  - palimpsest.info primary, GitHub raw mirror fallback (same files, same
    Actions pipeline behind both);
  - every series lands in the same SQLite store + Series envelope, so
    provenance/staleness work like FRED — AND the store accrues history
    beyond the upstream files' short retention window (each fetch upserts by
    date; the local record only grows);
  - the feeds are DAYS old as public series: the engine downstream labels
    them NOT YET BACKTESTABLE until they clear FARBASIN_MIN_OBS. No history
    is faked where none exists.
"""

from __future__ import annotations

import json

import httpx
import pandas as pd

from seiche import store
from seiche.config import ALL_SERIES, PALIMPSEST_BASES, PALIMPSEST_TTL_MIN, USER_AGENT
from seiche.sources.base import Series, SourceFault, utcnow_iso


async def _get_text(client: httpx.AsyncClient, path: str) -> str:
    last_err: Exception | None = None
    for base in PALIMPSEST_BASES:
        try:
            r = await client.get(
                f"{base}/{path}", headers={"User-Agent": USER_AGENT}, timeout=30
            )
            r.raise_for_status()
            return r.text
        except Exception as e:  # try the mirror before failing loud
            last_err = e
    raise SourceFault("palimpsest", f"{path}: {type(last_err).__name__}: {last_err}")


def _jsonl(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _daily(records: list[dict], date_key: str, value_key: str, agg: str) -> pd.Series:
    rows = []
    for r in records:
        d, v = r.get(date_key), r.get(value_key)
        if d is None or v is None:
            continue
        rows.append((pd.Timestamp(str(d)[:10]), float(v)))
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series([v for _, v in rows], index=pd.DatetimeIndex([d for d, _ in rows]))
    grouped = s.groupby(s.index)
    return (grouped.max() if agg == "max" else grouped.last()).sort_index()


def _merge_and_store(mnemonic: str, fresh: pd.Series) -> Series:
    """Upsert fresh points over the accrued store history — the local record
    outlives the upstream file's retention window."""
    spec = ALL_SERIES[mnemonic]
    prior = store.load_series(mnemonic)
    if prior is not None and not prior.points.empty:
        merged = pd.concat([prior.points, fresh])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = fresh
    s = Series(mnemonic, spec.source, spec.remote_id, spec.label, spec.unit,
               spec.freq, utcnow_iso(), merged)
    store.save_series(s)
    return s


async def fetch_all(client: httpx.AsyncClient, faults: list[dict]) -> dict:
    """DDTI + GFI series (accrued) plus the latest board for the card."""
    if all(store.is_fresh(m, PALIMPSEST_TTL_MIN) for m in
           ("PALIMPSEST_FEAR", "PALIMPSEST_NEW", "PALIMPSEST_GFI")):
        cached = {m: store.load_series(m) for m in
                  ("PALIMPSEST_FEAR", "PALIMPSEST_NEW", "PALIMPSEST_GFI")}
        latest = store.load_blob("palimpsest:latest") or {}
        if all(cached.values()):
            return {"fetched_at": utcnow_iso(), "series": cached, "latest": latest}

    out: dict = {"fetched_at": utcnow_iso(), "series": {}, "latest": {}}

    try:
        ddti_hist = _jsonl(await _get_text(client, "ddti-history.jsonl"))
        out["series"]["PALIMPSEST_FEAR"] = _merge_and_store(
            "PALIMPSEST_FEAR", _daily(ddti_hist, "generated_at", "top_threat", "max"))
        out["series"]["PALIMPSEST_NEW"] = _merge_and_store(
            "PALIMPSEST_NEW", _daily(ddti_hist, "generated_at", "n_new", "last"))
    except SourceFault as e:
        faults.append({"source": e.source, "detail": e.detail})

    try:
        gfi_hist = _jsonl(await _get_text(client, "history.jsonl"))
        out["series"]["PALIMPSEST_GFI"] = _merge_and_store(
            "PALIMPSEST_GFI", _daily(gfi_hist, "date", "gfi", "last"))
    except SourceFault as e:
        faults.append({"source": e.source, "detail": e.detail})

    try:
        latest = json.loads(await _get_text(client, "ddti-latest.json"))
        top = [
            {"term": r.get("term"), "domain": r.get("domain"),
             "threat": r.get("threat"), "is_new": r.get("is_new")}
            for r in (latest.get("ranked") or [])[:8]
        ]
        out["latest"] = {
            "generated_at": latest.get("generated_at"),
            "n_terms": latest.get("n_terms"),
            "top": top,
        }
        store.save_blob("palimpsest:latest", out["latest"])
    except SourceFault as e:
        faults.append({"source": e.source, "detail": e.detail})

    if not out["series"] and not out["latest"]:
        raise SourceFault("palimpsest", "all readings unreachable (primary + mirror)")
    return out
