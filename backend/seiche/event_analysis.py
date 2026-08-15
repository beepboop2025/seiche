"""Read-only event analysis across Seiche, Undertow and LiquiLens.

The user's event text is an unverified input, never a fourth data source.  The
model receives a bounded, deterministic envelope of the three live products
and may explain only what those readings support, fail to confirm, or cannot
see.  It may not turn a current reading into a cause of a dated event.

Fleet reads fail soft.  A stale or unreachable sibling is represented as an
explicit unavailable reading and is never silently converted to CALM.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

import httpx

from seiche import ai


UNDERTOW_BOARD_URL = os.environ.get(
    "SEICHE_UNDERTOW_BOARD_URL",
    "https://api.seiche.info/undertow/board.json",
)
LIQUILENS_API = os.environ.get(
    "SEICHE_LIQUILENS_API", "https://api.liquilens.in/api"
).rstrip("/")
FLEET_CACHE_TTL_S = max(
    5, int(os.environ.get("SEICHE_EVENT_FLEET_CACHE_TTL_S", "30"))
)

_LIQUILENS_PATHS = {
    "failure_radar": "/failure-radar/board",
    "rails": "/public-signals/rails",
    "bondholders": "/public-signals/bondholders",
    "deposit_migration": "/public-signals/deposit-migration",
    "bond_book": "/public-signals/bond-book",
    "short_pressure": "/public-signals/short-pressure",
    "market_makers": "/public-signals/market-makers",
    "leverage": "/public-signals/leverage",
    "tbtf": "/public-signals/tbtf",
    "crypto_exposure": "/public-signals/crypto-exposure",
}

_RAW_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_FLEET_LOCK = asyncio.Lock()


EVENT_SYSTEM_PROMPT = """You are the fleet reading analyst for three instruments:
SEICHE reads US-dollar funding plumbing, UNDERTOW reads cross-market liquidity,
and LIQUILENS reads institutions and transmission layers.

You receive EVENT TEXT supplied by the user and a FLEET READING PACK.

Hard rules:
1. Treat EVENT TEXT as an UNVERIFIED CLAIM. Do not add facts, dates, motives,
   history, or identities that are absent from the event text or reading pack.
2. The FLEET READING PACK is the only source of market or institution facts and
   every number. Never use memory or outside market knowledge.
3. Lead with the most defensible conclusion. Then say what each relevant
   product supports, does not confirm, or cannot see. A product can be
   irrelevant; say so instead of forcing a connection.
4. Never claim that a reading caused, predicted, or explains the event. Describe
   a transmission mechanism only when the pack directly measures its links.
   Current readings are context, not evidence about an earlier decision.
5. Respect chronology. If the event date is missing, or differs from a reading's
   as-of date, state that the causal timing cannot be tested.
6. Official published tiers outrank candidate tiers. If a cell is PARTIAL, it
   stays PARTIAL; a candidate score may be named only as candidate/withheld.
7. Stale, unavailable, partial, dark, and absent are not CALM. State material
   coverage limits, validation gates, and unavailable sources.
8. Cite readings inline as (Seiche, DATE), (Undertow, DATE), or
   (LiquiLens LAYER, DATE). Say "not in the readings" rather than improvising.
