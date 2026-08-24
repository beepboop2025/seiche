"""The public AI catalog never gets ahead of its signed release receipts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/release/verify_catalog_publication.py"
SPEC = importlib.util.spec_from_file_location("verify_catalog_publication", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _receipts(version: str = "0.11.1"):
    wheel_url = f"https://files.pythonhosted.org/packages/seiche-{version}.whl"
    sdist_url = f"https://files.pythonhosted.org/packages/seiche-{version}.tar.gz"
    bodies = {wheel_url: b"canonical wheel", sdist_url: b"canonical sdist"}
    pypi = {
        "info": {"name": "seiche", "version": version},
        "urls": [
            {
                "filename": f"seiche-{version}-py3-none-any.whl",
                "packagetype": "bdist_wheel",
                "yanked": False,
                "digests": {"sha256": hashlib.sha256(bodies[wheel_url]).hexdigest()},
                "size": len(bodies[wheel_url]),
                "url": wheel_url,
            },
            {
                "filename": f"seiche-{version}.tar.gz",
                "packagetype": "sdist",
                "yanked": False,
                "digests": {"sha256": hashlib.sha256(bodies[sdist_url]).hexdigest()},
                "size": len(bodies[sdist_url]),
                "url": sdist_url,
            },
        ],
    }
    health = {"version": f"{version} estuary", "faults": []}
    discovery = {
        "servers": [
            {
                "name": "io.github.beepboop2025/seiche",
                "version": version,
                "url": "https://api.seiche.info/mcp",
                "status": "active",
            }
        ]
    }
    return pypi, health, discovery, bodies


def _verify(pypi, health, discovery, bodies):
    def fetch_json(url, *, expected_host):
        assert expected_host in {"pypi.org", "api.seiche.info"}
        if expected_host == "pypi.org":
            return pypi
        if "/api/health" in url:
            return health
        return discovery

    def fetch_bytes(url, *, max_bytes, expected_host):
        assert max_bytes == gate.MAX_ARTIFACT_BYTES
        assert expected_host == "files.pythonhosted.org"
        return bodies[url]

    return gate.verify_public_receipts(
        "0.11.1", fetch_json=fetch_json, fetch_bytes=fetch_bytes
    )


def test_local_catalog_release_identity_is_internally_exact():
    version, entry = gate.verify_local_identity(ROOT)

    assert version == "0.11.1"
    assert len(entry["capabilities"]) == 11
    assert entry["prompts"] == [
        "is_now_dangerous",
        "money_market_deep_dive",
        "world_markets_briefing",
        "cross_market_cash_pressure",
    ]
    assert entry["resourceTemplates"] == []


def test_public_receipts_require_both_exact_pypi_bodies_and_live_runtime():
    receipt = _verify(*_receipts())

    assert receipt["version"] == "0.11.1"
    assert [item["filename"] for item in receipt["artifacts"]] == [
        "seiche-0.11.1-py3-none-any.whl",
        "seiche-0.11.1.tar.gz",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong_version", "wrong release version"),
        ("missing_sdist", "exactly two distributions"),
        ("bad_digest", "bytes differ"),
        ("runtime_old", "has not activated"),
        ("runtime_fault", "not strictly fault-free"),
        ("discovery_old", "discovery has not activated"),
    ],
)
def test_publication_gate_rejects_partial_or_inconsistent_receipts(mutation, message):
    pypi, health, discovery, bodies = _receipts()
    pypi, health, discovery, bodies = copy.deepcopy((pypi, health, discovery, bodies))
    if mutation == "wrong_version":
        pypi["info"]["version"] = "0.11.0"
    elif mutation == "missing_sdist":
        pypi["urls"].pop()
    elif mutation == "bad_digest":
        pypi["urls"][0]["digests"]["sha256"] = "0" * 64
    elif mutation == "runtime_old":
        health["version"] = "0.11.0 estuary"
    elif mutation == "runtime_fault":
        health["faults"] = [{"component": "collector"}]
    elif mutation == "discovery_old":
        discovery["servers"][0]["version"] = "0.11.0"

    with pytest.raises(gate.PublicationGateError, match=message):
        _verify(pypi, health, discovery, bodies)


def test_both_static_publishers_gate_before_their_first_public_write():
    fast = (ROOT / ".github/workflows/publish-static.yml").read_text()
    full = (ROOT / ".github/workflows/publish.yml").read_text()
    marker = "Gate catalog on the signed release, runtime, and PyPI receipts"

    assert fast.index("Fetch the exact declared release tag") < fast.index(marker)
    assert fast.index(marker) < fast.index("Push static files to the live site repo")
    assert full.index("Fetch the exact declared release tag") < full.index(marker)
    assert full.index(marker) < full.index("Publish to GitHub Pages (seiche-site)")
    for workflow in (fast, full):
        assert "fetch-depth: 0" in workflow
        assert "ops/release/verify_catalog_publication.py" in workflow
        assert '--expected-sha "$GITHUB_SHA"' in workflow
        assert '--signer-fingerprint "$RELEASE_SIGNING_KEY_FINGERPRINT"' in workflow


def test_signed_release_gate_rejects_malformed_external_pins_before_git_use():
    with pytest.raises(gate.PublicationGateError, match="SHA is malformed"):
        gate.verify_signed_release(
            ROOT,
            version="0.11.1",
            expected_sha="main",
            signer_fingerprint="SHA256:" + "A" * 43,
        )
    with pytest.raises(gate.PublicationGateError, match="fingerprint is malformed"):
        gate.verify_signed_release(
            ROOT,
            version="0.11.1",
            expected_sha="a" * 40,
            signer_fingerprint="untrusted",
        )
