"""Signed, origin-only control transport for Railway stateful operations.

The API child can authenticate and atomically stage a command, but it cannot
publish authority or recovery state.  The root supervisor repeats every
validation and promotes the staged bytes through the existing immutable
writers.  Evidence is returned through deployment logs or a closed,
capability-gated recovery download surface; neither path needs SSH or SFTP.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator, Mapping, NamedTuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from seiche import stateful_migration as migration

COMMAND_SCHEMA = "seiche.railway-control-command.v1"
SIGNER_REGISTRY_SCHEMA = "seiche.railway-control-signers.v1"
LOG_RESULT_SCHEMA = "seiche.railway-stateful-log-result.v1"
LOG_RESULT_MARKER = "SEICHE_RAILWAY_STATEFUL_RESULT_V1="
PAIRED_EVIDENCE_SCHEMA = "seiche.railway-recovery-offsite-paired-evidence.v1"
OFFSITE_RECEIPT_SCHEMA = "seiche.railway-offsite-recovery-receipt.v3"

ACTIVATION_OPERATION = "activation"
RECOVERY_EXPORT_OPERATION = "recovery_export"
OFFSITE_ACKNOWLEDGMENT_OPERATION = "offsite_acknowledgment"
OPERATIONS = frozenset(
    {
        ACTIVATION_OPERATION,
        RECOVERY_EXPORT_OPERATION,
        OFFSITE_ACKNOWLEDGMENT_OPERATION,
    }
)
RESULT_KINDS = frozenset(
    {"candidate", "activation", "recovery_created", "recovery_offsite_paired"}
)
RESULT_LIFECYCLES = frozenset({"created", "reused"})

CUTOVER_WORKFLOW = (
    "beepboop2025/seiche/.github/workflows/railway-stateful-cutover.yml"
)
RECOVERY_WORKFLOW = (
    "beepboop2025/seiche/.github/workflows/railway-stateful-recovery.yml"
)
OPERATION_WORKFLOWS = {
    ACTIVATION_OPERATION: CUTOVER_WORKFLOW,
    RECOVERY_EXPORT_OPERATION: RECOVERY_WORKFLOW,
    OFFSITE_ACKNOWLEDGMENT_OPERATION: RECOVERY_WORKFLOW,
}
RESULT_WORKFLOWS = {
    "candidate": CUTOVER_WORKFLOW,
    "activation": CUTOVER_WORKFLOW,
    "recovery_created": RECOVERY_WORKFLOW,
    "recovery_offsite_paired": RECOVERY_WORKFLOW,
}

ACTIVATION_PUBLIC_KEY = (
    "cefd080dc9210529424ff029f358e3fda44fa539250a35e3204c986ab139f4de"
)
ACTIVATION_KEY_ID = (
    "cf08c9956205cd0151ca4d71edbf65af6e82ef802a8afbfaef27b6f3be43e4f3"
)
RECOVERY_PUBLIC_KEY = (
    "9acdfc2b5c1852fb47608912183b5d28d943da126e3c448b66bbd46c7c31a844"
)
RECOVERY_KEY_ID = (
    "2be24b7ea07b1596f9e6bf95c22ee1425532b28e04b3d3cf4eb263fce7142987"
)

SIGNER_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "governance"
    / "railway-control-signers.json"
)
CONTROL_ROOT_NAME = "railway-control"
DROPBOX_NAME = "dropbox"
PROCESSING_NAME = "processing"
ACCEPTED_NAME = "accepted"
STAGING_NAME = "staging"

MAX_COMMAND_BYTES = 128 * 1024
MAX_REGISTRY_BYTES = 8 * 1024
MAX_PENDING_COMMANDS = 64
MAX_ACCEPTED_COMMANDS = 4096
MAX_LOG_EVIDENCE_BYTES = 64 * 1024
MAX_LOG_RESULT_BYTES = 96 * 1024
MAX_DEPLOYMENT_LOG_BYTES = 8 * 1024 * 1024
MAX_RECOVERY_MEMBER_BYTES = 5 * 1024**3
COMMAND_MAX_LIFETIME = timedelta(minutes=15)
COMMAND_FUTURE_SKEW = timedelta(minutes=1)
DOWNLOAD_MAX_LIFETIME = timedelta(hours=2)

_SHA40_RE = re.compile(r"[0-9a-f]{40}")
_SHA64_RE = re.compile(r"[0-9a-f]{64}")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_COMMAND_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "commit",
        "railway",
        "request_id",
        "issued_at",
        "expires_at",
        "nonce",
        "command_id",
        "operation",
        "key_id",
        "payload_sha256",
        "payload",
        "signature",
    }
)
_COMMAND_ID_KEYS = _COMMAND_KEYS - {"command_id", "signature"}
_SIGNING_KEYS = _COMMAND_KEYS - {"signature"}
_RAILWAY_KEYS = frozenset(
    {"project_id", "environment_id", "service_id", "deployment_id", "volume_id"}
)
_LOG_RESULT_KEYS = frozenset(
    {
        "schema",
        "kind",
        "lifecycle",
        "repository",
        "workflow",
        "commit",
        "deployment_id",
        "replica_id",
        "request_id",
        "runtime_started_at",
        "evidence_sha256",
        "evidence",
    }
)
_COMMAND_ID_DOMAIN = b"seiche.railway-control-command-id.v1\0"
_SIGNATURE_DOMAIN = b"seiche.railway-control-command.v1\0"

RECOVERY_MEMBER_NAMES = frozenset(
    {
        "activation-receipt.json",
        "candidate-receipt.json",
        "shadow-receipt.json",
        "request.json",
        "recovery-receipt.json",
        "seiche.dump",
        "var-lib-seiche.tgz",
        "palimpsest-china.tgz",
        "palimpsest-china-state.json",
        "api-data.tgz",
        "table-counts.txt",
        "deployed-sha.txt",
        "manifest.env",
        "SHA256SUMS",
    }
)
RECOVERY_EVIDENCE_NAMES = frozenset(
    {
        "activation-receipt.json",
        "candidate-receipt.json",
        "shadow-receipt.json",
        "request.json",
        "recovery-receipt.json",
    }
)

EXPECTED_SIGNER_REGISTRY = {
    "schema": SIGNER_REGISTRY_SCHEMA,
    "signers": [
        {
            "allowed_operations": [ACTIVATION_OPERATION],
            "key_id": ACTIVATION_KEY_ID,
            "public_key_ed25519": ACTIVATION_PUBLIC_KEY,
        },
        {
            "allowed_operations": [
                OFFSITE_ACKNOWLEDGMENT_OPERATION,
                RECOVERY_EXPORT_OPERATION,
            ],
            "key_id": RECOVERY_KEY_ID,
            "public_key_ed25519": RECOVERY_PUBLIC_KEY,
        },
    ],
}


class ControlContractError(RuntimeError):
    """A signed command, staged proposal, log, or download failed closed."""


class ValidatedCommand(NamedTuple):
    command_id: str
    operation: str
    request_id: str
    body: bytes
    document: dict[str, Any]


class CommandSubmission(NamedTuple):
    command_id: str
    lifecycle: str


class PendingCommand(NamedTuple):
    path: Path
    command: ValidatedCommand


class StatefulLogResult(NamedTuple):
    kind: str
    lifecycle: str
    request_id: str
    deployment_id: str
    replica_id: str
    logged_at: str
    logged_at_unix_ns: int
    runtime_started_at: str
    evidence_sha256: str
    evidence_body: bytes
    evidence: dict[str, Any]


class RecoveryMember(NamedTuple):
    descriptor: int
    name: str
    size: int
    sha256: str


def _canonical(value: object) -> bytes:
    return migration.canonical_document(value)


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical_time(value: object, *, label: str) -> datetime:
    try:
        parsed = migration._utc_timestamp(value, label=label)
    except migration.MigrationContractError as exc:
        raise ControlContractError("control timestamp is invalid") from exc
    canonical = parsed.isoformat().replace("+00:00", "Z")
    if value != canonical:
        raise ControlContractError("control timestamp is not canonical")
    return parsed


def railway_command_identity(environment: Mapping[str, str]) -> dict[str, str]:
    value = {
        "project_id": environment.get("RAILWAY_PROJECT_ID", ""),
        "environment_id": environment.get("RAILWAY_ENVIRONMENT_ID", ""),
        "service_id": environment.get("RAILWAY_SERVICE_ID", ""),
        "deployment_id": environment.get("RAILWAY_DEPLOYMENT_ID", ""),
        "volume_id": environment.get("SEICHE_RAILWAY_VOLUME_ID", ""),
    }
    if any(_UUID_RE.fullmatch(item) is None for item in value.values()):
        raise ControlContractError("Railway control identity is invalid")
    return value


def load_signer_registry(
    path: Path = SIGNER_REGISTRY_PATH,
) -> dict[str, tuple[bytes, frozenset[str]]]:
    try:
        body = migration._stable_read(path, maximum_bytes=MAX_REGISTRY_BYTES)
        value = migration._decode_canonical_json(
            body,
            label="Railway control signer registry",
        )
    except migration.MigrationContractError as exc:
        raise ControlContractError("control signer registry is unavailable") from exc
    if value != EXPECTED_SIGNER_REGISTRY:
        raise ControlContractError("control signer registry differs from release")
    signers: dict[str, tuple[bytes, frozenset[str]]] = {}
    for signer in value["signers"]:
        public_key = bytes.fromhex(signer["public_key_ed25519"])
        if (
            len(public_key) != 32
            or _digest(public_key) != signer["key_id"]
            or signer["key_id"] in signers
        ):
            raise ControlContractError("control signer registry is invalid")
        signers[signer["key_id"]] = (
            public_key,
            frozenset(signer["allowed_operations"]),
        )
    return signers


def command_id_for(value: Mapping[str, Any]) -> str:
    if set(value) != _COMMAND_ID_KEYS:
        raise ControlContractError("control command-id fields are invalid")
    return _digest(_COMMAND_ID_DOMAIN + _canonical(dict(value)))


def command_signing_bytes(value: Mapping[str, Any]) -> bytes:
    if set(value) != _SIGNING_KEYS:
        raise ControlContractError("control signature fields are invalid")
    operation = value.get("operation")
    if operation not in OPERATIONS:
        raise ControlContractError("control operation is invalid")
    return _SIGNATURE_DOMAIN + operation.encode("ascii") + b"\0" + _canonical(
        dict(value)
    )


def prepare_unsigned_command(
    operation: str,
    payload: Mapping[str, Any],
    environment: Mapping[str, str],
    *,
    issued_at: str,
    expires_at: str,
    nonce: str,
    key_id: str,
) -> dict[str, Any]:
    """Build the one canonical unsigned shape consumed by workflow signers."""

    if operation not in OPERATIONS or not isinstance(payload, Mapping):
        raise ControlContractError("control command input is invalid")
    request_value = payload.get("request")
    default_request_id = (
        request_value.get("request_id", "")
        if isinstance(request_value, Mapping)
        else payload.get("request_id", "")
    )
    value: dict[str, Any] = {
        "schema": COMMAND_SCHEMA,
        "repository": migration.REPOSITORY,
        "workflow": OPERATION_WORKFLOWS[operation],
        "commit": environment.get("SEICHE_RELEASE_SHA", ""),
        "railway": railway_command_identity(environment),
        "request_id": str(default_request_id),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "operation": operation,
        "key_id": key_id,
        "payload_sha256": _digest(_canonical(dict(payload))),
        "payload": dict(payload),
    }
    if operation == ACTIVATION_OPERATION:
        grant = payload.get("grant")
        value["request_id"] = str(
            grant.get("request_id", "") if isinstance(grant, Mapping) else ""
        )
    elif operation == OFFSITE_ACKNOWLEDGMENT_OPERATION:
        offsite = payload.get("offsite_receipt")
        value["request_id"] = str(
            offsite.get("request_id", "") if isinstance(offsite, Mapping) else ""
        )
    value["command_id"] = command_id_for(value)
    # Structural validation which does not require a private signature.
    if (
        _SHA40_RE.fullmatch(value["commit"]) is None
        or _SHA64_RE.fullmatch(value["request_id"]) is None
        or _SHA64_RE.fullmatch(nonce) is None
        or _SHA64_RE.fullmatch(key_id) is None
    ):
        raise ControlContractError("control command input identity is invalid")
    issued = _canonical_time(issued_at, label="control issued_at")
    expires = _canonical_time(expires_at, label="control expires_at")
    if not issued < expires <= issued + COMMAND_MAX_LIFETIME:
        raise ControlContractError("control command lifetime is invalid")
    _validate_payload(value)
    return value


def _validate_payload(value: Mapping[str, Any]) -> None:
    operation = value["operation"]
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ControlContractError("control payload is invalid")
    request_id = value["request_id"]
    commit = value["commit"]
    deployment_id = value["railway"]["deployment_id"]
    if operation == ACTIVATION_OPERATION:
        if set(payload) != {"public_probe", "grant"} or not all(
            isinstance(payload.get(name), dict) for name in payload
        ):
            raise ControlContractError("activation control payload is invalid")
        grant = payload["grant"]
        probe = payload["public_probe"]
        if (
            grant.get("request_id") != request_id
            or grant.get("commit") != commit
            or grant.get("deployment_id") != deployment_id
            or probe.get("commit") != commit
            or probe.get("deployment_id") != deployment_id
        ):
            raise ControlContractError("activation control payload is not bound")
    elif operation == RECOVERY_EXPORT_OPERATION:
        if set(payload) != {"request"} or not isinstance(payload["request"], dict):
            raise ControlContractError("recovery control payload is invalid")
        request = payload["request"]
        if (
            request.get("request_id") != request_id
            or request.get("commit") != commit
            or request.get("deployment_id") != deployment_id
            or _SHA64_RE.fullmatch(str(request.get("download_bearer_sha256", "")))
            is None
        ):
            raise ControlContractError("recovery control payload is not bound")
        requested_at = _canonical_time(
            request.get("requested_at"), label="recovery requested_at"
        )
        download_expires = _canonical_time(
            request.get("download_expires_at"), label="recovery download expires_at"
        )
        if not requested_at < download_expires <= requested_at + DOWNLOAD_MAX_LIFETIME:
            raise ControlContractError("recovery download lifetime is invalid")
    else:
        if set(payload) != {
            "recovery_request_sha256",
            "recovery_receipt_sha256",
            "offsite_receipt",
        }:
            raise ControlContractError("off-site control payload is invalid")
        offsite = payload.get("offsite_receipt")
        if (
            _SHA64_RE.fullmatch(str(payload.get("recovery_request_sha256", "")))
            is None
            or _SHA64_RE.fullmatch(str(payload.get("recovery_receipt_sha256", "")))
            is None
            or not isinstance(offsite, dict)
            or offsite.get("request_id") != request_id
            or offsite.get("commit") != commit
            or offsite.get("recovery_receipt_sha256")
            != payload.get("recovery_receipt_sha256")
        ):
            raise ControlContractError("off-site control payload is not bound")


def validate_command(
    body: bytes,
    environment: Mapping[str, str],
    *,
    now: datetime | None = None,
    registry_path: Path = SIGNER_REGISTRY_PATH,
    require_current: bool = True,
    require_mode: bool = True,
) -> ValidatedCommand:
    if not body or len(body) > MAX_COMMAND_BYTES:
        raise ControlContractError("control command size is invalid")
    try:
        value = migration._decode_canonical_json(body, label="Railway control command")
    except migration.MigrationContractError as exc:
        raise ControlContractError("control command is invalid") from exc
    if set(value) != _COMMAND_KEYS:
        raise ControlContractError("control command fields are invalid")
    operation = value.get("operation")
    expected_workflow = OPERATION_WORKFLOWS.get(str(operation))
    railway = value.get("railway")
    if (
        value.get("schema") != COMMAND_SCHEMA
        or value.get("repository") != migration.REPOSITORY
        or expected_workflow is None
        or value.get("workflow") != expected_workflow
        or _SHA40_RE.fullmatch(str(value.get("commit", ""))) is None
        or value.get("commit") != environment.get("SEICHE_RELEASE_SHA")
        or not isinstance(railway, dict)
        or set(railway) != _RAILWAY_KEYS
        or railway != railway_command_identity(environment)
        or _SHA64_RE.fullmatch(str(value.get("request_id", ""))) is None
        or _SHA64_RE.fullmatch(str(value.get("nonce", ""))) is None
        or _SHA64_RE.fullmatch(str(value.get("command_id", ""))) is None
        or _SHA64_RE.fullmatch(str(value.get("payload_sha256", ""))) is None
        or _SHA64_RE.fullmatch(str(value.get("key_id", ""))) is None
    ):
        raise ControlContractError("control command identity is invalid")
    mode = environment.get("SEICHE_RAILWAY_STATEFUL_MODE", "")
    allowed_for_mode = (
        {ACTIVATION_OPERATION}
        if mode == "cutover_candidate"
        else {RECOVERY_EXPORT_OPERATION, OFFSITE_ACKNOWLEDGMENT_OPERATION}
        if mode == "production"
        else set()
    )
    if require_mode and operation not in allowed_for_mode:
        raise ControlContractError("control operation is unavailable in this mode")
    payload = value.get("payload")
    if not isinstance(payload, dict) or value["payload_sha256"] != _digest(
        _canonical(payload)
    ):
        raise ControlContractError("control payload digest is invalid")
    issued_at = _canonical_time(value.get("issued_at"), label="control issued_at")
    expires_at = _canonical_time(value.get("expires_at"), label="control expires_at")
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ControlContractError("control clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    if (
        issued_at > observed + COMMAND_FUTURE_SKEW
        or (require_current and expires_at <= observed)
        or not issued_at < expires_at <= issued_at + COMMAND_MAX_LIFETIME
    ):
        raise ControlContractError("control command is stale")
    command_id_value = {name: value[name] for name in _COMMAND_ID_KEYS}
    if value["command_id"] != command_id_for(command_id_value):
        raise ControlContractError("control command id is invalid")
    _validate_payload(value)
    signers = load_signer_registry(registry_path)
    signer = signers.get(value["key_id"])
    if signer is None or operation not in signer[1]:
        raise ControlContractError("control signer is not authorized")
    signature_text = value.get("signature")
    if (
        not isinstance(signature_text, str)
        or not signature_text
        or len(signature_text) > 128
        or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", signature_text) is None
    ):
        raise ControlContractError("control signature is invalid")
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ControlContractError("control signature is invalid") from exc
    if len(signature) != 64 or base64.b64encode(signature).decode() != signature_text:
        raise ControlContractError("control signature is invalid")
    signing_value = {name: value[name] for name in _SIGNING_KEYS}
    try:
        Ed25519PublicKey.from_public_bytes(signer[0]).verify(
            signature,
            command_signing_bytes(signing_value),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ControlContractError("control signature is invalid") from exc
    return ValidatedCommand(
        value["command_id"],
        str(operation),
        value["request_id"],
        body,
        value,
    )


def _open_directory(path: Path, *, uid: int, gid: int, mode: int) -> int:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ControlContractError("control dropbox is unavailable") from exc
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ControlContractError("control dropbox is unsafe") from exc
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        or opened.st_uid != uid
        or opened.st_gid != gid
        or stat.S_IMODE(opened.st_mode) != mode
    ):
        os.close(descriptor)
        raise ControlContractError("control dropbox metadata is unsafe")
    return descriptor


def control_dropbox(platform_root: Path | None = None) -> Path:
    return (platform_root or migration.PLATFORM_ROOT) / CONTROL_ROOT_NAME / DROPBOX_NAME


def accepted_commands_root(platform_root: Path | None = None) -> Path:
    return (platform_root or migration.PLATFORM_ROOT) / CONTROL_ROOT_NAME / ACCEPTED_NAME


def processing_commands_root(platform_root: Path | None = None) -> Path:
    return (platform_root or migration.PLATFORM_ROOT) / CONTROL_ROOT_NAME / PROCESSING_NAME


def control_staging_root(platform_root: Path | None = None) -> Path:
    return (platform_root or migration.PLATFORM_ROOT) / CONTROL_ROOT_NAME / STAGING_NAME


def prepare_control_dropbox(
    *,
    platform_root: Path | None = None,
    runtime_gid: int = migration.RUNTIME_GID,
    root_uid: int = 0,
    root_gid: int = 0,
) -> Path:
    if os.geteuid() != root_uid or os.getegid() != root_gid:
        raise ControlContractError("control dropbox requires root")
    root = platform_root or migration.PLATFORM_ROOT
    control = root / CONTROL_ROOT_NAME
    dropbox = control / DROPBOX_NAME
    processing = control / PROCESSING_NAME
    accepted = control / ACCEPTED_NAME
    staging = control / STAGING_NAME
    try:
        control.mkdir(mode=0o750, parents=True, exist_ok=True)
        dropbox.mkdir(mode=0o1730, exist_ok=True)
        processing.mkdir(mode=0o710, exist_ok=True)
        accepted.mkdir(mode=0o710, exist_ok=True)
        staging.mkdir(mode=0o1730, exist_ok=True)
        for path, mode in (
            (control, 0o750),
            (dropbox, 0o1730),
            (processing, 0o710),
            (accepted, 0o710),
            (staging, 0o1730),
        ):
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                os.fchown(descriptor, root_uid, runtime_gid)
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        migration._fsync_directory(control)
    except OSError as exc:
        raise ControlContractError("control dropbox preparation failed") from exc
    descriptor = _open_directory(
        dropbox,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o1730,
    )
    os.close(descriptor)
    descriptor = _open_directory(
        processing,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o710,
    )
    os.close(descriptor)
    descriptor = _open_directory(
        accepted,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o710,
    )
    os.close(descriptor)
    descriptor = _open_directory(
        staging,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o1730,
    )
    os.close(descriptor)
    return dropbox


def submit_command(
    body: bytes,
    environment: Mapping[str, str],
    *,
    platform_root: Path | None = None,
    now: datetime | None = None,
    registry_path: Path = SIGNER_REGISTRY_PATH,
    runtime_uid: int = migration.RUNTIME_UID,
    runtime_gid: int = migration.RUNTIME_GID,
    root_uid: int = 0,
) -> CommandSubmission:
    if os.geteuid() != runtime_uid or os.getegid() != runtime_gid:
        raise ControlContractError("control API identity is invalid")
    command = validate_command(
        body,
        environment,
        now=now,
        registry_path=registry_path,
        require_mode=False,
    )
    dropbox = control_dropbox(platform_root)
    descriptor = _open_directory(
        dropbox,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o1730,
    )
    os.close(descriptor)
    accepted = accepted_commands_root(platform_root)
    descriptor = _open_directory(
        accepted,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o710,
    )
    os.close(descriptor)
    accepted_path = accepted / f"{command.command_id}.json"
    accepted_replay = False
    if accepted_path.exists() or accepted_path.is_symlink():
        try:
            existing = migration._stable_read(
                accepted_path,
                maximum_bytes=MAX_COMMAND_BYTES,
            )
        except migration.MigrationContractError as exc:
            raise ControlContractError("control replay state is unsafe") from exc
        if not hmac.compare_digest(existing, body):
            raise ControlContractError("control command replay differs")
        if command.operation == ACTIVATION_OPERATION:
            return CommandSubmission(command.command_id, "reused")
        accepted_replay = True
    processing = processing_commands_root(platform_root)
    descriptor = _open_directory(
        processing,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o710,
    )
    os.close(descriptor)
    processing_path = processing / f"{command.command_id}.json"
    if processing_path.exists() or processing_path.is_symlink():
        try:
            existing = migration._stable_read(
                processing_path,
                maximum_bytes=MAX_COMMAND_BYTES,
            )
        except migration.MigrationContractError as exc:
            raise ControlContractError("control replay state is unsafe") from exc
        if not hmac.compare_digest(existing, body):
            raise ControlContractError("control command replay differs")
        return CommandSubmission(command.command_id, "reused")
    command = validate_command(
        body,
        environment,
        now=now,
        registry_path=registry_path,
    )
    path = dropbox / f"{command.command_id}.json"
    if path.exists() or path.is_symlink():
        try:
            existing = migration._stable_read(path, maximum_bytes=MAX_COMMAND_BYTES)
        except migration.MigrationContractError as exc:
            raise ControlContractError("control replay state is unsafe") from exc
        if not hmac.compare_digest(existing, body):
            raise ControlContractError("control command replay differs")
        return CommandSubmission(command.command_id, "reused")
    staging = control_staging_root(platform_root)
    descriptor = _open_directory(
        staging,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o1730,
    )
    os.close(descriptor)
    try:
        descriptor, stage_name = tempfile.mkstemp(prefix=".command-", dir=staging)
    except OSError as exc:
        raise ControlContractError("control proposal could not be staged") from exc
    stage = Path(stage_name)
    try:
        written = 0
        while written < len(body):
            count = os.write(descriptor, body[written:])
            if count <= 0:
                raise OSError("control proposal write made no progress")
            written += count
        os.fchmod(descriptor, 0o440)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            metadata.st_uid != runtime_uid
            or metadata.st_gid != runtime_gid
            or stat.S_IMODE(metadata.st_mode) != 0o440
        ):
            raise ControlContractError("control proposal metadata is invalid")
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(stage, path, follow_symlinks=False)
            lifecycle = "reused" if accepted_replay else "created"
        except FileExistsError:
            existing = migration._stable_read(path, maximum_bytes=MAX_COMMAND_BYTES)
            if not hmac.compare_digest(existing, body):
                raise ControlContractError("control command replay differs")
            lifecycle = "reused"
    except BaseException:
        try:
            stage.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            stage.unlink()
        except FileNotFoundError:
            pass
    migration._fsync_directory(staging)
    migration._fsync_directory(dropbox)
    return CommandSubmission(command.command_id, lifecycle)


def pending_commands(
    environment: Mapping[str, str],
    *,
    operations: frozenset[str],
    platform_root: Path | None = None,
    now: datetime | None = None,
    registry_path: Path = SIGNER_REGISTRY_PATH,
    runtime_uid: int = migration.RUNTIME_UID,
    runtime_gid: int = migration.RUNTIME_GID,
    root_uid: int = 0,
) -> list[PendingCommand]:
    if os.geteuid() != root_uid:
        raise ControlContractError("control promotion requires root")
    if not operations or not operations <= OPERATIONS:
        raise ControlContractError("control promotion operation set is invalid")
    dropbox = control_dropbox(platform_root)
    descriptor = _open_directory(
        dropbox,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o1730,
    )
    os.close(descriptor)
    processing = processing_commands_root(platform_root)
    descriptor = _open_directory(
        processing,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o710,
    )
    os.close(descriptor)
    _repair_accepted_commands(
        environment,
        platform_root=platform_root,
        now=now,
        registry_path=registry_path,
        runtime_uid=runtime_uid,
        runtime_gid=runtime_gid,
        root_uid=root_uid,
    )
    processing_entries = sorted(processing.iterdir(), key=lambda item: item.name)
    dropbox_entries = sorted(dropbox.iterdir(), key=lambda item: item.name)
    if len(processing_entries) + len(dropbox_entries) > MAX_PENDING_COMMANDS:
        raise ControlContractError("control dropbox exceeds capacity")
    pending: list[PendingCommand] = []
    observed: set[tuple[str, str]] = set()
    for path in processing_entries:
        command = _load_journal_command(
            path,
            environment,
            now=now,
            registry_path=registry_path,
            allowed_uids={root_uid, runtime_uid},
            runtime_gid=runtime_gid,
            require_current=False,
            require_mode=False,
        )
        if command.operation not in operations:
            raise ControlContractError("control proposal scope is invalid")
        if path.lstat().st_uid != root_uid:
            _take_processing_ownership(
                path,
                command,
                runtime_gid=runtime_gid,
                root_uid=root_uid,
            )
        identity = (command.operation, command.request_id)
        if identity in observed:
            raise ControlContractError("control proposal is duplicated")
        observed.add(identity)
        pending.append(PendingCommand(path, command))
    for path in dropbox_entries:
        _repair_inflight_submission(
            path,
            platform_root=platform_root,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            root_uid=root_uid,
        )
        command = _load_journal_command(
            path,
            environment,
            now=now,
            registry_path=registry_path,
            allowed_uids={runtime_uid},
            runtime_gid=runtime_gid,
            require_current=True,
            require_mode=True,
        )
        if command.operation not in operations:
            raise ControlContractError("control proposal scope is invalid")
        identity = (command.operation, command.request_id)
        if identity in observed:
            raise ControlContractError("control proposal is duplicated")
        destination = processing / path.name
        if destination.exists() or destination.is_symlink():
            raise ControlContractError("processing control command is duplicated")
        try:
            path.rename(destination)
        except OSError as exc:
            raise ControlContractError("control proposal could not be claimed") from exc
        migration._fsync_directory(dropbox)
        migration._fsync_directory(processing)
        _take_processing_ownership(
            destination,
            command,
            runtime_gid=runtime_gid,
            root_uid=root_uid,
        )
        observed.add(identity)
        pending.append(PendingCommand(destination, command))
    return pending


def _repair_inflight_submission(
    path: Path,
    *,
    platform_root: Path | None,
    runtime_uid: int,
    runtime_gid: int,
    root_uid: int,
) -> None:
    """Collapse the temporary hard link used for atomic no-clobber publish."""

    metadata = path.lstat()
    if metadata.st_nlink == 1:
        return
    if metadata.st_nlink != 2:
        raise ControlContractError("control proposal link count is unsafe")
    staging = control_staging_root(platform_root)
    descriptor = _open_directory(
        staging,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o1730,
    )
    os.close(descriptor)
    entries = tuple(staging.iterdir())
    if len(entries) > MAX_PENDING_COMMANDS * 2:
        raise ControlContractError("control staging directory exceeds capacity")
    matches: list[Path] = []
    for entry in entries:
        candidate = entry.lstat()
        if (candidate.st_dev, candidate.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            continue
        if (
            re.fullmatch(r"\.command-[A-Za-z0-9_-]+", entry.name) is None
            or not stat.S_ISREG(candidate.st_mode)
            or candidate.st_uid != runtime_uid
            or candidate.st_gid != runtime_gid
            or stat.S_IMODE(candidate.st_mode) != 0o440
            or candidate.st_nlink != 2
        ):
            raise ControlContractError("control staging link is unsafe")
        matches.append(entry)
    if len(matches) != 1:
        raise ControlContractError("control proposal links are not closed")
    try:
        matches[0].unlink()
    except OSError as exc:
        raise ControlContractError("control staging link could not be repaired") from exc
    migration._fsync_directory(staging)
    repaired = path.lstat()
    if (
        repaired.st_nlink != 1
        or (repaired.st_dev, repaired.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise ControlContractError("control proposal link repair differs")


def _load_journal_command(
    path: Path,
    environment: Mapping[str, str],
    *,
    now: datetime | None,
    registry_path: Path,
    allowed_uids: set[int],
    runtime_gid: int,
    require_current: bool,
    require_mode: bool,
) -> ValidatedCommand:
    if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
        raise ControlContractError("control journal is not closed")
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_gid != runtime_gid
        or stat.S_IMODE(metadata.st_mode) != 0o440
        or metadata.st_uid not in allowed_uids
    ):
        raise ControlContractError("control proposal metadata is unsafe")
    try:
        body = migration._stable_read(path, maximum_bytes=MAX_COMMAND_BYTES)
    except migration.MigrationContractError as exc:
        raise ControlContractError("control proposal is unsafe") from exc
    command = validate_command(
        body,
        environment,
        now=now,
        registry_path=registry_path,
        require_current=require_current,
        require_mode=require_mode,
    )
    if path.stem != command.command_id:
        raise ControlContractError("control proposal identity is invalid")
    return command


def _take_processing_ownership(
    path: Path,
    command: ValidatedCommand,
    *,
    runtime_gid: int,
    root_uid: int,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ControlContractError("processing control proposal is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        body = bytearray()
        while len(body) <= MAX_COMMAND_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_COMMAND_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if (
            bytes(body) != command.body
            or len(body) > MAX_COMMAND_BYTES
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_gid != runtime_gid
            or stat.S_IMODE(metadata.st_mode) != 0o440
        ):
            raise ControlContractError("processing control proposal is unsafe")
        os.fchown(descriptor, root_uid, runtime_gid)
        os.fchmod(descriptor, 0o440)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    migration._fsync_directory(path.parent)


def _repair_accepted_commands(
    environment: Mapping[str, str],
    *,
    platform_root: Path | None,
    now: datetime | None,
    registry_path: Path,
    runtime_uid: int,
    runtime_gid: int,
    root_uid: int,
) -> None:
    """Finish an archive ownership transition interrupted after atomic rename."""

    accepted = accepted_commands_root(platform_root)
    directory = _open_directory(
        accepted,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o710,
    )
    os.close(directory)
    entries = sorted(accepted.iterdir(), key=lambda item: item.name)
    if len(entries) > MAX_ACCEPTED_COMMANDS:
        raise ControlContractError("accepted control archive exceeds capacity")
    repaired = False
    for path in entries:
        if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
            raise ControlContractError("accepted control archive is not closed")
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_gid != runtime_gid
            or stat.S_IMODE(metadata.st_mode) != 0o440
            or metadata.st_uid not in {root_uid, runtime_uid}
        ):
            raise ControlContractError("accepted control command metadata is unsafe")
        if metadata.st_uid == root_uid:
            continue
        try:
            body = migration._stable_read(path, maximum_bytes=MAX_COMMAND_BYTES)
        except migration.MigrationContractError as exc:
            raise ControlContractError("accepted control command is unsafe") from exc
        command = validate_command(
            body,
            environment,
            now=now,
            registry_path=registry_path,
            require_current=False,
            require_mode=False,
        )
        if path.stem != command.command_id:
            raise ControlContractError("accepted control command identity is invalid")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            current = os.fstat(descriptor)
            if (
                current.st_dev,
                current.st_ino,
                current.st_mtime_ns,
                current.st_size,
            ) != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mtime_ns,
                metadata.st_size,
            ):
                raise ControlContractError("accepted control command changed")
            os.fchown(descriptor, root_uid, runtime_gid)
            os.fchmod(descriptor, 0o440)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        repaired = True
    if repaired:
        migration._fsync_directory(accepted)


def seal_command(
    pending: PendingCommand,
    *,
    platform_root: Path | None = None,
    runtime_gid: int = migration.RUNTIME_GID,
    root_uid: int = 0,
) -> None:
    if os.geteuid() != root_uid:
        raise ControlContractError("control sealing requires root")
    processing = processing_commands_root(platform_root)
    descriptor = _open_directory(
        processing,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o710,
    )
    os.close(descriptor)
    if pending.path.parent != processing or pending.path.name != (
        f"{pending.command.command_id}.json"
    ):
        raise ControlContractError("control proposal path is invalid")
    accepted = accepted_commands_root(platform_root)
    descriptor = _open_directory(
        accepted,
        uid=root_uid,
        gid=runtime_gid,
        mode=0o710,
    )
    os.close(descriptor)
    accepted_entries = tuple(accepted.iterdir())
    if len(accepted_entries) >= MAX_ACCEPTED_COMMANDS:
        raise ControlContractError("accepted control archive exceeds capacity")
    if any(
        re.fullmatch(r"[0-9a-f]{64}\.json", item.name) is None
        for item in accepted_entries
    ):
        raise ControlContractError("accepted control archive is not closed")
    destination = accepted / pending.path.name
    if destination.exists() or destination.is_symlink():
        try:
            existing = migration._stable_read(
                destination,
                maximum_bytes=MAX_COMMAND_BYTES,
            )
        except migration.MigrationContractError as exc:
            raise ControlContractError("accepted control command is unsafe") from exc
        if not hmac.compare_digest(existing, pending.command.body):
            raise ControlContractError("accepted control command differs")
        try:
            pending.path.unlink()
        except OSError as exc:
            raise ControlContractError("duplicate control proposal remained") from exc
        migration._fsync_directory(processing)
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(pending.path, flags)
    try:
        before = os.fstat(descriptor)
        body = bytearray()
        while len(body) <= MAX_COMMAND_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_COMMAND_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if (
            bytes(body) != pending.command.body
            or len(body) > MAX_COMMAND_BYTES
            or before.st_uid != root_uid
            or before.st_gid != runtime_gid
            or stat.S_IMODE(before.st_mode) != 0o440
            or before.st_nlink != 1
            or not stat.S_ISREG(before.st_mode)
        ):
            raise ControlContractError("control proposal changed before sealing")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_size,
        ):
            raise ControlContractError("control proposal changed during sealing")
    finally:
        os.close(descriptor)
    try:
        pending.path.rename(destination)
    except OSError as exc:
        raise ControlContractError("control proposal could not be archived") from exc
    migration._fsync_directory(accepted)
    migration._fsync_directory(processing)


def _validate_log_evidence(
    kind: str,
    evidence: Mapping[str, Any],
    *,
    request_id: str,
    commit: str,
    deployment_id: str,
) -> None:
    if kind == "candidate":
        request = evidence.get("request")
        railway = evidence.get("railway")
        valid = (
            isinstance(request, dict)
            and request.get("id") == request_id
            and request.get("commit") == commit
            and isinstance(railway, dict)
            and railway.get("deployment_id") == deployment_id
        )
    elif kind in {"activation", "recovery_created"}:
        railway = evidence.get("railway")
        valid = (
            evidence.get("request_id") == request_id
            and evidence.get("commit") == commit
            and isinstance(railway, dict)
            and railway.get("deployment_id") == deployment_id
        )
    else:
        valid = (
            set(evidence)
            == {
                "schema",
                "request_id",
                "recovery_receipt_sha256",
                "offsite_receipt_sha256",
                "recovery_receipt",
                "offsite_receipt",
            }
            and evidence.get("schema") == PAIRED_EVIDENCE_SCHEMA
            and evidence.get("request_id") == request_id
            and isinstance(evidence.get("recovery_receipt"), dict)
            and isinstance(evidence.get("offsite_receipt"), dict)
            and evidence["recovery_receipt"].get("request_id") == request_id
            and evidence["recovery_receipt"].get("commit") == commit
            and evidence["recovery_receipt"].get("railway", {}).get("deployment_id")
            == deployment_id
            and evidence["offsite_receipt"].get("request_id") == request_id
            and evidence["offsite_receipt"].get("commit") == commit
            and evidence["offsite_receipt"].get("schema") == OFFSITE_RECEIPT_SCHEMA
            and evidence["offsite_receipt"].get("recovery_receipt_sha256")
            == evidence.get("recovery_receipt_sha256")
            and evidence.get("recovery_receipt_sha256")
            == _digest(_canonical(evidence["recovery_receipt"]))
            and evidence.get("offsite_receipt_sha256")
            == _digest(_canonical(evidence["offsite_receipt"]))
        )
    if not valid:
        raise ControlContractError("stateful log evidence binding is invalid")


def render_log_result(
    evidence: Mapping[str, Any],
    *,
    kind: str,
    lifecycle: str,
    request_id: str,
    environment: Mapping[str, str],
    runtime_started_at: str,
) -> str:
    if kind not in RESULT_KINDS or lifecycle not in RESULT_LIFECYCLES:
        raise ControlContractError("stateful log result lifecycle is invalid")
    if _SHA64_RE.fullmatch(request_id) is None:
        raise ControlContractError("stateful log request identity is invalid")
    commit = environment.get("SEICHE_RELEASE_SHA", "")
    deployment_id = environment.get("RAILWAY_DEPLOYMENT_ID", "")
    replica_id = environment.get("RAILWAY_REPLICA_ID", "")
    if (
        _SHA40_RE.fullmatch(commit) is None
        or _UUID_RE.fullmatch(deployment_id) is None
        or _UUID_RE.fullmatch(replica_id) is None
    ):
        raise ControlContractError("stateful log Railway identity is invalid")
    _canonical_time(runtime_started_at, label="runtime started_at")
    evidence_value = dict(evidence)
    _validate_log_evidence(
        kind,
        evidence_value,
        request_id=request_id,
        commit=commit,
        deployment_id=deployment_id,
    )
    evidence_body = _canonical(evidence_value)
    if not evidence_body or len(evidence_body) > MAX_LOG_EVIDENCE_BYTES:
        raise ControlContractError("stateful log evidence exceeds capacity")
    envelope = {
        "schema": LOG_RESULT_SCHEMA,
        "kind": kind,
        "lifecycle": lifecycle,
        "repository": migration.REPOSITORY,
        "workflow": RESULT_WORKFLOWS[kind],
        "commit": commit,
        "deployment_id": deployment_id,
        "replica_id": replica_id,
        "request_id": request_id,
        "runtime_started_at": runtime_started_at,
        "evidence_sha256": _digest(evidence_body),
        "evidence": evidence_value,
    }
    marker = LOG_RESULT_MARKER + base64.b64encode(_canonical(envelope)).decode("ascii")
    if len(marker.encode("ascii")) > MAX_LOG_RESULT_BYTES:
        raise ControlContractError("stateful log result exceeds capacity")
    return marker


def extract_log_results(
    body: bytes,
    *,
    expected_kind: str,
    expected_request_id: str,
    expected_commit: str,
    expected_deployment_id: str,
    expected_replicas: Mapping[str, str],
    not_before: str,
) -> dict[str, StatefulLogResult]:
    if not body or len(body) > MAX_DEPLOYMENT_LOG_BYTES:
        raise ControlContractError("Railway deployment log size is invalid")
    if (
        expected_kind not in RESULT_KINDS
        or not expected_replicas
        or not set(expected_replicas) <= RESULT_LIFECYCLES
        or _SHA64_RE.fullmatch(expected_request_id) is None
        or _SHA40_RE.fullmatch(expected_commit) is None
        or _UUID_RE.fullmatch(expected_deployment_id) is None
        or any(_UUID_RE.fullmatch(item) is None for item in expected_replicas.values())
    ):
        raise ControlContractError("expected stateful log identity is invalid")
    not_before_ns = migration.rfc3339_utc_nanoseconds(
        not_before,
        label="stateful log not-before",
    )
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlContractError("Railway deployment logs are not UTF-8") from exc
    results: dict[str, StatefulLogResult] = {}
    for line in text.splitlines():
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ControlContractError("Railway deployment log line is invalid") from exc
        if not isinstance(record, dict) or not isinstance(record.get("message"), str):
            raise ControlContractError("Railway deployment log record is invalid")
        message = record["message"]
        if LOG_RESULT_MARKER not in message:
            continue
        if not message.startswith(LOG_RESULT_MARKER):
            raise ControlContractError("stateful log result framing is invalid")
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str):
            raise ControlContractError("stateful log timestamp is absent")
        logged_at_ns = migration.rfc3339_utc_nanoseconds(
            timestamp,
            label="Railway stateful log timestamp",
        )
        if logged_at_ns < not_before_ns:
            continue
        encoded = message.removeprefix(LOG_RESULT_MARKER)
        if (
            not encoded
            or len(message.encode("utf-8")) > MAX_LOG_RESULT_BYTES
            or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", encoded) is None
        ):
            raise ControlContractError("stateful log result encoding is invalid")
        try:
            envelope_body = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ControlContractError("stateful log result is malformed") from exc
        if base64.b64encode(envelope_body).decode("ascii") != encoded:
            raise ControlContractError("stateful log result encoding is not canonical")
        try:
            envelope = migration._decode_canonical_json(
                envelope_body,
                label="stateful log result",
            )
        except migration.MigrationContractError as exc:
            raise ControlContractError("stateful log result is invalid") from exc
        kind = envelope.get("kind")
        lifecycle = envelope.get("lifecycle")
        if (
            set(envelope) != _LOG_RESULT_KEYS
            or envelope.get("schema") != LOG_RESULT_SCHEMA
            or kind not in RESULT_KINDS
            or lifecycle not in RESULT_LIFECYCLES
            or envelope.get("repository") != migration.REPOSITORY
            or envelope.get("workflow") != RESULT_WORKFLOWS[kind]
            or _SHA40_RE.fullmatch(str(envelope.get("commit", ""))) is None
            or _UUID_RE.fullmatch(str(envelope.get("deployment_id", ""))) is None
            or _UUID_RE.fullmatch(str(envelope.get("replica_id", ""))) is None
            or _SHA64_RE.fullmatch(str(envelope.get("request_id", ""))) is None
        ):
            raise ControlContractError("stateful log result fields are invalid")
        runtime_started_at = envelope.get("runtime_started_at")
        started = _canonical_time(runtime_started_at, label="runtime started_at")
        if int(started.timestamp()) * 1_000_000_000 > logged_at_ns:
            raise ControlContractError("stateful log result timing is invalid")
        evidence = envelope.get("evidence")
        if not isinstance(evidence, dict):
            raise ControlContractError("stateful log evidence is invalid")
        evidence_body = _canonical(evidence)
        if (
            not evidence_body
            or len(evidence_body) > MAX_LOG_EVIDENCE_BYTES
            or envelope.get("evidence_sha256") != _digest(evidence_body)
        ):
            raise ControlContractError("stateful log evidence digest is invalid")
        _validate_log_evidence(
            str(kind),
            evidence,
            request_id=str(envelope["request_id"]),
            commit=str(envelope["commit"]),
            deployment_id=str(envelope["deployment_id"]),
        )
        # One deployment accumulates candidate, activation, and many recovery
        # results. Fully authenticate every marker, then select only the exact
        # kind/request/lifecycle the caller asked for.
        if kind != expected_kind or envelope["request_id"] != expected_request_id:
            continue
        if lifecycle not in expected_replicas:
            continue
        if (
            envelope["commit"] != expected_commit
            or envelope["deployment_id"] != expected_deployment_id
            or envelope["replica_id"] != expected_replicas[lifecycle]
        ):
            raise ControlContractError("stateful log result is stale or unexpected")
        result = StatefulLogResult(
            expected_kind,
            lifecycle,
            expected_request_id,
            expected_deployment_id,
            expected_replicas[lifecycle],
            timestamp,
            logged_at_ns,
            str(runtime_started_at),
            _digest(evidence_body),
            evidence_body,
            evidence,
        )
        existing = results.get(lifecycle)
        if existing is not None:
            # Railway may retain the replica UUID across a restart.  The
            # supervisor then emits another reused marker with a new runtime
            # boundary.  Accept that only when every durable identity and the
            # canonical evidence bytes are unchanged, and deterministically
            # retain the newest log record.  Same-timestamp duplicates remain
            # ambiguous and fail closed.
            if (
                existing.evidence_body != result.evidence_body
                or existing.evidence_sha256 != result.evidence_sha256
                or existing.logged_at_unix_ns == result.logged_at_unix_ns
            ):
                raise ControlContractError("stateful log result is duplicated")
            if result.logged_at_unix_ns > existing.logged_at_unix_ns:
                results[lifecycle] = result
        else:
            results[lifecycle] = result
    if set(results) != set(expected_replicas):
        raise ControlContractError("stateful log result lifecycle set is incomplete")
    if len(results) > 1 and len(
        {item.evidence_sha256 for item in results.values()}
    ) != 1:
        raise ControlContractError("stateful log result evidence changed")
    return results


def extract_log_result(
    body: bytes,
    *,
    expected_kind: str,
    expected_lifecycle: str,
    expected_request_id: str,
    expected_commit: str,
    expected_deployment_id: str,
    expected_replica_id: str,
    not_before: str,
) -> StatefulLogResult:
    return extract_log_results(
        body,
        expected_kind=expected_kind,
        expected_request_id=expected_request_id,
        expected_commit=expected_commit,
        expected_deployment_id=expected_deployment_id,
        expected_replicas={expected_lifecycle: expected_replica_id},
        not_before=not_before,
    )[expected_lifecycle]


def extract_latest_recovery_pair(
    body: bytes,
    *,
    expected_commit: str,
    expected_deployment_id: str,
    now: datetime,
    max_age: timedelta = timedelta(hours=26),
) -> tuple[StatefulLogResult, StatefulLogResult]:
    """Select the newest exact recovery/off-site pair from one deployment log."""

    if (
        not body
        or len(body) > MAX_DEPLOYMENT_LOG_BYTES
        or _SHA40_RE.fullmatch(expected_commit) is None
        or _UUID_RE.fullmatch(expected_deployment_id) is None
        or now.tzinfo is None
        or now.utcoffset() is None
        or not timedelta(minutes=1) <= max_age <= timedelta(hours=48)
    ):
        raise ControlContractError("recovery monitor identity is invalid")
    current = now.astimezone(UTC)
    cutoff = current - max_age
    cutoff_ns = int(cutoff.timestamp()) * 1_000_000_000 + cutoff.microsecond * 1000
    current_ns = int(current.timestamp()) * 1_000_000_000 + current.microsecond * 1000
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlContractError("Railway deployment logs are not UTF-8") from exc
    candidates: list[tuple[int, dict[str, Any]]] = []
    for line in text.splitlines():
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ControlContractError("Railway deployment log line is invalid") from exc
        if not isinstance(record, dict) or not isinstance(record.get("message"), str):
            raise ControlContractError("Railway deployment log record is invalid")
        message = record["message"]
        if LOG_RESULT_MARKER not in message:
            continue
        if not message.startswith(LOG_RESULT_MARKER):
            raise ControlContractError("stateful log result framing is invalid")
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str):
            raise ControlContractError("stateful log timestamp is absent")
        logged_at_ns = migration.rfc3339_utc_nanoseconds(
            timestamp,
            label="Railway stateful log timestamp",
        )
        encoded = message.removeprefix(LOG_RESULT_MARKER)
        try:
            envelope_body = base64.b64decode(encoded, validate=True)
            envelope = migration._decode_canonical_json(
                envelope_body,
                label="stateful log result",
            )
        except (binascii.Error, ValueError, migration.MigrationContractError) as exc:
            raise ControlContractError("stateful log result is invalid") from exc
        if (
            envelope.get("kind") == "recovery_offsite_paired"
            and envelope.get("commit") == expected_commit
            and envelope.get("deployment_id") == expected_deployment_id
            and cutoff_ns <= logged_at_ns <= current_ns
        ):
            candidates.append((logged_at_ns, envelope))
    if not candidates:
        raise ControlContractError("current recovery pair is absent")
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise ControlContractError("current recovery pair is ambiguous")
    paired_meta = candidates[0][1]
    not_before = cutoff.isoformat().replace("+00:00", "Z")
    paired = extract_log_result(
        body,
        expected_kind="recovery_offsite_paired",
        expected_lifecycle=str(paired_meta.get("lifecycle", "")),
        expected_request_id=str(paired_meta.get("request_id", "")),
        expected_commit=expected_commit,
        expected_deployment_id=expected_deployment_id,
        expected_replica_id=str(paired_meta.get("replica_id", "")),
        not_before=not_before,
    )
    created_candidates: list[tuple[int, dict[str, Any]]] = []
    for line in text.splitlines():
        if not line:
            continue
        record = json.loads(line)
        message = record.get("message", "")
        if not isinstance(message, str) or not message.startswith(LOG_RESULT_MARKER):
            continue
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        logged_at_ns = migration.rfc3339_utc_nanoseconds(
            timestamp,
            label="Railway stateful log timestamp",
        )
        encoded = message.removeprefix(LOG_RESULT_MARKER)
        try:
            envelope = migration._decode_canonical_json(
                base64.b64decode(encoded, validate=True),
                label="stateful log result",
            )
        except (binascii.Error, ValueError, migration.MigrationContractError):
            continue
        if (
            envelope.get("kind") == "recovery_created"
            and envelope.get("request_id") == paired.request_id
            and envelope.get("commit") == expected_commit
            and envelope.get("deployment_id") == expected_deployment_id
            and cutoff_ns <= logged_at_ns <= paired.logged_at_unix_ns
        ):
            created_candidates.append((logged_at_ns, envelope))
    if not created_candidates:
        raise ControlContractError("paired recovery result is absent")
    created_candidates.sort(key=lambda item: item[0], reverse=True)
    created_meta = created_candidates[0][1]
    created = extract_log_result(
        body,
        expected_kind="recovery_created",
        expected_lifecycle=str(created_meta.get("lifecycle", "")),
        expected_request_id=paired.request_id,
        expected_commit=expected_commit,
        expected_deployment_id=expected_deployment_id,
        expected_replica_id=str(created_meta.get("replica_id", "")),
        not_before=not_before,
    )
    if (
        created.logged_at_unix_ns > paired.logged_at_unix_ns
        or paired.evidence.get("recovery_receipt_sha256")
        != created.evidence_sha256
        or paired.evidence.get("recovery_receipt") != created.evidence
    ):
        raise ControlContractError("recovery pair evidence differs")
    return created, paired


def decode_download_bearer(value: str) -> bytes:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        raise ControlContractError("recovery capability is invalid")
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except (binascii.Error, ValueError) as exc:
        raise ControlContractError("recovery capability is invalid") from exc
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
        raise ControlContractError("recovery capability is invalid")
    return decoded


def _open_verified_member(
    path: Path,
    *,
    name: str,
    expected_sha256: str,
    root_uid: int,
    runtime_gid: int,
) -> RecoveryMember:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ControlContractError("recovery member is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != root_uid
            or metadata.st_gid != runtime_gid
            or stat.S_IMODE(metadata.st_mode) != 0o440
            or metadata.st_size <= 0
            or metadata.st_size > MAX_RECOVERY_MEMBER_BYTES
        ):
            raise ControlContractError("recovery member metadata is unsafe")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        observed = digest.hexdigest()
        if not hmac.compare_digest(observed, expected_sha256):
            raise ControlContractError("recovery member digest differs")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return RecoveryMember(descriptor, name, metadata.st_size, observed)
    except BaseException:
        os.close(descriptor)
        raise


def open_recovery_member(
    request_id: str,
    member: str,
    bearer: str,
    environment: Mapping[str, str],
    *,
    platform_root: Path | None = None,
    now: datetime | None = None,
    root_uid: int = 0,
    runtime_gid: int = migration.RUNTIME_GID,
) -> RecoveryMember:
    if _SHA64_RE.fullmatch(request_id) is None or member not in RECOVERY_MEMBER_NAMES:
        raise ControlContractError("recovery member request is invalid")
    from seiche import stateful_recovery as recovery

    root = platform_root or migration.PLATFORM_ROOT
    evidence_root = root / "recovery-evidence" / request_id
    evidence_metadata = evidence_root.lstat()
    if (
        not stat.S_ISDIR(evidence_metadata.st_mode)
        or evidence_metadata.st_uid != root_uid
        or evidence_metadata.st_gid != runtime_gid
        or stat.S_IMODE(evidence_metadata.st_mode) != 0o550
    ):
        raise ControlContractError("recovery evidence directory is unsafe")
    if {item.name for item in evidence_root.iterdir()} != RECOVERY_EVIDENCE_NAMES:
        raise ControlContractError("recovery evidence directory is not closed")
    request_path = evidence_root / "request.json"
    try:
        request_body = migration._stable_read(request_path, maximum_bytes=32 * 1024)
        request_value = migration._decode_canonical_json(
            request_body,
            label="recovery request",
        )
    except migration.MigrationContractError as exc:
        raise ControlContractError("recovery request is unavailable") from exc
    activation_body, activation = recovery.activation_context(environment)
    try:
        staged_activation_body = migration._stable_read(
            evidence_root / "activation-receipt.json",
            maximum_bytes=256 * 1024,
        )
    except migration.MigrationContractError as exc:
        raise ControlContractError("recovery activation evidence is unavailable") from exc
    if staged_activation_body != activation_body:
        raise ControlContractError("recovery activation evidence differs")
    request = recovery.validate_request(
        request_value,
        activation_receipt=activation,
        now=now,
        require_fresh=False,
    )
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ControlContractError("recovery download clock is invalid")
    if observed.astimezone(UTC).replace(microsecond=0) >= _canonical_time(
        request.get("download_expires_at"), label="download expires_at"
    ):
        raise ControlContractError("recovery download capability expired")
    capability = decode_download_bearer(bearer)
    if not hmac.compare_digest(
        _digest(capability),
        str(request.get("download_bearer_sha256", "")),
    ):
        raise ControlContractError("recovery capability is invalid")
    candidate_body, candidate = recovery.candidate_context(
        environment,
        activation_receipt=activation,
    )
    try:
        staged_candidate_body = migration._stable_read(
            evidence_root / "candidate-receipt.json",
            maximum_bytes=256 * 1024,
        )
        shadow_body = migration._stable_read(
            evidence_root / "shadow-receipt.json",
            maximum_bytes=256 * 1024,
        )
        shadow_value = migration._decode_canonical_json(
            shadow_body,
            label="recovery shadow evidence",
        )
    except migration.MigrationContractError as exc:
        raise ControlContractError("recovery chain evidence is unavailable") from exc
    if staged_candidate_body != candidate_body:
        raise ControlContractError("recovery candidate evidence differs")
    shadow = recovery.validate_shadow_chain(
        shadow_value,
        candidate_receipt=candidate,
    )
    snapshot_id = request["snapshot_id"]
    receipt_path = evidence_root / "recovery-receipt.json"
    try:
        receipt_body = migration._stable_read(receipt_path, maximum_bytes=256 * 1024)
        receipt_value = migration._decode_canonical_json(
            receipt_body,
            label="recovery receipt",
        )
    except migration.MigrationContractError as exc:
        raise ControlContractError("recovery receipt is unavailable") from exc
    receipt = recovery.validate_receipt(
        receipt_value,
        request=request,
        activation_receipt=activation,
        candidate_receipt=candidate,
        shadow_receipt=shadow,
        railway=migration.railway_identity(environment),
    )
    evidence = {
        "activation-receipt.json": (
            evidence_root / "activation-receipt.json",
            _digest(activation_body),
        ),
        "candidate-receipt.json": (
            evidence_root / "candidate-receipt.json",
            _digest(candidate_body),
        ),
        "shadow-receipt.json": (
            evidence_root / "shadow-receipt.json",
            _digest(shadow_body),
        ),
        "request.json": (request_path, _digest(request_body)),
        "recovery-receipt.json": (receipt_path, _digest(receipt_body)),
    }
    if member in evidence:
        path, expected_sha256 = evidence[member]
    else:
        generation = root / "recovery-snapshots" / snapshot_id
        generation_metadata = generation.lstat()
        if (
            not stat.S_ISDIR(generation_metadata.st_mode)
            or generation_metadata.st_uid != root_uid
            or generation_metadata.st_gid != runtime_gid
            or stat.S_IMODE(generation_metadata.st_mode) != 0o550
        ):
            raise ControlContractError("recovery generation is unsafe")
        path = generation / member
        expected_sha256 = (
            receipt["snapshot"]["inventory_sha256"]
            if member == "SHA256SUMS"
            else receipt["snapshot"]["member_sha256"].get(member, "")
        )
    if _SHA64_RE.fullmatch(str(expected_sha256)) is None:
        raise ControlContractError("recovery member proof is invalid")
    return _open_verified_member(
        path,
        name=member,
        expected_sha256=str(expected_sha256),
        root_uid=root_uid,
        runtime_gid=runtime_gid,
    )


def stream_recovery_member(member: RecoveryMember) -> Iterator[bytes]:
    descriptor = member.descriptor
    try:
        remaining = member.size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ControlContractError("recovery member was truncated")
            remaining -= len(chunk)
            yield chunk
        if os.read(descriptor, 1):
            raise ControlContractError("recovery member grew during transfer")
    finally:
        os.close(descriptor)
