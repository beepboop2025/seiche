"""Permissioned REST API and a deliberately bounded A2A 1.0 HTTP+JSON adapter.

All mutations delegate to Store. No callback URL is fetched and no message is
executed as an instruction by this relay. A2A requires an already accepted room,
explicit asynchronous mode, and supports text/structured parts only.
"""
import asyncio
import logging
import os
import re
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .models import NetworkError, Principal
from . import hosted_surfaces

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Capability = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AgentCreate(StrictModel):
    label: Label
    capabilities: list[Capability] = Field(default_factory=list, max_length=20)
    public: bool = False


class AgentUpdate(StrictModel):
    label: Label | None = None
    capabilities: list[Capability] | None = Field(default=None, max_length=20)
    public: bool | None = None


class AgentStatus(StrictModel):
    status: Literal["active", "paused", "revoked"]


class TargetAgent(StrictModel):
    target_agent_id: Identifier


class RoomCreate(StrictModel):
    label: Label
    participant_ids: list[Identifier] = Field(min_length=1, max_length=15)


class MessageCreate(StrictModel):
    kind: Literal["question", "reply", "evidence", "status", "cancel", "escalation"]
    payload: dict[str, Any]
    idempotency_key: Identifier
    expires_in: int = Field(default=86400, ge=1, le=2592000)
    parent_id: Identifier | None = None


class TaskStatus(StrictModel):
    status: Literal["accepted", "running", "completed", "failed", "cancelled"]


class Completion(StrictModel):
    useful: bool


class Report(StrictModel):
    target_agent_id: Identifier
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class A2APart(StrictModel):
    text: Annotated[str, StringConstraints(max_length=16384)] | None = None
    data: Any = None
    mediaType: Literal["text/plain", "application/json"] | None = None

    @model_validator(mode="after")
    def one_content(self):
        if ("text" in self.model_fields_set) == ("data" in self.model_fields_set):
            raise ValueError("Provide exactly one text or data field")
        if "text" in self.model_fields_set and self.text is None:
            raise ValueError("Text must be a string")
        return self


class A2AMessage(StrictModel):
    messageId: Identifier
    role: Literal["ROLE_USER"]
    parts: list[A2APart] = Field(min_length=1, max_length=16)
    contextId: Identifier | None = None
    taskId: Identifier | None = None


class A2AConfiguration(StrictModel):
    returnImmediately: bool = False
    acceptedOutputModes: list[Literal["text/plain", "application/json"]] = Field(default_factory=list, max_length=2)
    historyLength: int = Field(default=0, ge=0, le=100)


class A2ASend(StrictModel):
    message: A2AMessage
    configuration: A2AConfiguration = Field(default_factory=A2AConfiguration)


# Reviewed research frontends receive public data access only, never workspace
# or operator-report CORS.
DATA_BROWSER_ORIGINS = hosted_surfaces.PUBLIC_ORIGINS


def _public_data_request(scope):
    if hosted_surfaces.public_surface(scope):
        return True
    if scope.get("type") != "http" or scope.get("method") not in {"GET", "OPTIONS"}:
        return False
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and path.startswith(root + "/"):
        path = path[len(root):]
    return path == "/v1/data-catalog" or path.startswith("/v1/data/")


class ScopedCORS:
    def __init__(self, app, *, allowed_origins):
        self.workspace = CORSMiddleware(app, allow_origins=sorted(allowed_origins),
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "A2A-Version"], allow_credentials=False)
        data_options = dict(allow_origins=sorted(set(allowed_origins) | DATA_BROWSER_ORIGINS), allow_credentials=False,
            allow_headers=["Authorization", "Content-Type", "X-Financial-Evidence-Traffic-Class", "X-Liquilens-Traffic-Class",
                           "MCP-Protocol-Version", "Mcp-Session-Id", "Mcp-Method", "Mcp-Name", "Last-Event-ID"],
            expose_headers=["X-Dataset-ID", "X-Dataset-Catalog-Version", "X-Source-URL", "X-Product", "Mcp-Session-Id",
                            *sorted(hosted_surfaces.CSV_HEADERS)])
        self.data = CORSMiddleware(app, allow_methods=["GET"], **data_options)
        self.noise = CORSMiddleware(app, allow_methods=["POST"], **data_options)
        self.product_mcp = CORSMiddleware(app, allow_methods=["GET", "POST", "DELETE"], **data_options)

    async def __call__(self, scope, receive, send):
        middleware = self.data if _public_data_request(scope) else self.workspace
        if _public_data_request(scope):
            path = hosted_surfaces.relative_path(scope)
            if path in {"/v1/noisefloor/" + item for item in hosted_surfaces.NOISEFLOOR_POST}:
                middleware = self.noise
            elif path in hosted_surfaces.PUBLIC_MCP_PATHS:
                middleware = self.product_mcp
        await middleware(scope, receive, send)


