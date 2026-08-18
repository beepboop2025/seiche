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
import hashlib
import json
import math
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

# Every network and model boundary has an explicit byte ceiling.  The sibling
# APIs are trusted fleet services, but a proxy error or schema regression must
# not turn one public question into an unbounded allocation or model request.
MAX_UPSTREAM_RESPONSE_BYTES = 512 * 1024
MAX_READING_PACK_BYTES = 48 * 1024
MAX_MODEL_ENVELOPE_BYTES = 64 * 1024
# Wire bytes are capped independently from the much smaller structured
# contract accepted below.  The cap is enforced while streaming, before JSON
# decoding or joining response chunks.
MAX_MODEL_HTTP_RESPONSE_BYTES = 64 * 1024
MAX_PROVIDER_OUTPUT_BYTES = 8 * 1024
MAX_EVENT_TEXT_CHARS = 1_200
MAX_ENTITY_MATCHES = 4
MAX_STRUCTURED_CLAIMS = 4

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

# Required fields come from each public LiquiLens endpoint's actual response
# contract.  Optional fields are validated when present so that a sibling
# schema change cannot be silently rendered as an empty/healthy reading.
_LIQUILENS_REQUIRED_TYPES = {
    "failure_radar": {"rows": list, "tiers": dict},
    "rails": {"rows": list, "aggregate": dict},
    "bondholders": {"us": dict, "uk": dict},
    "deposit_migration": {"channels": dict, "coverage": dict},
    "bond_book": {
        "rows": list, "aggregate": dict, "counts": dict, "nowcast": dict,
    },
    "short_pressure": {"us_rows": list, "uk_rows": list, "eu_rows": list},
    "market_makers": {"live": dict},
    "leverage": {"markets": list, "breadth": dict},
    "tbtf": {"rows": list, "flagged": dict},
    "crypto_exposure": {"rows": list, "compound_flags": list},
}
_LIQUILENS_OPTIONAL_TYPES = {
    "failure_radar": {"excluded_stale": list},
    "deposit_migration": {"cannot_see": list},
    "short_pressure": {
        "compound_flags": list, "coverage": dict, "form_sho": dict,
    },
    "market_makers": {"regime_notes": list, "break_mismatch": bool},
    "leverage": {
        "leverage": dict, "stale_detail": dict, "countries": list,
    },
}

_RAW_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_FLEET_LOCK = asyncio.Lock()


EVENT_SYSTEM_PROMPT = """You are the fleet reading selector for three instruments:
SEICHE reads US-dollar funding plumbing, UNDERTOW reads cross-market liquidity,
and LIQUILENS reads institutions and transmission layers.

You receive EVENT TEXT supplied by the user and a FLEET READING PACK.

Hard rules:
1. Treat EVENT TEXT as an UNVERIFIED CLAIM. Do not add facts, dates, motives,
   history, or identities that are absent from the event text or reading pack.
2. The FLEET READING PACK is the only source of market or institution facts and
   every number. Never use memory or outside market knowledge.
3. Select only evidence IDs that directly help describe what a relevant
   product supports, does not confirm, or cannot see. Do not force a connection.
4. Never claim that a reading caused, predicted, or explains the event. Describe
   a transmission mechanism only when the pack directly measures its links.
   Current readings are context, not evidence about an earlier decision.
5. Respect chronology. If the event date is missing, or differs from a reading's
   as-of date, state that the causal timing cannot be tested.
6. Official published tiers outrank candidate tiers. If a cell is PARTIAL, it
   stays PARTIAL; a candidate score may be named only as candidate/withheld.
7. Stale, unavailable, partial, dark, and absent are not CALM. State material
   coverage limits, validation gates, and unavailable sources.
8. Return ONLY one JSON object with this exact shape and no extra keys:
   {"verdict":"context_only|not_confirmed|insufficient_readings",
    "claims":[{"evidence_id":"AN_ALLOWED_ID",
               "relationship":"supports_context|does_not_confirm|cannot_see"}],
    "limitations":["event_unverified","causality_not_established",
                   "timing_not_testable", ...]}
9. claims must contain 1-4 distinct allowed evidence IDs.  Never emit claim
   prose, names, handles, event text, citations not present in the pack, or any
   other string. limitations may additionally contain only
   stale_or_partial_coverage and unavailable_sources.
10. event_unverified, causality_not_established, and timing_not_testable are
    mandatory. Output raw JSON only: no Markdown fence, explanation, or prose."""


