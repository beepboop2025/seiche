"""The public AI catalog never gets ahead of its signed release receipts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/release/verify_catalog_publication.py"
SPEC = importlib.util.spec_from_file_location("verify_catalog_publication", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _receipts(version: str = "0.12.1"):
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
        "0.12.1", fetch_json=fetch_json, fetch_bytes=fetch_bytes
    )


def _market_entry():
    catalog = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text(encoding="utf-8"))
    return next(
        entry
        for entry in catalog["entries"]
        if entry["identifier"] == gate.MARKET_CORPUS_ENTRY
    )


def _market_receipts():
    signed = gate._market_corpus_publication_receipt(_market_entry())
    index_sha256 = signed["indexSha256"]
    index_artifact_id = signed["indexArtifactId"]
    health = {
        "schema_version": "1.0.0",
        "service": "liquilens-market-corpus",
        "status": "ok",
        "release_id": signed["releaseId"],
        "checks": {
            "deep": {
                "ok": True,
                "bis_flows": signed["bisFlows"],
                "datasets": signed["engineDatasets"],
                "bis_inventory_sha256": signed["inventorySha256"],
                "bis_all_flow_receipt": {
                    "sha256": "b" * 64,
                    "status": "complete",
                    "expected_count": signed["bisBulkFlat"],
                    "materialized_count": signed["bisBulkFlat"],
                    "error_count": 0,
                    "aggregate_row_count": signed["bisAggregateRows"],
                    "sampled_shard_count": signed["bisBulkFlat"],
                },
                "engine_index": {
                    "artifact_id": index_artifact_id,
                    "index_sha256": index_sha256,
                    "attempt_count": signed["engineAttempts"],
                    "object_count": signed["engineVerifiedObjects"],
                    "recovered_object_count": signed["engineRecoveredObjects"],
                    "unresolved_object_count": 0,
                },
            }
        },
    }
    catalog = {
        "schema_version": "1.0.0",
        "service": "liquilens-market-corpus",
        "release_id": health["release_id"],
        "index_sha256": index_sha256,
        "index_artifact_id": index_artifact_id,
        "corpora": {
            "liquilens_engine": {
                "datasets": signed["engineDatasets"],
                "verified_objects": signed["engineVerifiedObjects"],
                "attempts": signed["engineAttempts"],
                "successful_attempts": signed["engineVerifiedObjects"],
                "failed_attempts": signed["engineAttempts"]
                - signed["engineVerifiedObjects"],
                "recovered_objects": signed["engineRecoveredObjects"],
                "unresolved_objects": 0,
            },
            "bis": {
                "flows": signed["bisFlows"],
                "bulk_flat": signed["bisBulkFlat"],
                "api_only": signed["bisApiOnly"],
                "registry_only": signed["bisRegistryOnly"],
                "inventory_sha256": signed["inventorySha256"],
            },
            "seiche": {"status": "ok", "market_count": 9, "source_count": 20},
        },
    }
    discovery = {
        "servers": [
            {
                "name": gate.MARKET_CORPUS_NAME,
                "version": "1.0.0",
                "transport": "streamable-http",
                "url": gate.MARKET_CORPUS_MCP_URL,
                "availability": "declared_endpoint_verify_with_corpus_health",
                "health": gate.MARKET_CORPUS_HEALTH_URL,
            }
        ]
    }
    tools = {
        "jsonrpc": "2.0",
        "id": "market-corpus-publication-proof",
        "result": {"tools": [{"name": name} for name in gate.MARKET_CORPUS_TOOLS]},
    }
    return health, catalog, discovery, tools


def _verify_market(health, catalog, discovery, tools, *, entry=None):
    def fetch_json(url, *, expected_host):
        assert expected_host == "api.seiche.info"
        if url == gate.MARKET_CORPUS_HEALTH_URL:
            return health
        if url == gate.MARKET_CORPUS_DISCOVERY_URL:
            return discovery
        return catalog

    def post_json(url, payload, *, expected_host):
        assert url == gate.MARKET_CORPUS_MCP_URL
        assert expected_host == "api.seiche.info"
        assert payload["method"] == "tools/list"
        return tools

    return gate.verify_market_corpus_receipts(
        entry or _market_entry(), fetch_json=fetch_json, post_json=post_json
    )


def test_local_catalog_release_identity_is_internally_exact():
    version, entry = gate.verify_local_identity(ROOT)

    assert version == "0.12.1"
    assert len(entry["capabilities"]) == 12
    assert "trade_safety_risk_context" in entry["capabilities"]
    assert entry["prompts"] == [
        "is_now_dangerous",
        "money_market_deep_dive",
        "world_markets_briefing",
        "cross_market_cash_pressure",
    ]
    assert entry["resourceTemplates"] == []


def test_release_identity_allows_an_independent_catalog_server(tmp_path):
    catalog_path = tmp_path / gate.AI_CATALOG_PATH
    catalog_path.parent.mkdir(parents=True)
    current = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text())
    tagged = copy.deepcopy(current)
    tagged["entries"] = [
        entry
        for entry in tagged["entries"]
        if entry.get("identifier") != "urn:air:seiche.info:mcp:market-corpus"
    ]
    catalog_path.write_text(json.dumps(current))

    gate._verify_catalog_release_entries(tmp_path, tagged)

    assert gate.AI_CATALOG_PATH not in gate.RELEASE_IDENTITY_PATHS


def test_core_tag_does_not_freeze_the_independently_signed_corpus_entry(tmp_path):
    catalog_path = tmp_path / gate.AI_CATALOG_PATH
    catalog_path.parent.mkdir(parents=True)
    current = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text(encoding="utf-8"))
    tagged = copy.deepcopy(current)
    tagged_entry = next(
        entry
        for entry in tagged["entries"]
        if entry["identifier"] == gate.MARKET_CORPUS_ENTRY
    )
    tagged_entry["version"] = "0.9.0"
    tagged_entry["data"]["version"] = "0.9.0"
    catalog_path.write_text(json.dumps(current), encoding="utf-8")

    gate._verify_catalog_release_entries(tmp_path, tagged)


def test_independent_tag_binds_market_corpus_entry_placement():
    tagged = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text(encoding="utf-8"))
    current = copy.deepcopy(tagged)
    corpus = next(
        entry
        for entry in current["entries"]
        if entry["identifier"] == gate.MARKET_CORPUS_ENTRY
    )
    current["entries"] = [corpus] + [
        entry
        for entry in current["entries"]
        if entry["identifier"] != gate.MARKET_CORPUS_ENTRY
    ]

    with pytest.raises(gate.PublicationGateError, match="immediately after canonical"):
        gate._verify_market_corpus_tagged_identity(current, tagged)


def test_independent_tag_owns_market_corpus_version_lifecycle():
    current = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text(encoding="utf-8"))
    independent_tag = copy.deepcopy(current)
    for catalog in (current, independent_tag):
        entry = next(
            row
            for row in catalog["entries"]
            if row["identifier"] == gate.MARKET_CORPUS_ENTRY
        )
        entry["version"] = "1.0.1"
        entry["data"]["version"] = "1.0.1"

    verified = gate._verify_market_corpus_tagged_identity(current, independent_tag)

    assert verified["version"] == "1.0.1"


def test_version_tag_allows_only_the_independently_signed_receipt_field():
    current = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text(encoding="utf-8"))
    version_tag = copy.deepcopy(current)
    tagged_entry = next(
        entry
        for entry in version_tag["entries"]
        if entry["identifier"] == gate.MARKET_CORPUS_ENTRY
    )
    tagged_entry["metadata"].pop("publicationReceipt")

    verified = gate._verify_market_corpus_version_tagged_identity(current, version_tag)
    assert gate._market_corpus_publication_receipt(verified)["bisBulkFlat"] == 27

    tagged_entry["data"]["remotes"][0]["url"] = "https://attacker.example/mcp"
    with pytest.raises(gate.PublicationGateError, match="contract is inconsistent"):
        gate._verify_market_corpus_version_tagged_identity(current, version_tag)


def test_signed_publication_receipt_has_exact_release_generation():
    receipt = gate._market_corpus_publication_receipt(_market_entry())

    assert receipt == {
        "schemaVersion": "1.0.0",
        "tag": "market-corpus-receipt-corpus-7cb1695c6affa707-r5",
        "releaseId": "corpus-7cb1695c6affa707",
        "indexSha256": (
            "29bcd84daf10acb94a74779facebe3a0484b0f9dc0b16f7b5be5727e2e956b36"
        ),
        "indexArtifactId": (
            "liquilens-engine-public-index-v1:corpus-7cb1695c6affa707:"
            "29bcd84daf10acb94a74779facebe3a0484b0f9dc0b16f7b5be5727e2e956b36"
        ),
        "inventorySha256": (
            "05c5b08074c65299e59e09285a85e8aaffe895e64a8d840a3a81bbdca4a83f64"
        ),
        "bisFlows": 29,
        "bisBulkFlat": 27,
        "bisApiOnly": 1,
        "bisRegistryOnly": 1,
        "bisAggregateRows": 76_344_667,
        "engineDatasets": 1122,
        "engineVerifiedObjects": 1110,
        "engineAttempts": 1118,
        "engineRecoveredObjects": 8,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tag", "market-corpus-receipt-corpus-0000000000000000"),
        ("bisBulkFlat", 1),
        ("bisApiOnly", 0),
        ("engineAttempts", 999),
        ("indexArtifactId", "unbound-artifact"),
    ],
)
def test_signed_publication_receipt_rejects_self_consistent_looking_drift(field, value):
    entry = copy.deepcopy(_market_entry())
    receipt = gate._market_corpus_publication_receipt(entry)
    receipt[field] = value
    entry["metadata"]["publicationReceipt"] = json.dumps(
        receipt, sort_keys=True, separators=(",", ":")
    )

    with pytest.raises(gate.PublicationGateError, match="receipt is inconsistent"):
        gate._market_corpus_publication_receipt(entry)


def test_publication_receipt_tag_must_target_exact_workflow_head(monkeypatch):
    current = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text(encoding="utf-8"))
    version_tag = copy.deepcopy(current)
    version_entry = next(
        entry
        for entry in version_tag["entries"]
        if entry["identifier"] == gate.MARKET_CORPUS_ENTRY
    )
    version_entry["metadata"].pop("publicationReceipt")
    expected_sha = "e" * 40

    monkeypatch.setattr(gate, "_signing_git_config", lambda *_args: "trusted=true")
    monkeypatch.setattr(
        gate,
        "_read_tagged_json",
        lambda _root, tag, _path: (
            version_tag if tag == "market-corpus-v1.0.0" else current
        ),
    )

    def verify_tag(_root, *, tag, head, git_config):
        assert head == expected_sha
        assert git_config == "trusted=true"
        if tag == "market-corpus-v1.0.0":
            return "a" * 40
        return expected_sha

    monkeypatch.setattr(gate, "_verify_annotated_signed_tag", verify_tag)

    tag, entry = gate.verify_market_corpus_release(
        ROOT,
        expected_sha=expected_sha,
        signer_fingerprint="SHA256:" + "A" * 43,
    )
    assert tag == "market-corpus-receipt-corpus-7cb1695c6affa707-r5"
    assert gate._market_corpus_publication_receipt(entry)["releaseId"] == (
        "corpus-7cb1695c6affa707"
    )

    def stale_receipt_tag(_root, *, tag, head, git_config):
        del head, git_config
        return "a" * 40 if tag == "market-corpus-v1.0.0" else "d" * 40

    monkeypatch.setattr(gate, "_verify_annotated_signed_tag", stale_receipt_tag)
    with pytest.raises(gate.PublicationGateError, match="does not target"):
        gate.verify_market_corpus_release(
            ROOT,
            expected_sha=expected_sha,
            signer_fingerprint="SHA256:" + "A" * 43,
        )


def test_release_identity_rejects_an_unknown_unsigned_catalog_entry(tmp_path):
    catalog_path = tmp_path / gate.AI_CATALOG_PATH
    catalog_path.parent.mkdir(parents=True)
    current = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text(encoding="utf-8"))
    tagged = copy.deepcopy(current)
    current["entries"].append({"identifier": "urn:untrusted:server"})
    catalog_path.write_text(json.dumps(current), encoding="utf-8")

    with pytest.raises(gate.PublicationGateError, match="signed AI catalog entries"):
        gate._verify_catalog_release_entries(tmp_path, tagged)


@pytest.mark.parametrize("entry_kind", ["canonical", "other-signed"])
def test_release_identity_rejects_a_changed_signed_catalog_entry(tmp_path, entry_kind):
    catalog_path = tmp_path / gate.AI_CATALOG_PATH
    catalog_path.parent.mkdir(parents=True)
    current = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text())
    tagged = copy.deepcopy(current)
    signed_entries = [
        entry
        for entry in tagged["entries"]
        if entry.get("identifier") != "urn:air:seiche.info:mcp:market-corpus"
    ]
    target = (
        next(entry for entry in signed_entries if entry["identifier"] == gate.MCP_ENTRY)
        if entry_kind == "canonical"
        else next(
            entry for entry in signed_entries if entry["identifier"] != gate.MCP_ENTRY
        )
    )
    target["version"] = "0.11.0"
    catalog_path.write_text(json.dumps(current))

    with pytest.raises(gate.PublicationGateError, match="signed AI catalog entries"):
        gate._verify_catalog_release_entries(tmp_path, tagged)


def test_release_identity_comparison_is_json_type_strict(tmp_path):
    catalog_path = tmp_path / gate.AI_CATALOG_PATH
    catalog_path.parent.mkdir(parents=True)
    tagged = json.loads((ROOT / gate.AI_CATALOG_PATH).read_text(encoding="utf-8"))
    current = copy.deepcopy(tagged)
    tagged_entry = next(
        entry for entry in tagged["entries"] if entry["identifier"] == gate.MCP_ENTRY
    )
    current_entry = next(
        entry for entry in current["entries"] if entry["identifier"] == gate.MCP_ENTRY
    )
    tagged_entry["metadata"]["publicToolCount"] = 0
    current_entry["metadata"]["publicToolCount"] = False
    catalog_path.write_text(json.dumps(current), encoding="utf-8")

    with pytest.raises(gate.PublicationGateError, match="signed AI catalog entries"):
        gate._verify_catalog_release_entries(tmp_path, tagged)


def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers():
    with pytest.raises(gate.PublicationGateError, match="strict UTF-8 JSON"):
        gate._load_json_bytes(b'{"entries":[],"entries":[]}', label="duplicate")
    with pytest.raises(gate.PublicationGateError, match="strict UTF-8 JSON"):
        gate._load_json_bytes(b'{"value":NaN}', label="nonfinite")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("attacker_remote", "contract is inconsistent"),
        ("spoofed_name", "contract is inconsistent"),
        ("identifier_only", "metadata is malformed"),
    ],
)
def test_market_corpus_entry_rejects_untrusted_identity(mutation, message):
    entry = copy.deepcopy(_market_entry())
    if mutation == "attacker_remote":
        entry["data"]["remotes"][0]["url"] = "https://attacker.example/mcp"
    elif mutation == "spoofed_name":
        entry["data"]["name"] = gate.MCP_NAME
    else:
        entry = {"identifier": gate.MARKET_CORPUS_ENTRY, "version": "1.0.0"}

    with pytest.raises(gate.PublicationGateError, match=message):
        gate._validate_market_corpus_entry(entry)


def test_market_corpus_receipts_bind_deep_health_catalog_and_tools():
    receipt = _verify_market(*_market_receipts())

    assert receipt["releaseId"] == "corpus-7cb1695c6affa707"
    assert receipt["bisRows"] == 76_344_667
    assert receipt["tools"] == list(gate.MARKET_CORPUS_TOOLS)


def test_market_corpus_real_schema_separates_dataset_and_verified_counts():
    health, catalog, discovery, tools = _market_receipts()

    assert health["checks"]["deep"]["datasets"] == 1122
    assert health["checks"]["deep"]["engine_index"]["object_count"] == 1110
    assert catalog["corpora"]["liquilens_engine"]["datasets"] == 1122
    assert catalog["corpora"]["liquilens_engine"]["verified_objects"] == 1110
    _verify_market(health, catalog, discovery, tools)

    for datasets, object_count in ((1110, 1122), (1122, 1122), (1110, 1110)):
        drifted = copy.deepcopy(health)
        drifted["checks"]["deep"]["datasets"] = datasets
        drifted["checks"]["deep"]["engine_index"]["object_count"] = object_count
        with pytest.raises(gate.PublicationGateError, match="not deeply healthy"):
            _verify_market(drifted, catalog, discovery, tools)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("materializing", "not deeply healthy"),
        ("malformed_health", "not deeply healthy"),
        ("incomplete_receipt", "not deeply healthy"),
        ("self_reduced_denominator", "not deeply healthy"),
        ("non_integer_denominator", "not deeply healthy"),
        ("coordinated_engine_index", "not deeply healthy"),
        ("coordinated_engine_artifact", "not deeply healthy"),
        ("coordinated_engine_attempts", "not deeply healthy"),
        ("coordinated_engine_recovered", "not deeply healthy"),
        ("coordinated_flow_total", "not deeply healthy"),
        ("aggregate_rows", "not deeply healthy"),
        ("bis_taxonomy", "catalog differs"),
        ("catalog_release", "catalog differs"),
        ("engine_index", "catalog differs"),
        ("engine_artifact", "catalog differs"),
        ("discovery_version", "public discovery differs"),
        ("discovery_duplicate", "public discovery differs"),
        ("tool_drift", "tools differ"),
    ],
)
def test_market_corpus_receipts_reject_partial_or_drifted_runtime(mutation, message):
    health, catalog, discovery, tools = copy.deepcopy(_market_receipts())
    if mutation == "materializing":
        health["status"] = "materializing"
        health["checks"]["deep"] = {"ok": False, "error": "bis_materializing"}
    elif mutation == "malformed_health":
        health = []
    elif mutation == "incomplete_receipt":
        health["checks"]["deep"]["bis_all_flow_receipt"]["materialized_count"] = 26
    elif mutation == "self_reduced_denominator":
        receipt = health["checks"]["deep"]["bis_all_flow_receipt"]
        receipt["expected_count"] = 1
        receipt["materialized_count"] = 1
        receipt["sampled_shard_count"] = 1
        catalog["corpora"]["bis"]["bulk_flat"] = 1
    elif mutation == "non_integer_denominator":
        health["checks"]["deep"]["bis_all_flow_receipt"]["expected_count"] = 27.0
    elif mutation == "coordinated_engine_index":
        replacement_sha = "d" * 64
        replacement_artifact = (
            f"liquilens-engine-public-index-v1:{health['release_id']}:{replacement_sha}"
        )
        health["checks"]["deep"]["engine_index"]["index_sha256"] = replacement_sha
        health["checks"]["deep"]["engine_index"]["artifact_id"] = replacement_artifact
        catalog["index_sha256"] = replacement_sha
        catalog["index_artifact_id"] = replacement_artifact
    elif mutation == "coordinated_engine_artifact":
        health["checks"]["deep"]["engine_index"]["artifact_id"] = "unbound-artifact"
        catalog["index_artifact_id"] = "unbound-artifact"
    elif mutation == "coordinated_engine_attempts":
        health["checks"]["deep"]["engine_index"]["attempt_count"] = 999
        catalog["corpora"]["liquilens_engine"]["attempts"] = 999
    elif mutation == "coordinated_engine_recovered":
        health["checks"]["deep"]["engine_index"]["recovered_object_count"] = 0
        catalog["corpora"]["liquilens_engine"]["recovered_objects"] = 0
    elif mutation == "coordinated_flow_total":
        health["checks"]["deep"]["bis_flows"] = 1
        catalog["corpora"]["bis"]["flows"] = 1
    elif mutation == "aggregate_rows":
        health["checks"]["deep"]["bis_all_flow_receipt"]["aggregate_row_count"] -= 1
    elif mutation == "bis_taxonomy":
        catalog["corpora"]["bis"]["api_only"] = 0
    elif mutation == "catalog_release":
        catalog["release_id"] = "corpus-fedcba9876543210"
    elif mutation == "engine_index":
        catalog["index_sha256"] = "d" * 64
    elif mutation == "engine_artifact":
        catalog["index_artifact_id"] += "-drift"
    elif mutation == "discovery_version":
        discovery["servers"][0]["version"] = "9.9.9"
    elif mutation == "discovery_duplicate":
        discovery["servers"].append(copy.deepcopy(discovery["servers"][0]))
    else:
        tools["result"]["tools"].pop()

    with pytest.raises(gate.PublicationGateError, match=message):
        _verify_market(health, catalog, discovery, tools)


def test_market_corpus_signed_version_must_match_live_discovery():
    health, catalog, discovery, tools = copy.deepcopy(_market_receipts())
    entry = copy.deepcopy(_market_entry())
    entry["version"] = "9.9.9"
    entry["data"]["version"] = "9.9.9"

    with pytest.raises(gate.PublicationGateError, match="public discovery differs"):
        _verify_market(health, catalog, discovery, tools, entry=entry)


def test_published_catalog_must_be_the_exact_gated_source(tmp_path):
    source = tmp_path / gate.AI_CATALOG_PATH
    published = tmp_path / "frontend/dist/.well-known/ai-catalog.json"
    source.parent.mkdir(parents=True)
    published.parent.mkdir(parents=True)
    body = (ROOT / gate.AI_CATALOG_PATH).read_bytes()
    source.write_bytes(body)
    published.write_bytes(body)

    gate.verify_published_catalog(tmp_path, published)
    published.write_bytes(body + b"\n")

    with pytest.raises(gate.PublicationGateError, match="bytes differ"):
        gate.verify_published_catalog(tmp_path, published)


def test_release_identity_worktree_must_match_head(tmp_path):
    catalog = tmp_path / gate.AI_CATALOG_PATH
    catalog.parent.mkdir(parents=True)
    catalog.write_text('{"entries":[]}', encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "add", gate.AI_CATALOG_PATH], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    gate._verify_worktree_identity(tmp_path, head)
    catalog.write_text('{"entries":[{"identifier":"drift"}]}', encoding="utf-8")

    with pytest.raises(gate.PublicationGateError, match="working tree differs"):
        gate._verify_worktree_identity(tmp_path, head)


@pytest.mark.parametrize("unsafe_readme", ["symlink", "oversized"])
def test_local_identity_rejects_an_unsafe_package_readme(tmp_path, unsafe_readme):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend/public/.well-known").mkdir(parents=True)
    for relative in (
        "server.json",
        "backend/pyproject.toml",
        "frontend/public/.well-known/ai-catalog.json",
    ):
        shutil.copy2(ROOT / relative, tmp_path / relative)
    readme = tmp_path / "backend/README.md"
    if unsafe_readme == "symlink":
        readme.symlink_to(ROOT / "backend/README.md")
    else:
        readme.write_bytes(b"x" * (gate.MAX_JSON_BYTES + 1))

    with pytest.raises(gate.PublicationGateError, match="README.md"):
        gate.verify_local_identity(tmp_path)


def test_public_receipts_require_both_exact_pypi_bodies_and_live_runtime():
    receipt = _verify(*_receipts())

    assert receipt["version"] == "0.12.1"
    assert [item["filename"] for item in receipt["artifacts"]] == [
        "seiche-0.12.1-py3-none-any.whl",
        "seiche-0.12.1.tar.gz",
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
        assert (
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
        )
        assert 'python-version: "3.12.12"' in workflow
        assert "market-corpus-v${corpus_version}" in workflow
        assert 'json.loads(matches[0]["metadata"]["publicationReceipt"])' in workflow
        assert "^market-corpus-receipt-corpus-[0-9a-f]{16}-r[1-9][0-9]*$" in workflow
        assert (
            "+refs/tags/${corpus_receipt_tag}:refs/tags/${corpus_receipt_tag}"
            in workflow
        )
        assert "ops/release/verify_catalog_publication.py" in workflow
        assert (
            'git show "${GITHUB_SHA}:ops/release/verify_catalog_publication.py"'
            in workflow
        )
        assert 'git hash-object "$verifier"' in workflow
        assert 'python -I -S "$verifier"' in workflow
        assert "python -I -S ops/release/verify_catalog_publication.py" not in workflow
        assert '--expected-sha "$GITHUB_SHA"' in workflow
        assert '--signer-fingerprint "$RELEASE_SIGNING_KEY_FINGERPRINT"' in workflow
    assert "--published-catalog frontend/dist/.well-known/ai-catalog.json" in full
    assert "--published-catalog" not in fast


def test_full_publish_refuses_stale_mirror_and_canonical_writes():
    workflow = (ROOT / ".github/workflows/publish.yml").read_text()
    fetch = (
        "git fetch --no-tags origin \\\n"
        '            "+refs/heads/main:refs/remotes/origin/main"'
    )
    mirror_guard = "Refuse a stale full-site publish"
    mirror_write = "Publish to GitHub Pages (seiche-site)"
    canonical_guard = "Re-prove current main before canonical deploy"
    canonical_write = "Deploy to canonical Cloudflare Pages"

    assert workflow.count(fetch) == 2
    current_main = (
        "current_main=\"$(git rev-parse 'refs/remotes/origin/main^{commit}')\""
    )
    assert workflow.count(current_main) == 2
    assert workflow.count('if [ "$current_main" != "$GITHUB_SHA" ]; then') == 2
    assert workflow.index(mirror_guard) < workflow.index(mirror_write)
    assert workflow.index(mirror_write) < workflow.index(canonical_guard)
    assert workflow.index(canonical_guard) < workflow.index(canonical_write)


def test_signed_release_gate_rejects_malformed_external_pins_before_git_use():
    with pytest.raises(gate.PublicationGateError, match="SHA is malformed"):
        gate.verify_signed_release(
            ROOT,
            version="0.12.1",
            expected_sha="main",
            signer_fingerprint="SHA256:" + "A" * 43,
        )
    with pytest.raises(gate.PublicationGateError, match="fingerprint is malformed"):
        gate.verify_signed_release(
            ROOT,
            version="0.12.1",
            expected_sha="a" * 40,
            signer_fingerprint="untrusted",
        )
