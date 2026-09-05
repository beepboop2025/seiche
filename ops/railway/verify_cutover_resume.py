#!/usr/bin/env python3
"""Bind an existing candidate to the failed main run that deployed it."""

from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

REPOSITORY = "beepboop2025/seiche"
WORKFLOW = ".github/workflows/railway-stateful-cutover.yml"
WAIT_STEP = "Wait for the exact candidate deployment"
REQUIRED_STEPS = (
    "Authenticate the signed application source independently of the workflow",
    "Prove the fenced source and isolated Railway target",
    "Build the canonical cutover candidate request",
    "Deploy the exact read-only candidate",
)


def canonical_request(encoded: str) -> tuple[bytes, dict]:
    if len(encoded) > 16384:
        raise ValueError("resume request exceeds capacity")
    raw = base64.b64decode(encoded, validate=True)
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or raw
        != (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode()
    ):
        raise ValueError("resume request is not canonical")
    return raw, value


def moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("resume timestamp is not UTC")
    return parsed


def validate_binding(
    request: dict,
    run: dict,
    jobs: dict,
    logs: str,
    deployment: dict,
    *,
    source: str,
    project: str,
    environment: str,
    service: str,
) -> str:
    if (
        run.get("event") != "workflow_dispatch"
        or run.get("head_branch") != "main"
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or run.get("path", "").split("@")[0] != WORKFLOW
        or run.get("head_repository", {}).get("full_name") != REPOSITORY
        or re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha"))) is None
    ):
        raise ValueError("resume source run identity is invalid")
    for field in ("id", "run_attempt"):
        if type(run.get(field)) is not int or run[field] <= 0:
            raise ValueError("resume source run number is invalid")
    if request.get("commit") != source:
        raise ValueError("resume application differs")
    identity = (
        f"{REPOSITORY}:{run['id']}:{run['run_attempt']}:{source}:"
        f"{request['snapshot_id']}:cutover-candidate\n"
    )
    if request.get("request_id") != hashlib.sha256(identity.encode()).hexdigest():
        raise ValueError("resume request belongs to another run or attempt")
    candidates = [
        j for j in jobs.get("jobs", []) if j.get("name") == "restore-candidate"
    ]
    if len(candidates) != 1:
        raise ValueError("resume source job is not unique")
    job = candidates[0]
    if job.get("conclusion") != "failure" or job.get("run_id") != run["id"]:
        raise ValueError("resume source job identity differs")
    steps = job.get("steps", [])
    for name in REQUIRED_STEPS:
        selected = [s for s in steps if s.get("name") == name]
        if len(selected) != 1 or selected[0].get("conclusion") != "success":
            raise ValueError("source candidate was not successfully submitted")
    failed = [s for s in steps if s.get("conclusion") == "failure"]
    if len(failed) != 1 or failed[0].get("name") != WAIT_STEP:
        raise ValueError("resume is limited to a failed deployment read wait")
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", logs)
    observed = re.findall(
        r"(?m)^restore-candidate\tWait for the exact candidate deployment\t"
        r"[^\n]*? DEPLOYMENT_ID: ([0-9a-f-]{36})\s*$",
        clean,
    )
    did = deployment.get("id")
    if (
        observed != [did]
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            str(did),
        )
        is None
    ):
        raise ValueError("resume deployment differs from original job log")
    if (
        deployment.get("projectId") != project
        or deployment.get("environmentId") != environment
        or deployment.get("serviceId") != service
    ):
        raise ValueError("resume deployment scope differs")
    start, end = moment(run["created_at"]), moment(job["completed_at"])
    if (
        not start
        <= moment(request["requested_at"])
        <= moment(deployment["createdAt"])
        <= end
    ):
        raise ValueError("resume deployment was not created by this run")
    return run["created_at"]


def main() -> None:
    from seiche.stateful_cutover import validate_fence, validate_request

    root = Path(os.environ["UPLOAD_ROOT"])
    raw, request = canonical_request(os.environ["RESUME_REQUEST"])
    fence = validate_fence(json.loads(Path(os.environ["FENCE_PATH"]).read_bytes()))
    validate_request(request, fence=fence)
    run_id = os.environ["RESUME_RUN_ID"]
    if re.fullmatch(r"[1-9][0-9]{0,19}", run_id) is None:
        raise ValueError("resume run ID is invalid")

    def capture(arguments: list[str]) -> str:
        return subprocess.check_output(arguments, text=True)

    run = json.loads(
        capture(["gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}"])
    )
    if run.get("id") != int(run_id):
        raise ValueError("resume run API identity differs")
    old = run.get("head_sha", "")
    if re.fullmatch(r"[0-9a-f]{40}", old) is None:
        raise ValueError("resume controller identity is malformed")
    signer = Path(os.environ["GITHUB_WORKSPACE"]) / "ops/deploy/release-allowed-signers"
    subprocess.run(
        [
            "git",
            "-c",
            "gpg.format=ssh",
            "-c",
            f"gpg.ssh.allowedSignersFile={signer}",
            "verify-commit",
            old,
        ],
        check=True,
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", old, os.environ["GITHUB_SHA"]],
        check=True,
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", os.environ["SOURCE_SHA"], old],
        check=True,
    )
    jobs = json.loads(
        capture(
            [
                "gh",
                "api",
                f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100",
            ]
        )
    )
    logs = capture(
        [
            "gh",
            "run",
            "view",
            run_id,
            "--repo",
            REPOSITORY,
            "--attempt",
            str(run["run_attempt"]),
            "--log",
        ]
    )
    did = os.environ["RESUME_DEPLOYMENT_ID"]
    if re.fullmatch(r"[0-9a-f-]{36}", did) is None:
        raise ValueError("resume deployment ID is malformed")
    query = "query($id:String!){deployment(id:$id){id projectId environmentId serviceId createdAt}}"
    deployment = json.loads(
        capture(["railway", "api", query, "--variables", json.dumps({"id": did})])
    )["data"]["deployment"]
    boundary = validate_binding(
        request,
        run,
        jobs,
        logs,
        deployment,
        source=os.environ["SOURCE_SHA"],
        project=os.environ["RAILWAY_PROJECT_ID"],
        environment=os.environ["RAILWAY_ENVIRONMENT_ID"],
        service=os.environ["RAILWAY_SERVICE_ID"],
    )
    if request["tree"] != capture(["git", "rev-parse", "HEAD^{tree}"]).strip():
        raise ValueError("resume source tree differs")
    (root / "request.json").write_bytes(raw)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for name, value in {
            "request_id": request["request_id"],
            "target": os.environ["SOURCE_SHA"],
            "upload_root": str(root),
            "resume_deployment_id": did,
            "resume_not_before": boundary,
        }.items():
            stream.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
