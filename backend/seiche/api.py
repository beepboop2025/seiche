"""Seiche REST API. Run: uvicorn seiche.api:app --port 8787"""

from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import hashlib
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from seiche import (
    accounts,
    assemble,
    context_views,
    mcp_server,
    methodology,
    provisioning,
    public_view,
    store,
    subscribe as subscribe_list,
    usage,
    world_model_delivery,
    x402,
)
from seiche.config import (
    ALERT_RULES,
    ALL_SERIES,
    COMPOSITE_WEIGHTS,
    DB_PATH,
    EPISODES,
    MCP_MAX_BATCH,
    MCP_RATE_LIMIT_PER_MIN,
    MCP_UPGRADE_URL,
    REGIMES,
    WRECKS_BLOB_KEY,
)
from seiche.domain.observation import QualityState, RedistributionStatus
from seiche.markets.base import CapabilityStatus, PackSupportStatus
from seiche.markets.calibration import get_local_calibration
from seiche.markets.materialize import PUBLIC_SNAPSHOT_VISIBILITY
from seiche.markets.registry import UnknownMarketError, default_registry
from seiche.repository import get_repository

# In production (SEICHE_ENV=production, set in the systemd unit) the interactive
# API docs and the machine-readable schema are turned off — they enumerate every
# gated route and its shape, which we don't hand to anonymous callers. Dev keeps
# them on.
_PROD = os.getenv("SEICHE_ENV", "").lower() == "production"


# ---- keep the board warm 24/7 ------------------------------------------------
# In production the snapshot is rebuilt by a background loop, not by whichever
# visitor happens to arrive after the cache expires: no reader ever pays the
# assembly bill (sources → 64 engine modules → deep layer → the Navigator's LLM
# commit), and the Navigator files its reading every day even on a day with
# zero visitors. Off in dev/tests (SEICHE_ENV!=production) unless forced with
# SEICHE_BG_REFRESH=1.

_REFRESH_EVERY_S = max(60, int(assemble.CACHE_MIN * 60 * 0.9))


async def _keep_warm() -> None:
    log = logging.getLogger("seiche.api")
    while True:
        try:
            await assemble.snapshot()
        except Exception:  # noqa: BLE001 — the loop must outlive any bad cycle
            log.exception("background board refresh failed; retrying next cycle")
        await asyncio.sleep(_REFRESH_EVERY_S)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Hand the MCP bridge THIS loop before any tool call can arrive: the tool
    # handlers run in worker threads and must run assemble coroutines on the
    # same loop the REST routes and keep-warm task use, or assemble's
    # module-level asyncio.Lock ends up shared across loops and wedges
    # (mcp_server._run has the full story).
    mcp_server.set_main_loop(asyncio.get_running_loop())
    refresh_task: asyncio.Task[None] | None = None
    if _PROD or os.getenv("SEICHE_BG_REFRESH") == "1":
        restored = await asyncio.to_thread(assemble.restore_cached_snapshot)
        if restored is not None:
            logging.getLogger("uvicorn.error").info(
                "serving %s last-known-good snapshot while board rebuilds",
                restored,
            )
        refresh_task = asyncio.create_task(_keep_warm(), name="seiche-keep-warm")
    try:
        yield
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task


app = FastAPI(title="Seiche", version=assemble.VERSION,
              description="Funding-stress & leveraged-positioning early-warning terminal",
              docs_url=None if _PROD else "/docs",
              redoc_url=None if _PROD else "/redoc",
              openapi_url=None if _PROD else "/openapi.json",
              lifespan=_lifespan)
# Uvicorn's default config gives only its own logger tree an INFO sink. Give
# this one bounded event stream its own stderr sink instead of raising the root
# logger (and every application dependency) to INFO. The guard keeps module
# reloads and embedding applications that already configured this exact logger
# from adding duplicate handlers.
_mcp_activation_log = logging.getLogger("seiche.mcp.activation")
_mcp_activation_log.setLevel(logging.INFO)
_mcp_activation_log.propagate = False
if not _mcp_activation_log.handlers:
    _mcp_activation_handler = logging.StreamHandler()
    _mcp_activation_handler.setFormatter(logging.Formatter("%(message)s"))
    _mcp_activation_log.addHandler(_mcp_activation_handler)

# CORS is applied once at the edge (Caddy on api.seiche.info); a second copy
# here produced duplicate Access-Control-Allow-Origin headers that browsers
# reject. Local dev uses the vite same-origin proxy, so no CORS is needed.

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _board_gate_enabled() -> bool:
    return os.getenv("SEICHE_BOARD_AUTH", "0") == "1"


