"""Central branded MCP ingress with bounded operational metadata, never payload logs.

Source collection remains with each product. This service centralizes access and
operations without re-licensing or silently copying source datasets into training.
"""
from __future__ import annotations
import asyncio
import base64
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
import secrets
import time
from pathlib import Path

import httpx
from fastapi import Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from .models import NetworkError
from .analytics import Analytics
from .geography import verified_country
from .upstream_auth import public_call_credential, UpstreamAuthUnavailable
from .extra_products import EXTRA_PRODUCTS, EXTRA_PRODUCT_TARGETS
from . import data_catalog, demand

PRODUCTS = {
    "seiche": ("Seiche", "https://api.seiche.info/mcp"),
    "liquilens": ("LiquiLens", "https://api.liquilens.in/mcp"),
    "undertow": ("Undertow", "https://api.seiche.info/undertow/mcp"),
    "palimpsest": ("Palimpsest", "https://api.seiche.info/palimpsest/mcp"),
    "noisefloor": ("NoiseFloor", "https://api.seiche.info/noisefloor/mcp"),
    "narcoscope": ("NarcoScope", "https://www.narcoscope.com/mcp"),
    "wake": ("Wake", "http://127.0.0.1:18798/mcp/"),
} | EXTRA_PRODUCT_TARGETS
METHODS = {"initialize", "notifications/initialized", "notifications/cancelled", "ping",
           "tools/list", "tools/call", "resources/list", "resources/read", "resources/templates/list",
           "prompts/list", "prompts/get", "logging/setLevel", "completion/complete", "server/discover"}
METADATA_PENDING_LIMIT = 8
METADATA_WAIT_SECONDS = 0.05
UPSTREAM_CLOSE_SECONDS = 5.0


def _mcp_name(value):
    """Mirror a bounded parsed MCP name, never an untrusted caller header."""
    if not isinstance(value, str) or not value:
        raise NetworkError("invalid_request", "This MCP method requires a bounded name or URI", 400)
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise NetworkError("invalid_request", "MCP name or URI is not valid UTF-8", 400) from None
    if len(encoded) > 2048:
        raise NetworkError("invalid_request", "MCP name or URI exceeds 2048 UTF-8 bytes", 400)
    if (all(32 <= byte <= 126 for byte in encoded) and value == value.strip(" \t")
            and not value.startswith("=?base64?") and not value.endswith("?=")):
        return value
    # The reviewed modern MCP decoder uses this UTF-8 base64 sentinel to
    # preserve Unicode, control characters and meaningful outer whitespace.
    return "=?base64?" + base64.b64encode(encoded).decode("ascii") + "?="

# Projection of the probe producer's public metadata contract. Unknown additions
# stay private until deliberately admitted here; source clocks and their policy
# explanations remain available, including nested admission and funding checks.
REPORT_FIELDS = {"schema", "evaluated_at", "products", "traffic_class", "raw_payloads_retained", "interpretation"}
SOURCE_FIELDS = set("""product transport tool freshness source_age_seconds clocks reasons scope coverage
source_reason_counts critical_subset definition source_ids missing_source_ids announced_not_yet_effective_sources
current_observation_required rights publication_allowed policy use_boundary historical_reference_clocks
clock_freshness admission_check source_policy native_availability transport_duration_ms inspected_rows
observation_clock_rows fresh stale unknown restricted status source_id field observed_at cadence max_age_seconds
clock_precision policy_age_seconds reason retired_from replacement excluded_from_current_input_health
missed_release_weeks latest_expected_release_at expected_observation_period_start schedule_is_publication_receipt
effective_at available_at availability_clock_kind availability_age_seconds current_observation_available
clock_role forward_effective_days reference_period_age_seconds expected_observation_date publication_timezone
unverified_public_holidays_not_excused
publication_cutoff_local grace_days upstream_auth expires_at checked_at age_seconds cached cache_ttl_seconds
next_attempt_at""".split())


