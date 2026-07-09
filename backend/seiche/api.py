"""Seiche REST API. Run: uvicorn seiche.api:app --port 8787"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from seiche import accounts, assemble, public_view, store
from seiche.config import (
    ALERT_RULES,
    ALL_SERIES,
    COMPOSITE_WEIGHTS,
    DB_PATH,
    EPISODES,
    REGIMES,
)

# In production (SEICHE_ENV=production, set in the systemd unit) the interactive
# API docs and the machine-readable schema are turned off — they enumerate every
# gated route and its shape, which we don't hand to anonymous callers. Dev keeps
# them on.
_PROD = os.getenv("SEICHE_ENV", "").lower() == "production"

app = FastAPI(title="Seiche", version=assemble.VERSION,
              description="Funding-stress & leveraged-positioning early-warning terminal",
              docs_url=None if _PROD else "/docs",
              redoc_url=None if _PROD else "/redoc",
              openapi_url=None if _PROD else "/openapi.json")

# CORS is applied once at the edge (Caddy on api.seiche.info); a second copy
# here produced duplicate Access-Control-Allow-Origin headers that browsers
# reject. Local dev uses the vite same-origin proxy, so no CORS is needed.

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _board_gate_enabled() -> bool:
    return os.getenv("SEICHE_BOARD_AUTH", "0") == "1"


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


@app.get("/api/overview")
async def overview(force: bool = False, ident: dict | None = Depends(require_board)):
    """The full board — subscriber-gated when SEICHE_BOARD_AUTH=1 (the public
    box). Free visitors get /api/public instead. `force` (cache-bypass
    recompute) is honoured only for authenticated callers."""
    return await assemble.snapshot(force=force and ident is not None)


@app.get("/api/public")
async def public(force: bool = False,
                 authorization: str | None = Header(default=None)):
    """Free surface: the conclusion + PROOF scoreboard only. Never the board.
    `force` is ignored for unauthenticated callers — no anonymous recompute."""
    ident = _bearer_identity(authorization)
    snap = await assemble.snapshot(force=force and ident is not None)
    return public_view.public_payload(snap)


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
async def dispatch_paid(slug: str, authorization: str | None = Header(default=None)):
    """The paid continuation of a dispatch. The free section is a public static
    asset; this returns the subscriber-only part and ONLY to a valid token —
    the paid markdown never ships to the public site."""
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,80}$", slug):
        raise HTTPException(422, "bad slug")
    if _bearer_identity(authorization) is None:
        raise HTTPException(401, "the desk's read is a subscriber feature — sign in")
    path = DISPATCH_DIR / f"{slug}.paid.md"
    if not path.exists():
        raise HTTPException(404, "no paid section for this dispatch")
    return {"slug": slug, "paid": path.read_text()}


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
async def asof(date: str, ident: dict | None = Depends(require_board)):
    """Time Machine: the whole light board replayed as of a historical date.
    Subscriber-gated when SEICHE_ASOF_AUTH=1 (the public box); open in dev."""
    if accounts.asof_gate_enabled() and ident is None:
        raise HTTPException(401, "Time Machine replay is a subscriber feature — sign in")
    if not _DATE_RE.match(date):
        raise HTTPException(422, "date must be YYYY-MM-DD")
    payload = await assemble.snapshot_asof(date)
    if payload.get("ok") is False:
        raise HTTPException(404, payload.get("reason", "replay unavailable"))
    return payload


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


@app.get("/api/series/{mnemonic}")
async def series(mnemonic: str, n: int = 750,
                 _ident: dict | None = Depends(require_board)):
    if mnemonic not in ALL_SERIES:
        raise HTTPException(404, f"unknown series '{mnemonic}'")
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
async def health():
    snap = await assemble.snapshot()
    return {
        "generated_at": snap["generated_at"],
        "version": snap.get("version"),
        "faults": snap["faults"],
        "provenance": snap["provenance"],
    }


# Serve the built frontend when present (single-process deploy).
_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