def _safe_text(value: Any, limit: int = 500) -> str:
    text = str(value).replace("\x00", " ").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    """Return a deterministic JSON-safe value with strict local fan-out caps."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _safe_text(value)
    if depth >= 4:
        return _safe_text(value, 160)
    if isinstance(value, dict):
        out = {}
        for key in sorted(value, key=lambda item: str(item))[:32]:
            safe_key = _safe_text(key, 80)
            out[safe_key] = _bounded_value(value[key], depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth=depth + 1) for item in value[:16]]
    return _safe_text(value, 160)


def _pick(value: Any, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        return {}
    return {key: _bounded_value(value.get(key)) for key in keys if key in value}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, default=str, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _unavailable(source: str, reason: str, *, evidence_id: str,
                 status: Any = None) -> dict:
    out = {
        "evidence_id": evidence_id,
        "source": source,
        "available": False,
        "reading": "UNAVAILABLE",
        "reason": _safe_text(reason, 240),
    }
    if isinstance(status, int):
        out["status"] = status
    return out


def _slug(value: Any, fallback: str = "reading") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return (slug[:64].rstrip("-") or fallback)


def compact_seiche(snapshot: dict) -> dict:
    """Small event-facing view of the canonical Seiche context pack."""
    pack = ai.context_pack(snapshot if isinstance(snapshot, dict) else {})
    generated = str(pack.get("generated_at") or "")
    return {
        "evidence_id": "seiche:board",
        "source": "https://api.seiche.info/api/gauge",
        "as_of": generated[:10] or None,
        "generated_at": _bounded_value(pack.get("generated_at")),
        "composite": _bounded_value(pack.get("composite")),
        "headline": _bounded_value(pack.get("headline")),
        "tell": _bounded_value(pack.get("tell")),
        "next_turn": _bounded_value(pack.get("next_turn")),
        "ml": _bounded_value(pack.get("ml")),
        "stacker": _bounded_value(pack.get("stacker")),
        "kink": _bounded_value(pack.get("kink")),
        "weather_crunches": _bounded_value(pack.get("weather_crunches")),
        "moorings": _bounded_value(pack.get("moorings")),
        "funding_pop": _bounded_value(pack.get("funding_pop")),
        "faults": _bounded_value(pack.get("faults")),
        "provenance_staleness": _bounded_value(pack.get("provenance_staleness")),
    }


def compact_undertow(board: dict | None) -> dict:
    evidence_id = "undertow:board"
    if not isinstance(board, dict):
        return _unavailable(UNDERTOW_BOARD_URL, "board unavailable",
                            evidence_id=evidence_id)
    if board.get("_error"):
        return _unavailable(
            UNDERTOW_BOARD_URL,
            board.get("detail") or board.get("_error"),
            evidence_id=evidence_id, status=board.get("_status"),
        )

    raw_segments = board.get("segments")
    funding = board.get("funding")
    if not isinstance(raw_segments, (dict, list)) or not isinstance(funding, dict):
        return _unavailable(
            UNDERTOW_BOARD_URL, "invalid board schema",
            evidence_id=evidence_id,
        )
    if isinstance(raw_segments, list):
        raw_segments = {
            _safe_text(row.get("segment") or "?", 80): row
            for row in raw_segments[:16]
            if isinstance(row, dict)
        }
    segments = {}
    for name in sorted(raw_segments, key=lambda item: str(item))[:16]:
        cell = raw_segments[name]
        if not isinstance(cell, dict):
            continue
        measures = []
        raw_measures = cell.get("measures")
        if not isinstance(raw_measures, list):
            raw_measures = []
        for measure in raw_measures[:2]:
            measures.append(_pick(measure, (
                "measure", "stress_pctl", "stress_pctl_withheld", "obs",
                "asof", "span_days", "limits", "caveat",
            )))
        replay = cell.get("validation_replay")
        replay = replay if isinstance(replay, dict) else {}
        safe_name = _safe_text(name, 80)
        segments[safe_name] = {
            "evidence_id": f"undertow:segment:{_slug(safe_name)}",
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

    provenance = board.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    freshness = provenance.get("freshness")
    freshness = freshness if isinstance(freshness, dict) else {}
    return {
        "evidence_id": evidence_id,
        "source": UNDERTOW_BOARD_URL,
        "available": True,
        "as_of": _bounded_value(board.get("asof")),
        "generated_at": _bounded_value(provenance.get("generated_at")),
        "public_subset": _bounded_value(board.get("public_subset")),
        "funding": _bounded_value(funding),
        "segments": segments,
        "upstream_observation_dates": _bounded_value(freshness.get("upstream_inputs")),
        "input_health": _bounded_value(provenance.get("input_health")),
    }


_ENTITY_KEYS = (
    "name", "bank", "issuer", "holdco", "slug", "ticker", "isin", "label",
)
_ENTITY_STOP = {
    "bank", "banks", "banking", "national", "association", "financial",
    "finance", "group", "holdings", "holding", "company", "corporation",
    "limited", "ltd", "plc", "inc", "the", "and", "trust", "state",
    "capital", "first", "city", "customers",
}
_AMBIGUOUS_SINGLE_TOKENS = {
    "america", "american", "chase", "india", "global", "international",
    "united", "citizens", "regions", "key", "ally", "fit", "all",
} | _ENTITY_STOP
_EXPLICIT_IDENTIFIER_KEYS = {"ticker", "isin", "slug"}
_LEGAL_SUFFIX_WORDS = {
    "national", "association", "limited", "ltd", "plc", "inc",
    "company", "corporation", "holdings", "holding", "corp", "llc",
    "ag", "sa", "nv", "se",
}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower())
            if len(token) >= 3}


def _alias_items(row: dict) -> list[tuple[str, str]]:
    aliases = [(key, str(row.get(key))) for key in _ENTITY_KEYS
               if row.get(key) not in (None, "")]
    nested = row.get("join_keys")
    if isinstance(nested, dict):
        for key in ("name", "slug", "ticker", "isin", "bank", "holdco"):
            value = nested.get(key)
            if isinstance(value, str) and value:
                aliases.append((key, value))
    return aliases


def _aliases(row: dict) -> list[str]:
    return [value for _, value in _alias_items(row)]


def _contains_token_sequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[index:index + len(needle)] == needle
               for index in range(len(haystack) - len(needle) + 1))


def _compact_sequences(words: list[str], max_words: int = 4) -> set[str]:
    """Contiguous compact forms, e.g. JP Morgan/J.P. Morgan -> jpmorgan."""
    forms = set(words)
    for start in range(len(words)):
        for width in range(2, min(max_words, len(words) - start) + 1):
            forms.add("".join(words[start:start + width]))
    return forms


def _brand_forms(alias_words: list[str]) -> list[tuple[list[str], int]]:
    """Return full and clearly bounded brand forms with match strengths."""
    core = list(alias_words)
    while core and core[-1] in _LEGAL_SUFFIX_WORDS:
        core.pop()
    forms = [(core, 100)] if core else []

    # Long legal display names commonly append "Bank" and an ambiguous brand
    # component (JPMorgan Chase Bank, N.A.).  Removing them is safe only when a
    # distinctive brand remains; it must never turn "Deutsche Bank" into the
    # weak token "Deutsche".
    brand = list(core)
    if len(brand) > 2 and brand[-1] in {"bank", "banks", "group"}:
        brand.pop()
    while (len(brand) > 1
           and brand[-1] in _AMBIGUOUS_SINGLE_TOKENS
           and any(word not in _AMBIGUOUS_SINGLE_TOKENS
                   for word in brand[:-1])):
        brand.pop()
    if brand and brand != core:
        forms.append((brand, 95))
    return forms


def _entity_match_score(row: dict, question: str) -> int:
    """Rank only exact/strong aliases and explicit identifiers.

    A score is used rather than a boolean so exact names and compact spelling
    variants outrank a fallback distinctive-token match.  A caller may then
    drop weak candidates whenever an exact candidate exists.
    """
    if not isinstance(row, dict) or not isinstance(question, str):
        return 0
    q_words = re.findall(r"[a-z0-9]+", question.lower())
    q_compact = _compact_sequences(q_words)
    q_tokens = _tokens(question) - _ENTITY_STOP
    best = 0
    for kind, alias in _alias_items(row):
        alias_words = re.findall(r"[a-z0-9]+", alias.lower())
        if not alias_words:
            continue

        if kind in _EXPLICIT_IDENTIFIER_KEYS:
            literal = re.escape(alias)
            explicit = re.search(
                rf"(?<![A-Za-z0-9]){literal}(?![A-Za-z0-9])",
                question, flags=re.IGNORECASE,
            )
            if explicit and (kind != "ticker"
                             or alias.lower() not in _AMBIGUOUS_SINGLE_TOKENS
                             or re.search(
                                 rf"(?<![A-Za-z0-9]){re.escape(alias.upper())}"
                                 rf"(?![A-Za-z0-9])", question)):
                best = max(best, 90)
            # Slugs are already sibling-published canonical identifiers. Match
            # their full compact form so `jpmorgan` also recognizes JP Morgan
            # and J.P. Morgan, without introducing prefix/substr matching.
            if (kind == "slug" and len("".join(alias_words)) >= 6
                    and "".join(alias_words) in q_compact):
                best = max(best, 90)
            continue

        # Full names and compact punctuation/spacing variants are strongest.
        # Exact compact equality, not substring containment, is what makes
        # JPMorgan, JP Morgan, and J.P. Morgan equivalent.
        for brand_words, score in _brand_forms(alias_words):
            single_is_distinctive = (
                len(brand_words) == 1
                and brand_words[0] not in _AMBIGUOUS_SINGLE_TOKENS
                and len(brand_words[0]) >= 4
            )
            if ((len(brand_words) >= 2 or single_is_distinctive)
                    and (_contains_token_sequence(q_words, brand_words)
                         or "".join(brand_words) in q_compact)):
                best = max(best, score)

        significant = _tokens(alias) - _ENTITY_STOP
        overlap = significant & q_tokens
        if len(overlap) >= 2:
            best = max(best, 80)

        # One token is accepted only when it is genuinely distinctive.  This
        # keeps "JPMorgan"/"Barclays" useful while rejecting America, city,
        # first, capital, Chase, and other ordinary one-word collisions.  It is
        # also allowed through a compact question form (JP Morgan -> JPMorgan),
        # but only when it is the alias's sole distinctive token.  Thus an
        # exact Deutsche Bank row cannot drag in Deutsche Pfandbriefbank.
        distinctive = significant - _AMBIGUOUS_SINGLE_TOKENS
        compact_overlap = {token for token in distinctive if token in q_compact}
        if len(distinctive) == 1 and compact_overlap:
            token = next(iter(distinctive))
            if (len(token) >= 7
                    or re.search(rf"(?<![A-Za-z0-9]){re.escape(token.upper())}"
                                 rf"(?![A-Za-z0-9])", question)):
                best = max(best, 70)

    cert = row.get("cert")
    if isinstance(cert, (int, str)) and str(cert).isdigit() and re.search(
            rf"\b(?:cert|certificate)\s*(?:number|no\.?|#)?\s*{re.escape(str(cert))}\b",
            question, flags=re.IGNORECASE):
        best = max(best, 90)
    return best


def row_matches_question(row: dict, question: str) -> bool:
    """Conservative public predicate for entity-to-question joins."""
    return _entity_match_score(row, question) > 0


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
        "name", "slug", "bank", "issuer", "cert", "holdco", "ticker",
        "isin", "country", "exchange", "pressure_state", "state", "reasons",
        "short_interest",
        "short_volume", "panic_volume_marker", "undertow_score_v02",
        "compound_flag", "compound_note", "run_risk_note", "named",
        "named_total_pct", "n_holders", "new_entrants_30d", "position_date",
        "register", "attribution",
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
        rows = []
        for key in ("us_rows", "uk_rows", "eu_rows"):
            value = data.get(key)
            if isinstance(value, list):
                rows.extend(value)
    else:
        value = data.get("rows")
        rows = value if isinstance(value, list) else []
    fields = _ROW_FIELDS.get(name, _ENTITY_KEYS)
    ranked = []
    for index, row in enumerate(rows):
        score = _entity_match_score(row, question) if isinstance(row, dict) else 0
        if score:
            ranked.append((score, index, row))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if ranked and ranked[0][0] >= 80:
        ranked = [item for item in ranked if item[0] >= 80]

    out = []
    for _, _, row in ranked[:MAX_ENTITY_MATCHES]:
        picked = _pick(row, fields)
        identity = "|".join(_aliases(row)) or json.dumps(
            _pick(row, ("cert", "isin")), sort_keys=True, default=str,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        picked["evidence_id"] = f"liquilens:{name}:entity:{digest}"
        out.append(picked)
    return out


def _liquilens_schema_reason(name: str, data: dict) -> str | None:
    expected = {
        **_LIQUILENS_REQUIRED_TYPES[name],
        **_LIQUILENS_OPTIONAL_TYPES.get(name, {}),
    }
    required = _LIQUILENS_REQUIRED_TYPES[name]
    for field in required:
        if field not in data:
            return f"invalid reading schema: {name}.{field} is missing"
    for field, field_type in expected.items():
        if field in data and not isinstance(data[field], field_type):
            kind = "array" if field_type is list else (
                "object" if field_type is dict else field_type.__name__)
            return f"invalid reading schema: {name}.{field} must be an {kind}"
    return None


def _without_history(row: Any) -> Any:
    if not isinstance(row, dict):
        return _bounded_value(row)
    return _bounded_value({
        key: value for key, value in row.items()
        if key not in {"history", "series", "citations", "method_note"}
    })


def compact_liquilens_layer(name: str, data: dict | None,
                            question: str) -> dict:
    source = LIQUILENS_API + _LIQUILENS_PATHS[name]
    evidence_id = f"liquilens:{name}"
    if not isinstance(data, dict):
        return _unavailable(source, "reading unavailable",
                            evidence_id=evidence_id)
    if data.get("_error"):
        return _unavailable(
            source, data.get("detail") or data.get("_error"),
            evidence_id=evidence_id, status=data.get("_status"),
        )
    if data.get("available") is False:
        # Keep dates/staleness, but do not copy a live regime word. An
        # unavailable sibling is UNAVAILABLE, never CALM.
        return {
            **_unavailable(source, data.get("reason") or "reading unavailable",
                           evidence_id=evidence_id),
            **_pick(data, ("as_of", "stale")),
        }
    schema_reason = _liquilens_schema_reason(name, data)
    if schema_reason:
        return _unavailable(source, schema_reason,
                            evidence_id=evidence_id)

    out = {
        "evidence_id": evidence_id,
        "source": source,
        "available": True,
        **_pick(data, (
            "as_of", "available", "stale", "regime", "regime_reasons",
            "badge", "data_asof", "data_lag_days", "quarter",
            "quarter_stale", "quarter_age_days", "refresh_due",
        )),
    }
    method_note = data.get("method_note")
    if method_note:
        out["method_note"] = _safe_text(method_note, 500)

    if name == "failure_radar":
        out.update(_pick(data, ("tiers", "excluded_stale", "quadrant_rule")))
    elif name == "rails":
        out["aggregate"] = _bounded_value(data.get("aggregate"))
        rows = data.get("rows")
        rows = rows if isinstance(rows, list) else []
        out["rows"] = [_without_history(row) for row in rows[:12]]
    elif name == "bondholders":
        out["us"] = _pick(data.get("us") or {}, ("state", "tff", "cayman"))
        out["uk"] = _pick(data.get("uk") or {}, (
            "state", "flags", "latest", "register_as_of", "register_stale",
            "stale_reason", "trajectory", "concentration",
        ))
    elif name == "deposit_migration":
        out["channels"] = _bounded_value(data.get("channels"))
        out["coverage"] = _bounded_value(data.get("coverage"))
        out["cannot_see"] = _bounded_value(data.get("cannot_see"))
    elif name == "bond_book":
        out.update(_pick(data, ("aggregate", "counts", "nowcast",
                                "quarter_mismatch")))
    elif name == "short_pressure":
        out["compound_flags"] = _bounded_value(data.get("compound_flags"))
        out["coverage"] = _bounded_value(data.get("coverage"))
        out["form_sho"] = _bounded_value(data.get("form_sho"))
    elif name == "market_makers":
        out["live"] = _bounded_value(data.get("live"))
        out["regime_notes"] = _bounded_value(data.get("regime_notes"))
        out["break_mismatch"] = _bounded_value(data.get("break_mismatch"))
    elif name == "leverage":
        out["breadth"] = _bounded_value(data.get("breadth"))
        markets = data.get("markets")
        markets = markets if isinstance(markets, list) else []
        out["markets"] = [_without_history(row) for row in markets[:10]]
        out["stale_detail"] = _bounded_value(data.get("stale_detail"))
    elif name == "tbtf":
        out["flagged"] = _bounded_value(data.get("flagged"))
        out["resolution_gaps_2026"] = _bounded_value(
            data.get("resolution_gaps_2026"))
    elif name == "crypto_exposure":
        out["compound_flags"] = _bounded_value(data.get("compound_flags"))

    if name in _ROW_FIELDS:
        out["entity_matches"] = _matched_rows(name, data, question)
    return _bounded_value(out)


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict:
    try:
        async with client.stream("GET", url) as response:
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_UPSTREAM_RESPONSE_BYTES:
                    return {
                        "_error": "response exceeded byte budget",
                        "_status": response.status_code,
                    }
                chunks.append(chunk)
            raw = b"".join(chunks)
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
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
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        return {"_error": f"{type(exc).__name__}: {str(exc)[:160]}"}


class _ModelTransportError(RuntimeError):
    """The event-specific model transport rejected an unsafe response."""


# OpenRouter's free meta-model selects and fails over only among models whose
# price is zero.  Other providers are deliberately not inferred from ambient
# keys: a potentially billable endpoint is eligible only through the explicit
# SEICHE_LLM_BASE_URL route below.
_FREE_EVENT_ROUTES = (
    {
        "name": "openrouter-free",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "model": "openrouter/free",
        "extra_body": {"provider": {"allow_fallbacks": True}},
    },
)


async def _capped_openai_chat(
        *, base_url: str, api_key: str, model: str,
        messages: list[dict], extra_body: dict | None = None) -> str:
    """Stream one OpenAI-compatible reply under a hard wire-byte ceiling."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 700,
    }
    if extra_body:
        payload.update(extra_body)
    headers = {"Accept-Encoding": "identity"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False) as client:
        async with client.stream(
                "POST", f"{base_url.rstrip('/')}/chat/completions",
                headers=headers, json=payload) as response:
            encoding = response.headers.get("content-encoding", "identity")
            if encoding.lower().strip() not in {"", "identity"}:
                raise _ModelTransportError(
                    "compressed model response was not accepted")
            length = response.headers.get("content-length")
            try:
                declared_length = int(length) if length is not None else None
            except ValueError as exc:
                raise _ModelTransportError(
                    "model response content-length was invalid") from exc
            if (declared_length is not None
                    and declared_length > MAX_MODEL_HTTP_RESPONSE_BYTES):
                raise _ModelTransportError(
                    "model response exceeded wire byte budget")

            chunks = []
            size = 0
            async for chunk in response.aiter_raw(chunk_size=8 * 1024):
                size += len(chunk)
                if size > MAX_MODEL_HTTP_RESPONSE_BYTES:
                    raise _ModelTransportError(
                        "model response exceeded wire byte budget")
                chunks.append(chunk)
            raw = b"".join(chunks)
            response.raise_for_status()

    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise _ModelTransportError("model response was not JSON") from exc
    if not isinstance(body, dict):
        raise _ModelTransportError("model response was not an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _ModelTransportError("model response choices were invalid")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise _ModelTransportError("model response content was not text")
    return content


async def _via_event_free_router(messages: list[dict]) -> str | None:
    """Use only reviewed free routes; OpenRouter performs free-model failover."""
    last_error: Exception | None = None
    attempted = False
    for route in _FREE_EVENT_ROUTES:
        key = os.environ.get(route["key_env"], "")
        if not key:
            continue
        attempted = True
        try:
            return await _capped_openai_chat(
                base_url=route["base_url"], api_key=key,
                model=route["model"], messages=messages,
                extra_body=route.get("extra_body"),
            )
        except Exception as exc:  # noqa: BLE001 - try the next reviewed route
            last_error = exc
    if attempted and last_error:
        raise last_error
    return None


async def _via_event_env(messages: list[dict]) -> str | None:
    """Use the explicitly configured OpenAI-compatible endpoint, if any."""
    base_url = os.environ.get("SEICHE_LLM_BASE_URL")
    if not base_url:
        return None
    return await _capped_openai_chat(
        base_url=base_url,
        api_key=os.environ.get("SEICHE_LLM_API_KEY", ""),
        model=os.environ.get("SEICHE_LLM_MODEL", "gpt-4o-mini"),
        messages=messages,
    )


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


_CORE_READING_KEYS = (
    "evidence_id", "source", "available", "reason", "status", "as_of",
    "generated_at", "stale", "regime", "badge", "quarter", "data_asof",
    "data_lag_days",
)


def _minimal_reading(reading: Any) -> dict:
    if not isinstance(reading, dict):
        return {"available": False, "reason": "invalid compact reading"}
    out = _pick(reading, _CORE_READING_KEYS)
    composite = reading.get("composite")
    if isinstance(composite, dict):
        out["composite"] = _pick(
            composite, ("regime", "value", "coverage_pct", "dead_inputs"))
    funding = reading.get("funding")
    if isinstance(funding, dict):
        out["funding"] = _pick(funding, ("regime", "score", "asof"))
    segments = reading.get("segments")
    if isinstance(segments, dict):
        out["segments"] = {
            _safe_text(name, 80): _pick(cell, (
                "evidence_id", "tier", "score", "candidate_tier",
                "candidate_score", "score_withheld_reason",
            ))
            for name, cell in sorted(segments.items(), key=lambda item: str(item[0]))[:12]
            if isinstance(cell, dict)
        }
    return out


def _fit_reading_pack(pack: dict, max_bytes: int = MAX_READING_PACK_BYTES) -> dict:
    """Deterministically replace the largest readings with fail-closed summaries."""
    bounds = {
        "max_serialized_bytes": max_bytes,
        "pruned": False,
        "omitted": [],
        "omitted_count": 0,
    }
    pack["bounds"] = bounds
    if len(_json_bytes(pack)) <= max_bytes:
        return pack

    candidates: list[tuple[int, str, dict, str]] = []
    for product in ("seiche", "undertow"):
        reading = pack.get(product)
        if isinstance(reading, dict):
            candidates.append((len(_json_bytes(reading)), product, pack, product))
    layers = ((pack.get("liquilens") or {}).get("layers")
              if isinstance(pack.get("liquilens"), dict) else None)
    if isinstance(layers, dict):
        for name, reading in layers.items():
            if isinstance(reading, dict):
                path = f"liquilens.layers.{name}"
                candidates.append((len(_json_bytes(reading)), path, layers, name))

    omitted = []
    for _, path, parent, key in sorted(candidates,
                                       key=lambda item: (-item[0], item[1])):
        original = parent[key]
        minimal = _minimal_reading(original)
        if len(_json_bytes(minimal)) >= len(_json_bytes(original)):
            continue
        parent[key] = minimal
        omitted.append(path + ".details")
        if len(_json_bytes(pack)) <= max_bytes:
            break

    bounds.update({
        "pruned": bool(omitted),
        "omitted": omitted[:16],
        "omitted_count": len(omitted),
    })
    if len(_json_bytes(pack)) <= max_bytes:
        return pack

    # The core-only representation is the last safe shape.  It retains every
    # product's availability/date and explicit partial/unavailable status.
    minimal_layers = {
        name: _minimal_reading(reading)
        for name, reading in sorted((layers or {}).items())
    }
    minimal_pack = {
        "contract": pack.get("contract"),
        "event_text_status": pack.get("event_text_status"),
        "seiche": _minimal_reading(pack.get("seiche")),
        "undertow": _minimal_reading(pack.get("undertow")),
        "liquilens": {
            "scope": "institution and transmission screens; screens, not ratings",
            "layers": minimal_layers,
        },
        "bounds": {
            "max_serialized_bytes": max_bytes,
            "pruned": True,
            "omitted": ["all optional reading details"],
            "omitted_count": max(len(omitted), 1),
        },
    }
    if len(_json_bytes(minimal_pack)) > max_bytes:
        # This can happen only with an artificially tiny caller-supplied budget;
        # fail closed instead of returning an over-budget model input.
        return {
            "contract": "readings_only_event_connection.v1",
            "event_text_status": "user-supplied, unverified; not a reading",
            "seiche": _unavailable("https://api.seiche.info/api/gauge",
                                    "reading pack exceeded byte budget",
                                    evidence_id="seiche:board"),
            "undertow": _unavailable(UNDERTOW_BOARD_URL,
                                      "reading pack exceeded byte budget",
                                      evidence_id="undertow:board"),
            "liquilens": {"scope": "screens unavailable after bounded compaction",
                           "layers": {}},
            "bounds": {"max_serialized_bytes": max_bytes, "pruned": True,
                       "omitted": ["reading pack"], "omitted_count": 1},
        }
    return minimal_pack


async def event_context(question: str, snapshot: dict,
                        raw_fleet: dict[str, dict] | None = None) -> dict:
    raw = raw_fleet if raw_fleet is not None else await _raw_fleet()
    liquilens = {
        name: compact_liquilens_layer(name, raw.get(name), question)
        for name in _LIQUILENS_PATHS
    }
    pack = {
        "contract": "readings_only_event_connection.v1",
        "event_text_status": "user-supplied, unverified; not a reading",
        "seiche": compact_seiche(snapshot),
        "undertow": compact_undertow(raw.get("undertow")),
        "liquilens": {
            "scope": "institution and transmission screens; screens, not ratings",
            "layers": liquilens,
        },
    }
    return _fit_reading_pack(pack)


async def liquilens_desk_sibling(
        question: str,
        raw_fleet: dict[str, dict] | None = None) -> dict:
    """Fail-closed LiquiLens screens for institution or tandem desk questions.

    Reuses the same REST paths and schema compaction as event analysis. A
    missing or drifted sibling is UNAVAILABLE. This is not a joint score.
    """
    raw = raw_fleet if raw_fleet is not None else await _raw_fleet()
    layers = {
        name: compact_liquilens_layer(name, raw.get(name), question)
        for name in _LIQUILENS_PATHS
    }
    status = source_status({
        "seiche": {},
        "undertow": {},
        "liquilens": {"layers": layers},
    })[-1]
    return {
        "scope": "institution and transmission screens; screens, not ratings",
        "sibling_rule": "unavailable is UNAVAILABLE, never CALM; no joint score",
        "layers": layers,
        "source_status": {
            "product": "liquilens",
            "available": status["available"],
            "as_of": status.get("as_of"),
            "layers_available": status["layers_available"],
            "layers_unavailable": status["layers_unavailable"],
            "layer_reasons": status.get("layer_reasons") or {},
        },
    }


_TELEGRAM_LINK = re.compile(r"https?://(?:www\.)?t\.me/[^\s]+", re.IGNORECASE)
_HANDLE = re.compile(r"(?<![A-Za-z0-9])@[A-Za-z0-9_]{3,64}\b")
_TELEGRAM_ID_LINE = re.compile(
    r"(?im)^\s*(?:forwarded\s+from|telegram\s+(?:user|sender|chat)(?:\s+id)?)"
    r"\s*[:=-].*$"
)


def _sanitized_event_text(question: Any) -> str:
    text = _safe_text(question, MAX_EVENT_TEXT_CHARS)
    text = _TELEGRAM_LINK.sub("[telegram-link-redacted]", text)
    text = _HANDLE.sub("[handle-redacted]", text)
    text = _TELEGRAM_ID_LINE.sub("[telegram-identity-redacted]", text)
    return text[:MAX_EVENT_TEXT_CHARS]


def _format_fact_value(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float, str)):
        return _safe_text(value, 120)
    if isinstance(value, list):
        items = [_format_fact_value(item) for item in value[:3]]
        return ", ".join(item for item in items if item)[:180] or None
    if isinstance(value, dict):
        items = []
        for key in sorted(value)[:4]:
            rendered = _format_fact_value(value[key])
            if rendered:
                items.append(f"{_safe_text(key, 32)}={rendered}")
        return "; ".join(items)[:180] or None
    return _safe_text(value, 120)


