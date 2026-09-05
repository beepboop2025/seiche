#!/usr/bin/env python3
"""Wait for startup readiness before taking the strict recovery health proof."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BODY_BYTES = 65536
STARTUP_STATES = {"warming_or_unavailable", "rebuilding_from_last_known_good"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_health(url: str, timeout: float) -> tuple[int, dict]:
    request = Request(url, headers={"User-Agent": "Seiche-Recovery-Monitor/1.0"})
    try:
        response = build_opener(NoRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_BODY_BYTES + 1)
        status = response.code
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("runtime health response exceeds the size limit")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("runtime health response is not an object")
    return status, payload


def wait_until_ready(
    origin: str,
    timeout_seconds: float,
    *,
    fetch: Callable = read_health,
    clock: Callable = time.monotonic,
    pause: Callable = time.sleep,
) -> dict:
    if not re.fullmatch(r"https://[a-z0-9][a-z0-9.-]{1,251}\.up\.railway\.app", origin):
        raise ValueError("invalid Railway origin")
    if not 0 < timeout_seconds <= 900:
        raise ValueError("startup wait must be within fifteen minutes")
    deadline = clock() + timeout_seconds
    while (remaining := deadline - clock()) > 0:
        status, payload = fetch(origin + "/healthz", min(30, remaining))
        if clock() >= deadline:
            break
        if status == 200 and payload.get("status") == "ready":
            if payload.get("mode") != "production":
                raise ValueError("runtime is ready without production authority")
            return payload
        if status != 503 or payload.get("status") not in STARTUP_STATES:
            raise ValueError(
                f"runtime health is not a retryable startup state (HTTP {status})"
            )
        print("Recovery monitor waiting for the first production board", flush=True)
        pause(min(10, deadline - clock()))
    raise TimeoutError("production board was not ready within the startup window")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900)
    args = parser.parse_args()
    payload = wait_until_ready(args.origin, args.timeout_seconds)
    args.output.write_text(json.dumps(payload, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
