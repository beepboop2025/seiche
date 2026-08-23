"""Fail-closed Telegram state transfer into a dedicated Railway service.

The module deliberately separates byte restoration from Telegram authority.
An exact Hetzner fence and archive can create a non-authoritative candidate;
only a separately published grant can let the runtime start one getUpdates
consumer.  No function in this module changes Railway or Telegram control
planes.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator, Mapping, NamedTuple

from seiche import stateful_migration as platform


REPOSITORY = "beepboop2025/seiche"
WORKFLOW = "beepboop2025/seiche/.github/workflows/railway-telegram.yml"
SOURCE_REF = "refs/heads/main"
ROOT = Path("/var/lib/seiche-telegram")
IMAGE_REQUEST_PATH = Path("/migration/request.json")
RUNTIME_UID = platform.RUNTIME_UID
RUNTIME_GID = platform.RUNTIME_GID

IMAGE_SCHEMA = "seiche.railway-telegram-image-request.v1"
TRANSFER_SCHEMA = "seiche.railway-telegram-transfer-request.v1"
CANDIDATE_SCHEMA = "seiche.railway-telegram-candidate-receipt.v1"
GRANT_SCHEMA = "seiche.railway-telegram-authority-grant.v1"
WORKER_PROOF_SCHEMA = "seiche.railway-telegram-worker-proof.v1"
ACTIVATION_SCHEMA = "seiche.railway-telegram-activation-receipt.v1"

PREPARE_CONFIRMATION = "PREPARE_NON_AUTHORITATIVE_TELEGRAM_SERVICE"
TRANSFER_CONFIRMATION = "RESTORE_FROZEN_TELEGRAM_STATE"
GRANT_CONFIRMATION = "RAILWAY_BECOMES_SOLE_TELEGRAM_CONSUMER"

REQUEST_MAX_AGE = timedelta(minutes=30)
REQUEST_FUTURE_SKEW = timedelta(minutes=5)
FENCE_MAX_LIFETIME = timedelta(hours=4, minutes=5)
MIN_POLLER_SETTLE = 60
MAX_STATE_FILES = 256
MAX_STATE_FILE_BYTES = 32 * 1024**2
MAX_STATE_BYTES = 512 * 1024**2

BOT_UNITS = (
    "seiche-bot.service",
    "seiche-bot-alert.service",
    "seiche-bot-alert.timer",
    "seiche-bot-letter.service",
    "seiche-bot-letter.timer",
    "seiche-bot-tandem.service",
    "seiche-bot-tandem.timer",
)

_SHA40_RE = re.compile(r"[0-9a-f]{40}")
_SHA64_RE = re.compile(r"[0-9a-f]{64}")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_SNAPSHOT_RE = re.compile(r"20[0-9]{6}T[0-9]{6}Z")
_REGION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}")
_LAB_CHANNEL_RE = re.compile(r"-100[0-9]{6,16}")
_STATE_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\.(?:json|jsonl)")
_BOT_TEMP_RE = re.compile(r"(?P<name>[a-z][a-z0-9_-]{0,63}\.json)\.tmp")
_WORKER_TEMP_RE = re.compile(
    r"\.(?P<name>[a-z][a-z0-9_-]{0,63}\.(?:json|jsonl))\.[0-9]+\.tmp"
)

_IMAGE_KEYS = frozenset(
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
        "requested_at",
        "confirmation",
    }
)
_RAILWAY_KEYS = frozenset(
    {
        "deployment_id",
        "project_id",
        "environment_id",
        "service_id",
        "volume_id",
        "volume_name",
        "volume_mount_path",
        "region",
    }
)
_STATE_KEYS = frozenset(
    {
        "offset",
        "subscriber_count",
        "subscribers_sha256",
        "file_sha256",
        "file_size",
        "tree_sha256",
        "total_bytes",
    }
)
_FENCE_KEYS = frozenset(
    {
        "source",
        "state",
        "state_root",
        "units",
        "poller_stopped",
        "timers_stopped",
        "timers_disabled",
        "active_processes",
        "lab_channel_id",
        "frozen_at",
        "settled_at",
        "expires_at",
        "poller_settle_seconds",
    }
)
_TRANSFER_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "commit",
        "image_request_id",
        "request_id",
        "snapshot_id",
        "requested_at",
        "archive_sha256",
        "bot_token_sha256",
        "state",
        "railway",
        "fence",
        "confirmation",
    }
)
_CANDIDATE_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "commit",
        "request_id",
        "request_sha256",
        "image_request_id",
        "archive_sha256",
        "railway",
        "authority",
        "state",
        "restored_at",
        "research_only",
        "can_publish",
        "can_execute",
    }
)
_GRANT_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "commit",
        "request_id",
        "candidate_receipt_sha256",
        "bot_token_sha256",
        "activated_at",
        "confirmation",
    }
)
_WORKER_PROOF_KEYS = frozenset(
    {
        "schema",
        "repository",
        "commit",
        "request_id",
        "candidate_receipt_sha256",
        "grant_sha256",
        "railway",
        "bot",
        "initial_offset",
        "observed_offset",
        "first_poll_at",
        "scheduler_baseline",
        "get_updates_ok",
        "conflict_observed",
    }
)
_ACTIVATION_KEYS = frozenset(
    {
        "schema",
        "repository",
        "workflow",
        "commit",
        "request_id",
        "candidate_receipt_sha256",
        "grant_sha256",
        "worker_proof_sha256",
        "railway",
        "authority",
        "bot",
        "state",
        "scheduler_baseline",
        "activated_at",
        "research_only",
        "can_publish",
        "can_execute",
    }
)


class TelegramMigrationError(RuntimeError):
    """One Telegram migration or authority invariant failed."""


class Candidate(NamedTuple):
    receipt_path: Path
    receipt: Mapping[str, Any]
    state_root: Path


def canonical(value: object) -> bytes:
    return platform.canonical_document(value)


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _validate_digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA64_RE.fullmatch(value) is None:
        raise TelegramMigrationError(f"{label} is invalid")
    return value


def _utc(value: object, *, label: str) -> datetime:
    try:
        return platform._utc_timestamp(value, label=label)
    except platform.MigrationContractError as exc:
        raise TelegramMigrationError(str(exc)) from exc


def iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_document(
    path: Path, *, label: str, maximum_bytes: int = 256 * 1024
) -> tuple[bytes, dict[str, Any]]:
    try:
        body = platform._stable_read(path, maximum_bytes=maximum_bytes)
        return body, platform._decode_canonical_json(body, label=label)
    except platform.MigrationContractError as exc:
        raise TelegramMigrationError(str(exc)) from exc


def validate_image_request(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _IMAGE_KEYS:
        raise TelegramMigrationError("Telegram image request fields are invalid")
    if (
        value.get("schema") != IMAGE_SCHEMA
        or value.get("repository") != REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("source_ref") != SOURCE_REF
        or value.get("confirmation") != PREPARE_CONFIRMATION
        or not isinstance(value.get("commit"), str)
        or _SHA40_RE.fullmatch(value["commit"]) is None
        or not isinstance(value.get("tree"), str)
        or _SHA40_RE.fullmatch(value["tree"]) is None
    ):
        raise TelegramMigrationError("Telegram image request identity is invalid")
    for field in (
        "source_archive_sha256",
        "source_bundle_sha256",
        "request_id",
    ):
        _validate_digest(value.get(field), label=field)
    _utc(value.get("requested_at"), label="image requested_at")
    return dict(value)


def image_context(
    environment: Mapping[str, str],
    *,
    request_path: Path | None = None,
) -> tuple[bytes, dict[str, Any]]:
    request_path = request_path or IMAGE_REQUEST_PATH
    body, request = load_document(
        request_path,
        label="Telegram image request",
        maximum_bytes=32 * 1024,
    )
    validate_image_request(request)
    if request["commit"] != environment.get("SEICHE_RELEASE_SHA"):
        raise TelegramMigrationError("Telegram image release identity differs")
    return body, request


def railway_identity(environment: Mapping[str, str]) -> dict[str, str]:
    value = {
        "deployment_id": environment.get("RAILWAY_DEPLOYMENT_ID", ""),
        "project_id": environment.get("RAILWAY_PROJECT_ID", ""),
        "environment_id": environment.get("RAILWAY_ENVIRONMENT_ID", ""),
        "service_id": environment.get("RAILWAY_SERVICE_ID", ""),
        "volume_id": environment.get("SEICHE_RAILWAY_TELEGRAM_VOLUME_ID", ""),
        "volume_name": environment.get("RAILWAY_VOLUME_NAME", ""),
        "volume_mount_path": environment.get("RAILWAY_VOLUME_MOUNT_PATH", ""),
        "region": environment.get("RAILWAY_REPLICA_REGION", ""),
    }
    if set(value) != _RAILWAY_KEYS:
        raise TelegramMigrationError("Telegram Railway identity fields are invalid")
    for name in (
        "deployment_id",
        "project_id",
        "environment_id",
        "service_id",
        "volume_id",
    ):
        if _UUID_RE.fullmatch(value[name]) is None:
            raise TelegramMigrationError(f"Telegram Railway {name} is invalid")
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value["volume_name"]) is None
        or value["volume_mount_path"] != str(ROOT)
        or _REGION_RE.fullmatch(value["region"]) is None
    ):
        raise TelegramMigrationError("Telegram Railway volume identity is invalid")
    return value


def lab_channel_identity(environment: Mapping[str, str]) -> str:
    value = environment.get("LAB_CHANNEL_ID", "")
    if _LAB_CHANNEL_RE.fullmatch(value) is None:
        raise TelegramMigrationError("Telegram Lab channel identity is invalid")
    return value


def _json(body: bytes, *, label: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite constant {value}")

    try:
        return json.loads(body, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TelegramMigrationError(f"{label} is not valid JSON") from exc


def inspect_state(root: Path) -> dict[str, Any]:
    try:
        metadata = root.lstat()
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise TelegramMigrationError("Telegram state root is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink():
        raise TelegramMigrationError("Telegram state root is unsafe")
    if not 2 <= len(entries) <= MAX_STATE_FILES:
        raise TelegramMigrationError("Telegram state file count is invalid")
    names = {entry.name for entry in entries}
    if not {"offset.json", "subscribers.json"}.issubset(names):
        raise TelegramMigrationError("Telegram critical state files are absent")

    documents: dict[str, Any] = {}
    file_sha256: dict[str, str] = {}
    file_size: dict[str, int] = {}
    total_bytes = 0
    tree = hashlib.sha256()
    for path in entries:
        if _STATE_NAME_RE.fullmatch(path.name) is None:
            raise TelegramMigrationError("Telegram state filename is not closed")
        try:
            item = path.lstat()
        except OSError as exc:
            raise TelegramMigrationError("Telegram state file is unavailable") from exc
        if (
            not stat.S_ISREG(item.st_mode)
            or item.st_nlink != 1
            or item.st_size > MAX_STATE_FILE_BYTES
        ):
            raise TelegramMigrationError("Telegram state file is unsafe")
        try:
            body = platform._stable_read(path, maximum_bytes=MAX_STATE_FILE_BYTES)
        except platform.MigrationContractError as exc:
            raise TelegramMigrationError(str(exc)) from exc
        if path.suffix == ".json":
            documents[path.name] = _json(body, label=path.name)
        else:
            for number, line in enumerate(body.splitlines(), start=1):
                if line:
                    _json(line, label=f"{path.name}:{number}")
        observed = digest(body)
        file_sha256[path.name] = observed
        file_size[path.name] = len(body)
        total_bytes += len(body)
        tree.update(path.name.encode("ascii") + b"\0")
        tree.update(observed.encode("ascii") + b"\0")
        tree.update(str(len(body)).encode("ascii") + b"\n")
    if total_bytes > MAX_STATE_BYTES:
        raise TelegramMigrationError("Telegram state exceeds its migration bound")
    offset = documents["offset.json"]
    subscribers = documents["subscribers.json"]
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise TelegramMigrationError("Telegram update offset is invalid")
    if not isinstance(subscribers, dict) or any(
        re.fullmatch(r"-?[0-9]+", str(chat_id)) is None for chat_id in subscribers
    ):
        raise TelegramMigrationError("Telegram subscriber state is invalid")
    return {
        "offset": offset,
        "subscriber_count": len(subscribers),
        "subscribers_sha256": digest(canonical(subscribers)),
        "file_sha256": file_sha256,
        "file_size": file_size,
        "tree_sha256": tree.hexdigest(),
        "total_bytes": total_bytes,
    }


def validate_state_identity(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _STATE_KEYS:
        raise TelegramMigrationError("Telegram state identity fields are invalid")
    if (
        not isinstance(value.get("offset"), int)
        or isinstance(value.get("offset"), bool)
        or value["offset"] < 0
        or not isinstance(value.get("subscriber_count"), int)
        or value["subscriber_count"] < 0
        or not isinstance(value.get("total_bytes"), int)
        or not 0 < value["total_bytes"] <= MAX_STATE_BYTES
    ):
        raise TelegramMigrationError("Telegram state identity is invalid")
    _validate_digest(value.get("subscribers_sha256"), label="subscriber digest")
    _validate_digest(value.get("tree_sha256"), label="state tree digest")
    digests = value.get("file_sha256")
    sizes = value.get("file_size")
    if (
        not isinstance(digests, dict)
        or not isinstance(sizes, dict)
        or set(digests) != set(sizes)
        or not {"offset.json", "subscribers.json"}.issubset(digests)
        or not 2 <= len(digests) <= MAX_STATE_FILES
    ):
        raise TelegramMigrationError("Telegram state member identity is invalid")
    for name, item_digest in digests.items():
        if (
            not isinstance(name, str)
            or _STATE_NAME_RE.fullmatch(name) is None
            or _SHA64_RE.fullmatch(str(item_digest)) is None
            or not isinstance(sizes[name], int)
            or not 0 <= sizes[name] <= MAX_STATE_FILE_BYTES
        ):
            raise TelegramMigrationError("Telegram state member proof is invalid")
    if sum(sizes.values()) != value["total_bytes"]:
        raise TelegramMigrationError("Telegram state byte total differs")
    return dict(value)


def validate_live_state(root: Path, *, baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an evolved post-grant tree without requiring snapshot equality."""
    validate_state_identity(baseline)
    observed = inspect_state(root)
    if observed["offset"] < baseline["offset"]:
        raise TelegramMigrationError("Telegram live offset moved backwards")
    return observed


