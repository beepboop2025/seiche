"""Original-N3 offsite continuation rejects cross-run and mutable evidence."""

from __future__ import annotations

import base64
import copy
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import io
import json
import textwrap
from pathlib import Path
import zipfile

import pytest

from seiche import stateful_migration as migration
from seiche import stateful_recovery as recovery

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "resume_recovery", ROOT / "ops/railway/resume_recovery.py"
)
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)
NOW = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)


def controller() -> dict[str, str]:
    return {
        "GITHUB_RUN_ID": "33999999999",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": resume.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_SHA": "b" * 40,
        "GITHUB_WORKFLOW_REF": f"{resume.REPOSITORY}/{resume.WORKFLOW_PATH}@refs/heads/main",
        "RELEASE_SHA": resume.APPLICATION_SHA,
        "DEPLOYMENT_ID": resume.DEPLOYMENT_ID,
        "CONFIRMATION": "RESUME_ORIGINAL_N3_OFFSITE_WITHOUT_EXPORT",
    }


def official() -> tuple[dict, dict, dict]:
    identity = {
        "run_id": resume.ORIGINAL_RUN_ID,
        "run_attempt": 1,
        "head_sha": resume.APPLICATION_SHA,
        "head_branch": "main",
        "status": "completed",
    }
    run = {
        "id": resume.ORIGINAL_RUN_ID,
        "run_attempt": 1,
        "workflow_id": resume.ORIGINAL_WORKFLOW_ID,
        "head_sha": resume.APPLICATION_SHA,
        "head_branch": "main",
        "path": resume.WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "failure",
        "repository": {"id": resume.REPOSITORY_ID, "full_name": resume.REPOSITORY},
    }
    transitions = (
        (6, "Request and download an activation-bound portable export", "success"),
        (
            7,
            "Perform an isolated filesystem and PostgreSQL reverse-restore proof",
            "success",
        ),
        (
            8,
            "Seal the portable export in external S3 Object Lock compliance mode",
            "failure",
        ),
        (10, "OIDC-attest the exact recovery receipt", "skipped"),
        (11, "OIDC-attest the immutable off-site receipt", "skipped"),
        (12, "Retain bounded recovery failure diagnostics", "success"),
    )
    jobs = {
        "jobs": [
            {**identity, "name": "monitor", "conclusion": "success"},
            {
                **identity,
                "name": "export-recovery",
                "id": resume.ORIGINAL_JOB_ID,
                "conclusion": "failure",
                "steps": [
                    {
                        "number": n,
                        "name": name,
                        "conclusion": result,
                        "status": "completed",
                    }
                    for n, name, result in transitions
                ],
            },
        ]
    }
    artifact = {
        "id": resume.ARTIFACT_ID,
        "name": f"railway-recovery-failure-{resume.APPLICATION_SHA}-{resume.ORIGINAL_RUN_ID}-1",
        "size_in_bytes": resume.ARTIFACT_SIZE,
        "digest": f"sha256:{resume.ARTIFACT_SHA256}",
        "expired": False,
        "workflow_run": {
            "id": resume.ORIGINAL_RUN_ID,
            "repository_id": resume.REPOSITORY_ID,
            "head_repository_id": resume.REPOSITORY_ID,
            "head_branch": "main",
            "head_sha": resume.APPLICATION_SHA,
        },
    }
    return run, jobs, artifact


def storage() -> dict[str, str]:
    return {
        "S3_ENDPOINT": "https://storage.example.org",
        "S3_BUCKET": "recovery-bucket",
        "S3_PREFIX": "protected-prefix/railway",
    }


