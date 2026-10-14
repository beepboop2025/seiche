"""Public collection summaries and separately authorized private evidence."""

import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .analysis import dossier
from .contracts import CONTRACT, Contribution, IdentityClaim, PilotRequest
from .store import Store, Rejected, canonical


def bearer(request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or len(auth) > 300:
        raise Rejected("bearer_credential_required", 401)
    return auth[7:]


def create_app(store: Store, operator_token=None):
    @asynccontextmanager
    async def lifetime(app):
        store.expire()
        async def maintenance():
            while True:
                await asyncio.sleep(3600)
                await asyncio.to_thread(store.expire)
        worker = asyncio.create_task(maintenance())
        yield
        worker.cancel()

    app = FastAPI(title="Agent Activity Evidence", version=__version__, docs_url=None, redoc_url=None, lifespan=lifetime,
                 servers=[{"url": "https://agents.seiche.info/activity"}])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["agents.seiche.info", "api.seiche.info", "localhost", "127.0.0.1", "testserver"])
    static = Path(__file__).with_name("static")

    @app.exception_handler(Rejected)
    async def rejected(request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.middleware("http")
    async def boundaries(request, call_next):
        # Bound actual streamed bytes as well as Content-Length, including chunked input.
        if request.method in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({"error": "body_too_large"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-transform"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        return response

    def operator(request):
        token = bearer(request)
        if not operator_token or not hmac.compare_digest(token, operator_token):
            raise Rejected("operator_credential_required", 403)

    @app.get("/health")
    def health():
        with store.db() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok", "version": __version__, "release": os.environ.get("ACTIVITY_RELEASE", "development"),
                "evidence_status": "see_individual_source_status", "read_only_market_access": True}

    @app.get("/")
    @app.get("", include_in_schema=False)
    def home():
        prefix = os.environ.get("ACTIVITY_PUBLIC_PREFIX", "/")
        import re
        if not re.fullmatch(r"/[a-z0-9/-]*", prefix):
            raise Rejected("invalid_public_prefix", 503)
        return HTMLResponse((static / "index.html").read_text().replace("<head>", '<head><base href="'+prefix+'">', 1))

    @app.get("/docs", include_in_schema=False)
    def docs():
        return HTMLResponse((static / "docs.html").read_text())

    @app.get("/app.js", include_in_schema=False)
    def script():
        return FileResponse(static / "app.js", media_type="text/javascript")

    @app.get("/style.css", include_in_schema=False)
    def style():
        return FileResponse(static / "style.css", media_type="text/css")

    @app.get("/v1/coverage")
    def coverage():
        return store.summary()

    @app.get("/v1/contract")
    def contract():
        return CONTRACT

    @app.get("/v1/entities")
    def entities(limit: int = 50, offset: int = 0, kind: str | None = None):
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000 or kind not in {None, "registered_agent", "paid_service"}:
            raise Rejected("invalid_page")
        return store.entities(limit, offset, kind)

    @app.get("/v1/protocol-events")
    def protocol_events(limit: int = 50):
        if not 1 <= limit <= 100:
            raise Rejected("invalid_page")
        # Raw exchange account records remain private to the operator unless a source-specific redistribution review exists.
        rows = store.public_events(limit, "virtuals_acp")
        return {"items": [{k: r[k] for k in ["id", "kind", "occurred_at", "block_number", "transaction_hash", "explorer_url", "receipt", "verification", "ai_authorship"]} for r in rows],
                "scope": "observed finalized protocol events; not verified autonomous-agent transactions"}

    @app.get("/v1/receipts/{identifier}")
    def receipt(identifier: str):
        return store.receipt(identifier)

    @app.get("/v1/operator/entities")
    def operator_entities(request: Request, limit: int = 50, offset: int = 0):
        operator(request)
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            raise Rejected("invalid_page")
        return store.entities(limit, offset, private=True)

    @app.get("/v1/operator/dossier")
    def operator_dossier(request: Request, entity_id: str):
        operator(request)
        if len(entity_id) > 250:
            raise Rejected("invalid_identity")
        return dossier(store, entity_id)

    @app.get("/v1/operator/receipts/{identifier}")
    def operator_receipt(request: Request, identifier: str):
        operator(request)
        return store.receipt(identifier, raw=True)

    @app.post("/v1/events")
    def event(request: Request, payload: Contribution):
        return store.contribute(bearer(request), payload)

    @app.post("/v1/identity-claims")
    def identity(request: Request, payload: IdentityClaim):
        return store.claim(bearer(request), payload)

    @app.get("/v1/me")
    def me(request: Request):
        return store.private_report(bearer(request))

    @app.delete("/v1/me")
    def withdraw(request: Request):
        return store.withdraw(bearer(request))

    @app.post("/v1/pilot-requests", status_code=202)
    def pilot(payload: PilotRequest):
        return store.apply(payload.model_dump())

    @app.post("/mcp")
    def mcp(message: dict):
        # Stateless read-only JSON-RPC transport; private ingestion is never a tool.
        identifier, method = message.get("id"), message.get("method")
        if message.get("jsonrpc") != "2.0":
            return JSONResponse({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32600, "message": "Invalid request"}}, status_code=400)
        if method == "notifications/initialized":
            return JSONResponse(None, status_code=202)
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "agent-activity-evidence", "version": __version__}}
        elif method == "tools/list":
            result = {"tools": [{"name": "agent_activity_coverage", "description": "Observed source coverage with unknown global agent population preserved", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
                {"name": "agent_activity_sources", "description": "Public collection source status and evidence boundaries", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
        elif method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Invalid params"}}
            name = params.get("name")
            if params.get("arguments") not in [None, {}] or name not in {"agent_activity_coverage", "agent_activity_sources"}:
                return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Unknown tool or arguments"}}
            data = store.summary() if name == "agent_activity_coverage" else {"sources": store.source_status()}
            result = {"content": [{"type": "text", "text": canonical(data).decode()}], "isError": False}
        else:
            return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found"}}
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    return app
