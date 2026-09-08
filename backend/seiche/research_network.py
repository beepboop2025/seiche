"""Published research discovery shared by REST, MCP, CLI and human clients.

Palimpsest owns its complete catalog. Seiche reads that public catalog, keeps
its evidence states and source clocks, and supplies explicit next research
steps. This module never acquires source observations or changes a score.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "seiche.research-network.v1"
CATALOG_SCHEMA = "palimpsest-research-catalog/v1"
CATALOG_URL = "https://www.palimpsest.info/readings/research-catalog-latest.json"
API_URL = "https://api.seiche.info/api/v2/research-network"
SITE_URL = "https://seiche.info/#RESEARCH"
TOPICS = (
    "all", "china", "regions", "information_controls", "model_evaluations",
    "funding", "institutions", "liquidity", "global_data",
)
MAX_BYTES = 2 * 1024 * 1024
MAX_DATASETS = 1000
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "enum": list(TOPICS), "default": "all"},
        "offset": {"type": "integer", "minimum": 0, "maximum": MAX_DATASETS, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 12},
    },
    "additionalProperties": False,
}
BOUNDARY = (
    "Research context only. Shared geography, dates or topics do not establish "
    "causality, wrongdoing, creditworthiness or a trading signal. Dataset discovery "
    "does not grant permission to copy values; each source retains its own rights. "
    "Missing, restricted and stale evidence is never a neutral or safe reading."
)
_CACHE: dict[str, Any] = {}
_LOCK = threading.Lock()


def selection(arguments: dict) -> tuple[str, int, int]:
    if not isinstance(arguments, dict) or set(arguments) - set(INPUT_SCHEMA["properties"]):
        raise ValueError("only topic, offset and limit are supported")
    topic, offset, limit = (arguments.get("topic", "all"), arguments.get("offset", 0), arguments.get("limit", 12))
    if not isinstance(topic, str) or topic not in TOPICS:
        raise ValueError("unsupported research topic")
    if type(offset) is not int or not 0 <= offset <= MAX_DATASETS:
        raise ValueError("offset must be an integer from 0 to 1000")
    if type(limit) is not int or not 1 <= limit <= 25:
        raise ValueError("limit must be an integer from 1 to 25")
    return topic, offset, limit


def _clock(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def _text(value: Any, limit: int = 600) -> str | None:
    if not isinstance(value, str):
        return None
    return "".join(c for c in value if c >= " " and c not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")[:limit]


def _public_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 1000 or any(c.isspace() for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.netloc in {"palimpsest.info", "www.palimpsest.info"} and not parsed.query and not parsed.fragment:
            return value
    except ValueError:
        pass
    return None


def _string_list(value: Any, limit: int = 20) -> list[str]:
    return [s for item in value[:limit] if (s := _text(item, 160))] if isinstance(value, list) else []


def _freshness(artifact: dict, cadence: Any, evaluated_at: datetime) -> str:
    """Re-evaluate the producer's freshness label without refreshing its clock."""
    state = artifact.get("evidence_state")
    if state != "fresh":
        return state if state in {"stale", "gated", "warming", "disabled", "private-node"} else "unknown"
    observed = _clock(artifact.get("observed_at"))
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", cadence or "") if isinstance(cadence, str) else None
    if observed is None or match is None:
        return "unknown"
    seconds = sum(int(part or 0) * unit for part, unit in zip(match.groups(), (86400, 3600, 60, 1)))
    age = (evaluated_at - observed).total_seconds()
    if age < -300 or seconds <= 0:
        return "unknown"
    return "fresh" if age <= max(3600, seconds * 2) else "stale"


def _matches(row: dict, topic: str) -> bool:
    if topic == "all":
        return True
    layer = row.get("layer")
    identity = str(row.get("id", ""))
    if topic == "information_controls":
        return layer in {"content", "network", "narrative", "platform", "state"}
    if topic == "model_evaluations":
        return layer in {"model", "integrity"} or identity.startswith("eval-")
    if topic == "regions":
        return any(word in identity for word in ("regional", "bri", "belt", "corridor", "connected", "mirror-trade"))
    if topic == "global_data":
        return any(word in identity for word in ("narcoscope", "global", "regional", "bri", "connected"))
    return layer == "economy" or identity in {"china-evidence-observatory", "connected-research"}


def _step(product: str, question: str, url: str, api: str | None = None, mcp: str | None = None, tool: str | None = None, arguments: dict | None = None, bot: str | None = None) -> dict:
    return {"product": product, "question": question, "url": url, "api": api,
            "mcp": mcp, "tool": tool, "arguments": arguments or {}, "telegram": bot}