def _fact_parts(row: Any, keys: tuple[str, ...], limit: int = 6) -> list[str]:
    if not isinstance(row, dict):
        return []
    parts = []
    for key in keys:
        rendered = _format_fact_value(row.get(key))
        if rendered:
            parts.append(f"{key.replace('_', ' ')} {rendered}")
        if len(parts) >= limit:
            break
    return parts


def _clip_words(text: str, limit: int = 18) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]).rstrip(".,;") + "…"


def _evidence_registry(pack: dict) -> dict[str, dict]:
    """Map only IDs physically present in the bounded pack to exact facts."""
    registry: dict[str, dict] = {}

    def add(reading: Any, *, product: str, label: str, summary: str) -> None:
        if not isinstance(reading, dict):
            return
        evidence_id = reading.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            return
        as_of = reading.get("as_of") or reading.get("data_asof")
        date = _safe_text(as_of, 32) if as_of else "date unavailable"
        registry[evidence_id] = {
            "product": product,
            "available": reading.get("available", True) is not False,
            "as_of": date,
            "summary": _clip_words(summary or "No summarized fields are available."),
            "citation": f"({label}, {date}; {evidence_id})",
        }

    seiche = pack.get("seiche")
    seiche = seiche if isinstance(seiche, dict) else {}
    composite = seiche.get("composite")
    s_parts = _fact_parts(
        composite, ("regime", "value", "coverage_pct", "dead_inputs"), 4)
    add(seiche, product="seiche", label="Seiche",
        summary="Seiche composite: " + ("; ".join(s_parts) or "unavailable"))

    undertow = pack.get("undertow")
    undertow = undertow if isinstance(undertow, dict) else {}
    u_parts = _fact_parts(undertow.get("funding"), ("regime", "score", "asof"), 3)
    if undertow.get("available") is False:
        u_parts = ["unavailable: " + _safe_text(undertow.get("reason"), 100)]
    add(undertow, product="undertow", label="Undertow",
        summary="Undertow funding: " + ("; ".join(u_parts) or "unavailable"))
    segments = undertow.get("segments")
    if isinstance(segments, dict):
        for name, cell in sorted(segments.items()):
            parts = _fact_parts(cell, (
                "tier", "score", "candidate_tier", "candidate_score",
                "score_withheld_reason", "n_qualifying",
            ))
            add(cell, product="undertow", label="Undertow",
                summary=f"Undertow {name}: " + ("; ".join(parts) or "no scored fields"))

    liquilens = pack.get("liquilens")
    layers = liquilens.get("layers") if isinstance(liquilens, dict) else {}
    if isinstance(layers, dict):
        for name, layer in sorted(layers.items()):
            if not isinstance(layer, dict):
                continue
            parts = _fact_parts(layer, (
                "regime", "badge", "stale", "quarter", "data_lag_days",
                "reason",
            ))
            add(layer, product="liquilens", label=f"LiquiLens {name}",
                summary=f"LiquiLens {name}: " + ("; ".join(parts) or "no summary fields"))
            matches = layer.get("entity_matches")
            if not isinstance(matches, list):
                continue
            for row in matches:
                parts = _fact_parts(row, (
                    "name", "bank", "issuer", "holdco", "ticker", "isin",
                    "slug", "cert", "country", "state", "tier", "grade",
                    "hazard", "pressure_state", "compound_state", "status",
                    "exposure", "score", "compound_flag",
                ))
                add(row, product="liquilens", label=f"LiquiLens {name}",
                    summary=f"LiquiLens {name} matched entity: "
                            + ("; ".join(parts) or "identity unavailable"))
    return registry