def _json_safe(o: Any) -> Any:
    """Replace non-finite floats (NaN/Inf) with None so strict JSON can carry
    the payload. Recurses dicts/lists; everything else passes through."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    return o


def _market_pack(market_id: str):
    try:
        return default_registry().get(market_id)
    except UnknownMarketError as exc:
        known = ", ".join(pack.market_id for pack in default_registry().list())
        raise HTTPException(404, f"unknown market {market_id!r}; available: {known}") from exc


def _v2_capabilities(pack) -> tuple[dict[str, str], list[dict[str, Any]]]:
    matrix = {
        capability.capability_id: capability.status.value
        for capability in pack.capabilities
    }
    missing = [
        {
            "capability": capability.capability_id,
            "status": capability.status.value,
            "reason": capability.reason,
        }
        for capability in pack.capabilities
        if capability.status is not CapabilityStatus.READY
    ]
    return matrix, missing


def _v2_collector_faults(pack) -> list[dict[str, Any]]:
    public_adapters = {
        adapter.adapter_id
        for adapter in pack.source_adapters
        if adapter.redistribution_status is not RedistributionStatus.PROHIBITED
    }
    return [
        {
            "market_id": pack.market_id,
            "source": item["adapter_id"],
            "status": item["status"],
            "detail": item.get("fault"),
            "finished_at": item["finished_at"],
            "next_due": item["next_due"],
        }
        for item in get_repository().latest_collector_runs(pack.market_id)
        if item["status"] != "SUCCESS" and item["adapter_id"] in public_adapters
    ]


def _v2_unavailable(pack, product: str, reason: str) -> JSONResponse:
    capabilities, missing = _v2_capabilities(pack)
    return JSONResponse(
        status_code=503,
        content={
            "schema": f"seiche.{product}.v2",
            "product": product.upper(),
            "status": "UNAVAILABLE",
            "market_id": pack.market_id,
            "monetary_area_id": pack.monetary_area_id,
            "jurisdiction_codes": list(pack.jurisdiction_codes),
            "currency": pack.currency,
            "policy_regime": pack.policy_regime.value,
            "support_status": pack.support_status.value,
            # Raw canonical coverage can include tenant-prohibited timestamps
            # and counts. An unavailable public projection discloses no row
            # metadata; the next filtered snapshot will carry safe coverage.
            "data_coverage": {"canonical_observations": []},
            "capabilities": capabilities,
            "missing_capabilities": missing,
            "calibration_id": pack.calibration_id,
            "evidence_eligibility": {"eligible": False, "reason": reason},
            "event_cutoff": None,
            "knowledge_cutoff": None,
            "faults": _v2_collector_faults(pack)
            or [{"market_id": pack.market_id, "detail": reason}],
            "stale_inputs": [],
        },
    )


def _parse_v2_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "timestamp must be ISO-8601 with an explicit timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(422, "timestamp must include an explicit timezone")
    return parsed.astimezone(UTC)


def _decode_series_cursor(value: str | None) -> tuple[datetime, str] | None:
    if value is None:
        return None
    if not value or len(value) > 512:
        raise HTTPException(422, "cursor is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError
        event_time = _parse_v2_timestamp(payload["event_time"])
        instrument_id = payload["instrument_id"]
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise ValueError
    except (UnicodeEncodeError, binascii.Error, json.JSONDecodeError, KeyError, ValueError):
        raise HTTPException(422, "cursor is invalid") from None
    return event_time, instrument_id


def _encode_series_cursor(value: tuple[datetime, str] | None) -> str | None:
    if value is None:
        return None
    payload = json.dumps(
        {
            "v": 1,
            "event_time": value[0].astimezone(UTC).isoformat(),
            "instrument_id": value[1],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _series_evidence_eligibility(pack, observations) -> dict[str, Any]:
    """Fail closed until pack, calibration, and row quality are validated."""

    reasons: list[str] = []
    if not observations:
        reasons.append("no canonical observations are available")
    if pack.support_status is not PackSupportStatus.SUPPORTED:
        reasons.append("pack validation status is not SUPPORTED")
    try:
        calibration = get_local_calibration(pack.market_id)
    except KeyError:
        reasons.append("no registered local calibration is available")
    else:
        if calibration.calibration_id != pack.calibration_id:
            reasons.append("registered calibration does not match the market pack")
        if calibration.maturity != "VALIDATED":
            reasons.append("calibration is forward-only")
    ineligible_quality = sorted(
        {
            observation.quality.value
            for observation in observations
            if observation.quality not in {QualityState.VERIFIED, QualityState.REVISED}
        }
    )
    if ineligible_quality:
        reasons.append(
            "observation quality is not evidence-eligible: "
            + ", ".join(ineligible_quality)
        )
    if observations and not any(
        _observation_value_is_public(pack, observation)
        for observation in observations
    ):
        reasons.append("no publicly redistributable observation values are available")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "value_encoding": "decimal_string",
        "restricted_values": "redacted or omitted",
    }


def _series_page_coverage(observations) -> list[dict[str, Any]]:
    """Describe only rows that this public page is permitted to disclose."""

    by_role: dict[str, list[Any]] = defaultdict(list)
    for observation in observations:
        by_role[observation.semantic_role.value].append(observation)
    return [
        {
            "semantic_role": role,
            "observations": len(items),
            "event_start": min(item.event_time for item in items).isoformat(),
            "event_end": max(item.event_time for item in items).isoformat(),
            "latest_knowledge_time": max(
                item.knowledge_time for item in items
            ).isoformat(),
            "unavailable_observations": sum(
                item.quality is QualityState.UNAVAILABLE for item in items
            ),
        }
        for role, items in sorted(by_role.items())
    ]


def _bearer_identity(authorization: str | None) -> dict | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return accounts.verify_token(authorization.removeprefix("Bearer "))


def require_board(authorization: str | None = Header(default=None)) -> dict | None:
    """Shared gate for every non-public endpoint. When the board gate is on
    (SEICHE_BOARD_AUTH=1, the public box) a valid subscriber token is required;
    in dev/tests (gate off) it is a no-op that simply surfaces the caller's
    identity (or None) so handlers can honour `force` only for authed callers."""
    ident = _bearer_identity(authorization)
    if _board_gate_enabled() and ident is None:
        raise HTTPException(401, "the board is a subscriber feature — sign in")
    return ident


# ---- rate limiting ----------------------------------------------------------
# stdlib-only, in-process, per-IP. Matches the project ethos (no new deps); the
# counters reset on restart, which is fine for a single-process deploy. Behind
# Caddy the real client is in X-Forwarded-For.

LOGIN_RATE_LIMIT_PER_MIN = 10   # max login attempts per IP per rolling minute
LOGIN_LOCKOUT_AFTER = 5         # consecutive failures before a backoff lockout
LOGIN_LOCKOUT_SECONDS = 300     # how long that lockout lasts (5 min)
ASK_RATE_LIMIT_PER_MIN = 20     # max desk-assistant (LLM) calls per IP / minute
SUBSCRIBE_RATE_LIMIT_PER_MIN = 5  # a human types one address, not five a minute
MARKET_SERIES_RATE_LIMIT_PER_MIN = 30


class _RateLimiter:
    """Tiny sliding-window per-key limiter."""

    def __init__(self, limit_per_min: int) -> None:
        self._limit = limit_per_min
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] <= now - 60:
                dq.popleft()
            if len(dq) >= self._limit:
                return False
            dq.append(now)
            return True


class _LoginGuard:
    """Consecutive-failure backoff: after LOGIN_LOCKOUT_AFTER bad passwords from
    one IP, that IP is locked out for LOGIN_LOCKOUT_SECONDS. A success clears it."""

    def __init__(self) -> None:
        self._fails: dict[str, int] = defaultdict(int)
        self._locked_until: dict[str, float] = {}
        self._lock = Lock()

    def retry_after(self, key: str) -> int:
        with self._lock:
            remaining = self._locked_until.get(key, 0.0) - time.time()
            return int(remaining) + 1 if remaining > 0 else 0

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._fails[key] += 1
            if self._fails[key] >= LOGIN_LOCKOUT_AFTER:
                self._locked_until[key] = time.time() + LOGIN_LOCKOUT_SECONDS
                self._fails[key] = 0

    def record_success(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)


_login_limiter = _RateLimiter(LOGIN_RATE_LIMIT_PER_MIN)
_login_guard = _LoginGuard()
_ask_limiter = _RateLimiter(ASK_RATE_LIMIT_PER_MIN)
_mcp_limiter = _RateLimiter(MCP_RATE_LIMIT_PER_MIN)
_subscribe_limiter = _RateLimiter(SUBSCRIBE_RATE_LIMIT_PER_MIN)
_market_series_limiter = _RateLimiter(MARKET_SERIES_RATE_LIMIT_PER_MIN)


def _client_ip(request: Request) -> str:
    # Seiche binds loopback (127.0.0.1) with Caddy as the single proxy in front.
    # Caddy APPENDS the real peer to the END of X-Forwarded-For, so a client can
    # spoof leftmost entries but NOT the rightmost one. Trust only the rightmost
    # entry — reading the leftmost (or a bare X-Real-IP that Caddy doesn't
    # overwrite) lets an attacker rotate their apparent IP per request and bypass
    # rate limiting and login lockout. If Caddy is ever configured with
    # `header_up X-Real-IP {remote_host}` (overwriting client input), prefer that.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


# ---- overview wire cache -----------------------------------------------------
# The board payload is ~360KB of JSON polled by every open tab. Serializing it
# per request (and shipping it uncompressed) was the single biggest tax on the
# UI. The snapshot dict is immutable between rebuilds, so serialize + gzip it
# ONCE per rebuild and answer conditional requests with 304s.

_OVERVIEW_WIRE: dict[str, Any] = {"src": None, "body": b"", "gz": b"", "etag": ""}
_OVERVIEW_CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=240"


def _overview_wire(payload: dict) -> dict[str, Any]:
    if _OVERVIEW_WIRE["src"] is not payload:
        body = json.dumps(_json_safe(payload), separators=(",", ":"), allow_nan=False).encode()
        _OVERVIEW_WIRE.update(
            src=payload,
            body=body,
            gz=gzip.compress(body, 6),
            etag='"' + hashlib.sha256(body).hexdigest()[:20] + '"',
        )
    return _OVERVIEW_WIRE


@app.get("/api")
def api_index() -> dict[str, Any]:
    """Curated public discovery document.

    Production OpenAPI stays disabled because it would enumerate private
    routes. This small document exposes only the stable, public contracts an
    integration should start from.
    """
    return {
        "product": "Seiche",
        "job": "system-level US dollar funding-stress early warning",
        "developer_guide": "https://seiche.info/developers.html",
        "mcp": {
            "url": "https://api.seiche.info/mcp",
            "transport": "streamable-http",
            "authentication": "none for the eight public tools",
            "first_tool": "funding_stress_now",
        },
        "delivery": mcp_server.telegram_delivery("agent_api"),
        "rest": {
            "openapi": "/api/openapi.json",
            "public_snapshot": "/api/public",
            "small_gauge": "/api/gauge",
            "market_catalog_v2": "/api/v2/markets",
            "market_coverage_v2": "/api/v2/coverage",
            "global_tide_v2": "/api/v2/global/tide",
            "oil_funding": "/api/oil-funding",
            "fx_materials": "/api/estuary",
            "health": "/api/health",
            "series_catalog": "/api/series/index.json",
            "realtime_venue": "/undertow/live/quotes.json",
        },
        "conventions": {
            "as_of": "Every reading carries its source or publication time.",
            "absence": "Missing or stale evidence is stated, never rendered as calm.",
            "editorial": "The thesis, evidence, countercase and confidence travel together.",
            "clocks": (
                "Venue microstructure is real time; official macro series keep their native "
                "daily or weekly publication clocks."
            ),
            "disclaimer": "Research data, not investment advice.",
        },
    }


def _public_openapi_document() -> dict[str, Any]:
    """Small, stable contract for anonymous integrations only.

    ``app.openapi()`` is intentionally not used: the application also owns
    subscriber and operator routes, and production must not enumerate those.
    """
    object_response = {
        "description": "Successful JSON response",
        "content": {
            "application/json": {
                "schema": {"type": "object", "additionalProperties": True},
            },
        },
    }

    def context_response(schema_name: str) -> dict[str, Any]:
        return {
            "description": "Successful versioned context response",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["schema"],
                        "properties": {
                            "schema": {"type": "string", "const": schema_name},
                        },
                        "additionalProperties": True,
                    },
                },
            },
        }

    paths: dict[str, Any] = {
        "/api": {
            "get": {
                "operationId": "discoverSeicheApi",
                "summary": "Discover the public Seiche integration surface",
                "responses": {"200": object_response},
            },
        },
        "/api/health": {
            "get": {
                "operationId": "getSeicheHealth",
                "summary": "Read cached service and data-source health",
                "description": (
                    "Reads only the last completed board snapshot. A restart can "
                    "serve a dated last-known-good snapshot while rebuilding. A "
                    "cold cache, or require_rebuilt=true before this process has "
                    "completed its own build, returns 503 immediately. This request "
                    "never starts or waits for a board build."
                ),
                "parameters": [{
                    "name": "require_rebuilt",
                    "in": "query",
                    "required": False,
                    "description": (
                        "Deployment gate: require a snapshot completed by the "
                        "current process rather than a restored handoff."
                    ),
                    "schema": {"type": "boolean", "default": False},
                }],
                "responses": {
                    "200": object_response,
                    "503": {
                        "description": "Snapshot cache is warming or unavailable",
                        "headers": {
                            "Retry-After": {
                                "description": "Suggested seconds before retrying",
                                "schema": {"type": "string"},
                            },
                            "Cache-Control": {
                                "description": "Unavailable health must not be cached",
                                "schema": {"type": "string"},
                            },
                        },
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["status", "version"],
                                    "properties": {
                                        "status": {
                                            "type": "string",
                                            "enum": [
                                                "warming_or_unavailable",
                                                "rebuilding_from_last_known_good",
                                            ],
                                        },
                                        "version": {"type": "string"},
                                        "serving_generated_at": {
                                            "type": ["string", "null"],
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                            },
                        },
                    },
                },
            },
        },
        "/api/gauge": {
            "get": {
                "operationId": "getFundingStressGauge",
                "summary": "Read the compact current funding-stress gauge",
                "description": "The smallest stable contract for dashboards, alerts and risk pipelines.",
                "responses": {"200": object_response},
            },
        },
        "/api/v2/markets": {
            "get": {
                "operationId": "listMonetaryAreaPacks",
                "summary": "List registered monetary-area packs and capabilities",
                "responses": {"200": object_response},
            },
        },
        "/api/v2/markets/{market_id}/overview": {
            "get": {
                "operationId": "getMarketOverviewV2",
                "summary": "Read the latest sealed local-market overview",
                "parameters": [{
                    "name": "market_id", "in": "path", "required": True,
                    "schema": {"type": "string"},
                }],
                "responses": {"200": object_response, "503": object_response},
            },
        },
        "/api/v2/markets/{market_id}/gauge": {
            "get": {
                "operationId": "getLocalSeicheGaugeV2",
                "summary": "Read the latest sealed local Seiche gauge",
                "parameters": [{
                    "name": "market_id", "in": "path", "required": True,
                    "schema": {"type": "string"},
                }],
                "responses": {"200": object_response, "503": object_response},
            },
        },
        "/api/v2/markets/{market_id}/asof/{timestamp}": {
            "get": {
                "operationId": "getMarketOverviewAsOfV2",
                "summary": "Read the last sealed market overview knowable by a timestamp",
                "parameters": [
                    {"name": "market_id", "in": "path", "required": True,
                     "schema": {"type": "string"}},
                    {"name": "timestamp", "in": "path", "required": True,
                     "schema": {"type": "string", "format": "date-time"}},
                ],
                "responses": {"200": object_response, "404": object_response},
            },
        },
        "/api/v2/markets/{market_id}/series": {
            "get": {
                "operationId": "getCanonicalMarketSeriesV2",
                "summary": "Read canonical, licence-aware market observations",
                "parameters": [
                    {
                        "name": "market_id", "in": "path", "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "n", "in": "query", "required": False,
                        "schema": {
                            "type": "integer", "minimum": 1, "maximum": 5000,
                            "default": 1000,
                        },
                    },
                    {
                        "name": "cursor", "in": "query", "required": False,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {"200": object_response},
            },
        },
        "/api/v2/global/tide": {
            "get": {
                "operationId": "getGlobalSeicheTideV2",
                "summary": "Read cross-basin synchronization and transmission",
                "responses": {"200": object_response},
            },
        },
        "/api/v2/coverage": {
            "get": {
                "operationId": "getMarketCoverageV2",
                "summary": "Read per-market data, capability and connector coverage",
                "responses": {"200": object_response},
            },
        },
        "/api/public": {
            "get": {
                "operationId": "getPublicFundingStressRecord",
                "summary": "Read the argument, countercase, data quality and PROOF scoreboard",
                "responses": {"200": object_response},
            },
        },
        "/api/oil-funding": {
            "get": {
                "operationId": "getOilFundingContext",
                "summary": "Read oil, energy-futures and dollar-funding cash pressure",
                "description": (
                    "Chartless observed spot/funding evidence, Ballast's bounded "
                    "WTI/Henry Hub gross cash-displacement ledger, live Cushing "
                    "and Brent−WTI observations separated from dated capacity, "
                    "benchmark and chokepoint references, and explicitly "
                    "scenario-only cargo, margin and India arithmetic."
                ),
                "responses": {
                    "200": context_response("seiche.oil-funding.v1")
                },
            },
        },
        "/api/estuary": {
            "get": {
                "operationId": "getFxMaterialsPassage",
                "summary": "Read FX/material pressure and holdout-tested Passage links",
                "description": (
                    "Compares upstream FX and physical-material cash pressure with "
                    "funding already priced; context only, never a composite input."
                ),
                "responses": {"200": context_response("seiche.estuary.v1")},
            },
        },
        "/api/series/index.json": {
            "get": {
                "operationId": "listPublicSeries",
                "summary": "List downloadable public time series",
                "responses": {"200": object_response},
            },
        },
        "/undertow/live/quotes.json": {
            "get": {
                "operationId": "getRealtimeVenueMicrostructure",
                "summary": "Read the relayed crypto venue microstructure packet",
                "description": (
                    "Binance spot and USD-M futures data relayed by Undertow. "
                    "This clock is separate from the official macro publication clocks."
                ),
                "responses": {"200": object_response},
            },
        },
        "/api/asof/{date}": {
            "get": {
                "operationId": "getFundingStressAsOf",
                "summary": "Replay the public board as of a UTC date",
                "parameters": [{
                    "name": "date",
                    "in": "path",
                    "required": True,
                    "description": "UTC date in YYYY-MM-DD form",
                    "schema": {"type": "string", "format": "date"},
                }],
                "responses": {"200": object_response, "404": {"description": "No record for that date"}},
            },
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Seiche Public API",
            "version": assemble.VERSION,
            "description": (
                "Curated funding-stress data; v1 remains the US dollar alias. "
                "Research data, not investment advice."
            ),
        },
        "servers": [{"url": "https://api.seiche.info"}],
        "externalDocs": {
            "description": "MCP and API quickstart",
            "url": "https://seiche.info/developers.html",
        },
        "paths": paths,
    }


@app.get("/api/openapi.json", include_in_schema=False)
def public_openapi(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _public_openapi_document()


_DELIVERY_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-transform",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


@app.get(world_model_delivery.DELIVERY_ROUTE, include_in_schema=False)
def signed_world_model_delivery(
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Relay one signed Lab envelope; Railway verifies its Ed25519 authority."""

    config = world_model_delivery.configured_delivery()
    if config is None:
        raise HTTPException(
            status_code=404,
            detail="not found",
            headers=dict(_DELIVERY_CACHE_HEADERS),
        )
    if not world_model_delivery.bearer_authorized(config, authorization):
        headers = {**_DELIVERY_CACHE_HEADERS, "WWW-Authenticate": "Bearer"}
        raise HTTPException(status_code=401, detail="unauthorized", headers=headers)
    try:
        opened = world_model_delivery.open_delivery(config)
    except world_model_delivery.DeliveryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="signed delivery unavailable",
            headers=dict(_DELIVERY_CACHE_HEADERS),
        ) from exc
    headers = {**_DELIVERY_CACHE_HEADERS, "Content-Length": str(opened.size)}
    try:
        return StreamingResponse(
            world_model_delivery.iter_delivery(opened),
            status_code=200,
            media_type="application/json",
            headers=headers,
        )
    except Exception:
        opened.handle.close()
        raise


