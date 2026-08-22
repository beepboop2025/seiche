"""Small, fail-closed client for the anonymous Seiche API."""

from __future__ import annotations

import json
import os
import re
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx
from openbb_core.app.model.abstract.error import OpenBBError

DEFAULT_BASE_URL = "https://api.seiche.info"
USER_AGENT = "openbb-seiche/0.1.0 (+https://seiche.info)"
MAX_RESPONSE_BYTES = 2_000_000
HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _valid_hostname(hostname: str) -> bool:
    """Accept IP literals or well-formed IDNA DNS labels."""
    try:
        ip_address(hostname)
        return True
    except ValueError:
        pass
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError:
        return False
    labels = ascii_hostname.split(".")
    return (
        bool(ascii_hostname)
        and len(ascii_hostname) <= 253
        and all(HOST_LABEL.fullmatch(label) for label in labels)
    )


def base_url() -> str:
    """Return a trusted API origin, allowing localhost for development only."""
    value = os.getenv("SEICHE_OPENBB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if value != value.strip() or "\\" in value or any(char.isspace() for char in value):
        raise OpenBBError("SEICHE_OPENBB_BASE_URL contains unsafe URL characters.")
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise OpenBBError("SEICHE_OPENBB_BASE_URL is malformed.") from exc
    try:
        port = parsed.port
    except ValueError as exc:
        raise OpenBBError("SEICHE_OPENBB_BASE_URL contains an invalid port.") from exc
    if not hostname or "%" in parsed.netloc or not _valid_hostname(hostname):
        raise OpenBBError("SEICHE_OPENBB_BASE_URL contains an invalid hostname.")
    local = hostname in {"localhost", "127.0.0.1", "::1"}
    if not parsed.hostname or (
        parsed.scheme != "https" and not (local and parsed.scheme == "http")
    ):
        raise OpenBBError(
            "SEICHE_OPENBB_BASE_URL must use HTTPS (HTTP is allowed only for localhost)."
        )
    if port == 0:
        raise OpenBBError("SEICHE_OPENBB_BASE_URL contains an invalid port.")
    if (
        parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise OpenBBError(
            "SEICHE_OPENBB_BASE_URL must be a bare origin without credentials."
        )
    return value


def canonical_url(path: str) -> str:
    """Build the exact public/source URL used by a fetcher."""
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "?" in path
        or "#" in path
        or "\\" in path
    ):
        raise OpenBBError(
            "Seiche API paths must be absolute-origin paths without a query."
        )
    return f"{base_url()}{path}"


async def get_json(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """GET a public contract and convert transport/schema faults to OpenBB errors."""
    url = canonical_url(path)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        follow_redirects=False,
    )
    try:
        async with active_client.stream(
            "GET",
            url,
            params=params,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json" and not media_type.endswith("+json"):
                raise OpenBBError(
                    f"Seiche public API returned non-JSON content for {path}."
                )
            announced = response.headers.get("Content-Length")
            if (
                announced
                and announced.isdecimal()
                and int(announced) > MAX_RESPONSE_BYTES
            ):
                raise OpenBBError(
                    f"Seiche public API response exceeded {MAX_RESPONSE_BYTES} bytes for {path}."
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise OpenBBError(
                        f"Seiche public API response exceeded {MAX_RESPONSE_BYTES} bytes for {path}."
                    )
        payload = json.loads(body)
    except (httpx.HTTPError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenBBError(
            f"Seiche public API request failed for {path}: {exc}"
        ) from exc
    finally:
        if owns_client:
            await active_client.aclose()
    if not isinstance(payload, dict):
        raise OpenBBError(
            f"Seiche public API returned a non-object payload for {path}."
        )
    return payload
