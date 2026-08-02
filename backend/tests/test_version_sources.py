"""One version, four places that must agree.

The MCP handshake, the registry listing, the installable package and the
build metadata each carry the version separately, and nothing until now
compared them. A release cut while they disagree fails in the worst order:
twine uploads a version PyPI already has, rejects it, and the registry job
(needs: pypi) never runs, so the registry keeps advertising a server whose
installable package is a version behind, missing whatever the bump added.
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


def test_all_version_sources_agree():
    server = _server_json()
    versions = {
        "assemble.VERSION": assemble.VERSION,
        "server.json version": server["version"],
        "server.json packages[0].version": server["packages"][0]["version"],
        "backend/pyproject.toml version": _pyproject()["project"]["version"],
    }
    assert len(set(versions.values())) == 1, versions


def test_version_is_bare_semver():
    """VERSION is the machine-facing contract: the MCP registry and the
    handshake both take it verbatim, so the codename belongs in RELEASE."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", assemble.VERSION), assemble.VERSION
    assert assemble.RELEASE and assemble.RELEASE not in assemble.VERSION
    assert assemble.VERSION_LABEL == f"{assemble.VERSION} {assemble.RELEASE}"