@app.head("/api/overview")
async def overview_head(ident: dict | None = Depends(require_board)):
    """Uptime monitors and cache validators probe with HEAD: answer from the
    wire cache's headers without shipping (or rebuilding) the body."""
    await assemble.snapshot()
    wire = _OVERVIEW_WIRE
    headers = {"Cache-Control": _OVERVIEW_CACHE_CONTROL, "Vary": "Accept-Encoding"}
    if wire["etag"]:
        headers["ETag"] = wire["etag"]
    return Response(status_code=200, media_type="application/json", headers=headers)


@app.get("/api/overview")
async def overview(request: Request, force: bool = False,
                   ident: dict | None = Depends(require_board)):
    """The full board — subscriber-gated when SEICHE_BOARD_AUTH=1 (the public
    box). Free visitors get /api/public instead. `force` (cache-bypass
    recompute) is honoured only for authenticated callers."""
    payload = await assemble.snapshot(force=force and ident is not None)
    wire = _overview_wire(payload)
    headers = {
        "ETag": wire["etag"],
        "Cache-Control": _OVERVIEW_CACHE_CONTROL,
        "Vary": "Accept-Encoding",
    }
    if wire["etag"] and wire["etag"] in (request.headers.get("if-none-match") or ""):
        return Response(status_code=304, headers=headers)
    if "gzip" in (request.headers.get("accept-encoding") or "").lower():
        headers["Content-Encoding"] = "gzip"
        return Response(content=wire["gz"], media_type="application/json", headers=headers)
    return Response(content=wire["body"], media_type="application/json", headers=headers)


