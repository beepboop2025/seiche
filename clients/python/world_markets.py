#!/usr/bin/env python3
"""Dependency-free example client for Seiche's public world-markets contract.

The client returns the server payload unchanged. ``contract_receipt`` is a
separate convenience projection so response, source, and evaluation clocks are
never silently collapsed into one timestamp.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.seiche.info"
ALLOWED_SECTIONS = frozenset(
    {
        "summary",
        "money_markets",
        "forex",
        "capital_markets",
        "sources",
        "methodology",
        "all",
    }
)
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000
USER_AGENT = "seiche-public-python-example/1.0 (+https://seiche.info/developers)"


class SeicheClientError(RuntimeError):
    """Raised for transport failures or a response outside the public contract."""


def fetch_world_markets(
    section: str = "sources",
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Fetch one bounded, anonymous REST projection.

    There are deliberately no credentials, hidden retries, or recompute flags.
    A 503 remains unavailable evidence and is never rewritten as an empty or
    calm result.
    """

    if section not in ALLOWED_SECTIONS:
        raise ValueError(
            "section must be one of: " + ", ".join(sorted(ALLOWED_SECTIONS))
        )
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive")

    query = urllib.parse.urlencode({"section": section})
    url = f"{base_url.rstrip('/')}/api/v2/world-markets?{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get("Retry-After")
        suffix = f"; retry-after={retry_after}" if retry_after else ""
        raise SeicheClientError(f"Seiche returned HTTP {exc.code}{suffix}") from exc
    except urllib.error.URLError as exc:
        raise SeicheClientError(f"Seiche request failed: {exc.reason}") from exc

    if len(body) > max_response_bytes:
        raise SeicheClientError(
            f"response exceeded the {max_response_bytes}-byte client limit"
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeicheClientError("Seiche returned invalid UTF-8 JSON") from exc
    _validate_contract(payload, section)
    return payload


def _validate_contract(payload: Any, section: str) -> None:
    if not isinstance(payload, dict):
        raise SeicheClientError("world-markets response must be a JSON object")
    if payload.get("schema") != "seiche.world-markets.v1":
        raise SeicheClientError("unexpected world-markets schema")
    if payload.get("selection") != section:
        raise SeicheClientError("server selection does not match the request")
    if payload.get("context_only") is not True:
        raise SeicheClientError("context-only boundary is missing")
    clocks = payload.get("clocks")
    citation = payload.get("citation")
    scope = payload.get("scope")
    if not isinstance(clocks, dict) or not clocks.get("boundary"):
        raise SeicheClientError("clock boundary is missing")
    if not isinstance(citation, dict) or not citation.get("canonical_url"):
        raise SeicheClientError("citation block is missing")
    if not isinstance(scope, dict) or scope.get("coverage_claim") != (
        "curated_partial_non_exhaustive"
    ):
        raise SeicheClientError("partial-coverage boundary is missing")


def contract_receipt(
    payload: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Return citation, clocks, coverage boundary, and effective client limits."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive")

    return {
        "schema": payload["schema"],
        "selection": payload["selection"],
        "status": payload.get("status"),
        "clocks": payload["clocks"],
        "citation": payload["citation"],
        "scope": payload["scope"],
        "client_limits": {
            "timeout_seconds": timeout_seconds,
            "max_response_bytes": max_response_bytes,
            "automatic_retries": 0,
        },
    }


if __name__ == "__main__":
    print(json.dumps(contract_receipt(fetch_world_markets()), indent=2, sort_keys=True))