class _InvalidGroundedContract(ValueError):
    pass


_VERDICTS = {"context_only", "not_confirmed", "insufficient_readings"}
_RELATIONSHIPS = {"supports_context", "does_not_confirm", "cannot_see"}
_LIMITATIONS = {
    "event_unverified", "causality_not_established", "timing_not_testable",
    "stale_or_partial_coverage", "unavailable_sources",
}
_REQUIRED_LIMITATIONS = {
    "event_unverified", "causality_not_established", "timing_not_testable",
}


def _required_pack_limitations(pack: dict) -> set[str]:
    required = set(_REQUIRED_LIMITATIONS)
    statuses = source_status(pack)
    if any(row.get("available") is False for row in statuses):
        required.add("unavailable_sources")

    partial = bool((pack.get("bounds") or {}).get("pruned"))
    seiche = pack.get("seiche")
    if isinstance(seiche, dict):
        staleness = seiche.get("provenance_staleness")
        if isinstance(staleness, dict):
            partial = partial or any(
                key != "fresh" and isinstance(count, (int, float)) and count > 0
                for key, count in staleness.items()
            )
    undertow = pack.get("undertow")
    segments = undertow.get("segments") if isinstance(undertow, dict) else {}
    if isinstance(segments, dict):
        partial = partial or any(
            isinstance(cell, dict) and cell.get("tier") in {"PARTIAL", "DARK"}
            for cell in segments.values()
        )
    liquilens = pack.get("liquilens")
    layers = liquilens.get("layers") if isinstance(liquilens, dict) else {}
    if isinstance(layers, dict):
        partial = partial or any(
            isinstance(layer, dict)
            and (layer.get("stale") is True or layer.get("quarter_stale") is True)
            for layer in layers.values()
        )
    if partial:
        required.add("stale_or_partial_coverage")
    return required