class RequestBoundary:
    """Cap streamed request bytes before JSON parsing; reject cross-origin access."""

    def __init__(self, app, *, allowed_origins: set[str], max_bytes: int = 65536):
        self.app, self.allowed_origins, self.max_bytes = app, allowed_origins, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        max_bytes = hosted_surfaces.body_limit(scope, self.max_bytes)
        origin = headers.get(b"origin", b"").decode("latin1")
        response = None
        public_origin = _public_data_request(scope) and origin in DATA_BROWSER_ORIGINS
        if origin and origin not in self.allowed_origins and not public_origin:
            response = JSONResponse({"error": {"code": "origin_denied", "message": "Origin is not permitted"}}, status_code=403)
        elif headers.get(b"content-encoding", b"identity").lower() != b"identity":
            response = JSONResponse({"error": {"code": "encoding_unsupported", "message": "Compressed request bodies are not supported"}}, status_code=415)
        body = bytearray()
        if response is None:
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                body.extend(event.get("body", b""))
                if len(body) > max_bytes:
                    response = JSONResponse({"error": {"code": "body_too_large", "message": f"Request body exceeds {max_bytes} bytes"}}, status_code=413)
                    break
                if not event.get("more_body", False):
                    break

        async def protected_send(event):
            if event["type"] == "http.response.start":
                event["headers"] = list(event.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
                ]
            await send(event)

        if response is not None:
            await response(scope, receive, protected_send)
            return
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, protected_send)


def _a2a_task(value: dict, history_length: int = 0) -> dict:
    states = {"submitted": "SUBMITTED", "accepted": "WORKING", "running": "WORKING", "completed": "COMPLETED", "failed": "FAILED", "cancelled": "CANCELED"}
    status = value.get("status", value.get("task_status", "submitted"))
    task = {"id": value.get("message_id", value.get("id")), "contextId": value["room_id"],
            "status": {"state": "TASK_STATE_" + states[status]}}
    timestamp = value.get("updated_at", value.get("created_at"))
    if timestamp is not None:
        task["status"]["timestamp"] = datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat().replace("+00:00", "Z")
    if history_length and value.get("payload", {}).get("a2a_message"):
        task["history"] = [value["payload"]["a2a_message"]]
    return task


