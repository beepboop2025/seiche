"""MCP usage metering — the commercial meter for the hosted /mcp endpoint.

stdlib-only, SQLite-backed, matching the project ethos. One row per caller per
UTC day; a tool call increments it and checks the caller's tier quota. Keys are
``user:<username>`` for authenticated subscribers and ``ip:<addr>`` for
anonymous callers, so the free public surface is capped without an account.

Quotas live in config (MCP_DAILY_QUOTAS / MCP_ANON_DAILY) — those are the
commercial dials. A quota of None means unlimited.
"""

from __future__ import annotations

import datetime
import sqlite3

from seiche.config import DB_PATH, MCP_ANON_DAILY, MCP_DAILY_QUOTAS


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mcp_usage (
               ukey TEXT NOT NULL,
               day  TEXT NOT NULL,
               calls INTEGER NOT NULL DEFAULT 0,
               PRIMARY KEY (ukey, day)
           )"""
    )
    return conn


def quota_for(identity: dict | None) -> int | None:
    """The daily tool-call ceiling for a caller. None = unlimited."""
    if identity is None:
        return MCP_ANON_DAILY
    # .get with a sentinel so an explicit None (unlimited) isn't overwritten.
    tier = identity.get("tier", "pro")
    return MCP_DAILY_QUOTAS.get(tier, MCP_ANON_DAILY)


def key_for(identity: dict | None, ip: str) -> str:
    if identity is not None:
        return f"user:{identity['username']}"
    return f"ip:{ip}"


def peek(ukey: str) -> int:
    """Calls used today for a key, without recording one."""
    day = _today()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT calls FROM mcp_usage WHERE ukey=? AND day=?", (ukey, day)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else 0


def charge(ukey: str, limit: int | None) -> dict:
    """Record one billable call and report the meter. If `limit` is exceeded the
    call is NOT recorded and ``allowed`` is False (the caller should be refused
    before doing work). None limit = unlimited."""
    day = _today()
    conn = _conn()
    try:
        used = conn.execute(
            "SELECT calls FROM mcp_usage WHERE ukey=? AND day=?", (ukey, day)
        ).fetchone()
        used = used[0] if used else 0
        if limit is not None and used >= limit:
            return {"allowed": False, "used": used, "limit": limit, "remaining": 0}
        conn.execute(
            "INSERT INTO mcp_usage (ukey, day, calls) VALUES (?,?,1) "
            "ON CONFLICT(ukey, day) DO UPDATE SET calls = calls + 1",
            (ukey, day),
        )
        conn.commit()
        used += 1
    finally:
        conn.close()
    remaining = None if limit is None else max(0, limit - used)
    return {"allowed": True, "used": used, "limit": limit, "remaining": remaining}
