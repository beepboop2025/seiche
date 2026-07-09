"""Subscriber accounts — stdlib-only auth for the gated endpoints.

The public window (api.seiche.info) serves the live board to everyone; the
Time Machine replay is the subscriber feature. Design matches the project's
ethos: no new dependencies, fail loud, nothing clever.

  * passwords: hashlib.scrypt (n=2^14, r=8, p=1), per-user 16-byte salt;
  * tokens: HMAC-SHA256 over "username|tier|expiry" with a secret that lives
    in DATA_DIR/auth_secret (created 0600 on first use) or SEICHE_AUTH_SECRET;
  * the gate is OPT-IN: SEICHE_ASOF_AUTH=1 turns it on (the box); dev and
    tests run open unless they say otherwise.

Accounts are provisioned by the operator (`seiche user add NAME`), not by
self-signup — payments come later; this is the lock, not the till.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time

from seiche.config import DATA_DIR, DB_PATH

_SCRYPT = dict(n=2**14, r=8, p=1)
TOKEN_TTL_S = 30 * 24 * 3600  # 30 days


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
               username TEXT PRIMARY KEY,
               salt_hex TEXT NOT NULL,
               hash_hex TEXT NOT NULL,
               tier TEXT NOT NULL DEFAULT 'pro',
               created_utc REAL NOT NULL
           )"""
    )
    # idempotent migration: subscriber alert prefs
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "email" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
    if "alerts_on" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN alerts_on INTEGER DEFAULT 0")
    return conn


def _secret() -> bytes:
    env = os.getenv("SEICHE_AUTH_SECRET")
    if env:
        return env.encode()
    path = DATA_DIR / "auth_secret"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return path.read_text().strip().encode()


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT).hex()


def add_user(username: str, password: str, tier: str = "pro") -> None:
    if not username or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("username must be alphanumeric (plus - _)")
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    salt = os.urandom(16)
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (username, salt_hex, hash_hex, tier, created_utc) "
            "VALUES (?,?,?,?,?)",
            (username, salt.hex(), _hash(password, salt), tier, time.time()),
        )


def verify_user(username: str, password: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT salt_hex, hash_hex, tier FROM users WHERE username=?", (username,)
        ).fetchone()
    if row is None:
        return None
    salt_hex, hash_hex, tier = row
    if hmac.compare_digest(_hash(password, bytes.fromhex(salt_hex)), hash_hex):
        return {"username": username, "tier": tier}
    return None


def user_exists(username: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE username=?", (username,)
        ).fetchone()
    return row is not None


def list_users() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT username, tier, created_utc FROM users").fetchall()
    return [{"username": u, "tier": t, "created_utc": c} for u, t, c in rows]


# ---- tokens -----------------------------------------------------------------

def issue_token(username: str, tier: str, now: float | None = None) -> dict:
    exp = int((now or time.time()) + TOKEN_TTL_S)
    body = f"{username}|{tier}|{exp}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return {"token": f"{body}|{sig}", "expires_utc": exp, "tier": tier}


def verify_token(token: str, now: float | None = None) -> dict | None:
    parts = token.split("|")
    if len(parts) != 4:
        return None
    username, tier, exp_s, sig = parts
    body = f"{username}|{tier}|{exp_s}"
    want = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig):
        return None
    if int(exp_s) < (now or time.time()):
        return None
    return {"username": username, "tier": tier}


def get_alert_prefs(username: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT email, alerts_on FROM users WHERE username=?", (username,)
        ).fetchone()
    if row is None:
        return {"email": "", "alerts_on": False}
    return {"email": row[0] or "", "alerts_on": bool(row[1])}


def set_alert_prefs(username: str, email: str, alerts_on: bool) -> dict:
    email = (email or "").strip()
    if email and ("@" not in email or len(email) > 254):
        raise ValueError("invalid email")
    if alerts_on and not email:
        raise ValueError("an email is required to turn alerts on")
    with _conn() as conn:
        conn.execute("UPDATE users SET email=?, alerts_on=? WHERE username=?",
                     (email, 1 if alerts_on else 0, username))
    return {"email": email, "alerts_on": alerts_on}


def alert_recipients() -> list[str]:
    """Emails of subscribers who have alerts on — the notify fan-out list."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT email FROM users WHERE alerts_on=1 AND email != ''"
        ).fetchall()
    return [r[0] for r in rows]


def asof_gate_enabled() -> bool:
    return os.getenv("SEICHE_ASOF_AUTH", "0") == "1"
