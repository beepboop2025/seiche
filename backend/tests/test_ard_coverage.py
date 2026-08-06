"""Unit coverage for the dependency-free ARD coverage monitor."""

import importlib.util
import json
from pathlib import Path
import sys


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