def offsite_fixture() -> tuple[dict, dict[str, bytes]]:
    """Synthetic metadata uses real resume identity; no backup or credential data."""
    state = {
        "active_activation_id": None,
        "pending_candidate_activation_id": None,
        "audit_schema": "seiche.palimpsest-china-activation-state.v1",
        "tree_sha256": "a" * 64,
    }
    receipt = {
        "schema": recovery.RECEIPT_SCHEMA,
        "commit": resume.APPLICATION_SHA,
        "request_id": resume.REQUEST_ID,
        "snapshot": {
            "id": resume.SNAPSHOT_ID,
            "inventory_sha256": "a" * 64,
            "member_sha256": {
                name: "b" * 64
                for name in resume.OBJECT_NAMES
                - resume.METADATA
                - {"SHA256SUMS", "proof/reverse-restore.json"}
            },
        },
        "railway": {"deployment_id": resume.DEPLOYMENT_ID},
        "palimpsest_china_state": state,
        "activation_receipt_sha256": "c" * 64,
        "candidate_receipt_sha256": "d" * 64,
        "shadow_receipt_sha256": "e" * 64,
        "request_sha256": "f" * 64,
        "timing": {"writers_restarted_at": "2026-09-05T17:37:07Z"},
    }
    original = {"recovery-receipt.json": migration.canonical_document(receipt)}
    hashes = {
        "activation-receipt.json": "c" * 64,
        "candidate-receipt.json": "d" * 64,
        "shadow-receipt.json": "e" * 64,
        "request.json": "f" * 64,
        "recovery-receipt.json": resume.digest(original["recovery-receipt.json"]),
        "SHA256SUMS": "a" * 64,
        "proof/reverse-restore.json": resume.ORIGINAL_HASHES[
            "proof/reverse-restore.json"
        ],
        **receipt["snapshot"]["member_sha256"],
    }
    value = {
        "schema": recovery.OFFSITE_RECEIPT_SCHEMA,
        "repository": resume.REPOSITORY,
        "workflow": f"{resume.REPOSITORY}/{resume.WORKFLOW_PATH}",
        "commit": resume.APPLICATION_SHA,
        "snapshot_id": resume.SNAPSHOT_ID,
        "request_id": resume.REQUEST_ID,
        "recovery_receipt_sha256": hashes["recovery-receipt.json"],
        "reverse_restore_proof_sha256": hashes["proof/reverse-restore.json"],
        "palimpsest_china_state": state,
        "bucket": storage()["S3_BUCKET"],
        "prefix": storage()["S3_PREFIX"],
        "object_lock_mode": "COMPLIANCE",
        "retain_until": "2026-12-04T17:49:07Z",
        "sealed_at": "2026-09-05T19:30:00Z",
        "authority_changed": False,
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
        "objects": {
            name: {
                "key": f"{resume.validate_location(storage())}/{name}",
                "version_id": "-leading-version",
                "sha256": sha256,
                "size": 100,
            }
            for name, sha256 in hashes.items()
        },
    }
    return value, original


def test_distinct_controller_and_original_official_run_are_accepted():
    resume.validate_controller(controller(), "b" * 40, "b" * 40)
    resume.validate_original_identity(*official())