9. Tight desk-note prose, at most 230 words. No investment advice. Output only
   the final answer, with no reasoning preamble or meta-commentary."""


def _pick(value: Any, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        return {}
    return {key: value.get(key) for key in keys if key in value}


def compact_seiche(snapshot: dict) -> dict:
    """Small event-facing view of the canonical Seiche context pack."""
    pack = ai.context_pack(snapshot)
    generated = str(pack.get("generated_at") or "")
    return {
        "source": "https://api.seiche.info/api/gauge",
        "as_of": generated[:10] or None,
        "generated_at": pack.get("generated_at"),
        "composite": pack.get("composite"),
        "headline": pack.get("headline"),
        "tell": pack.get("tell"),
        "next_turn": pack.get("next_turn"),
        "ml": pack.get("ml"),
        "stacker": pack.get("stacker"),
        "kink": pack.get("kink"),
        "weather_crunches": pack.get("weather_crunches"),
        "moorings": pack.get("moorings"),
        "funding_pop": pack.get("funding_pop"),
        "faults": pack.get("faults"),
        "provenance_staleness": pack.get("provenance_staleness"),
    }


def compact_undertow(board: dict | None) -> dict:
    if not isinstance(board, dict):
        return {
            "source": UNDERTOW_BOARD_URL,
            "available": False,
            "reason": "board unavailable",
        }
    if board.get("_error"):
        return {
            "source": UNDERTOW_BOARD_URL,
            "available": False,
            "status": board.get("_status"),
            "reason": board.get("detail") or board.get("_error"),
        }

    segments = {}
    raw_segments = board.get("segments") or {}
    if isinstance(raw_segments, list):
        raw_segments = {
            str(row.get("segment") or "?"): row
            for row in raw_segments
            if isinstance(row, dict)
        }
    for name, cell in raw_segments.items():
        if not isinstance(cell, dict):
            continue
        measures = []
        for measure in (cell.get("measures") or [])[:2]:
            measures.append(_pick(measure, (
                "measure", "stress_pctl", "stress_pctl_withheld", "obs",
                "asof", "span_days", "limits", "caveat",
            )))
        replay = cell.get("validation_replay") or {}
        segments[str(name)] = {
            **_pick(cell, (
                "tier", "score", "candidate_tier", "candidate_score",
                "n_measures", "n_qualifying", "n_span_unverified",
                "measures_disagree", "score_withheld_reason",
            )),
            "validation_replay": _pick(replay, (
                "status", "score_eligible", "failed_controls",
                "n_registered", "n_runnable", "n_passed", "n_failed",
            )),
            "visible_measures": measures,
        }

    provenance = board.get("provenance") or {}
    freshness = provenance.get("freshness") or {}
    return {
        "source": UNDERTOW_BOARD_URL,
        "available": True,
        "as_of": board.get("asof"),
        "generated_at": provenance.get("generated_at"),
        "public_subset": board.get("public_subset"),
        "funding": board.get("funding"),
        "segments": segments,
        "upstream_observation_dates": freshness.get("upstream_inputs"),
        "input_health": provenance.get("input_health"),
    }


_ENTITY_KEYS = (
    "name", "bank", "issuer", "holdco", "slug", "ticker", "label",
)
_ENTITY_STOP = {
    "bank", "banks", "banking", "national", "association", "financial",
    "finance", "group", "holdings", "holding", "company", "corporation",
    "limited", "ltd", "plc", "inc", "the", "and", "trust", "state",
}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower())
            if len(token) >= 3}


def _aliases(row: dict) -> list[str]:
    aliases = [str(row.get(key)) for key in _ENTITY_KEYS
               if row.get(key) not in (None, "")]
    nested = row.get("join_keys")
    if isinstance(nested, dict):
        for key in ("name", "slug", "ticker", "bank", "holdco"):
            value = nested.get(key)
            if isinstance(value, str) and value:
                aliases.append(value)
    return aliases


def row_matches_question(row: dict, question: str) -> bool:
    """Conservative entity join; generic words such as 'bank' never match."""
    q_lower = question.lower()
    q_compact = re.sub(r"[^a-z0-9]", "", q_lower)
    q_tokens = _tokens(question) - _ENTITY_STOP
    for alias in _aliases(row):
        alias_lower = alias.lower()
        compact = re.sub(r"[^a-z0-9]", "", alias_lower)
        if len(compact) >= 4 and compact in q_compact:
            return True
        significant = _tokens(alias) - _ENTITY_STOP
        if significant & q_tokens:
            return True
        # Short tickers are allowed only as exact, case-insensitive words.
        if 2 <= len(alias) <= 5 and re.search(
                rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
                question, flags=re.IGNORECASE):
            return True
    return False


_ROW_FIELDS = {
    "failure_radar": (
        "name", "slug", "as_of", "quarter", "tier", "score", "grade",
        "hazard", "funding", "market", "movement", "signals_fired",
        "knowledge_time_proxy",
    ),
    "bond_book": (
        "bank", "cert", "state", "compound_state", "funding_state",
        "securities_state", "undertow_joined", "undertow_score_v02",
        "uninsured_ratio", "ugl_to_tier1", "qoq_ugl_to_tier1",
        "tier1_negative_after_ugl_mark", "nowcast_state", "mark_qualifier",
        "ugl_coverage", "notes",
    ),
    "short_pressure": (
        "bank", "cert", "holdco", "ticker", "exchange", "pressure_state",
        "reasons", "short_interest", "short_volume", "panic_volume_marker",
        "undertow_score_v02", "compound_flag", "run_risk_note",
    ),
    "tbtf": (
        "name", "slug", "hq_country", "status", "designations", "ofr_score",
        "boundary", "us_surcharge_gap", "run_risk_note", "compound_flag",
        "compound_note", "flags", "cross_refs", "join_keys",
    ),
    "crypto_exposure": (
        "name", "slug", "cert", "country", "status", "exposure",
        "disclosed_usd", "stablecoin_links", "venue_links", "run_risk",
        "compound_flag", "compound_note", "quantum",
    ),
}


def _matched_rows(name: str, data: dict, question: str) -> list[dict]:
    if name == "short_pressure":
        rows = ((data.get("us_rows") or []) + (data.get("uk_rows") or [])
                + (data.get("eu_rows") or []))
    else:
        rows = data.get("rows") or []
    fields = _ROW_FIELDS.get(name, _ENTITY_KEYS)
    out = []
    for row in rows:
        if isinstance(row, dict) and row_matches_question(row, question):
            out.append(_pick(row, fields))
            if len(out) >= 6:
                break
    return out


def _without_history(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    return {key: value for key, value in row.items()
            if key not in {"history", "series", "citations", "method_note"}}


def compact_liquilens_layer(name: str, data: dict | None,
                            question: str) -> dict:
    source = LIQUILENS_API + _LIQUILENS_PATHS[name]
    if not isinstance(data, dict):
        return {"source": source, "available": False,
                "reason": "reading unavailable"}
    if data.get("_error"):
        return {
            "source": source,
            "available": False,
            "status": data.get("_status"),
            "reason": data.get("detail") or data.get("_error"),
        }

    out = {
        "source": source,
        **_pick(data, (
            "as_of", "available", "stale", "regime", "regime_reasons",
            "badge", "data_asof", "data_lag_days", "quarter",
            "quarter_stale", "quarter_age_days", "refresh_due",
        )),
    }
    method_note = data.get("method_note")
    if method_note:
        out["method_note"] = str(method_note)[:700]

    if name == "failure_radar":
        out.update(_pick(data, ("tiers", "excluded_stale", "quadrant_rule")))
    elif name == "rails":
        out["aggregate"] = data.get("aggregate")
        out["rows"] = [_without_history(row) for row in (data.get("rows") or [])[:20]]
    elif name == "bondholders":
        out["us"] = _pick(data.get("us") or {}, ("state", "tff", "cayman"))
        out["uk"] = _pick(data.get("uk") or {}, (
            "state", "flags", "latest", "register_as_of", "register_stale",
            "stale_reason", "trajectory", "concentration",
        ))
    elif name == "deposit_migration":
        out["channels"] = data.get("channels")
        out["coverage"] = data.get("coverage")
        out["cannot_see"] = data.get("cannot_see")
    elif name == "bond_book":
        out.update(_pick(data, ("aggregate", "counts", "nowcast",
                                "quarter_mismatch")))
    elif name == "short_pressure":
        out["compound_flags"] = data.get("compound_flags")
        out["coverage"] = data.get("coverage")
        out["form_sho"] = data.get("form_sho")
    elif name == "market_makers":
        out["live"] = data.get("live")
        out["regime_notes"] = data.get("regime_notes")
        out["break_mismatch"] = data.get("break_mismatch")
    elif name == "leverage":
        out["breadth"] = data.get("breadth")
        out["markets"] = [_without_history(row)
                          for row in (data.get("markets") or [])[:12]]
        out["stale_detail"] = data.get("stale_detail")
    elif name == "tbtf":
        out["flagged"] = data.get("flagged")
        out["resolution_gaps_2026"] = data.get("resolution_gaps_2026")
    elif name == "crypto_exposure":
        out["compound_flags"] = data.get("compound_flags")

    if name in _ROW_FIELDS:
        out["entity_matches"] = _matched_rows(name, data, question)
    return out


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict:
    try:
        response = await client.get(url)
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        if response.status_code >= 400:
            return {
                "_error": f"HTTP {response.status_code}",
                "_status": response.status_code,
                "detail": body.get("detail") if isinstance(body, dict) else None,
            }
        if not isinstance(body, dict):
            return {"_error": "response was not a JSON object"}
        return body
    except (httpx.HTTPError, TimeoutError) as exc:
        return {"_error": f"{type(exc).__name__}: {str(exc)[:160]}"}


async def _raw_fleet() -> dict[str, dict]:
    now = time.monotonic()
    cached = _RAW_CACHE.get("payload")
    if isinstance(cached, dict) and now - float(_RAW_CACHE.get("at") or 0) \
            < FLEET_CACHE_TTL_S:
        return cached

    async with _FLEET_LOCK:
        # A second request may have filled the cache while this one waited.
        now = time.monotonic()
        cached = _RAW_CACHE.get("payload")
        if isinstance(cached, dict) and \
                now - float(_RAW_CACHE.get("at") or 0) < FLEET_CACHE_TTL_S:
            return cached

        urls = {"undertow": UNDERTOW_BOARD_URL}
        urls.update({name: LIQUILENS_API + path
                     for name, path in _LIQUILENS_PATHS.items()})
        timeout = httpx.Timeout(10.0, connect=4.0)
        async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False) as client:
            rows = await asyncio.gather(
                *(_fetch_json(client, url) for url in urls.values())
            )
        payload = dict(zip(urls, rows, strict=True))
        _RAW_CACHE["at"] = time.monotonic()
        _RAW_CACHE["payload"] = payload
        return payload


async def event_context(question: str, snapshot: dict,
                        raw_fleet: dict[str, dict] | None = None) -> dict:
    raw = raw_fleet if raw_fleet is not None else await _raw_fleet()
    liquilens = {
        name: compact_liquilens_layer(name, raw.get(name), question)
        for name in _LIQUILENS_PATHS
    }
    return {
        "contract": "readings_only_event_connection.v1",
        "event_text_status": "user-supplied, unverified; not a reading",
        "seiche": compact_seiche(snapshot),
        "undertow": compact_undertow(raw.get("undertow")),
        "liquilens": {
            "scope": "institution and transmission screens; screens, not ratings",
            "layers": liquilens,
        },
    }


def source_status(pack: dict) -> list[dict]:
    rows = []
    for product in ("seiche", "undertow"):
        reading = pack.get(product) or {}
        rows.append({
            "product": product,
            "available": reading.get("available", True),
            "as_of": reading.get("as_of"),
            "reason": reading.get("reason"),
        })
    layers = ((pack.get("liquilens") or {}).get("layers") or {})
    available = [name for name, row in layers.items()
                 if row.get("available", True) is not False]
    unavailable = [name for name, row in layers.items()
                   if row.get("available", True) is False]
    as_of = max((str(row.get("as_of")) for row in layers.values()
                 if row.get("as_of")), default=None)
    rows.append({"product": "liquilens", "available": bool(available),
                 "as_of": as_of, "layers_available": available,
                 "layers_unavailable": unavailable})
    return rows


def fallback_answer(pack: dict) -> str:
    """Useful no-LLM response; deliberately refuses a semantic connection."""
    seiche = pack.get("seiche") or {}
    comp = seiche.get("composite") or {}
    s_bits = [str(comp.get("regime") or "unavailable")]
    if comp.get("value") is not None:
        s_bits.append(f"{comp['value']}/100")

    undertow = pack.get("undertow") or {}
    funding = undertow.get("funding") or {}
    tiers = [f"{name} {cell.get('tier')}" for name, cell in
             (undertow.get("segments") or {}).items()]
    u_text = (f"funding {funding.get('regime') or 'unavailable'}"
              + (f"; {', '.join(tiers)}" if tiers else ""))

    layer_bits = []
    for name, row in (((pack.get("liquilens") or {}).get("layers")) or {}).items():
        state = row.get("regime") or row.get("badge")
        if state:
            layer_bits.append(f"{name} {state}"
                              + (" (stale)" if row.get("stale") else ""))
        elif row.get("available") is False:
            layer_bits.append(f"{name} unavailable")

    return (
        f"Seiche reads {' at '.join(s_bits)} ({seiche.get('as_of') or '?'}). "
        f"Undertow reads {u_text} ({undertow.get('as_of') or '?'}). "
        f"LiquiLens layers: {', '.join(layer_bits) or 'no layer summary available'}. "
        "The readings alone do not establish that they caused, predicted, or "
        "validated the supplied event. The explanation layer is unavailable, "
        "so no semantic connection is being invented."
    )


async def analyze(question: str, snapshot: dict,
                  raw_fleet: dict[str, dict] | None = None) -> dict:
    pack = await event_context(question, snapshot, raw_fleet=raw_fleet)
    messages = [
        {"role": "system", "content": EVENT_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps({
            "event_text_unverified": question,
            "fleet_reading_pack": pack,
        }, default=str)},
    ]
    errors = []
    for route, fn in (("free-llm-router", ai._via_router),
                      ("env-endpoint", ai._via_env)):
        try:
            answer = await fn(messages)
            if answer:
                return {
                    "ok": True,
                    "mode": "readings_only_event_connection",
                    "route": route,
                    "answer": ai._strip_reasoning(answer),
                    "grounding": (
                        "event text treated as unverified; analysis restricted "
                        "to live Seiche, Undertow and LiquiLens reading packs"
                    ),
                    "sources": source_status(pack),
                }
        except Exception as exc:  # noqa: BLE001 - try the next configured route
            errors.append(f"{route}: {type(exc).__name__}: {str(exc)[:80]}")
    return {
        "ok": False,
        "mode": "readings_only_event_connection",
        "answer": fallback_answer(pack),
        "reason": "no LLM route available (" + (
            "; ".join(errors) if errors else
            "free-llm-router unkeyed and SEICHE_LLM_BASE_URL unset"
        ) + ")",
        "grounding": (
            "deterministic reading summary only; semantic connection withheld"
        ),
        "sources": source_status(pack),
    }
