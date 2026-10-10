"""Unit coverage for the dependency-free ARD coverage monitor."""

import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
from urllib.error import HTTPError

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "ard_coverage.py"
SPEC = importlib.util.spec_from_file_location("ard_coverage", SCRIPT)
ard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = ard
SPEC.loader.exec_module(ard)


def _seiche_catalog():
    return json.loads((
        ROOT / "frontend" / "public" / ".well-known" / "ai-catalog.json"
    ).read_text())


def test_committed_seiche_catalog_passes_the_monitor_contract():
    product = next(product for product in ard.PRODUCTS
                   if product.slug == "seiche")
    assert ard.validate_catalog(_seiche_catalog(), product) == []


def test_only_seiche_pins_a_version_owned_by_this_repository():
    versions = {product.slug: product.mcp_version for product in ard.PRODUCTS}
    assert versions["seiche"] == json.loads((ROOT / "server.json").read_text())[
        "version"
    ]
    assert versions["liquilens"] is None
    assert versions["undertow"] is None
    media_types = {product.slug: product.mcp_media_type for product in ard.PRODUCTS}
    assert media_types["seiche"] == "application/json"
    assert media_types["liquilens"] == "application/mcp-server-card+json"
    assert media_types["undertow"] == "application/mcp-server-card+json"


def test_sibling_catalog_accepts_a_new_release_but_requires_internal_agreement():
    sibling = next(product for product in ard.PRODUCTS
                   if product.slug == "liquilens")
    catalog = _seiche_catalog()
    mcp = next(
        entry
        for entry in catalog["entries"]
        if entry["identifier"] == "urn:air:seiche.info:mcp:funding-stress"
    )
    mcp["identifier"] = sibling.mcp_identifier
    mcp["type"] = sibling.mcp_media_type
    mcp["version"] = "9.9.9"
    mcp["data"]["name"] = sibling.mcp_name
    mcp["data"]["version"] = "9.9.9"
    mcp["data"]["remotes"] = [
        {"type": "streamable-http", "url": sibling.mcp_endpoint}
    ]
    mcp["capabilities"] = [
        f"tool_{index}" for index in range(30)
    ] + [sibling.first_tool]
    openapi = next(entry for entry in catalog["entries"]
                   if entry["type"] == "application/vnd.oai.openapi+json")
    openapi["identifier"] = sibling.openapi_identifier
    openapi["url"] = sibling.openapi_url

    assert ard.validate_catalog(catalog, sibling) == []
    mcp["data"]["version"] = "9.9.8"
    assert "embedded MCP version does not match the catalog version" in (
        ard.validate_catalog(catalog, sibling)
    )


def test_sibling_inventory_must_match_names_even_when_counts_match(monkeypatch):
    product = next(product for product in ard.PRODUCTS if product.slug == "liquilens")
    names = [product.first_tool, "new_release_tool"]
    monkeypatch.setattr(ard, "_request_json", lambda *args, **kwargs: (
        {"result": {"tools": [{"name": name} for name in names]}}, {}))
    assert ard.probe_mcp(product, 1, names)["ok"]
    result = ard.probe_mcp(product, 1, [product.first_tool, "missing_tool"])
    assert not result["ok"]
    assert "live tool names do not match catalog capabilities" in result["errors"]


@pytest.mark.parametrize("names", [[], ["latest_article", None],
                                    ["latest_article", "latest_article"]])
def test_malformed_live_inventory_fails(monkeypatch, names):
    product = ard.PRODUCTS[0]
    monkeypatch.setattr(ard, "_request_json", lambda *args, **kwargs: (
        {"result": {"tools": [{"name": name} for name in names]}}, {}))
    assert not ard.probe_mcp(product, 1)["ok"]


