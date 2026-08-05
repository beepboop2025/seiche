"""GDELT press-attention collector with a bulk-feed primary path.

GDELT's legacy DOC 2.0 search API used to require twelve calls for Seiche's
six frozen topics (volume + tone for each).  In June 2026 GDELT announced
that the legacy search infrastructure was overloaded during its Spanner
migration and asked researchers to use the downloadable WEB-NGRAM feed.
Production now follows that instruction:

* discover the newest substantial WEB-NGRAM heartbeat;
* download it once and scan it once for all six frozen topic phrase sets;
* divide matched documents by every document represented in that heartbeat;
* append the normalized observation to a durable local history;
* serve a recent last-known-good history when a single refresh misses.

The replacement deliberately does not synthesize GDELT tone.  WEB-NGRAM is
an occurrence corpus, not the DOC API's tone model, so the engine reports
tone as unavailable and scores attention from normalized volume only.  The
legacy implementation remains behind ``GDELT_SOURCE_MODE=legacy-doc`` for a
controlled comparison or recovery run; it is no longer the default.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import gzip
from io import BytesIO
import json
import os
from pathlib import Path
import re

import httpx

from seiche import store
from seiche.config import (
    GDELT_CALL_SPACING_S,
    GDELT_FAIL_COOLDOWN_MIN,
    GDELT_TIMESPAN,
    GDELT_TTL_MIN,
    GDELT_WEB_BASE as DEFAULT_WEB_BASE,
    GDELT_WEB_HISTORY_MAX,
    GDELT_WEB_LOOKBACK_MIN,
    GDELT_WEB_MAX_COMPRESSED_MB,
    GDELT_WEB_MAX_STALE_H,
    SCUTTLEBUTT_TOPICS,
    USER_AGENT,
)
from seiche.sources.base import utcnow_iso

# The box can still route an explicit legacy run through gdelt-gate.  Keeping
# this import-time contract preserves the documented env override and makes a
# recovery run identical on a laptop and in production.
API = (os.environ.get("GDELT_BASE", "https://api.gdeltproject.org").rstrip("/")
       + "/api/v2/doc/doc")

WEB_BASE = os.environ.get("GDELT_WEB_BASE", DEFAULT_WEB_BASE).rstrip("/")
WEB_HISTORY_KEY = "gdelt:web-ngrams:history:v1"
WEB_INDEX_KEY = "gdelt:web-ngrams:index:v1"
WEB_COOLDOWN_KEY = "gdelt:web-ngrams:cooldown:v1"
WEB_MODE = "web-ngrams"
WEB_HISTORY_FILE = os.environ.get("GDELT_WEB_HISTORY_FILE")
_web_refresh_task: asyncio.Task[tuple[dict, str | None]] | None = None

_DIGITS = re.compile(r"\D")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_QUOTED = re.compile(r'"([^"]+)"')


def _norm_phrase(value: str) -> str:
    return _NON_WORD.sub(" ", value.lower()).strip()


# Derive the match phrases from the frozen query registry.  The transport can
# change without creating a second, drifting opinion about what each topic is.
_TOPIC_PHRASES = {
    key: tuple(dict.fromkeys(_norm_phrase(p) for p in _QUOTED.findall(query)))
    for key, _label, query in SCUTTLEBUTT_TOPICS
}


def _iso(gdelt_date: str) -> str:
    """GDELT timeline dates ('20260716T000000Z') -> YYYY-MM-DD."""
    d = _DIGITS.sub("", gdelt_date)[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _timeline(payload: dict) -> list[list]:
    tl = payload.get("timeline") or []
    if not tl:
        return []
    return [[_iso(p["date"]), float(p["value"])] for p in tl[0].get("data", [])]


async def _mode(client: httpx.AsyncClient, query: str, mode: str) -> list[list]:
    """One legacy DOC mode.  Used only by the explicit legacy path."""
    r = await client.get(
        API,
        params={"query": query, "mode": mode, "timespan": GDELT_TIMESPAN,
                "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    body = r.text
    if not body.lstrip().startswith("{"):
        # GDELT serves rate-limit notices as HTTP 200 plain text.
        raise RuntimeError(f"non-JSON reply: {body[:100]!r}")
    return _timeline(json.loads(body))


def _candidate_stamps(asof: datetime | None = None) -> list[str]:
    """Newest-first minute stamps likely to contain a completed heartbeat.

    GDELT publishes a file for roughly two minutes ago but its legacy ingest
    arrives in bursts.  Five minutes of publication grace plus a thirty-minute
    search window covers two complete 15-minute heartbeats without guessing a
    fixed minute within each burst.
    """
    point = asof or datetime.now(timezone.utc)
    if point.tzinfo is None:
        point = point.replace(tzinfo=timezone.utc)
    cursor = point.astimezone(timezone.utc) - timedelta(minutes=5)
    return [
        (cursor - timedelta(minutes=offset)).strftime("%Y%m%d%H%M00")
        for offset in range(GDELT_WEB_LOOKBACK_MIN)
    ]


def _parse_web_batch(compressed: bytes, stamp: str) -> dict:
    """Scan one WEB-NGRAM gzip stream once for every frozen topic.

    The file is tab-separated ``DOCID, QUADGRAM, COUNT``.  A document is a
    hit when any of its quadgrams contains one of that topic's exact frozen
    phrases after punctuation/hyphen normalization.  Sets make repeated
    mentions in one article count once, matching the DOC volume semantics.
    """
    hits = {key: set() for key in _TOPIC_PHRASES}
    documents: set[int] = set()
    with gzip.GzipFile(fileobj=BytesIO(compressed)) as stream:
        for raw_line in stream:
            parts = raw_line.decode("utf-8", errors="replace").rstrip("\r\n").split("\t", 2)
            if len(parts) < 2:
                continue
            try:
                doc_id = int(parts[0])
            except ValueError:
                continue
            documents.add(doc_id)
            gram = f" {_norm_phrase(parts[1])} "
            for key, phrases in _TOPIC_PHRASES.items():
                if any(f" {phrase} " in gram for phrase in phrases):
                    hits[key].add(doc_id)
    if not documents:
        raise RuntimeError("WEB-NGRAM heartbeat contained no document rows")
    batch_at = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc).isoformat()
    return {
        "batch_at": batch_at,
        "documents": len(documents),
        "topic_counts": {key: len(doc_ids) for key, doc_ids in hits.items()},
    }


async def _fetch_web_sample(client: httpx.AsyncClient,
                            asof: datetime | None = None) -> dict:
    """Discover and download the largest recent WEB-NGRAM heartbeat."""
    limit = GDELT_WEB_MAX_COMPRESSED_MB * 1024 * 1024

    async def probe(stamp: str) -> tuple[str, int] | None:
        url = f"{WEB_BASE}/{stamp}.ngrams.txt.gz"
        try:
            r = await client.head(url, timeout=10)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            size = int(r.headers.get("content-length", "0"))
        except ValueError:
            return None
        if not 0 < size <= limit:
            return None
        return stamp, size

    found = [p for p in await asyncio.gather(
        *(probe(stamp) for stamp in _candidate_stamps(asof))) if p is not None]
    if not found:
        raise RuntimeError("no bounded WEB-NGRAM heartbeat found in lookback window")

    # A heartbeat burst includes tiny tail files and one substantial corpus.
    # The largest bounded object is the representative sample; choosing merely
    # the newest file frequently selects a few-dozen-document tail.
    stamp, expected_size = max(found, key=lambda item: item[1])
    url = f"{WEB_BASE}/{stamp}.ngrams.txt.gz"
    r = await client.get(url, timeout=90)
    r.raise_for_status()
    if len(r.content) > limit:
        raise RuntimeError(
            f"WEB-NGRAM heartbeat grew beyond {GDELT_WEB_MAX_COMPRESSED_MB}MB cap")
    if expected_size and len(r.content) != expected_size:
        raise RuntimeError(
            f"WEB-NGRAM heartbeat length changed ({expected_size} -> {len(r.content)})")
    sample = await asyncio.to_thread(_parse_web_batch, r.content, stamp)
    sample["url"] = url
    sample["compressed_bytes"] = len(r.content)
    return sample


def _load_web_history() -> dict:
    raw = store.load_blob(WEB_HISTORY_KEY)
    if (not isinstance(raw, dict) or not isinstance(raw.get("samples"), list)) \
            and WEB_HISTORY_FILE:
        try:
            raw = json.loads(Path(WEB_HISTORY_FILE).read_text())
        except (OSError, ValueError, TypeError):
            raw = None
    if not isinstance(raw, dict) or not isinstance(raw.get("samples"), list):
        return {"schema": "seiche.gdelt-web-history.v1", "samples": []}
    return raw


def _merge_web_sample(history: dict, sample: dict) -> dict:
    by_stamp = {
        row.get("batch_at"): row
        for row in history.get("samples", [])
        if isinstance(row, dict) and row.get("batch_at")
    }
    by_stamp[sample["batch_at"]] = sample
    rows = [by_stamp[key] for key in sorted(by_stamp)][-GDELT_WEB_HISTORY_MAX:]
    out = {"schema": "seiche.gdelt-web-history.v1", "samples": rows}
    store.save_blob(WEB_HISTORY_KEY, out)
    if WEB_HISTORY_FILE:
        path = Path(WEB_HISTORY_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(out, separators=(",", ":")) + "\n")
        temporary.replace(path)
    return out


def _web_blob(history: dict, *, stale: bool = False,
              refresh_note: str | None = None) -> dict:
    samples = history.get("samples") or []
    latest = samples[-1] if samples else {}
    topics: dict[str, dict] = {}
    for key, label, query in (SCUTTLEBUTT_TOPICS if samples else ()):
        volume = []
        for sample in samples:
            total = int(sample.get("documents") or 0)
            count = int((sample.get("topic_counts") or {}).get(key, 0))
            if total > 0:
                volume.append([sample["batch_at"], 100.0 * count / total])
        latest_total = int(latest.get("documents") or 0)
        latest_count = int((latest.get("topic_counts") or {}).get(key, 0))
        topics[key] = {
            "label": label,
            "query": query,
            "volume": volume,
            "tone": [],
            "matched_documents": latest_count,
            "sample_documents": latest_total,
            "current_share_pct": (
                100.0 * latest_count / latest_total if latest_total else None),
        }
    return {
        "fetched_at": utcnow_iso(),
        "asof": latest.get("batch_at"),
        "mode": WEB_MODE,
        "stale": stale,
        "refresh_note": refresh_note,
        "samples": len(samples),
        "latest_sample": latest or None,
        "topics": topics,
    }


def _history_age_hours(history: dict) -> float | None:
    samples = history.get("samples") or []
    if not samples:
        return None
    try:
        stamp = datetime.fromisoformat(samples[-1]["batch_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds() / 3600


async def _refresh_web_once() -> tuple[dict, str | None]:
    """Own one cancellation-safe bulk refresh and its persistence.

    The client belongs to this task rather than to an incoming API request.
    Callers can therefore time out without closing the connection underneath
    the shared refresh.  The optional string is a user-visible source fault;
    a recent last-known-good observation deliberately returns no top fault.
    """
    history = _load_web_history()
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            sample = await _fetch_web_sample(client)
    except Exception as exc:  # noqa: BLE001 — source failures stay explicit
        detail = f"WEB-NGRAM {type(exc).__name__}: {exc}"
        store.save_blob(WEB_COOLDOWN_KEY, {"at": utcnow_iso(), "detail": detail})
        age_h = _history_age_hours(history)
        if age_h is not None and age_h <= GDELT_WEB_MAX_STALE_H:
            return _web_blob(
                history,
                stale=True,
                refresh_note=f"{detail}; serving {age_h:.1f}h-old last-known-good",
            ), None
        return _web_blob(history, stale=True, refresh_note=detail), detail

    history = _merge_web_sample(history, sample)
    out = _web_blob(history)
    store.save_blob(WEB_INDEX_KEY, out)
    return out, None


def _shared_web_refresh() -> asyncio.Task[tuple[dict, str | None]]:
    """Return the one in-flight refresh for the current event loop."""
    global _web_refresh_task
    loop = asyncio.get_running_loop()
    if _web_refresh_task is None or _web_refresh_task.done() \
            or _web_refresh_task.get_loop() is not loop:
        _web_refresh_task = loop.create_task(_refresh_web_once())
    return _web_refresh_task


async def fetch_all(client: httpx.AsyncClient, faults: list[dict]) -> dict:
    """Fetch the production bulk feed, with a bounded last-known-good grace."""
    if os.environ.get("GDELT_SOURCE_MODE", WEB_MODE).lower() == "legacy-doc":
        return await _fetch_legacy_doc(client, faults)

    cached = store.load_blob(WEB_INDEX_KEY, GDELT_TTL_MIN)
    if isinstance(cached, dict):
        return cached

    history = _load_web_history()
    cooldown = store.load_blob(WEB_COOLDOWN_KEY, GDELT_FAIL_COOLDOWN_MIN)
    if cooldown is not None:
        age_h = _history_age_hours(history)
        detail = ((cooldown.get("detail") if isinstance(cooldown, dict) else None)
                  or "bulk-feed retry cooldown active")
        if age_h is not None and age_h <= GDELT_WEB_MAX_STALE_H:
            return _web_blob(
                history,
                stale=True,
                refresh_note=f"{detail}; serving {age_h:.1f}h-old last-known-good",
            )
        faults.append({"source": "gdelt", "detail": detail})
        return _web_blob(history, stale=True, refresh_note=detail)
    # Shielding is essential: asyncio cannot stop the worker thread doing the
    # gzip scan.  If a health probe or browser disconnects, this refresh keeps
    # its own HTTP client alive, finishes once, and populates the shared cache.
    out, fault_detail = await asyncio.shield(_shared_web_refresh())
    if fault_detail:
        faults.append({"source": "gdelt", "detail": fault_detail})
    return out


async def backfill_web_history(client: httpx.AsyncClient,
                               targets: list[datetime]) -> dict:
    """Operator helper: add historical heartbeat samples, resumably."""
    history = _load_web_history()
    for target in targets:
        sample = await _fetch_web_sample(client, target)
        history = _merge_web_sample(history, sample)
    return history


async def _fetch_legacy_doc(client: httpx.AsyncClient,
                            faults: list[dict]) -> dict:
    """Original twelve-call DOC sweep, retained for explicit recovery runs."""
    key = "gdelt:index"
    cached = store.load_blob(key, 11 * 60 + 30)
    if cached is not None:
        return cached
    if store.load_blob(key + ":cooldown", GDELT_FAIL_COOLDOWN_MIN) is not None:
        stale = store.load_blob(key)
        if stale is not None:
            return stale
        faults.append({"source": "gdelt",
                       "detail": "rate-limit cooldown active and no cached sweep yet"})
        return {"fetched_at": None, "mode": "legacy-doc", "topics": {}}
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
        except Exception as exc:  # noqa: BLE001 — fail loud per topic
            faults.append({"source": "gdelt",
                           "detail": f"{tkey}: {type(exc).__name__}: {exc}"})
            if "429" in str(exc) or "limit requests" in str(exc):
                faults.append({"source": "gdelt",
                               "detail": "rate-limited — sweep aborted, cooldown set"})
                break
    if topics and len(topics) < len(SCUTTLEBUTT_TOPICS):
        stale = store.load_blob(key)
        for tkey, topic in ((stale or {}).get("topics") or {}).items():
            if tkey not in topics:
                topics[tkey] = {**topic, "stale": True}
    out = {"fetched_at": utcnow_iso(), "mode": "legacy-doc", "topics": topics}
    if topics:
        store.save_blob(key, out)
    else:
        store.save_blob(key + ":cooldown", {"at": utcnow_iso()})
    return out
