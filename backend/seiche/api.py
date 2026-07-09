"""Seiche REST API. Run: uvicorn seiche.api:app --port 8787"""

from __future__ import annotations

import re
import sqlite3

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

app = FastAPI(title="Seiche", version=assemble.VERSION,
              description="Funding-stress & leveraged-positioning early-warning terminal")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _board_gate_enabled() -> bool:
    import os
    return os.getenv("SEICHE_BOARD_AUTH", "0") == "1"


@app.get("/api/overview")
async def overview(force: bool = False,
                   authorization: str | None = Header(default=None)):
    """The full board — subscriber-gated when SEICHE_BOARD_AUTH=1 (the public
    box). Free visitors get /api/public instead."""
    if _board_gate_enabled() and _bearer_identity(authorization) is None:
        raise HTTPException(401, "the board is a subscriber feature — sign in")
    return await assemble.snapshot(force=force)


@app.get("/api/public")
async def public(force: bool = False):
    """Free surface: the conclusion + PROOF scoreboard only. Never the board."""
    snap = await assemble.snapshot(force=force)
    return public_view.public_payload(snap)


@app.get("/api/engines/{name}")
async def engine(name: str):
    snap = await assemble.snapshot()
    if name not in snap["engines"]:
        raise HTTPException(404, f"unknown engine '{name}'")
    return snap["engines"][name]


from pydantic import BaseModel


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def login(body: LoginBody):
    """Subscriber login — returns a 30-day bearer token. Accounts are
    provisioned by the operator (`seiche user add`); no self-signup yet."""
    user = accounts.verify_user(body.username, body.password)
    if user is None:
        raise HTTPException(401, "invalid username or password")
    return accounts.issue_token(user["username"], user["tier"])


def _bearer_identity(authorization: str | None) -> dict | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return accounts.verify_token(authorization.removeprefix("Bearer "))


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
async def asof(date: str, authorization: str | None = Header(default=None)):
    """Time Machine: the whole light board replayed as of a historical date.
    Subscriber-gated when SEICHE_ASOF_AUTH=1 (the public box); open in dev."""
    if accounts.asof_gate_enabled() and _bearer_identity(authorization) is None:
        raise HTTPException(401, "Time Machine replay is a subscriber feature — sign in")
    if not _DATE_RE.match(date):
        raise HTTPException(422, "date must be YYYY-MM-DD")
    payload = await assemble.snapshot_asof(date)
    if payload.get("ok") is False:
        raise HTTPException(404, payload.get("reason", "replay unavailable"))
    return payload


@app.get("/api/deep")
async def deep():
    """History reconstruction, Tell, Turn, Playbook, PROOF backtest."""
    snap = await assemble.snapshot()
    return snap.get("deep", {})


@app.get("/api/book")
async def book():
    """The Book: today's positions, walk-forward P&L, live track record."""
    snap = await assemble.snapshot()
    return snap.get("deep", {}).get("book", {"ok": False, "reason": "unavailable"})


@app.get("/api/series/{mnemonic}")
async def series(mnemonic: str, n: int = 750):
    if mnemonic not in ALL_SERIES:
        raise HTTPException(404, f"unknown series '{mnemonic}'")
    await assemble.snapshot()  # ensure fetched
    s = store.load_series(mnemonic)
    if s is None:
        raise HTTPException(503, f"series '{mnemonic}' not yet available")
    return {"provenance": s.provenance(), "points": s.tail_records(n)}


@app.get("/api/config")
async def config_view():
    """The editorial voice, read-only: what the operator can tune and where."""
    return {
        "composite_weights": COMPOSITE_WEIGHTS,
        "regimes": [{"below": c, "name": n} for c, n in REGIMES],
        "episodes": EPISODES,
        "alert_rules": ALERT_RULES,
        "tuning_file": "backend/seiche/config.py",
    }


@app.get("/api/alerts")
async def alerts(n: int = 50):
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
async def brief_text():
    """This morning's desk note, rendered as markdown."""
    from seiche import brief as brief_mod

    snap = await assemble.snapshot()
    return brief_mod.render_markdown(snap)


@app.get("/api/ask")
async def ask(q: str):
    """Desk assistant: answers grounded strictly in the live board."""
    from seiche import ai

    if not q or len(q) > 600:
        raise HTTPException(422, "q must be 1-600 characters")
    snap = await assemble.snapshot()
    return await ai.ask(q, snap)


@app.get("/api/pit")
async def pit(n: int = 400):
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