def test_registry_checks_exact_active_latest_record(monkeypatch):
    product = ard.PRODUCTS[0]
    payload = {
        "server": {"name": product.mcp_name, "version": "9.9.9"},
        "_meta": {"io.modelcontextprotocol.registry/official": {
            "status": "active", "isLatest": True}},
    }
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return payload, {}

    monkeypatch.setattr(ard, "_request_json", request)
    assert ard.probe_registry(product, 1, "9.9.9")["ok"]
    assert calls[0][0].endswith("io.github.beepboop2025%2Fliquilens/versions/latest")
    assert calls[0][1]["attempts"] == 3
    payload["server"]["name"] = "unrelated/server"
    assert not ard.probe_registry(product, 1, "9.9.9")["ok"]
    payload["server"]["name"] = product.mcp_name
    payload["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deleted"
    assert not ard.probe_registry(product, 1, "9.9.9")["ok"]


def test_registry_transport_retries_timeout_without_masking_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(ard.time, "sleep", lambda seconds: None)

    def timeout(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("registry unavailable")

    monkeypatch.setattr(ard, "urlopen", timeout)
    with pytest.raises(ard.ProbeError, match="registry unavailable"):
        ard._request_json("https://example.com", timeout=1, attempts=3)
    assert len(calls) == 3


def test_registry_transport_does_not_retry_not_found(monkeypatch):
    calls = []

    def missing(*args, **kwargs):
        calls.append(1)
        raise HTTPError("https://example.com", 404, "missing", {}, BytesIO(b"missing"))

    monkeypatch.setattr(ard, "urlopen", missing)
    with pytest.raises(ard.ProbeError, match="HTTP 404"):
        ard._request_json("https://example.com", timeout=1, attempts=3)
    assert len(calls) == 1


def test_value_or_reference_and_query_bounds_are_enforced():
    product = next(product for product in ard.PRODUCTS
                   if product.slug == "seiche")
    catalog = _seiche_catalog()
    catalog["entries"][0]["url"] = "https://example.com/duplicate.json"
    catalog["entries"][0]["representativeQueries"] = ["only one"]
    errors = ard.validate_catalog(catalog, product)
    assert any("exactly one of url or data" in error for error in errors)
    assert any("2-5 strings" in error for error in errors)


def test_json_and_sse_responses_share_one_decoder():
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    encoded = json.dumps(payload).encode()
    assert ard._decode_json_or_sse(encoded) == payload
    assert ard._decode_json_or_sse(b"event: message\ndata: " + encoded) == payload


def test_registry_result_matches_any_canonical_product_signal():
    product = next(product for product in ard.PRODUCTS
                   if product.slug == "seiche")
    results = [
        {"identifier": "urn:air:example.com:mcp:unrelated"},
        {"data": {"name": product.mcp_name}},
    ]
    assert ard._matching_ard_result(results, product) == (2, "mcpName")
    assert ard._matching_ard_result(
        [{"url": product.mcp_endpoint}], product) == (1, "endpoint")
    assert ard._matching_ard_result([], product) == (None, None)


def test_markdown_keeps_indexing_separate_from_hard_health():
    report = {
        "generatedAt": "2026-08-06T00:00:00+00:00",
        "localOnly": False,
        "strictIndexing": False,
        "summary": {"hardChecksPassed": True},
        "products": {},
    }
    for product in ard.PRODUCTS:
        report["products"][product.slug] = {
            "expected": {},
            "catalog": {"ok": True, "errors": []},
            "mcpRegistry": {
                "ok": True, "version": product.mcp_version, "errors": []},
            "mcpInventory": {
                "ok": True, "toolCount": product.public_tool_count,
                "errors": []},
            "openapi": {"ok": True, "pathCount": 10, "errors": []},
            "ardSearch": {
                name: {
                    "ok": True, "indexed": False, "rank": None,
                    "errors": [],
                }
                for name in ard.ARD_REGISTRIES
            },
        }
    rendered = ard.render_markdown(report)
    assert rendered.count("not indexed") == (
        len(ard.PRODUCTS) * len(ard.ARD_REGISTRIES))
    assert "GitHub" in rendered
    assert "Ora" in rendered
    assert "HF" in rendered
    assert "coverage gaps, not hard failures" in rendered
