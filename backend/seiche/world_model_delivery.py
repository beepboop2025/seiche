"""Fail-closed, opaque access to one signed Lab delivery envelope.

Seiche is only a byte relay at this boundary.  It does not parse the JSON,
verify the publisher signature, or derive any authority from the artifact.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import hmac
import os
from pathlib import Path
import re
import stat
from typing import BinaryIO


DELIVERY_ROUTE = "/api/internal/v1/world-model/us-usd-funding-core-v2"
DELIVERY_PATH_ENV = "SEICHE_WORLD_MODEL_DELIVERY_PATH"
DELIVERY_TOKEN_ENV = "SEICHE_WORLD_MODEL_DELIVERY_BEARER_TOKEN"
DELIVERY_MAX_BYTES_ENV = "SEICHE_WORLD_MODEL_DELIVERY_MAX_BYTES"
DELIVERY_FILENAME = "us-usd-funding-core-v2.json"
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
HARD_MAX_BYTES = 5 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024

_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


class DeliveryUnavailable(RuntimeError):
    """The configured delivery cannot be served without weakening a guard."""


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    path: Path
    bearer_token: str
    max_bytes: int


@dataclass(slots=True)
class OpenDelivery:
    handle: BinaryIO
    size: int


def configured_delivery() -> DeliveryConfig | None:
    """Return an enabled, syntactically safe relay config or ``None``.

    Both the exact path and bearer token are mandatory, so a default install
    has no usable relay.  Invalid values fail closed exactly like missing ones.
    """

    raw_path = os.getenv(DELIVERY_PATH_ENV, "").strip()
    bearer_token = os.getenv(DELIVERY_TOKEN_ENV, "")
    raw_max = os.getenv(DELIVERY_MAX_BYTES_ENV, str(DEFAULT_MAX_BYTES)).strip()
    if not raw_path or not bearer_token:
        return None
    path = Path(raw_path)
    if (
        not path.is_absolute()
        or path.name != DELIVERY_FILENAME
        or any(part in {".", ".."} for part in path.parts[1:])
        or _TOKEN_RE.fullmatch(bearer_token) is None
    ):
        return None
    try:
        max_bytes = int(raw_max, 10)
    except ValueError:
        return None
    if not 1 <= max_bytes <= HARD_MAX_BYTES:
        return None
    return DeliveryConfig(path=path, bearer_token=bearer_token, max_bytes=max_bytes)


def bearer_authorized(config: DeliveryConfig, authorization: str | None) -> bool:
    """Compare the supplied bearer credential without data-dependent equality."""

    prefix = "Bearer "
    valid_scheme = bool(authorization and authorization.startswith(prefix))
    supplied = authorization[len(prefix) :] if valid_scheme and authorization else ""
    matches = hmac.compare_digest(
        supplied.encode("utf-8"), config.bearer_token.encode("utf-8")
    )
    return valid_scheme and matches


def _lstat_without_symlinks(path: Path) -> os.stat_result:
    current = Path(path.anchor)
    result: os.stat_result | None = None
    for part in path.parts[1:]:
        current /= part
        result = os.lstat(current)
        if stat.S_ISLNK(result.st_mode):
            raise DeliveryUnavailable("symlink components are not accepted")
    if result is None:
        raise DeliveryUnavailable("delivery path has no file component")
    return result


def open_delivery(config: DeliveryConfig) -> OpenDelivery:
    """Open the configured inode without following links and enforce its size."""

    try:
        before = _lstat_without_symlinks(config.path)
    except OSError as exc:
        raise DeliveryUnavailable("delivery is unavailable") from exc
    if not stat.S_ISREG(before.st_mode):
        raise DeliveryUnavailable("delivery is not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(config.path, flags)
    except OSError as exc:
        raise DeliveryUnavailable("delivery cannot be opened safely") from exc
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or (observed.st_dev, observed.st_ino) != (before.st_dev, before.st_ino)
            or stat.S_IMODE(observed.st_mode) != 0o440
            or not 0 < observed.st_size <= config.max_bytes
        ):
            raise DeliveryUnavailable("delivery failed regular-file or size guards")
        return OpenDelivery(os.fdopen(descriptor, "rb", closefd=True), observed.st_size)
    except Exception:
        os.close(descriptor)
        raise


def iter_delivery(opened: OpenDelivery) -> Iterator[bytes]:
    """Yield exactly the bytes present in the opened, size-bounded inode."""

    remaining = opened.size
    with opened.handle:
        while remaining:
            chunk = opened.handle.read(min(STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                raise DeliveryUnavailable("delivery changed while it was being read")
            remaining -= len(chunk)
            yield chunk


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DELIVERY_MAX_BYTES_ENV",
    "DELIVERY_PATH_ENV",
    "DELIVERY_ROUTE",
    "DELIVERY_TOKEN_ENV",
    "DeliveryUnavailable",
    "bearer_authorized",
    "configured_delivery",
    "iter_delivery",
    "open_delivery",
]
