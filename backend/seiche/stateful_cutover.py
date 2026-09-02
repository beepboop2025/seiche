"""Writer-fenced Railway cutover state machine for Seiche.

Phase 5 is intentionally split into two Railway states. A candidate restores
the final frozen Hetzner snapshot and serves authenticated reads only. A later
protected activation grant may start the Railway writers after the public edge
has proved that exact candidate. There is no automatic Hetzner resume path in
this runtime.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, NamedTuple

from seiche import stateful_migration as migration

FENCE_SCHEMA = "seiche.railway-authority-fence.v1"
REQUEST_SCHEMA = "seiche.railway-stateful-cutover-request.v1"
CANDIDATE_RECEIPT_SCHEMA = "seiche.railway-cutover-candidate-receipt.v4"
GRANT_SCHEMA = "seiche.railway-authority-grant.v1"
ACTIVATION_RECEIPT_SCHEMA = "seiche.railway-activation-receipt.v1"
PUBLIC_PROBE_SCHEMA = "seiche.railway-public-candidate-probe.v1"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-stateful-cutover.yml"
EDGE_HEADER = "x-seiche-edge-token"
FENCE_MAX_AGE = timedelta(hours=4)
GRANT_FUTURE_SKEW = timedelta(minutes=5)

_PUBLIC_PROBE_KEYS = frozenset(
    {
        "schema",
        "url",
        "status",
        "authority",
        "deployment_id",
        "commit",
        "body_sha256",
        "observed_at",
    }
)

FENCED_UNITS = (
    "seiche-api.service",
    "seiche-pull.service",
    "seiche-market-worker.service",
    "seiche-source-worker.service",
    "seiche-market-backfill.service",
    "seiche-snapshot-promote.service",
    "seiche-snapshot-import.service",
    "seiche-release-poll.service",
    "seiche-release-poll.timer",
    "seiche-release-recovery-seal.service",
    "seiche-data-readiness.service",
    "seiche-data-readiness.timer",
    "seiche-market-validation.service",
    "seiche-market-validation.timer",
    "seiche-market-backup.timer",
    "seiche-market-restore-check.timer",
    "seiche-market-offsite-backup.service",
    "seiche-market-offsite-backup.timer",
    "seiche-alert.service",
    "seiche-alert.timer",
    "seiche.service",
    "seiche-update.service",
    "seiche-update.timer",
)

_SHA40_RE = re.compile(r"[0-9a-f]{40}")
_SHA64_RE = re.compile(r"[0-9a-f]{64}")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-" r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_DOMAIN_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_REQUEST_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "source_ref",
        "commit",
        "tree",
        "source_archive_sha256",
        "source_bundle_sha256",
        "request_id",
        "operation",
        "snapshot_id",
        "source_revision",
        "source_inventory_sha256",
        "source_content_set_sha256",
        "source_release_receipt_sha256",
        "source_recovery_receipt_sha256",
        "source_shadow_receipt_sha256",
        "source_fence_sha256",
        "source_writers_frozen",
        "public_traffic_enabled",
        "requested_at",
    }
)
_SHADOW_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "request",
        "authority",
        "bundle",
        "database",
        "filesystem",
        "palimpsest_china_state",
        "railway",
        "timing",
        "research_only",
        "can_publish",
        "can_execute",
    }
)
_ACTIVATION_KEYS = frozenset(
    {
        "schema",
        "commit",
        "request_id",
        "candidate_receipt_sha256",
        "grant_sha256",
        "fence_sha256",
        "railway",
        "authority",
        "workers",
        "public",
        "activated_at",
        "workers_started_at",
        "research_only",
        "can_publish",
        "can_execute",
    }
)


class CutoverContractError(ValueError):
    """One closed Phase-5 authority contract failed validation."""


class CutoverRestore(NamedTuple):
    receipt: dict[str, Any]
    database_dsn: str
    receipt_path: Path
    generation_path: Path


def _utc(value: object, *, label: str) -> datetime:
    try:
        return migration._utc_timestamp(value, label=label)
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc


def _canonical(value: Mapping[str, Any]) -> bytes:
    return migration.canonical_document(dict(value))


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _validate_digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA64_RE.fullmatch(value) is None:
        raise CutoverContractError(f"{label} is invalid")
    return value


def _validate_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA40_RE.fullmatch(value) is None:
        raise CutoverContractError(f"{label} is invalid")
    return value


def validate_fence(
    value: object,
    *,
    now: datetime | None = None,
    require_current: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "repository",
        "commit",
        "tree",
        "authority",
        "snapshot",
        "receipts",
        "units",
        "can_activate_railway",
        "can_resume_hetzner_before_activation",
    }:
        raise CutoverContractError("authority fence fields are invalid")
    if (
        value.get("schema") != FENCE_SCHEMA
        or value.get("repository") != migration.REPOSITORY
        or value.get("can_activate_railway") is not True
        or value.get("can_resume_hetzner_before_activation") is not True
    ):
        raise CutoverContractError("authority fence policy is invalid")
    commit = _validate_sha(value.get("commit"), label="fence commit")
    _validate_sha(value.get("tree"), label="fence tree")
    authority = value.get("authority")
    if not isinstance(authority, dict) or set(authority) != {
        "source",
        "state",
        "writers_frozen",
        "api_stopped",
        "frozen_at",
        "expires_at",
    }:
        raise CutoverContractError("authority fence state is invalid")
    if (
        authority.get("source") != "hetzner"
        or authority.get("state") != "frozen"
        or authority.get("writers_frozen") is not True
        or authority.get("api_stopped") is not True
    ):
        raise CutoverContractError("Hetzner authority is not fully fenced")
    frozen_at = _utc(authority["frozen_at"], label="frozen_at")
    expires_at = _utc(authority["expires_at"], label="expires_at")
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise CutoverContractError("fence validation clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    if observed < frozen_at or (require_current and observed > expires_at):
        raise CutoverContractError("authority fence is not currently valid")
    if expires_at - frozen_at > FENCE_MAX_AGE:
        raise CutoverContractError("authority fence lifetime is too broad")
    snapshot = value.get("snapshot")
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "id",
        "source_revision",
        "inventory_sha256",
        "content_set_sha256",
        "restore_receipt_sha256",
    }:
        raise CutoverContractError("authority fence snapshot is invalid")
    if (
        not isinstance(snapshot["id"], str)
        or migration._SNAPSHOT_RE.fullmatch(snapshot["id"]) is None
        or _validate_sha(snapshot["source_revision"], label="fence snapshot revision")
        != commit
    ):
        raise CutoverContractError("authority fence snapshot identity is invalid")
    for name in (
        "inventory_sha256",
        "content_set_sha256",
        "restore_receipt_sha256",
    ):
        _validate_digest(snapshot[name], label=f"fence snapshot {name}")
    receipts = value.get("receipts")
    if not isinstance(receipts, dict) or set(receipts) != {
        "release_sha256",
        "recovery_sha256",
        "latest_shadow_sha256",
    }:
        raise CutoverContractError("authority fence receipt chain is invalid")
    for name, digest in receipts.items():
        _validate_digest(digest, label=f"fence receipt {name}")
    units = value.get("units")
    if not isinstance(units, dict) or set(units) != set(FENCED_UNITS):
        raise CutoverContractError("authority fence unit inventory is invalid")
    expected_state = {
        "active": False,
        "enabled": False,
        "runtime_masked": True,
    }
    if any(state != expected_state for state in units.values()):
        raise CutoverContractError("one or more Hetzner writers are not fenced")
    return dict(value)


def load_fence(
    path: Path,
    *,
    expected_sha256: str,
    now: datetime | None = None,
    require_current: bool = True,
) -> dict[str, Any]:
    _validate_digest(expected_sha256, label="expected fence digest")
    try:
        body = migration._stable_read(path, maximum_bytes=128 * 1024)
        value = migration._decode_canonical_json(body, label="authority fence")
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    if _digest(body) != expected_sha256:
        raise CutoverContractError("authority fence digest differs from request")
    return validate_fence(value, now=now, require_current=require_current)


def validate_request(
    value: object,
    *,
    fence: Mapping[str, Any],
    now: datetime | None = None,
    require_fresh: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _REQUEST_KEYS:
        raise CutoverContractError("cutover request fields are invalid")
    if (
        value.get("schema") != REQUEST_SCHEMA
        or value.get("repository") != migration.REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("source_ref") != migration.SOURCE_REF
        or value.get("operation") != "cutover_candidate"
        or value.get("source_writers_frozen") is not True
        or value.get("public_traffic_enabled") is not False
    ):
        raise CutoverContractError("cutover request policy is invalid")
    commit = _validate_sha(value.get("commit"), label="cutover commit")
    _validate_sha(value.get("tree"), label="cutover tree")
    if _validate_sha(value.get("source_revision"), label="source revision") != commit:
        raise CutoverContractError("cutover snapshot does not match the candidate")
    for name in (
        "source_archive_sha256",
        "source_bundle_sha256",
        "request_id",
        "source_inventory_sha256",
        "source_content_set_sha256",
        "source_release_receipt_sha256",
        "source_recovery_receipt_sha256",
        "source_shadow_receipt_sha256",
        "source_fence_sha256",
    ):
        _validate_digest(value.get(name), label=f"cutover {name}")
    requested_at = _utc(value.get("requested_at"), label="requested_at")
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise CutoverContractError("cutover request clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    if requested_at > observed + GRANT_FUTURE_SKEW or (
        require_fresh and requested_at < observed - FENCE_MAX_AGE
    ):
        raise CutoverContractError("cutover request is not fresh")
    snapshot = fence["snapshot"]
    receipts = fence["receipts"]
    bindings = {
        "commit": fence["commit"],
        "snapshot_id": snapshot["id"],
        "source_revision": snapshot["source_revision"],
        "source_inventory_sha256": snapshot["inventory_sha256"],
        "source_content_set_sha256": snapshot["content_set_sha256"],
        "source_release_receipt_sha256": receipts["release_sha256"],
        "source_recovery_receipt_sha256": receipts["recovery_sha256"],
        "source_shadow_receipt_sha256": receipts["latest_shadow_sha256"],
        "source_fence_sha256": _digest(_canonical(fence)),
    }
    if any(value.get(name) != expected for name, expected in bindings.items()):
        raise CutoverContractError("cutover request differs from the authority fence")
    return dict(value)


def load_request(
    request_path: Path,
    source_archive: Path,
    source_bundle: Path,
    fence: Mapping[str, Any],
    *,
    now: datetime | None = None,
    require_fresh: bool = True,
) -> dict[str, Any]:
    try:
        body = migration._stable_read(request_path, maximum_bytes=32 * 1024)
        request = migration._decode_canonical_json(body, label="cutover request")
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    validated = validate_request(
        request,
        fence=fence,
        now=now,
        require_fresh=require_fresh,
    )
    if migration.sha256_file(source_archive) != validated["source_archive_sha256"]:
        raise CutoverContractError("cutover source archive digest differs")
    if migration.sha256_file(source_bundle) != validated["source_bundle_sha256"]:
        raise CutoverContractError("cutover source bundle digest differs")
    return validated


def _generation_name(request: Mapping[str, Any]) -> str:
    return (
        f"cutover-{request['snapshot_id']}-"
        f"{str(request['source_content_set_sha256'])[:16]}"
    )


def render_candidate_receipt(
    request: Mapping[str, Any],
    fence: Mapping[str, Any],
    bundle: migration.BackupBundle,
    database: migration.RestoredDatabase,
    *,
    railway: Mapping[str, str],
    generation_digests: Mapping[str, str],
    nbs_audit_result: str,
    agent_room_audit: Mapping[str, Any],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    try:
        palimpsest_china_state = migration.palimpsest_china_state_from_audit(
            bundle.palimpsest_china_state_audit
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    return {
        "schema": CANDIDATE_RECEIPT_SCHEMA,
        "request": {
            "id": request["request_id"],
            "sha256": _digest(_canonical(request)),
            "commit": request["commit"],
            "tree": request["tree"],
            "source_shadow_receipt_sha256": request["source_shadow_receipt_sha256"],
        },
        "authority": {
            "mode": "cutover_candidate",
            "source": "none",
            "hetzner_writers_frozen": True,
            "railway_writers_started": False,
            "public_traffic_enabled": False,
        },
        "fence": {
            "sha256": request["source_fence_sha256"],
            "frozen_at": fence["authority"]["frozen_at"],
            "expires_at": fence["authority"]["expires_at"],
        },
        "bundle": {
            "schema": migration.BACKUP_SCHEMA,
            "snapshot_id": bundle.snapshot_id,
            "source_revision": bundle.source_revision,
            "source_inventory_sha256": bundle.inventory_sha256,
            "source_content_set_sha256": bundle.content_set_sha256,
            "member_sha256": dict(bundle.member_sha256),
            "total_bytes": bundle.total_bytes,
        },
        "database": {
            "name": database.name,
            "critical_table_counts": list(database.counts),
            "critical_table_count_floor": list(bundle.counts_floor),
            "restore": "pass",
        },
        "filesystem": {
            "generation": _generation_name(request),
            "tree_sha256": dict(generation_digests),
            "api_sqlite_quick_check": "pass",
            "agent_room_audit": migration.validate_agent_room_audit(agent_room_audit),
            "nbs_full_store_audit_contract": "seiche.nbs-full-store-audit.v1",
            "nbs_full_store_audit_result": nbs_audit_result,
            "palimpsest_china_state_audit_contract": (
                "seiche.palimpsest-china-activation-state.v1"
            ),
            "palimpsest_china_state_audit_result": "verified",
        },
        "palimpsest_china_state": palimpsest_china_state,
        "railway": dict(railway),
        "timing": {"started_at": started_at, "completed_at": completed_at},
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }


def validate_candidate_receipt(
    value: object,
    *,
    request: Mapping[str, Any],
    fence: Mapping[str, Any],
    railway: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "request",
        "authority",
        "fence",
        "bundle",
        "database",
        "filesystem",
        "palimpsest_china_state",
        "railway",
        "timing",
        "research_only",
        "can_publish",
        "can_execute",
    }:
        raise CutoverContractError("candidate receipt fields are invalid")
    if (
        value.get("schema") != CANDIDATE_RECEIPT_SCHEMA
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
        or value.get("request")
        != {
            "id": request["request_id"],
            "sha256": _digest(_canonical(request)),
            "commit": request["commit"],
            "tree": request["tree"],
            "source_shadow_receipt_sha256": request["source_shadow_receipt_sha256"],
        }
        or value.get("authority")
        != {
            "mode": "cutover_candidate",
            "source": "none",
            "hetzner_writers_frozen": True,
            "railway_writers_started": False,
            "public_traffic_enabled": False,
        }
        or value.get("fence")
        != {
            "sha256": request["source_fence_sha256"],
            "frozen_at": fence["authority"]["frozen_at"],
            "expires_at": fence["authority"]["expires_at"],
        }
    ):
        raise CutoverContractError("candidate receipt authority is invalid")
    bundle = value.get("bundle")
    if (
        not isinstance(bundle, dict)
        or bundle.get("schema") != migration.BACKUP_SCHEMA
        or bundle.get("snapshot_id") != request["snapshot_id"]
        or bundle.get("source_revision") != request["source_revision"]
        or bundle.get("source_inventory_sha256") != request["source_inventory_sha256"]
        or bundle.get("source_content_set_sha256")
        != request["source_content_set_sha256"]
        or not isinstance(bundle.get("member_sha256"), dict)
        or set(bundle["member_sha256"]) != set(migration._BACKUP_MEMBERS)
        or any(
            _SHA64_RE.fullmatch(str(item)) is None
            for item in bundle["member_sha256"].values()
        )
        or not isinstance(bundle.get("total_bytes"), int)
        or bundle["total_bytes"] <= 0
    ):
        raise CutoverContractError("candidate receipt bundle is invalid")
    database = value.get("database")
    if not isinstance(database, dict) or set(database) != {
        "name",
        "critical_table_counts",
        "critical_table_count_floor",
        "restore",
    }:
        raise CutoverContractError("candidate receipt database is invalid")
    if (
        database["name"]
        != migration.derive_database_name(
            str(request["snapshot_id"]),
            str(request["source_content_set_sha256"]),
        )
        or database["restore"] != "pass"
    ):
        raise CutoverContractError("candidate receipt database identity is invalid")
    for name in ("critical_table_counts", "critical_table_count_floor"):
        counts = database[name]
        if (
            not isinstance(counts, list)
            or len(counts) != 4
            or any(not isinstance(item, int) or item < 0 for item in counts)
        ):
            raise CutoverContractError("candidate receipt counts are invalid")
    if any(
        actual < floor
        for actual, floor in zip(
            database["critical_table_counts"],
            database["critical_table_count_floor"],
        )
    ):
        raise CutoverContractError("candidate receipt counts are below floor")
    filesystem = value.get("filesystem")
    if (
        not isinstance(filesystem, dict)
        or set(filesystem)
        != {
            "generation",
            "tree_sha256",
            "api_sqlite_quick_check",
            "agent_room_audit",
            "nbs_full_store_audit_contract",
            "nbs_full_store_audit_result",
            "palimpsest_china_state_audit_contract",
            "palimpsest_china_state_audit_result",
        }
        or filesystem.get("generation") != _generation_name(request)
        or filesystem.get("api_sqlite_quick_check") != "pass"
        or filesystem.get("nbs_full_store_audit_contract")
        != "seiche.nbs-full-store-audit.v1"
        or filesystem.get("nbs_full_store_audit_result")
        not in {"not_onboarded", "verified_head"}
        or filesystem.get("palimpsest_china_state_audit_contract")
        != "seiche.palimpsest-china-activation-state.v1"
        or filesystem.get("palimpsest_china_state_audit_result") != "verified"
        or not isinstance(filesystem.get("tree_sha256"), dict)
        or set(filesystem["tree_sha256"])
        != {"market", "nbs", "api", "palimpsest-china"}
        or any(
            _SHA64_RE.fullmatch(str(item)) is None
            for item in filesystem["tree_sha256"].values()
        )
    ):
        raise CutoverContractError("candidate receipt filesystem is invalid")
    try:
        migration.validate_agent_room_audit(filesystem.get("agent_room_audit"))
        migration.validate_palimpsest_china_state(value.get("palimpsest_china_state"))
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    observed_railway = value.get("railway")
    if not isinstance(observed_railway, dict) or set(observed_railway) != {
        "deployment_id",
        "project_id",
        "environment_id",
        "service_id",
        "volume_id",
        "volume_name",
        "volume_mount_path",
        "region",
    }:
        raise CutoverContractError("candidate Railway identity is invalid")
    if railway is not None and observed_railway != dict(railway):
        raise CutoverContractError("candidate belongs to another Railway runtime")
    for name in (
        "deployment_id",
        "project_id",
        "environment_id",
        "service_id",
        "volume_id",
    ):
        if _UUID_RE.fullmatch(str(observed_railway[name])) is None:
            raise CutoverContractError("candidate Railway UUID is invalid")
    if observed_railway["volume_mount_path"] != str(migration.PLATFORM_ROOT):
        raise CutoverContractError("candidate Railway volume mount is invalid")
    if (
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            str(observed_railway["volume_name"]),
        )
        is None
        or migration._REGION_RE.fullmatch(str(observed_railway["region"])) is None
    ):
        raise CutoverContractError("candidate Railway volume or region is invalid")
    timing = value.get("timing")
    if not isinstance(timing, dict) or set(timing) != {"started_at", "completed_at"}:
        raise CutoverContractError("candidate receipt timing is invalid")
    if _utc(timing["completed_at"], label="completed_at") < _utc(
        timing["started_at"], label="started_at"
    ):
        raise CutoverContractError("candidate receipt timing order is invalid")
    return dict(value)


def validate_source_shadow_receipt(
    value: object,
    *,
    request: Mapping[str, Any],
    expected_palimpsest_china_state: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _SHADOW_RECEIPT_KEYS:
        raise CutoverContractError("source shadow receipt fields are invalid")
    shadow_request = value.get("request")
    if (
        value.get("schema") != migration.RECEIPT_SCHEMA
        or _digest(_canonical(value)) != request["source_shadow_receipt_sha256"]
        or not isinstance(shadow_request, dict)
        or set(shadow_request)
        != {
            "id",
            "sha256",
            "commit",
            "tree",
            "source_archive_sha256",
            "source_bundle_sha256",
            "source_release_receipt_sha256",
            "source_recovery_receipt_sha256",
        }
        or shadow_request.get("commit") != request["commit"]
        or value.get("authority")
        != {
            "mode": "shadow",
            "source": "hetzner",
            "source_writers_frozen": False,
            "public_traffic_enabled": False,
            "workers_started": False,
        }
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
    ):
        raise CutoverContractError("source shadow receipt binding is invalid")
    for name in (
        "id",
        "sha256",
        "source_archive_sha256",
        "source_bundle_sha256",
        "source_release_receipt_sha256",
        "source_recovery_receipt_sha256",
    ):
        if (
            not isinstance(shadow_request.get(name), str)
            or _SHA64_RE.fullmatch(shadow_request[name]) is None
        ):
            raise CutoverContractError(f"source shadow request {name} is invalid")
    for name in ("commit", "tree"):
        if (
            not isinstance(shadow_request.get(name), str)
            or _SHA40_RE.fullmatch(shadow_request[name]) is None
        ):
            raise CutoverContractError(f"source shadow request {name} is invalid")
    try:
        shadow_state = migration.validate_palimpsest_china_state(
            value.get("palimpsest_china_state")
        )
        expected_state = migration.validate_palimpsest_china_state(
            expected_palimpsest_china_state
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    if shadow_state != expected_state:
        raise CutoverContractError("cutover Palimpsest China state differs from shadow")
    try:
        migration.validate_agent_room_audit(
            value.get("filesystem", {}).get("agent_room_audit")
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    return dict(value)


def load_source_shadow_receipt(
    platform_root: Path,
    *,
    request: Mapping[str, Any],
    expected_palimpsest_china_state: Mapping[str, Any],
) -> dict[str, Any]:
    receipts = platform_root / "receipts"
    if not receipts.is_dir() or receipts.is_symlink():
        raise CutoverContractError("source shadow receipt directory is unsafe")
    entries = sorted(receipts.iterdir(), key=lambda path: path.name)
    if len(entries) > 1024:
        raise CutoverContractError("source shadow receipt directory is unbounded")
    matches: list[dict[str, Any]] = []
    total_bytes = 0
    for path in entries:
        if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
            raise CutoverContractError("source shadow receipt directory is not closed")
        try:
            body = migration._stable_read(path, maximum_bytes=256 * 1024)
            value = migration._decode_canonical_json(body, label="shadow receipt")
        except migration.MigrationContractError as exc:
            raise CutoverContractError(str(exc)) from exc
        total_bytes += len(body)
        if total_bytes > 64 * 1024 * 1024:
            raise CutoverContractError(
                "source shadow receipt directory exceeds capacity"
            )
        if _digest(body) == request["source_shadow_receipt_sha256"]:
            matches.append(value)
    if len(matches) != 1:
        raise CutoverContractError("source shadow receipt is not unique")
    return validate_source_shadow_receipt(
        matches[0],
        request=request,
        expected_palimpsest_china_state=expected_palimpsest_china_state,
    )


def restore_candidate(
    request: Mapping[str, Any],
    fence: Mapping[str, Any],
    bundle: migration.BackupBundle,
    *,
    platform_root: Path,
    base_dsn: str,
    railway: Mapping[str, str],
    runtime_uid: int = migration.RUNTIME_UID,
    runtime_gid: int = migration.RUNTIME_GID,
    active_resume: bool = False,
) -> CutoverRestore:
    if not isinstance(active_resume, bool):
        raise TypeError("active_resume must be a boolean")
    if (
        bundle.schema != migration.BACKUP_SCHEMA
        or bundle.palimpsest_china_state_audit is None
    ):
        raise CutoverContractError(
            "cutover requires the current Palimpsest-state backup contract"
        )
    try:
        palimpsest_china_state = migration.palimpsest_china_state_from_audit(
            bundle.palimpsest_china_state_audit
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    source_shadow_receipt = load_source_shadow_receipt(
        platform_root,
        request=request,
        expected_palimpsest_china_state=palimpsest_china_state,
    )
    source_agent_room_audit = migration.validate_agent_room_audit(
        source_shadow_receipt.get("filesystem", {}).get("agent_room_audit")
    )
    started_at = migration._iso_now()
    generations = platform_root / "generations"
    receipts = platform_root / "cutover-receipts"
    try:
        migration._prepare_shared_directory(
            platform_root,
            gid=runtime_gid,
            parents=True,
        )
        migration._prepare_shared_directory(generations, gid=runtime_gid)
        migration._prepare_shared_directory(receipts, gid=runtime_gid)
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    generation_name = _generation_name(request)
    generation_path = generations / generation_name
    receipt_path = receipts / f"{request['request_id']}.candidate.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        try:
            body = migration._stable_read(receipt_path, maximum_bytes=256 * 1024)
            receipt = validate_candidate_receipt(
                migration._decode_canonical_json(body, label="candidate receipt"),
                request=request,
                fence=fence,
                railway=railway,
            )
            if (
                migration.validate_agent_room_audit(
                    receipt["filesystem"].get("agent_room_audit")
                )
                != source_agent_room_audit
            ):
                raise CutoverContractError(
                    "cutover Agent Room state differs from shadow"
                )
            if active_resume:
                migration.validate_active_generation(
                    generation_path,
                    receipt,
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                )
            else:
                migration.validate_receipted_generation(
                    generation_path,
                    receipt,
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                )
            dsn = migration._target_dsn(base_dsn, receipt["database"]["name"])
            observed_counts = migration.inspect_postgres_counts(dsn)
            receipt_counts = tuple(receipt["database"]["critical_table_counts"])
            if (
                any(
                    observed < expected
                    for observed, expected in zip(
                        observed_counts,
                        receipt_counts,
                        strict=True,
                    )
                )
                if active_resume
                else observed_counts != receipt_counts
            ):
                raise CutoverContractError(
                    "active PostgreSQL counts regressed"
                    if active_resume
                    else "candidate PostgreSQL counts changed"
                )
            return CutoverRestore(receipt, dsn, receipt_path, generation_path)
        except migration.MigrationContractError as exc:
            raise CutoverContractError(str(exc)) from exc
    if active_resume:
        raise CutoverContractError(
            "activation exists without its immutable candidate receipt"
        )
    if generation_path.exists() or generation_path.is_symlink():
        raise CutoverContractError(
            "unreceipted cutover generation needs reconciliation"
        )
    staging = platform_root / f".cutover-{request['request_id']}"
    if staging.exists() or staging.is_symlink():
        raise CutoverContractError("stale cutover staging needs reconciliation")
    staging.mkdir(mode=0o700)
    try:
        agent_room_audit: dict[str, Any] = {}
        nbs_result, tree_digests = migration.restore_filesystem_generation(
            bundle,
            staging,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            agent_room_audit_out=agent_room_audit,
        )
        if agent_room_audit != source_agent_room_audit:
            raise CutoverContractError("cutover Agent Room state differs from shadow")
        database = migration.restore_postgres(bundle, base_dsn)
        (staging / "generation").rename(generation_path)
        migration._fsync_directory(generations)
        receipt = render_candidate_receipt(
            request,
            fence,
            bundle,
            database,
            railway=railway,
            generation_digests=tree_digests,
            nbs_audit_result=nbs_result,
            agent_room_audit=agent_room_audit,
            started_at=started_at,
            completed_at=migration._iso_now(),
        )
        validate_candidate_receipt(
            receipt,
            request=request,
            fence=fence,
            railway=railway,
        )
        migration._write_receipt(receipt_path, receipt, gid=runtime_gid)
        return CutoverRestore(receipt, database.dsn, receipt_path, generation_path)
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    finally:
        if staging.is_dir() and not staging.is_symlink():
            import shutil

            shutil.rmtree(staging)


def edge_token_sha256(token: str) -> str:
    if (
        not isinstance(token, str)
        or not 32 <= len(token) <= 512
        or token != token.strip()
        or any(character.isspace() for character in token)
    ):
        raise CutoverContractError("Railway edge token is invalid")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def edge_request_allowed(provided: str | None, expected: str) -> bool:
    if provided is None:
        return False
    try:
        edge_token_sha256(expected)
    except CutoverContractError:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def candidate_environment(
    base: Mapping[str, str],
    restore: CutoverRestore,
    *,
    edge_token: str,
    runtime_uid: int = migration.RUNTIME_UID,
    runtime_gid: int = migration.RUNTIME_GID,
) -> dict[str, str]:
    receipt = restore.receipt
    generation = str(receipt["filesystem"]["generation"])
    root = restore.generation_path
    if root.name != generation or root.parent.name != "generations":
        raise CutoverContractError("candidate generation path is invalid")
    try:
        from seiche import palimpsest_china_activation as activation

        palimpsest_environment_names = {
            spec.environment for spec in activation._BUNDLE_FILE_SPECS
        }
    except Exception as exc:
        raise CutoverContractError(
            "candidate Palimpsest China runtime contract is unavailable"
        ) from exc
    environment = {
        key: value
        for key, value in base.items()
        if key
        not in (
            {
                "DATABASE_URL",
                "RAILWAY_TOKEN",
                "RAILWAY_API_TOKEN",
                "PYTHONHOME",
                "PYTHONPATH",
                "SEICHE_RUNTIME_DATA_DIR",
                "SEICHE_AGENT_ROOM_DB_PATH",
                "SEICHE_ATTEST_DIR",
                "SEICHE_AGENT_ROOM_EXPECTED_KEY_ID",
            }
            | palimpsest_environment_names
        )
    }
    runtime_data = root / "api"
    environment.update(
        {
            "HOME": "/tmp/seiche-home",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": "/workspace/backend",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "SEICHE_ENV": "production",
            "SEICHE_RELEASE_SHA": str(receipt["request"]["commit"]),
            "SEICHE_DATABASE_URL": restore.database_dsn,
            "SEICHE_RUNTIME_DATA_DIR": str(runtime_data),
            "SEICHE_AGENT_ROOM_DB_PATH": str(
                runtime_data / "_agent_room" / "agent-room.sqlite"
            ),
            "SEICHE_ATTEST_DIR": str(runtime_data / "_attest"),
            "SEICHE_AGENT_ROOM_EXPECTED_KEY_ID": (
                migration.agent_room_expected_key_binding(
                    receipt["filesystem"].get("agent_room_audit")
                )
            ),
            "SEICHE_RAW_CAPTURE_DIR": str(root / "market" / "raw"),
            "SEICHE_NORMALIZED_DIR": str(root / "market" / "normalized"),
            "SEICHE_BACKFILL_STATE_DIR": str(root / "market" / "backfill"),
            "SEICHE_VALIDATION_DIR": str(root / "market" / "validation"),
            "SEICHE_USD_FUNDING_CORE_EXPORT_DIR": str(
                root / "market" / "exports" / "us-usd-funding-core-v1"
            ),
            "SEICHE_NBS_ROOT": str(root / "nbs"),
            "SEICHE_NBS_PUBLIC_DIR": str(root / "nbs" / "public"),
            "SEICHE_RAILWAY_STATEFUL_MODE": "cutover_candidate",
            "SEICHE_RAILWAY_CANDIDATE_RECEIPT_PATH": str(restore.receipt_path),
            "SEICHE_RAILWAY_CANDIDATE_RECEIPT_SHA256": _digest(_canonical(receipt)),
            "SEICHE_RAILWAY_CUTOVER_REQUEST_ID": str(receipt["request"]["id"]),
            "SEICHE_RAILWAY_EDGE_TOKEN": edge_token,
            "SEICHE_RAILWAY_EDGE_TOKEN_SHA256": edge_token_sha256(edge_token),
            "SEICHE_COLLECTOR_HEARTBEAT_REQUIRED": "0",
            "SEICHE_SOURCE_HEARTBEAT_REQUIRED": "0",
        }
    )
    try:
        environment.update(
            migration.palimpsest_runtime_environment(
                root / "palimpsest-china",
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
            )
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    return environment


def _validate_bound_candidate_runtime_receipt(
    environment: Mapping[str, str],
) -> tuple[dict[str, Any], str]:
    """Load the exact candidate that owns runtime paths and room identity."""

    path = Path(environment.get("SEICHE_RAILWAY_CANDIDATE_RECEIPT_PATH", ""))
    if (
        not path.is_absolute()
        or path.parent != migration.PLATFORM_ROOT / "cutover-receipts"
        or not path.name.endswith(".candidate.json")
    ):
        raise CutoverContractError("Railway candidate receipt path is invalid")
    try:
        body = migration._stable_read(path, maximum_bytes=256 * 1024)
        value = migration._decode_canonical_json(body, label="candidate receipt")
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    receipt_sha256 = _digest(body)
    if (
        receipt_sha256
        != environment.get("SEICHE_RAILWAY_CANDIDATE_RECEIPT_SHA256")
        or value.get("schema") != CANDIDATE_RECEIPT_SCHEMA
        or value.get("request", {}).get("id")
        != environment.get("SEICHE_RAILWAY_CUTOVER_REQUEST_ID")
        or value.get("request", {}).get("commit")
        != environment.get("SEICHE_RELEASE_SHA")
        or value.get("authority", {}).get("mode") != "cutover_candidate"
        or value.get("authority", {}).get("hetzner_writers_frozen") is not True
        or value.get("authority", {}).get("railway_writers_started") is not False
    ):
        raise CutoverContractError("Railway candidate receipt binding is invalid")
    try:
        agent_room_audit = migration.validate_agent_room_audit(
            value.get("filesystem", {}).get("agent_room_audit")
        )
        migration.validate_palimpsest_china_state(value.get("palimpsest_china_state"))
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    if environment.get("SEICHE_AGENT_ROOM_EXPECTED_KEY_ID") != (
        migration.agent_room_expected_key_binding(agent_room_audit)
    ):
        raise CutoverContractError("Railway Agent Room key binding is invalid")
    return value, receipt_sha256


def validate_candidate_runtime(environment: Mapping[str, str]) -> dict[str, Any]:
    if environment.get("SEICHE_RAILWAY_STATEFUL_MODE") != "cutover_candidate":
        raise CutoverContractError("Railway candidate mode is invalid")
    value, _receipt_sha256 = _validate_bound_candidate_runtime_receipt(environment)
    if edge_token_sha256(
        environment.get("SEICHE_RAILWAY_EDGE_TOKEN", "")
    ) != environment.get("SEICHE_RAILWAY_EDGE_TOKEN_SHA256"):
        raise CutoverContractError("Railway edge token binding is invalid")
    return value


def validate_grant(
    value: object,
    *,
    candidate_receipt: Mapping[str, Any],
    edge_token_digest: str,
    now: datetime | None = None,
    require_fresh: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "repository",
        "workflow",
        "commit",
        "request_id",
        "candidate_receipt_sha256",
        "fence_sha256",
        "deployment_id",
        "edge_token_sha256",
        "public_base_url",
        "public_probe_sha256",
        "activated_at",
        "confirmation",
    }:
        raise CutoverContractError("activation grant fields are invalid")
    if (
        value.get("schema") != GRANT_SCHEMA
        or value.get("repository") != migration.REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("confirmation") != "RAILWAY_BECOMES_SOLE_WRITER"
        or value.get("commit") != candidate_receipt["request"]["commit"]
        or value.get("request_id") != candidate_receipt["request"]["id"]
        or value.get("candidate_receipt_sha256")
        != _digest(_canonical(candidate_receipt))
        or value.get("fence_sha256") != candidate_receipt["fence"]["sha256"]
        or value.get("deployment_id") != candidate_receipt["railway"]["deployment_id"]
        or value.get("edge_token_sha256") != edge_token_digest
    ):
        raise CutoverContractError("activation grant binding is invalid")
    _validate_digest(value.get("public_probe_sha256"), label="public probe digest")
    base_url = value.get("public_base_url")
    if not isinstance(base_url, str) or base_url != "https://api.seiche.info":
        raise CutoverContractError("activation public base URL is invalid")
    activated_at = _utc(value.get("activated_at"), label="activated_at")
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise CutoverContractError("activation clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    fence_expires = _utc(candidate_receipt["fence"]["expires_at"], label="expires_at")
    candidate_completed = _utc(
        candidate_receipt["timing"]["completed_at"],
        label="candidate completed_at",
    )
    if (
        activated_at > observed + GRANT_FUTURE_SKEW
        or (require_fresh and activated_at < observed - timedelta(minutes=15))
        or activated_at < candidate_completed
        or activated_at > fence_expires
    ):
        raise CutoverContractError("activation grant is not fresh or fence-valid")
    return dict(value)


def validate_public_candidate_probe(
    value: object,
    *,
    candidate_receipt: Mapping[str, Any],
    grant: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PUBLIC_PROBE_KEYS:
        raise CutoverContractError("public candidate probe fields are invalid")
    if (
        value.get("schema") != PUBLIC_PROBE_SCHEMA
        or value.get("url") != "https://api.seiche.info/api/health"
        or value.get("status") != 200
        or value.get("authority") != "candidate"
        or value.get("deployment_id") != candidate_receipt["railway"]["deployment_id"]
        or value.get("deployment_id") != grant["deployment_id"]
        or value.get("commit") != candidate_receipt["request"]["commit"]
        or value.get("commit") != grant["commit"]
        or _digest(_canonical(value)) != grant["public_probe_sha256"]
    ):
        raise CutoverContractError("public candidate probe binding is invalid")
    _validate_digest(value.get("body_sha256"), label="public probe body digest")
    observed_at = _utc(value.get("observed_at"), label="public probe observed_at")
    candidate_completed = _utc(
        candidate_receipt["timing"]["completed_at"],
        label="candidate completed_at",
    )
    granted_at = _utc(grant["activated_at"], label="grant activated_at")
    if not candidate_completed <= observed_at <= granted_at:
        raise CutoverContractError("public candidate probe timing is invalid")
    return dict(value)


def _publish_immutable_authority_file(
    root: Path,
    name: str,
    body: bytes,
    *,
    runtime_gid: int,
) -> None:
    destination = root / name
    if destination.exists() or destination.is_symlink():
        try:
            existing = migration._stable_read(destination, maximum_bytes=256 * 1024)
        except migration.MigrationContractError as exc:
            raise CutoverContractError(str(exc)) from exc
        if existing != body:
            raise CutoverContractError("immutable authority document differs")
        return
    descriptor, stage_name = tempfile.mkstemp(prefix=f".{name}.", dir=root)
    stage = Path(stage_name)
    try:
        written = 0
        while written < len(body):
            count = os.write(descriptor, body[written:])
            if count <= 0:
                raise OSError("authority write made no progress")
            written += count
        os.fchmod(descriptor, 0o440)
        os.fchown(descriptor, os.geteuid(), runtime_gid)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(stage, destination, follow_symlinks=False)
        except FileExistsError:
            try:
                existing = migration._stable_read(
                    destination,
                    maximum_bytes=256 * 1024,
                )
            except migration.MigrationContractError as exc:
                raise CutoverContractError(str(exc)) from exc
            if existing != body:
                raise CutoverContractError("immutable authority document differs")
        migration._fsync_directory(root)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            stage.unlink()
        except FileNotFoundError:
            pass
        migration._fsync_directory(root)


def publish_authority_documents(
    request_id: str,
    public_probe_body: bytes,
    grant_body: bytes,
    *,
    platform_root: Path | None = None,
    request_path: Path = Path("/migration/request.json"),
    source_archive: Path = Path("/migration/source.tar"),
    source_bundle: Path = Path("/migration/source.bundle"),
    edge_token: str | None = None,
    railway: Mapping[str, str] | None = None,
    now: datetime | None = None,
    require_fresh: bool = True,
    runtime_gid: int = migration.RUNTIME_GID,
) -> tuple[str, str]:
    _validate_digest(request_id, label="authority publication request")
    root = platform_root or migration.PLATFORM_ROOT
    try:
        grant_value = migration._decode_canonical_json(
            grant_body,
            label="activation grant",
        )
        probe_value = migration._decode_canonical_json(
            public_probe_body,
            label="public candidate probe",
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    if grant_value.get("request_id") != request_id:
        raise CutoverContractError("authority publication request differs")
    fence_digest = _validate_digest(
        grant_value.get("fence_sha256"),
        label="authority publication fence",
    )
    fence = load_fence(
        root / "authority-fences" / f"{fence_digest}.json",
        expected_sha256=fence_digest,
        now=now,
        require_current=require_fresh,
    )
    request = load_request(
        request_path,
        source_archive,
        source_bundle,
        fence,
        now=now,
        require_fresh=require_fresh,
    )
    candidate_path = root / "cutover-receipts" / f"{request_id}.candidate.json"
    try:
        candidate_body = migration._stable_read(
            candidate_path,
            maximum_bytes=256 * 1024,
        )
        candidate_value = migration._decode_canonical_json(
            candidate_body,
            label="candidate receipt",
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    observed_railway = (
        dict(railway) if railway is not None else migration.railway_identity(os.environ)
    )
    candidate = validate_candidate_receipt(
        candidate_value,
        request=request,
        fence=fence,
        railway=observed_railway,
    )
    token = (
        edge_token
        if edge_token is not None
        else os.environ.get(
            "SEICHE_RAILWAY_EDGE_TOKEN",
            "",
        )
    )
    grant = validate_grant(
        grant_value,
        candidate_receipt=candidate,
        edge_token_digest=edge_token_sha256(token),
        now=now,
        require_fresh=require_fresh,
    )
    validate_public_candidate_probe(
        probe_value,
        candidate_receipt=candidate,
        grant=grant,
    )
    authority_root = root / "authority"
    _prepare_authority_directory(authority_root, runtime_gid=runtime_gid)
    probe_name = f"{request_id}.public-probe.json"
    _publish_immutable_authority_file(
        authority_root,
        probe_name,
        public_probe_body,
        runtime_gid=runtime_gid,
    )
    _publish_immutable_authority_file(
        authority_root,
        "activation-grant.json",
        grant_body,
        runtime_gid=runtime_gid,
    )
    return _digest(public_probe_body), _digest(grant_body)


def render_activation_receipt(
    candidate_receipt: Mapping[str, Any],
    grant: Mapping[str, Any],
    *,
    worker_commands: Mapping[str, list[str]],
    workers_started_at: str,
) -> dict[str, Any]:
    return {
        "schema": ACTIVATION_RECEIPT_SCHEMA,
        "commit": candidate_receipt["request"]["commit"],
        "request_id": candidate_receipt["request"]["id"],
        "candidate_receipt_sha256": _digest(_canonical(candidate_receipt)),
        "grant_sha256": _digest(_canonical(grant)),
        "fence_sha256": candidate_receipt["fence"]["sha256"],
        "railway": dict(candidate_receipt["railway"]),
        "authority": {
            "mode": "production",
            "source": "railway",
            "hetzner_writers_frozen": True,
            "railway_writers_started": True,
            "public_traffic_enabled": True,
        },
        "workers": {
            name: {"command": command, "process_started": True}
            for name, command in worker_commands.items()
        },
        "public": {
            "base_url": grant["public_base_url"],
            "probe_sha256": grant["public_probe_sha256"],
        },
        "activated_at": grant["activated_at"],
        "workers_started_at": workers_started_at,
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }


def validate_activation_receipt(
    value: object,
    *,
    candidate_receipt: Mapping[str, Any],
    grant: Mapping[str, Any],
    railway: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _ACTIVATION_KEYS:
        raise CutoverContractError("activation receipt fields are invalid")
    expected_authority = {
        "mode": "production",
        "source": "railway",
        "hetzner_writers_frozen": True,
        "railway_writers_started": True,
        "public_traffic_enabled": True,
    }
    if (
        value.get("schema") != ACTIVATION_RECEIPT_SCHEMA
        or value.get("commit") != candidate_receipt["request"]["commit"]
        or value.get("request_id") != candidate_receipt["request"]["id"]
        or value.get("candidate_receipt_sha256")
        != _digest(_canonical(candidate_receipt))
        or value.get("grant_sha256") != _digest(_canonical(grant))
        or value.get("fence_sha256") != candidate_receipt["fence"]["sha256"]
        or value.get("authority") != expected_authority
        or value.get("public")
        != {
            "base_url": grant["public_base_url"],
            "probe_sha256": grant["public_probe_sha256"],
        }
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
    ):
        raise CutoverContractError("activation receipt binding is invalid")
    observed_railway = value.get("railway")
    if observed_railway != candidate_receipt["railway"] or (
        railway is not None and observed_railway != dict(railway)
    ):
        raise CutoverContractError("activation receipt Railway identity is invalid")
    workers = value.get("workers")
    expected_commands = worker_commands()
    if not isinstance(workers, dict) or set(workers) != set(expected_commands):
        raise CutoverContractError("activation receipt worker set is invalid")
    for name, expected in expected_commands.items():
        worker = workers[name]
        command = worker.get("command") if isinstance(worker, dict) else None
        if (
            not isinstance(worker, dict)
            or set(worker) != {"command", "process_started"}
            or worker["process_started"] is not True
            or not isinstance(command, list)
            or len(command) != len(expected)
            or not isinstance(command[0], str)
            or not Path(command[0]).is_absolute()
            or not Path(command[0]).name.startswith("python")
            or command[1:] != expected[1:]
        ):
            raise CutoverContractError("activation receipt worker command is invalid")
    grant_time = _utc(grant["activated_at"], label="grant activated_at")
    receipt_time = _utc(value.get("activated_at"), label="receipt activated_at")
    workers_started = _utc(
        value.get("workers_started_at"),
        label="workers_started_at",
    )
    if receipt_time != grant_time or workers_started < grant_time:
        raise CutoverContractError("activation receipt timing is invalid")
    return dict(value)


def production_environment(
    candidate: Mapping[str, str],
    activation_receipt: Mapping[str, Any],
    *,
    receipt_path: Path,
) -> dict[str, str]:
    environment = dict(candidate)
    environment.update(
        {
            "SEICHE_RAILWAY_STATEFUL_MODE": "production",
            "SEICHE_RAILWAY_ACTIVATION_RECEIPT_PATH": str(receipt_path),
            "SEICHE_RAILWAY_ACTIVATION_RECEIPT_SHA256": _digest(
                _canonical(activation_receipt)
            ),
            "SEICHE_COLLECTOR_HEARTBEAT_REQUIRED": "1",
            "SEICHE_SOURCE_HEARTBEAT_REQUIRED": "1",
        }
    )
    return environment


def validate_activation_runtime(environment: Mapping[str, str]) -> dict[str, Any]:
    if environment.get("SEICHE_RAILWAY_STATEFUL_MODE") != "production":
        raise CutoverContractError("Railway production mode is invalid")
    path = Path(environment.get("SEICHE_RAILWAY_ACTIVATION_RECEIPT_PATH", ""))
    if (
        not path.is_absolute()
        or path.parent != migration.PLATFORM_ROOT / "cutover-receipts"
        or not path.name.endswith(".activation.json")
    ):
        raise CutoverContractError("Railway activation receipt path is invalid")
    try:
        body = migration._stable_read(path, maximum_bytes=256 * 1024)
        value = migration._decode_canonical_json(body, label="activation receipt")
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    candidate, candidate_sha256 = _validate_bound_candidate_runtime_receipt(
        environment
    )
    if (
        _digest(body) != environment.get("SEICHE_RAILWAY_ACTIVATION_RECEIPT_SHA256")
        or value.get("schema") != ACTIVATION_RECEIPT_SCHEMA
        or value.get("commit") != environment.get("SEICHE_RELEASE_SHA")
        or value.get("request_id")
        != environment.get("SEICHE_RAILWAY_CUTOVER_REQUEST_ID")
        or value.get("authority")
        != {
            "mode": "production",
            "source": "railway",
            "hetzner_writers_frozen": True,
            "railway_writers_started": True,
            "public_traffic_enabled": True,
        }
        or value.get("candidate_receipt_sha256") != candidate_sha256
        or value.get("commit") != candidate.get("request", {}).get("commit")
        or value.get("request_id") != candidate.get("request", {}).get("id")
    ):
        raise CutoverContractError("Railway activation receipt binding is invalid")
    return value


def api_command(port: str) -> list[str]:
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise CutoverContractError("Railway PORT is invalid")
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "seiche.api:app",
        "--host",
        "0.0.0.0",
        "--port",
        port,
        "--no-access-log",
    ]


def worker_commands() -> dict[str, list[str]]:
    return {
        "market": [
            sys.executable,
            "-m",
            "seiche.cli",
            "market-worker",
            "--poll-seconds",
            "30",
        ],
        "source": [
            sys.executable,
            "-m",
            "seiche.cli",
            "source-worker",
            "--poll-seconds",
            "300",
        ],
    }


def _spawn(
    command: list[str], environment: Mapping[str, str]
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd="/workspace",
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        user=migration.RUNTIME_UID,
        group=migration.RUNTIME_GID,
        extra_groups=[migration.RUNTIME_GID],
    )


def _stop_children(children: list[subprocess.Popen[bytes]], signum: int) -> None:
    for child in children:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass


def _terminate_children(children: list[subprocess.Popen[bytes]]) -> None:
    _stop_children(children, signal.SIGTERM)
    for child in children:
        if child.poll() is None:
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()


def _serve_children(
    children: list[subprocess.Popen[bytes]],
    *,
    poll_seconds: int,
) -> int:
    stopping = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        _stop_children(children, signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            exited = next(
                (child for child in children if child.poll() is not None), None
            )
            if exited is not None:
                return exited.returncode or 1
            time.sleep(poll_seconds)
    finally:
        _terminate_children(children)
    return 0


def writer_environment(candidate: Mapping[str, str]) -> dict[str, str]:
    environment = dict(candidate)
    environment.update(
        {
            "SEICHE_RAILWAY_STATEFUL_MODE": "production",
            "SEICHE_COLLECTOR_HEARTBEAT_REQUIRED": "1",
            "SEICHE_SOURCE_HEARTBEAT_REQUIRED": "1",
        }
    )
    environment.pop("SEICHE_RAILWAY_ACTIVATION_RECEIPT_PATH", None)
    environment.pop("SEICHE_RAILWAY_ACTIVATION_RECEIPT_SHA256", None)
    return environment


def _start_writer_children(
    production: Mapping[str, str],
    commands: Mapping[str, list[str]],
    *,
    poll_seconds: int,
) -> list[subprocess.Popen[bytes]]:
    children: list[subprocess.Popen[bytes]] = []
    try:
        children.extend(
            (
                _spawn(commands["market"], production),
                _spawn(commands["source"], production),
            )
        )
        time.sleep(min(float(poll_seconds), 1.0))
        if any(child.poll() is not None for child in children):
            raise CutoverContractError("a Railway writer failed during restart")
        return children
    except Exception:
        _terminate_children(children)
        raise


def _control_enabled(environment: Mapping[str, str]) -> bool:
    return environment.get("SEICHE_RAILWAY_CONTROL_ENABLED") == "1"


def _emit_stateful_log_result(
    evidence: Mapping[str, Any],
    *,
    kind: str,
    lifecycle: str,
    request_id: str,
    environment: Mapping[str, str],
    runtime_started_at: str | None,
) -> None:
    if not _control_enabled(environment):
        return
    from seiche import stateful_control

    print(
        stateful_control.render_log_result(
            evidence,
            kind=kind,
            lifecycle=lifecycle,
            request_id=request_id,
            environment=environment,
            runtime_started_at=runtime_started_at or migration._iso_now(),
        ),
        flush=True,
    )


def _promote_activation_control_commands(
    candidate: Mapping[str, str],
    *,
    platform_root: Path,
) -> None:
    if not _control_enabled(candidate):
        return
    from seiche import stateful_control

    pending = stateful_control.pending_commands(
        candidate,
        operations=frozenset({stateful_control.ACTIVATION_OPERATION}),
        platform_root=platform_root,
    )
    for proposal in pending:
        payload = proposal.command.document["payload"]
        public_probe_body = migration.canonical_document(payload["public_probe"])
        grant_body = migration.canonical_document(payload["grant"])
        publish_authority_documents(
            proposal.command.request_id,
            public_probe_body,
            grant_body,
            platform_root=platform_root,
            railway=migration.railway_identity(candidate),
            require_fresh=False,
        )
        stateful_control.seal_command(
            proposal,
            platform_root=platform_root,
        )


def _serve_production(
    production: Mapping[str, str],
    *,
    writers: list[subprocess.Popen[bytes]],
    api: subprocess.Popen[bytes],
    commands: Mapping[str, list[str]],
    poll_seconds: int,
    runtime_started_at: str | None = None,
) -> int:
    from seiche import stateful_control
    from seiche import stateful_recovery as recovery

    stopping = False
    failed_requests: set[str] = set()

    def children() -> list[subprocess.Popen[bytes]]:
        return [*writers, api]

    def stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        _stop_children(children(), signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        recovery.reemit_latest_recovery_results(
            production,
            runtime_started_at=runtime_started_at,
        )
        while not stopping:
            exited = next(
                (child for child in children() if child.poll() is not None), None
            )
            if exited is not None:
                return exited.returncode or 1
            claimed_exports = recovery.promote_control_commands(
                production,
                runtime_started_at=runtime_started_at,
            ) or {}
            request = recovery.next_pending_request(
                production,
                claimed_request_ids=frozenset(claimed_exports),
            )
            if request is None or request["request_id"] in failed_requests:
                time.sleep(poll_seconds)
                continue
            _terminate_children(writers)
            writers.clear()
            if stopping:
                break
            writers_stopped_at = migration._iso_now()
            try:
                exported = recovery.export_snapshot(
                    production,
                    request,
                    runtime_uid=migration.RUNTIME_UID,
                    runtime_gid=migration.RUNTIME_GID,
                )
            except (
                Exception
            ) as exc:  # noqa: BLE001 - retain production, retry on restart
                if stopping:
                    break
                api_returncode = api.poll()
                if api_returncode is not None:
                    return api_returncode or 1
                writers.extend(
                    _start_writer_children(
                        production,
                        commands,
                        poll_seconds=poll_seconds,
                    )
                )
                failed_requests.add(str(request["request_id"]))
                print(
                    "seiche Railway recovery: export failed; writers restored "
                    f"fault_type={type(exc).__name__}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            if stopping or api.poll() is not None:
                if api.poll() is not None:
                    return api.returncode or 1
                break
            writers.extend(
                _start_writer_children(
                    production,
                    commands,
                    poll_seconds=poll_seconds,
                )
            )
            if stopping:
                break
            writers_restarted_at = migration._iso_now()
            expected_receipt_path = (
                migration.PLATFORM_ROOT
                / "recovery-receipts"
                / f"{request['snapshot_id']}-{request['request_id']}.json"
            )
            receipt_existed = (
                expected_receipt_path.exists() or expected_receipt_path.is_symlink()
            )
            try:
                receipt_path, receipt = recovery.finalize_receipt(
                    production,
                    request,
                    exported,
                    writers_stopped_at=writers_stopped_at,
                    writers_restarted_at=writers_restarted_at,
                    worker_commands=commands,
                )
            except (
                Exception
            ) as exc:  # noqa: BLE001 - bundle remains for restart recovery
                failed_requests.add(str(request["request_id"]))
                print(
                    "seiche Railway recovery: receipt sealing deferred "
                    f"fault_type={type(exc).__name__}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            proposal = claimed_exports.get(str(request["request_id"]))
            if proposal is not None:
                stateful_control.seal_command(proposal)
            _emit_stateful_log_result(
                receipt,
                kind="recovery_created",
                lifecycle="reused" if receipt_existed else "created",
                request_id=str(request["request_id"]),
                environment=production,
                runtime_started_at=runtime_started_at,
            )
            print(
                "seiche Railway recovery: export receipted "
                f"request={request['request_id']} "
                f"receipt={hashlib.sha256(_canonical(receipt)).hexdigest()} "
                f"path={receipt_path}",
                flush=True,
            )
    finally:
        _terminate_children(children())
    return 0


def supervise_production(
    production: Mapping[str, str],
    *,
    poll_seconds: int = 2,
    runtime_started_at: str | None = None,
) -> int:
    if not 1 <= poll_seconds <= 60:
        raise CutoverContractError("cutover poll interval is invalid")
    validate_activation_runtime(production)
    Path("/tmp/seiche-home").mkdir(mode=0o700, exist_ok=True)
    os.chown("/tmp/seiche-home", migration.RUNTIME_UID, migration.RUNTIME_GID)
    commands = worker_commands()
    writers: list[subprocess.Popen[bytes]] = []
    api: subprocess.Popen[bytes] | None = None
    try:
        writers.extend(
            _start_writer_children(
                production,
                commands,
                poll_seconds=poll_seconds,
            )
        )
        api = _spawn(api_command(production.get("PORT", "")), production)
    except Exception:
        _terminate_children([*writers, *([api] if api is not None else [])])
        raise
    return _serve_production(
        production,
        writers=writers,
        api=api,
        commands=commands,
        poll_seconds=poll_seconds,
        runtime_started_at=runtime_started_at,
    )


def _validate_candidate_activation_boundary(
    candidate: Mapping[str, str],
    candidate_receipt: Mapping[str, Any],
) -> None:
    """Repeat the exact point-in-time proof immediately before writers start."""

    validated = validate_candidate_runtime(candidate)
    if _digest(_canonical(validated)) != _digest(_canonical(candidate_receipt)):
        raise CutoverContractError("activation candidate receipt changed")
    generation = str(validated["filesystem"]["generation"])
    generation_path = migration.PLATFORM_ROOT / "generations" / generation
    if Path(candidate.get("SEICHE_RUNTIME_DATA_DIR", "")) != generation_path / "api":
        raise CutoverContractError("activation candidate runtime path changed")
    try:
        migration.validate_receipted_generation(
            generation_path,
            validated,
            runtime_uid=migration.RUNTIME_UID,
            runtime_gid=migration.RUNTIME_GID,
        )
        observed_counts = migration.inspect_postgres_counts(
            candidate.get("SEICHE_DATABASE_URL", "")
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    if observed_counts != tuple(validated["database"]["critical_table_counts"]):
        raise CutoverContractError("activation candidate PostgreSQL counts changed")


def activate_and_supervise(
    candidate: Mapping[str, str],
    candidate_receipt: Mapping[str, Any],
    grant: Mapping[str, Any],
    *,
    activation_path: Path,
    poll_seconds: int = 2,
    runtime_started_at: str | None = None,
) -> int:
    if not 1 <= poll_seconds <= 60:
        raise CutoverContractError("cutover poll interval is invalid")
    # The candidate API is stopped by the caller before this handoff. Repeat
    # its complete point-in-time filesystem, Agent Room, and PostgreSQL proof
    # before the first writer is spawned, closing the candidate-serving window.
    _validate_candidate_activation_boundary(candidate, candidate_receipt)
    Path("/tmp/seiche-home").mkdir(mode=0o700, exist_ok=True)
    os.chown("/tmp/seiche-home", migration.RUNTIME_UID, migration.RUNTIME_GID)
    commands = worker_commands()
    writers = writer_environment(candidate)
    children: list[subprocess.Popen[bytes]] = []
    try:
        children.extend(
            (
                _spawn(commands["market"], writers),
                _spawn(commands["source"], writers),
            )
        )
        time.sleep(min(float(poll_seconds), 1.0))
        if any(child.poll() is not None for child in children):
            raise CutoverContractError(
                "a Railway writer exited before authority was receipted"
            )
        activation = render_activation_receipt(
            candidate_receipt,
            grant,
            worker_commands=commands,
            workers_started_at=migration._iso_now(),
        )
        validate_activation_receipt(
            activation,
            candidate_receipt=candidate_receipt,
            grant=grant,
        )
        migration._write_receipt(
            activation_path,
            activation,
            gid=migration.RUNTIME_GID,
        )
        production = production_environment(
            candidate,
            activation,
            receipt_path=activation_path,
        )
        validate_activation_runtime(production)
        _emit_stateful_log_result(
            activation,
            kind="activation",
            lifecycle="created",
            request_id=str(candidate_receipt["request"]["id"]),
            environment=production,
            runtime_started_at=runtime_started_at,
        )
        children.append(_spawn(api_command(production.get("PORT", "")), production))
    except Exception:
        _terminate_children(children)
        raise
    return _serve_production(
        production,
        writers=children[:2],
        api=children[2],
        commands=commands,
        poll_seconds=poll_seconds,
        runtime_started_at=runtime_started_at,
    )


def _load_cutover_document(path: Path, *, label: str) -> dict[str, Any]:
    try:
        return migration._decode_canonical_json(
            migration._stable_read(path, maximum_bytes=256 * 1024),
            label=label,
        )
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc


def _prepare_authority_directory(
    path: Path,
    *,
    runtime_gid: int = migration.RUNTIME_GID,
) -> None:
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise CutoverContractError("cutover authority directory is unsafe")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fchmod(descriptor, 0o750)
        os.fchown(descriptor, os.geteuid(), runtime_gid)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def supervise_cutover(
    candidate: Mapping[str, str],
    candidate_receipt: Mapping[str, Any],
    *,
    grant_path: Path,
    poll_seconds: int = 2,
    runtime_started_at: str | None = None,
) -> int:
    if not 1 <= poll_seconds <= 60:
        raise CutoverContractError("cutover poll interval is invalid")
    Path("/tmp/seiche-home").mkdir(mode=0o700, exist_ok=True)
    os.chown("/tmp/seiche-home", migration.RUNTIME_UID, migration.RUNTIME_GID)
    children = [_spawn(api_command(candidate.get("PORT", "")), candidate)]
    stopping = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        _stop_children(children, signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    fence_expires = _utc(candidate_receipt["fence"]["expires_at"], label="expires_at")
    try:
        while not stopping:
            if children[0].poll() is not None:
                return children[0].returncode or 1
            _promote_activation_control_commands(
                candidate,
                platform_root=grant_path.parent.parent,
            )
            if grant_path.exists() or grant_path.is_symlink():
                grant = validate_grant(
                    _load_cutover_document(grant_path, label="activation grant"),
                    candidate_receipt=candidate_receipt,
                    edge_token_digest=candidate["SEICHE_RAILWAY_EDGE_TOKEN_SHA256"],
                )
                _stop_children(children, signal.SIGTERM)
                children[0].wait(timeout=30)
                children = []
                activation_path = (
                    grant_path.parent.parent
                    / "cutover-receipts"
                    / (f"{candidate_receipt['request']['id']}.activation.json")
                )
                return activate_and_supervise(
                    candidate,
                    candidate_receipt,
                    grant,
                    activation_path=activation_path,
                    poll_seconds=poll_seconds,
                    runtime_started_at=runtime_started_at,
                )
            if datetime.now(UTC).replace(microsecond=0) > fence_expires:
                raise CutoverContractError("authority fence expired before activation")
            time.sleep(poll_seconds)
    finally:
        _terminate_children(children)
    return 0 if stopping else 1


def run(
    *,
    request_path: Path = Path("/migration/request.json"),
    source_archive: Path = Path("/migration/source.tar"),
    source_bundle: Path = Path("/migration/source.bundle"),
    platform_root: Path = migration.PLATFORM_ROOT,
) -> int:
    runtime_started_at = migration._iso_now()
    if os.geteuid() != 0 or os.getegid() != 0:
        raise CutoverContractError("cutover supervisor must start as root")
    expected_fence = os.environ.get("SEICHE_RAILWAY_AUTHORITY_FENCE_SHA256", "")
    fence_path = platform_root / "authority-fences" / (f"{expected_fence}.json")
    fence = load_fence(
        fence_path,
        expected_sha256=expected_fence,
        require_current=False,
    )
    request = load_request(
        request_path,
        source_archive,
        source_bundle,
        fence,
        require_fresh=False,
    )
    if request["source_fence_sha256"] != expected_fence:
        raise CutoverContractError("runtime fence differs from request")
    railway = migration.railway_identity(os.environ)
    grant_path = platform_root / "authority" / "activation-grant.json"
    activation_path = (
        platform_root
        / "cutover-receipts"
        / (f"{request['request_id']}.activation.json")
    )
    has_grant = grant_path.exists() or grant_path.is_symlink()
    has_activation = activation_path.exists() or activation_path.is_symlink()
    if has_activation and not has_grant:
        raise CutoverContractError(
            "activation receipt exists without its authority grant"
        )
    if not has_grant:
        validate_fence(fence)
        validate_request(request, fence=fence)
    inbox = platform_root / "inbox" / str(request["snapshot_id"])
    try:
        bundle = migration.validate_bundle(inbox, request)
    except migration.MigrationContractError as exc:
        raise CutoverContractError(str(exc)) from exc
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    if not base_dsn:
        raise CutoverContractError("Railway PostgreSQL URL is absent")
    candidate_receipt_path = (
        platform_root
        / "cutover-receipts"
        / f"{request['request_id']}.candidate.json"
    )
    candidate_path_existed = (
        candidate_receipt_path.exists() or candidate_receipt_path.is_symlink()
    )
    restore = restore_candidate(
        request,
        fence,
        bundle,
        platform_root=platform_root,
        base_dsn=base_dsn,
        railway=railway,
        runtime_uid=migration.RUNTIME_UID,
        runtime_gid=migration.RUNTIME_GID,
        active_resume=has_activation,
    )
    edge_token = os.environ.get("SEICHE_RAILWAY_EDGE_TOKEN", "")
    environment = candidate_environment(
        os.environ,
        restore,
        edge_token=edge_token,
        runtime_uid=migration.RUNTIME_UID,
        runtime_gid=migration.RUNTIME_GID,
    )
    validate_candidate_runtime(environment)
    if _control_enabled(environment):
        from seiche import stateful_control

        stateful_control.prepare_control_dropbox(platform_root=platform_root)
        _promote_activation_control_commands(
            environment,
            platform_root=platform_root,
        )
        has_grant = grant_path.exists() or grant_path.is_symlink()
    _emit_stateful_log_result(
        restore.receipt,
        kind="candidate",
        lifecycle="reused" if candidate_path_existed else "created",
        request_id=str(request["request_id"]),
        environment=environment,
        runtime_started_at=runtime_started_at,
    )
    _prepare_authority_directory(grant_path.parent)
    if has_grant:
        grant = validate_grant(
            _load_cutover_document(grant_path, label="activation grant"),
            candidate_receipt=restore.receipt,
            edge_token_digest=environment["SEICHE_RAILWAY_EDGE_TOKEN_SHA256"],
            require_fresh=False,
        )
        if has_activation:
            activation = validate_activation_receipt(
                _load_cutover_document(
                    activation_path,
                    label="activation receipt",
                ),
                candidate_receipt=restore.receipt,
                grant=grant,
                railway=railway,
            )
            production = production_environment(
                environment,
                activation,
                receipt_path=activation_path,
            )
            validate_activation_runtime(production)
            print(
                "seiche Railway cutover: receipted production authority resumed",
                flush=True,
            )
            _emit_stateful_log_result(
                activation,
                kind="activation",
                lifecycle="reused",
                request_id=str(request["request_id"]),
                environment=production,
                runtime_started_at=runtime_started_at,
            )
            return supervise_production(
                production,
                runtime_started_at=runtime_started_at,
            )
        print(
            "seiche Railway cutover: resuming interrupted granted activation",
            flush=True,
        )
        return activate_and_supervise(
            environment,
            restore.receipt,
            grant,
            activation_path=activation_path,
            runtime_started_at=runtime_started_at,
        )
    print(
        "seiche Railway cutover: candidate ready; both writer planes fenced", flush=True
    )
    return supervise_cutover(
        environment,
        restore.receipt,
        grant_path=grant_path,
        runtime_started_at=runtime_started_at,
    )


def _validate_cli(arguments: argparse.Namespace) -> int:
    def document(path: str, label: str) -> tuple[bytes, dict[str, Any]]:
        try:
            body = migration._stable_read(Path(path), maximum_bytes=256 * 1024)
            value = migration._decode_canonical_json(body, label=label)
        except migration.MigrationContractError as exc:
            raise CutoverContractError(str(exc)) from exc
        return body, value

    body, value = document(arguments.document, arguments.kind)
    if arguments.kind == "fence":
        validate_fence(value)
    elif arguments.kind in {"candidate", "activation"}:
        if not arguments.request or not arguments.fence:
            raise CutoverContractError("candidate validation context is incomplete")
        _fence_body, fence_value = document(arguments.fence, "authority fence")
        fence = validate_fence(
            fence_value,
            require_current=not arguments.historical,
        )
        _request_body, request_value = document(arguments.request, "cutover request")
        request = validate_request(
            request_value,
            fence=fence,
            require_fresh=not arguments.historical,
        )
        if arguments.kind == "candidate":
            validate_candidate_receipt(value, request=request, fence=fence)
        else:
            if not arguments.candidate or not arguments.grant:
                raise CutoverContractError(
                    "activation validation context is incomplete"
                )
            _candidate_body, candidate_value = document(
                arguments.candidate,
                "candidate receipt",
            )
            candidate = validate_candidate_receipt(
                candidate_value,
                request=request,
                fence=fence,
            )
            _grant_body, grant_value = document(arguments.grant, "activation grant")
            validate_grant(
                grant_value,
                candidate_receipt=candidate,
                edge_token_digest=arguments.edge_token_sha256 or "",
                require_fresh=not arguments.historical,
            )
            validate_activation_receipt(
                value,
                candidate_receipt=candidate,
                grant=grant_value,
            )
    print(_digest(body))
    return 0


def _decode_authority_cli_body(value: str, *, label: str) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value) <= 512 * 1024:
        raise CutoverContractError(f"{label} encoding is invalid")
    try:
        body = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CutoverContractError(f"{label} encoding is invalid") from exc
    if base64.b64encode(body).decode("ascii") != value:
        raise CutoverContractError(f"{label} encoding is not canonical")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    validate = subparsers.add_parser("validate")
    validate.add_argument("kind", choices=("fence", "candidate", "activation"))
    validate.add_argument("document")
    validate.add_argument("--request")
    validate.add_argument("--fence")
    validate.add_argument("--candidate")
    validate.add_argument("--grant")
    validate.add_argument("--edge-token-sha256")
    validate.add_argument("--historical", action="store_true")
    publish = subparsers.add_parser("publish-authority")
    publish.add_argument("request_id")
    publish.add_argument("public_probe_base64")
    publish.add_argument("grant_base64")
    arguments = parser.parse_args(argv)
    if arguments.command == "validate":
        return _validate_cli(arguments)
    if arguments.command == "publish-authority":
        probe_digest, grant_digest = publish_authority_documents(
            arguments.request_id,
            _decode_authority_cli_body(
                arguments.public_probe_base64,
                label="public candidate probe",
            ),
            _decode_authority_cli_body(
                arguments.grant_base64,
                label="activation grant",
            ),
        )
        print(f"{probe_digest} {grant_digest}")
        return 0
    return run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CutoverContractError as error:
        print(f"seiche Railway cutover: {error}", file=sys.stderr)
        time.sleep(1)
        raise SystemExit(1) from None