def _validated_provider_contract(raw: Any, registry: dict[str, dict],
                                 required_limitations: set[str]) -> dict:
    if not isinstance(raw, str):
        raise _InvalidGroundedContract("provider output was not text")
    if len(raw.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES:
        raise _InvalidGroundedContract("provider output exceeded byte budget")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise _InvalidGroundedContract("provider output was not raw JSON") from exc
    if not isinstance(value, dict) or set(value) != {"verdict", "claims", "limitations"}:
        raise _InvalidGroundedContract("provider output keys were invalid")
    if value.get("verdict") not in _VERDICTS:
        raise _InvalidGroundedContract("provider verdict was invalid")

    claims = value.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_STRUCTURED_CLAIMS:
        raise _InvalidGroundedContract("provider claims were invalid")
    seen = set()
    normalized_claims = []
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"evidence_id", "relationship"}:
            raise _InvalidGroundedContract("provider claim shape was invalid")
        evidence_id = claim.get("evidence_id")
        relationship = claim.get("relationship")
        if not isinstance(evidence_id, str) or evidence_id not in registry:
            raise _InvalidGroundedContract("provider cited unknown evidence")
        if evidence_id in seen or relationship not in _RELATIONSHIPS:
            raise _InvalidGroundedContract("provider claim value was invalid")
        if not registry[evidence_id]["available"] and relationship != "cannot_see":
            raise _InvalidGroundedContract("unavailable evidence was treated as affirmative")
        seen.add(evidence_id)
        normalized_claims.append({"evidence_id": evidence_id,
                                  "relationship": relationship})

    limitations = value.get("limitations")
    if (not isinstance(limitations, list)
            or any(not isinstance(item, str) for item in limitations)
            or len(set(limitations)) != len(limitations)
            or not set(limitations) <= _LIMITATIONS
            or not required_limitations <= set(limitations)):
        raise _InvalidGroundedContract("provider limitations were invalid")
    return {"verdict": value["verdict"], "claims": normalized_claims,
            "limitations": limitations}


