"""Signed, non-SSH Railway control transport contracts."""

from __future__ import annotations

import base64
import copy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
import pytest

from seiche import api
from seiche import stateful_control as control
from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration
from seiche import stateful_recovery as recovery


ROOT = Path(__file__).resolve().parents[2]
REQUEST_ID = "a" * 64
COMMIT = "b" * 40
DEPLOYMENT_ID = "11111111-1111-4111-8111-111111111111"
REPLICA_ID = "66666666-6666-4666-8666-666666666666"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _environment(mode: str = "cutover_candidate") -> dict[str, str]:
    return {
        "SEICHE_RELEASE_SHA": COMMIT,
        "SEICHE_RAILWAY_STATEFUL_MODE": mode,
        "SEICHE_RAILWAY_CONTROL_ENABLED": "1",
        "SEICHE_RAILWAY_EDGE_TOKEN": "edge-token-" + "x" * 32,
        "RAILWAY_PUBLIC_DOMAIN": "seiche-control.up.railway.app",
        "RAILWAY_PROJECT_ID": "22222222-2222-4222-8222-222222222222",
        "RAILWAY_ENVIRONMENT_ID": "33333333-3333-4333-8333-333333333333",
        "RAILWAY_SERVICE_ID": "44444444-4444-4444-8444-444444444444",
        "RAILWAY_DEPLOYMENT_ID": DEPLOYMENT_ID,
        "SEICHE_RAILWAY_VOLUME_ID": "55555555-5555-4555-8555-555555555555",
        "RAILWAY_VOLUME_NAME": "seiche-stateful-data",
        "RAILWAY_VOLUME_MOUNT_PATH": str(migration.PLATFORM_ROOT),
        "RAILWAY_REPLICA_REGION": "asia-southeast1",
        "RAILWAY_REPLICA_ID": REPLICA_ID,
    }


