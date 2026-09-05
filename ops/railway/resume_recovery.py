#!/usr/bin/env python3
"""Read-only, one-off continuation of the original N3 offsite recovery.

The new main controller proves an old application snapshot; it cannot export a
new snapshot, upload S3 objects, or change application authority. The workflow
performs another isolated restore and the existing governed acknowledgment only
after this module has verified the original official failure and immutable bytes.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
import zipfile

from seiche import stateful_migration as migration
from seiche import stateful_recovery as recovery

REPOSITORY = "beepboop2025/seiche"
REPOSITORY_ID = 1291299671
WORKFLOW_PATH = ".github/workflows/railway-stateful-recovery.yml"
APPLICATION_SHA = "f092bf2c5880065c97f40be5afb7805f91bcd0b0"
DEPLOYMENT_ID = "f18f0c80-89cf-41da-8049-c0b1c23efe35"
SNAPSHOT_ID = "20260905T172136Z"
REQUEST_ID = "a61ed579b9f69ef6820c6e57fe9d75e4f6e77b44065c7cf12c9b34f3be15c865"
ORIGINAL_RUN_ID = 33980619784
ORIGINAL_ATTEMPT = 1
ORIGINAL_JOB_ID = 101345130708
ORIGINAL_WORKFLOW_ID = 340451796
ARTIFACT_ID = 9974082687
ARTIFACT_SHA256 = "bde024641e05ac60b418cb73c183d4d2cbb34610f7d029b844b8648638f559fe"
ARTIFACT_SIZE = 14742
ORIGINAL_HASHES = {
    "request.json": "d36240c96c4340c25d58ffdfbfd6fae1696d648d3aa153ee3facc6e4cd96afe9",
    "recovery-receipt.json": "85df6afa6f059c597a9af9ad2fd5cb3b3073d1ee8f37467730e5224e484c241d",
    "proof/reverse-restore.json": "07f555a4ae24baeb4dadb9bbd71bd15f599614d6307b21bc3f43e130e25d37b7",
}
METADATA = frozenset(
    {
        "activation-receipt.json",
        "candidate-receipt.json",
        "shadow-receipt.json",
        "request.json",
        "recovery-receipt.json",
    }
)
OBJECT_NAMES = METADATA | frozenset(
    {
        "SHA256SUMS",
        "seiche.dump",
        "var-lib-seiche.tgz",
        "palimpsest-china.tgz",
        "palimpsest-china-state.json",
        "api-data.tgz",
        "table-counts.txt",
        "deployed-sha.txt",
        "manifest.env",
        "proof/reverse-restore.json",
    }
)


class ResumeError(ValueError):
    """The original recovery cannot be continued with this evidence."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ResumeError(message)


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(body: bytes) -> dict:
    value = json.loads(body)
    require(isinstance(value, dict), "receipt must be an object")
    require(migration.canonical_document(value) == body, "receipt is not canonical")
    return value


def validate_controller(environment: dict[str, str], head: str, main: str) -> None:
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(
            re.fullmatch(r"[1-9][0-9]{0,19}", environment.get(name, "")) is not None,
            "controller run identity is invalid",
        )
    require(
        int(environment["GITHUB_RUN_ID"]) != ORIGINAL_RUN_ID,
        "controller is the original failed run",
    )
    require(
        environment.get("GITHUB_REPOSITORY") == REPOSITORY,
        "controller repository differs",
    )
    require(
        environment.get("GITHUB_REF") == "refs/heads/main",
        "controller must run on main",
    )
    require(
        environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
        "controller must be dispatched",
    )
    require(
        environment.get("GITHUB_WORKFLOW_REF")
        == f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/main",
        "controller workflow differs",
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", head) is not None and head != APPLICATION_SHA,
        "controller SHA is invalid or still the original application",
    )
    require(
        head == main == environment.get("GITHUB_SHA"),
        "controller is not exact current main",
    )
    require(
        environment.get("REQUESTED_SOURCE_SHA", "") in {"", head},
        "requested controller differs",
    )
    require(
        environment.get("RELEASE_SHA") == APPLICATION_SHA,
        "live application source differs",
    )
    require(
        environment.get("DEPLOYMENT_ID") == DEPLOYMENT_ID, "live deployment differs"
    )
    require(
        environment.get("CONFIRMATION") == "RESUME_ORIGINAL_N3_OFFSITE_WITHOUT_EXPORT",
        "resume confirmation differs",
    )