@pytest.mark.parametrize(
    "field,value",
    [
        ("GITHUB_RUN_ID", str(resume.ORIGINAL_RUN_ID)),
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("GITHUB_RUN_ID", "1\n"),
        ("GITHUB_REPOSITORY", "fork/seiche"),
        ("GITHUB_REF", "refs/heads/rescue"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_SHA", "c" * 40),
        (
            "GITHUB_WORKFLOW_REF",
            "beepboop2025/seiche/.github/workflows/other.yml@refs/heads/main",
        ),
        ("RELEASE_SHA", "c" * 40),
        ("DEPLOYMENT_ID", "11111111-1111-4111-8111-111111111111"),
        ("REQUESTED_SOURCE_SHA", resume.APPLICATION_SHA),
        ("CONFIRMATION", "EXPORT_WITHOUT_AUTHORITY_CHANGE"),
    ],
)
def test_wrong_controller_or_application_is_rejected(field, value):
    environment = controller()
    environment[field] = value
    with pytest.raises(resume.ResumeError):
        resume.validate_controller(environment, "b" * 40, "b" * 40)


@pytest.mark.parametrize(
    "head,main",
    [
        (resume.APPLICATION_SHA, resume.APPLICATION_SHA),
        ("b" * 40, "c" * 40),
        ("x" * 40, "x" * 40),
    ],
)
def test_stale_or_invalid_controller_is_rejected(head, main):
    with pytest.raises(resume.ResumeError):
        resume.validate_controller(controller(), head, main)


@pytest.mark.parametrize(
    "index,path,value",
    [
        (0, ("id",), 1),
        (0, ("run_attempt",), 2),
        (0, ("head_sha",), "c" * 40),
        (0, ("path",), "different.yml"),
        (0, ("repository", "full_name"), "fork/seiche"),
        (0, ("workflow_id",), 1),
        (0, ("conclusion",), "success"),
        (1, ("jobs", 0, "conclusion"), "failure"),
        (1, ("jobs", 1, "steps", 0, "conclusion"), "failure"),
        (1, ("jobs", 1, "steps", 1, "conclusion"), "failure"),
        (1, ("jobs", 1, "steps", 2, "conclusion"), "success"),
        (1, ("jobs", 1, "run_attempt"), 2),
        (1, ("jobs", 1, "id"), 1),
        (2, ("id",), 1),
        (2, ("digest",), "sha256:" + "0" * 64),
        (2, ("expired",), True),
        (2, ("workflow_run", "head_sha"), "c" * 40),
        (2, ("workflow_run", "id"), 1),
        (2, ("size_in_bytes",), 100),
    ],
)
def test_official_identity_rejects_cross_run_evidence(index, path, value):
    documents = official()
    target = documents[index]
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    with pytest.raises(resume.ResumeError):
        resume.validate_original_identity(*documents)


def test_official_jobs_must_be_unique():
    run, jobs, artifact = official()
    jobs["jobs"].append(copy.deepcopy(jobs["jobs"][1]))
    with pytest.raises(resume.ResumeError):
        resume.validate_original_identity(run, jobs, artifact)


@pytest.mark.parametrize(
    "version,sha256,size",
    [
        ("", "a" * 64, 1),
        ("null", "a" * 64, 1),
        ("x\n", "a" * 64, 1),
        ("--file=@key", "a" * 64, 1),
        ("x", "G" * 64, 1),
        ("x", "a" * 63, 1),
        ("x", "a" * 64, True),
        ("x", "a" * 64, 0),
        ("x", "a" * 64, 5 * 1024**3 + 1),
    ],
)
def test_malformed_immutable_object_input_is_rejected(version, sha256, size):
    with pytest.raises(resume.ResumeError):
        resume.validate_object(version, sha256, size)


def test_leading_dash_version_is_supported():
    resume.validate_object("-3KC69jiWhU6", "a" * 64, 100)


@pytest.mark.parametrize(
    "field,value",
    [
        ("S3_ENDPOINT", "http://storage.example.org"),
        ("S3_ENDPOINT", "https://user:pass@storage.example.org"),
        ("S3_ENDPOINT", "https://storage.example.org/path"),
        ("S3_ENDPOINT", "https://test.up.railway.app"),
        ("S3_PREFIX", "prefix/../other"),
        ("S3_PREFIX", "prefix//other"),
        ("S3_BUCKET", ""),
    ],
)
def test_invalid_protected_storage_location_is_rejected(field, value):
    environment = storage()
    environment[field] = value
    with pytest.raises(resume.ResumeError):
        resume.validate_location(environment)


def test_closed_immutable_receipt_and_original_identity_are_accepted():
    value, original = offsite_fixture()
    assert (
        len(
            resume.validate_offsite(
                migration.canonical_document(value), original, storage(), now=NOW
            )["objects"]
        )
        == 15
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("commit",), "c" * 40),
        (("snapshot_id",), "20260905T172137Z"),
        (("request_id",), "a" * 64),
        (("bucket",), "other-bucket"),
        (("prefix",), "other-prefix"),
        (("authority_changed",), True),
        (("can_execute",), True),
        (("object_lock_mode",), "GOVERNANCE"),
        (("reverse_restore_proof_sha256",), "c" * 64),
        (
            ("objects", "seiche.dump", "key"),
            "protected-prefix/other-snapshot/seiche.dump",
        ),
        (("objects", "seiche.dump", "size"), True),
        (("objects", "seiche.dump", "version_id"), "null"),
        (("objects", "seiche.dump", "sha256"), "c" * 64),
        (("objects", "proof/reverse-restore.json", "sha256"), "c" * 64),
        (("sealed_at",), "2026-09-01T18:00:00Z"),
        (("sealed_at",), "2026-09-06T18:00:00Z"),
    ],
)
def test_receipt_rejects_tampering_and_cross_snapshot_objects(path, value):
    receipt, original = offsite_fixture()
    target = receipt
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value
    with pytest.raises((resume.ResumeError, recovery.RecoveryContractError)):
        resume.validate_offsite(
            migration.canonical_document(receipt), original, storage(), now=NOW
        )


