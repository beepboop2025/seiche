"""The public AI catalog never gets ahead of its signed release receipts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/release/verify_catalog_publication.py"
SPEC = importlib.util.spec_from_file_location("verify_catalog_publication", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _receipts(version: str = "0.12.4"):
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
        "0.12.4", fetch_json=fetch_json, fetch_bytes=fetch_bytes
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

    assert version == "0.12.4"
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
        "tag": "market-corpus-receipt-corpus-7cb1695c6affa707-r7",
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
    assert tag == "market-corpus-receipt-corpus-7cb1695c6affa707-r7"
    assert gate._market_corpus_publication_receipt(entry)["releaseId"] == (
        "corpus-7cb1695c6affa707"
    )

    def stale_receipt_tag(_root, *, tag, head, git_config):
        del head, git_config
        return "a" * 40 if tag == "market-corpus-v1.0.0" else "d" * 40

    monkeypatch.setattr(gate, "_verify_annotated_signed_tag", stale_receipt_tag)
    monkeypatch.setattr(
        gate, "verify_signed_release", lambda *_args, **_kwargs: "v0.12.4"
    )
    monkeypatch.setattr(
        gate,
        "_run_git",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout=expected_sha + "\n"
        ),
    )
    with pytest.raises(gate.PublicationGateError, match="does not target"):
        gate.verify_market_corpus_release(
            ROOT,
            expected_sha=expected_sha,
            signer_fingerprint="SHA256:" + "A" * 43,
        )


def _content_git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def content_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _content_git(root, "init", "-q")
    for key, value in (
        ("user.name", "seiche-desk"),
        ("user.email", "desk@seiche.info"),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
        ("core.filemode", "true"),
    ):
        _content_git(root, "config", key, value)
    for relative in (*gate.RELEASE_IDENTITY_PATHS, gate.AI_CATALOG_PATH):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    _content_git(root, "add", ".")
    _content_git(root, "commit", "-q", "-m", "release fixture")
    return root


def _content_commit(
    root, changes, *, subject="dispatch: daily", author="desk@seiche.info"
):
    for relative, body in changes.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    _content_git(root, "add", ".")
    _content_git(
        root,
        "commit",
        "-q",
        "--allow-empty",
        "--author",
        f"desk <{author}>",
        "-m",
        subject,
    )
    return _content_git(root, "rev-parse", "HEAD")


def _sign_content_release(root, *, receipt=True):
    key = root.parent / "fixture-signing-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    allowed = root / "ops/deploy/release-allowed-signers"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("fixture " + key.with_suffix(".pub").read_text())
    _content_git(root, "config", "gpg.format", "ssh")
    _content_git(root, "config", "user.signingkey", str(key))
    _content_git(root, "add", ".")
    _content_git(root, "commit", "-q", "--amend", "--no-edit", "-S")
    tags = ["v0.12.4", "market-corpus-v1.0.0"]
    if receipt:
        tags.append(gate._market_corpus_publication_receipt(_market_entry())["tag"])
    for tag in tags:
        _content_git(root, "tag", "-s", "-m", "signed fixture", tag)
    fingerprint = subprocess.check_output(
        ["ssh-keygen", "-E", "sha256", "-lf", str(key) + ".pub"], text=True
    ).split()[1]
    return _content_git(root, "rev-parse", "HEAD"), fingerprint


def test_signed_corpus_receipt_accepts_exact_release_and_daily_weekly_descendants(
    content_repo,
):
    root = content_repo
    release, fingerprint = _sign_content_release(root)
    gate.verify_market_corpus_release(
        root, expected_sha=release, signer_fingerprint=fingerprint
    )
    _content_commit(
        root,
        {
            "frontend/public/dispatches/2026-09-05-daily.md": "daily",
            "frontend/public/dispatches/index.json": "[]",
            "frontend/public/articles/2026-09-05-cash.md": "article",
            "frontend/public/articles/2026-09-05-cash.json": "{}",
            "frontend/public/articles/learning.json": "{}",
            "backend/seiche/dispatches/2026-09-05-daily.desk.md": "desk",
            "backend/seiche/dispatches/state.json": "{}",
            "backend/seiche/dispatches/odds_ledger.jsonl": "{}\n",
        },
    )
    head = _content_commit(
        root,
        {
            "frontend/public/dispatches/index.json": '["weekly"]',
            "backend/seiche/dispatches/2026-09-07-week-ahead.desk.md": "weekly",
            "backend/seiche/dispatches/weekly_state.json": "{}",
        },
        subject="week ahead: funding calendar",
    )
    tag, _ = gate.verify_market_corpus_release(
        root, expected_sha=head, signer_fingerprint=fingerprint
    )
    assert _content_git(root, "rev-parse", f"{tag}^{{commit}}") == release
    assert head != release


def test_fresh_exact_head_corpus_receipt_keeps_existing_independent_release_path(
    content_repo,
):
    root = content_repo
    release, fingerprint = _sign_content_release(root, receipt=False)
    _content_commit(root, {"backend/seiche/example.py": "# independent release\n"})
    _content_git(root, "commit", "-q", "--amend", "--no-edit", "-S")
    head = _content_git(root, "rev-parse", "HEAD")
    tag = gate._market_corpus_publication_receipt(_market_entry())["tag"]
    _content_git(root, "tag", "-s", "-m", "fresh exact-head receipt", tag)
    assert (
        gate.verify_signed_release(
            root, version="0.12.4", expected_sha=head, signer_fingerprint=fingerprint
        )
        == "v0.12.4"
    )
    assert (
        gate.verify_market_corpus_release(
            root, expected_sha=head, signer_fingerprint=fingerprint
        )[0]
        == tag
    )
    assert release != head


@pytest.mark.parametrize(
    "path",
    [
        "backend/seiche/assemble.py",
        ".github/workflows/publish.yml",
        gate.AI_CATALOG_PATH,
        "server.json",
    ],
)
def test_generated_content_rejects_even_reverted_release_or_code_drift(
    content_repo, path
):
    root = content_repo
    release, fingerprint = _sign_content_release(root)
    target = root / path
    original = target.read_text() if target.exists() else None
    _content_commit(root, {path: (original or "") + "\n# drift\n"})
    if original is None:
        target.unlink()
    else:
        target.write_text(original)
    head = _content_commit(root, {}, subject="week ahead: reverted drift")
    # The old endpoint-only release identity comparison still passes.
    gate.verify_signed_release(
        root, version="0.12.4", expected_sha=head, signer_fingerprint=fingerprint
    )
    with pytest.raises(gate.PublicationGateError, match="forbidden path or file mode"):
        gate.verify_market_corpus_release(
            root, expected_sha=head, signer_fingerprint=fingerprint
        )
    assert release != head


@pytest.mark.parametrize(
    ("path", "author", "subject"),
    [
        ("frontend/public/articles/tool.py", "desk@seiche.info", "dispatch: daily"),
        (
            "frontend/public/articles/nested/hidden.json",
            "desk@seiche.info",
            "dispatch: daily",
        ),
        (
            "frontend/public/articles/misleading\nname.md",
            "desk@seiche.info",
            "dispatch: daily",
        ),
        (
            "backend/seiche/dispatches/__init__.py",
            "desk@seiche.info",
            "dispatch: daily",
        ),
        (
            "backend/seiche/dispatches/unknown.json",
            "desk@seiche.info",
            "dispatch: daily",
        ),
        ("frontend/public/articles/cash.md", "intruder@example.com", "dispatch: daily"),
        ("frontend/public/articles/cash.md", "desk@seiche.info", "fix: bypass"),
    ],
)
def test_generated_content_rejects_unauthorized_lane(
    content_repo, path, author, subject
):
    release = _content_git(content_repo, "rev-parse", "HEAD")
    head = _content_commit(content_repo, {path: "test"}, author=author, subject=subject)
    with pytest.raises(gate.PublicationGateError, match="generated-content commit"):
        gate._verify_generated_content_descendants(
            content_repo, release=release, head=head
        )


@pytest.mark.parametrize("mode", ["executable", "symlink", "gitlink"])
def test_generated_content_rejects_nonregular_file_modes(content_repo, mode):
    root = content_repo
    release = _content_git(root, "rev-parse", "HEAD")
    relative = "frontend/public/articles/cash.md"
    target = root / relative
    target.parent.mkdir(parents=True)
    if mode == "gitlink":
        _content_git(
            root, "update-index", "--add", "--cacheinfo", f"160000,{release},{relative}"
        )
    else:
        if mode == "symlink":
            target.symlink_to("../../../../server.json")
        else:
            target.write_text("executable")
            target.chmod(0o755)
        _content_git(root, "add", relative)
    _content_git(root, "commit", "-q", "-m", "dispatch: unsafe mode")
    head = _content_git(root, "rev-parse", "HEAD")
    with pytest.raises(gate.PublicationGateError, match="forbidden path or file mode"):
        gate._verify_generated_content_descendants(root, release=release, head=head)


@pytest.mark.parametrize(
    "source",
    [
        "frontend/public/articles/old.md",
        "backend/seiche/example.py",
    ],
)
def test_generated_content_checks_both_sides_of_renames(content_repo, source):
    root = content_repo
    release = _content_commit(root, {source: "same content\n"})
    destination = root / "frontend/public/articles/new.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    (root / source).rename(destination)
    head = _content_commit(root, {})
    if source.endswith(".py"):
        with pytest.raises(
            gate.PublicationGateError, match="forbidden path or file mode"
        ):
            gate._verify_generated_content_descendants(root, release=release, head=head)
    else:
        gate._verify_generated_content_descendants(root, release=release, head=head)


def test_generated_content_rejects_merge_and_empty_commits(content_repo):
    root = content_repo
    release = _content_git(root, "rev-parse", "HEAD")
    empty = _content_commit(root, {})
    with pytest.raises(gate.PublicationGateError, match="no verifiable changes"):
        gate._verify_generated_content_descendants(root, release=release, head=empty)
    # A merge of two otherwise acceptable desk commits is still outside this lane.
    left = _content_commit(root, {"frontend/public/articles/left.md": "left"})
    _content_git(root, "checkout", "-q", "--detach", empty)
    _content_commit(root, {"frontend/public/articles/right.md": "right"})
    _content_git(root, "merge", "--no-ff", "-q", "-m", "dispatch: merge", left)
    head = _content_git(root, "rev-parse", "HEAD")
    with pytest.raises(gate.PublicationGateError, match="single-parent"):
        gate._verify_generated_content_descendants(root, release=empty, head=head)


def _signed_controller_fixture(root):
    for relative in gate.PUBLICATION_CONTROLLER_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# initial publication support\n")
    _content_git(root, "add", ".")
    _content_git(root, "commit", "-q", "-m", "publication support fixture")
    return _sign_content_release(root)


def _controller_commit(root, changes, *, sign=True, author=None, subject=None):
    head = _content_commit(
        root,
        changes,
        author=author or gate.PUBLICATION_CONTROLLER_AUTHOR.decode(),
        subject=subject or "publication-controller: repair publication contracts",
    )
    if sign:
        _content_git(
            root, "commit", "-q", "--amend", "--no-edit", "--allow-empty", "-S"
        )
        head = _content_git(root, "rev-parse", "HEAD")
    return head


def test_signed_controller_then_daily_weekly_keeps_original_release_receipts(
    content_repo, monkeypatch, capsys
):
    root = content_repo
    release, fingerprint = _signed_controller_fixture(root)
    controller = _controller_commit(
        root,
        {
            relative: "# reviewed publication repair\n"
            for relative in gate.PUBLICATION_CONTROLLER_PATHS
        },
    )
    for head in (
        controller,
        _content_commit(root, {"frontend/public/articles/cash.md": "daily"}),
        _content_commit(
            root,
            {"frontend/public/dispatches/week.md": "weekly"},
            subject="week ahead: funding calendar",
        ),
    ):
        # Check the real signed-release and corpus gates at each publication head.
        current = _content_git(root, "rev-parse", "HEAD")
        _content_git(root, "checkout", "-q", "--detach", head)
        assert (
            gate.verify_signed_release(
                root,
                version="0.12.4",
                expected_sha=head,
                signer_fingerprint=fingerprint,
            )
            == "v0.12.4"
        )
        receipt, _ = gate.verify_market_corpus_release(
            root, expected_sha=head, signer_fingerprint=fingerprint
        )
        assert _content_git(root, "rev-parse", f"{receipt}^{{commit}}") == release
        assert _content_git(root, "rev-parse", "v0.12.4^{commit}") == release
        _content_git(root, "checkout", "-q", "--detach", current)
    # Exercise the real CLI identity output; public network receipt collection
    # is a separate concern and is intentionally stubbed in this offline test.
    monkeypatch.setattr(gate, "verify_public_receipts", lambda _version: {})
    monkeypatch.setattr(gate, "verify_market_corpus_receipts", lambda _entry: {})
    assert (
        gate.main(
            [
                "--root",
                str(root),
                "--expected-sha",
                head,
                "--signer-fingerprint",
                fingerprint,
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["revision"] == head
    assert report["releaseRevision"] == release
    assert report["releaseRevision"] != report["revision"]
    assert report["releaseTag"] == "v0.12.4"


@pytest.mark.parametrize("failure", ["unsigned", "wrong-key", "missing-pin"])
def test_controller_descendants_require_the_pinned_signature(content_repo, failure):
    root = content_repo
    release, fingerprint = _signed_controller_fixture(root)
    if failure == "wrong-key":
        key = root.parent / "untrusted-controller-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        _content_git(root, "config", "user.signingkey", str(key))
    head = _controller_commit(
        root,
        {"backend/tests/test_publish_scripts.py": "# repaired\n"},
        sign=failure != "unsigned",
    )
    with pytest.raises(gate.PublicationGateError, match="signature|pinned signer"):
        gate._verify_generated_content_descendants(
            root,
            release=release,
            head=head,
            signer_fingerprint=None if failure == "missing-pin" else fingerprint,
        )


@pytest.mark.skipif(
    shutil.which("gpg") is None,
    reason="requires GPG for signature-algorithm regression",
)
def test_valid_gpg_controller_signature_does_not_satisfy_the_ssh_pin(
    content_repo, monkeypatch
):
    root = content_repo
    release, fingerprint = _signed_controller_fixture(root)
    # Keep Unix socket names below the platform limit even when pytest's
    # temporary repository path is long. This keyring never uses the real HOME.
    with tempfile.TemporaryDirectory(prefix="seiche-gpg-", dir="/tmp") as keyring:
        monkeypatch.setenv("GNUPGHOME", keyring)
        try:
            subprocess.run(
                [
                    "gpg",
                    "--batch",
                    "--pinentry-mode",
                    "loopback",
                    "--passphrase",
                    "",
                    "--quick-generate-key",
                    "controller-fixture <fixture@example.invalid>",
                    "ed25519",
                    "sign",
                    "0",
                ],
                check=True,
                capture_output=True,
            )
            keys = subprocess.check_output(
                ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
                text=True,
            )
            gpg_fingerprint = next(
                line.split(":")[9]
                for line in keys.splitlines()
                if line.startswith("fpr:")
            )
            _content_git(root, "config", "gpg.format", "openpgp")
            _content_git(root, "config", "user.signingkey", gpg_fingerprint)
            head = _controller_commit(
                root, {"backend/tests/test_publish_scripts.py": "# repair\n"}
            )
            # Git considers this real signature valid, but it is not the pinned SSH key.
            _content_git(root, "verify-commit", head)
            with pytest.raises(
                gate.PublicationGateError, match="differs from the pinned signer"
            ):
                gate._verify_generated_content_descendants(
                    root,
                    release=release,
                    head=head,
                    signer_fingerprint=fingerprint,
                )
        finally:
            subprocess.run(
                ["gpgconf", "--homedir", keyring, "--kill", "gpg-agent"],
                check=False,
                capture_output=True,
            )


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/publish.yml",
        "backend/seiche/assemble.py",
        "backend/tests/unreviewed_test.py",
        "ops/release/unreviewed.py",
        "frontend/public/articles/mixed.md",
    ],
)
def test_signed_controller_rejects_every_unlisted_path(content_repo, path):
    root = content_repo
    release, fingerprint = _signed_controller_fixture(root)
    head = _controller_commit(
        root,
        {
            "backend/tests/test_publish_scripts.py": "# repair\n",
            path: "# forbidden\n",
        },
    )
    with pytest.raises(gate.PublicationGateError, match="forbidden path or file mode"):
        gate._verify_generated_content_descendants(
            root, release=release, head=head, signer_fingerprint=fingerprint
        )


@pytest.mark.parametrize("failure", ["wrong-author", "wrong-subject", "empty"])
def test_signed_controller_requires_explicit_identity_and_changes(
    content_repo, failure
):
    root = content_repo
    release, fingerprint = _signed_controller_fixture(root)
    head = _controller_commit(
        root,
        {}
        if failure == "empty"
        else {
            "backend/tests/test_publish_scripts.py": "# repair\n",
        },
        author="intruder@example.com" if failure == "wrong-author" else None,
        subject="fix: unrelated" if failure == "wrong-subject" else None,
    )
    with pytest.raises(gate.PublicationGateError, match="generated-content commit"):
        gate._verify_generated_content_descendants(
            root, release=release, head=head, signer_fingerprint=fingerprint
        )


@pytest.mark.parametrize("failure", ["executable", "symlink", "deletion"])
def test_signed_controller_only_modifies_existing_regular_files(content_repo, failure):
    root = content_repo
    release, fingerprint = _signed_controller_fixture(root)
    path = root / "backend/tests/test_publish_scripts.py"
    if failure == "executable":
        path.chmod(0o755)
    else:
        path.unlink()
        if failure == "symlink":
            path.symlink_to("../../server.json")
    head = _controller_commit(root, {})
    with pytest.raises(gate.PublicationGateError, match="forbidden path or file mode"):
        gate._verify_generated_content_descendants(
            root, release=release, head=head, signer_fingerprint=fingerprint
        )


def test_signed_controller_merge_and_reverted_code_drift_remain_rejected(content_repo):
    root = content_repo
    release, fingerprint = _signed_controller_fixture(root)
    original = (root / "backend/seiche/assemble.py").read_text()
    _controller_commit(root, {"backend/seiche/assemble.py": "# drift\n"})
    reverted = _controller_commit(root, {"backend/seiche/assemble.py": original})
    with pytest.raises(gate.PublicationGateError, match="forbidden path or file mode"):
        gate._verify_generated_content_descendants(
            root, release=release, head=reverted, signer_fingerprint=fingerprint
        )
    _content_git(root, "checkout", "-q", "--detach", release)
    left = _controller_commit(
        root, {"backend/tests/test_publish_scripts.py": "# left\n"}
    )
    _content_git(root, "checkout", "-q", "--detach", release)
    _controller_commit(
        root, {"backend/tests/test_railway_stateful_control.py": "# right\n"}
    )
    _content_git(
        root,
        "merge",
        "--no-ff",
        "-q",
        "-S",
        "-m",
        "publication-controller: merge",
        left,
    )
    head = _content_git(root, "rev-parse", "HEAD")
    with pytest.raises(gate.PublicationGateError, match="single-parent"):
        gate._verify_generated_content_descendants(
            root, release=release, head=head, signer_fingerprint=fingerprint
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

    assert receipt["version"] == "0.12.4"
    assert [item["filename"] for item in receipt["artifacts"]] == [
        "seiche-0.12.4-py3-none-any.whl",
        "seiche-0.12.4.tar.gz",
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
    for workflow, source_var in (
        (fast, "GITHUB_SHA"),
        (full, "PUBLICATION_SOURCE_SHA"),
    ):
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
            f'git show "${{{source_var}}}:ops/release/verify_catalog_publication.py"'
            in workflow
        )
        assert 'git hash-object "$verifier"' in workflow
        assert 'python -I -S "$verifier"' in workflow
        assert "python -I -S ops/release/verify_catalog_publication.py" not in workflow
        assert f'--expected-sha "${source_var}"' in workflow
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
    initial_guard = "Require the selected source to be exact current main"

    assert workflow.count(fetch) == 3
    current_main = (
        "current_main=\"$(git rev-parse 'refs/remotes/origin/main^{commit}')\""
    )
    assert workflow.count(current_main) == 2
    comparison = 'if [ "$current_main" != "$PUBLICATION_SOURCE_SHA" ]; then'
    assert workflow.count(comparison) == 2
    assert workflow.index(initial_guard) < workflow.index("Install backend")
    assert workflow.index(mirror_guard) < workflow.index(mirror_write)
    assert workflow.index(mirror_write) < workflow.index(canonical_guard)
    assert workflow.index(canonical_guard) < workflow.index(canonical_write)
    for guard in (mirror_guard, canonical_guard):
        step = _full_publish_step(guard)
        assert fetch in step
        assert current_main in step
        assert comparison in step
        assert "exit 1" in step


def _full_publish_step(name):
    workflow = (ROOT / ".github/workflows/publish.yml").read_text()
    return workflow.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]


def test_full_publish_binds_checkout_cache_and_verifier_to_selected_source():
    workflow = (ROOT / ".github/workflows/publish.yml").read_text()
    assert "PUBLICATION_SOURCE_SHA: ${{ inputs.release_sha || github.sha }}" in workflow
    assert "PUBLICATION_CONTROLLER_SHA: ${{ github.sha }}" in workflow
    assert "ref: ${{ env.PUBLICATION_SOURCE_SHA }}" in workflow
    assert "persist-credentials: false" in workflow
    cache = _full_publish_step("Restore exact-code publish gate")
    assert "path: .cache/publish-gates/${{ env.PUBLICATION_SOURCE_SHA }}" in cache
    assert (
        "key: seiche-publish-gate-v1-${{ runner.os }}-py312-${{ env.PUBLICATION_SOURCE_SHA }}"
        in cache
    )
    marker = _full_publish_step("Verify exact-code publish gate")
    assert '$(cat "$MARKER" 2>/dev/null || true)" = "$PUBLICATION_SOURCE_SHA"' in marker
    install = _full_publish_step("Install backend")
    assert 'pip install -e "./backend[dev,collectors,postgres]"' in install
    assert install.index('if [ "$RUN_FULL_SUITE" = "true" ]; then') < install.index(
        'pip install -e "./backend[dev,collectors,postgres]"'
    )


@pytest.mark.parametrize(
    "guard",
    (
        "Require the selected source to be exact current main",
        "Refuse a stale full-site publish",
        "Re-prove current main before canonical deploy",
    ),
)
@pytest.mark.parametrize(
    "scenario", ("current_source", "main_advanced", "fetch_failed")
)
def test_full_publish_guards_compare_application_source_not_controller(
    content_repo, guard, scenario
):
    root = content_repo
    source = _content_git(root, "rev-parse", "HEAD")
    controller = _content_commit(root, {}, subject="separate controller fixture")
    _content_git(root, "branch", "-M", "main")
    remote = root.parent / "publisher-origin.git"
    _content_git(root, "clone", "--bare", "-q", str(root), str(remote))
    _content_git(root, "remote", "add", "origin", str(remote))
    _content_git(root, "checkout", "-q", "--detach", source)
    _content_git(remote, "update-ref", "refs/heads/main", source)
    if scenario == "main_advanced":
        _content_git(remote, "update-ref", "refs/heads/main", controller)
    elif scenario == "fetch_failed":
        _content_git(root, "remote", "set-url", "origin", str(root.parent / "missing"))
    script = textwrap.dedent(_full_publish_step(guard).split("        run: |\n", 1)[1])
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=root,
        env={
            **os.environ,
            "PUBLICATION_SOURCE_SHA": source,
            "PUBLICATION_CONTROLLER_SHA": controller,
            "GITHUB_SHA": controller,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == (scenario == "current_source"), result.stderr


def test_signed_release_gate_rejects_malformed_external_pins_before_git_use():
    with pytest.raises(gate.PublicationGateError, match="SHA is malformed"):
        gate.verify_signed_release(
            ROOT,
            version="0.12.4",
            expected_sha="main",
            signer_fingerprint="SHA256:" + "A" * 43,
        )
    with pytest.raises(gate.PublicationGateError, match="fingerprint is malformed"):
        gate.verify_signed_release(
            ROOT,
            version="0.12.4",
            expected_sha="a" * 40,
            signer_fingerprint="untrusted",
        )


# Real temporary SSH keys below are synthetic fixtures. No production signer,
# receipt or remote publication is used by these frontend contract regressions.
_FRONT_SPEC = importlib.util.spec_from_file_location(
    "frontend_publication", ROOT / "ops/release/verify_frontend_publication.py"
)
assert _FRONT_SPEC and _FRONT_SPEC.loader
front = importlib.util.module_from_spec(_FRONT_SPEC)
_FRONT_SPEC.loader.exec_module(front)
_SITE_SPEC = importlib.util.spec_from_file_location(
    "frontend_site_proof", ROOT / "ops/release/frontend_site_proof.py"
)
assert _SITE_SPEC and _SITE_SPEC.loader
site_proof = importlib.util.module_from_spec(_SITE_SPEC)
_SITE_SPEC.loader.exec_module(site_proof)


@pytest.fixture
def frontend_repo(content_repo):
    root = content_repo
    for relative in (
        "ops/release/verify_catalog_publication.py",
        "ops/release/verify_frontend_publication.py",
        "ops/release/frontend_site_proof.py",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/tsconfig.json",
        "frontend/vite.config.ts",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (root / "frontend/src").mkdir()
    (root / "frontend/src/App.tsx").write_text("// synthetic original UI\n")
    release, fingerprint = _sign_content_release(root)
    return root, release, fingerprint


def _frontend_change(root, changes=None):
    return _content_commit(
        root,
        changes or {"frontend/src/App.tsx": "// synthetic watchlist\n"},
        author="reviewer@example.invalid",
        subject="feat: synthetic frontend change",
    )


def _frontend_tag(root, fingerprint, *, payload_change=None, key=None):
    source = _content_git(root, "rev-parse", "HEAD")
    payload, _ = front.prepare_receipt(
        root, expected_sha=source, signer_fingerprint=fingerprint
    )
    if payload_change:
        payload.update(payload_change)
    receipt = root.parent / "synthetic-frontend-receipt.json"
    receipt.write_bytes(front._canonical(payload))
    tag = front.TAG_PREFIX + source
    config = ["-c", f"user.signingkey={key}"] if key else []
    _content_git(
        root,
        *config,
        "tag",
        "-s",
        "--cleanup=verbatim",
        "-F",
        str(receipt),
        tag,
        source,
    )
    return tag


def test_frontend_tag_authenticates_unsigned_source_without_rebinding_backend(
    frontend_repo,
):
    root, release, fingerprint = frontend_repo
    source = _frontend_change(root)
    tag = _frontend_tag(root, fingerprint)
    proof = front.verify_frontend_receipt(
        root, expected_sha=source, signer_fingerprint=fingerprint, receipt_tag=tag
    )
    assert proof["sourceSha"] == source != release
    assert proof["backendReleaseSha"] == proof["corpusReceiptSha"] == release
    assert proof["purpose"] == "frontend_only_no_runtime_activation"
    assert (
        _content_git(root, "rev-parse", proof["corpusReceiptTag"] + "^{commit}")
        == release
    )
    # The existing full-application fallback remains closed for this UI commit.
    with pytest.raises(gate.PublicationGateError, match="generated-content"):
        gate.verify_market_corpus_release(
            root, expected_sha=source, signer_fingerprint=fingerprint
        )


def test_frontend_receipt_accepts_reviewed_merge_and_reports_excluded_paths(
    frontend_repo,
):
    root, release, fingerprint = frontend_repo
    _content_commit(
        root, {"frontend/public/dispatches/2026-09-08.md": "synthetic evidence"}
    )
    _frontend_change(root, {"backend/scripts/ard_coverage.py": "# ignored monitor\n"})
    _frontend_change(root, {"ops/railway-automation/run.sh": "# ignored monitor\n"})
    _frontend_change(
        root, {"ops/railway-automation/Dockerfile.distribution": "# isolated CI\n"}
    )
    _frontend_change(
        root,
        {".github/workflows/distribution-contracts.yml": "# isolated CI fallback\n"},
    )
    _frontend_change(
        root,
        {
            "ops/railway-automation/publisher/publish.py": "# separately reviewed controller\n"
        },
    )
    base_branch = _content_git(root, "branch", "--show-current")
    _content_git(root, "checkout", "-q", "-b", "synthetic-ui")
    _frontend_change(root)
    _content_git(root, "checkout", "-q", base_branch)
    _content_git(
        root,
        "merge",
        "--no-ff",
        "--no-gpg-sign",
        "-m",
        "reviewed synthetic merge",
        "synthetic-ui",
    )
    source = _content_git(root, "rev-parse", "HEAD")
    payload, changes = front.prepare_receipt(
        root, expected_sha=source, signer_fingerprint=fingerprint
    )
    assert {"frontend", "excluded_monitor", "excluded_desk_content"} <= {
        change["kind"] for change in changes
    }
    assert payload["backendReleaseSha"] == release
    assert (
        front.verify_frontend_receipt(
            root,
            expected_sha=source,
            signer_fingerprint=fingerprint,
            receipt_tag=_frontend_tag(root, fingerprint),
        )
        == payload
    )


@pytest.mark.parametrize(
    "relative",
    [
        "backend/seiche/api.py",
        "backend/seiche/assemble.py",
        "backend/scripts/another-monitor.py",
        "ops/railway-automation/other.sh",
        "ops/railway-automation/publisher/unreviewed.py",
        "ops/railway-automation/market-contracts/unreviewed.sh",
        "ops/railway-automation/full-publisher/unreviewed.py",
        ".github/workflows/recovery-monitor-handoff-extra.yml",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/tsconfig.json",
        "frontend/vite.config.ts",
        "frontend/public/data/overview.json",
        "frontend/public/.well-known/ai-catalog.json",
        "frontend/public/_redirects",
        "ops/release/verify_catalog_publication.py",
        "ops/deploy/release-allowed-signers",
        ".github/workflows/publish.yml",
    ],
)
def test_frontend_contract_rejects_runtime_build_catalog_data_and_unlisted_operations(
    frontend_repo, relative
):
    root, release, _ = frontend_repo
    _frontend_change(root)
    source = _frontend_change(root, {relative: "synthetic forbidden mutation\n"})
    with pytest.raises(front.Error, match="forbidden"):
        front.compatibility_changes(root, release, source)


@pytest.mark.parametrize("relative", [
    "ops/railway-automation/market-contracts/Dockerfile",
    "ops/railway-automation/market-contracts/Dockerfile.dockerignore",
    "ops/railway-automation/market-contracts/run.sh",
    "ops/railway-automation/market-contracts/README.md",
    "ops/railway-automation/full-publisher/Dockerfile",
    "ops/railway-automation/full-publisher/publish.py",
    "ops/railway-automation/full-publisher/test_publish.py",
    "ops/railway-automation/full-publisher/README.md",
])
def test_frontend_receipt_accepts_only_reviewed_native_controller_paths(frontend_repo, relative):
    root, release, _ = frontend_repo
    _frontend_change(root)
    source = _frontend_change(root, {relative: "# reviewed isolated executor\n"})
    changes = front.compatibility_changes(root, release, source)
    assert any(change["path"] == relative and change["kind"] == "excluded_monitor" for change in changes)


def test_frontend_receipt_requires_one_time_handoff_to_be_removed(frontend_repo):
    root, release, _ = frontend_repo
    _frontend_change(root)
    relative = ".github/workflows/recovery-monitor-handoff.yml"
    source = _frontend_change(root, {relative: "# reviewed one-time handoff\n"})
    with pytest.raises(front.Error, match="forbidden one-time"):
        front.compatibility_changes(root, release, source)
    _content_git(root, "rm", relative)
    _content_git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Remove completed one-time handoff")
    source = _content_git(root, "rev-parse", "HEAD")
    changes = front.compatibility_changes(root, release, source)
    assert sum(change["path"] == relative and change["kind"] == "retired_handoff" for change in changes) == 2


def test_frontend_contract_rejects_reverted_runtime_edit(frontend_repo):
    root, release, _ = frontend_repo
    relative = "backend/seiche/assemble.py"
    original = (root / relative).read_text()
    _frontend_change(root, {relative: "# temporary unsafe runtime\n"})
    _frontend_change(root, {relative: original})
    source = _frontend_change(root)
    with pytest.raises(front.Error, match="forbidden"):
        front.compatibility_changes(root, release, source)


@pytest.mark.parametrize("mode", ["symlink", "executable", "gitlink"])
def test_frontend_contract_rejects_nonregular_ui_entries(frontend_repo, mode):
    root, release, _ = frontend_repo
    target = root / "frontend/src/unsafe.ts"
    if mode == "symlink":
        target.symlink_to("../../../outside")
    elif mode == "executable":
        target.write_text("// executable fixture")
        target.chmod(0o755)
    source = _frontend_change(root)
    if mode == "gitlink":
        _content_git(
            root,
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            release,
            "frontend/src/nested.ts",
        )
        _content_git(root, "commit", "-q", "-m", "synthetic gitlink")
        source = _content_git(root, "rev-parse", "HEAD")
    with pytest.raises(front.Error, match="nonregular"):
        front.compatibility_changes(root, release, source)


@pytest.mark.parametrize(
    "relative",
    ["frontend/public/dispatches/forged.json", "backend/seiche/dispatches/state.json"],
)
def test_frontend_contract_cannot_launder_generated_evidence(frontend_repo, relative):
    root, release, _ = frontend_repo
    source = _frontend_change(
        root, {"frontend/src/App.tsx": "// synthetic UI", relative: "forged evidence"}
    )
    with pytest.raises(front.Error, match="unauthorized generated"):
        front.compatibility_changes(root, release, source)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-baseline",
        "wrong-purpose",
        "wrong-source",
        "wrong-key",
        "unsigned",
        "lightweight",
        "wrong-tag-name",
    ],
)
def test_frontend_receipt_rejects_unbound_subjects_and_signatures(
    frontend_repo, mutation
):
    root, _, fingerprint = frontend_repo
    source = _frontend_change(root)
    payload_change = {
        "wrong-baseline": {"backendReleaseSha": "a" * 40},
        "wrong-purpose": {"purpose": "runtime_activated"},
        "wrong-source": {"sourceSha": "a" * 40},
    }.get(mutation)
    key = None
    if mutation == "wrong-key":
        key = root.parent / "untrusted-frontend-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
        )
    tag = front.TAG_PREFIX + source
    if mutation == "lightweight":
        _content_git(root, "tag", tag)
    elif mutation == "unsigned":
        _content_git(root, "tag", "-a", "-m", "unsigned fixture", tag)
    else:
        tag = _frontend_tag(root, fingerprint, payload_change=payload_change, key=key)
    if mutation == "wrong-tag-name":
        tag = "frontend-publication-../../untrusted"
    with pytest.raises(front.Error):
        front.verify_frontend_receipt(
            root, expected_sha=source, signer_fingerprint=fingerprint, receipt_tag=tag
        )


def test_frontend_receipt_refuses_reuse_and_unverifiable_remote_absence(frontend_repo):
    root, _, fingerprint = frontend_repo
    source = _frontend_change(root)
    tag = front.TAG_PREFIX + source
    remote = root.parent / "synthetic-remote.git"
    _content_git(root.parent, "init", "--bare", "-q", str(remote))
    _content_git(root, "remote", "add", "origin", str(remote))
    front.require_unused_tag(root, tag)
    _frontend_tag(root, fingerprint)
    with pytest.raises(front.Error, match="already exists locally"):
        front.require_unused_tag(root, tag)
    _content_git(root, "push", "-q", "origin", f"refs/tags/{tag}")
    _content_git(root, "tag", "-d", tag)
    with pytest.raises(front.Error, match="already exists remotely"):
        front.require_unused_tag(root, tag)
    _content_git(root, "remote", "set-url", "origin", str(root.parent / "absent"))
    with pytest.raises(front.Error, match="absence could not"):
        front.require_unused_tag(root, tag)


def test_frontend_receipt_requires_clean_source_and_verifier_bytes(frontend_repo):
    root, _, fingerprint = frontend_repo
    source = _frontend_change(root)
    target = root / "frontend/src/App.tsx"
    target.write_text("// dirty UI")
    with pytest.raises(front.Error, match="dirty"):
        front.prepare_receipt(root, expected_sha=source, signer_fingerprint=fingerprint)
    _content_git(root, "checkout", "--", "frontend/src/App.tsx")
    verifier = "ops/release/verify_catalog_publication.py"
    _content_git(root, "update-index", "--assume-unchanged", verifier)
    (root / verifier).write_text("# hidden dirty gate")
    with pytest.raises(front.Error, match="input differs"):
        front.prepare_receipt(root, expected_sha=source, signer_fingerprint=fingerprint)


@pytest.mark.parametrize("failure", ["hidden-ui", "untracked-build-input"])
def test_frontend_receipt_refuses_uncommitted_build_inputs(frontend_repo, failure):
    root, _, fingerprint = frontend_repo
    source = _frontend_change(root)
    if failure == "hidden-ui":
        _content_git(root, "update-index", "--assume-unchanged", "frontend/src/App.tsx")
        (root / "frontend/src/App.tsx").write_text("// hidden UI drift")
    else:
        (root / "frontend/src/untracked.ts").write_text("// uncommitted build input")
    with pytest.raises(front.Error, match="index flags|untracked build"):
        front.prepare_receipt(root, expected_sha=source, signer_fingerprint=fingerprint)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "same-version-different-source",
        "candidate",
        "missing-deployment",
        "invalid-deployment",
        "changed-deployment",
        "duplicate-source",
        "duplicate-authority",
        "duplicate-deployment",
        "corpus-owner",
    ],
)
def test_frontend_runtime_subject_remains_the_actual_backend_receipt(mutation):
    headers = {
        "X-Seiche-Release-SHA": "b" * 40,
        "X-Seiche-Railway-Authority": "production",
        "X-Seiche-Railway-Deployment": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    }
    observed = front.RuntimeReceipts(
        "b" * 40,
        version="0.12.4",
        corpus_release_id="corpus-" + "c" * 16,
        request=lambda _request: (b'{"ok":true}', headers),
    )
    assert observed.fetch_json(
        "https://api.seiche.info/api/health?release=0.12.4",
        expected_host="api.seiche.info",
    ) == {"ok": True}
    if mutation == "same-version-different-source":
        headers["X-Seiche-Release-SHA"] = "a" * 40
    elif mutation == "candidate":
        headers["X-Seiche-Railway-Authority"] = "candidate"
    elif mutation == "missing-deployment":
        headers.pop("X-Seiche-Railway-Deployment")
    elif mutation == "invalid-deployment":
        headers["X-Seiche-Railway-Deployment"] = "not-a-provider-identity"
    elif mutation == "changed-deployment":
        headers["X-Seiche-Railway-Deployment"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    elif mutation == "corpus-owner":
        headers["X-Corpus-Release"] = "corpus-" + "c" * 16
    elif mutation and mutation.startswith("duplicate-"):
        from email.message import Message

        duplicate = Message()
        for name, value in headers.items():
            duplicate[name] = value
        name = {
            "duplicate-source": "X-Seiche-Release-SHA",
            "duplicate-authority": "X-Seiche-Railway-Authority",
            "duplicate-deployment": "X-Seiche-Railway-Deployment",
        }[mutation]
        duplicate[name] = headers[name]
        headers = duplicate
    if mutation:
        with pytest.raises(
            front.Error,
            match="backend production subject|identity changed|conflicting corpus-owner",
        ):
            observed.fetch_json(
                "https://api.seiche.info/.well-known/mcp.json",
                expected_host="api.seiche.info",
            )
    else:
        observed.fetch_json(
            "https://api.seiche.info/.well-known/mcp.json",
            expected_host="api.seiche.info",
        )
        assert observed.subject["releaseSha"] == "b" * 40
        assert len(observed.endpoints) == 2


@pytest.mark.parametrize(
    "mutation", [None, "missing", "wrong", "duplicate", "conflicting", "mixed-owner"]
)
def test_frontend_corpus_subject_uses_only_its_signed_native_release(mutation):
    release_id = "corpus-" + "c" * 16
    headers = {"X-Corpus-Release": release_id}
    observed = front.RuntimeReceipts(
        "b" * 40,
        version="0.12.4",
        corpus_release_id=release_id,
        request=lambda _request: (b'{"ok":true}', headers),
    )
    observed.fetch_json(
        front.gate.MARKET_CORPUS_HEALTH_URL, expected_host="api.seiche.info"
    )
    if mutation == "missing":
        headers = {}
    elif mutation == "wrong":
        headers = {"X-Corpus-Release": "corpus-" + "d" * 16}
    elif mutation in {"duplicate", "conflicting"}:
        from email.message import Message

        headers = Message()
        headers["X-Corpus-Release"] = release_id
        headers["x-corpus-release"] = (
            release_id if mutation == "duplicate" else "corpus-" + "d" * 16
        )
    elif mutation == "mixed-owner":
        headers["X-Seiche-Release-SHA"] = "b" * 40
    if mutation:
        with pytest.raises(
            front.Error, match="corpus subject|conflicting runtime-owner"
        ):
            observed.fetch_json(
                front.gate.MARKET_CORPUS_CATALOG_URL, expected_host="api.seiche.info"
            )
    else:
        observed.fetch_json(
            front.gate.MARKET_CORPUS_CATALOG_URL, expected_host="api.seiche.info"
        )
        observed.post_json(
            front.gate.MARKET_CORPUS_MCP_URL,
            {
                "jsonrpc": "2.0",
                "id": "market-corpus-publication-proof",
                "method": "tools/list",
                "params": {},
            },
            expected_host="api.seiche.info",
        )
        assert observed.corpus_subject == {
            "releaseId": release_id,
            "identityHeader": "X-Corpus-Release",
        }
        assert observed.subject is None  # No invented Seiche deployment identity.


@pytest.mark.parametrize(
    "url,method",
    [
        (gate.MARKET_CORPUS_HEALTH_URL, "POST"),
        (gate.MARKET_CORPUS_CATALOG_URL, "POST"),
        (gate.MARKET_CORPUS_MCP_URL, "GET"),
        (gate.MARKET_CORPUS_CATALOG_URL + "?different=true", "GET"),
        (gate.MARKET_CORPUS_MCP_URL + "/", "POST"),
        ("https://api.seiche.info/api/v2/corpus/other", "GET"),
        ("https://api.seiche.info/api/v2/corpus/%6dcp", "POST"),
        ("https://api.seiche.info/.well-known/mcp.json", "POST"),
        ("https://api.seiche.info/api/health?release=0.12.3", "GET"),
        ("https://elsewhere.invalid/api/v2/corpus/mcp", "POST"),
    ],
)
def test_frontend_runtime_route_classification_rejects_unregistered_requests(
    url, method
):
    requests = []
    observed = front.RuntimeReceipts(
        "b" * 40,
        version="0.12.4",
        corpus_release_id="corpus-" + "c" * 16,
        request=lambda request: requests.append(request),
    )
    with pytest.raises(front.Error, match="registered|canonical"):
        if method == "GET":
            observed.fetch_json(url, expected_host="api.seiche.info")
        else:
            observed.post_json(
                url, {"method": "tools/list"}, expected_host="api.seiche.info"
            )
    assert requests == []


@pytest.mark.parametrize("payload", [None, {"method": "tools/call"}])
def test_frontend_corpus_post_cannot_turn_into_an_arbitrary_tool_call(payload):
    requests = []
    observed = front.RuntimeReceipts(
        "b" * 40,
        version="0.12.4",
        corpus_release_id="corpus-" + "c" * 16,
        request=lambda request: requests.append(request),
    )
    with pytest.raises(front.Error, match="exact tools/list|payload is missing"):
        observed.post_json(
            gate.MARKET_CORPUS_MCP_URL,
            payload,
            expected_host="api.seiche.info",
        )
    assert requests == []


@pytest.mark.parametrize(
    "mutation", [None, "corpus-count", "runtime-fault", "pypi-body"]
)
def test_frontend_independent_subjects_preserve_all_original_semantic_gates(
    monkeypatch, mutation
):
    pypi, health, discovery, bodies = _receipts()
    corpus_health, corpus_catalog, corpus_discovery, tools = _market_receipts()
    release_id = gate._market_corpus_publication_receipt(_market_entry())["releaseId"]
    seiche_headers = {
        "X-Seiche-Release-SHA": "b" * 40,
        "X-Seiche-Railway-Authority": "production",
        "X-Seiche-Railway-Deployment": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    }
    if mutation == "corpus-count":
        corpus_catalog["corpora"]["liquilens_engine"]["datasets"] = 999
    elif mutation == "runtime-fault":
        health["faults"] = [{"component": "collector"}]
    elif mutation == "pypi-body":
        bodies[next(iter(bodies))] = b"tampered immutable package"
    responses = {
        "https://api.seiche.info/api/health?release=0.12.4": (health, seiche_headers),
        "https://api.seiche.info/.well-known/mcp.json?release=0.12.4": (
            discovery,
            seiche_headers,
        ),
        gate.MARKET_CORPUS_HEALTH_URL: (
            corpus_health,
            {"X-Corpus-Release": release_id},
        ),
        gate.MARKET_CORPUS_CATALOG_URL: (
            corpus_catalog,
            {"X-Corpus-Release": release_id},
        ),
        gate.MARKET_CORPUS_DISCOVERY_URL: (corpus_discovery, seiche_headers),
        gate.MARKET_CORPUS_MCP_URL: (tools, {"X-Corpus-Release": release_id}),
    }

    def request(request):
        body, headers = responses[request.full_url]
        return json.dumps(body).encode(), headers

    observed = front.RuntimeReceipts(
        "b" * 40, version="0.12.4", corpus_release_id=release_id, request=request
    )
    monkeypatch.setattr(front.gate, "_fetch_json", lambda _url, **_kwargs: pypi)

    def verify():
        front.gate.verify_public_receipts(
            "0.12.4",
            fetch_json=observed.fetch_json,
            fetch_bytes=lambda url, **_kwargs: bodies[url],
        )
        front.gate.verify_market_corpus_receipts(
            _market_entry(),
            fetch_json=observed.fetch_json,
            post_json=observed.post_json,
        )

    if mutation:
        with pytest.raises(front.Error):
            verify()
    else:
        verify()
        assert observed.subject["releaseSha"] == "b" * 40
        assert observed.corpus_subject["releaseId"] == release_id
        assert len(observed.endpoints) == 6


@pytest.mark.parametrize(
    "final_url",
    [
        gate.MARKET_CORPUS_CATALOG_URL,
        gate.MARKET_CORPUS_HEALTH_URL,
        gate.MARKET_CORPUS_CATALOG_URL + "?different=true",
        "https://elsewhere.invalid/api/v2/corpus/v1/catalog",
    ],
)
def test_frontend_runtime_transport_binds_the_final_response_route(
    monkeypatch, final_url
):
    class Response:
        headers = {"Content-Length": "2"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def geturl(self):
            return final_url

        def read(self, _bound):
            return b"{}"

    monkeypatch.setattr(
        front.urllib.request, "urlopen", lambda _request, **_kwargs: Response()
    )
    request = front.urllib.request.Request(gate.MARKET_CORPUS_CATALOG_URL)
    if final_url == gate.MARKET_CORPUS_CATALOG_URL:
        assert front._runtime_request(request)[0] == b"{}"
    else:
        with pytest.raises(front.Error, match="exact canonical route"):
            front._runtime_request(request)


@pytest.fixture
def frontend_site(tmp_path):
    root = tmp_path / "synthetic-site"
    root.mkdir()
    _content_git(root, "init", "-q")
    _content_git(root, "config", "user.name", "synthetic")
    _content_git(root, "config", "user.email", "fixture@example.invalid")
    _content_git(root, "config", "commit.gpgsign", "false")
    _content_commit(
        root,
        {
            "index.html": '<script type="module" src="./assets/old.js"></script>',
            "assets/old.js": "synthetic old UI",
            "data/overview.json": '{"as_of":"2000-01-01","status":"restricted"}',
            ".well-known/ai-catalog.json": '{"synthetic":true}',
            "dispatches/kept.md": "synthetic sealed evidence",
        },
    )
    before = site_proof.snapshot(root, tmp_path / "synthetic-rollback.tar")
    (root / "assets/new.js").write_text("synthetic new watchlist UI")
    (root / "index.html").write_text(
        '<script type="module" src="./assets/new.js"></script>'
    )
    return root, before


def test_frontend_site_retains_recovery_and_proves_exact_public_bytes(frontend_site):
    root, before = frontend_site
    manifest = site_proof.seal(root, before, source_sha="a" * 40)
    assert manifest["changed"] == ["assets/new.js", "index.html"]
    assert manifest["recoveryArchiveSha256"] == before["archiveSha256"]

    def fetch(url):
        from urllib.parse import urlsplit

        relative = urlsplit(url).path.lstrip("/") or "index.html"
        return (root / relative).read_bytes()

    proof = site_proof.verify_public(root, manifest, cache_key="synthetic", fetch=fetch)
    assert proof["status"] == "verified"
    with pytest.raises(site_proof.ProofError, match="public bytes differ"):
        site_proof.verify_public(
            root, manifest, cache_key="synthetic", fetch=lambda _url: b"old CDN content"
        )


@pytest.mark.parametrize(
    "relative",
    [
        "data/overview.json",
        ".well-known/ai-catalog.json",
        "dispatches/kept.md",
        "assets/old.js",
        "extra-page.html",
    ],
)
def test_frontend_site_rejects_sealed_data_catalog_and_existing_asset_mutations(
    frontend_site, relative
):
    root, before = frontend_site
    (root / relative).write_text("synthetic unauthorized replacement")
    with pytest.raises(site_proof.ProofError, match="sealed file|unauthorized file"):
        site_proof.seal(root, before, source_sha="a" * 40)


def test_frontend_site_rejects_symlinks_missing_assets_and_path_escape(frontend_site):
    root, before = frontend_site
    (root / "assets/escape.js").symlink_to("../../outside")
    with pytest.raises(site_proof.ProofError, match="unsafe site path"):
        site_proof.seal(root, before, source_sha="a" * 40)
    (root / "assets/escape.js").unlink()
    (root / "assets/new.js").unlink()
    with pytest.raises(site_proof.ProofError, match="missing local asset"):
        site_proof.seal(root, before, source_sha="a" * 40)
    manifest = {"schema": site_proof.SCHEMA, "publicFiles": {"../outside": "a" * 64}}
    with pytest.raises(site_proof.ProofError, match="unsafe path"):
        site_proof.verify_public(
            root, manifest, cache_key="synthetic", fetch=lambda _url: b""
        )


def test_frontend_workflow_retains_subject_archive_recovery_and_compare_and_swap():
    workflow = (ROOT / ".github/workflows/publish-static.yml").read_text()
    assert (
        'test "$FRONTEND_RECEIPT_TAG" = "frontend-publication-$GITHUB_SHA"' in workflow
    )
    assert (
        '"refs/tags/${FRONTEND_RECEIPT_TAG}:refs/tags/${FRONTEND_RECEIPT_TAG}"'
        in workflow
    )
    assert '"+refs/tags/${FRONTEND_RECEIPT_TAG}' not in workflow
    assert 'git -C "$GITHUB_WORKSPACE" archive "$GITHUB_SHA" frontend' in workflow
    assert 'cd "$RUNNER_TEMP/frontend-source/frontend"' in workflow
    assert 'rsync -a --delete dist/ "$GITHUB_WORKSPACE/frontend/dist/"' in workflow
    assert 'git archive "$backend_release_sha" backend | tar -x' in workflow
    assert (
        'renderer_backend="$RUNNER_TEMP/frontend-backend-subject/backend"' in workflow
    )
    assert 'python -I -S - "$renderer_backend"' in workflow
    assert 'python -I - "$renderer_backend"' in workflow
    assert (
        workflow.index("frontend_site_proof.py seal")
        < workflow.index("Retain verified frontend recovery")
        < workflow.index("Push static files to the live site repo")
    )
    assert "if-no-files-found: error" in workflow
    assert (
        'test "$(git rev-parse --verify refs/remotes/origin/main)" = "$GITHUB_SHA"'
        in workflow
    )
    assert "Site mirror compare-and-swap lost" in workflow
    assert "Stage exact mirror and refuse stale exact-head deployment" in workflow
    assert workflow.index(
        "Deploy static fast path to Cloudflare Pages"
    ) < workflow.index("frontend_site_proof.py public")