def validate_original_identity(run: dict, jobs: dict, artifact: dict) -> None:
    expected = {
        "id": ORIGINAL_RUN_ID,
        "run_attempt": ORIGINAL_ATTEMPT,
        "workflow_id": ORIGINAL_WORKFLOW_ID,
        "head_sha": APPLICATION_SHA,
        "head_branch": "main",
        "path": WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "failure",
    }
    require(
        all(run.get(key) == value for key, value in expected.items()),
        "original run identity differs",
    )
    require(
        run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("repository", {}).get("id") == REPOSITORY_ID,
        "original repository differs",
    )
    rows = jobs.get("jobs", [])
    selected = [row for row in rows if row.get("name") == "export-recovery"]
    monitors = [row for row in rows if row.get("name") == "monitor"]
    require(len(selected) == len(monitors) == 1, "original jobs are ambiguous")
    for job, conclusion in ((selected[0], "failure"), (monitors[0], "success")):
        require(
            all(
                job.get(k) == v
                for k, v in {
                    "run_id": ORIGINAL_RUN_ID,
                    "run_attempt": ORIGINAL_ATTEMPT,
                    "head_sha": APPLICATION_SHA,
                    "head_branch": "main",
                    "status": "completed",
                    "conclusion": conclusion,
                }.items()
            ),
            "original job identity differs",
        )
    require(selected[0].get("id") == ORIGINAL_JOB_ID, "original export job differs")
    for number, name, conclusion in (
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
    ):
        steps = [
            step
            for step in selected[0].get("steps", [])
            if step.get("number") == number
        ]
        require(
            len(steps) == 1
            and steps[0].get("name") == name
            and steps[0].get("status") == "completed"
            and steps[0].get("conclusion") == conclusion,
            "original export/restore/offsite transition differs",
        )
    require(
        all(
            artifact.get(key) == value
            for key, value in {
                "id": ARTIFACT_ID,
                "name": f"railway-recovery-failure-{APPLICATION_SHA}-{ORIGINAL_RUN_ID}-1",
                "size_in_bytes": ARTIFACT_SIZE,
                "digest": f"sha256:{ARTIFACT_SHA256}",
                "expired": False,
            }.items()
        ),
        "official failure artifact identity differs",
    )
    require(
        all(
            artifact.get("workflow_run", {}).get(key) == value
            for key, value in {
                "id": ORIGINAL_RUN_ID,
                "repository_id": REPOSITORY_ID,
                "head_repository_id": REPOSITORY_ID,
                "head_branch": "main",
                "head_sha": APPLICATION_SHA,
            }.items()
        ),
        "failure artifact belongs to another run",
    )


def original_evidence(archive_body: bytes) -> dict[str, bytes]:
    require(
        len(archive_body) == ARTIFACT_SIZE and digest(archive_body) == ARTIFACT_SHA256,
        "failure artifact bytes differ",
    )
    with zipfile.ZipFile(io.BytesIO(archive_body)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "duplicate failure artifact member")
        result = {}
        for name, expected in ORIGINAL_HASHES.items():
            require(name in names, "original failure evidence is missing")
            member = archive.getinfo(name)
            require(member.file_size <= 512 * 1024, "original evidence exceeds bound")
            body = archive.read(member)
            require(digest(body) == expected, "original failure proof digest differs")
            canonical(body)
            result[name] = body
        return result


def validate_object(
    version: object, sha256: object, size: object, *, maximum: int = 5 * 1024**3
) -> None:
    require(
        isinstance(version, str)
        and version != "null"
        and re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,1024}", version) is not None,
        "object version is invalid",
    )
    require(
        isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256) is not None,
        "object SHA-256 is invalid",
    )
    require(type(size) is int and 0 < size <= maximum, "object size is invalid")