@app.get("/api/public")
async def public(response: Response, force: bool = False,
                 authorization: str | None = Header(default=None)):
    """Free derived surface: argument, countercase, data quality, conclusion
    and PROOF. Never the underlying engine payloads. `force` is ignored for
    unauthenticated callers — no anonymous recompute."""
    ident = _bearer_identity(authorization)
    snap = await assemble.snapshot(force=force and ident is not None)
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=240"
    return public_view.public_payload(snap)


@app.get("/api/gauge")
async def gauge(response: Response):
    """The regime gauge, free and machine-shaped: the one reading a risk
    pipeline (an RWA curator, an agent framework, a circuit breaker) needs,
    versioned so consumers can pin the contract. Never the full board."""
    snap = await assemble.snapshot()
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=240"
    engines = snap.get("engines", {})
    deep = snap.get("deep", {}) or {}
    comp = engines.get("composite", {})
    tell = deep.get("tell", {}) or {}
    cal = snap.get("calendar", {}) or {}
    # Forward ensemble, additive to the v1 contract: the Stack's published
    # 5-business-day event probability plus every member view by name (the
    # Navigator and the Model Court included). Absent members are omitted, an
    # absent ensemble is null; consumers must not assume a fixed member set.
    stk = deep.get("stacker", {}) or {}
    members: dict[str, Any] = dict(stk.get("members_now") or {})
    nav = snap.get("navigator", {}) or {}
    if nav.get("ok") and nav.get("p_event_5bd") is not None:
        members["navigator"] = nav.get("p_event_5bd")
    # The court sits on the finished deep layer, so it is published under
    # `deep`, not `engines`; reading the wrong branch silently omitted it.
    # Its pooled read lives in ensemble.p, not a p_event_5bd key.
    mcourt = deep.get("modelcourt", {}) or {}
    court_p = (mcourt.get("ensemble") or {}).get("p")
    if mcourt.get("ok") and court_p is not None:
        members["modelcourt"] = court_p
    return {
        "schema": "seiche.gauge.v1",
        "generated_at": snap.get("generated_at"),
        "index": comp.get("value"),
        "regime": comp.get("regime"),
        "coverage_pct": comp.get("coverage_pct"),
        "tell": tell.get("tell"),
        "p_event_5bd": stk.get("p_now") if stk.get("ok") else None,
        "p_event_5bd_dispersion": stk.get("dispersion_now") if stk.get("ok") else None,
        "p_event_5bd_members": members or None,
        "next_turn": cal.get("next_turn"),
        "crunch_windows": (cal.get("crunch_windows") or [])[:3],
        "faults": len(snap.get("faults") or []),
        "notes": "point-in-time as-published; PROOF scoreboard at /api/public; not investment advice",
    }


# ---- market-pack v2 ---------------------------------------------------------
# These routes are read-only over sealed snapshots and canonical observations.
# They never call assemble.snapshot(), so a cold API request cannot fan out to
# every source or let one monetary area's collector block another area's read.

def _public_adapter_ids(pack) -> frozenset[str]:
    return frozenset(
        adapter.adapter_id
        for adapter in pack.source_adapters
        if adapter.redistribution_status is not RedistributionStatus.PROHIBITED
    )


def _public_instrument_ids(pack) -> tuple[str, ...]:
    adapter_ids = _public_adapter_ids(pack)
    return tuple(
        instrument.instrument_id
        for instrument in pack.instruments
        if instrument.source_adapter_id in adapter_ids
    )


def _observation_value_is_public(pack, observation) -> bool:
    """Apply the most restrictive declared and per-row value policy."""

    instrument = pack.instrument_map.get(observation.instrument_id)
    if instrument is None:
        return False
    adapter = pack.adapter_map.get(instrument.source_adapter_id)
    return (
        adapter is not None
        and adapter.redistribution_status is RedistributionStatus.ALLOWED
        and observation.redistribution_status is RedistributionStatus.ALLOWED
    )


def _public_snapshot_payload(record: dict | None) -> dict | None:
    if record is None:
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("visibility") != PUBLIC_SNAPSHOT_VISIBILITY:
        return None
    return payload


@app.get("/api/v2/markets")
def markets_v2(response: Response):
    response.headers["Cache-Control"] = "public, max-age=300"
    markets = []
    for pack in default_registry().list():
        summary = pack.summary()
        latest = get_repository().load_latest_market_snapshot(pack.market_id, "gauge")
        payload = _public_snapshot_payload(latest)
        latest = latest if payload is not None else None
        summary["latest_snapshot"] = (
            {
                "snapshot_id": latest["snapshot_id"],
                "event_cutoff": latest["event_cutoff"],
                "knowledge_cutoff": latest["knowledge_cutoff"],
                "sealed_at": latest["sealed_at"],
                "evidence_eligible": latest["evidence_eligible"],
            }
            if latest
            else None
        )
        summary["data_coverage"] = (
            payload.get("data_coverage", {"canonical_observations": []})
            if payload
            else {"canonical_observations": []}
        )
        summary["evidence_eligibility"] = (
            payload.get("evidence_eligibility")
            if payload
            else {"eligible": False, "reason": "no sealed market snapshot"}
        )
        summary["event_cutoff"] = payload.get("event_cutoff") if payload else None
        summary["knowledge_cutoff"] = (
            payload.get("knowledge_cutoff") if payload else None
        )
        summary["faults"] = (
            payload.get("faults", []) if payload else _v2_collector_faults(pack)
        )
        summary["stale_inputs"] = payload.get("stale_inputs", []) if payload else []
        markets.append(summary)
    return {
        "schema": "seiche.markets.v2",
        "markets": markets,
        "count": len(markets),
        "collection_policy": "independent schedules; API reads sealed snapshots only",
    }


@app.get("/api/v2/markets/{market_id}/overview")
def market_overview_v2(market_id: str, response: Response):
    pack = _market_pack(market_id)
    record = get_repository().load_latest_market_snapshot(pack.market_id, "overview")
    payload = _public_snapshot_payload(record)
    if payload is None:
        return _v2_unavailable(
            pack,
            "market-overview",
            "no redistribution-filtered overview has been published by this market pack",
        )
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=240"
    return payload


@app.get("/api/v2/markets/{market_id}/gauge")
def market_gauge_v2(market_id: str, response: Response):
    pack = _market_pack(market_id)
    record = get_repository().load_latest_market_snapshot(pack.market_id, "gauge")
    payload = _public_snapshot_payload(record)
    if payload is None:
        return _v2_unavailable(
            pack,
            "local-gauge",
            "no redistribution-filtered gauge has been published by this market pack",
        )
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=240"
    return payload


@app.get("/api/v2/markets/{market_id}/asof/{timestamp}")
def market_asof_v2(market_id: str, timestamp: str, response: Response):
    pack = _market_pack(market_id)
    cutoff = _parse_v2_timestamp(timestamp)
    record = get_repository().load_market_snapshot_as_of(pack.market_id, "overview", cutoff)
    payload = _public_snapshot_payload(record)
    if payload is None:
        raise HTTPException(
            404,
            f"no redistribution-filtered {pack.market_id} overview known by {cutoff.isoformat()}",
        )
    response.headers["Cache-Control"] = "public, max-age=86400"
    return {
        **payload,
        "requested_knowledge_cutoff": cutoff.isoformat(),
        "sealed_snapshot_id": record["snapshot_id"],
    }