def test_stale_and_noncanonical_receipts_are_rejected():
    value, original = offsite_fixture()
    body = migration.canonical_document(value)
    with pytest.raises(recovery.RecoveryContractError):
        resume.validate_offsite(
            body, original, storage(), now=NOW + timedelta(hours=27)
        )
    with pytest.raises(resume.ResumeError):
        resume.validate_offsite(
            json.dumps(value).encode(), original, storage(), now=NOW
        )
    value["objects"]["extra-object"] = copy.deepcopy(value["objects"]["seiche.dump"])
    with pytest.raises(recovery.RecoveryContractError):
        resume.validate_offsite(
            migration.canonical_document(value), original, storage(), now=NOW
        )


def test_wrong_original_live_deployment_is_rejected():
    value, original = offsite_fixture()
    changed = json.loads(original["recovery-receipt.json"])
    changed["railway"]["deployment_id"] = "other"
    original["recovery-receipt.json"] = migration.canonical_document(changed)
    with pytest.raises(resume.ResumeError, match="original application identity"):
        resume.validate_offsite(
            migration.canonical_document(value), original, storage(), now=NOW
        )


def test_original_proof_bytes_cannot_be_replaced(tmp_path):
    original = {
        name: migration.canonical_document({"original": name})
        for name in resume.ORIGINAL_HASHES
    }
    for name, body in original.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(body)
    resume.compare_original(tmp_path, original)
    (tmp_path / "proof/reverse-restore.json").write_bytes(b'{"new":"restore"}\n')
    with pytest.raises(resume.ResumeError, match="replaced original"):
        resume.compare_original(tmp_path, original)


def test_pinned_archive_and_member_hashes_are_enforced(monkeypatch):
    original = {
        name: migration.canonical_document({"original": name})
        for name in resume.ORIGINAL_HASHES
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in original.items():
            archive.writestr(name, body)
    body = output.getvalue()
    with pytest.raises(resume.ResumeError, match="artifact bytes differ"):
        resume.original_evidence(body)
    monkeypatch.setattr(resume, "ARTIFACT_SIZE", len(body))
    monkeypatch.setattr(resume, "ARTIFACT_SHA256", resume.digest(body))
    with pytest.raises(resume.ResumeError, match="proof digest differs"):
        resume.original_evidence(body)
    monkeypatch.setattr(
        resume,
        "ORIGINAL_HASHES",
        {name: resume.digest(value) for name, value in original.items()},
    )
    assert resume.original_evidence(body) == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("VersionId", "another-version"),
        ("ContentLength", 101),
        ("ContentLength", True),
        ("ObjectLockMode", "GOVERNANCE"),
        ("ObjectLockRetainUntilDate", "2026-09-06T20:00:00Z"),
        ("ObjectLockRetainUntilDate", "2026-12-04T20:00:00"),
        ("SSECustomerAlgorithm", "aws:kms"),
        ("SSECustomerKeyMD5", "wrong-key"),
    ],
)
def test_readback_rejects_wrong_version_size_retention_or_sse_key(field, value):
    key_md5 = base64.b64encode(
        hashlib.md5(b"x" * 32, usedforsecurity=False).digest()
    ).decode()
    head = {
        "VersionId": "-version",
        "ContentLength": 100,
        "Metadata": {"sha256": "a" * 64},
        "ObjectLockMode": "COMPLIANCE",
        "ObjectLockRetainUntilDate": "2026-12-04T20:00:00Z",
        "SSECustomerAlgorithm": "AES256",
        "SSECustomerKeyMD5": key_md5,
    }
    result = resume.validate_head(
        head, version="-version", sha256="a" * 64, size=100, key_md5=key_md5, now=NOW
    )
    assert "SSECustomerKeyMD5" not in result
    head[field] = value
    with pytest.raises(resume.ResumeError):
        resume.validate_head(
            head,
            version="-version",
            sha256="a" * 64,
            size=100,
            key_md5=key_md5,
            now=NOW,
        )