def validate_location(environment: dict[str, str]) -> str:
    endpoint = urlsplit(environment["S3_ENDPOINT"])
    require(
        endpoint.scheme == "https"
        and bool(endpoint.hostname)
        and endpoint.username is None
        and endpoint.password is None
        and endpoint.path == ""
        and not endpoint.query
        and not endpoint.fragment
        and not endpoint.hostname.endswith(".railway.app"),
        "offsite endpoint is invalid",
    )
    require(
        re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,62}", environment["S3_BUCKET"]) is not None,
        "offsite bucket is invalid",
    )
    prefix = environment["S3_PREFIX"]
    require(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,200}", prefix) is not None
        and not prefix.endswith("/")
        and ".." not in prefix.split("/")
        and "" not in prefix.split("/"),
        "offsite prefix is invalid",
    )
    return f"{prefix}/{SNAPSHOT_ID}/{REQUEST_ID}"


def validate_offsite(
    body: bytes,
    original: dict[str, bytes],
    environment: dict[str, str],
    *,
    now: datetime | None = None,
) -> dict:
    value = canonical(body)
    original_receipt = canonical(original["recovery-receipt.json"])
    require(
        original_receipt.get("commit") == APPLICATION_SHA
        and original_receipt.get("request_id") == REQUEST_ID
        and original_receipt.get("snapshot", {}).get("id") == SNAPSHOT_ID
        and original_receipt.get("railway", {}).get("deployment_id") == DEPLOYMENT_ID,
        "original application identity differs",
    )
    recovery.validate_offsite_receipt(value, recovery_receipt=original_receipt, now=now)
    require(
        value["bucket"] == environment["S3_BUCKET"]
        and value["prefix"] == environment["S3_PREFIX"],
        "receipt does not use protected storage location",
    )
    require(set(value["objects"]) == OBJECT_NAMES, "resume object set differs")
    require(
        value["reverse_restore_proof_sha256"]
        == ORIGINAL_HASHES["proof/reverse-restore.json"],
        "immutable receipt replaced the original restore proof",
    )
    key_root = validate_location(environment)
    for name, item in value["objects"].items():
        require(
            item["key"] == f"{key_root}/{name}",
            "object key is outside the original recovery",
        )
        validate_object(item["version_id"], item["sha256"], item["size"])
    return value


def validate_head(
    head: dict, *, version: str, sha256: str, size: int, key_md5: str, now: datetime
) -> dict:
    raw_retention = head.get("ObjectLockRetainUntilDate")
    require(isinstance(raw_retention, str), "retention timestamp is missing")
    retained = datetime.fromisoformat(raw_retention.replace("Z", "+00:00"))
    require(
        retained.tzinfo is not None and retained.utcoffset() is not None,
        "retention timestamp lacks timezone",
    )
    require(
        head.get("VersionId") == version
        and type(head.get("ContentLength")) is int
        and head["ContentLength"] == size
        and head.get("Metadata", {}).get("sha256") == sha256
        and head.get("ObjectLockMode") == "COMPLIANCE"
        and head.get("SSECustomerAlgorithm") == "AES256"
        and head.get("SSECustomerKeyMD5") == key_md5
        and retained >= now + timedelta(days=29),
        "versioned retention/size/SSE-C proof differs",
    )
    # Retain only bounded verification fields, never the customer's key fingerprint.
    return {
        "VersionId": version,
        "ContentLength": size,
        "Metadata": {"sha256": sha256},
        "ObjectLockMode": "COMPLIANCE",
        "ObjectLockRetainUntilDate": raw_retention,
        "SSECustomerAlgorithm": "AES256",
        "SSECustomerKeyVerified": True,
    }