def _public_key(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _activation_payload() -> dict[str, object]:
    return {
        "public_probe": {
            "request_id": REQUEST_ID,
            "commit": COMMIT,
            "deployment_id": DEPLOYMENT_ID,
        },
        "grant": {
            "request_id": REQUEST_ID,
            "commit": COMMIT,
            "deployment_id": DEPLOYMENT_ID,
        },
    }


def _recovery_payload(now: datetime) -> dict[str, object]:
    return {
        "request": {
            "schema": recovery.REQUEST_SCHEMA,
            "repository": migration.REPOSITORY,
            "workflow": recovery.WORKFLOW,
            "commit": COMMIT,
            "deployment_id": DEPLOYMENT_ID,
            "activation_receipt_sha256": "1" * 64,
            "request_id": REQUEST_ID,
            "snapshot_id": now.strftime("%Y%m%dT%H%M%SZ"),
            "requested_at": _iso(now),
            "download_bearer_sha256": hashlib.sha256(b"r" * 32).hexdigest(),
            "download_expires_at": _iso(now + timedelta(hours=1)),
            "confirmation": recovery.CONFIRMATION,
        }
    }


def _signed_command(
    operation: str,
    payload: dict[str, object],
    environment: dict[str, str],
    private: Ed25519PrivateKey,
    *,
    now: datetime,
) -> tuple[bytes, str, bytes]:
    public = _public_key(private)
    key_id = hashlib.sha256(public).hexdigest()
    unsigned = control.prepare_unsigned_command(
        operation,
        payload,
        environment,
        issued_at=_iso(now),
        expires_at=_iso(now + timedelta(minutes=10)),
        nonce="9" * 64,
        key_id=key_id,
    )
    signature = private.sign(control.command_signing_bytes(unsigned))
    document = {
        **unsigned,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return migration.canonical_document(document), key_id, public


def _install_test_signers(
    monkeypatch: pytest.MonkeyPatch,
    signers: dict[str, tuple[bytes, frozenset[str]]],
) -> None:
    monkeypatch.setattr(control, "load_signer_registry", lambda _path: signers)


def test_release_registry_pins_operation_separated_ed25519_keys() -> None:
    body = (ROOT / "governance" / "railway-control-signers.json").read_bytes()
    assert migration.canonical_document(json.loads(body)) == body
    signers = control.load_signer_registry(
        ROOT / "governance" / "railway-control-signers.json"
    )
    assert signers == {
        control.ACTIVATION_KEY_ID: (
            bytes.fromhex(control.ACTIVATION_PUBLIC_KEY),
            frozenset({control.ACTIVATION_OPERATION}),
        ),
        control.RECOVERY_KEY_ID: (
            bytes.fromhex(control.RECOVERY_PUBLIC_KEY),
            frozenset(
                {
                    control.RECOVERY_EXPORT_OPERATION,
                    control.OFFSITE_ACKNOWLEDGMENT_OPERATION,
                }
            ),
        ),
    }
    assert hashlib.sha256(bytes.fromhex(control.ACTIVATION_PUBLIC_KEY)).hexdigest() == (
        control.ACTIVATION_KEY_ID
    )
    assert hashlib.sha256(bytes.fromhex(control.RECOVERY_PUBLIC_KEY)).hexdigest() == (
        control.RECOVERY_KEY_ID
    )


def test_command_signature_domain_mode_and_key_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    activation_key = Ed25519PrivateKey.generate()
    recovery_key = Ed25519PrivateKey.generate()
    environment = _environment()
    body, activation_id, activation_public = _signed_command(
        control.ACTIVATION_OPERATION,
        _activation_payload(),
        environment,
        activation_key,
        now=now,
    )
    recovery_public = _public_key(recovery_key)
    recovery_id = hashlib.sha256(recovery_public).hexdigest()
    _install_test_signers(
        monkeypatch,
        {
            activation_id: (
                activation_public,
                frozenset({control.ACTIVATION_OPERATION}),
            ),
            recovery_id: (
                recovery_public,
                frozenset(
                    {
                        control.RECOVERY_EXPORT_OPERATION,
                        control.OFFSITE_ACKNOWLEDGMENT_OPERATION,
                    }
                ),
            ),
        },
    )
    validated = control.validate_command(body, environment, now=now)
    assert validated.operation == control.ACTIVATION_OPERATION
    assert validated.request_id == REQUEST_ID

    crossed, _key_id, _public = _signed_command(
        control.ACTIVATION_OPERATION,
        _activation_payload(),
        environment,
        recovery_key,
        now=now,
    )
    with pytest.raises(control.ControlContractError, match="not authorized"):
        control.validate_command(crossed, environment, now=now)

    tampered = json.loads(body)
    tampered["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")
    with pytest.raises(control.ControlContractError, match="signature"):
        control.validate_command(
            migration.canonical_document(tampered),
            environment,
            now=now,
        )

    with pytest.raises(control.ControlContractError, match="mode"):
        control.validate_command(body, _environment("production"), now=now)


def test_command_rejects_stale_identity_and_noncanonical_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    environment = _environment()
    body, key_id, public = _signed_command(
        control.ACTIVATION_OPERATION,
        _activation_payload(),
        environment,
        private,
        now=now,
    )
    _install_test_signers(
        monkeypatch,
        {key_id: (public, frozenset({control.ACTIVATION_OPERATION}))},
    )
    with pytest.raises(control.ControlContractError, match="stale"):
        control.validate_command(body, environment, now=now + timedelta(minutes=10))
    drifted = dict(environment)
    drifted["SEICHE_RAILWAY_VOLUME_ID"] = "77777777-7777-4777-8777-777777777777"
    with pytest.raises(control.ControlContractError, match="identity"):
        control.validate_command(body, drifted, now=now)
    with pytest.raises(control.ControlContractError, match="invalid"):
        control.validate_command(body.rstrip(), environment, now=now)


def test_atomic_submission_is_byte_idempotent_across_accepted_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    environment = _environment()
    body, key_id, public = _signed_command(
        control.ACTIVATION_OPERATION,
        _activation_payload(),
        environment,
        private,
        now=now,
    )
    _install_test_signers(
        monkeypatch,
        {key_id: (public, frozenset({control.ACTIVATION_OPERATION}))},
    )
    uid, gid = os.geteuid(), os.getegid()
    control.prepare_control_dropbox(
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
        root_gid=gid,
    )
    created = control.submit_command(
        body,
        environment,
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )
    assert created.lifecycle == "created"
    assert not tuple(control.control_staging_root(tmp_path).iterdir())
    assert control.submit_command(
        body,
        environment,
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    ).lifecycle == "reused"
    pending = control.pending_commands(
        environment,
        operations=frozenset({control.ACTIVATION_OPERATION}),
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )[0]
    assert pending.path.parent == control.processing_commands_root(tmp_path)
    control.seal_command(
        pending,
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
    )
    assert control.submit_command(
        body,
        _environment("production"),
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    ).lifecycle == "reused"


def test_fresh_wrong_mode_command_never_enters_dropbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    candidate = _environment()
    body, key_id, public = _signed_command(
        control.ACTIVATION_OPERATION,
        _activation_payload(),
        candidate,
        private,
        now=now,
    )
    _install_test_signers(
        monkeypatch,
        {key_id: (public, frozenset({control.ACTIVATION_OPERATION}))},
    )
    uid, gid = os.geteuid(), os.getegid()
    control.prepare_control_dropbox(
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
        root_gid=gid,
    )
    with pytest.raises(control.ControlContractError, match="mode"):
        control.submit_command(
            body,
            _environment("production"),
            platform_root=tmp_path,
            now=now,
            runtime_uid=uid,
            runtime_gid=gid,
            root_uid=uid,
        )
    assert not tuple(control.control_dropbox(tmp_path).iterdir())
    assert not tuple(control.processing_commands_root(tmp_path).iterdir())
    assert not tuple(control.accepted_commands_root(tmp_path).iterdir())
    assert not tuple(control.control_staging_root(tmp_path).iterdir())


def test_claim_rename_failure_leaves_retryable_runtime_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    environment = _environment()
    body, key_id, public = _signed_command(
        control.ACTIVATION_OPERATION,
        _activation_payload(),
        environment,
        private,
        now=now,
    )
    _install_test_signers(
        monkeypatch,
        {key_id: (public, frozenset({control.ACTIVATION_OPERATION}))},
    )
    uid, gid = os.geteuid(), os.getegid()
    control.prepare_control_dropbox(
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
        root_gid=gid,
    )
    created = control.submit_command(
        body,
        environment,
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )
    pending_path = control.control_dropbox(tmp_path) / f"{created.command_id}.json"
    original_rename = Path.rename

    def fail_archive_rename(path: Path, target: Path) -> Path:
        if path == pending_path:
            raise OSError("injected archive rename failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_archive_rename)
    monkeypatch.setattr(
        os,
        "fchown",
        lambda *_args: pytest.fail("ownership changed before archive rename"),
    )
    with pytest.raises(control.ControlContractError, match="could not be claimed"):
        control.pending_commands(
            environment,
            operations=frozenset({control.ACTIVATION_OPERATION}),
            platform_root=tmp_path,
            now=now,
            runtime_uid=uid,
            runtime_gid=gid,
            root_uid=uid,
        )
    assert pending_path.read_bytes() == body
    assert not (
        control.accepted_commands_root(tmp_path) / f"{created.command_id}.json"
    ).exists()


def test_claim_repairs_submission_crash_between_link_and_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    environment = _environment()
    body, key_id, public = _signed_command(
        control.ACTIVATION_OPERATION,
        _activation_payload(),
        environment,
        private,
        now=now,
    )
    _install_test_signers(
        monkeypatch,
        {key_id: (public, frozenset({control.ACTIVATION_OPERATION}))},
    )
    uid, gid = os.geteuid(), os.getegid()
    control.prepare_control_dropbox(
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
        root_gid=gid,
    )
    created = control.submit_command(
        body,
        environment,
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )
    proposal = control.control_dropbox(tmp_path) / f"{created.command_id}.json"
    interrupted_stage = control.control_staging_root(tmp_path) / ".command-interrupted"
    os.link(proposal, interrupted_stage)
    assert proposal.stat().st_nlink == 2

    pending = control.pending_commands(
        environment,
        operations=frozenset({control.ACTIVATION_OPERATION}),
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )
    assert len(pending) == 1
    assert not interrupted_stage.exists()
    assert pending[0].path.parent == control.processing_commands_root(tmp_path)
    assert pending[0].path.stat().st_nlink == 1


def test_processing_claim_resumes_after_command_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    environment = _environment()
    body, key_id, public = _signed_command(
        control.ACTIVATION_OPERATION,
        _activation_payload(),
        environment,
        private,
        now=now,
    )
    _install_test_signers(
        monkeypatch,
        {key_id: (public, frozenset({control.ACTIVATION_OPERATION}))},
    )
    uid, gid = os.geteuid(), os.getegid()
    control.prepare_control_dropbox(
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
        root_gid=gid,
    )
    control.submit_command(
        body,
        environment,
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )
    claimed = control.pending_commands(
        environment,
        operations=frozenset({control.ACTIVATION_OPERATION}),
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )[0]
    assert claimed.path.parent == control.processing_commands_root(tmp_path)
    resumed = control.pending_commands(
        environment,
        operations=frozenset({control.ACTIVATION_OPERATION}),
        platform_root=tmp_path,
        now=now + timedelta(hours=1),
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )
    assert resumed == [claimed]
    control.seal_command(
        resumed[0],
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
    )
    assert not tuple(control.processing_commands_root(tmp_path).iterdir())


def test_recovery_accepted_replay_reenters_processing_for_result_reemit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    environment = _environment("production")
    body, key_id, public = _signed_command(
        control.RECOVERY_EXPORT_OPERATION,
        _recovery_payload(now),
        environment,
        private,
        now=now,
    )
    _install_test_signers(
        monkeypatch,
        {key_id: (public, frozenset({control.RECOVERY_EXPORT_OPERATION}))},
    )
    uid, gid = os.geteuid(), os.getegid()
    control.prepare_control_dropbox(
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
        root_gid=gid,
    )
    control.submit_command(
        body,
        environment,
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )
    claimed = control.pending_commands(
        environment,
        operations=frozenset({control.RECOVERY_EXPORT_OPERATION}),
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )[0]
    control.seal_command(
        claimed,
        platform_root=tmp_path,
        runtime_gid=gid,
        root_uid=uid,
    )
    replay = control.submit_command(
        body,
        environment,
        platform_root=tmp_path,
        now=now,
        runtime_uid=uid,
        runtime_gid=gid,
        root_uid=uid,
    )
    assert replay.lifecycle == "reused"
    assert (
        control.control_dropbox(tmp_path) / f"{replay.command_id}.json"
    ).read_bytes() == body


def test_recovery_evidence_orphan_stage_is_safely_rebuilt(tmp_path: Path) -> None:
    request_id = "c" * 64
    gid = os.getegid()
    evidence_root = tmp_path / "recovery-evidence"
    cutover._prepare_authority_directory(evidence_root, runtime_gid=gid)
    orphan = evidence_root / f".{request_id}.orphan"
    orphan.mkdir(mode=0o700)
    partial = orphan / "request.json"
    partial.write_bytes(b"partial\n")
    partial.chmod(0o600)
    bodies = {
        name: f"{name}\n".encode()
        for name in control.RECOVERY_EVIDENCE_NAMES
    }
    destination = recovery._publish_recovery_evidence(
        tmp_path,
        request_id,
        bodies,
        runtime_gid=gid,
    )
    assert not orphan.exists()
    assert destination.stat().st_mode & 0o777 == 0o550
    assert {item.name for item in destination.iterdir()} == set(bodies)
    assert all(item.stat().st_mode & 0o777 == 0o440 for item in destination.iterdir())


def test_activation_processing_replays_idempotent_effect_before_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = SimpleNamespace(
        command=SimpleNamespace(
            request_id=REQUEST_ID,
            document={"payload": _activation_payload()},
        )
    )
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(control, "pending_commands", lambda *_args, **_kwargs: [proposal])
    monkeypatch.setattr(
        cutover,
        "publish_authority_documents",
        lambda *_args, **kwargs: calls.append(
            ("publish", kwargs.get("require_fresh"))
        ),
    )
    monkeypatch.setattr(
        control,
        "seal_command",
        lambda *_args, **_kwargs: calls.append(("seal", proposal)),
    )
    cutover._promote_activation_control_commands(
        _environment(),
        platform_root=tmp_path,
    )
    assert calls == [("publish", False), ("seal", proposal)]


def test_recovery_processing_waits_for_receipt_then_repairs_emits_and_seals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    request = _recovery_payload(now)["request"]
    proposal = SimpleNamespace(
        command=SimpleNamespace(
            operation=control.RECOVERY_EXPORT_OPERATION,
            command_id="d" * 64,
            request_id=REQUEST_ID,
            document={"payload": {"request": request}},
        )
    )
    monkeypatch.setattr(control, "pending_commands", lambda *_args, **_kwargs: [proposal])
    monkeypatch.setattr(recovery, "publish_request", lambda *_args, **_kwargs: request)
    monkeypatch.setattr(
        recovery,
        "receipted_request_context",
        lambda *_args, **_kwargs: None,
    )
    seals: list[object] = []
    monkeypatch.setattr(
        control,
        "seal_command",
        lambda item, **_kwargs: seals.append(item),
    )
    claimed = recovery.promote_control_commands(
        _environment("production"),
        platform_root=tmp_path,
        runtime_gid=os.getegid(),
    )
    assert claimed == {REQUEST_ID: proposal}
    assert seals == []

    receipt = _recovery_evidence()
    monkeypatch.setattr(
        recovery,
        "receipted_request_context",
        lambda *_args, **_kwargs: receipt,
    )
    monkeypatch.setattr(
        control,
        "render_log_result",
        lambda *_args, **_kwargs: "SEICHE_RAILWAY_STATEFUL_RESULT_V1=fixture",
    )
    assert recovery.promote_control_commands(
        _environment("production"),
        platform_root=tmp_path,
        runtime_gid=os.getegid(),
    ) == {}
    assert seals == [proposal]
    assert "SEICHE_RAILWAY_STATEFUL_RESULT_V1=fixture" in capsys.readouterr().out


def test_offsite_processing_replays_published_effect_before_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proposal = SimpleNamespace(
        command=SimpleNamespace(
            operation=control.OFFSITE_ACKNOWLEDGMENT_OPERATION,
            command_id="e" * 64,
            request_id=REQUEST_ID,
            document={
                "payload": {
                    "recovery_request_sha256": "1" * 64,
                    "recovery_receipt_sha256": "2" * 64,
                    "offsite_receipt": {"request_id": REQUEST_ID},
                }
            },
        )
    )
    monkeypatch.setattr(control, "pending_commands", lambda *_args, **_kwargs: [proposal])
    published = False
    calls: list[tuple[str, object]] = []

    def publish(*_args: object, **kwargs: object) -> tuple[dict, str, dict]:
        nonlocal published
        lifecycle = "reused" if published else "created"
        published = True
        calls.append(("publish", kwargs.get("require_fresh")))
        return {}, lifecycle, {"request_id": REQUEST_ID}

    seal_attempts = 0

    def seal(*_args: object, **_kwargs: object) -> None:
        nonlocal seal_attempts
        seal_attempts += 1
        calls.append(("seal", seal_attempts))
        if seal_attempts == 1:
            raise control.ControlContractError("injected post-publish crash")

    monkeypatch.setattr(recovery, "publish_offsite_receipt", publish)
    monkeypatch.setattr(control, "seal_command", seal)
    monkeypatch.setattr(
        control,
        "render_log_result",
        lambda *_args, **kwargs: (
            calls.append(("render", kwargs["lifecycle"]))
            or "SEICHE_RAILWAY_STATEFUL_RESULT_V1=fixture"
        ),
    )
    with pytest.raises(control.ControlContractError, match="post-publish crash"):
        recovery.promote_control_commands(
            _environment("production"),
            platform_root=tmp_path,
            runtime_gid=os.getegid(),
        )
    assert calls == [("publish", False), ("seal", 1)]
    assert capsys.readouterr().out == ""

    recovery.promote_control_commands(
        _environment("production"),
        platform_root=tmp_path,
        runtime_gid=os.getegid(),
    )
    assert calls == [
        ("publish", False),
        ("seal", 1),
        ("publish", False),
        ("seal", 2),
        ("render", "reused"),
    ]
    assert "SEICHE_RAILWAY_STATEFUL_RESULT_V1=fixture" in capsys.readouterr().out


def test_restart_repairs_evidence_and_reemits_sealed_recovery_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    request = _recovery_payload(now)["request"]
    assert isinstance(request, dict)
    snapshot_id = str(request["snapshot_id"])
    requests = tmp_path / "recovery-requests"
    receipts = tmp_path / "recovery-receipts"
    offsite = tmp_path / "recovery-offsite-receipts"
    requests.mkdir()
    receipts.mkdir()
    offsite.mkdir()
    (requests / f"{REQUEST_ID}.json").write_bytes(migration.canonical_document(request))
    (receipts / f"{snapshot_id}-{REQUEST_ID}.json").write_bytes(b"{}\n")
    (offsite / f"{snapshot_id}-{REQUEST_ID}.json").write_bytes(b"{}\n")

    receipt = _recovery_evidence()
    paired = {"request_id": REQUEST_ID}
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(recovery, "activation_context", lambda _env: (b"{}\n", {}))
    monkeypatch.setattr(
        recovery,
        "validate_request",
        lambda value, **_kwargs: value,
    )
    monkeypatch.setattr(
        recovery,
        "receipted_request_context",
        lambda *_args, **_kwargs: calls.append(("repair_evidence", REQUEST_ID))
        or receipt,
    )
    monkeypatch.setattr(
        recovery,
        "publish_offsite_receipt",
        lambda *_args, **kwargs: (
            calls.append(("repair_pair", kwargs["require_fresh"])) or {},
            "reused",
            paired,
        ),
    )
    monkeypatch.setattr(
        control,
        "render_log_result",
        lambda evidence, **kwargs: (
            calls.append(("render", (kwargs["kind"], kwargs["lifecycle"], evidence)))
            or "SEICHE_RAILWAY_STATEFUL_RESULT_V1=fixture"
        ),
    )
    recovery.reemit_latest_recovery_results(
        _environment("production"),
        platform_root=tmp_path,
        runtime_started_at="2026-09-03T01:05:00Z",
        runtime_gid=os.getegid(),
    )
    assert calls == [
        ("repair_evidence", REQUEST_ID),
        ("render", ("recovery_created", "reused", receipt)),
        ("repair_pair", False),
        ("render", ("recovery_offsite_paired", "reused", paired)),
    ]
    assert capsys.readouterr().out.count(
        "SEICHE_RAILWAY_STATEFUL_RESULT_V1=fixture"
    ) == 2


def _candidate_evidence(request_id: str = REQUEST_ID) -> dict[str, object]:
    return {
        "request": {"id": request_id, "commit": COMMIT},
        "railway": {"deployment_id": DEPLOYMENT_ID},
    }


def _recovery_evidence(request_id: str = REQUEST_ID) -> dict[str, object]:
    return {
        "request_id": request_id,
        "commit": COMMIT,
        "railway": {"deployment_id": DEPLOYMENT_ID},
    }


def _log_line(message: str, timestamp: str) -> bytes:
    return (json.dumps({"timestamp": timestamp, "message": message}) + "\n").encode()


def test_log_parser_proves_created_restart_reuse_and_skips_other_kinds() -> None:
    environment = _environment()
    evidence = _candidate_evidence()
    created = control.render_log_result(
        evidence,
        kind="candidate",
        lifecycle="created",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:00:00Z",
    )
    reused = control.render_log_result(
        evidence,
        kind="candidate",
        lifecycle="reused",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:01:00Z",
    )
    activation = control.render_log_result(
        _recovery_evidence(),
        kind="activation",
        lifecycle="created",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:02:00Z",
    )
    logs = b"".join(
        (
            _log_line(created, "2026-09-03T01:00:01.123456789Z"),
            _log_line(reused, "2026-09-03T01:01:01Z"),
            _log_line(activation, "2026-09-03T01:02:01Z"),
        )
    )
    results = control.extract_log_results(
        logs,
        expected_kind="candidate",
        expected_request_id=REQUEST_ID,
        expected_commit=COMMIT,
        expected_deployment_id=DEPLOYMENT_ID,
        expected_replicas={"created": REPLICA_ID, "reused": REPLICA_ID},
        not_before="2026-09-03T01:00:00Z",
    )
    assert set(results) == {"created", "reused"}
    assert results["created"].evidence_body == results["reused"].evidence_body


def test_log_parser_selects_latest_byte_identical_same_replica_reemit() -> None:
    environment = _environment()
    evidence = _candidate_evidence()
    first = control.render_log_result(
        evidence,
        kind="candidate",
        lifecycle="reused",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:00:00Z",
    )
    restarted = control.render_log_result(
        evidence,
        kind="candidate",
        lifecycle="reused",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:05:00Z",
    )
    result = control.extract_log_result(
        _log_line(first, "2026-09-03T01:00:01Z")
        + _log_line(restarted, "2026-09-03T01:05:01.123456789Z"),
        expected_kind="candidate",
        expected_lifecycle="reused",
        expected_request_id=REQUEST_ID,
        expected_commit=COMMIT,
        expected_deployment_id=DEPLOYMENT_ID,
        expected_replica_id=REPLICA_ID,
        not_before="2026-09-03T01:00:00Z",
    )
    assert result.logged_at == "2026-09-03T01:05:01.123456789Z"
    assert result.runtime_started_at == "2026-09-03T01:05:00Z"
    assert result.evidence_body == migration.canonical_document(evidence)


def test_log_parser_rejects_conflicting_same_replica_reemit() -> None:
    environment = _environment()
    first = control.render_log_result(
        _candidate_evidence(),
        kind="candidate",
        lifecycle="reused",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:00:00Z",
    )
    changed_evidence = _candidate_evidence()
    changed_evidence["noncanonical_extra"] = True
    second = control.render_log_result(
        changed_evidence,
        kind="candidate",
        lifecycle="reused",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:05:00Z",
    )
    with pytest.raises(control.ControlContractError, match="duplicated"):
        control.extract_log_result(
            _log_line(first, "2026-09-03T01:00:01Z")
            + _log_line(second, "2026-09-03T01:05:01Z"),
            expected_kind="candidate",
            expected_lifecycle="reused",
            expected_request_id=REQUEST_ID,
            expected_commit=COMMIT,
            expected_deployment_id=DEPLOYMENT_ID,
            expected_replica_id=REPLICA_ID,
            not_before="2026-09-03T01:00:00Z",
        )


@pytest.mark.parametrize("mutation", ("duplicate", "malformed", "changed", "wrong"))
def test_log_parser_rejects_ambiguous_or_tampered_results(mutation: str) -> None:
    environment = _environment()
    created = control.render_log_result(
        _candidate_evidence(),
        kind="candidate",
        lifecycle="created",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:00:00Z",
    )
    if mutation == "duplicate":
        logs = _log_line(created, "2026-09-03T01:00:01Z") * 2
    elif mutation == "malformed":
        logs = _log_line(control.LOG_RESULT_MARKER + "bad", "2026-09-03T01:00:01Z")
    elif mutation == "changed":
        changed = control.render_log_result(
            _candidate_evidence("c" * 64),
            kind="candidate",
            lifecycle="reused",
            request_id="c" * 64,
            environment=environment,
            runtime_started_at="2026-09-03T01:00:00Z",
        )
        logs = _log_line(created, "2026-09-03T01:00:01Z") + _log_line(
            changed, "2026-09-03T01:00:02Z"
        )
    else:
        envelope = json.loads(
            base64.b64decode(created.removeprefix(control.LOG_RESULT_MARKER))
        )
        envelope["deployment_id"] = "77777777-7777-4777-8777-777777777777"
        wrong = control.LOG_RESULT_MARKER + base64.b64encode(
            migration.canonical_document(envelope)
        ).decode()
        logs = _log_line(wrong, "2026-09-03T01:00:01Z")
    if mutation == "changed":
        result = control.extract_log_result(
            logs,
            expected_kind="candidate",
            expected_lifecycle="created",
            expected_request_id=REQUEST_ID,
            expected_commit=COMMIT,
            expected_deployment_id=DEPLOYMENT_ID,
            expected_replica_id=REPLICA_ID,
            not_before="2026-09-03T01:00:00Z",
        )
        assert result.request_id == REQUEST_ID
    else:
        with pytest.raises(control.ControlContractError):
            control.extract_log_result(
                logs,
                expected_kind="candidate",
                expected_lifecycle="created",
                expected_request_id=REQUEST_ID,
                expected_commit=COMMIT,
                expected_deployment_id=DEPLOYMENT_ID,
                expected_replica_id=REPLICA_ID,
                not_before="2026-09-03T01:00:00Z",
            )


def test_latest_recovery_pair_is_same_request_and_digest_bound() -> None:
    environment = _environment("production")
    receipt = _recovery_evidence()
    created = control.render_log_result(
        receipt,
        kind="recovery_created",
        lifecycle="created",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:00:00Z",
    )
    paired_evidence = {
        "schema": control.PAIRED_EVIDENCE_SCHEMA,
        "request_id": REQUEST_ID,
        "recovery_receipt_sha256": hashlib.sha256(
            migration.canonical_document(receipt)
        ).hexdigest(),
        "offsite_receipt_sha256": "",
        "recovery_receipt": receipt,
        "offsite_receipt": {
            "schema": control.OFFSITE_RECEIPT_SCHEMA,
            "request_id": REQUEST_ID,
            "commit": COMMIT,
            "recovery_receipt_sha256": hashlib.sha256(
                migration.canonical_document(receipt)
            ).hexdigest(),
        },
    }
    paired_evidence["offsite_receipt_sha256"] = hashlib.sha256(
        migration.canonical_document(paired_evidence["offsite_receipt"])
    ).hexdigest()
    paired = control.render_log_result(
        paired_evidence,
        kind="recovery_offsite_paired",
        lifecycle="created",
        request_id=REQUEST_ID,
        environment=environment,
        runtime_started_at="2026-09-03T01:00:00Z",
    )
    logs = _log_line(created, "2026-09-03T01:00:01Z") + _log_line(
        paired, "2026-09-03T01:00:02Z"
    )
    observed_created, observed_paired = control.extract_latest_recovery_pair(
        logs,
        expected_commit=COMMIT,
        expected_deployment_id=DEPLOYMENT_ID,
        now=datetime(2026, 9, 3, 1, 1, tzinfo=UTC),
        max_age=timedelta(hours=1),
    )
    assert observed_created.request_id == observed_paired.request_id == REQUEST_ID


def test_verified_member_is_exact_regular_immutable_file(tmp_path: Path) -> None:
    path = tmp_path / "member"
    body = b"sealed recovery evidence\n"
    path.write_bytes(body)
    path.chmod(0o440)
    opened = control._open_verified_member(
        path,
        name="manifest.env",
        expected_sha256=hashlib.sha256(body).hexdigest(),
        root_uid=os.geteuid(),
        runtime_gid=os.getegid(),
    )
    assert b"".join(control.stream_recovery_member(opened)) == body
    path.chmod(0o640)
    with pytest.raises(control.ControlContractError, match="metadata"):
        control._open_verified_member(
            path,
            name="manifest.env",
            expected_sha256=hashlib.sha256(body).hexdigest(),
            root_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(control.ControlContractError):
        control._open_verified_member(
            link,
            name="manifest.env",
            expected_sha256=hashlib.sha256(body).hexdigest(),
            root_uid=os.geteuid(),
            runtime_gid=os.getegid(),
        )


def test_control_api_is_hidden_disabled_dual_gated_and_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_path = "/api/internal/v1/railway-control/commands"
    client = TestClient(api.app, base_url="https://seiche-control.up.railway.app")
    monkeypatch.delenv("SEICHE_RAILWAY_CONTROL_ENABLED", raising=False)
    assert client.post(command_path, content=b"{}\n", headers={"content-type": "application/json"}).status_code == 404

    for name, value in _environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        control,
        "submit_command",
        lambda *_args, **_kwargs: control.CommandSubmission("a" * 64, "created"),
    )
    headers = {
        cutover.EDGE_HEADER: _environment()["SEICHE_RAILWAY_EDGE_TOKEN"],
        "content-type": "application/json",
    }
    assert client.post(
        command_path,
        content=b"{}\n",
        headers={"content-type": "application/json"},
    ).status_code == 404
    assert client.post(
        command_path,
        content=b"{}\n",
        headers={**headers, cutover.EDGE_HEADER: "wrong-edge-token"},
    ).status_code == 404
    assert client.post(
        command_path,
        content=b"{}\n",
        headers={**headers, "content-type": "application/json; charset=utf-8"},
    ).status_code == 404
    for content_length in ("0", "invalid", str(control.MAX_COMMAND_BYTES + 1)):
        assert client.post(
            command_path,
            content=b"{}\n",
            headers={**headers, "content-length": content_length},
        ).status_code == 404
    accepted = client.post(command_path, content=b"{}\n", headers=headers)
    assert accepted.status_code == 202
    assert accepted.json() == {
        "status": "accepted",
        "command_id": "a" * 64,
        "lifecycle": "created",
    }
    assert accepted.headers["cache-control"] == "no-store"
    assert client.get(command_path, headers=headers).status_code == 404
    assert client.post(
        command_path,
        content=b"{}\n",
        headers={**headers, "host": "public.example"},
    ).status_code == 404
    assert client.post(
        command_path,
        content=b"{}\n",
        headers={**headers, "host": ""},
    ).status_code == 404
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN")
    assert client.post(command_path, content=b"{}\n", headers=headers).status_code == 404
    assert command_path not in api.app.openapi()["paths"]


def test_control_api_never_exposes_validation_or_capability_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in _environment("production").items():
        monkeypatch.setenv(name, value)
    client = TestClient(api.app, base_url="https://seiche-control.up.railway.app")
    headers = {
        cutover.EDGE_HEADER: _environment()["SEICHE_RAILWAY_EDGE_TOKEN"],
        "content-type": "application/json",
    }
    monkeypatch.setattr(
        control,
        "submit_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            control.ControlContractError("secret validation detail")
        ),
    )
    failed = client.post(
        "/api/internal/v1/railway-control/commands",
        content=b"{}\n",
        headers=headers,
    )
    assert failed.status_code == 404
    assert failed.json() == {"detail": "not found"}
    assert failed.headers["cache-control"] == "no-store"
    monkeypatch.setattr(
        control,
        "open_recovery_member",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            control.ControlContractError("token differs")
        ),
    )
    download = client.get(
        f"/api/internal/v1/railway-control/recovery/{REQUEST_ID}/manifest.env",
        headers={
            cutover.EDGE_HEADER: _environment()["SEICHE_RAILWAY_EDGE_TOKEN"],
            "authorization": "Bearer " + base64.urlsafe_b64encode(b"r" * 32).decode().rstrip("="),
        },
    )
    assert download.status_code == 404
    assert download.json() == {"detail": "not found"}


def test_recovery_request_v2_binds_download_capability_lifetime() -> None:
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    activation = {
        "commit": COMMIT,
        "railway": {"deployment_id": DEPLOYMENT_ID},
    }
    activation_digest = hashlib.sha256(
        migration.canonical_document(activation)
    ).hexdigest()
    request = _recovery_payload(now)["request"]
    assert isinstance(request, dict)
    request["activation_receipt_sha256"] = activation_digest
    assert recovery.validate_request(request, activation_receipt=activation, now=now)
    expired = copy.deepcopy(request)
    expired["download_expires_at"] = expired["requested_at"]
    with pytest.raises(recovery.RecoveryContractError, match="download lifetime"):
        recovery.validate_request(expired, activation_receipt=activation, now=now)


def test_root_promotion_uses_existing_immutable_publishers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command = SimpleNamespace(
        operation=control.ACTIVATION_OPERATION,
        request_id=REQUEST_ID,
        document={"payload": _activation_payload()},
    )
    proposal = SimpleNamespace(command=command)
    monkeypatch.setattr(control, "pending_commands", lambda *_args, **_kwargs: [proposal])
    published: list[tuple[str, bytes, bytes]] = []
    monkeypatch.setattr(
        cutover,
        "publish_authority_documents",
        lambda request_id, probe, grant, **_kwargs: published.append(
            (request_id, probe, grant)
        ),
    )
    monkeypatch.setattr(control, "seal_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration, "railway_identity", lambda _env: {})
    cutover._promote_activation_control_commands(
        {"SEICHE_RAILWAY_CONTROL_ENABLED": "1"},
        platform_root=tmp_path,
    )
    assert published[0][0] == REQUEST_ID
    assert json.loads(published[0][1]) == _activation_payload()["public_probe"]
    assert json.loads(published[0][2]) == _activation_payload()["grant"]


def test_recovery_generation_is_root_group_readable_and_closed(tmp_path: Path) -> None:
    generation = tmp_path / "generation"
    generation.mkdir()
    for name in migration._ALL_BACKUP_MEMBERS:
        (generation / name).write_bytes(b"x")
    recovery._seal_recovery_generation(
        generation,
        runtime_gid=os.getegid(),
        root_uid=os.geteuid(),
    )
    assert stat.S_IMODE(generation.stat().st_mode) == 0o550
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o440 for path in generation.iterdir())
    recovery._validate_recovery_generation_permissions(
        generation,
        runtime_gid=os.getegid(),
        root_uid=os.geteuid(),
    )
