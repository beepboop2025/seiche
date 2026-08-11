"""Narrow systemd entry point for a root-controller-approved activation."""

from __future__ import annotations

import hmac
import json
import logging
import os
from pathlib import Path
import re
import stat
from typing import Any


REQUEST_DIRECTORY = Path("/run/seiche-release")
REQUEST_PATH = REQUEST_DIRECTORY / "promotion-request.json"
MAX_REQUEST_BYTES = 4096
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")


def _request_bytes() -> bytes:
    """Read only the installer-created, root-owned controller request."""
    directory_stat = os.stat(REQUEST_DIRECTORY, follow_symlinks=False)
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != 0
        or directory_stat.st_gid != os.getegid()
        or stat.S_IMODE(directory_stat.st_mode) != 0o750
    ):
        raise ValueError("unsafe promotion request directory")

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(REQUEST_PATH, flags)
    try:
        request_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(request_stat.st_mode)
            or request_stat.st_uid != 0
            or request_stat.st_gid != os.getegid()
            or stat.S_IMODE(request_stat.st_mode) != 0o640
        ):
            raise ValueError("unsafe promotion request file")
        raw = os.read(descriptor, MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("promotion request is too large")
        return raw
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("duplicate promotion request key")
        parsed[key] = value
    return parsed


def _load_request() -> tuple[str, str]:
    payload = json.loads(
        _request_bytes().decode("utf-8"),
        object_pairs_hook=_unique_object,
    )
    if not isinstance(payload, dict) or set(payload) != {
        "expected_sha",
        "activation_token",
    }:
        raise ValueError("invalid promotion request shape")

    expected_sha = payload.get("expected_sha")
    activation_token = payload.get("activation_token")
    release_sha = os.environ.get("SEICHE_RELEASE_SHA")
    if not isinstance(expected_sha, str) or _SHA_PATTERN.fullmatch(expected_sha) is None:
        raise ValueError("invalid expected release SHA")
    if (
        not isinstance(activation_token, str)
        or _TOKEN_PATTERN.fullmatch(activation_token) is None
    ):
        raise ValueError("invalid activation token")
    if release_sha is None or _SHA_PATTERN.fullmatch(release_sha) is None:
        raise ValueError("invalid process release SHA")
    if not hmac.compare_digest(expected_sha, release_sha):
        raise ValueError("promotion request does not match process release")
    return expected_sha, activation_token


def main() -> int:
    try:
        from seiche import assemble

        expected_sha, activation_token = _load_request()
        activated = assemble.activate_pending_snapshot(
            expected_sha,
            activation_token,
        )
    except Exception:  # noqa: BLE001 - the controller receives only an exit status
        logging.getLogger("seiche.release_promote").exception(
            "rejected the snapshot promotion request"
        )
        return 1
    return 0 if activated else 1


if __name__ == "__main__":
    raise SystemExit(main())