def verify_download(path: Path, *, sha256: str, size: int) -> None:
    require(
        path.is_file() and not path.is_symlink() and path.stat().st_size == size,
        "downloaded object size differs",
    )
    with path.open("rb") as stream:
        require(
            hashlib.file_digest(stream, "sha256").hexdigest() == sha256,
            "downloaded object digest differs",
        )


def compare_original(root: Path, original: dict[str, bytes]) -> None:
    for name, body in original.items():
        require(
            (root / name).read_bytes() == body,
            "download replaced original request/recovery/restore bytes",
        )


def github_api(path: str) -> bytes:
    return subprocess.check_output(
        ["gh", "api", f"repos/{REPOSITORY}/{path}"], timeout=90
    )


def download_object(
    root: Path, name: str, item: dict, environment: dict[str, str]
) -> None:
    destination = root / (
        name
        if name in METADATA
        or name.startswith("proof/")
        or name == "offsite-receipt.json"
        else f"bundle/{name}"
    )
    validate_object(item["version_id"], item["sha256"], item["size"])
    key = base64.b64decode(environment["S3_SSE_C_KEY_B64"], validate=True)
    require(
        len(key) == 32
        and base64.b64encode(key).decode() == environment["S3_SSE_C_KEY_B64"],
        "SSE-C key is invalid",
    )
    with tempfile.TemporaryDirectory(
        prefix="seiche-resume-sse-", dir=environment["RUNNER_TEMP"]
    ) as temporary:
        key_path = Path(temporary) / "key"
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        key_md5 = base64.b64encode(
            hashlib.md5(key, usedforsecurity=False).digest()
        ).decode()
        aws_environment = dict(environment)
        aws_environment.pop("S3_SSE_C_KEY_B64", None)
        head = json.loads(
            subprocess.check_output(
                [
                    "aws",
                    "--endpoint-url",
                    environment["S3_ENDPOINT"],
                    "--no-cli-pager",
                    "s3api",
                    "head-object",
                    "--bucket",
                    environment["S3_BUCKET"],
                    "--key",
                    item["key"],
                    f"--version-id={item['version_id']}",
                    "--sse-customer-algorithm",
                    "AES256",
                    "--sse-customer-key",
                    f"fileb://{key_path}",
                ],
                env=aws_environment,
                timeout=90,
            )
        )
        proof = validate_head(
            head,
            version=item["version_id"],
            sha256=item["sha256"],
            size=item["size"],
            key_md5=key_md5,
            now=datetime.now(UTC),
        )
    helper = (
        Path(environment["GITHUB_WORKSPACE"]) / "ops/deploy/seiche-s3-object-lock.sh"
    )
    subprocess.run(
        [
            str(helper),
            "get-verify",
            item["key"],
            item["version_id"],
            item["sha256"],
            str(destination),
        ],
        check=True,
        env=environment,
        timeout=1200,
    )
    verify_download(destination, sha256=item["sha256"], size=item["size"])
    proof["DownloadedSHA256"] = item["sha256"]
    (
        root / "proof/resume-offsite-heads" / f"{name.replace('/', '_')}.json"
    ).write_bytes(migration.canonical_document(proof))