@app.get("/api/v2/markets/{market_id}/series")
def market_series_v2(
    market_id: str,
    request: Request,
    response: Response,
    n: int = 1000,
    cursor: str | None = None,
):
    pack = _market_pack(market_id)
    if not _market_series_limiter.allow(_client_ip(request)):
        raise HTTPException(
            429,
            "market series request limit exceeded",
            headers={"Retry-After": "60"},
        )
    if not 1 <= n <= 5000:
        raise HTTPException(422, "n must be between 1 and 5000")
    public_adapters = _public_adapter_ids(pack)
    prohibited_adapters = set(pack.adapter_map) - set(public_adapters)
    public_instrument_ids = _public_instrument_ids(pack)
    now = datetime.now(UTC).replace(microsecond=0)
    repository = get_repository()
    observations, next_page = repository.load_observation_page(
        pack.market_id,
        now,
        limit=n,
        event_time=now,
        instrument_ids=public_instrument_ids,
        redistribution_statuses=(
            RedistributionStatus.ALLOWED,
            RedistributionStatus.DERIVED_ONLY,
            RedistributionStatus.METADATA_ONLY,
        ),
        before=_decode_series_cursor(cursor),
    )
    available_instruments = {item.instrument_id for item in observations}
    records = []
    # The repository pages newest-first so its index and cursor can stop early;
    # retain the endpoint's established chronological order within each page.
    for observation in reversed(observations):
        record = observation.to_record()
        if not _observation_value_is_public(pack, observation):
            record["value"] = None
            record["value_status"] = "REDACTED_BY_LICENCE"
        records.append(record)
    instruments = []
    for instrument in pack.instruments:
        adapter = pack.adapter_map[instrument.source_adapter_id]
        if adapter.redistribution_status is RedistributionStatus.PROHIBITED:
            continue
        instruments.append(
            {
                "instrument_id": instrument.instrument_id,
                "mnemonic": instrument.mnemonic,
                "semantic_role": instrument.semantic_role.value,
                "canonical_unit": instrument.canonical_unit.value,
                "source_adapter": adapter.adapter_id,
                "connector_classification": adapter.classification.value,
                "redistribution_status": adapter.redistribution_status.value,
                "expected_cadence": adapter.expected_cadence,
                "availability": (
                    "READY"
                    if instrument.instrument_id in available_instruments
                    else "UNAVAILABLE"
                ),
            }
        )
    stale = [
        {
            "instrument_id": item.instrument_id,
            "event_time": item.event_time.isoformat(),
            "staleness": item.staleness.value,
        }
        for item in observations
        if item.staleness.value not in {"fresh", "aging"}
    ]
    capabilities, missing = _v2_capabilities(pack)
    latest_gauge = repository.load_latest_market_snapshot(pack.market_id, "gauge")
    gauge_payload = _public_snapshot_payload(latest_gauge) or {}
    faults = [
        item
        for item in (gauge_payload.get("faults") or _v2_collector_faults(pack))
        if item.get("source") not in prohibited_adapters
    ]
    # A sealed gauge may carry instrument timestamps outside this public page.
    # Derive staleness only from the already policy-filtered observations.
    stale_inputs = stale
    event_cutoff = max((item.event_time for item in observations), default=None)
    knowledge_cutoff = max((item.knowledge_time for item in observations), default=None)
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=240"
    return {
        "schema": "seiche.market-series.v2",
        "status": "READY" if observations else "UNAVAILABLE",
        "market_id": pack.market_id,
        "monetary_area_id": pack.monetary_area_id,
        "jurisdiction_codes": list(pack.jurisdiction_codes),
        "currency": pack.currency,
        "policy_regime": pack.policy_regime.value,
        "support_status": pack.support_status.value,
        "data_coverage": _series_page_coverage(observations),
        "coverage_scope": "returned_page",
        "capabilities": capabilities,
        "missing_capabilities": missing,
        "calibration_id": pack.calibration_id,
        "evidence_eligibility": _series_evidence_eligibility(pack, observations),
        "event_cutoff": event_cutoff.isoformat() if event_cutoff else None,
        "knowledge_cutoff": knowledge_cutoff.isoformat() if knowledge_cutoff else None,
        "faults": faults,
        "stale_inputs": stale_inputs,
        "instruments": instruments,
        "observations": records,
        "next_cursor": _encode_series_cursor(next_page),
    }


@app.get("/api/v2/global/tide")
def global_tide_v2(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=240"
    record = get_repository().load_latest_market_snapshot("GLOBAL", "tide")
    payload = _public_snapshot_payload(record)
    if payload is not None:
        return payload
    return {
        "schema": "seiche.global-tide.v2",
        "product": "GLOBAL_SEICHE_TIDE",
        "status": "UNAVAILABLE",
        "market_id": "GLOBAL",
        "monetary_area_id": None,
        "jurisdiction_codes": [],
        "currency": None,
        "policy_regime": None,
        "data_coverage": [],
        "missing_capabilities": [
            {
                "capability": "cross_basin_coupling",
                "status": "UNAVAILABLE",
                "reason": "no independently sealed cross-basin snapshot is available",
            }
        ],
        "calibration_id": None,
        "evidence_eligibility": {"eligible": False},
        "event_cutoff": None,
        "knowledge_cutoff": None,
        "faults": [],
        "stale_inputs": [],
        "reading": {"value": None},
        "notes": "Local gauges are never averaged into this product.",
    }


@app.get("/api/v2/coverage")
def coverage_v2(response: Response):
    response.headers["Cache-Control"] = "public, max-age=300"
    markets = []
    for pack in default_registry().list():
        capabilities, missing = _v2_capabilities(pack)
        latest = get_repository().load_latest_market_snapshot(pack.market_id, "gauge")
        payload = _public_snapshot_payload(latest) or {}
        latest = latest if payload else None
        public_adapters = _public_adapter_ids(pack)
        markets.append(
            {
                "market_id": pack.market_id,
                "monetary_area_id": pack.monetary_area_id,
                "currency": pack.currency,
                "policy_regime": pack.policy_regime.value,
                "support_status": pack.support_status.value,
                "calibration_id": pack.calibration_id,
                "capabilities": capabilities,
                "missing_capabilities": missing,
                "data_coverage": payload.get(
                    "data_coverage", {"canonical_observations": []}
                ),
                "latest_snapshot": (
                    {
                        "event_cutoff": latest["event_cutoff"],
                        "knowledge_cutoff": latest["knowledge_cutoff"],
                        "evidence_eligible": latest["evidence_eligible"],
                    }
                    if latest
                    else None
                ),
                "evidence_eligibility": payload.get(
                    "evidence_eligibility",
                    {"eligible": False, "reason": "no sealed market snapshot"},
                ),
                "event_cutoff": payload.get("event_cutoff"),
                "knowledge_cutoff": payload.get("knowledge_cutoff"),
                "faults": payload.get("faults") or _v2_collector_faults(pack),
                "stale_inputs": payload.get("stale_inputs") or [],
                "forward_validation_records": get_repository().forward_record_count(
                    pack.market_id
                ),
                "connectors": [
                    {
                        "adapter_id": adapter.adapter_id,
                        "classification": adapter.classification.value,
                        "redistribution_status": adapter.redistribution_status.value,
                        "expected_cadence": adapter.expected_cadence,
                    }
                    for adapter in pack.source_adapters
                    if adapter.adapter_id in public_adapters
                ],
            }
        )
    global_snapshot = get_repository().load_latest_market_snapshot("GLOBAL", "tide")
    global_payload = _public_snapshot_payload(global_snapshot)
    global_snapshot = global_snapshot if global_payload is not None else None
    return {
        "schema": "seiche.coverage.v2",
        "markets": markets,
        "global_tide": (
            global_payload.get("status", "UNAVAILABLE")
            if global_payload
            else "UNAVAILABLE"
        ),
        "global_tide_snapshot": (
            {
                "event_cutoff": global_payload.get("event_cutoff"),
                "knowledge_cutoff": global_payload.get(
                    "knowledge_cutoff"
                ),
                "evidence_eligibility": global_payload.get(
                    "evidence_eligibility"
                ),
                "faults": global_payload.get("faults", []),
                "stale_inputs": global_payload.get("stale_inputs", []),
            }
            if global_payload
            else None
        ),
        "forward_validation_records": get_repository().forward_record_count(),
    }


@app.get("/api/oil-funding")
async def oil_funding_context(response: Response):
    """Compact Oil x Funding evidence for bots, agents, and integrations."""
    snapshot = await assemble.snapshot()
    response.headers["Cache-Control"] = (
        "public, max-age=60, stale-while-revalidate=240"
    )
    return context_views.oil_funding(snapshot)


@app.get("/api/estuary")
async def estuary_context(response: Response):
    """Compact Estuary/Passage evidence with its context-only boundary."""
    snapshot = await assemble.snapshot()
    response.headers["Cache-Control"] = (
        "public, max-age=60, stale-while-revalidate=240"
    )
    return context_views.estuary(snapshot)


@app.get("/api/wrecks")
async def wrecks_record():
    """Wrecks: labelled crypto stress episodes replayed against the funding
    board, transmission vs specificity stated honestly. Free, like PROOF —
    credibility is the public surface."""
    payload = store.load_blob(WRECKS_BLOB_KEY)
    if payload is None:
        raise HTTPException(404, "wrecks record not computed yet — operator runs `seiche wrecks --refresh`")
    return payload


@app.get("/api/engines/{name}")
async def engine(name: str, _ident: dict | None = Depends(require_board)):
    snap = await assemble.snapshot()
    if name not in snap["engines"]:
        raise HTTPException(404, f"unknown engine '{name}'")
    return snap["engines"][name]


from pydantic import BaseModel


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def login(body: LoginBody, request: Request):
    """Subscriber login — returns a 30-day bearer token. Accounts are
    provisioned by the operator (`seiche user add`); no self-signup yet.
    Per-IP rate-limited with consecutive-failure backoff."""
    ip = _client_ip(request)
    locked = _login_guard.retry_after(ip)
    if locked:
        raise HTTPException(429, f"too many failed attempts — try again in {locked}s",
                            headers={"Retry-After": str(locked)})
    if not _login_limiter.allow(ip):
        raise HTTPException(429, "too many login attempts — slow down",
                            headers={"Retry-After": "60"})
    user = accounts.verify_user(body.username, body.password)
    if user is None:
        _login_guard.record_failure(ip)
        raise HTTPException(401, "invalid username or password")
    _login_guard.record_success(ip)
    return accounts.issue_token(user["username"], user["tier"])


DISPATCH_DIR = Path(__file__).parent / "dispatches"


@app.get("/api/dispatch/{slug}")
async def dispatch_full(slug: str):
    """The desk's-read continuation of a dispatch. Free, like the rest of the
    terminal — Seiche is a public good. New letters ship `.desk.md`; history
    on the box still carries the pre-open-access `.paid.md` name, so both are
    served. The `paid` response key is historical and kept because deployed
    frontends read it."""
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,80}$", slug):
        raise HTTPException(422, "bad slug")
    path = DISPATCH_DIR / f"{slug}.desk.md"
    if not path.exists():
        path = DISPATCH_DIR / f"{slug}.paid.md"
    if not path.exists():
        raise HTTPException(404, "no continuation for this dispatch")
    return {"slug": slug, "paid": path.read_text()}