def _public_metadata(value, *, depth=0):
    if depth > 12:
        raise ValueError("Metadata nesting exceeds report contract")
    if isinstance(value, dict):
        projected = {}
        for key, item in value.items():
            if key not in SOURCE_FIELDS:
                continue
            if key == "source_reason_counts":
                if not isinstance(item, dict) or len(item) > 128:
                    raise ValueError("Invalid source reason counts")
                projected[key] = {label: count for label, count in item.items()
                                  if isinstance(label, str) and len(label) <= 160
                                  and all(c.isalnum() or c == "_" for c in label)
                                  and type(count) is int and count >= 0}
            else:
                projected[key] = _public_metadata(item, depth=depth+1)
        return projected
    if isinstance(value, list):
        if len(value) > 2000:
            raise ValueError("Too many source rows")
        return [_public_metadata(item, depth=depth+1) for item in value]
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str) and len(value) <= 4096:
        return value
    if type(value) in {int, float} and math.isfinite(value):
        return value
    raise ValueError("Invalid source metadata")


def _public_source_report(report):
    if not isinstance(report, dict) or report.get("schema") != "seiche-agent-network.freshness.v1":
        raise ValueError("Unknown source report schema")
    stamp = report.get("evaluated_at")
    if not isinstance(stamp, str) or len(stamp) > 64:
        raise ValueError("Missing report clock")
    observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("Report clock must be timezone-aware")
    rows = report.get("products")
    if not isinstance(rows, list) or len(rows) > len(PRODUCTS):
        raise ValueError("Invalid product report rows")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("product"), str)
                or row["product"] not in PRODUCTS or row["product"] in seen
                or row.get("transport") not in {"available", "unavailable", "tool_error"}
                or row.get("freshness") not in {"fresh", "stale", "unknown", "restricted"}):
            raise ValueError("Invalid product report")
        seen.add(row["product"])
    result = {key: _public_metadata(value) for key, value in report.items() if key in REPORT_FIELDS}
    age = time.time() - observed.timestamp()
    return {**result, "monitor_current": 0 <= age < 1200, "monitor_age_seconds": round(age)}


def _rpc_result(body, media, request_id):
    """Inspect only the matching bounded RPC response; never retain its body."""
    try:
        if media == "application/json":
            value=json.loads(body)
            return value if isinstance(value,dict) and value.get("id")==request_id else None
        if media == "text/event-stream":
            text=body.decode("utf-8").replace("\r\n","\n").replace("\r","\n")
            for event in text.split("\n\n"):
                data="\n".join(line[5:].lstrip(" ") for line in event.split("\n") if line.startswith("data:"))
                if not data:
                    continue
                value=json.loads(data)
                if isinstance(value,dict) and value.get("id")==request_id and ("result" in value or "error" in value):
                    return value
    except (ValueError,UnicodeError,RecursionError):
        pass
    return None


