"""Explicit version contracts for Seiche's hosted and packaged surfaces.

The MCP handshake, hosted registry listing, and build metadata describe the
deployed server and must agree. The optional PyPI transport advertises the same
0.10.0 estuary version and ten-tool public surface so a human release publishes
the right artifact. PyPI itself still carries 0.9.1 until that release is cut.
"""

import json
import re
import tomllib
from pathlib import Path

from seiche import assemble
from seiche.markets.registry import default_registry

REPO = Path(__file__).resolve().parents[2]


def _server_json() -> dict:
    return json.loads((REPO / "server.json").read_text())


def _pyproject() -> dict:
    return tomllib.loads((REPO / "backend" / "pyproject.toml").read_text())


def _product_card() -> dict:
    return json.loads(
        (REPO / "frontend" / "public" / "product-card.json").read_text()
    )


def test_hosted_version_sources_agree():
    server = _server_json()
    versions = {
        "assemble.VERSION": assemble.VERSION,
        "server.json version": server["version"],
        "backend/pyproject.toml version": _pyproject()["project"]["version"],
    }
    assert len(set(versions.values())) == 1, versions


def test_registry_stdio_package_matches_hosted_surface():
    """The registry card pins the running 0.10.0 / ten-tool public surface."""
    server = _server_json()
    package = server["packages"][0]
    description = package["environmentVariables"][0]["description"]
    hosted = server["version"]

    assert package["registryType"] == "pypi"
    assert package["identifier"] == "seiche"
    assert package["version"] == hosted == "0.10.0"
    assert package["transport"] == {"type": "stdio"}
    assert "ten free public tools" in description
    assert "latest_article" in description
    assert "money_market_context" in description
    assert "0.10.0" in description


def test_money_market_discovery_separates_catalog_from_dated_evidence():
    card = _product_card()
    market_pack = card["market_pack_v2"]
    registered = {pack.market_id for pack in default_registry().list()}

    assert market_pack["registered_market_packs"] == 11
    assert set(market_pack["registered_market_ids"]) == registered
    assert "not a claim" in market_pack["scope"]
    assert set(market_pack["availability_states"]) == {
        "AVAILABLE",
        "DERIVED_CONTEXT",
        "POLICY_ONLY",
        "DECLARED_UNAVAILABLE",
    }

    dated = card["evidence"]["snapshot_2026_08_09"]
    assert dated["captured_at"] == "2026-08-09T14:36:00Z"
    assert dated["market_packs"] == 10
    assert market_pack["snapshot_captured_at"] == dated["captured_at"]

    access = card["access"]
    assert access["usd_money_markets"].endswith("/api/money-markets")
    assert access["global_money_markets"].endswith("/api/v2/money-markets")
    assert access["public_money_market_mcp_tool"] == "money_market_context"
    assert "Ten public MCP tools" in access["authentication"]


def test_version_is_bare_semver():
    """VERSION is the machine-facing contract: the MCP registry and the
    handshake both take it verbatim, so the codename belongs in RELEASE."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", assemble.VERSION), assemble.VERSION
    assert assemble.RELEASE and assemble.RELEASE not in assemble.VERSION
    assert assemble.VERSION_LABEL == f"{assemble.VERSION} {assemble.RELEASE}"


def test_registry_description_fits_official_limit():
    """The official MCP Registry rejects descriptions over 100 characters."""
    description = _server_json()["description"]
    assert description
    assert len(description) <= 100, len(description)
