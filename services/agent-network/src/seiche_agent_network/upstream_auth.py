"""Private, per-request credentials for the fixed Undertow public MCP surface.

The native service verifies the signature and current entitlement. This reader
only checks the local credential envelope and expiry; it cannot grant access.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
import time

import httpx

UNDERTOW_URL = "https://api.seiche.info/undertow/mcp"
TOKEN_FILE_ENV = "SEICHE_UNDERTOW_MCP_TOKEN_FILE"
# Deliberately independent of discovery/analytics: adding a tool there must not
# silently extend a privileged credential's reach.
UNDERTOW_PUBLIC_TOOLS = frozenset({
    "crypto_exit_check", "crypto_funding_cost", "crypto_portfolio_stress",
    "depth_episodes", "exit_cost", "gold_cash_realisation", "latest_article",
    "liquidity_tiers", "market_corpus_context", "research_network", "sealed_record",
    "trade_safety_exit_context", "unwind_watch", "venue_concentration",
    "venue_price_reconciliation",
})


@dataclass(frozen=True)
class UpstreamCredential:
    status: str
    expires_at: str | None = None
    token: str | None = field(default=None, repr=False)

    def metadata(self):
        return {"status": self.status, "expires_at": self.expires_at}

    @property
    def cache_identity(self):
        # Used only in the private admission cache, never the public report.
        return hashlib.sha256((self.token or self.status).encode()).hexdigest()


def read_private_json(path, *, limit=16384):
    """No symlinks, devices, permissive files, unbounded reads, or shell loading."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                or info.st_size > limit or info.st_nlink != 1):
            raise ValueError("Private file contract not satisfied")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            body = handle.read(limit + 1)
        if len(body) > limit:
            raise ValueError("Private file exceeds limit")
        return json.loads(body)
    finally:
        os.close(fd)


def load_undertow_credential(*, now=None):
    path = os.environ.get(TOKEN_FILE_ENV)
    if path is None:
        return UpstreamCredential("not_configured")
    if not path:
        return UpstreamCredential("invalid")
    try:
        value = read_private_json(path)
    except OSError:
        return UpstreamCredential("unreadable")
    except (ValueError, TypeError, RecursionError):
        return UpstreamCredential("invalid")
    try:
        if (not isinstance(value, dict) or set(value) != {"token", "expires_at", "product", "purpose"}
                or value["product"] != "undertow" or value["purpose"] != "public_mcp_gateway"):
            raise ValueError()
        token = value["token"]
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,12000}\.[0-9a-f]{64}", token):
            raise ValueError()
        encoded = token.split(".", 1)[0]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if (not isinstance(claims, dict) or claims.get("aud") != "mcp"
                or not isinstance(claims.get("sub"), str) or not claims["sub"]
                or type(claims.get("exp")) is not int or type(claims.get("iat")) is not int
                or claims["iat"] >= claims["exp"]):
            raise ValueError()
        expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        if expires.tzinfo is None or expires.timestamp() != claims["exp"]:
            raise ValueError()
        clock = time.time() if now is None else now
        if claims["iat"] > clock + 60:
            raise ValueError()
        stamp = expires.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if clock >= claims["exp"]:
            return UpstreamCredential("expired", stamp)
        return UpstreamCredential("ready", stamp, token)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return UpstreamCredential("invalid")


class UpstreamAuthUnavailable(Exception):
    """Sanitized failure, deliberately without token, path, or native response."""


def public_call_credential(product, method, tool, *, now=None):
    """Return a secret only for an explicitly admitted public tools/call."""
    if product != "undertow" or method != "tools/call":
        return None
    if tool == "agent_access_status":
        return None  # Always anonymous, so operator identity/grants cannot leak.
    if not isinstance(tool, str) or tool not in UNDERTOW_PUBLIC_TOOLS:
        raise UpstreamAuthUnavailable("Only the public Undertow tool surface is available")
    credential = load_undertow_credential(now=now)
    if credential.status not in {"ready", "not_configured"}:
        raise UpstreamAuthUnavailable("Undertow upstream credential unavailable")
    return credential.token


class ProbeAuth:
    """Official MCP SDK HTTP hooks; discovery stays anonymous and stateless."""
    def __init__(self, *, clock=time.time):
        self.clock = clock

    async def request(self, request):
        request.headers.pop("authorization", None)
        request.headers.pop("cookie", None)
        if str(request.url) != UNDERTOW_URL:
            return
        # Native Undertow is stateless. Never elevate an existing MCP session.
        request.headers.pop("mcp-session-id", None)
        if request.method != "POST":
            return
        body = await request.aread()
        if len(body) > 65536:
            raise UpstreamAuthUnavailable("Probe request exceeds limit")
        try:
            payload = json.loads(body)
            params = payload.get("params", {})
            tool = params.get("name") if isinstance(params, dict) else None
            token = public_call_credential("undertow", payload.get("method"), tool, now=self.clock())
        except (ValueError, AttributeError, TypeError):
            raise UpstreamAuthUnavailable("Unsupported probe request") from None
        if token:
            request.headers["Authorization"] = "Bearer " + token

    async def response(self, response):
        if str(response.request.url) == UNDERTOW_URL and response.headers.get("mcp-session-id"):
            # Fail closed if a future native revision changes its stateless
            # contract. No authenticated or anonymous session can be retained.
            await response.aclose()
            raise UpstreamAuthUnavailable("Undertow session contract changed")