def recover_live_state_temps(root: Path) -> None:
    """Discard only interrupted atomic writes when a committed final exists."""
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise TelegramMigrationError("Telegram live state is unavailable") from exc
    recovered = False
    for path in entries:
        match = _BOT_TEMP_RE.fullmatch(path.name) or _WORKER_TEMP_RE.fullmatch(
            path.name
        )
        if match is None:
            continue
        final = root / match.group("name")
        try:
            temporary_metadata = path.lstat()
            final_metadata = final.lstat()
        except OSError as exc:
            raise TelegramMigrationError(
                "Telegram interrupted state write has no committed final"
            ) from exc
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_nlink != 1
            or temporary_metadata.st_size > MAX_STATE_FILE_BYTES
            or not stat.S_ISREG(final_metadata.st_mode)
            or final_metadata.st_nlink != 1
            or final.is_symlink()
        ):
            raise TelegramMigrationError("Telegram interrupted state write is unsafe")
        path.unlink()
        recovered = True
    if recovered:
        platform._fsync_directory(root)


def validate_archive(path: Path) -> tuple[tarfile.TarInfo, ...]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TelegramMigrationError("Telegram archive is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_STATE_BYTES
    ):
        raise TelegramMigrationError("Telegram archive is unsafe")
    try:
        archive = tarfile.open(path, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise TelegramMigrationError("Telegram archive is unavailable") from exc
    with archive:
        members = archive.getmembers()
    if not 3 <= len(members) <= MAX_STATE_FILES + 1:
        raise TelegramMigrationError("Telegram archive member count is invalid")
    names: set[str] = set()
    expanded = 0
    root_seen = False
    for member in members:
        canonical_name = member.name.rstrip("/")
        parts = PurePosixPath(canonical_name).parts
        if (
            not parts
            or canonical_name.startswith("/")
            or "\x00" in canonical_name
            or any(part in {"", ".", ".."} for part in parts)
            or PurePosixPath(canonical_name).as_posix() != canonical_name
            or canonical_name in names
        ):
            raise TelegramMigrationError("Telegram archive member path is unsafe")
        names.add(canonical_name)
        if parts == ("seiche-bot",):
            if not member.isdir():
                raise TelegramMigrationError("Telegram archive root is not a directory")
            root_seen = True
            continue
        if (
            len(parts) != 2
            or parts[0] != "seiche-bot"
            or _STATE_NAME_RE.fullmatch(parts[1]) is None
            or not member.isfile()
            or member.issym()
            or member.islnk()
            or member.size > MAX_STATE_FILE_BYTES
            or member.name != canonical_name
        ):
            raise TelegramMigrationError("Telegram archive member is not closed")
        expanded += member.size
    if not root_seen or expanded > MAX_STATE_BYTES:
        raise TelegramMigrationError("Telegram archive expansion is invalid")
    return tuple(members)


def extract_archive(path: Path, destination: Path) -> Path:
    validate_archive(path)
    if destination.exists() or destination.is_symlink():
        raise TelegramMigrationError("Telegram extraction destination exists")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            archive.extractall(path=destination, filter="data")
        state = destination / "seiche-bot"
        inspect_state(state)
        return state
    except Exception:
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        raise


def create_archive(state_root: Path, destination: Path) -> dict[str, Any]:
    identity = inspect_state(state_root)
    if destination.exists() or destination.is_symlink():
        raise TelegramMigrationError("Telegram archive destination exists")
    try:
        with tarfile.open(destination, mode="w:gz", compresslevel=9) as archive:
            archive.add(state_root, arcname="seiche-bot", recursive=True)
    except (OSError, tarfile.TarError) as exc:
        raise TelegramMigrationError("Telegram archive creation failed") from exc
    validate_archive(destination)
    return identity


def _validate_railway(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _RAILWAY_KEYS:
        raise TelegramMigrationError("Telegram target Railway fields are invalid")
    environment = {
        "RAILWAY_DEPLOYMENT_ID": str(value.get("deployment_id", "")),
        "RAILWAY_PROJECT_ID": str(value.get("project_id", "")),
        "RAILWAY_ENVIRONMENT_ID": str(value.get("environment_id", "")),
        "RAILWAY_SERVICE_ID": str(value.get("service_id", "")),
        "SEICHE_RAILWAY_TELEGRAM_VOLUME_ID": str(value.get("volume_id", "")),
        "RAILWAY_VOLUME_NAME": str(value.get("volume_name", "")),
        "RAILWAY_VOLUME_MOUNT_PATH": str(value.get("volume_mount_path", "")),
        "RAILWAY_REPLICA_REGION": str(value.get("region", "")),
    }
    return railway_identity(environment)


def validate_transfer(
    value: object,
    *,
    image_request: Mapping[str, Any],
    railway: Mapping[str, str],
    now: datetime | None = None,
    require_fresh: bool = True,
    expected_lab_channel_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _TRANSFER_KEYS:
        raise TelegramMigrationError("Telegram transfer request fields are invalid")
    if (
        value.get("schema") != TRANSFER_SCHEMA
        or value.get("repository") != REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("commit") != image_request.get("commit")
        or value.get("image_request_id") != image_request.get("request_id")
        or value.get("confirmation") != TRANSFER_CONFIRMATION
        or value.get("railway") != dict(railway)
    ):
        raise TelegramMigrationError("Telegram transfer binding is invalid")
    _validate_digest(value.get("request_id"), label="transfer request id")
    _validate_digest(value.get("archive_sha256"), label="Telegram archive")
    _validate_digest(value.get("bot_token_sha256"), label="bot token")
    snapshot_id = value.get("snapshot_id")
    if not isinstance(snapshot_id, str) or _SNAPSHOT_RE.fullmatch(snapshot_id) is None:
        raise TelegramMigrationError("Telegram snapshot identity is invalid")
    validate_state_identity(value.get("state"))
    _validate_railway(value.get("railway"))
    fence = value.get("fence")
    if not isinstance(fence, dict) or set(fence) != _FENCE_KEYS:
        raise TelegramMigrationError("Telegram authority fence fields are invalid")
    if (
        fence.get("source") != "hetzner"
        or fence.get("state") != "frozen"
        or fence.get("state_root") != "/var/lib/seiche-bot"
        or fence.get("units") != list(BOT_UNITS)
        or fence.get("poller_stopped") is not True
        or fence.get("timers_stopped") is not True
        or fence.get("timers_disabled") is not True
        or fence.get("active_processes") != []
        or not isinstance(fence.get("lab_channel_id"), str)
        or _LAB_CHANNEL_RE.fullmatch(fence["lab_channel_id"]) is None
        or not isinstance(fence.get("poller_settle_seconds"), int)
        or fence["poller_settle_seconds"] < MIN_POLLER_SETTLE
    ):
        raise TelegramMigrationError("Telegram authority is not fully fenced")
    if (
        expected_lab_channel_id is not None
        and fence["lab_channel_id"] != expected_lab_channel_id
    ):
        raise TelegramMigrationError("Telegram Lab channel identity differs")
    requested_at = _utc(value.get("requested_at"), label="transfer requested_at")
    frozen_at = _utc(fence.get("frozen_at"), label="Telegram frozen_at")
    settled_at = _utc(fence.get("settled_at"), label="Telegram settled_at")
    expires_at = _utc(fence.get("expires_at"), label="Telegram fence expires_at")
    snapshot_at = datetime.strptime(snapshot_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise TelegramMigrationError("Telegram transfer clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    if (
        not frozen_at <= snapshot_at <= settled_at <= requested_at <= expires_at
        or settled_at - frozen_at < timedelta(seconds=MIN_POLLER_SETTLE)
        or expires_at - frozen_at > FENCE_MAX_LIFETIME
        or requested_at > observed + REQUEST_FUTURE_SKEW
        or (require_fresh and requested_at < observed - REQUEST_MAX_AGE)
        or (require_fresh and observed > expires_at)
    ):
        raise TelegramMigrationError("Telegram transfer timing is invalid")
    return dict(value)


def candidate_path(root: Path, request: Mapping[str, Any]) -> Path:
    return (
        root / "candidates" / (f"{request['snapshot_id']}-{request['request_id']}.json")
    )


def generation_path(root: Path, request: Mapping[str, Any]) -> Path:
    return root / "generations" / str(request["snapshot_id"])


def render_candidate(
    request: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    railway: Mapping[str, str],
    restored_at: str,
) -> dict[str, Any]:
    return {
        "schema": CANDIDATE_SCHEMA,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "commit": request["commit"],
        "request_id": request["request_id"],
        "request_sha256": digest(canonical(request)),
        "image_request_id": request["image_request_id"],
        "archive_sha256": request["archive_sha256"],
        "railway": dict(railway),
        "authority": {
            "mode": "candidate",
            "source": "hetzner",
            "hetzner_frozen": True,
            "telegram_calls_enabled": False,
        },
        "state": {
            **dict(state),
            "relative_path": f"generations/{request['snapshot_id']}/seiche-bot",
        },
        "restored_at": restored_at,
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }


def validate_candidate(
    value: object,
    *,
    request: Mapping[str, Any],
    railway: Mapping[str, str],
    state_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _CANDIDATE_KEYS:
        raise TelegramMigrationError("Telegram candidate receipt fields are invalid")
    if (
        value.get("schema") != CANDIDATE_SCHEMA
        or value.get("repository") != REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("commit") != request["commit"]
        or value.get("request_id") != request["request_id"]
        or value.get("request_sha256") != digest(canonical(request))
        or value.get("image_request_id") != request["image_request_id"]
        or value.get("archive_sha256") != request["archive_sha256"]
        or value.get("railway") != dict(railway)
        or value.get("authority")
        != {
            "mode": "candidate",
            "source": "hetzner",
            "hetzner_frozen": True,
            "telegram_calls_enabled": False,
        }
        or value.get("research_only") is not True
        or value.get("can_publish") is not False
        or value.get("can_execute") is not False
    ):
        raise TelegramMigrationError("Telegram candidate receipt binding is invalid")
    state = value.get("state")
    if not isinstance(state, dict) or set(state) != {*_STATE_KEYS, "relative_path"}:
        raise TelegramMigrationError("Telegram candidate state fields are invalid")
    if state.get("relative_path") != (
        f"generations/{request['snapshot_id']}/seiche-bot"
    ):
        raise TelegramMigrationError("Telegram candidate state path is invalid")
    identity = {key: state[key] for key in _STATE_KEYS}
    validate_state_identity(identity)
    if identity != request["state"]:
        raise TelegramMigrationError("Telegram candidate state differs from request")
    if state_root is not None and inspect_state(state_root) != identity:
        raise TelegramMigrationError("Telegram candidate state bytes differ")
    restored_at = _utc(value.get("restored_at"), label="Telegram restored_at")
    if restored_at < _utc(
        request["requested_at"], label="Telegram transfer requested_at"
    ):
        raise TelegramMigrationError("Telegram candidate predates its request")
    return dict(value)


def _prepare_directory(path: Path, *, mode: int = 0o750) -> None:
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise TelegramMigrationError("Telegram runtime directory is unsafe")
    os.chown(path, os.geteuid(), RUNTIME_GID)
    os.chmod(path, mode)


def prepare_root(root: Path = ROOT) -> None:
    _prepare_directory(root)
    for name in (
        "incoming",
        "transfers",
        "generations",
        "candidates",
        "grants",
        "worker-proofs",
        "activations",
        "runtime",
        "staging",
        "recovery-requests",
        "recovery-snapshots",
        "recovery-receipts",
    ):
        mode = {"runtime": 0o770, "staging": 0o700}.get(name, 0o750)
        _prepare_directory(
            root / name,
            mode=mode,
        )


def _closed_authority_documents(path: Path) -> list[Path]:
    try:
        metadata = path.lstat()
        entries = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise TelegramMigrationError(
            "Telegram authority directory is unavailable"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise TelegramMigrationError("Telegram authority directory is unsafe")
    for entry in entries:
        if re.fullmatch(r"[0-9a-f]{64}\.json", entry.name) is None:
            raise TelegramMigrationError("Telegram authority directory is not closed")
    return entries


@contextmanager
def _authority_transaction(root: Path) -> Iterator[None]:
    path = root / "authority.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise TelegramMigrationError("Telegram authority lock is unsafe")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    body = canonical(value)
    if path.exists() or path.is_symlink():
        existing, _value = load_document(path, label=path.name)
        if existing != body:
            raise TelegramMigrationError("immutable Telegram document differs")
        return
    staging = path.parent.parent / "staging"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".immutable.", dir=staging)
    temporary = Path(temporary_name)
    try:
        try:
            written = 0
            while written < len(body):
                count = os.write(descriptor, body[written:])
                if count <= 0:
                    raise OSError("immutable Telegram write made no progress")
                written += count
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o440)
            os.fchown(descriptor, os.geteuid(), RUNTIME_GID)
        finally:
            os.close(descriptor)
        if path.exists() or path.is_symlink():
            existing, _value = load_document(path, label=path.name)
            if existing != body:
                raise TelegramMigrationError("immutable Telegram document differs")
            return
        os.replace(temporary, path)
        platform._fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def restore_candidate(
    request: Mapping[str, Any],
    archive: Path,
    environment: Mapping[str, str],
    *,
    root: Path = ROOT,
) -> Candidate:
    _image_body, image = image_context(environment)
    railway = railway_identity(environment)
    transfer = validate_transfer(
        request,
        image_request=image,
        railway=railway,
        require_fresh=False,
        expected_lab_channel_id=lab_channel_identity(environment),
    )
    if (
        digest(platform._stable_read(archive, maximum_bytes=MAX_STATE_BYTES))
        != transfer["archive_sha256"]
    ):
        raise TelegramMigrationError("Telegram archive digest differs from request")
    prepare_root(root)
    final = generation_path(root, transfer)
    receipt_path = candidate_path(root, transfer)
    if final.exists() or final.is_symlink():
        if not final.is_dir() or final.is_symlink():
            raise TelegramMigrationError("Telegram candidate generation is unsafe")
        os.chown(final, os.geteuid(), RUNTIME_GID)
        os.chmod(final, 0o710)
        state_root = final / "seiche-bot"
        state = inspect_state(state_root)
    else:
        stage = Path(
            tempfile.mkdtemp(prefix=".telegram-stage.", dir=root / "generations")
        )
        try:
            extracted = extract_archive(archive, stage / "extract")
            state = inspect_state(extracted)
            if state != transfer["state"]:
                raise TelegramMigrationError(
                    "Telegram archive state differs from request"
                )
            for path in reversed(platform._walk_real_tree(extracted)):
                os.chown(path, RUNTIME_UID, RUNTIME_GID, follow_symlinks=False)
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
            (stage / "extract").rename(final)
            os.chown(final, os.geteuid(), RUNTIME_GID)
            os.chmod(final, 0o710)
            platform._fsync_directory(root / "generations")
            state_root = final / "seiche-bot"
        finally:
            if stage.is_dir() and not stage.is_symlink():
                shutil.rmtree(stage)
    if state != transfer["state"]:
        raise TelegramMigrationError("Telegram restored state is unstable")
    if receipt_path.exists() or receipt_path.is_symlink():
        _existing_body, existing = load_document(
            receipt_path,
            label="Telegram candidate receipt",
        )
        validate_candidate(
            existing,
            request=transfer,
            railway=railway,
            state_root=state_root,
        )
        return Candidate(receipt_path, existing, state_root)
    receipt = render_candidate(
        transfer,
        state=state,
        railway=railway,
        restored_at=iso_now(),
    )
    validate_candidate(
        receipt,
        request=transfer,
        railway=railway,
        state_root=state_root,
    )
    _write_immutable(receipt_path, receipt)
    return Candidate(receipt_path, receipt, state_root)


def validate_grant(
    value: object,
    *,
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    now: datetime | None = None,
    require_fresh: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _GRANT_KEYS:
        raise TelegramMigrationError("Telegram authority grant fields are invalid")
    if (
        value.get("schema") != GRANT_SCHEMA
        or value.get("repository") != REPOSITORY
        or value.get("workflow") != WORKFLOW
        or value.get("commit") != request["commit"]
        or value.get("request_id") != request["request_id"]
        or value.get("candidate_receipt_sha256") != digest(canonical(candidate))
        or value.get("bot_token_sha256") != request["bot_token_sha256"]
        or value.get("confirmation") != GRANT_CONFIRMATION
    ):
        raise TelegramMigrationError("Telegram authority grant binding is invalid")
    activated_at = _utc(value.get("activated_at"), label="Telegram activated_at")
    requested_at = _utc(request["requested_at"], label="Telegram requested_at")
    expires_at = _utc(request["fence"]["expires_at"], label="Telegram expires_at")
    observed = now or datetime.now(UTC)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise TelegramMigrationError("Telegram grant clock is not timezone-aware")
    observed = observed.astimezone(UTC).replace(microsecond=0)
    if (
        not requested_at <= activated_at <= expires_at
        or activated_at > observed + REQUEST_FUTURE_SKEW
        or (require_fresh and observed > expires_at)
    ):
        raise TelegramMigrationError("Telegram authority grant timing is invalid")
    return dict(value)


def validate_worker_proof(
    value: object,
    *,
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    grant: Mapping[str, Any],
    railway: Mapping[str, str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _WORKER_PROOF_KEYS:
        raise TelegramMigrationError("Telegram worker proof fields are invalid")
    bot = value.get("bot")
    baseline = value.get("scheduler_baseline")
    if (
        value.get("schema") != WORKER_PROOF_SCHEMA
        or value.get("repository") != REPOSITORY
        or value.get("commit") != request["commit"]
        or value.get("request_id") != request["request_id"]
        or value.get("candidate_receipt_sha256") != digest(canonical(candidate))
        or value.get("grant_sha256") != digest(canonical(grant))
        or value.get("railway") != dict(railway)
        or value.get("initial_offset") != request["state"]["offset"]
        or not isinstance(value.get("observed_offset"), int)
        or value["observed_offset"] < value["initial_offset"]
        or value.get("get_updates_ok") is not True
        or value.get("conflict_observed") is not False
        or not isinstance(bot, dict)
        or set(bot) != {"id", "username"}
        or not isinstance(bot.get("id"), int)
        or bot["id"] <= 0
        or not isinstance(bot.get("username"), str)
        or re.fullmatch(r"[A-Za-z0-9_]{5,64}", bot["username"]) is None
        or not isinstance(baseline, dict)
        or set(baseline) != {"alert", "letter", "tandem"}
        or any(not isinstance(item, str) for item in baseline.values())
    ):
        raise TelegramMigrationError("Telegram worker proof is invalid")
    first_poll = _utc(value.get("first_poll_at"), label="Telegram first_poll_at")
    if first_poll < _utc(grant["activated_at"], label="Telegram grant activated_at"):
        raise TelegramMigrationError("Telegram worker proof predates authority")
    return dict(value)


def render_activation(
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    grant: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": ACTIVATION_SCHEMA,
        "repository": REPOSITORY,
        "workflow": WORKFLOW,
        "commit": request["commit"],
        "request_id": request["request_id"],
        "candidate_receipt_sha256": digest(canonical(candidate)),
        "grant_sha256": digest(canonical(grant)),
        "worker_proof_sha256": digest(canonical(proof)),
        "railway": dict(candidate["railway"]),
        "authority": {
            "mode": "production",
            "source": "railway",
            "hetzner_frozen": True,
            "sole_get_updates_consumer": True,
            "timers_started": True,
        },
        "bot": dict(proof["bot"]),
        "state": {
            "initial_offset": proof["initial_offset"],
            "observed_offset": proof["observed_offset"],
            "tree_sha256": request["state"]["tree_sha256"],
            "subscriber_count": request["state"]["subscriber_count"],
        },
        "scheduler_baseline": dict(proof["scheduler_baseline"]),
        "activated_at": proof["first_poll_at"],
        "research_only": True,
        "can_publish": True,
        "can_execute": False,
    }


def validate_activation(
    value: object,
    *,
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    grant: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _ACTIVATION_KEYS:
        raise TelegramMigrationError("Telegram activation receipt fields are invalid")
    expected = render_activation(request, candidate, grant, proof)
    if value != expected:
        raise TelegramMigrationError("Telegram activation receipt binding is invalid")
    return dict(value)


def finalize_activation(
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    grant: Mapping[str, Any],
    proof: Mapping[str, Any],
    *,
    root: Path = ROOT,
) -> tuple[Path, dict[str, Any]]:
    value = render_activation(request, candidate, grant, proof)
    validate_activation(
        value,
        request=request,
        candidate=candidate,
        grant=grant,
        proof=proof,
    )
    path = (
        root
        / "activations"
        / (f"{request['snapshot_id']}-{request['request_id']}.json")
    )
    _write_immutable(path, value)
    return path, value


def _decode_base64(value: str) -> bytes:
    if not 1 <= len(value) <= 1024 * 1024:
        raise TelegramMigrationError("Telegram document encoding is invalid")
    try:
        body = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TelegramMigrationError("Telegram document encoding is invalid") from exc
    if base64.b64encode(body).decode("ascii") != value:
        raise TelegramMigrationError("Telegram document encoding is not canonical")
    return body


def _decode_document(value: str, *, label: str) -> tuple[bytes, dict[str, Any]]:
    body = _decode_base64(value)
    try:
        document = platform._decode_canonical_json(body, label=label)
    except platform.MigrationContractError as exc:
        raise TelegramMigrationError(str(exc)) from exc
    return body, document


def publish_transfer(
    encoded: str,
    environment: Mapping[str, str],
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    body, value = _decode_document(encoded, label="Telegram transfer request")
    _image_body, image = image_context(environment)
    railway = railway_identity(environment)
    request = validate_transfer(
        value,
        image_request=image,
        railway=railway,
        expected_lab_channel_id=lab_channel_identity(environment),
    )
    prepare_root(root)
    with _authority_transaction(root):
        if _closed_authority_documents(root / "grants"):
            raise TelegramMigrationError("Telegram authority was already granted")
        archive = root / "incoming" / f"{request['request_id']}.tgz"
        if (
            digest(platform._stable_read(archive, maximum_bytes=MAX_STATE_BYTES))
            != request["archive_sha256"]
        ):
            raise TelegramMigrationError("uploaded Telegram archive digest differs")
        validate_archive(archive)
        path = root / "transfers" / f"{request['request_id']}.json"
        if path.exists() or path.is_symlink():
            existing, existing_value = load_document(
                path, label="Telegram transfer request"
            )
            if existing != body:
                raise TelegramMigrationError("immutable Telegram transfer differs")
            return existing_value
        _write_immutable(path, request)
        return request


def publish_grant(
    encoded: str,
    environment: Mapping[str, str],
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    _body, value = _decode_document(encoded, label="Telegram authority grant")
    _image_body, image = image_context(environment)
    railway = railway_identity(environment)
    prepare_root(root)
    with _authority_transaction(root):
        request_id = value.get("request_id") if isinstance(value, dict) else None
        if not isinstance(request_id, str) or _SHA64_RE.fullmatch(request_id) is None:
            raise TelegramMigrationError("Telegram grant request id is invalid")
        _request_body, request = load_document(
            root / "transfers" / f"{request_id}.json",
            label="Telegram transfer request",
        )
        validate_transfer(
            request,
            image_request=image,
            railway=railway,
            require_fresh=False,
            expected_lab_channel_id=lab_channel_identity(environment),
        )
        _candidate_body, candidate = load_document(
            candidate_path(root, request),
            label="Telegram candidate receipt",
        )
        validate_candidate(
            candidate,
            request=request,
            railway=railway,
            state_root=generation_path(root, request) / "seiche-bot",
        )
        grant = validate_grant(value, request=request, candidate=candidate)
        target = root / "grants" / f"{request_id}.json"
        if any(path != target for path in _closed_authority_documents(target.parent)):
            raise TelegramMigrationError("a different Telegram authority grant exists")
        _write_immutable(target, grant)
        return grant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    transfer = subparsers.add_parser("publish-transfer")
    transfer.add_argument("document_base64")
    grant = subparsers.add_parser("publish-grant")
    grant.add_argument("document_base64")
    inspect = subparsers.add_parser("inspect-state")
    inspect.add_argument("state_root")
    archive = subparsers.add_parser("validate-archive")
    archive.add_argument("archive")
    candidate = subparsers.add_parser("validate-candidate")
    candidate.add_argument("receipt")
    candidate.add_argument("--request", required=True)
    activation = subparsers.add_parser("validate-activation")
    activation.add_argument("receipt")
    activation.add_argument("--request", required=True)
    activation.add_argument("--candidate", required=True)
    activation.add_argument("--grant", required=True)
    activation.add_argument("--proof", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "publish-transfer":
        value = publish_transfer(arguments.document_base64, os.environ)
        print(value["request_id"])
        return 0
    if arguments.command == "publish-grant":
        value = publish_grant(arguments.document_base64, os.environ)
        print(value["request_id"])
        return 0
    if arguments.command == "inspect-state":
        print(canonical(inspect_state(Path(arguments.state_root))).decode(), end="")
        return 0
    if arguments.command == "validate-archive":
        validate_archive(Path(arguments.archive))
        print(
            digest(
                platform._stable_read(
                    Path(arguments.archive), maximum_bytes=MAX_STATE_BYTES
                )
            )
        )
        return 0
    _request_body, request_value = load_document(
        Path(arguments.request), label="Telegram transfer request"
    )
    railway = _validate_railway(request_value.get("railway"))
    _candidate_body, candidate_value = load_document(
        Path(
            arguments.receipt
            if arguments.command == "validate-candidate"
            else arguments.candidate
        ),
        label="Telegram candidate receipt",
    )
    validate_candidate(
        candidate_value,
        request=request_value,
        railway=railway,
    )
    if arguments.command == "validate-candidate":
        print(digest(_candidate_body))
        return 0
    _grant_body, grant_value = load_document(
        Path(arguments.grant), label="Telegram authority grant"
    )
    validate_grant(
        grant_value,
        request=request_value,
        candidate=candidate_value,
        require_fresh=False,
    )
    _proof_body, proof_value = load_document(
        Path(arguments.proof), label="Telegram worker proof"
    )
    validate_worker_proof(
        proof_value,
        request=request_value,
        candidate=candidate_value,
        grant=grant_value,
        railway=railway,
    )
    activation_body, activation_value = load_document(
        Path(arguments.receipt), label="Telegram activation receipt"
    )
    validate_activation(
        activation_value,
        request=request_value,
        candidate=candidate_value,
        grant=grant_value,
        proof=proof_value,
    )
    print(digest(activation_body))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TelegramMigrationError as error:
        print(f"seiche Railway Telegram migration: {error}", file=os.sys.stderr)
        raise SystemExit(1) from None