def test_size_and_local_download_hash_are_both_required(tmp_path):
    path = tmp_path / "download"
    path.write_bytes(b"original")
    sha256 = resume.digest(b"original")
    resume.verify_download(path, sha256=sha256, size=8)
    with pytest.raises(resume.ResumeError, match="size"):
        resume.verify_download(path, sha256=sha256, size=7)
    path.write_bytes(b"replaced")
    with pytest.raises(resume.ResumeError, match="digest"):
        resume.verify_download(path, sha256=sha256, size=8)


def test_workflow_preserves_main_environment_monitor_and_attestation_boundaries():
    workflow = (ROOT / resume.WORKFLOW_PATH).read_text()
    assert "group: railway-stateful-recovery\n  cancel-in-progress: false" in workflow
    job = workflow.split("\n  resume-offsite:\n", 1)[1]
    assert "github.ref == 'refs/heads/main'" in job
    assert "needs.monitor.result == 'success'" in job and "needs: monitor" in job
    assert "environment: railway-stateful-recovery-export" in job
    assert "      actions: read" in job and "      id-token: write" in job
    assert "      attestations: write" in job and "      contents: read" in job
    assert job.count("actions/attest-build-provenance@") == 2
    assert "subject-path: ${{ steps.resume.outputs.receipt_path }}" in job
    assert "subject-path: ${{ steps.offsite.outputs.receipt_path }}" in job
    restore = job.split("      - name: Repeat the isolated", 1)[1].split(
        "      - name:", 1
    )[0]
    assert 'Path("proof/resume-reverse-restore.json").write_text' in restore
    assert 'Path("proof/reverse-restore.json").write_text' not in restore
    assert (
        "restore_filesystem_generation" in restore
        and "pg_restore --exit-on-error" in restore
    )
    acknowledgment = job.split("      - name: Acknowledge the exact original", 1)[
        1
    ].split("      - name:", 1)[0]
    assert "OFFSITE_ACKNOWLEDGMENT_OPERATION" in acknowledgment
    assert (
        "recovery_request_sha256" in acknowledgment
        and "extract_log_result" in acknowledgment
    )
    assert "expected_offsite" in acknowledgment
    assert (
        "          S3_" not in acknowledgment and "          AWS_" not in acknowledgment
    )
    assert "put-verify" not in job and "publish_request" not in job
    assert "download_bearer" not in job and "EXPORT_OPERATION" not in job
    upload = job.split(
        "      - name: Retain the private portable-recovery evidence", 1
    )[1].split("      - name:", 1)[0]
    assert "name: railway-recovery-resumed-" in upload and "/bundle" not in upload
    failures = job.split(
        "      - name: Retain bounded continuation failure diagnostics", 1
    )[1]
    assert "/bundle" not in failures and "filesystem-restore" not in failures
    monitor_proof = workflow.split(
        "      - name: Record the accepted monitor identity", 1
    )[1].split("      - name:", 1)[0]
    assert '"bootstrap"' in monitor_proof and '"controller_commit"' in monitor_proof
    assert (
        "        if:" not in monitor_proof
    )  # Success-only, never bypass failed monitor checks.


