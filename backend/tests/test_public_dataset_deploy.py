"""Offline contracts for the canonical direct-OFR deployment verifier."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "release" / "verify_public_dataset.py"
SPEC = importlib.util.spec_from_file_location("verify_public_dataset", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _responses() -> dict[str, tuple[bytes, str]]:
    public = ROOT / "frontend" / "public"
    return {
        "/datasets/direct-ofr/": (
            (public / verifier.LANDING_PATH).read_bytes(),
            "text/html",
        ),
        "/datasets/direct-ofr/catalog.jsonld": (
            (public / verifier.CATALOG_PATH).read_bytes(),
            "application/ld+json",
        ),
    }


def test_exact_public_dataset_pair_produces_a_bounded_receipt(monkeypatch):
    responses = _responses()

    def fetch(url: str, *, timeout: float):
        assert timeout == 3.0
        return responses[verifier.urllib.parse.urlparse(url).path]

    monkeypatch.setattr(verifier, "_fetch", fetch)
    receipt = verifier.verify_public_dataset(
        base_url="https://seiche.info",
        expected_root=ROOT / "frontend" / "public",
        cache_key="signed-sha",
        timeout=3.0,
    )
    assert receipt == {
        "status": "verified",
        "landing_url": "https://seiche.info/datasets/direct-ofr/",
        "catalog_url": ("https://seiche.info/datasets/direct-ofr/catalog.jsonld"),
        "series": 10,
        "records": 11163,
        "publication_status": "draft_not_submitted",
    }


@pytest.mark.parametrize("failure", ["mime", "bytes", "records"])
def test_public_dataset_pair_fails_closed_on_served_drift(monkeypatch, failure):
    responses = _responses()
    catalog_path = "/datasets/direct-ofr/catalog.jsonld"
    if failure == "mime":
        responses[catalog_path] = (responses[catalog_path][0], "application/json")
    elif failure == "bytes":
        responses["/datasets/direct-ofr/"] = (b"stale", "text/html")
    else:
        payload = json.loads(responses[catalog_path][0])
        dataset = next(
            node for node in payload["@graph"] if node.get("@type") == "dcat:Dataset"
        )
        dataset["seiche:recordCount"] = 1
        changed = (json.dumps(payload) + "\n").encode()
        responses[catalog_path] = (changed, "application/ld+json")

    def fetch(url: str, *, timeout: float):
        return responses[verifier.urllib.parse.urlparse(url).path]

    monkeypatch.setattr(verifier, "_fetch", fetch)
    with pytest.raises(verifier.VerificationError):
        verifier.verify_public_dataset(
            base_url="https://seiche.info",
            expected_root=ROOT / "frontend" / "public",
            cache_key="signed-sha",
            timeout=3.0,
        )