def _render_verified_answer(contract: dict, registry: dict[str, dict]) -> str:
    verdicts = {
        "context_only": "The readings provide current context only; they do not verify the supplied event.",
        "not_confirmed": "The selected readings do not confirm the supplied event.",
        "insufficient_readings": "The available readings are insufficient to test the supplied event.",
    }
    relationships = {
        "supports_context": "This is relevant context, not validation or cause.",
        "does_not_confirm": "This reading does not confirm the event.",
        "cannot_see": "This reading cannot test the claimed link.",
    }
    limitation_text = {
        "event_unverified": "event text is user-supplied and unverified",
        "causality_not_established": "the readings do not establish causality",
        "timing_not_testable": "causal timing cannot be tested",
        "stale_or_partial_coverage": "some coverage is stale or partial",
        "unavailable_sources": "one or more sources are unavailable",
    }
    lines = ["The event text is user-supplied and unverified. "
             + verdicts[contract["verdict"]]]
    for claim in contract["claims"]:
        evidence = registry[claim["evidence_id"]]
        lines.append(
            f"- {evidence['summary']} {relationships[claim['relationship']]} "
            f"{evidence['citation']}"
        )
    limitations = "; ".join(limitation_text[item]
                            for item in contract["limitations"])
    lines.append("Limitations: " + limitations + ".")
    return "\n".join(lines)


