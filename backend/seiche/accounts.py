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
import re
import secrets
import sqlite3
import stat
import threading
import time

from seiche.config import DATA_DIR, DB_PATH

_SCRYPT = dict(n=2**14, r=8, p=1)
TOKEN_TTL_S = 30 * 24 * 3600  # 30 days
_AUTH_SECRET_RE = re.compile(rb"[0-9a-f]{64}")
_AUTH_SECRET_MAX_BYTES = 65
_auth_secret_lock = threading.Lock()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create/migrate the account table on an existing connection.

    Provisioning deliberately supplies its own connection so the paid grant
    and account insert can live in one transaction.  Keeping schema setup here
    avoids a second connection (and therefore a second transaction boundary).

    A standalone caller takes SQLite's writer reservation before inspecting
    the columns.  Without that reservation, two first-time provisions can both
    observe a missing migration and race the same ``ALTER TABLE``.  A caller
    already inside a transaction (the provisioner) owns that reservation and
    must retain control of its commit boundary.
    """
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
                   username TEXT PRIMARY KEY,
                   salt_hex TEXT NOT NULL,
                   hash_hex TEXT NOT NULL,
                   tier TEXT NOT NULL DEFAULT 'pro',
                   created_utc REAL NOT NULL
               )""")
        # idempotent migration: subscriber alert prefs
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
        if "alerts_on" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN alerts_on INTEGER DEFAULT 0")
        if owns_transaction:
            conn.commit()
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_schema(conn)
    return conn


def _expected_secret_uid() -> int:
    return os.geteuid()


def _secret_metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_secret_directory() -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(DATA_DIR, flags)
        opened = os.fstat(descriptor)
        visible = DATA_DIR.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(visible.st_mode)
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o022
        ):
            raise ValueError(
                "auth secret directory must be owner-controlled and non-writable "
                "by group or others"
            )
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _read_auth_secret_at(directory_fd: int) -> bytes:
    descriptor = -1
    try:
        visible_before = os.stat(
            "auth_secret", dir_fd=directory_fd, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(visible_before.st_mode)
            or visible_before.st_nlink != 1
            or visible_before.st_uid != _expected_secret_uid()
            or stat.S_IMODE(visible_before.st_mode) not in {0o400, 0o600}
            or not 1 <= visible_before.st_size <= _AUTH_SECRET_MAX_BYTES
        ):
            raise ValueError(
                "auth secret must be a single-link, owner-only regular file"
            )
        descriptor = os.open(
            "auth_secret",
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        opened = os.fstat(descriptor)
        if _secret_metadata_identity(opened) != _secret_metadata_identity(
            visible_before
        ):
            raise ValueError("auth secret changed before it was opened")
        body = bytearray()
        while len(body) <= _AUTH_SECRET_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(256, _AUTH_SECRET_MAX_BYTES + 1 - len(body)),
            )
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)
        visible_after = os.stat(
            "auth_secret", dir_fd=directory_fd, follow_symlinks=False
        )
        if (
            len(body) > _AUTH_SECRET_MAX_BYTES
            or _secret_metadata_identity(opened) != _secret_metadata_identity(after)
            or _secret_metadata_identity(opened)
            != _secret_metadata_identity(visible_after)
        ):
            raise ValueError("auth secret changed while it was read")
        raw = bytes(body)
        secret = raw[:-1] if raw.endswith(b"\n") else raw
        if (
            raw not in {secret, secret + b"\n"}
            or _AUTH_SECRET_RE.fullmatch(secret) is None
        ):
            raise ValueError("auth secret is not canonical 256-bit hexadecimal")
        return secret
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError("auth secret is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_auth_secret(directory_fd: int, body: bytes) -> bool:
    temporary = f".auth_secret.{secrets.token_hex(16)}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written <= 0:
                raise OSError("auth-secret write made no progress")
            offset += written
        os.fsync(descriptor)
        created = os.fstat(descriptor)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_nlink != 1
            or created.st_uid != _expected_secret_uid()
            or stat.S_IMODE(created.st_mode) != 0o600
            or created.st_size != len(body)
        ):
            raise ValueError("new auth secret has unsafe metadata")
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary,
                "auth_secret",
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        os.unlink(temporary, dir_fd=directory_fd)
        temporary = ""
        os.fsync(directory_fd)
        return True
    except OSError as exc:
        raise ValueError("auth secret could not be created safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _secret() -> bytes:
    env = os.getenv("SEICHE_AUTH_SECRET")
    if env:
        return env.encode()
    with _auth_secret_lock:
        directory_fd = _open_secret_directory()
        try:
            try:
                return _read_auth_secret_at(directory_fd)
            except FileNotFoundError:
                _publish_auth_secret(
                    directory_fd, secrets.token_hex(32).encode("ascii")
                )
                return _read_auth_secret_at(directory_fd)
        finally:
            os.close(directory_fd)


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT).hex()


def add_user(
    username: str,
    password: str,
    tier: str = "pro",
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Insert a new account, never replace an existing identity.

    ``conn`` is used by the payment provisioner to make the account insert
    part of its ``BEGIN IMMEDIATE`` transaction.  Other callers retain the
    simple one-call API and get a transaction owned by this function.
    """
    if not username or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("username must be alphanumeric (plus - _)")
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    salt = os.urandom(16)
    params = (username, salt.hex(), _hash(password, salt), tier, time.time())
    statement = (
        "INSERT INTO users (username, salt_hex, hash_hex, tier, created_utc) "
        "VALUES (?,?,?,?,?)"
    )
    if conn is not None:
        _ensure_schema(conn)
        conn.execute(statement, params)
        return
    with _conn() as owned:
        owned.execute(statement, params)


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


def current_identity(username: str) -> dict | None:
    """Return the account's current authorization identity, if it still exists."""

    with _conn() as conn:
        row = conn.execute(
            "SELECT username, tier FROM users WHERE username=?", (username,)
        ).fetchone()
    if row is None:
        return None
    return {"username": row[0], "tier": row[1]}


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
    try:
        expires_at = int(exp_s)
    except ValueError:
        return None
    if expires_at < (now or time.time()):
        return None
    return {"username": username, "tier": tier}


def verify_current_token(token: str, now: float | None = None) -> dict | None:
    """Verify the token and bind it to the account's current tier.

    HMAC validity proves that Seiche issued the token; it does not prove the
    account still exists or retains the same entitlement. Looking up the
    current row makes deletion and tier changes revoke old tokens immediately.
    """

    identity = verify_token(token, now=now)
    if identity is None:
        return None
    current = current_identity(identity["username"])
    if current != identity:
        return None
    return current


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
        conn.execute(
            "UPDATE users SET email=?, alerts_on=? WHERE username=?",
            (email, 1 if alerts_on else 0, username),
        )
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
