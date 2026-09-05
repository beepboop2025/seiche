"""Real signature and state-preservation checks for application successors."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import subprocess

import pytest

from seiche import stateful_application as app
from seiche import stateful_application_runtime as runtime
from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration
from seiche import stateful_recovery as recovery
from test_railway_stateful_recovery import (
    _activation_context,
    _request as recovery_request,
)


def iso(value):
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


@pytest.fixture
def signed(tmp_path, monkeypatch):
    key = tmp_path / "release-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    monkeypatch.setattr(
        app, "OWNER_PUBLIC_KEY", key.with_suffix(".pub").read_text().strip()
    )

    def sign(purpose, payload):
        unsigned = {"schema": app.SIGNED_SCHEMA, "purpose": purpose, "payload": payload}
        process = subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", app.SIGNATURE_NAMESPACE],
            input=app.canonical(unsigned),
            capture_output=True,
            check=True,
        )
        return {**unsigned, "signature": process.stdout.decode()}

    return sign


@pytest.fixture
def transition(tmp_path, monkeypatch, signed):
    platform, environment, original = _activation_context(tmp_path, monkeypatch)
    monkeypatch.setattr(migration, "RUNTIME_GID", os.getegid())
    candidate = app.read_document(
        Path(environment["SEICHE_RAILWAY_CANDIDATE_RECEIPT_PATH"])
    )
    shadow = app.read_document(next((platform / "receipts").iterdir()))
    now = datetime.now(UTC).replace(microsecond=0)
    request = {
        "schema": app.REQUEST_SCHEMA,
        "repository": migration.REPOSITORY,
        "source_ref": "refs/heads/main",
        "operation": "application_upgrade",
        "commit": "b" * 40,
        "tree": "c" * 40,
        "source_archive_sha256": "d" * 64,
        "source_bundle_sha256": "e" * 64,
        "requested_at": iso(now - timedelta(minutes=1)),
        "expires_at": iso(now + timedelta(minutes=40)),
        "railway": {
            k: v for k, v in original["railway"].items() if k != "deployment_id"
        },
        "parent": {
            "commit": original["commit"],
            "deployment_id": original["railway"]["deployment_id"],
            "activation_request_id": original["request_id"],
            "activation_sha256": app.digest(original),
            "migration_activation_sha256": app.digest(original),
            "candidate_sha256": app.digest(candidate),
            "shadow_sha256": app.digest(shadow),
            "recovery_request_sha256": "f" * 64,
            "recovery_sha256": "1" * 64,
            "offsite_sha256": "2" * 64,
            "generation": candidate["filesystem"]["generation"],
            "database": candidate["database"]["name"],
        },
    }
    request["request_id"] = app.digest(request)
    fence = signed(
        "source_stopped",
        {
            "request_id": request["request_id"],
            "parent_activation_sha256": app.digest(original),
            "requested_at": iso(now),
            "expires_at": request["expires_at"],
            "deployment": {
                "id": original["railway"]["deployment_id"],
                "projectId": request["railway"]["project_id"],
                "environmentId": request["railway"]["environment_id"],
                "serviceId": request["railway"]["service_id"],
                "instances": [
                    {"id": "77777777-7777-4777-8777-777777777777", "status": "EXITED"}
                ],
            },
            "hetzner_writers_frozen": True,
            "api_stopped": True,
            "writers_stopped": True,
        },
    )
    successor = {
        "schema": app.CANDIDATE_SCHEMA,
        "request": {
            "id": request["request_id"],
            "sha256": app.digest(request),
            "commit": request["commit"],
            "tree": request["tree"],
        },
        "railway": {
            **request["railway"],
            "deployment_id": "66666666-6666-4666-8666-666666666666",
        },
        "source_fence": fence,
        "data": {
            "generation": request["parent"]["generation"],
            "database": request["parent"]["database"],
            "critical_table_counts": [12, 22, 32, 42],
            "agent_room_audit": candidate["filesystem"]["agent_room_audit"],
            "restored_from_backup": False,
        },
        "authority": app.CANDIDATE_AUTHORITY,
        "validated_at": iso(now),
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }
    grant = signed(
        "activate",
        {
            "request_sha256": app.digest(request),
            "candidate_sha256": app.digest(successor),
            "parent_activation_sha256": app.digest(original),
            "railway": successor["railway"],
            "edge_token_sha256": environment["SEICHE_RAILWAY_EDGE_TOKEN_SHA256"],
            "public_base_url": "https://api.seiche.info",
            "public_probe_sha256": "9" * 64,
            "requested_at": iso(now),
            "expires_at": request["expires_at"],
            "confirmation": "STOPPED_PARENT_CURRENT_DATA_NEW_APPLICATION",
        },
    )
    parent = {
        "activation": original,
        "migration_activation": original,
        "candidate": candidate,
        "shadow": shadow,
    }
    return platform, environment, request, successor, grant, parent


def test_real_signature_rejects_tampering_wrong_purpose_and_key(signed, monkeypatch):
    envelope = signed("source_stopped", {"identity": "original"})
    assert app.validate_approval(envelope, "source_stopped") == {"identity": "original"}
    with pytest.raises(app.ApplicationContractError, match="purpose"):
        app.validate_approval(envelope, "activate")
    changed = deepcopy(envelope)
    changed["payload"]["identity"] = "substitute"
    with pytest.raises(app.ApplicationContractError, match="signature"):
        app.validate_approval(changed, "source_stopped")
    monkeypatch.setattr(
        app,
        "OWNER_PUBLIC_KEY",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV",
    )
    with pytest.raises(app.ApplicationContractError, match="signature"):
        app.validate_approval(envelope, "source_stopped")


@pytest.mark.parametrize(
    "change", ["live", "wrong_scope", "duplicate", "no_instances", "expired"]
)
def test_signed_source_proof_still_requires_unique_stopped_scoped_instances(
    transition, signed, change
):
    _, _, request, candidate, _, _ = transition
    payload = deepcopy(candidate["source_fence"]["payload"])
    if change == "live":
        payload["deployment"]["instances"][0]["status"] = "RUNNING"
    elif change == "wrong_scope":
        payload["deployment"]["projectId"] = "88888888-8888-4888-8888-888888888888"
    elif change == "duplicate":
        payload["deployment"]["instances"] *= 2
    elif change == "no_instances":
        payload["deployment"]["instances"] = []
    else:
        payload["requested_at"] = iso(datetime.now(UTC) - timedelta(hours=2))
        payload["expires_at"] = iso(datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(app.ApplicationContractError):
        app.validate_source_fence(signed("source_stopped", payload), request=request)


def test_successor_preserves_original_candidate_and_new_recovery_identity(transition):
    _, _, request, candidate, grant, parent = transition
    original_bytes = app.canonical(parent["candidate"])
    app.validate_request(request)
    app.validate_candidate(candidate, request=request)
    activation = runtime.render_activation(
        request, candidate, grant, parent, migration._iso_now()
    )
    app.validate_activation(activation)
    validated = recovery.validate_candidate_chain(
        parent["candidate"], activation_receipt=activation
    )
    assert app.canonical(validated) == original_bytes
    assert activation["commit"] != validated["request"]["commit"]
    future_export = recovery_request(activation, now=datetime.now(UTC))
    recovery.validate_request(future_export, activation_receipt=activation)
    assert future_export["commit"] == request["commit"]
    changed = deepcopy(activation)
    changed["application"]["migration_activation"]["commit"] = request["commit"]
    with pytest.raises(recovery.RecoveryContractError):
        recovery.validate_candidate_chain(
            parent["candidate"], activation_receipt=changed
        )


@pytest.mark.parametrize("change", ["destination", "restore", "generation", "request"])
def test_new_candidate_cannot_relabel_state_or_deployment(transition, change):
    _, _, request, candidate, _, _ = transition
    changed = deepcopy(candidate)
    if change == "destination":
        changed["railway"]["deployment_id"] = request["parent"]["deployment_id"]
    elif change == "restore":
        changed["data"]["restored_from_backup"] = True
    elif change == "generation":
        changed["data"]["generation"] += "-replacement"
    else:
        changed["request"]["commit"] = request["parent"]["commit"]
    with pytest.raises(app.ApplicationContractError):
        app.validate_candidate(changed, request=request)


def test_accepted_grant_retires_old_authority_and_rejects_replay_after_successor(
    transition, monkeypatch
):
    platform, _, request, candidate, grant, parent = transition
    authority = platform / "authority"
    for directory in (
        authority,
        authority / "superseded",
        authority / "application-grants",
    ):
        directory.mkdir(exist_ok=True)
    legacy = {
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": "1" * 64,
        "activated_at": parent["activation"]["activated_at"],
    }
    assert app.digest(legacy) == parent["activation"]["grant_sha256"]
    (authority / "activation-grant.json").write_bytes(app.canonical(legacy))
    accepted = runtime.accept_grant(
        request, candidate, grant, parent, platform=platform
    )
    assert app.read_document(accepted) == grant
    assert not (authority / "activation-grant.json").exists()
    assert (
        app.read_document(authority / "superseded" / f"{app.digest(legacy)}.json")
        == legacy
    )
    assert (
        runtime.accept_grant(request, candidate, grant, parent, platform=platform)
        == accepted
    )
    runtime._pointer(authority / "application-active.json", {"request_id": "next"})
    with pytest.raises(app.ApplicationContractError, match="superseded"):
        runtime.accept_grant(request, candidate, grant, parent, platform=platform)


def test_crash_after_retirement_recovers_same_unaccepted_transition(
    transition, monkeypatch
):
    platform, _, request, candidate, grant, parent = transition
    authority = platform / "authority"
    for directory in (
        authority,
        authority / "superseded",
        authority / "application-grants",
    ):
        directory.mkdir(exist_ok=True)
    legacy = {
        "public_base_url": "https://api.seiche.info",
        "public_probe_sha256": "1" * 64,
        "activated_at": parent["activation"]["activated_at"],
    }
    (authority / "activation-grant.json").write_bytes(app.canonical(legacy))
    actual = runtime._seal
    monkeypatch.setattr(
        runtime, "_seal", lambda *a: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        runtime.accept_grant(request, candidate, grant, parent, platform=platform)
    assert not (authority / "activation-grant.json").exists()
    monkeypatch.setattr(runtime, "_seal", actual)
    accepted = runtime.accept_grant(
        request, candidate, grant, parent, platform=platform
    )
    assert app.read_document(accepted) == grant


def test_current_state_audit_rejects_regression_and_never_calls_restore(
    transition, monkeypatch
):
    _, _, request, _, _, parent = transition
    parent["recovery"] = {
        "filesystem": parent["candidate"]["filesystem"],
        "snapshot": {"critical_table_count_floor": [10, 20, 30, 40]},
    }
    seen = []
    monkeypatch.setattr(
        migration,
        "validate_active_generation",
        lambda path, proof: (
            seen.append(path) or proof["filesystem"]["agent_room_audit"]
        ),
    )
    monkeypatch.setattr(
        migration, "inspect_postgres_counts", lambda dsn: (12, 22, 32, 42)
    )
    monkeypatch.setattr(
        cutover, "restore_candidate", lambda *a, **k: pytest.fail("must not restore")
    )
    data = runtime.audit_current_state(request, parent, "postgresql://test")
    assert data["restored_from_backup"] is False
    assert seen == [
        migration.PLATFORM_ROOT / "generations" / request["parent"]["generation"]
    ]
    monkeypatch.setattr(
        migration, "inspect_postgres_counts", lambda dsn: (9, 22, 32, 42)
    )
    with pytest.raises(app.ApplicationContractError, match="regressed"):
        runtime.audit_current_state(request, parent, "postgresql://test")


def _runtime_fixture(transition, monkeypatch):
    platform, environment, request, candidate, grant, parent = transition
    image = platform.parent / "image"
    (image / "parent").mkdir(parents=True)
    (image / "request.json").write_bytes(app.canonical(request))
    (image / "parent" / "candidate.json").write_bytes(
        app.canonical(parent["candidate"])
    )
    monkeypatch.setattr(app, "REQUEST_PATH", image / "request.json")
    environment.update(
        {
            "SEICHE_RELEASE_SHA": request["commit"],
            "SEICHE_RAILWAY_APPLICATION_REQUEST_ID": request["request_id"],
            "SEICHE_RAILWAY_CUTOVER_REQUEST_ID": request["request_id"],
            "RAILWAY_DEPLOYMENT_ID": candidate["railway"]["deployment_id"],
            "SEICHE_DATABASE_URL": "postgresql://test/" + request["parent"]["database"],
        }
    )
    activation = runtime.render_activation(
        request, candidate, grant, parent, migration._iso_now()
    )
    path = platform / "cutover-receipts" / f"{request['request_id']}.activation.json"
    path.write_bytes(app.canonical(activation))
    environment = cutover.production_environment(
        environment, activation, receipt_path=path
    )
    authority = platform / "authority"
    authority.mkdir(exist_ok=True)
    runtime._pointer(
        authority / "application-active.json",
        runtime.pointer_value(request, candidate, grant, "active"),
    )
    return platform, environment, activation


def test_successor_exports_and_validates_new_revision_with_original_data_receipts(
    transition, monkeypatch
):
    platform, environment, activation = _runtime_fixture(transition, monkeypatch)
    _, _, request, _, _, parent = transition
    original = app.canonical(parent["candidate"])
    assert cutover.validate_activation_runtime(environment) == activation
    monkeypatch.setattr(
        migration, "inspect_postgres_counts", lambda _: (12, 22, 32, 42)
    )
    monkeypatch.setattr(migration, "_audit_nbs", lambda _: "verified_head")
    monkeypatch.setattr(
        recovery,
        "_dump_postgres",
        lambda path, dsn: path.write_bytes(b"PGDMP" + b"x" * 2048),
    )
    export_request = recovery_request(activation, now=datetime.now(UTC))
    exported = recovery.export_snapshot(
        environment,
        export_request,
        platform_root=platform,
        runtime_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    _, receipt = recovery.finalize_receipt(
        environment,
        export_request,
        exported,
        platform_root=platform,
        writers_stopped_at=exported.started_at,
        writers_restarted_at=exported.completed_at,
        worker_commands=cutover.worker_commands(),
        runtime_gid=os.getegid(),
    )
    recovery.validate_receipt(
        receipt,
        request=export_request,
        activation_receipt=activation,
        candidate_receipt=parent["candidate"],
        shadow_receipt=parent["shadow"],
        bundle_root=exported.bundle.root,
    )
    assert (
        receipt["commit"] == receipt["snapshot"]["source_revision"] == request["commit"]
    )
    assert receipt["candidate_receipt_sha256"] == app.digest(parent["candidate"])
    assert app.canonical(parent["candidate"]) == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("RAILWAY_DEPLOYMENT_ID", "88888888-8888-4888-8888-888888888888"),
        ("SEICHE_RELEASE_SHA", "9" * 40),
        ("SEICHE_RUNTIME_DATA_DIR", "/var/lib/old-data"),
        ("SEICHE_DATABASE_URL", "postgresql://test/other"),
    ],
)
def test_runtime_rejects_identity_and_data_path_substitution(
    transition, monkeypatch, field, value
):
    _, environment, _ = _runtime_fixture(transition, monkeypatch)
    environment[field] = value
    with pytest.raises(app.ApplicationContractError):
        cutover.validate_activation_runtime(environment)


def test_revoked_application_cannot_restart_collectors_after_export(
    transition, monkeypatch
):
    platform, environment, _ = _runtime_fixture(transition, monkeypatch)
    runtime._pointer(
        platform / "authority" / "application-active.json", {"request_id": "successor"}
    )
    monkeypatch.setattr(
        cutover, "_spawn", lambda *a, **k: pytest.fail("revoked writer was spawned")
    )
    with pytest.raises(app.ApplicationContractError, match="superseded"):
        cutover._start_writer_children(
            environment, cutover.worker_commands(), poll_seconds=1
        )


def test_application_context_keeps_exact_source_recipe_and_closed_extra_member():
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "application_context", root / "ops/railway/build_application_context.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = (root / "ops/railway/Dockerfile.stateful").read_text()
    rendered = module.render_dockerfile(source)
    assert rendered.replace("COPY parent/ /migration/parent/\n", "") == source
    with pytest.raises(ValueError):
        module.render_dockerfile(rendered)
    with pytest.raises(ValueError):
        module.render_dockerfile(
            source.replace(
                "COPY source.tar source.bundle request.json /migration/",
                "COPY . /migration/",
            )
        )