def source_status(pack: dict) -> list[dict]:
    rows = []
    for product in ("seiche", "undertow"):
        reading = pack.get(product) or {}
        reading = reading if isinstance(reading, dict) else {}
        rows.append({
            "product": product,
            "available": reading.get("available", True),
            "as_of": reading.get("as_of"),
            "reason": reading.get("reason"),
        })
    layers = ((pack.get("liquilens") or {}).get("layers") or {})
    layers = layers if isinstance(layers, dict) else {}
    available = [name for name, row in layers.items()
                 if isinstance(row, dict)
                 and row.get("available", True) is not False]
    unavailable = [name for name, row in layers.items()
                   if not isinstance(row, dict)
                   or row.get("available", True) is False]
    layer_reasons = {
        name: _safe_text(
            (row.get("reason") if isinstance(row, dict) else None)
            or "invalid compact reading", 160,
        )
        for name, row in sorted(layers.items())
        if not isinstance(row, dict)
        or row.get("available", True) is False
    }
    as_of = max((str(row.get("as_of")) for row in layers.values()
                 if isinstance(row, dict) and row.get("as_of")), default=None)
    rows.append({"product": "liquilens", "available": bool(available),
                 "as_of": as_of, "layers_available": available,
                 "layers_unavailable": unavailable,
                 "layer_reasons": layer_reasons})
    return rows


