"""Provisioning — the funnel's last mile: a confirmed payment becomes a
subscriber account + bearer token.

A payment processor's signed webhook (BTCPay, NOWPayments, Stripe, ...) or the
operator CLI calls ``provision()`` with a tier and a payment reference. It:

  * creates the account with a strong auto-generated password,
  * issues a 30-day bearer token,
  * records the payment for idempotency + audit,
  * best-effort emails the credentials if an address and SMTP are configured.

Idempotent on ``payment_ref`` — a retried webhook never double-grants and never
re-issues a password, even under a concurrent retry (the ref is claimed
atomically via a PRIMARY KEY before any account is created). stdlib-only,
matching the project ethos.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time

from seiche import accounts, mailer
from seiche.config import DB_PATH, MCP_UPGRADE_URL, SUBSCRIBER_TIERS

PROVISION_SECRET_ENV = "SEICHE_PROVISION_SECRET"


class ProvisionError(ValueError):
    """A provisioning request that cannot be honoured (bad tier / username)."""


def enabled() -> bool:
    """Provisioning-over-HTTP is opt-in: without a shared secret the webhook is
    disabled (fail closed — never provision on an unauthenticated request)."""
    return bool(os.getenv(PROVISION_SECRET_ENV))


def verify_signature(raw: bytes, signature: str | None) -> bool:
    """HMAC-SHA256 of the raw request body with SEICHE_PROVISION_SECRET, hex.
    Accepts an optional ``sha256=`` prefix (Stripe/GitHub style)."""
    secret = os.getenv(PROVISION_SECRET_ENV)
    if not secret or not signature:
        return False
    sig = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig.strip())


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")   # ride out the shared writer lock
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provisions (
               payment_ref TEXT PRIMARY KEY,
               username    TEXT NOT NULL,
               tier        TEXT NOT NULL,
               email       TEXT NOT NULL DEFAULT '',
               amount      REAL,
               currency    TEXT NOT NULL DEFAULT '',
               created_utc REAL NOT NULL
           )"""
    )
    return conn


def _existing(conn: sqlite3.Connection, payment_ref: str) -> dict | None:
    row = conn.execute(
        "SELECT username, tier, email, created_utc FROM provisions WHERE payment_ref=?",
        (payment_ref,),
    ).fetchone()
    if row is None:
        return None
    return {"username": row[0], "tier": row[1], "email": row[2], "created_utc": row[3]}


def _replay(record: dict | None) -> dict:
    """Shape a repeat of an already-honoured payment_ref: no new password/token."""
    return {**(record or {}), "already": True, "token": None, "password": None}


def _valid_username(u: str) -> bool:
    return bool(u) and u.replace("_", "").replace("-", "").isalnum()


def _username_from(email: str) -> str:
    """A valid (alphanumeric + - _), unique-ish username. From the email
    local-part when present, else a random subscriber id, plus a short suffix
    so two buyers with the same local-part don't collide."""
    base = ""
    if email and "@" in email:
        base = re.sub(r"[^A-Za-z0-9_-]", "", email.split("@", 1)[0])[:24]
    base = base or "sub"
    return f"{base}_{secrets.token_hex(3)}"


def provision(tier: str, *, email: str = "", username: str = "",
              payment_ref: str = "", amount: float | None = None,
              currency: str = "") -> dict:
    """Grant a subscription. Returns the credentials on first grant; on a repeat
    of the same payment_ref returns the recorded account with ``already`` True
    and no password (it is shown only once)."""
    tier = (tier or "").strip().lower()
    if tier not in SUBSCRIBER_TIERS:
        raise ProvisionError(
            f"unknown tier '{tier}' — choose one of {', '.join(SUBSCRIBER_TIERS)}"
        )
    # A missing reference still gets a unique key so every grant is recorded and
    # idempotency holds for the retry of a *given* request.
    payment_ref = (payment_ref or f"manual:{secrets.token_hex(8)}").strip()

    requested = (username or _username_from(email)).strip()
    if not _valid_username(requested):
        raise ProvisionError("username must be alphanumeric (plus - and _)")

    conn = _conn()
    try:
        prior = _existing(conn, payment_ref)
        if prior is not None:
            return _replay(prior)

        # Never overwrite an existing account: accounts.add_user is INSERT OR
        # REPLACE (used elsewhere for deliberate password resets), so a colliding
        # username — including a buyer-supplied one echoed through the webhook —
        # would clobber that account (account takeover). Grant a suffixed name
        # instead; the payer still gets access, the victim is untouched.
        uname = requested
        while accounts.user_exists(uname):
            uname = f"{requested}_{secrets.token_hex(3)}"
        password = secrets.token_urlsafe(14)

        # Claim the payment_ref ATOMICALLY before creating the account. A
        # concurrent retry of the same ref loses this PRIMARY KEY insert and is
        # handled as a replay, so one payment can never mint two accounts.
        try:
            conn.execute(
                "INSERT INTO provisions (payment_ref, username, tier, email, "
                "amount, currency, created_utc) VALUES (?,?,?,?,?,?,?)",
                (payment_ref, uname, tier, email or "", amount, currency or "", time.time()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return _replay(_existing(conn, payment_ref))

        accounts.add_user(uname, password, tier=tier)
    finally:
        conn.close()

    token = accounts.issue_token(uname, tier)
    result = {
        "already": False,
        "username": uname,
        "password": password,          # shown ONCE
        "tier": tier,
        "token": token["token"],
        "token_expires_utc": token["expires_utc"],
        "payment_ref": payment_ref,
    }
    if email:
        _deliver(email, result)
    return result


def _deliver(email: str, cred: dict) -> None:
    body = (
        "Your Seiche subscription is active.\n\n"
        f"  username: {cred['username']}\n"
        f"  password: {cred['password']}   (shown once — store it safely)\n"
        f"  tier:     {cred['tier']}\n\n"
        "Use it as a bearer token against the API and the MCP endpoint:\n"
        "  https://api.seiche.info/mcp\n\n"
        "Log in for a fresh 30-day token any time at /api/auth/login.\n"
        f"Manage or upgrade: {MCP_UPGRADE_URL}\n"
    )
    try:
        mailer.send(email, "Your Seiche subscription is active", body)
    except Exception:  # delivery is best-effort; never fail a paid provision
        pass