def test_direct_aws_read_does_not_inherit_sse_key_and_uses_one_version_argument(
    tmp_path, monkeypatch
):
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "proof").mkdir()
    (root / "proof/resume-offsite-heads").mkdir()
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    payload = b"original-receipt"
    key = b"x" * 32
    environment = {
        **storage(),
        "S3_SSE_C_KEY_B64": base64.b64encode(key).decode(),
        "RUNNER_TEMP": str(temporary),
        "GITHUB_WORKSPACE": str(ROOT),
    }
    item = {
        "version_id": "-leading-version",
        "sha256": resume.digest(payload),
        "size": len(payload),
        "key": resume.validate_location(environment) + "/offsite-receipt.json",
    }
    commands = []

    def head(command, *, env, timeout):
        commands.append(command)
        assert "S3_SSE_C_KEY_B64" not in env
        assert "--version-id=-leading-version" in command
        assert "--version-id" not in command
        key_path = Path(
            command[command.index("--sse-customer-key") + 1].removeprefix("fileb://")
        )
        assert key_path.read_bytes() == key and key_path.stat().st_mode & 0o777 == 0o600
        return json.dumps(
            {
                "VersionId": item["version_id"],
                "ContentLength": len(payload),
                "Metadata": {"sha256": item["sha256"]},
                "SSECustomerAlgorithm": "AES256",
                "SSECustomerKeyMD5": base64.b64encode(
                    hashlib.md5(key, usedforsecurity=False).digest()
                ).decode(),
                "ObjectLockMode": "COMPLIANCE",
                "ObjectLockRetainUntilDate": (
                    datetime.now(UTC) + timedelta(days=90)
                ).isoformat(),
            }
        ).encode()

    def helper(command, *, env, timeout, check):
        commands.append(command)
        assert (
            command[1] == "get-verify"
            and env["S3_SSE_C_KEY_B64"] == environment["S3_SSE_C_KEY_B64"]
        )
        Path(command[-1]).write_bytes(payload)

    monkeypatch.setattr(resume.subprocess, "check_output", head)
    monkeypatch.setattr(resume.subprocess, "run", helper)
    resume.download_object(root, "offsite-receipt.json", item, environment)
    assert len(commands) == 2 and not list(temporary.iterdir())
    proof = json.loads(
        (root / "proof/resume-offsite-heads/offsite-receipt.json.json").read_bytes()
    )
    assert proof["DownloadedSHA256"] == item["sha256"]
    assert "SSECustomerKeyMD5" not in proof


@pytest.mark.parametrize("bootstrap", [True, False])
@pytest.mark.parametrize("tampered", [True, False])
def test_monitor_receipt_records_exact_selected_pair_or_explicit_bootstrap(
    tmp_path, monkeypatch, bootstrap, tampered
):
    root = tmp_path / "railway-recovery-monitor"
    root.mkdir()
    pair = {
        "paired_request_id": resume.REQUEST_ID,
        "recovery_receipt_sha256": resume.ORIGINAL_HASHES["recovery-receipt.json"],
        "offsite_receipt_sha256": "b" * 64,
    }
    if tampered:
        pair["offsite_receipt_sha256"] = "missing"
    (root / "monitor-pair-identity.json").write_text(json.dumps(pair))
    for name, value in {
        "RUNNER_TEMP": str(tmp_path),
        "BOOTSTRAP": str(bootstrap).lower(),
        "GITHUB_REPOSITORY": resume.REPOSITORY,
        "GITHUB_SHA": "b" * 40,
        "GITHUB_RUN_ID": "33999999999",
        "GITHUB_RUN_ATTEMPT": "1",
        "MONITORED_RELEASE": resume.APPLICATION_SHA,
        "MONITORED_DEPLOYMENT": resume.DEPLOYMENT_ID,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        resume.subprocess, "check_output", lambda *a, **kw: "b" * 40 + "\n"
    )
    workflow = (ROOT / resume.WORKFLOW_PATH).read_text()
    block = workflow.split("python3 -I -S - <<'PYMONITOR'\n", 1)[1].split(
        "          PYMONITOR", 1
    )[0]
    program = compile(textwrap.dedent(block), "monitor-receipt", "exec")
    if tampered and not bootstrap:
        with pytest.raises(SystemExit, match="pair identity is invalid"):
            exec(program, {})
        assert not (root / "monitor-proof.json").exists()
    else:
        exec(program, {})
        proof = json.loads((root / "monitor-proof.json").read_bytes())
        assert proof["bootstrap"] is bootstrap and type(proof["run_id"]) is int
        assert proof["controller_commit"] == "b" * 40
        for field in pair:
            assert proof[field] == (None if bootstrap else pair[field])