def fallback_answer(pack: dict) -> str:
    """Useful no-LLM response; deliberately refuses a semantic connection."""
    seiche = pack.get("seiche") or {}
    seiche = seiche if isinstance(seiche, dict) else {}
    comp = seiche.get("composite") or {}
    comp = comp if isinstance(comp, dict) else {}
    s_bits = [str(comp.get("regime") or "unavailable")]
    if comp.get("value") is not None:
        s_bits.append(f"{comp['value']}/100")

    undertow = pack.get("undertow") or {}
    undertow = undertow if isinstance(undertow, dict) else {}
    funding = undertow.get("funding") or {}
    funding = funding if isinstance(funding, dict) else {}
    tiers = [f"{name} {cell.get('tier')}" for name, cell in
             (undertow.get("segments") or {}).items()
             if isinstance(cell, dict)] if isinstance(
                 undertow.get("segments") or {}, dict) else []
    u_text = (f"funding {funding.get('regime') or 'unavailable'}"
              + (f"; {', '.join(tiers)}" if tiers else ""))

    layer_bits = []
    for name, row in (((pack.get("liquilens") or {}).get("layers")) or {}).items():
        if not isinstance(row, dict):
            layer_bits.append(f"{name} unavailable")
            continue
        state = row.get("regime") or row.get("badge")
        if state:
            layer_bits.append(f"{name} {state}"
                              + (" (stale)" if row.get("stale") else ""))
        elif row.get("available") is False:
            layer_bits.append(f"{name} unavailable")

    return (
        "The event text is user-supplied and unverified. "
        f"Seiche reads {' at '.join(s_bits)} "
        f"(Seiche, {seiche.get('as_of') or 'date unavailable'}; seiche:board). "
        f"Undertow reads {u_text} "
        f"(Undertow, {undertow.get('as_of') or 'date unavailable'}; undertow:board). "
        f"LiquiLens layers: {', '.join(layer_bits) or 'no layer summary available'}. "
        "The readings alone do not establish that they caused, predicted, or "
        "validated the supplied event. The explanation layer is unavailable, "
        "so no semantic connection is being invented. Limitations: event text "
        "is unverified; causality and timing cannot be established."
    )


async def analyze(question: str, snapshot: dict,
                  raw_fleet: dict[str, dict] | None = None) -> dict:
    pack = await event_context(question, snapshot, raw_fleet=raw_fleet)
    registry = _evidence_registry(pack)
    required_limitations = _required_pack_limitations(pack)
    envelope = {
        "event_text_unverified": _sanitized_event_text(question),
        "fleet_reading_pack": pack,
        "allowed_evidence_ids": sorted(registry),
        "required_limitations": sorted(required_limitations),
    }
    envelope_bytes = _json_bytes(envelope)
    if len(envelope_bytes) > MAX_MODEL_ENVELOPE_BYTES:
        return {
            "ok": False,
            "mode": "readings_only_event_connection",
            "answer": fallback_answer(pack),
            "reason": "bounded model envelope unavailable",
            "grounding": "deterministic reading summary only; semantic connection withheld",
            "sources": source_status(pack),
        }
    messages = [
        {"role": "system", "content": EVENT_SYSTEM_PROMPT},
        {"role": "user", "content": envelope_bytes.decode("utf-8")},
    ]
    errors = []
    for route, fn in (("free-llm-router", _via_event_free_router),
                      ("env-endpoint", _via_event_env)):
        try:
            raw_answer = await fn(messages)
            if raw_answer:
                contract = _validated_provider_contract(
                    raw_answer, registry, required_limitations)
                return {
                    "ok": True,
                    "mode": "readings_only_event_connection",
                    "route": route,
                    "answer": _render_verified_answer(contract, registry),
                    "verified_contract": contract,
                    "grounding": (
                        "provider output validated as evidence IDs and enums; "
                        "visible prose rendered only from the bounded reading pack"
                    ),
                    "sources": source_status(pack),
                }
        except Exception as exc:  # noqa: BLE001 - reject route and try the next
            errors.append(f"{route}: {type(exc).__name__}: {str(exc)[:80]}")
    return {
        "ok": False,
        "mode": "readings_only_event_connection",
        "answer": fallback_answer(pack),
        "reason": "no provider returned a valid grounded contract",
        "grounding": (
            "deterministic reading summary only; semantic connection withheld"
        ),
        "sources": source_status(pack),
    }
