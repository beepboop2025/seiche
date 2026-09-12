"""Bind scheduled recovery executions to the owner-approved image and live provider.

The original installation ID remains historical approval evidence. Railway cron
creates a new deployment ID; that execution must independently match the exact
approved image and project/environment/service before its subjects are released.
The sealed native executor and its original verifier are unchanged.
"""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import verify
from attest import (
    Live,
    Storage,
    admit_github,
    document,
    download_receipts,
    fetch_index,
    installation_identity,
    owner_signature,
    require,
    runtime_identity,
    validate_tail_inputs,
)


def controller_identity(controller, policy, payload, now):
    value = controller.api(
        "query($id:String!){projectToken{projectId environmentId} "
        "deployment(id:$id){id projectId environmentId serviceId status createdAt meta instances{id status}}}",
        {"id": payload["controller_deployment_id"]},
    )
    expected_scope = {
        field + "Id": policy["controller_" + field + "_id"]
        for field in ("project", "environment")
    }
    require(
        value.get("projectToken") == expected_scope,
        "controller API token is not scoped to the reviewed environment",
    )
    deployment = value.get("deployment")
    expected = {
        "id": payload["controller_deployment_id"],
        **expected_scope,
        "serviceId": policy["controller_service_id"],
    }
    require(
        isinstance(deployment, dict)
        and all(deployment.get(k) == v for k, v in expected.items()),
        "scheduled controller deployment scope differs",
    )
    require(
        deployment.get("status") in {"SUCCESS", "REMOVED"},
        "scheduled controller deployment is not successful or retained",
    )
    instances = deployment.get("instances")
    require(
        isinstance(instances, list)
        and len(instances) == 1
        and isinstance(instances[0], dict)
        and instances[0].get("id") == payload["controller_replica_id"],
        "scheduled controller replica differs from the signed execution",
    )
    metadata = deployment.get("meta")
    require(
        isinstance(metadata, dict)
        and metadata.get("imageDigest")
        == policy["controller_image_digest"]
        == payload["controller_image_digest"],
        "scheduled controller image differs from owner approval",
    )
    created = datetime.fromisoformat(
        str(deployment.get("createdAt", "")).replace("Z", "+00:00")
    )
    executed = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
    require(
        created.tzinfo is not None and created <= executed <= now,
        "controller deployment postdates its execution",
    )
    return {
        **expected,
        "created_at": created.isoformat(),
        "replica_id": instances[0]["id"],
        "image_digest": metadata["imageDigest"],
    }


def approved_installation(installation, payload, policy, now, verify_signature):
    # Authenticate the historical installation against its original ID. The
    # actual signed execution stays untouched and is checked directly with the
    # provider, including its independently assigned deployment ID.
    approval_subject = {
        **payload,
        "controller_deployment_id": policy["controller_deployment_id"],
    }
    return installation_identity(
        *installation, approval_subject, policy, now, verify_signature=verify_signature
    )


