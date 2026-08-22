#!/usr/bin/env python3
"""Verify that the canonical direct-OFR pages serve the deployed exact bytes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


MAX_RESPONSE_BYTES = 2_000_000
LANDING_PATH = "datasets/direct-ofr/index.html"
CATALOG_PATH = "datasets/direct-ofr/catalog.jsonld"
CANONICAL_DATASET = "https://seiche.info/datasets/direct-ofr/"


class VerificationError(RuntimeError):
    """The canonical deployment differs from the reviewed dataset surface."""


def _fetch(url: str, *, timeout: float) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
            "User-Agent": "seiche-public-dataset-verifier/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        media_type = response.headers.get_content_type()
    if len(body) > MAX_RESPONSE_BYTES:
        raise VerificationError(f"oversized response from {url}")
    return body, media_type


def _dataset_node(catalog: dict[str, Any]) -> dict[str, Any]:
    matches = [
        node
        for node in catalog.get("@graph", [])
        if isinstance(node, dict)
        and node.get("@id") == CANONICAL_DATASET
        and node.get("@type") == "dcat:Dataset"
    ]
    if len(matches) != 1:
        raise VerificationError("public DCAT graph lacks one canonical Dataset node")
    return matches[0]


def verify_public_dataset(
    *,
    base_url: str,
    expected_root: Path,
    cache_key: str,
    timeout: float,
) -> dict[str, Any]:
    """Fetch the canonical pair and compare it with the deployed directory."""

    expected_landing = (expected_root / LANDING_PATH).read_bytes()
    expected_catalog = (expected_root / CATALOG_PATH).read_bytes()
    query = urllib.parse.urlencode({"deployment": cache_key})
    landing_url = f"{base_url.rstrip('/')}/datasets/direct-ofr/?{query}"
    catalog_url = f"{base_url.rstrip('/')}/datasets/direct-ofr/catalog.jsonld?{query}"
    landing, landing_type = _fetch(landing_url, timeout=timeout)
    catalog_body, catalog_type = _fetch(catalog_url, timeout=timeout)

    if landing_type != "text/html":
        raise VerificationError(f"landing media type is {landing_type!r}")
    if catalog_type != "application/ld+json":
        raise VerificationError(f"catalog media type is {catalog_type!r}")
    if landing != expected_landing:
        raise VerificationError("canonical landing bytes differ from the deployment")
    if catalog_body != expected_catalog:
        raise VerificationError("canonical catalog bytes differ from the deployment")

    try:
        catalog = json.loads(catalog_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("canonical catalog is not UTF-8 JSON") from exc
    dataset = _dataset_node(catalog)
    if dataset.get("seiche:seriesCount") != 10:
        raise VerificationError("canonical catalog does not receipt ten series")
    if dataset.get("seiche:recordCount") != 11_163:
        raise VerificationError("canonical catalog does not receipt 11,163 records")
    if dataset.get("seiche:publicationStatus") != "draft_not_submitted":
        raise VerificationError("canonical catalog publication status changed")
    if dataset.get("seiche:doi") is not None:
        raise VerificationError("canonical draft unexpectedly claims a DOI")
    return {
        "status": "verified",
        "landing_url": CANONICAL_DATASET,
        "catalog_url": f"{CANONICAL_DATASET}catalog.jsonld",
        "series": 10,
        "records": 11_163,
        "publication_status": "draft_not_submitted",
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://seiche.info")
    parser.add_argument("--expected-root", type=Path, required=True)
    parser.add_argument("--cache-key", required=True)
    parser.add_argument("--attempts", type=_positive_int, default=18)
    parser.add_argument("--interval-seconds", type=_positive_float, default=10.0)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=15.0)
    args = parser.parse_args()

    last_error: Exception | None = None
    for attempt in range(1, args.attempts + 1):
        try:
            receipt = verify_public_dataset(
                base_url=args.base_url,
                expected_root=args.expected_root,
                cache_key=args.cache_key,
                timeout=args.timeout_seconds,
            )
        except (
            OSError,
            VerificationError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ) as exc:
            last_error = exc
            print(f"dataset deployment probe {attempt}/{args.attempts}: {exc}")
            if attempt < args.attempts:
                time.sleep(args.interval_seconds)
            continue
        print(json.dumps(receipt, sort_keys=True))
        return 0
    raise SystemExit(f"canonical dataset deployment never converged: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