def main() -> None:
    environment = dict(os.environ)
    environment.update(
        AWS_EC2_METADATA_DISABLED="true",
        AWS_REQUEST_CHECKSUM_CALCULATION="when_required",
        AWS_RESPONSE_CHECKSUM_VALIDATION="when_required",
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    remote = subprocess.check_output(
        ["git", "ls-remote", "origin", "refs/heads/main"], text=True
    ).split()
    require(len(remote) == 2, "main identity is ambiguous")
    validate_controller(environment, head, remote[0])
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", APPLICATION_SHA, head], check=True
    )
    require(
        not subprocess.check_output(
            ["git", "status", "--short", "--untracked-files=no"]
        ),
        "controller checkout is dirty",
    )
    version = environment["OFFSITE_RECEIPT_VERSION"]
    sha256 = environment["OFFSITE_RECEIPT_SHA256"]
    raw_size = environment["OFFSITE_RECEIPT_SIZE"]
    require(
        re.fullmatch(r"[1-9][0-9]{0,5}", raw_size) is not None,
        "receipt size input is invalid",
    )
    size = int(raw_size)
    validate_object(version, sha256, size, maximum=512 * 1024)
    key_root = validate_location(environment)
    root = Path(environment["EVIDENCE_ROOT"])
    root.mkdir(mode=0o700)
    for directory in (
        "bundle",
        "proof",
        "proof/resume-offsite-heads",
        "proof/original-failure",
        "proof/original-failure/proof",
    ):
        (root / directory).mkdir(mode=0o700)
    responses = {
        "run": github_api(
            f"actions/runs/{ORIGINAL_RUN_ID}/attempts/{ORIGINAL_ATTEMPT}"
        ),
        "jobs": github_api(
            f"actions/runs/{ORIGINAL_RUN_ID}/attempts/{ORIGINAL_ATTEMPT}/jobs?per_page=100"
        ),
        "artifact": github_api(f"actions/artifacts/{ARTIFACT_ID}"),
    }
    validate_original_identity(
        *(json.loads(responses[name]) for name in ("run", "jobs", "artifact"))
    )
    for name, body in responses.items():
        (root / "proof" / f"original-{name}.json").write_bytes(body)
    archive = github_api(f"actions/artifacts/{ARTIFACT_ID}/zip")
    original = original_evidence(archive)
    (root / "proof/original-failure-artifact.zip").write_bytes(archive)
    for name, body in original.items():
        (root / "proof/original-failure" / name).write_bytes(body)
    download_object(
        root,
        "offsite-receipt.json",
        {
            "key": f"{key_root}/offsite-receipt.json",
            "version_id": version,
            "sha256": sha256,
            "size": size,
        },
        environment,
    )
    offsite = validate_offsite(
        (root / "offsite-receipt.json").read_bytes(), original, environment
    )
    for name, item in sorted(offsite["objects"].items()):
        download_object(root, name, item, environment)
    compare_original(root, original)
    receipt = canonical((root / "recovery-receipt.json").read_bytes())
    recovery.validate_receipt(
        receipt,
        request=canonical((root / "request.json").read_bytes()),
        activation_receipt=canonical((root / "activation-receipt.json").read_bytes()),
        candidate_receipt=canonical((root / "candidate-receipt.json").read_bytes()),
        shadow_receipt=canonical((root / "shadow-receipt.json").read_bytes()),
        bundle_root=root / "bundle",
    )
    proof = {
        "schema": "seiche.original-n3-recovery-continuation.v1",
        "repository": REPOSITORY,
        "workflow": f"{REPOSITORY}/{WORKFLOW_PATH}",
        "controller_commit": head,
        "controller_run_id": int(environment["GITHUB_RUN_ID"]),
        "controller_run_attempt": int(environment["GITHUB_RUN_ATTEMPT"]),
        "application_commit": APPLICATION_SHA,
        "deployment_id": DEPLOYMENT_ID,
        "snapshot_id": SNAPSHOT_ID,
        "request_id": REQUEST_ID,
        "original_run_id": ORIGINAL_RUN_ID,
        "original_run_attempt": ORIGINAL_ATTEMPT,
        "original_failure_artifact_id": ARTIFACT_ID,
        "original_failure_artifact_sha256": ARTIFACT_SHA256,
        "offsite_receipt_sha256": sha256,
        "offsite_receipt_version_id": version,
        "offsite_receipt_size": size,
        "original_evidence_sha256": ORIGINAL_HASHES,
        "downloaded_object_count": len(offsite["objects"]),
        "new_export_requested": False,
        "object_uploads": False,
        "authority_changed": False,
    }
    (root / "proof/resume-lineage.json").write_bytes(
        migration.canonical_document(proof)
    )
    with Path(environment["GITHUB_OUTPUT"]).open("a") as stream:
        for name, value in {
            "evidence_root": root,
            "snapshot_id": SNAPSHOT_ID,
            "request_id": REQUEST_ID,
            "receipt_path": root / "recovery-receipt.json",
        }.items():
            stream.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