def prepare_subjects(
    policy,
    storage,
    live,
    recovery,
    *,
    controller,
    installation,
    now,
    clock=lambda: datetime.now(timezone.utc),
    verify_signature=owner_signature,
):
    before = runtime_identity(live, policy, now)
    envelope, index_ref, _ = fetch_index(storage, policy, now)
    payload = verify.validate_execution_index(
        envelope, policy=policy, current_runtime=before, now=now
    )
    approved_installation(installation, payload, policy, now, verify_signature)
    controller_before = controller_identity(controller, policy, payload, now)
    bodies, _, _ = download_receipts(storage, payload, recovery, policy, now)
    finished = clock()
    after = runtime_identity(live, policy, finished)
    require(
        {k: v for k, v in before.items() if k != "observed_at"}
        == {k: v for k, v in after.items() if k != "observed_at"},
        "production runtime changed during attestation preparation",
    )
    verify.validate_execution_index(
        envelope, policy=policy, current_runtime=after, now=finished
    )
    approved_installation(installation, payload, policy, finished, verify_signature)
    controller_after = controller_identity(controller, policy, payload, finished)
    require(
        controller_before == controller_after,
        "controller identity changed during attestation preparation",
    )
    require(
        storage.head(index_ref["key"])[0] == index_ref,
        "daily index changed during attestation preparation",
    )
    subjects = {
        verify.INDEX_OBJECTS[name]: bodies[name]
        for name in ("recovery_receipt", "offsite_receipt")
    }
    proof = {
        "schema": "seiche.native-recovery-attestation-preparation.v2",
        "status": "pass",
        "execution_platform": "railway",
        "attestation_platform": "github",
        "restore_executed_here": False,
        "controller_identity_proof": "live Railway execution/image identity and owner-signed installation approval",
        "installation_sha256": policy["installation_sha256"],
        "controller_provider": controller_after,
        "observed_at": finished.isoformat(),
        "index": index_ref,
        "runtime": after,
        "controller_source": payload["controller_source"],
        "controller_image_digest": payload["controller_image_digest"],
        "subjects": {
            name: {"sha256": verify.digest(body), "size": len(body)}
            for name, body in subjects.items()
        },
    }
    return subjects, proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--installation", required=True, type=Path)
    parser.add_argument("--installation-signature", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    policy_body = args.policy.read_bytes()
    require(
        verify.digest(policy_body) == args.policy_sha256,
        "tail policy differs from independent review",
    )
    policy = document(policy_body)
    checkout_root = Path(__file__).resolve().parents[3]
    git_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(checkout_root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    checkout = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout_root,
        env=git_env,
        text=True,
        timeout=30,
    ).strip()
    admit_github(os.environ, policy, checkout)
    require(
        not args.output.exists(), "attestation output must be a new private directory"
    )
    tracked_backend = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "backend"],
        cwd=checkout_root,
        env=git_env,
        text=True,
        timeout=30,
    ).splitlines()
    require(
        "deploy/railway-ci/recovery-controller/attest_live.py" in policy["tail_inputs"],
        "live controller verifier is not independently pinned",
    )
    validate_tail_inputs(checkout_root, policy["tail_inputs"], tracked_backend)
    sys.path.insert(0, str(checkout_root / "backend"))
    from seiche import stateful_recovery

    spec = importlib.util.spec_from_file_location(
        "reviewed_storage", checkout_root / "ops/railway/resume_recovery.py"
    )
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    with tempfile.TemporaryDirectory(prefix="native-attestation-") as temporary:
        private = Path(temporary)
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": temporary,
            "RUNNER_TEMP": temporary,
            "GITHUB_WORKSPACE": str(checkout_root),
            "AWS_EC2_METADATA_DISABLED": "true",
        }
        environment.update(
            {
                name: os.environ.pop("ATTEST_" + name)
                for name in (
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                    "AWS_DEFAULT_REGION",
                    "S3_SSE_C_KEY_B64",
                )
            }
        )
        environment.update(
            {
                name: policy[key]
                for name, key in (
                    ("S3_ENDPOINT", "storage_endpoint"),
                    ("S3_BUCKET", "storage_bucket"),
                    ("S3_PREFIX", "storage_prefix"),
                )
            }
        )
        live = Live(
            os.environ.pop("ATTEST_RAILWAY_TOKEN"),
            os.environ.pop("ATTEST_RAILWAY_EDGE_TOKEN"),
            private,
        )
        storage = Storage(private, environment, original)
        controller = Live(
            os.environ.pop("ATTEST_CONTROLLER_RAILWAY_TOKEN"), "", private
        )
        subjects, proof = prepare_subjects(
            policy,
            storage,
            live,
            stateful_recovery,
            controller=controller,
            installation=(
                args.installation.read_bytes(),
                args.installation_signature.read_bytes(),
            ),
            now=datetime.now(timezone.utc),
        )
        args.output.mkdir(mode=0o700)
        for name, body in subjects.items():
            (args.output / name).write_bytes(body)
        (args.output / "native-attestation-proof.json").write_bytes(
            verify.canonical(proof)
        )
    public_proof = {
        name: proof[name]
        for name in (
            "status",
            "execution_platform",
            "attestation_platform",
            "restore_executed_here",
            "observed_at",
            "controller_identity_proof",
            "installation_sha256",
            "controller_source",
            "controller_image_digest",
            "subjects",
        )
    }
    print(
        "RAILWAY_RECOVERY_ATTESTATION_SUBJECTS_PASS "
        + json.dumps(public_proof, sort_keys=True),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Provider subprocess exceptions can contain protected object keys in argv.
        print(
            "RAILWAY_RECOVERY_ATTESTATION_SUBJECTS_FAIL error_type="
            + type(error).__name__,
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1) from None
