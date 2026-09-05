"""A failed provider read must not allow another deployment to be adopted."""

import base64
import hashlib
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESUME = runpy.run_path(str(ROOT / "ops/railway/verify_cutover_resume.py"))
RETRY = ROOT / "ops/railway/retry_read.py"
DID = "11111111-1111-4111-8111-111111111111"
SOURCE = "a" * 40


def fixture():
    request = {
        "commit": SOURCE,
        "snapshot_id": "20260905T025038Z",
        "requested_at": "2026-09-05T03:01:00Z",
    }
    request["request_id"] = hashlib.sha256(
        f"beepboop2025/seiche:123:1:{SOURCE}:{request['snapshot_id']}:cutover-candidate\n".encode()
    ).hexdigest()
    run = {
        "id": 123,
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "failure",
        "path": RESUME["WORKFLOW"],
        "head_repository": {"full_name": "beepboop2025/seiche"},
        "head_sha": "b" * 40,
        "created_at": "2026-09-05T03:00:00Z",
    }
    jobs = {
        "jobs": [
            {
                "name": "restore-candidate",
                "run_id": 123,
                "conclusion": "failure",
                "completed_at": "2026-09-05T03:10:00Z",
                "steps": [
                    *(
                        {"name": name, "conclusion": "success"}
                        for name in RESUME["REQUIRED_STEPS"]
                    ),
                    {"name": RESUME["WAIT_STEP"], "conclusion": "failure"},
                ],
            }
        ]
    }
    logs = f"restore-candidate\t{RESUME['WAIT_STEP']}\t2026-09-05T03:02:00Z   DEPLOYMENT_ID: {DID}\n"
    deployment = {
        "id": DID,
        "projectId": "project",
        "environmentId": "environment",
        "serviceId": "service",
        "createdAt": "2026-09-05T03:02:00Z",
    }
    return request, run, jobs, logs, deployment


def check(values):
    return RESUME["validate_binding"](
        *values,
        source=SOURCE,
        project="project",
        environment="environment",
        service="service",
    )


def test_resume_binds_original_run_request_and_deployment():
    assert check(fixture()) == "2026-09-05T03:00:00Z"


def receipt_failure_fixture():
    values = fixture()
    steps = values[2]["jobs"][0]["steps"]
    steps[-1]["conclusion"] = "success"
    steps.extend([
        {"name": RESUME["RUNTIME_STEP"], "conclusion": "success"},
        {"name": RESUME["RECEIPT_STEP"], "conclusion": "failure"},
    ])
    return values


def test_resume_receipt_failure_preserves_original_request_and_deployment():
    assert check(receipt_failure_fixture()) == "2026-09-05T03:00:00Z"
    values = receipt_failure_fixture()
    values[4]["id"] = "22222222-2222-4222-8222-222222222222"
    with pytest.raises(ValueError, match="differs from original job log"):
        check(values)


@pytest.mark.parametrize("name", [RESUME["WAIT_STEP"], RESUME["RUNTIME_STEP"]])
@pytest.mark.parametrize("conclusion", ["skipped", "failure", "cancelled"])
def test_receipt_resume_requires_all_prior_runtime_proofs(name, conclusion):
    values = receipt_failure_fixture()
    for step in values[2]["jobs"][0]["steps"]:
        if step["name"] == name:
            step["conclusion"] = conclusion
    with pytest.raises(ValueError):
        check(values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 124),
        ("run_attempt", 2),
        ("head_branch", "unreviewed"),
        ("event", "pull_request"),
        ("conclusion", "success"),
        ("head_sha", "unsafe"),
        ("path", ".github/workflows/unrelated.yml"),
        ("head_repository", {"full_name": "attacker/seiche"}),
    ],
)
def test_resume_rejects_foreign_or_changed_run(field, value):
    values = list(fixture())
    values[1][field] = value
    with pytest.raises(ValueError):
        check(values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "22222222-2222-4222-8222-222222222222"),
        ("projectId", "other"),
        ("environmentId", "other"),
        ("serviceId", "other"),
        ("createdAt", "2026-09-05T02:59:00Z"),
        ("createdAt", "2026-09-05T03:11:00Z"),
    ],
)
def test_resume_rejects_deployment_not_submitted_by_original_run(field, value):
    values = list(fixture())
    values[4][field] = value
    with pytest.raises(ValueError):
        check(values)


def test_resume_refuses_missing_or_duplicate_job_log_identity():
    for logs in ("", fixture()[3] * 2):
        values = list(fixture())
        values[3] = logs
        with pytest.raises(ValueError):
            check(values)


def test_resume_refuses_failed_submission_and_another_failure_step():
    for index in (0, 3, 4):
        values = list(fixture())
        step = values[2]["jobs"][0]["steps"][index]
        if index == 4:
            step["name"] = "A different failure"
        else:
            step["conclusion"] = "failure"
        with pytest.raises(ValueError):
            check(values)


def test_resume_request_encoding_is_closed():
    raw = b'{"x":1}\n'
    assert RESUME["canonical_request"](base64.b64encode(raw).decode()) == (
        raw,
        {"x": 1},
    )
    for bad in (b'{"x":1,"x":1}\n', b'{ "x":1}\n', b"[]\n"):
        with pytest.raises(ValueError):
            RESUME["canonical_request"](base64.b64encode(bad).decode())
    with pytest.raises(ValueError):
        RESUME["canonical_request"]("A" * 16385)


@pytest.mark.parametrize(
    "arguments,mode,count,result",
    [
        (["deployment", "list"], "transport", 3, 0),
        (["api", "query { me { id } }"], "transport", 3, 0),
        (["restart"], "transport", 1, 1),
        (["api", 'mutation { deploymentRestart(id: "x") }'], "transport", 1, 1),
        (["variable", "set", "KEY=value"], "transport", 1, 1),
        (["deployment", "list"], "authorization", 1, 1),
    ],
)
def test_real_wrapper_retries_reads_only(tmp_path, arguments, mode, count, result):
    counter = tmp_path / "count"
    binary = tmp_path / "fake-railway"
    binary.write_text(
        f"#!{sys.executable}\n"
        + """import os, pathlib, sys
p=pathlib.Path(os.environ['CLI_RETRY_TEST_COUNTER'])
n=int(p.read_text())+1 if p.exists() else 1
p.write_text(str(n))
if n < 3:
    print('operation timed out' if os.environ['CLI_RETRY_TEST_MODE']=='transport' else 'Unauthorized',file=sys.stderr)
    raise SystemExit(1)
print('{}')
"""
    )
    binary.chmod(0o700)
    completed = subprocess.run(
        [sys.executable, str(RETRY), *arguments],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "RAILWAY_REAL_BIN": str(binary),
            "CLI_RETRY_TEST_COUNTER": str(counter),
            "CLI_RETRY_TEST_MODE": mode,
        },
        timeout=15,
    )
    assert completed.returncode == result
    assert int(counter.read_text()) == count