# ---- The Week Ahead list ----------------------------------------------------
# Optional, anonymous, and it gates nothing. No `require_board` here and none
# wanted: an address is how a reader asks to be told when the Monday letter
# lands, and Seiche stays readable in full without ever giving one.
#
# Both handlers are sync `def` on purpose. The Listmonk hop is blocking stdlib
# urllib; declared `async` it would block the event loop for every other open
# tab while a wedged newsletter box burned its four-second timeout. FastAPI runs
# a sync handler on the threadpool, which is exactly what this wants.


@app.get("/api/subscribe")
def subscribe_status():
    """Is the list wired up? The front door asks before it draws anything, so a
    reader never types an address into a form that has nowhere to post it: with
    the feature off the UI renders a plain mailto link to the desk instead.

    Returns configuration state, never configuration. The endpoint URL and the
    list UUID stay on the box."""
    return subscribe_list.status()


@app.post("/api/subscribe")
def subscribe_join(request: Request, body: Any = Body(default=None)):
    """Put an address on The Week Ahead list, via Listmonk's public endpoint.

    Listmonk owns consent: the address lands `unconfirmed`, one confirmation
    link goes out, and only a click on it promotes the row to `confirmed`.
    Nothing here can shortcut that, because nothing here holds a credential
    that could.

    The address is never written to `seiche.sqlite`. That database holds
    subscriber password hashes and does not gain a marketing list; the
    subscribe module imports neither `store` nor `sqlite3`, and a test asserts
    it."""
    if not _subscribe_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "too many attempts, slow down",
                            headers={"Retry-After": "60"})

    email = subscribe_list.clean_email((body or {}).get("email") if isinstance(body, dict) else None)
    if email is None:
        raise HTTPException(422, "that does not look like an email address")

    if not subscribe_list.enabled():
        # Not an error: the list simply is not open yet. Say so plainly and
        # hand back the desk address rather than pretending it worked.
        return {"ok": False, "enabled": False, "delivered": False,
                "mailto": subscribe_list.DESK_EMAIL,
                "message": (f"The list is not open yet. Mail {subscribe_list.DESK_EMAIL} "
                            "and you go on it the day it opens.")}

    delivered = subscribe_list.submit(email)
    # `delivered` False means Listmonk was unreachable or unhappy. The reader's
    # request still succeeds, because a newsletter box having a bad day is not
    # their failure, but the message stays honest about what to do if nothing
    # lands.
    return {
        "ok": True,
        "enabled": True,
        "delivered": delivered,
        "mailto": subscribe_list.DESK_EMAIL,
        "message": (
            "Check your inbox for the confirmation link. Nothing is sent until you click it."
            if delivered else
            f"Taken. If the confirmation link does not arrive, mail {subscribe_list.DESK_EMAIL}."
        ),
    }


@app.get("/api/me")
async def me(authorization: str | None = Header(default=None)):
    ident = _bearer_identity(authorization)
    if ident is None:
        raise HTTPException(401, "not signed in")
    return ident


class AlertPrefsBody(BaseModel):
    email: str = ""
    alerts_on: bool = False


@app.get("/api/alerts/prefs")
async def get_alert_prefs(authorization: str | None = Header(default=None)):
    ident = _bearer_identity(authorization)
    if ident is None:
        raise HTTPException(401, "not signed in")
    return accounts.get_alert_prefs(ident["username"])


@app.post("/api/alerts/prefs")
async def set_alert_prefs(body: AlertPrefsBody,
                          authorization: str | None = Header(default=None)):
    """Subscriber email alerts: set the address and toggle. When on, the box's
    pull cycle emails you on regime change, Tell/crunch thresholds, and dead
    inputs. Off by default; requires an email to enable."""
    ident = _bearer_identity(authorization)
    if ident is None:
        raise HTTPException(401, "not signed in")
    try:
        return accounts.set_alert_prefs(ident["username"], body.email, body.alerts_on)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/asof/{date}")
async def asof(date: str, response: Response,
               ident: dict | None = Depends(require_board)):
    """Time Machine: the whole light board replayed as of a historical date.
    Subscriber-gated when SEICHE_ASOF_AUTH=1 (the public box); open in dev."""
    if accounts.asof_gate_enabled() and ident is None:
        raise HTTPException(401, "Time Machine replay is a subscriber feature — sign in")
    if not _DATE_RE.match(date):
        raise HTTPException(422, "date must be YYYY-MM-DD")
    payload = await assemble.snapshot_asof(date)
    if payload.get("ok") is False:
        raise HTTPException(404, payload.get("reason", "replay unavailable"))
    # A finished data-day replayed from final-vintage inputs is deterministic:
    # let browsers and the edge keep it for a day instead of re-replaying.
    response.headers["Cache-Control"] = "public, max-age=86400"
    # Historical replays can carry NaN/Inf from sparse early vintages; strict
    # JSON rejects those and the whole replay 500s. Null them out instead —
    # a missing number is honest, a dead endpoint is not.
    return _json_safe(payload)


@app.get("/api/deep")
async def deep(_ident: dict | None = Depends(require_board)):
    """History reconstruction, Tell, Turn, Playbook, PROOF backtest."""
    snap = await assemble.snapshot()
    return snap.get("deep", {})


@app.get("/api/book")
async def book(_ident: dict | None = Depends(require_board)):
    """The Book: today's positions, walk-forward P&L, live track record."""
    snap = await assemble.snapshot()
    return snap.get("deep", {}).get("book", {"ok": False, "reason": "unavailable"})


# Citability surface: registered BEFORE /api/series/{mnemonic} on purpose;
# Starlette matches in registration order and the generic route would swallow
# "index.json" and "SOFR.csv" as mnemonics. Public by design: a reading nobody
# can download and check is a vibe, and the raw registry series are free
# public data (the licensed exceptions are refused per-series).