def create_app(store, *, public_url="http://127.0.0.1:8765", allowed_origins=None,
               allowed_hosts=None, purge_interval=3600, gateway=False) -> FastAPI:
    """Create an isolated network service; never mutate Seiche's publication app."""
    base = urlsplit(public_url)
    if base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password or base.query or base.fragment:
        raise ValueError("public_url must be an absolute HTTP(S) service URL")
    if base.scheme != "https" and base.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        raise ValueError("Non-loopback public_url requires HTTPS")
    origins = set(allowed_origins or [f"{base.scheme}://{base.netloc}"])
    if "*" in origins:
        raise ValueError("Wildcard origins are not supported")
    hosts = allowed_hosts or ([base.hostname, "127.0.0.1", "localhost", "testserver"] if base.hostname in {"127.0.0.1", "localhost", "::1", "testserver"} else [base.hostname])
    if "*" in hosts:
        raise ValueError("Wildcard hosts are not supported")

    @asynccontextmanager
    async def lifespan(app):
        async def purge():
            await asyncio.to_thread(store.purge_expired)
            app.state.retention_status = "ok"

        async def retention():
            while True:
                await asyncio.sleep(purge_interval)
                try:
                    await purge()
                except Exception:
                    app.state.retention_status = "failed"
                    logging.getLogger(__name__).error("Network retention run failed; next interval will retry")
        await purge()
        worker = asyncio.create_task(retention()) if purge_interval > 0 else None
        try:
            async with AsyncExitStack() as stack:
                if hasattr(app.state, "social_public_mcp"):
                    await stack.enter_async_context(app.state.social_public_mcp.session_manager.run())
                yield
        finally:
            if worker:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            if hasattr(app.state, "gateway"):
                await app.state.gateway.aclose()

    app = FastAPI(title="Seiche Agent Network", version="0.1.0", lifespan=lifespan, root_path=base.path.rstrip("/"), docs_url=None, redoc_url=None,
                  description="Permissioned financial-agent communication. No trade execution. Private contents are excluded from shared model training.")
    app.state.store = store
    app.state.public_url = public_url.rstrip("/")
    app.state.retention_status = "not_started"
    from .enrollment import mount_enrollment
    mount_enrollment(app, store)
    from .discovery import mount_discovery
    mount_discovery(app, public_url)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(ScopedCORS, allowed_origins=origins)
    app.add_middleware(RequestBoundary, allowed_origins=origins)
    bearer = HTTPBearer(auto_error=False)

    def principal(auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Principal:
        if not auth or auth.scheme.lower() != "bearer" or len(auth.credentials) > 256:
            raise NetworkError("unauthenticated", "A valid bearer credential is required", 401)
        return store.authenticate(auth.credentials)

    Identity = Annotated[Principal, Depends(principal)]
    from .participation import mount_participation
    mount_participation(app, store, principal)
    from .social import mount_social
    mount_social(app, store, principal)
    from .social_public_mcp import mount_public_social_mcp
    mount_public_social_mcp(app, store, hosts=hosts, origins=origins)

    @app.exception_handler(NetworkError)
    async def domain_error(request, exc):
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}
        if exc.status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        if request.url.path.startswith("/a2a/"):
            content = {"error": {"code": exc.status, "message": exc.message,
                                 "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": exc.code, "domain": "seiche.agent-network"}]}}
        else:
            content = {"error": {"code": exc.code, "message": exc.message}}
        return JSONResponse(content, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic's default error includes submitted input, which may be private.
        return await domain_error(request, NetworkError("invalid_request", "Request fields are invalid or unsupported", 422))

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return await domain_error(request, NetworkError("internal_error", "Request could not be completed", 500))

    @app.get("/healthz")
    def health():
        failed = app.state.retention_status == "failed"
        revision = os.environ.get("SEICHE_NETWORK_REVISION", "")
        artifact = os.environ.get("SEICHE_NETWORK_ARTIFACT_SHA256", "")
        return JSONResponse({"status": "degraded" if failed else "ok", "service": "seiche-agent-network", "version": "0.1.0",
                             "source_revision": revision if re.fullmatch(r"[a-f0-9]{40}",revision) else None,
                             "artifact_sha256": artifact if re.fullmatch(r"[a-f0-9]{64}",artifact) else None,
                             "retention": app.state.retention_status}, status_code=503 if failed else 200)

    @app.get("/v1/me")
    def me(p: Identity):
        return store.me(p)

    @app.delete("/v1/me")
    def delete_organization(p: Identity):
        return store.delete_organization(p)

    @app.get("/v1/agents")
    def owned_agents(p: Identity):
        return store.list_owned_agents(p)

    @app.post("/v1/agents", status_code=201)
    def register(body: AgentCreate, p: Identity):
        return store.register_agent(p, **body.model_dump())

    @app.patch("/v1/agents/{agent_id}")
    def update_agent(agent_id: Identifier, body: AgentUpdate, p: Identity):
        return store.update_agent(p, agent_id, **body.model_dump(exclude_unset=True))

    @app.post("/v1/agents/{agent_id}/rotate")
    def rotate(agent_id: Identifier, p: Identity):
        return store.rotate_agent_token(p, agent_id)

    @app.post("/v1/agents/{agent_id}/status")
    def agent_status(agent_id: Identifier, body: AgentStatus, p: Identity):
        return store.set_agent_status(p, agent_id, body.status)

    @app.get("/v1/directory")
    def directory(p: Identity, q: str | None = Query(default=None, max_length=160)):
        return store.directory(p, query=q)

    @app.get("/v1/contacts")
    def contacts(p: Identity):
        return store.list_contacts(p)

    @app.post("/v1/contacts/invite", status_code=201)
    def invite(body: TargetAgent, p: Identity):
        return store.invite_contact(p, body.target_agent_id)

    @app.post("/v1/contacts/{contact_id}/accept")
    def accept_contact(contact_id: Identifier, p: Identity):
        return store.accept_contact(p, contact_id)

    @app.post("/v1/contacts/{contact_id}/revoke")
    def revoke_contact(contact_id: Identifier, p: Identity):
        return store.revoke_contact(p, contact_id)

    @app.post("/v1/blocks")
    def block(body: TargetAgent, p: Identity):
        return store.block_agent(p, body.target_agent_id)

    @app.get("/v1/rooms")
    def rooms(p: Identity):
        return store.list_rooms(p)

    @app.post("/v1/rooms", status_code=201)
    def create_room(body: RoomCreate, p: Identity):
        return store.create_room(p, **body.model_dump())

    @app.post("/v1/rooms/{room_id}/accept")
    def accept_room(room_id: Identifier, p: Identity):
        return store.accept_room(p, room_id)

    @app.delete("/v1/rooms/{room_id}/members/{agent_id}")
    def remove_member(room_id: Identifier, agent_id: Identifier, p: Identity):
        return store.remove_member(p, room_id, agent_id)

    @app.delete("/v1/rooms/{room_id}")
    def delete_room(room_id: Identifier, p: Identity):
        return store.delete_room(p, room_id)

    @app.post("/v1/rooms/{room_id}/messages", status_code=201)
    def send_message(room_id: Identifier, body: MessageCreate, p: Identity):
        return store.send_message(p, room_id, **body.model_dump())

    @app.get("/v1/rooms/{room_id}/messages")
    def messages(room_id: Identifier, p: Identity, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        return store.list_messages(p, room_id, after=after, limit=limit)

    @app.get("/v1/rooms/{room_id}/verify")
    def verify(room_id: Identifier, p: Identity):
        return store.verify_room(p, room_id)

    @app.get("/v1/inbox")
    def inbox(p: Identity, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        return store.poll_deliveries(p, after=after, limit=limit)

    @app.post("/v1/messages/{message_id}/ack")
    def acknowledge(message_id: Identifier, p: Identity):
        return store.acknowledge(p, message_id)

    @app.get("/v1/tasks/{message_id}")
    def task(message_id: Identifier, p: Identity):
        return store.get_task(p, message_id)

    @app.post("/v1/tasks/{message_id}/status")
    def task_status(message_id: Identifier, body: TaskStatus, p: Identity):
        return store.set_task_status(p, message_id, body.status)

    @app.post("/v1/tasks/{message_id}/cancel")
    def cancel_task(message_id: Identifier, p: Identity):
        return store.cancel_task(p, message_id)

    @app.post("/v1/tasks/{message_id}/complete")
    def complete_task(message_id: Identifier, body: Completion, p: Identity):
        return store.complete_task(p, message_id, body.useful)

    @app.post("/v1/reports", status_code=201)
    def report(body: Report, p: Identity):
        return store.report(p, **body.model_dump())

    @app.get("/v1/metrics")
    def metrics(p: Identity):
        return store.metrics(p)

    def a2a_version(request: Request):
        if request.headers.get("a2a-version", request.query_params.get("A2A-Version", "0.3")).split(".")[:2] != ["1", "0"]:
            raise NetworkError("VersionNotSupportedError", "This adapter supports A2A 1.0; send A2A-Version: 1.0", 400)

    def visible_agent(p, agent_id):
        return store.get_agent(p, agent_id)

    def room_target(p, room_id, agent_id):
        visible_agent(p, agent_id)
        room = next((r for r in store.list_rooms(p) if r["id"] == room_id), None)
        if room is None or not any(m["agent_id"] == agent_id and m["status"] == "active" for m in room["members"]):
            raise NetworkError("TaskNotFoundError", "Accepted peer room is required", 404)
        return room

    def targeted_task(p, task_id, agent_id):
        value = store.get_task(p, task_id)
        room_target(p, value["room_id"], agent_id)
        if value.get("payload", {}).get("target_agent_id") != agent_id:
            raise NetworkError("TaskNotFoundError", "Task not accessible through this peer interface", 404)
        return value

    def agent_card(p, agent_id):
        agent = visible_agent(p, agent_id)
        return {"name": agent["label"], "description": "Permissioned Seiche peer inbox. Requires an accepted room contextId and returnImmediately=true. Text/data parts only. No trading execution, streaming or push callbacks.",
                "version": "0.1.0", "supportedInterfaces": [{"url": public_url.rstrip("/") + "/a2a/" + agent_id, "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}],
                "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": True},
                "defaultInputModes": ["text/plain", "application/json"], "defaultOutputModes": ["text/plain", "application/json"],
                "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}},
                "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
                "skills": [{"id": "permissioned-peer-exchange", "name": "Permissioned peer exchange", "description": "Submit evidence questions to an accepted peer, then poll task state. Capability labels are owner-declared.", "tags": agent["capabilities"]}]}

    @app.get("/a2a/{agent_id}/.well-known/agent-card.json")
    def card(agent_id: Identifier, p: Identity):
        return agent_card(p, agent_id)

    @app.get("/a2a/{agent_id}/extendedAgentCard", dependencies=[Depends(a2a_version)])
    def extended_card(agent_id: Identifier, p: Identity):
        return agent_card(p, agent_id)

    @app.post("/a2a/{agent_id}/message:send", dependencies=[Depends(a2a_version)])
    def a2a_send(agent_id: Identifier, body: A2ASend, p: Identity):
        if not body.configuration.returnImmediately:
            raise NetworkError("UnsupportedOperationError", "This asynchronous adapter requires configuration.returnImmediately=true", 400)
        message = body.message
        room_id = message.contextId
        if message.taskId:
            prior = targeted_task(p, message.taskId, agent_id)
            if room_id is not None and prior["room_id"] != room_id:
                raise NetworkError("invalid_request", "Task and context do not match", 400)
            if prior["status"] in {"completed", "failed", "cancelled"}:
                raise NetworkError("UnsupportedOperationError", "A terminal task cannot accept follow-up messages", 409)
            room_id = prior["room_id"]
        if not room_id:
            raise NetworkError("invalid_request", "An accepted peer room contextId is required", 400)
        room = room_target(p, room_id, agent_id)
        active = {m["agent_id"] for m in room["members"] if m["status"] == "active"}
        if p.agent_id == agent_id or active != {p.agent_id, agent_id}:
            raise NetworkError("invalid_request", "A2A peer sends require a two-agent accepted room", 400)
        saved = store.send_message(p, room_id, "reply" if message.taskId else "question", {"a2a_message": message.model_dump(exclude_unset=True), "target_agent_id": agent_id},
                                   "a2a:" + message.messageId, parent_id=message.taskId)
        result = store.get_task(p, message.taskId or saved["id"])
        return JSONResponse({"task": _a2a_task(result, body.configuration.historyLength)}, media_type="application/a2a+json", headers={"A2A-Version": "1.0"})

    @app.get("/a2a/{agent_id}/tasks/{task_id}", dependencies=[Depends(a2a_version)])
    def a2a_get(agent_id: Identifier, task_id: Identifier, p: Identity, historyLength: int = Query(default=0, ge=0, le=100)):
        result = targeted_task(p, task_id, agent_id)
        return JSONResponse(_a2a_task(result, historyLength), media_type="application/a2a+json", headers={"A2A-Version": "1.0"})

    @app.post("/a2a/{agent_id}/tasks/{task_id}:cancel", dependencies=[Depends(a2a_version)])
    def a2a_cancel(agent_id: Identifier, task_id: Identifier, p: Identity):
        result = targeted_task(p, task_id, agent_id)
        store.cancel_task(p, task_id)
        return JSONResponse(_a2a_task(store.get_task(p, task_id)), media_type="application/a2a+json", headers={"A2A-Version": "1.0"})

    from .ui import mount_console
    mount_console(app)
    if gateway:
        from .gateway import mount_gateway
        mount_gateway(app, store)
    return app
