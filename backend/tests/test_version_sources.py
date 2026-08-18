"""Explicit version contracts for Seiche's hosted and packaged surfaces.

The MCP handshake, hosted registry listing, and build metadata describe the
deployed server and must agree. The optional PyPI transport advertises the same
0.10.0 estuary version and nine-tool public surface so a human release publishes
the right artifact. PyPI itself still carries 0.9.1 until that release is cut.
"""

import json
import re
import tomllib
from pathlib import Path

from seiche import assemble

REPO = Path(__file__).resolve().parents[2]


def _server_json() -> dict:
    return json.loads((REPO / "server.json").read_text())


def _pyproject() -> dict:
    return tomllib.loads((REPO / "backend" / "pyproject.toml").read_text())


def test_hosted_version_sources_agree():
    server = _server_json()
    versions = {
        "assemble.VERSION": assemble.VERSION,
        "server.json version": server["version"],
        "backend/pyproject.toml version": _pyproject()["project"]["version"],
    }
    assert len(set(versions.values())) == 1, versions


def test_registry_stdio_package_matches_hosted_surface():
    """The registry card pins the running 0.10.0 / nine-tool public surface."""
    server = _server_json()
    package = server["packages"][0]
    description = package["environmentVariables"][0]["description"]
    hosted = server["version"]

    assert package["registryType"] == "pypi"
    assert package["identifier"] == "seiche"
    assert package["version"] == hosted == "0.10.0"
    assert package["transport"] == {"type": "stdio"}
    assert "nine free public tools" in description
    assert "latest_article" in description
    assert "0.10.0" in description


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