def research_steps(topic: str) -> list[dict]:
    """Explicit domain handoffs. No claim of automatic causal joins."""
    steps = [
        _step("Palimpsest", "What is measured, revised, missing or restricted in the original record?",
              "https://palimpsest.info/china/evidence/" if topic in {"china", "funding", "institutions", "liquidity"} else "https://palimpsest.info/data.html",
              CATALOG_URL, "https://api.seiche.info/palimpsest/mcp", "research_catalog", bot="https://t.me/palimpsest_watch_bot"),
        _step("Seiche", "How do funding, currencies and capital-market conditions relate to this research?",
              "https://seiche.info/#MONEY%20MARKETS", "https://api.seiche.info/api/v2/world-markets?section=summary",
              "https://api.seiche.info/mcp", "world_markets_context", {"section": "summary"}, "https://t.me/seiche_desk_bot"),
    ]
    if topic not in {"information_controls", "model_evaluations"}:
        steps.extend([
            _step("LiquiLens", "Which institution filings support or challenge the counterparty hypothesis?",
                  "https://liquilens.in/start/", mcp="https://api.liquilens.in/mcp", tool="failure_radar_board", bot="https://t.me/liquilens_bot"),
            _step("LiquiLens Corporate", "Is published funding stress reaching nonfinancial firms?",
                  "https://liquilens.in/", mcp="https://api.liquilens.in/mcp", tool="corporate_transmission_board"),
            _step("LiquiLens Real Economy", "What does separate household-credit evidence show?",
                  "https://liquilens.in/", mcp="https://api.liquilens.in/mcp", tool="household_credit_board"),
            _step("Undertow", "What does the published liquidity and exit-cost evidence show at the relevant size?",
                  "https://liquilens-undertow.com/app/#crypto", mcp="https://api.seiche.info/undertow/mcp",
                  tool="liquidity_tiers", bot="https://t.me/undertow_LiquiLens_bot"),
            _step("NarcoScope", "Which granular drug, arms and informal-economy observations cover this geography and period?",
                  "https://narcoscope.com/#data", mcp="https://narcoscope.com/api/mcp", tool="get_market_catalog",
                  bot="https://t.me/NarcoScopeEvidenceBot"),
            _step("Market Brief", "Can the separate research records support a source-linked market brief?",
                  "https://beepboop2025.github.io/market-brief/"),
        ])
    return steps


def project(catalog: dict | None, *, topic: str = "all", offset: int = 0, limit: int = 12,
            evaluated_at: datetime, source_sha256: str | None = None, retrieved_at: str | None = None) -> dict:
    topic, offset, limit = selection({"topic": topic, "offset": offset, "limit": limit})
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluation time must have a timezone")
    valid = (isinstance(catalog, dict) and catalog.get("schema") == CATALOG_SCHEMA
             and isinstance(catalog.get("datasets"), list) and len(catalog["datasets"]) <= MAX_DATASETS
             and all(isinstance(row, dict) and isinstance(row.get("id"), str)
                     and re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", row["id"]) for row in catalog["datasets"])
             and len({row["id"] for row in catalog["datasets"]}) == len(catalog["datasets"]))
    generated_at = catalog.get("generated_at") if valid else None
    source_clock = _clock(generated_at)
    age = (evaluated_at - source_clock).total_seconds() if source_clock else None
    status = "available" if valid and age is not None and -300 <= age <= 86400 else "stale" if valid and age is not None and age > 86400 else "unavailable"
    # A malformed/future/missing catalog clock cannot establish current discovery.
    rows = [row for row in catalog["datasets"] if _matches(row, topic)] if valid and status != "unavailable" else []
    selected = []
    for row in rows[offset:offset + limit]:
        artifact = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
        urls = row.get("urls") if isinstance(row.get("urls"), dict) else {}
        rights = row.get("license") if isinstance(row.get("license"), dict) else {}
        selected.append({
            "id": _text(row["id"], 120), "title": _text(row.get("name"), 200),
            "description": _text(row.get("description")), "layer": _text(row.get("layer"), 60),
            "geography": _string_list(row.get("geography")), "sources": _string_list(row.get("sources")),
            "source_reported_state": _text(artifact.get("evidence_state"), 60),
            "evidence_state": _freshness(artifact, row.get("cadence"), evaluated_at),
            "observed_at": _text(artifact.get("observed_at"), 60), "cadence": _text(row.get("cadence"), 60),
            "url": _public_url(urls.get("landing_page")), "data_url": _public_url(urls.get("latest")),
            "rights": _text(rights.get("name")), "values_included": False,
        })
    return {
        "schema": SCHEMA, "status": status, "context_only": True,
        "selection": {"topic": topic, "offset": offset, "limit": limit},
        "evaluated_at": evaluated_at.isoformat(),
        "source": {"product": "Palimpsest", "url": CATALOG_URL, "schema": CATALOG_SCHEMA,
                   "generated_at": generated_at, "retrieved_at": retrieved_at, "sha256": source_sha256,
                   "integrity": "retrieved_bytes_hash_not_producer_attestation"},
        "catalog_total": len(catalog["datasets"]) if valid else None,
        "matched_total": len(rows), "returned": len(selected),
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "datasets": selected, "next_steps": research_steps(topic),
        "links": {"api": API_URL, "site": SITE_URL, "mcp": "https://api.seiche.info/mcp"},
        "boundary": BOUNDARY,
        "eligibility": {"blend_into_score": False, "training": False, "execution": False},
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_catalog() -> tuple[dict, str, str]:
    request = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "seiche-research-network/1", "Accept": "application/json"})
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=8) as response:
        if response.headers.get_content_type() != "application/json":
            raise ValueError("catalog did not return JSON")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("catalog exceeds the read budget")
    data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    return data, hashlib.sha256(raw).hexdigest(), datetime.now(timezone.utc).isoformat()


def read(arguments: dict | None = None) -> dict:
    topic, offset, limit = selection({} if arguments is None else arguments)
    with _LOCK:
        if time.monotonic() >= _CACHE.get("expires", 0):
            try:
                catalog, digest, retrieved = _fetch_catalog()
                _CACHE.update(catalog=catalog, digest=digest, retrieved=retrieved, expires=time.monotonic() + 60)
            except (OSError, ValueError, TypeError, RecursionError):
                # Do not relabel a previous successful response as a fresh fetch.
                _CACHE.update(catalog=None, digest=None, retrieved=None, expires=time.monotonic() + 15)
        cached = copy.deepcopy(_CACHE)
    return project(cached["catalog"], topic=topic, offset=offset, limit=limit,
                   evaluated_at=datetime.now(timezone.utc), source_sha256=cached["digest"], retrieved_at=cached["retrieved"])