class Gateway:
    def __init__(self, store, *, transport=None, clock=time.time):
        self.store, self.clock = store, clock
        self.client = httpx.AsyncClient(transport=transport, follow_redirects=False,
            timeout=httpx.Timeout(30.0, read=90.0), limits=httpx.Limits(max_connections=64))
        self.sessions = {}
        self.active = 0
        self.buckets = Counter()
        self.window = 0
        self.measurement_failures = 0
        self.measurement_diagnostics = Counter()
        self._cleanup_tasks = set()
        self._record_tasks = set()
        self._closing = False
        self.tool_registry = data_catalog.tool_registry()
        self.analytics = Analytics(store, tool_registry=self.tool_registry)
        self.geo_secret = os.environ.get("SEICHE_GEO_HMAC_KEY")

    async def aclose(self):
        self._closing = True
        if self._cleanup_tasks:
            await asyncio.wait(self._cleanup_tasks, timeout=UPSTREAM_CLOSE_SECONDS + METADATA_WAIT_SECONDS + 0.1)
        if self._record_tasks:
            await asyncio.wait(self._record_tasks, timeout=METADATA_WAIT_SECONDS)
        try:
            await asyncio.wait_for(self.client.aclose(), timeout=UPSTREAM_CLOSE_SECONDS)
        except Exception:
            self.measurement_failures += 1

    def record(self, principal, product, method, traffic, status, elapsed, *, tool="other", country="unknown", result=None,
               classification=None, response_state="unknown"):
        outcome, freshness, age = "unknown", "unknown", None
        if isinstance(result, dict):
            if method == "tools/call" and isinstance(result.get("result"),dict) and type(result["result"].get("isError")) is bool:
                outcome = "error" if result["result"]["isError"] else "success"
            elif (method == "tools/call" and isinstance(result.get("result"),dict)
                    and "isError" not in result["result"]
                    and (isinstance(result["result"].get("content"), list)
                         or isinstance(result["result"].get("structuredContent"), dict))):
                outcome = "success"  # MCP CallToolResult.isError defaults to false.
            elif method == "tools/call" and "error" in result:
                outcome = "error"
            if method == "tools/call":
                from .freshness import inspect_result
                verdict = inspect_result(product,tool,result,now=self.clock())
                freshness, age = verdict["freshness"], verdict.get("source_age_seconds")
                if freshness not in {"fresh","stale","restricted"}:
                    freshness = "unknown"
        self.analytics.record(principal, product=product,tool=tool,method=method,http_status=status,
            duration_ms=max(0,min(elapsed,86400000)),country=country,traffic_class=traffic,
            freshness=freshness,source_age_seconds=age,tool_outcome=outcome,
            classification=classification, response_state=response_state)

    def diagnostics(self):
        names = ("submitted", "committed", "late", "late_committed", "dropped_capacity", "dropped_closing",
                 "permanent_errors", "completion_unknown", "upstream_close_errors")
        return {"scope": "current_process_only", "durable_queue": False, "pending": len(self._record_tasks),
                **{name: self.measurement_diagnostics[name] for name in names},
                "meaning": "Late writes may commit after the response deadline. Dropped entries were never queued. "
                           "Permanent errors failed the database transaction. Process restart can lose pending work "
                           "and resets these counters; this is not a durable delivery guarantee."}

    def report(self, principal):
        report={**self.analytics.report(principal),"collection_complete":None,
                "coverage":"Forwarded central ingress exchanges only; other endpoints, pre-forward rejections and process downtime are outside these counts"}
        if principal is None:
            report["process_measurement_failures"]=self.measurement_failures
            report["measurement_diagnostics"] = self.diagnostics()
        return report

    async def _record(self,*args,**kwargs):
        if self._closing or len(self._record_tasks) >= METADATA_PENDING_LIMIT:
            self.measurement_failures += 1
            self.measurement_diagnostics["dropped_closing" if self._closing else "dropped_capacity"] += 1
            return
        task = asyncio.create_task(asyncio.to_thread(self.record, *args, **kwargs))
        self._record_tasks.add(task)
        self.measurement_diagnostics["submitted"] += 1
        counted = False
        late = False

        def finished(done):
            nonlocal counted
            self._record_tasks.discard(done)
            failed = done.cancelled() or done.exception() is not None
            if done.cancelled():
                self.measurement_diagnostics["completion_unknown"] += 1
            elif failed:
                self.measurement_diagnostics["permanent_errors"] += 1
            else:
                self.measurement_diagnostics["committed"] += 1
                if late:
                    self.measurement_diagnostics["late_committed"] += 1
            if failed and not counted:
                self.measurement_failures += 1
                counted = True
        task.add_done_callback(finished)
        # asyncio.wait does not cancel the worker on timeout/caller cancellation.
        # Its capacity remains occupied until the actual write finishes.
        done, _ = await asyncio.wait({task}, timeout=METADATA_WAIT_SECONDS)
        if not done and not counted:
            late = True
            self.measurement_diagnostics["late"] += 1
            self.measurement_failures += 1
            counted = True

    async def _finish(self,upstream,args,kwargs):
        try:
            await asyncio.wait_for(upstream.aclose(), timeout=UPSTREAM_CLOSE_SECONDS)
        except Exception:
            self.measurement_failures += 1
            self.measurement_diagnostics["upstream_close_errors"] += 1
        finally:
            await self._record(*args,**kwargs)

    async def _cleanup(self,upstream,args,kwargs):
        task=asyncio.create_task(self._finish(upstream,args,kwargs))
        self._cleanup_tasks.add(task)
        def finished(done):
            self._cleanup_tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        await asyncio.shield(task)

    async def proxy(self, product, request):
        if product not in PRODUCTS or request.url.query:
            raise NetworkError("not_found", "Unknown product or unsupported query", 404)
        authorization = request.headers.get("authorization", "")
        principal = None
        if "authorization" in request.headers:
            if not authorization.startswith("Bearer ") or not authorization[7:] or len(authorization[7:]) > 256:
                raise NetworkError("unauthorized", "Bearer credential required", 401)
            principal = await asyncio.to_thread(self.store.authenticate, authorization[7:])
        identity = principal.credential_id if principal else "anonymous"
        now = self.clock()
        minute = int(now // 60)
        if minute != self.window:
            self.buckets.clear(); self.window = minute
        if len(self.buckets) >= 4096 and identity not in self.buckets:
            raise NetworkError("capacity", "Ingress capacity reached", 429)
        if self._closing or self.active + len(self._cleanup_tasks) >= 64 or self.buckets[identity] >= (600 if principal else 1200):
            raise NetworkError("rate_limited", "Ingress operating limit reached; retry later", 429)
        self.buckets[identity] += 1
        self.sessions = {k:v for k,v in self.sessions.items() if v[3] > now}
        supplied_session = request.headers.get("mcp-session-id")
        if product == "undertow" and supplied_session:
            raise NetworkError("session_unsupported", "Undertow uses stateless public requests", 404)
        upstream_session = None
        if supplied_session:
            session = self.sessions.get(supplied_session)
            if not session or session[0] != product or session[1] != identity:
                raise NetworkError("session_missing", "Reinitialize this product session", 404)
            upstream_session = session[2]
        if request.method in {"GET", "DELETE"} and not upstream_session:
            raise NetworkError("session_missing", "Session required", 400)
        method = request.method
        tool = "none"
        request_id = None
        body = await request.body()
        if len(body) > 65536:
            raise NetworkError("too_large", "Request exceeds 64 KiB", 413)
        if request.method == "POST":
            try:
                payload = json.loads(body)
                method = payload.get("method")
                request_id = payload.get("id")
                if payload.get("jsonrpc") != "2.0" or method not in METHODS:
                    raise ValueError()
                if method == "tools/call" and isinstance(payload.get("params"),dict):
                    tool = "other"
                    requested_tool = payload["params"].get("name")
                    if requested_tool in self.tool_registry[product]:
                        tool = requested_tool
            except (ValueError, AttributeError, TypeError, RecursionError):
                raise NetworkError("invalid_request", "A supported MCP JSON-RPC object is required")
        if product in EXTRA_PRODUCTS and method == "tools/call" and tool not in self.tool_registry[product]:
            raise NetworkError("tool_unavailable", "Only reviewed public read tools are admitted for this product", 403)
        try:
            upstream_token = public_call_credential(product, method, tool, now=now)
        except UpstreamAuthUnavailable:
            if product == "undertow" and method == "tools/call" and tool == "other":
                raise NetworkError("tool_unavailable", "Only the public Undertow tool surface is available", 403) from None
            raise NetworkError("upstream_auth_unavailable", "Product upstream credential requires operator renewal", 503) from None
        traffic = request.headers.get("x-financial-evidence-traffic-class", "unknown")
        if traffic not in {"synthetic", "internal", "backtest", "shadow", "paper", "operator_reported_production"}:
            traffic = "unknown"
        public_path = request.url.path
        prefix = request.scope.get("root_path", "")
        if prefix and not public_path.startswith(prefix + "/"):
            public_path = prefix + public_path
        country = verified_country(request.headers,request.method,public_path,self.geo_secret,now=now)
        headers = {"Content-Type":"application/json", "Accept":"application/json, text/event-stream",
                   "Accept-Encoding":"identity", "X-Financial-Evidence-Traffic-Class":traffic}
        if upstream_token:
            headers["Authorization"] = "Bearer " + upstream_token
        if upstream_session:
            headers["Mcp-Session-Id"] = upstream_session
        version = request.headers.get("mcp-protocol-version")
        if version in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"}:
            headers["MCP-Protocol-Version"] = version
        if version == "2026-07-28":
            headers["Mcp-Method"] = method
            if method in {"tools/call", "prompts/get", "resources/read"}:
                params = payload.get("params")
                if not isinstance(params, dict):
                    raise NetworkError("invalid_request", "This MCP method requires named parameters", 400)
                headers["Mcp-Name"] = _mcp_name(params.get("uri" if method == "resources/read" else "name"))
        # Body ingestion may have yielded after the first capacity check.
        if self._closing or self.active + len(self._cleanup_tasks) >= 64:
            raise NetworkError("rate_limited", "Ingress operating limit reached; retry later", 429)
        self.active += 1
        try:
            upstream_request = self.client.build_request(request.method,
                PRODUCTS[product][1], headers=headers, content=body)
            # Products share hosts and this HTTP client. An HTTP cookie jar
            # must never carry the service identity into another product or
            # an intentionally anonymous discovery/status request.
            upstream_request.headers.pop("cookie", None)
            upstream = await self.client.send(upstream_request, stream=True)
        except asyncio.CancelledError:
            self.active -= 1
            await self._record(principal,product,method,traffic,499,round((self.clock()-now)*1000),tool=tool,country=country)
            raise
        except (httpx.HTTPError, OSError):
            self.active -= 1
            await self._record(principal,product,method,traffic,502,round((self.clock()-now)*1000),tool=tool,country=country)
            raise NetworkError("upstream_unavailable", "Product temporarily unavailable", 502)
        if 300 <= upstream.status_code < 400:
            self.active -= 1
            await self._cleanup(upstream,(principal,product,method,traffic,502,round((self.clock()-now)*1000)),{"tool":tool,"country":country})
            raise NetworkError("upstream_redirect", "Product redirect requires operator review", 502)
        if upstream.headers.get("content-encoding", "identity").strip().lower() != "identity":
            self.active -= 1
            await self._cleanup(upstream,(principal,product,method,traffic,502,round((self.clock()-now)*1000)),{"tool":tool,"country":country})
            raise NetworkError("upstream_encoding", "Compressed product responses are not supported", 502)
        output_headers = {"Cache-Control":"no-store", "X-Product":product}
        media = upstream.headers.get("content-type", "application/json").split(";")[0]
        if media not in {"application/json", "text/event-stream"}:
            media = "application/octet-stream"
        new_session = upstream.headers.get("mcp-session-id")
        if new_session and product == "undertow":
            self.active -= 1
            await self._cleanup(upstream,(principal,product,method,traffic,502,round((self.clock()-now)*1000)),{"tool":tool,"country":country})
            raise NetworkError("upstream_session_contract", "Product session contract requires operator review", 502)
        if new_session:
            if len(new_session) > 512 or len(self.sessions) >= 4096:
                self.active -= 1
                await self._cleanup(upstream,(principal,product,method,traffic,503,round((self.clock()-now)*1000)),{"tool":tool,"country":country})
                raise NetworkError("capacity", "Session capacity reached", 503)
            public_session = supplied_session or secrets.token_urlsafe(32)
            self.sessions[public_session] = (product,identity,new_session,now+3600)
            output_headers["Mcp-Session-Id"] = public_session
        if request.method == "DELETE" and supplied_session:
            self.sessions.pop(supplied_session,None)

        captured = bytearray()
        state={"status":upstream.status_code,"complete":False,"released":False}

        async def release():
            if state["released"]:
                return
            state["released"]=True
            self.active-=1
            status=state["status"] if state["complete"] or state["status"]>=400 else 499
            result=None
            if state["complete"] and captured:
                result=_rpc_result(captured,media,request_id)
            await self._cleanup(upstream,(principal,product,method,traffic,status,round((self.clock()-now)*1000)),{"tool":tool,"country":country,"result":result})

        async def stream():
            total = 0
            try:
                async with asyncio.timeout(max(.01,120-(self.clock()-now))):
                    # Native streaming responses stay raw; never decompress an
                    # upstream body before enforcing the response-byte cap.
                    async def chunks():
                        if upstream.is_stream_consumed:
                            yield upstream.content
                        else:
                            # Iterate the raw transport stream directly so its
                            # EOF close also goes through our five-second close
                            # budget, rather than aiter_raw's implicit close.
                            async for chunk in upstream.stream:
                                yield chunk
                    async for chunk in chunks():
                        total += len(chunk)
                        if total > 4_194_304:
                            raise RuntimeError("Product response exceeded ingress limit")
                        if method == "tools/call" and media in {"application/json","text/event-stream"}:
                            captured.extend(chunk)
                        yield chunk
                state["complete"]=True
            except asyncio.CancelledError:
                state["status"]=499
                raise
            except Exception:
                state["status"]=502
                raise
            finally:
                await release()

        class ManagedResponse(StreamingResponse):
            async def __call__(self,scope,receive,send):
                try:
                    await super().__call__(scope,receive,send)
                finally:
                    await release()
        return ManagedResponse(stream(),status_code=upstream.status_code,media_type=media,headers=output_headers)

    async def fixed_json(self, request, url, params, classification):
        """Bounded fixed-origin GET. Never forward caller credentials or URLs."""
        principal = None
        auth = request.headers.get("authorization")
        if auth is not None:
            if not auth.startswith("Bearer ") or not auth[7:] or len(auth[7:]) > 256:
                raise NetworkError("unauthorized", "Bearer credential required", 401)
            principal = await asyncio.to_thread(self.store.authenticate, auth[7:])
        identity = principal.credential_id if principal else "anonymous"
        now = self.clock()
        minute = int(now // 60)
        if minute != self.window:
            self.buckets.clear()
            self.window = minute
        if len(self.buckets) >= 4096 and identity not in self.buckets:
            raise NetworkError("capacity", "Ingress capacity reached", 429)
        if self._closing or self.active + len(self._cleanup_tasks) >= 64 or self.buckets[identity] >= (600 if principal else 1200):
            raise NetworkError("rate_limited", "Ingress operating limit reached; retry later", 429)
        self.buckets[identity] += 1
        traffic = request.headers.get("x-financial-evidence-traffic-class", "unknown")
        from .analytics import TRAFFIC_CLASSES
        if traffic not in TRAFFIC_CLASSES:
            traffic = "unknown"
        path = request.url.path
        prefix = request.scope.get("root_path", "")
        if prefix and not path.startswith(prefix + "/"):
            path = prefix + path
        country = verified_country(request.headers, "GET", path, self.geo_secret, now=now)
        headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                   "X-Financial-Evidence-Traffic-Class": traffic}
        output = {"Cache-Control": "no-store", "X-Dataset-ID": classification.dataset_id,
                  "X-Dataset-Catalog-Version": data_catalog.VERSION, "X-Source-URL": url,
                  "X-Product": classification.product}
        self.active += 1
        upstream, status, response_state = None, 502, "unknown"
        try:
            outbound = self.client.build_request("GET", url, headers=headers, params=params or None)
            outbound.headers.pop("cookie", None)
            async with asyncio.timeout(120):
                upstream = await self.client.send(outbound, stream=True)
                if 300 <= upstream.status_code < 400:
                    raise NetworkError("upstream_redirect", "Dataset redirect requires operator review", 502)
                if upstream.headers.get("content-encoding", "identity").strip().lower() != "identity":
                    raise NetworkError("upstream_encoding", "Compressed dataset responses are unsupported", 502)
                media = upstream.headers.get("content-type", "application/json").split(";", 1)[0].strip().lower()
                if media != "application/json" and not media.endswith("+json"):
                    raise NetworkError("upstream_media", "Dataset response must be JSON", 502)
                captured = bytearray()
                async def chunks():
                    if upstream.is_stream_consumed:
                        yield upstream.content
                    else:
                        async for chunk in upstream.stream:
                            yield chunk
                async for chunk in chunks():
                    if len(captured) + len(chunk) > 4194304:
                        raise NetworkError("upstream_size", "Dataset response exceeds the bounded ingress limit", 502)
                    captured.extend(chunk)
                raw = bytes(captured)
                response_state = "empty" if not raw else "unknown"
                if raw:
                    try:
                        parsed = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    except (ValueError, UnicodeError, RecursionError):
                        raise NetworkError("upstream_json", "Dataset response is not valid JSON", 502) from None
                    response_state = "empty" if parsed is None or parsed == [] or parsed == {} or parsed == "" else "nonempty"
                elif upstream.status_code not in {204, 205, 304}:
                    raise NetworkError("upstream_json", "Dataset response is not valid JSON", 502)
                status = upstream.status_code
                return Response(raw, status_code=status, media_type=media, headers=output)
        except asyncio.CancelledError:
            status = 499
            raise
        except (httpx.HTTPError, OSError, TimeoutError):
            raise NetworkError("upstream_unavailable", "Dataset temporarily unavailable", 502) from None
        finally:
            self.active -= 1
            args = (principal, classification.product, "GET", traffic, status, round((self.clock() - now) * 1000))
            metadata = {"tool": "none", "country": country, "classification": classification, "response_state": response_state}
            if upstream is not None:
                await self._cleanup(upstream, args, metadata)
            else:
                await self._record(*args, **metadata)


def mount_gateway(app, store, *, transport=None):
    gateway = Gateway(store, transport=transport)
    app.state.gateway = gateway

    @app.get("/v1/data-catalog")
    def data_catalog_route():
        return data_catalog.catalog()

    @app.get("/v1/data/{dataset_id}")
    async def fixed_data(dataset_id: str, request: Request):
        url, params, classification = data_catalog.fixed_request(dataset_id, request.query_params)
        return await gateway.fixed_json(request, url, params, classification)

    @app.get("/v1/research/api/v1/{operation}")
    async def fixed_research(operation: str, request: Request):
        url, params, classification = data_catalog.research_request(operation, request.query_params)
        return await gateway.fixed_json(request, url, params, classification)

    @app.get("/v1/dataset-usage")
    async def dataset_usage(request: Request, traffic: str = "all", limit: int = Query(default=100, ge=1, le=200)):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise NetworkError("unauthorized", "Bearer credential required", 401)
        principal = await asyncio.to_thread(store.authenticate, auth[7:])
        return await asyncio.to_thread(demand.report, store, principal, traffic=traffic, limit=limit)

    @app.get("/v1/operator/dataset-usage")
    async def operator_dataset_usage(request: Request, traffic: str = "all", limit: int = Query(default=100, ge=1, le=200)):
        configured = os.environ.get("SEICHE_OPERATOR_REPORT_TOKEN", "")
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(configured) < 32 or not secrets.compare_digest(configured, auth[7:]):
            raise NetworkError("unauthorized", "Operator reporting credential required", 401)
        result = await asyncio.to_thread(demand.report, store, None, traffic=traffic, limit=limit)
        result["measurement_diagnostics"] = gateway.diagnostics()
        return result

    def source_status():
        location=os.environ.get("SEICHE_FRESHNESS_REPORT")
        if not location:
            return {"status":"not_configured","products":[],"monitor_current":False}
        path=Path(location)
        try:
            if not path.is_file() or path.stat().st_size>1_048_576:
                return {"status":"missing_report","products":[],"monitor_current":False}
            with path.open("rb") as handle:
                raw = handle.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("Source report exceeds byte limit")
            return _public_source_report(json.loads(raw))
        except (ValueError,KeyError,TypeError,OSError,RecursionError,OverflowError):
            return {"status":"invalid_report","products":[],"monitor_current":False}

    @app.get("/v1/data-status")
    async def data_status():
        # This file contains source clocks and availability only, never usage,
        # identities, credentials, message bodies or raw product payloads.
        return await asyncio.to_thread(source_status)

    @app.get("/v1/products")
    def products():
        return {"products":[{"id":key,"name":value[0],"mcp_path":f"/products/{key}/mcp",
                              "direct_origin":("https://agents.seiche.info/commerce/mcp/" if key == "wake" else value[1]),"free_core":True} for key,value in PRODUCTS.items()],
                "measurement":"30-day operational metadata; no arguments, results, IPs or prompts retained",
                "training":"Separate explicit contribution enrollment required"}

    @app.api_route("/products/{product}/mcp", methods=["GET","POST","DELETE"])
    async def product_mcp(product:str,request:Request):
        return await gateway.proxy(product,request)

    @app.get("/v1/product-usage")
    async def usage(request:Request):
        auth=request.headers.get("authorization","")
        if not auth.startswith("Bearer "):
            raise NetworkError("unauthorized","Bearer credential required",401)
        principal=await asyncio.to_thread(store.authenticate,auth[7:])
        return await asyncio.to_thread(gateway.report,principal)

    @app.get("/v1/operator/usage")
    async def operator_usage(request:Request):
        configured=os.environ.get("SEICHE_OPERATOR_REPORT_TOKEN","")
        supplied=request.headers.get("authorization","").removeprefix("Bearer ")
        if len(configured)<32 or not secrets.compare_digest(configured,supplied):
            raise NetworkError("unauthorized","Operator reporting credential required",401)
        return await asyncio.to_thread(gateway.report,None)

    @app.get("/v1/operator/freshness")
    async def operator_freshness(request:Request):
        configured=os.environ.get("SEICHE_OPERATOR_REPORT_TOKEN","")
        supplied=request.headers.get("authorization","").removeprefix("Bearer ")
        if len(configured)<32 or not secrets.compare_digest(configured,supplied):
            raise NetworkError("unauthorized","Operator reporting credential required",401)
        return await asyncio.to_thread(source_status)
