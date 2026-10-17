#!/usr/bin/env python3
"""Renew one native Undertow MCP credential without changing entitlement.

Run locally as root. Only the derived bearer credential enters the gateway's
private state directory. Native signing keys and owner identifiers never leave
this process, enter command arguments, or appear in the status output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import importlib
import json
import os
from pathlib import Path
import pwd
import secrets
import stat
import sys
import time

TTL_SECONDS = 30 * 86400
RENEW_WITHIN_SECONDS = 7 * 86400
ENV_KEYS = frozenset({"UNDERTOW_WEB_SECRET", "UNDERTOW_OWNER_CHAT", "UNDERTOW_BOT_STATE", "UNDERTOW_REPO"})
ENVELOPE_KEYS = frozenset({"token", "expires_at", "product", "purpose"})


def _private_bytes(path, maximum):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > maximum:
            raise ValueError("Expected a bounded private regular file")
        value = os.read(fd, maximum + 1)
        if len(value) > maximum:
            raise ValueError("Private file exceeds its bound")
        return value
    finally:
        os.close(fd)


def read_native_environment(path):
    """Read fixed literal assignments; never evaluate shell or source a file."""
    values = {}
    for raw in _private_bytes(path, 65536).decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if key not in ENV_KEYS:
            continue
        if not separator or key in values:
            raise ValueError("Invalid or repeated native configuration key")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Malformed quoted native configuration value")
            value = value[1:-1]
        if not value or "\x00" in value or len(value) > 8192:
            raise ValueError("Invalid native configuration value")
        values[key] = value
    if set(values) != ENV_KEYS:
        raise ValueError("Required native configuration is missing")
    return values


def load_native(repo):
    """Import the deployed native verifier and issuer from the pinned repo."""
    repo = Path(repo).resolve(strict=True)
    module_path = repo / "undertow_mm" / "webauth.py"
    if not module_path.is_file():
        raise ValueError("Native authentication module is absent")
    sys.path.insert(0, str(repo))
    try:
        module = importlib.import_module("undertow_mm.webauth")
    finally:
        sys.path.pop(0)
    if Path(module.__file__).resolve() != module_path.resolve():
        raise ValueError("Native authentication import came from another repository")
    return module


def _claims(native, token, secret, owner, *, now):
    claims = native.verify_agent_token(token, secret, now=now)
    if not isinstance(claims, dict) or claims.get("chat_id") != owner:
        raise ValueError("Native credential identity or signature is invalid")
    expiry = claims.get("exp")
    if not isinstance(expiry, int) or isinstance(expiry, bool) or expiry <= 0:
        raise ValueError("Native credential expiration is invalid")
    return claims


def _existing_expiry(path, native, secret, owner):
    record = json.loads(_private_bytes(path, 16384))
    if not isinstance(record, dict) or set(record) != ENVELOPE_KEYS:
        raise ValueError("Unsupported gateway credential envelope")
    if record["product"] != "undertow" or record["purpose"] != "public_mcp_gateway":
        raise ValueError("Credential scope mismatch")
    token = record["token"]
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise ValueError("Invalid native bearer token")
    # Epoch verification authenticates signature and MCP audience even if a
    # previously valid token has expired. Current-time validity is checked below.
    claims = _claims(native, token, secret, owner, now=0)
    expiry = datetime.fromisoformat(record["expires_at"])
    if expiry.tzinfo is None or expiry.utcoffset() is None or expiry.timestamp() != claims["exp"]:
        raise ValueError("Envelope expiry does not match the signed native expiry")
    return claims["exp"]


def _replace_private(directory_fd, name, value, uid, gid):
    temporary = ".undertow-token-" + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory_fd)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "w") as handle:
            fd = -1
            json.dump(value, handle, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def renew(native_env, native_repo, output, runtime_user, *, now=None, native=None):
    now = int(time.time()) if now is None else int(now)
    values = read_native_environment(native_env)
    repo = Path(native_repo).resolve(strict=True)
    if Path(values["UNDERTOW_REPO"]).resolve(strict=True) != repo:
        raise ValueError("Configured native repository does not match the pinned repository")
    state = Path(values["UNDERTOW_BOT_STATE"])
    if not state.is_dir():
        raise ValueError("Native entitlement state directory is absent")
    native = native if native is not None else load_native(repo)
    owner, secret = values["UNDERTOW_OWNER_CHAT"], values["UNDERTOW_WEB_SECRET"]
    permission = native.entitlement(owner, state, owner=owner)
    if not isinstance(permission, dict) or not permission.get("entitled") or "mcp" not in permission.get("grants", []):
        raise ValueError("Existing native identity has no MCP entitlement")
    account = pwd.getpwnam(runtime_user)
    output = Path(output)
    directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    lock_fd = None
    try:
        info = os.fstat(directory_fd)
        if info.st_mode & 0o077:
            raise ValueError("Credential directory must be private")
        lock_fd = os.open(".undertow-token-renew.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory_fd)
        lock_info = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_mode & 0o077 or lock_info.st_uid != os.geteuid():
            raise ValueError("Unsafe renewal lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existed = output.exists() or output.is_symlink()
        if existed:
            expiry = _existing_expiry(output, native, secret, owner)
            if expiry - now > RENEW_WITHIN_SECONDS:
                return {"status": "current", "expires_at": datetime.fromtimestamp(expiry, timezone.utc).isoformat(), "rotated": False}
        token = native.issue_agent_token(owner, "seiche-public-mcp-gateway", secret, ttl_s=TTL_SECONDS, now=now)
        if not isinstance(token, str) or not token or len(token) > 8192:
            raise ValueError("Native issuer returned an invalid token")
        claims = _claims(native, token, secret, owner, now=now)
        if claims["exp"] != now + TTL_SECONDS:
            raise ValueError("Native issuer did not honor the bounded lifetime")
        expiry_iso = datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat()
        envelope = {"token": token, "expires_at": expiry_iso, "product": "undertow", "purpose": "public_mcp_gateway"}
        _replace_private(directory_fd, output.name, envelope, account.pw_uid, account.pw_gid)
        return {"status": "renewed" if existed else "created", "expires_at": expiry_iso, "rotated": True,
                "lifetime_days": 30, "renew_within_days": 7, "entitlement_changed": False}
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(directory_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-env", default="/etc/undertow-mcp.env")
    parser.add_argument("--native-repo", default="/opt/liquilens-undertow")
    parser.add_argument("--output", default="/var/lib/seiche-agent-network/undertow-mcp-token.json")
    parser.add_argument("--runtime-user", default="seiche-network")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise PermissionError("Root is required for native secret access")
        result = renew(args.native_env, args.native_repo, args.output, args.runtime_user)
        print(json.dumps(result))
        return 0
    except Exception:
        print("Undertow gateway credential renewal failed; no credential was disclosed. Check private configuration, current native signature, entitlement and file permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