@app.get("/api/series/index.json")
async def series_index(response: Response):
    """Public: the citable-series catalog, every registry mnemonic with its
    config metadata, native lag, export URLs, and current availability."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return methodology.series_index()


@app.get("/api/series/{mnemonic}.csv")
async def series_csv(mnemonic: str):
    """Public: date,value CSV for any series the board holds, with source,
    unit, native lag and retrieved-at in a comment header."""
    if mnemonic not in ALL_SERIES:
        raise HTTPException(404, f"unknown series '{mnemonic}'")
    restricted = methodology.csv_restriction(mnemonic)
    if restricted:
        raise HTTPException(403, restricted)
    await assemble.snapshot()  # ensure fetched
    s = store.load_series(mnemonic)
    if s is None:
        raise HTTPException(503, f"series '{mnemonic}' not yet available")
    return Response(
        content=methodology.render_series_csv(s),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{mnemonic}.csv"',
            "Cache-Control": "public, max-age=300",
        },
    )


@app.get("/api/series/{mnemonic}")
async def series(mnemonic: str, n: int = 750,
                 _ident: dict | None = Depends(require_board)):
    if mnemonic not in ALL_SERIES:
        raise HTTPException(404, f"unknown series '{mnemonic}'")
    # Same licence allow-list as the CSV twin. This route used to lean on
    # require_board, which is a deliberate no-op in production (Seiche is
    # free, SEICHE_BOARD_AUTH=0), so the full held history of licensed
    # series went out as JSON while the CSV export refused it — the format
    # changed, the act of redistribution did not. The board's own charts
    # never read this route (they draw from /api/overview), so there is no
    # display window to preserve: licensed series refuse outright, with the
    # owner named, and free public-data series stay fully open.
    restricted = methodology.csv_restriction(mnemonic)
    if restricted:
        raise HTTPException(403, restricted)
    await assemble.snapshot()  # ensure fetched
    s = store.load_series(mnemonic)
    if s is None:
        raise HTTPException(503, f"series '{mnemonic}' not yet available")
    return {"provenance": s.provenance(), "points": s.tail_records(n)}


@app.get("/api/config")
async def config_view(_ident: dict | None = Depends(require_board)):
    """The editorial voice, read-only: what the operator can tune and where."""
    return {
        "composite_weights": COMPOSITE_WEIGHTS,
        "regimes": [{"below": c, "name": n} for c, n in REGIMES],
        "episodes": EPISODES,
        "alert_rules": ALERT_RULES,
        "tuning_file": "backend/seiche/config.py",
    }


@app.get("/api/alerts")
async def alerts(n: int = 50, _ident: dict | None = Depends(require_board)):
    """Recent alert log (written by `seiche alert` / `seiche watch`)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT fired_at, rule, state_key, message FROM alerts ORDER BY fired_at DESC LIMIT ?",
            (n,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return {
        "alerts": [
            {"fired_at": r[0], "rule": r[1], "state": r[2], "message": r[3]} for r in rows
        ]
    }


@app.get("/api/brief", response_class=PlainTextResponse)
async def brief_text(_ident: dict | None = Depends(require_board)):
    """This morning's desk note, rendered as markdown."""
    from seiche import brief as brief_mod

    snap = await assemble.snapshot()
    return brief_mod.render_markdown(snap)


@app.get("/api/ask")
async def ask(q: str, request: Request):
    """Desk assistant: answers grounded strictly in the live board.
    Per-IP rate-limited (it calls the LLM)."""
    from seiche import ai

    if not _ask_limiter.allow(_client_ip(request)):
        raise HTTPException(429, "too many questions — slow down",
                            headers={"Retry-After": "60"})
    if not q or len(q) > 600:
        raise HTTPException(422, "q must be 1-600 characters")
    snap = await assemble.snapshot()
    return await ai.ask(q, snap)


@app.get("/api/pit")
async def pit(n: int = 400, _ident: dict | None = Depends(require_board)):
    """The forward-accruing as-published index record (no reconstruction)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT key, payload FROM blobs WHERE key LIKE 'pit:%' ORDER BY key DESC LIMIT ?",
            (n,),
        ).fetchall()
    finally:
        conn.close()
    import json as _json

    return {"records": [_json.loads(p) for _, p in reversed(rows)]}


@app.get("/api/health")
async def health(response: Response, require_rebuilt: bool = False):
    """Cached availability, plus an optional current-process release gate."""
    snap = assemble.cached_snapshot()
    if snap is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "warming_or_unavailable",
                "version": assemble.VERSION_LABEL,
            },
            headers={"Cache-Control": "no-store", "Retry-After": "10"},
        )
    if require_rebuilt and not assemble.cached_snapshot_was_rebuilt():
        return JSONResponse(
            status_code=503,
            content={
                "status": "rebuilding_from_last_known_good",
                "version": assemble.VERSION_LABEL,
                "serving_generated_at": snap.get("generated_at"),
            },
            headers={"Cache-Control": "no-store", "Retry-After": "10"},
        )
    response.headers["Cache-Control"] = "no-store"
    return {
        "generated_at": snap["generated_at"],
        "version": snap.get("version"),
        "faults": snap["faults"],
        "provenance": snap["provenance"],
    }


@app.get("/api/badge/record")
async def badge_record():
    """Public: the sealed-record badge (shields.io endpoint schema). Point
    img.shields.io/endpoint at this URL and a README badge stays honest on its
    own — green only while the as-published chain has no business-day holes."""
    from seiche import badge

    return badge.record_badge()


@app.get("/api/notary")
async def notary_ledger(n: int = 200):
    """Public: the tamper-evident ledger of every as-published reading, and how
    to verify it yourself. This is the trust asset made checkable — no auth, on
    purpose."""
    from seiche import notary

    return {
        "chain": notary.verify_chain(),
        "head": notary.head(),
        "genesis": notary.GENESIS,
        "anchor": "opentimestamps (bitcoin)",
        "entries": notary.entries(n),
        "how_to_verify": (
            "each reading is canonical-JSON SHA-256'd; links chain as "
            "sha256(prev|digest|utc|date). Recompute from GENESIS to confirm no "
            "past call was altered or reordered. Each digest's .ots proof settles "
            "in Bitcoin (verify with the `ots` tool) so the date cannot be backdated."
        ),
        "proof_url": "/api/notary/proof/{record_sha256}",
    }


@app.get("/api/notary/proof/{sha256}")
async def notary_proof(sha256: str):
    """Public: the raw OpenTimestamps (.ots) proof for a digest, so anyone can
    run `ots verify` and confirm the Bitcoin timestamp for themselves."""
    from seiche import notary

    if not re.match(r"^[0-9a-f]{64}$", sha256):
        raise HTTPException(422, "digest must be 64 lowercase hex chars")
    proof = notary.proof_for(sha256)
    if proof is None:
        raise HTTPException(404, "no proof yet (unanchored — awaiting the next stamp)")
    return Response(content=proof, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{sha256[:16]}.ots"'})


# ---- Signed as-published record (attest layer) --------------------------------
# The notary chains readings; the attest layer signs them (Ed25519) and anchors
# each day's record hash to Bitcoin via OpenTimestamps. These endpoints are
# public and read-only, and serve commitments only — day, record hash,
# signature, anchor status — never payloads, so the verification surface stays
# identical whatever the stream carries.

@app.get("/api/attest/pubkey")
async def attest_pubkey():
    """Public: the operator's Ed25519 public key and the signing domain, so a
    reader can verify every signature with no trust in this server."""
    from seiche import attest

    return {
        "public_key": attest.public_key_hex(),
        "algo": attest.ALGO,
        "domain": attest.DOMAIN,
        "message_format": "{domain}:{stream}:{day}:{record_hash}",
        "how_to_verify": (
            "recompute each record hash from the ledger line, build the message "
            "above, and check the Ed25519 signature against this key; OTS proofs "
            "settle in Bitcoin (verify with the `ots` tool)."
        ),
    }


@app.get("/api/attest/stream/{stream}")
async def attest_stream(stream: str, n: int = 400):
    """Public: per-day commitments of a ledger stream — record hash, signature,
    anchor status. Never payloads."""
    from seiche import attest

    if not attest._STREAM_RE.match(stream):
        raise HTTPException(422, "invalid stream name")
    records = attest.read_records(stream)
    if not records:
        raise HTTPException(404, f"no committed records on stream '{stream}'")
    sigs = {s["record_hash"]: s for s in attest.read_signatures(stream)}
    anchors: dict[str, dict] = {}
    for a in attest.read_anchors(stream):  # later lines (upgrades) win
        anchors[a["day"]] = a
    days = []
    for rec in records[-n:]:
        s = sigs.get(rec["hash"])
        a = anchors.get(rec["day"])
        days.append({
            "day": rec["day"],
            "record_hash": rec["hash"],
            "prev_hash": rec["prev_hash"],
            "signature": {
                "sig": s["sig"], "public_key": s["public_key"],
                "algo": s["algo"], "signed_at": s["signed_at"],
            } if s else None,
            "anchor": {
                "status": a["status"],
                "calendar": a.get("calendar"),
                "bitcoin_height": a.get("bitcoin_height"),
                "submitted_at": a.get("submitted_at"),
            } if a else None,
        })
    return {
        "stream": stream,
        "n_records": len(records),
        "verification": attest.verify_stream(stream),
        "days": days,
    }


@app.get("/api/attest/verify/{stream}")
async def attest_verify(stream: str):
    """Public: the full independent verification verdict for a stream (chain,
    hashes, signatures, anchors)."""
    from seiche import attest

    if not attest._STREAM_RE.match(stream):
        raise HTTPException(422, "invalid stream name")
    if not attest.read_records(stream):
        raise HTTPException(404, f"no committed records on stream '{stream}'")
    return attest.verify_stream(stream)


# ---- MCP over HTTP ----------------------------------------------------------
# The hosted, metered Model Context Protocol endpoint: any AI agent adds this
# URL and reads the board as tools. Anonymous callers get the free public
# surface (capped per IP per day); a valid subscriber bearer token unlocks the
# full surface at the tier's quota. Reuses the exact stdio dispatch, so there is
# one tool implementation for both transports.
#
# This is a SYNC endpoint on purpose: the tool handlers block on
# mcp_server._run(), which submits assemble coroutines to THIS process's one
# serving loop and waits — a wait that would deadlock on the loop itself, so
# it must happen in the threadpool a sync route provides.

MCP_SERVER_ERROR = -32000  # JSON-RPC server-defined error (rate limit, bad body)


def _mcp_usage_headers(meter: dict | None) -> dict:
    if not meter:
        return {}
    h = {"X-MCP-Usage-Used": str(meter["used"])}
    if meter["limit"] is not None:
        h["X-MCP-Usage-Limit"] = str(meter["limit"])
        h["X-MCP-Usage-Remaining"] = str(meter["remaining"])
    return h


def _mcp_quota_result(msg_id: Any, meter: dict) -> dict:
    text = (
        f"ERROR: daily MCP quota reached ({meter['used']}/{meter['limit']} "
        f"tool calls today). Upgrade for a higher limit: {MCP_UPGRADE_URL}"
    )
    return {"jsonrpc": "2.0", "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": True}}


def _log_mcp_activation(message: Any, response: Any,
                        surface: str, origin: str) -> None:
    """Record the conversion event without caller data or tool arguments."""
    if not (isinstance(message, dict)
            and message.get("method") == "tools/call"):
        return
    params = message.get("params")
    requested = params.get("name") if isinstance(params, dict) else None
    tool = (requested if isinstance(requested, str)
            and requested in mcp_server.TOOLS else "unknown")
    result = response.get("result") if isinstance(response, dict) else None
    failed = (not isinstance(response, dict) or "error" in response
              or (isinstance(result, dict) and result.get("isError") is True))
    _mcp_activation_log.info(
        "mcp_activation product=seiche surface=%s tool=%s outcome=%s origin=%s",
        surface, tool, "error" if failed else "success", origin,
    )


@app.get("/mcp")
def mcp_http_get() -> Response:
    """Streamable-HTTP GET channel (SSE). The transport is stateless
    single-response mode and never emits server-initiated messages, so the
    stream closes right after opening; clients and registry indexers probe
    it only to judge transport compliance, and a 405 here reads as the SSE
    channel being absent."""
    return Response(
        ": stateless transport; no server-initiated messages\n\n",
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform"},
    )


@app.post("/mcp")
def mcp_http(request: Request, body: Any = Body(default=None),
             authorization: str | None = Header(default=None)):
    """Streamable-HTTP MCP transport (single-response mode). Accepts one
    JSON-RPC message or a batch; returns the JSON-RPC response(s), or 202 for a
    notification-only body."""
    ident = _bearer_identity(authorization)
    # An anonymous caller is ALWAYS the public surface. This used to read
    # `ident is None and _board_gate_enabled()`, which coupled MCP
    # entitlements to SEICHE_BOARD_AUTH — a setting about the BROWSER board.
    # With the gate off (the shipped default) the conjunction was false for
    # everyone, so every anonymous caller received the full surface: the
    # positioning book, the desk brief and the institutional-flows engine
    # were readable by plain unauthenticated curl.
    #
    # Seiche stays a free public good: the conclusion, the analogs, the PROOF
    # scoreboard and data health remain anonymous, per-tool, as they always
    # were. What the board gate must never decide is who may read the
    # proprietary derived engines.
    public = ident is None
    origin = "edge" if request.headers.get("X-Forwarded-For") else "direct"
    ip = _client_ip(request)

    burst_key = ident["username"] if ident else ip
    if not _mcp_limiter.allow(burst_key):
        return JSONResponse(
            mcp_server._error(None, MCP_SERVER_ERROR, "rate limited — slow down"),
            status_code=429, headers={"Retry-After": "60"},
        )

    if body is None:
        return JSONResponse(
            mcp_server._error(None, mcp_server.PARSE_ERROR, "empty or non-JSON body"),
            status_code=400,
        )

    msgs = body if isinstance(body, list) else [body]
    if len(msgs) > MCP_MAX_BATCH:
        # one HTTP request only costs one rate-limiter hit, so an unbounded
        # batch would evade the per-minute ceiling and the meter.
        return JSONResponse(
            mcp_server._error(None, MCP_SERVER_ERROR,
                              f"batch too large (max {MCP_MAX_BATCH} messages)"),
            status_code=413,
        )
    # x402 pay-per-call: anonymous caller + a priced (subscriber) tool. The
    # whole branch only exists when the operator set SEICHE_X402_PAY_TO, and
    # it is fail-closed — no verified-and-settled payment, no tool result.
    if x402.enabled() and public:
        pay_header = request.headers.get("X-PAYMENT")
        single = msgs[0] if len(msgs) == 1 and isinstance(msgs[0], dict) else None
        tool = ((single or {}).get("params") or {}).get("name") \
            if (single or {}).get("method") == "tools/call" else None
        priced = x402.price_usd(tool)
        if pay_header is not None and priced is None:
            return JSONResponse(
                mcp_server._error(None, MCP_SERVER_ERROR,
                                  "X-PAYMENT covers exactly one tools/call for a priced tool"),
                status_code=400,
            )
        if priced is not None:
            resource = "https://api.seiche.info/mcp"
            reqs = x402.requirements(tool, resource)
            if pay_header is None:
                return JSONResponse(
                    x402.payment_required(tool, resource,
                                          f"{tool} is a paid tool on the anonymous surface"),
                    status_code=402,
                )
            payment = x402.decode_payment(pay_header)
            if payment is None:
                return JSONResponse(
                    x402.payment_required(tool, resource, "X-PAYMENT header malformed"),
                    status_code=402,
                )
            ok, why = x402.verify(payment, reqs)
            if not ok:
                return JSONResponse(
                    x402.payment_required(tool, resource, why), status_code=402)
            settled, receipt = x402.settle(payment, reqs)
            if not settled:
                return JSONResponse(
                    x402.payment_required(
                        tool, resource,
                        str(receipt.get("errorReason") or "settlement failed")),
                    status_code=402,
                )
            # paid: this one call runs on the full surface, no quota charged
            try:
                resp = mcp_server.dispatch(single, public=False)
            except Exception:
                resp = mcp_server._error(single.get("id"),
                                         mcp_server.INTERNAL_ERROR, "internal error")
            _log_mcp_activation(single, resp, "paid", origin)
            return JSONResponse(resp, headers={
                "X-PAYMENT-RESPONSE": x402.settle_header(receipt)})

    ukey = usage.key_for(ident, ip)
    limit = usage.quota_for(ident)
    responses: list[dict] = []
    meter: dict | None = None

    for m in msgs:
        billable = (
            isinstance(m, dict)
            and m.get("method") in mcp_server.BILLABLE_METHODS
            and "id" in m
        )
        if billable:
            meter = usage.charge(ukey, limit)
            if not meter["allowed"]:
                responses.append(_mcp_quota_result(m.get("id"), meter))
                continue
        try:
            resp = mcp_server.dispatch(m, public=public)
        except Exception:
            # dispatch is defensive, but never let one bad message 500 the batch.
            mid = m.get("id") if isinstance(m, dict) else None
            resp = mcp_server._error(mid, mcp_server.INTERNAL_ERROR, "internal error")
        _log_mcp_activation(
            m, resp, "public" if public else "subscriber", origin)
        if (resp is not None and x402.enabled() and public
                and isinstance(m, dict) and m.get("method") == "tools/list"):
            # advertise the payable tools to wallet-holding agents
            resp = x402.annotate_tools_list(resp)
        if resp is not None:
            responses.append(resp)

    headers = _mcp_usage_headers(meter)
    if any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs):
        headers["Mcp-Session-Id"] = secrets.token_hex(16)

    if not responses:                       # notification-only body
        return Response(status_code=202, headers=headers)
    payload = responses if isinstance(body, list) else responses[0]
    return JSONResponse(payload, headers=headers)


@app.post("/api/provision")
async def provision_webhook(request: Request,
                           x_seiche_signature: str | None = Header(default=None)):
    """The payment -> account hook. A payment processor (BTCPay/NOWPayments/
    Stripe) or an operator adapter POSTs a signed JSON body when a payment
    confirms; Seiche provisions the subscriber and returns the credentials.
    Fail-closed: disabled unless SEICHE_PROVISION_SECRET is set, and every call
    must carry a valid HMAC-SHA256 signature of the raw body.

    The signature must cover the exact bytes on the wire, so we read the raw
    body ourselves rather than letting FastAPI parse it first."""
    from starlette.concurrency import run_in_threadpool
    import json as _json

    if not provisioning.enabled():
        raise HTTPException(503, "provisioning is not enabled on this server")
    raw = await request.body()
    if not provisioning.verify_signature(raw, x_seiche_signature):
        raise HTTPException(401, "bad or missing signature")
    try:
        data = _json.loads(raw or b"{}")
    except _json.JSONDecodeError:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "body must be a JSON object")
    try:
        # provision() does blocking SQLite + (optional) SMTP — keep it off the
        # event loop so a slow mail server can't stall the API.
        return await run_in_threadpool(
            provisioning.provision,
            data.get("tier", ""),
            email=data.get("email", "") or "",
            username=data.get("username", "") or "",
            payment_ref=data.get("payment_ref", "") or "",
            amount=data.get("amount"),
            currency=data.get("currency", "") or "",
        )
    except provisioning.ProvisionError as exc:
        raise HTTPException(422, str(exc))


@app.get("/mcp/usage")
def mcp_usage_report(request: Request, authorization: str | None = Header(default=None)):
    """The caller's meter for today — used by an agent (or a billing UI) to see
    how much of the daily quota remains."""
    ident = _bearer_identity(authorization)
    ip = _client_ip(request)
    ukey = usage.key_for(ident, ip)
    limit = usage.quota_for(ident)
    used = usage.peek(ukey)
    return {
        "tier": ident["tier"] if ident else "anon",
        "used_today": used,
        "daily_limit": limit,
        "remaining": None if limit is None else max(0, limit - used),
        "upgrade_url": MCP_UPGRADE_URL,
    }


# Serve the built frontend when present (single-process deploy).
_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
