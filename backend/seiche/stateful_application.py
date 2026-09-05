"""Explicit application successors for an already activated Railway data plane.

The migration receipts remain immutable. An application successor requires a
separately signed source-stop proof and a grant bound to the new deployment.
It reuses the current generation; it never restores an older backup over it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Mapping

from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration

REQUEST_SCHEMA = "seiche.railway-application-request.v1"
SIGNED_SCHEMA = "seiche.railway-application-approval.v1"
CANDIDATE_SCHEMA = "seiche.railway-application-candidate.v1"
ACTIVATION_SCHEMA = "seiche.railway-application-activation.v1"
SIGNATURE_NAMESPACE = "seiche-railway-application-v1"
SIGNER_PRINCIPAL = "seiche-railway-application-release"
OWNER_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV"
)
SSH_KEYGEN = "/usr/bin/ssh-keygen"
REQUEST_PATH = Path("/migration/request.json")
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_LIFETIME = timedelta(hours=1)

_REQUEST_KEYS = {
    "schema",
    "repository",
    "source_ref",
    "operation",
    "request_id",
    "commit",
    "tree",
    "source_archive_sha256",
    "source_bundle_sha256",
    "requested_at",
    "expires_at",
    "railway",
    "parent",
}
_PARENT_KEYS = {
    "commit",
    "deployment_id",
    "activation_request_id",
    "activation_sha256",
    "migration_activation_sha256",
    "candidate_sha256",
    "shadow_sha256",
    "recovery_request_sha256",
    "recovery_sha256",
    "offsite_sha256",
    "generation",
    "database",
}
_RAILWAY_KEYS = {
    "project_id",
    "environment_id",
    "service_id",
    "deployment_id",
    "region",
    "volume_id",
    "volume_name",
    "volume_mount_path",
}
CANDIDATE_AUTHORITY = {
    "mode": "cutover_candidate",
    "source": "none",
    "hetzner_writers_frozen": True,
    "railway_writers_started": False,
    "public_traffic_enabled": False,
}
PRODUCTION_AUTHORITY = {
    "mode": "production",
    "source": "railway",
    "hetzner_writers_frozen": True,
    "railway_writers_started": True,
    "public_traffic_enabled": True,
}


class ApplicationContractError(cutover.CutoverContractError):
    """The application transition is incomplete, untrusted, or mismatched."""


def canonical(value: object) -> bytes:
    return migration.canonical_document(value)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _closed(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ApplicationContractError(f"{label} fields are invalid")
    return value


def _hex(value: object, length: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None
    ):
        raise ApplicationContractError(f"{label} is invalid")
    return value


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str) or migration._UUID_RE.fullmatch(value) is None:
        raise ApplicationContractError(f"{label} is invalid")
    return value


def _window(value: Mapping[str, Any], *, now: datetime | None, current: bool) -> None:
    start = cutover._utc(value.get("requested_at"), label="application requested_at")
    end = cutover._utc(value.get("expires_at"), label="application expires_at")
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ApplicationContractError("application clock must be timezone-aware")
    if not start < end <= start + MAX_LIFETIME or start > observed + timedelta(
        minutes=1
    ):
        raise ApplicationContractError("application lifetime is invalid")
    if current and observed > end:
        raise ApplicationContractError("application approval expired")


def validate_railway(value: object, *, deployment: bool = True) -> dict[str, str]:
    keys = _RAILWAY_KEYS if deployment else _RAILWAY_KEYS - {"deployment_id"}
    railway = _closed(value, keys, "application Railway target")
    for name in keys & {
        "project_id",
        "environment_id",
        "service_id",
        "deployment_id",
        "volume_id",
    }:
        _uuid(railway[name], name)
    for name in ("region", "volume_name"):
        if (
            not isinstance(railway[name], str)
            or not railway[name]
            or len(railway[name]) > 128
        ):
            raise ApplicationContractError(f"application {name} is invalid")
    if railway["volume_mount_path"] != str(migration.PLATFORM_ROOT):
        raise ApplicationContractError("application volume mount differs")
    return railway


def validate_request(
    value: object, *, now: datetime | None = None, current: bool = True
) -> dict[str, Any]:
    request = _closed(value, _REQUEST_KEYS, "application request")
    if (
        request["schema"] != REQUEST_SCHEMA
        or request["repository"] != migration.REPOSITORY
        or request["source_ref"] != "refs/heads/main"
        or request["operation"] != "application_upgrade"
    ):
        raise ApplicationContractError("application request policy is invalid")
    for name in ("commit", "tree"):
        _hex(request[name], 40, name)
    for name in ("request_id", "source_archive_sha256", "source_bundle_sha256"):
        _hex(request[name], 64, name)
    if request["request_id"] != digest(
        {k: v for k, v in request.items() if k != "request_id"}
    ):
        raise ApplicationContractError("application request digest differs")
    validate_railway(request["railway"], deployment=False)
    parent = _closed(request["parent"], _PARENT_KEYS, "application parent")
    _hex(parent["commit"], 40, "parent commit")
    _uuid(parent["deployment_id"], "parent deployment")
    for name in _PARENT_KEYS - {"commit", "deployment_id", "generation", "database"}:
        _hex(parent[name], 64, name)
    if request["commit"] == parent["commit"]:
        raise ApplicationContractError(
            "application successor must change the source commit"
        )
    if (
        not isinstance(parent["generation"], str)
        or re.fullmatch(
            r"cutover-20[0-9]{6}T[0-9]{6}Z-[0-9a-f]{16}", parent["generation"]
        )
        is None
    ):
        raise ApplicationContractError("application generation is invalid")
    if (
        not isinstance(parent["database"], str)
        or re.fullmatch(
            r"seiche_s_20[0-9]{6}t[0-9]{6}z_[0-9a-f]{12}", parent["database"]
        )
        is None
    ):
        raise ApplicationContractError("application database is invalid")
    _window(request, now=now, current=current)
    return request


@lru_cache(maxsize=32)
def _verify_signature(message: bytes, signature: str, public_key: str) -> None:
    if (
        not isinstance(signature, str)
        or len(signature) > 4096
        or not signature.startswith("-----BEGIN SSH SIGNATURE-----\n")
        or not signature.endswith("-----END SSH SIGNATURE-----\n")
    ):
        raise ApplicationContractError("application SSH signature framing is invalid")
    with tempfile.TemporaryDirectory(prefix="seiche-application-verify-") as directory:
        root = Path(directory)
        allowed = root / "allowed-signers"
        signed = root / "approval.sig"
        allowed.write_text(f"{SIGNER_PRINCIPAL} {public_key}\n", encoding="ascii")
        signed.write_text(signature, encoding="ascii")
        result = subprocess.run(
            [
                SSH_KEYGEN,
                "-Y",
                "verify",
                "-f",
                str(allowed),
                "-I",
                SIGNER_PRINCIPAL,
                "-n",
                SIGNATURE_NAMESPACE,
                "-s",
                str(signed),
            ],
            input=message,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    if result.returncode:
        raise ApplicationContractError("application approval signature is invalid")


def validate_approval(value: object, purpose: str) -> dict[str, Any]:
    envelope = _closed(
        value, {"schema", "purpose", "payload", "signature"}, "application approval"
    )
    if envelope["schema"] != SIGNED_SCHEMA or envelope["purpose"] != purpose:
        raise ApplicationContractError("application approval purpose differs")
    if not isinstance(envelope["payload"], dict):
        raise ApplicationContractError("application approval payload is invalid")
    unsigned = {name: item for name, item in envelope.items() if name != "signature"}
    message = canonical(unsigned)
    if len(message) > MAX_DOCUMENT_BYTES:
        raise ApplicationContractError("application approval is too large")
    _verify_signature(message, envelope["signature"], OWNER_PUBLIC_KEY)
    return envelope["payload"]


def validate_source_fence(
    value: object,
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
    current: bool = True,
) -> dict[str, Any]:
    fence = _closed(
        validate_approval(value, "source_stopped"),
        {
            "request_id",
            "parent_activation_sha256",
            "requested_at",
            "expires_at",
            "deployment",
            "hetzner_writers_frozen",
            "api_stopped",
            "writers_stopped",
        },
        "application source fence",
    )
    if (
        fence["request_id"] != request["request_id"]
        or fence["parent_activation_sha256"] != request["parent"]["activation_sha256"]
        or any(
            fence[k] is not True
            for k in ("hetzner_writers_frozen", "api_stopped", "writers_stopped")
        )
    ):
        raise ApplicationContractError("application source fence binding differs")
    deployment = _closed(
        fence["deployment"],
        {
            "id",
            "projectId",
            "environmentId",
            "serviceId",
            "instances",
        },
        "stopped source deployment",
    )
    bindings = {
        "id": request["parent"]["deployment_id"],
        "projectId": request["railway"]["project_id"],
        "environmentId": request["railway"]["environment_id"],
        "serviceId": request["railway"]["service_id"],
    }
    if any(deployment[k] != v for k, v in bindings.items()):
        raise ApplicationContractError("application stopped source target differs")
    instances = deployment["instances"]
    if not isinstance(instances, list) or not 1 <= len(instances) <= 8:
        raise ApplicationContractError("stopped source instances are absent")
    ids = set()
    for item in instances:
        row = _closed(item, {"id", "status"}, "stopped source instance")
        identity = _uuid(row["id"], "stopped source instance")
        if identity in ids or row["status"] not in {"STOPPED", "EXITED"}:
            raise ApplicationContractError(
                "application source instance is not uniquely stopped"
            )
        ids.add(identity)
    _window(fence, now=now, current=current)
    return fence


def read_document(path: Path) -> dict[str, Any]:
    body = migration._stable_read(path, maximum_bytes=MAX_DOCUMENT_BYTES)
    return migration._decode_canonical_json(body, label="application document")


def load_request() -> dict[str, Any]:
    request = validate_request(read_document(REQUEST_PATH), current=False)
    for name, field in (
        ("source.tar", "source_archive_sha256"),
        ("source.bundle", "source_bundle_sha256"),
    ):
        if migration.sha256_file(REQUEST_PATH.parent / name) != request[field]:
            raise ApplicationContractError(f"application {name} digest differs")
    head = subprocess.check_output(
        ["git", "-C", "/workspace", "rev-parse", "HEAD"], text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "-C", "/workspace", "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    if head != request["commit"] or tree != request["tree"]:
        raise ApplicationContractError("application checkout identity differs")
    if subprocess.run(
        [
            "git",
            "-C",
            "/workspace",
            "merge-base",
            "--is-ancestor",
            request["parent"]["commit"],
            head,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        raise ApplicationContractError(
            "application source does not descend from its parent"
        )
    return request


def _counts(value: object) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(item) is not int or item < 0 for item in value)
    ):
        raise ApplicationContractError("application critical table counts are invalid")
    return value


def validate_candidate(
    value: object,
    *,
    request: Mapping[str, Any],
    current: bool = False,
) -> dict[str, Any]:
    candidate = _closed(
        value,
        {
            "schema",
            "request",
            "railway",
            "source_fence",
            "data",
            "authority",
            "validated_at",
            "research_only",
            "can_publish",
            "can_execute",
        },
        "application candidate",
    )
    if (
        candidate["schema"] != CANDIDATE_SCHEMA
        or candidate["request"]
        != {
            "id": request["request_id"],
            "sha256": digest(dict(request)),
            "commit": request["commit"],
            "tree": request["tree"],
        }
        or candidate["authority"] != CANDIDATE_AUTHORITY
        or candidate["research_only"] is not True
        or candidate["can_publish"] is not False
        or candidate["can_execute"] is not False
    ):
        raise ApplicationContractError("application candidate binding differs")
    railway = validate_railway(candidate["railway"])
    if {k: v for k, v in railway.items() if k != "deployment_id"} != request[
        "railway"
    ] or railway["deployment_id"] == request["parent"]["deployment_id"]:
        raise ApplicationContractError("application destination differs")
    fence = validate_source_fence(
        candidate["source_fence"], request=request, current=current
    )
    when = cutover._utc(candidate["validated_at"], label="application validated_at")
    if (
        not cutover._utc(fence["requested_at"], label="source stopped_at")
        <= when
        <= cutover._utc(fence["expires_at"], label="source proof expiry")
    ):
        raise ApplicationContractError(
            "application candidate predates its stopped source"
        )
    data = _closed(
        candidate["data"],
        {
            "generation",
            "database",
            "critical_table_counts",
            "agent_room_audit",
            "restored_from_backup",
        },
        "application data",
    )
    if (
        any(data[k] != request["parent"][k] for k in ("generation", "database"))
        or data["restored_from_backup"] is not False
    ):
        raise ApplicationContractError("application must preserve its current data")
    _counts(data["critical_table_counts"])
    migration.validate_agent_room_audit(data["agent_room_audit"])
    return candidate


def validate_grant(
    value: object,
    *,
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    current: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    grant = _closed(
        validate_approval(value, "activate"),
        {
            "request_sha256",
            "candidate_sha256",
            "parent_activation_sha256",
            "railway",
            "edge_token_sha256",
            "public_base_url",
            "public_probe_sha256",
            "requested_at",
            "expires_at",
            "confirmation",
        },
        "application grant",
    )
    if (
        grant["request_sha256"] != digest(dict(request))
        or grant["candidate_sha256"] != digest(dict(candidate))
        or grant["parent_activation_sha256"] != request["parent"]["activation_sha256"]
        or grant["railway"] != candidate["railway"]
        or grant["public_base_url"] != "https://api.seiche.info"
        or grant["confirmation"] != "STOPPED_PARENT_CURRENT_DATA_NEW_APPLICATION"
    ):
        raise ApplicationContractError("application grant binding differs")
    for name in ("edge_token_sha256", "public_probe_sha256"):
        _hex(grant[name], 64, name)
    _window(grant, now=now, current=current)
    if not (
        cutover._utc(candidate["validated_at"], label="candidate time")
        <= cutover._utc(grant["requested_at"], label="grant time")
        < cutover._utc(grant["expires_at"], label="grant expiry")
        <= cutover._utc(request["expires_at"], label="request expiry")
    ):
        raise ApplicationContractError("application grant lifetime differs")
    return grant


def validate_activation(value: object) -> dict[str, Any]:
    """Verify the new identity without rewriting the original migration proof."""
    activation = _closed(
        value,
        {
            "schema",
            "commit",
            "request_id",
            "candidate_receipt_sha256",
            "fence_sha256",
            "grant_sha256",
            "railway",
            "authority",
            "workers",
            "public",
            "activated_at",
            "workers_started_at",
            "research_only",
            "can_publish",
            "can_execute",
            "application",
        },
        "application activation",
    )
    if activation["schema"] != ACTIVATION_SCHEMA:
        raise ApplicationContractError("application activation schema differs")
    transition = _closed(
        activation["application"],
        {
            "request",
            "candidate",
            "grant",
            "migration_activation",
        },
        "application transition",
    )
    request = validate_request(transition["request"], current=False)
    candidate = validate_candidate(transition["candidate"], request=request)
    grant = validate_grant(
        transition["grant"], request=request, candidate=candidate, current=False
    )
    original = transition["migration_activation"]
    if (
        not isinstance(original, dict)
        or original.get("schema") != cutover.ACTIVATION_RECEIPT_SCHEMA
        or digest(original) != request["parent"]["migration_activation_sha256"]
        or original.get("authority") != PRODUCTION_AUTHORITY
        or original.get("candidate_receipt_sha256")
        != request["parent"]["candidate_sha256"]
        or activation["commit"] != request["commit"]
        or activation["request_id"] != request["request_id"]
        or activation["candidate_receipt_sha256"]
        != request["parent"]["candidate_sha256"]
        or activation["fence_sha256"] != original.get("fence_sha256")
        or activation["grant_sha256"] != digest(transition["grant"])
        or activation["railway"] != candidate["railway"]
        or activation["authority"] != PRODUCTION_AUTHORITY
        or activation["public"]
        != {
            "base_url": grant["public_base_url"],
            "probe_sha256": grant["public_probe_sha256"],
        }
        or activation["activated_at"] != grant["requested_at"]
        or activation["research_only"] is not True
        or activation["can_publish"] is not False
        or activation["can_execute"] is not False
    ):
        raise ApplicationContractError("application activation binding differs")
    workers = _closed(
        activation["workers"], set(cutover.worker_commands()), "application workers"
    )
    for name, expected in cutover.worker_commands().items():
        worker = _closed(
            workers[name], {"command", "process_started"}, "application worker"
        )
        command = worker["command"]
        if (
            worker["process_started"] is not True
            or not isinstance(command, list)
            or len(command) != len(expected)
            or command[1:] != expected[1:]
            or not isinstance(command[0], str)
            or not Path(command[0]).is_absolute()
            or not Path(command[0]).name.startswith("python")
        ):
            raise ApplicationContractError("application writer command differs")
    if cutover._utc(
        activation["workers_started_at"], label="writers started"
    ) < cutover._utc(grant["requested_at"], label="grant requested"):
        raise ApplicationContractError("application writers predate the grant")
    return activation


def load_parent(request: Mapping[str, Any], *, current: bool) -> dict[str, Any]:
    """Require the last completed restore/off-site pair, not just a backup file."""
    from seiche import stateful_recovery as recovery

    names = {
        "activation": "activation_sha256",
        "candidate": "candidate_sha256",
        "shadow": "shadow_sha256",
        "recovery-request": "recovery_request_sha256",
        "recovery": "recovery_sha256",
        "offsite": "offsite_sha256",
    }
    parent = {}
    for name, field in names.items():
        value = read_document(REQUEST_PATH.parent / "parent" / f"{name}.json")
        if digest(value) != request["parent"][field]:
            raise ApplicationContractError(f"application parent {name} digest differs")
        parent[name] = value
    activation = parent["activation"]
    if activation.get("schema") == ACTIVATION_SCHEMA:
        validate_activation(activation)
        original = activation["application"]["migration_activation"]
    else:
        original = activation
    if (
        original.get("schema") != cutover.ACTIVATION_RECEIPT_SCHEMA
        or digest(original) != request["parent"]["migration_activation_sha256"]
        or activation.get("commit") != request["parent"]["commit"]
        or activation.get("request_id") != request["parent"]["activation_request_id"]
        or activation.get("railway", {}).get("deployment_id")
        != request["parent"]["deployment_id"]
    ):
        raise ApplicationContractError("application parent identity differs")
    # A transfer between projects, environments, services or volumes is a new
    # migration. This operation only updates code on the existing data plane.
    for key in (
        "project_id",
        "environment_id",
        "service_id",
        "volume_id",
        "volume_mount_path",
    ):
        if activation.get("railway", {}).get(key) != request["railway"][key]:
            raise ApplicationContractError(
                "application parent data-plane scope differs"
            )
    recovery.validate_receipt(
        parent["recovery"],
        request=parent["recovery-request"],
        activation_receipt=activation,
        candidate_receipt=parent["candidate"],
        shadow_receipt=parent["shadow"],
    )
    recovery.validate_offsite_receipt(
        parent["offsite"], recovery_receipt=parent["recovery"], require_fresh=current
    )
    if (
        parent["candidate"]["filesystem"]["generation"]
        != request["parent"]["generation"]
        or parent["candidate"]["database"]["name"] != request["parent"]["database"]
    ):
        raise ApplicationContractError("application parent generation differs")
    parent["migration_activation"] = original
    return parent


def _runtime_paths(environment: Mapping[str, str], request: Mapping[str, Any]) -> None:
    generation = (
        migration.PLATFORM_ROOT / "generations" / request["parent"]["generation"]
    )
    expected = {
        "SEICHE_RUNTIME_DATA_DIR": generation / "api",
        "SEICHE_AGENT_ROOM_DB_PATH": generation
        / "api"
        / "_agent_room"
        / "agent-room.sqlite",
        "SEICHE_ATTEST_DIR": generation / "api" / "_attest",
        "SEICHE_RAW_CAPTURE_DIR": generation / "market" / "raw",
        "SEICHE_NORMALIZED_DIR": generation / "market" / "normalized",
        "SEICHE_BACKFILL_STATE_DIR": generation / "market" / "backfill",
        "SEICHE_VALIDATION_DIR": generation / "market" / "validation",
        "SEICHE_USD_FUNDING_CORE_EXPORT_DIR": generation
        / "market"
        / "exports"
        / "us-usd-funding-core-v1",
        "SEICHE_NBS_ROOT": generation / "nbs",
        "SEICHE_NBS_PUBLIC_DIR": generation / "nbs" / "public",
    }
    if any(environment.get(key) != str(path) for key, path in expected.items()):
        raise ApplicationContractError("application runtime data paths differ")
    from urllib.parse import urlsplit

    if (
        urlsplit(environment.get("SEICHE_DATABASE_URL", "")).path
        != "/" + request["parent"]["database"]
    ):
        raise ApplicationContractError("application runtime database differs")


def validate_runtime(
    environment: Mapping[str, str], *, production: bool
) -> dict[str, Any]:
    request = validate_request(read_document(REQUEST_PATH), current=False)
    railway = migration.railway_identity(environment)
    if (
        environment.get("SEICHE_RELEASE_SHA") != request["commit"]
        or environment.get("SEICHE_RAILWAY_APPLICATION_REQUEST_ID")
        != request["request_id"]
        or environment.get("SEICHE_RAILWAY_CUTOVER_REQUEST_ID") != request["request_id"]
        or environment.get("SEICHE_RAILWAY_STATEFUL_MODE")
        != ("production" if production else "cutover_candidate")
    ):
        raise ApplicationContractError("application runtime identity differs")
    _runtime_paths(environment, request)
    suffix = "activation" if production else "application-candidate"
    path = (
        migration.PLATFORM_ROOT
        / "cutover-receipts"
        / f"{request['request_id']}.{suffix}.json"
    )
    value = read_document(path)
    if production:
        validate_activation(value)
        if (
            environment.get("SEICHE_RAILWAY_ACTIVATION_RECEIPT_PATH") != str(path)
            or environment.get("SEICHE_RAILWAY_ACTIVATION_RECEIPT_SHA256")
            != digest(value)
            or value["application"]["request"] != request
        ):
            raise ApplicationContractError("application runtime activation differs")
        active = read_document(
            migration.PLATFORM_ROOT / "authority" / "application-active.json"
        )
        if active != {
            "request_id": request["request_id"],
            "commit": request["commit"],
            "deployment_id": railway["deployment_id"],
            "grant_sha256": value["grant_sha256"],
            "state": "active",
        }:
            raise ApplicationContractError("application authority has been superseded")
        candidate = value["application"]["candidate"]
    else:
        candidate = validate_candidate(value, request=request)
    if candidate["railway"] != railway:
        raise ApplicationContractError("application runtime deployment differs")
    original_path = (
        migration.PLATFORM_ROOT
        / "cutover-receipts"
        / f"{read_document(REQUEST_PATH.parent / 'parent' / 'candidate.json')['request']['id']}.candidate.json"
    )
    original = read_document(original_path)
    if (
        digest(original) != request["parent"]["candidate_sha256"]
        or environment.get("SEICHE_RAILWAY_CANDIDATE_RECEIPT_PATH")
        != str(original_path)
        or environment.get("SEICHE_RAILWAY_CANDIDATE_RECEIPT_SHA256")
        != digest(original)
        or environment.get("SEICHE_AGENT_ROOM_EXPECTED_KEY_ID")
        != migration.agent_room_expected_key_binding(
            original["filesystem"]["agent_room_audit"]
        )
        or cutover.edge_token_sha256(environment.get("SEICHE_RAILWAY_EDGE_TOKEN", ""))
        != environment.get("SEICHE_RAILWAY_EDGE_TOKEN_SHA256")
    ):
        raise ApplicationContractError("application runtime migration binding differs")
    return value
