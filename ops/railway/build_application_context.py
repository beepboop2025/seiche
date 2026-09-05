#!/usr/bin/env python3
"""Prepare a reviewed application image context; never submit a deployment."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import subprocess

from seiche import stateful_application as application
from seiche import stateful_cutover as cutover


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def render_dockerfile(source: str) -> str:
    marker = "COPY source.tar source.bundle request.json /migration/\n"
    if source.count(marker) != 1 or "COPY parent/" in source:
        raise ValueError("stateful image source marker changed; review before building")
    return source.replace(marker, marker + "COPY parent/ /migration/parent/\n")


def build_context(
    repo: Path, commit: str, parent_dir: Path, railway_path: Path, output: Path
) -> dict:
    if git(repo, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError("application source must be the exact checked-out commit")
    if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError("application source has tracked modifications")
    subprocess.run(["git", "-C", str(repo), "verify-commit", commit], check=True)
    parent = {
        name: application.read_document(parent_dir / f"{name}.json")
        for name in (
            "activation",
            "candidate",
            "shadow",
            "recovery-request",
            "recovery",
            "offsite",
        )
    }
    previous = parent["activation"]
    original = (
        previous["application"]["migration_activation"]
        if previous.get("schema") == application.ACTIVATION_SCHEMA
        else previous
    )
    if original.get("schema") != cutover.ACTIVATION_RECEIPT_SCHEMA:
        raise ValueError("parent migration activation is invalid")
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            previous["commit"],
            commit,
        ],
        check=True,
    )
    railway = application.validate_railway(
        application.read_document(railway_path), deployment=False
    )
    output.mkdir(mode=0o700)
    (output / "parent").mkdir(mode=0o700)
    for name, value in parent.items():
        (output / "parent" / f"{name}.json").write_bytes(application.canonical(value))
    archive = git(repo, "archive", "--format=tar", commit)
    (output / "source.tar").write_bytes(archive)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "bundle",
            "create",
            str(output / "source.bundle"),
            "HEAD",
        ],
        check=True,
    )
    if (
        git(repo, "bundle", "list-heads", str(output / "source.bundle"))
        .decode()
        .strip()
        != f"{commit} HEAD"
    ):
        raise ValueError("application bundle ref differs")
    now = datetime.now(UTC).replace(microsecond=0)
    request = {
        "schema": application.REQUEST_SCHEMA,
        "repository": "beepboop2025/seiche",
        "source_ref": "refs/heads/main",
        "operation": "application_upgrade",
        "commit": commit,
        "tree": git(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
        "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
        "source_bundle_sha256": application.migration.sha256_file(
            output / "source.bundle"
        ),
        "requested_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "railway": railway,
        "parent": {
            "commit": previous["commit"],
            "deployment_id": previous["railway"]["deployment_id"],
            "activation_request_id": previous["request_id"],
            "activation_sha256": application.digest(previous),
            "migration_activation_sha256": application.digest(original),
            "candidate_sha256": application.digest(parent["candidate"]),
            "shadow_sha256": application.digest(parent["shadow"]),
            "recovery_request_sha256": application.digest(parent["recovery-request"]),
            "recovery_sha256": application.digest(parent["recovery"]),
            "offsite_sha256": application.digest(parent["offsite"]),
            "generation": parent["candidate"]["filesystem"]["generation"],
            "database": parent["candidate"]["database"]["name"],
        },
    }
    request["request_id"] = application.digest(request)
    application.validate_request(request)
    (output / "request.json").write_bytes(application.canonical(request))
    previous_path = application.REQUEST_PATH
    try:
        application.REQUEST_PATH = output / "request.json"
        application.load_parent(request, current=True)
    finally:
        application.REQUEST_PATH = previous_path
    dockerfile = git(repo, "show", f"{commit}:ops/railway/Dockerfile.stateful").decode()
    (output / "Dockerfile").write_text(render_dockerfile(dockerfile))
    (output / "railway.json").write_bytes(
        git(repo, "show", f"{commit}:ops/railway/railway.cutover.json")
    )
    return request


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--parent-dir", type=Path, required=True)
    parser.add_argument("--railway-target", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_context(
        args.repo.resolve(),
        args.commit,
        args.parent_dir.resolve(),
        args.railway_target.resolve(),
        args.output.resolve(),
    )
    print(result["request_id"])
