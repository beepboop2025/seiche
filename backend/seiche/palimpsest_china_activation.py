"""Root-controlled activation transaction for accepted China-economic bundles.

This module is installed into Seiche's immutable, release-addressed intake
runtime.  The small root launcher in ``ops/deploy`` imports it only from that
runtime.  Candidate verification is then repeated as the unprivileged
``seiche`` identity under an empty environment before any API configuration is
changed.

The activation receipt authorizes one immutable bundle; ``active.json`` is the
atomic marker selecting the receipt currently exposed to the API.  A failed
REST or MCP proof restores the previous environment, systemd drop-in, and
marker before returning an error.  Candidate bundles and receipts are retained
as inert audit evidence and are never silently rewritten.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import time
from typing import Any, Iterator, Mapping, NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from seiche.palimpsest_china_intake import (
    MAX_ACCEPTANCE_BYTES,
    MAX_AVAILABILITY_BYTES,
    MAX_ARTIFACT_BYTES,
    MAX_CHECKSUMS_BYTES,
    MAX_HANDOFF_BYTES,
    MAX_INPUT_LEDGER_BYTES,
    MAX_LINEAGE_CHAIN_BYTES,
    MAX_LINEAGE_EVIDENCE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_PRODUCER_COMMIT_EVIDENCE_BYTES,
    MAX_PRODUCER_MAIN_EVIDENCE_BYTES,
    PalimpsestChinaIntakeError,
    load_accepted_export,
)


ACTIVATION_RECEIPT_SCHEMA = "seiche.palimpsest-china-activation-receipt.v1"
ACTIVE_MARKER_SCHEMA = "seiche.palimpsest-china-active.v1"
CANDIDATE_SCHEMA = "seiche.palimpsest-china-activation-candidate.v1"
RUNTIME_PROOF_SCHEMA = "seiche.palimpsest-china-rest-mcp-proof.v1"
PENDING_SCHEMA = "seiche.palimpsest-china-activation-pending.v1"
PRODUCTION_STATE_ROOT = Path("/var/lib/seiche-palimpsest-china")
PRODUCTION_ENV_FILE = Path("/etc/seiche/palimpsest-china.env")
PRODUCTION_DROPIN_FILE = Path(
    "/etc/systemd/system/seiche-api.service.d/palimpsest-china.conf"
)
PRODUCTION_DEPLOY_LOCK = Path("/run/seiche-deploy/deploy.lock")
PRODUCTION_ACTIVATION_LOCK = Path("/run/seiche-deploy/palimpsest-china.lock")
PRODUCTION_API_URL = "http://127.0.0.1:8787"
PRODUCTION_SYSTEMCTL = Path("/usr/bin/systemctl")
PRODUCTION_RUNUSER = Path("/usr/sbin/runuser")
PRODUCTION_ENV = Path("/usr/bin/env")
PRODUCTION_RUNTIME_ROOT = Path("/opt/seiche-palimpsest-china")
PRODUCTION_PYTHON = Path("/usr/bin/python3")
PRODUCTION_REPOSITORY = Path("/home/seiche/app")
PRODUCTION_DEPLOYED_SHA = Path("/var/lib/seiche-deploy/deployed-sha")
PRODUCTION_ALLOWED_SIGNERS = Path("/etc/seiche-release.allowed-signers")
PRODUCTION_GIT = Path("/usr/bin/git")
PRODUCTION_SSH_KEYGEN = Path("/usr/bin/ssh-keygen")
PRODUCTION_API_USER = "seiche"
PRODUCTION_RELEASE_PRINCIPAL = "beepboop2025@users.noreply.github.com"
LOCK_TIMEOUT_SECONDS = 300.0
PROBE_TIMEOUT_SECONDS = 180.0
PROBE_INTERVAL_SECONDS = 2.0

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "activation_id",
        "bundle_id",
        "release_sha",
        "files",
        "producer_repository",
        "producer_sha",
        "producer_workflow_run_id",
        "signer_key_id",
        "accepted_at",
        "rights_expires_at",
        "previous_receipt_sha256",
        "runtime_proof",
        "recorded_at",
    }
)
_ACTIVE_KEYS = frozenset(
    {
        "schema",
        "activation_id",
        "bundle_id",
        "release_sha",
        "receipt_path",
        "receipt_sha256",
        "files",
        "activated_at",
    }
)
_CANDIDATE_KEYS = frozenset(
    {
        "schema",
        "files",
        "signer_key_id",
        "accepted_at",
        "rights_expires_at",
        "producer_repository",
        "producer_sha",
        "producer_workflow_run_id",
    }
)
_PROOF_KEYS = frozenset(
    {
        "schema",
        "api_url",
        "rest_path",
        "mcp_path",
        "rest_files",
        "rest_signer_key_id",
        "mcp_files",
        "mcp_signer_key_id",
        "verified_at",
    }
)
_PENDING_KEYS = frozenset(
    {
        "schema",
        "candidate_activation_id",
        "candidate_bundle_id",
        "candidate_files",
        "previous_activation_id",
        "started_at",
    }
)


@dataclass(frozen=True, slots=True)
class _BundleFileSpec:
    source_field: str
    filename: str
    environment: str
    maximum: int


_BUNDLE_FILE_SPECS = (
    _BundleFileSpec(
        "manifest",
        "manifest.json",
        "SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH",
        MAX_MANIFEST_BYTES,
    ),
    _BundleFileSpec(
        "artifact",
        "artifact.jsonl",
        "SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH",
        MAX_ARTIFACT_BYTES,
    ),
    _BundleFileSpec(
        "input_ledger",
        "input-ledger.jsonl",
        "SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH",
        MAX_INPUT_LEDGER_BYTES,
    ),
    _BundleFileSpec(
        "availability",
        "availability.json",
        "SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH",
        MAX_AVAILABILITY_BYTES,
    ),
    _BundleFileSpec(
        "producer_commit_evidence",
        "github-commit.json",
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH",
        MAX_PRODUCER_COMMIT_EVIDENCE_BYTES,
    ),
    _BundleFileSpec(
        "producer_main_evidence",
        "github-main-branch.json",
        "SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH",
        MAX_PRODUCER_MAIN_EVIDENCE_BYTES,
    ),
    _BundleFileSpec(
        "handoff",
        "handoff-receipt.json",
        "SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH",
        MAX_HANDOFF_BYTES,
    ),
    _BundleFileSpec(
        "checksums",
        "SHA256SUMS",
        "SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH",
        MAX_CHECKSUMS_BYTES,
    ),
    _BundleFileSpec(
        "lineage_chain",
        "china-econ-wdi-lineage-chain.jsonl",
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH",
        MAX_LINEAGE_CHAIN_BYTES,
    ),
    _BundleFileSpec(
        "lineage_evidence",
        "github-commit-lineage-evidence.jsonl",
        "SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH",
        MAX_LINEAGE_EVIDENCE_BYTES,
    ),
    _BundleFileSpec(
        "acceptance",
        "acceptance.json",
        "SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH",
        MAX_ACCEPTANCE_BYTES,
    ),
)
_BUNDLE_FILENAMES = frozenset(spec.filename for spec in _BUNDLE_FILE_SPECS)
_MAXIMUM_BY_FILENAME = {spec.filename: spec.maximum for spec in _BUNDLE_FILE_SPECS}


@dataclass(frozen=True, slots=True)
class BundleSources:
    """The complete immutable Palimpsest handoff selected by an operator."""

    manifest: Path
    artifact: Path
    input_ledger: Path
    availability: Path
    producer_commit_evidence: Path
    producer_main_evidence: Path
    handoff: Path
    checksums: Path
    lineage_chain: Path
    lineage_evidence: Path
    acceptance: Path

    def files(self) -> dict[str, Path]:
        return {
            spec.filename: Path(getattr(self, spec.source_field))
            for spec in _BUNDLE_FILE_SPECS
        }


class PalimpsestChinaActivationError(RuntimeError):
    """The privileged activation transaction could not be proven safe."""


def _fail(message: str) -> NoReturn:
    raise PalimpsestChinaActivationError(message)


@dataclass(frozen=True, slots=True)
class ActivationPaths:
    """Fixed host paths and identities used by one activation transaction."""

    state_root: Path
    env_file: Path
    dropin_file: Path
    deploy_lock: Path
    activation_lock: Path
    runtime_release: Path
    release_sha: str
    root_uid: int
    root_gid: int
    api_uid: int
    api_gid: int
    api_user: str = PRODUCTION_API_USER
    api_url: str = PRODUCTION_API_URL
    systemctl: Path = PRODUCTION_SYSTEMCTL
    runuser: Path = PRODUCTION_RUNUSER
    env_program: Path = PRODUCTION_ENV
    python: Path = PRODUCTION_PYTHON
    portable: bool = False
    attest_dir: Path | None = None

    @property
    def receipts_dir(self) -> Path:
        return self.state_root / "receipts"

    @property
    def active_marker(self) -> Path:
        return self.state_root / "active.json"

    @property
    def pending_marker(self) -> Path:
        return self.state_root / "pending.json"


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    path: Path
    body: bytes | None
    uid: int
    gid: int
    mode: int


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise PalimpsestChinaActivationError(
            "activation document is not canonical JSON data"
        ) from exc


def _strict_json(body: bytes, *, label: str) -> dict[str, Any]:
    try:
        text = body.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaActivationError(f"{label} is not strict UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise PalimpsestChinaActivationError(
            f"{label} contains non-finite JSON number {value}"
        )

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise PalimpsestChinaActivationError(
                    f"{label} contains duplicate key {key!r}"
                )
            out[key] = value
        return out

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise PalimpsestChinaActivationError(f"{label} is not valid JSON") from exc
    if type(value) is not dict:
        _fail(f"{label} must be an object")
    if _canonical(value) != body:
        _fail(f"{label} must use exact canonical JSON bytes")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], *, label: str
) -> None:
    if frozenset(value) != expected:
        _fail(f"{label} fields changed")


def _sha(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(f"{label} must be lowercase SHA-256")
    return value


def _git_sha(value: object, *, label: str) -> str:
    if type(value) is not str or _GIT_SHA_RE.fullmatch(value) is None:
        _fail(f"{label} must be a lowercase 40-hex Git SHA")
    return value


def _timestamp(value: object, *, label: str) -> datetime:
    if type(value) is not str or not value.endswith("Z") or len(value) > 64:
        _fail(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PalimpsestChinaActivationError(f"{label} must be canonical UTC") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        _fail(f"{label} must be canonical UTC")
    return parsed.astimezone(UTC)


def _now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _file_hashes(value: object, *, label: str) -> dict[str, str]:
    if type(value) is not dict or frozenset(value) != _BUNDLE_FILENAMES:
        _fail(f"{label} must bind the exact eleven runtime files")
    return {
        name: _sha(value[name], label=f"{label}.{name}")
        for name in sorted(_BUNDLE_FILENAMES)
    }


def _bundle_id(hashes: Mapping[str, str]) -> str:
    files = _file_hashes(dict(hashes), label="bundle files")
    return _digest(b"seiche:palimpsest-china-bundle:v3\n" + _canonical(files))


def _activation_id(*, bundle_id: str, release_sha: str) -> str:
    _sha(bundle_id, label="bundle id")
    _git_sha(release_sha, label="activation release SHA")
    return _digest(
        f"seiche:palimpsest-china-activation:v1\n{bundle_id}\n{release_sha}\n".encode(
            "ascii"
        )
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_directory(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    label: str,
    validate_ancestry: bool = False,
) -> None:
    if (
        not path.is_absolute()
        or path == Path("/")
        or Path(os.path.normpath(path)) != path
    ):
        _fail(f"{label} path is not canonical")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PalimpsestChinaActivationError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        _fail(f"{label} ownership or mode is unsafe")
    if not validate_ancestry:
        return
    current = Path("/")
    for component in path.parts[1:-1]:
        current /= component
        try:
            ancestor = current.lstat()
        except OSError as exc:
            raise PalimpsestChinaActivationError(
                f"{label} ancestry is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(ancestor.st_mode)
            or ancestor.st_uid != uid
            or stat.S_IMODE(ancestor.st_mode) & 0o022
        ):
            _fail(f"{label} ancestry is not root-owned and protected")


def _validate_protected_path(path: Path, *, uid: int, label: str) -> None:
    """Reject non-canonical paths, symlink traversal, and writable ancestry."""

    if (
        not path.is_absolute()
        or path == Path("/")
        or Path(os.path.normpath(path)) != path
    ):
        _fail(f"{label} path is not canonical")
    current = Path("/")
    for component in path.parts[1:-1]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise PalimpsestChinaActivationError(
                f"{label} ancestry is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            _fail(f"{label} ancestry is not owner-controlled and protected")


def _stable_read(
    path: Path,
    *,
    label: str,
    maximum: int,
    uid: int,
    gid: int | None = None,
    modes: frozenset[int] | None = None,
    minimum: int = 1,
) -> bytes:
    if not path.is_absolute() or Path(os.path.normpath(path)) != path:
        _fail(f"{label} path is not canonical")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != uid
            or (gid is not None and before.st_gid != gid)
            or (modes is not None and stat.S_IMODE(before.st_mode) not in modes)
            or (before.st_dev, before.st_ino) != (visible.st_dev, visible.st_ino)
            or before.st_size < minimum
            or before.st_size > maximum
        ):
            _fail(f"{label} metadata is unsafe")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        after = os.fstat(descriptor)

        def identity(item: os.stat_result) -> tuple[int, ...]:
            return (
                item.st_dev,
                item.st_ino,
                item.st_mode,
                item.st_nlink,
                item.st_uid,
                item.st_gid,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if len(body) != before.st_size or identity(before) != identity(after):
            _fail(f"{label} changed while being read")
        return body
    except OSError as exc:
        raise PalimpsestChinaActivationError(f"{label} cannot be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _atomic_write(
    path: Path,
    body: bytes,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    stage = path.parent / f".{path.name}.{secrets.token_hex(16)}"
    descriptor = -1
    try:
        descriptor = os.open(
            stage,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written < 1:
                _fail(f"atomic write made no progress for {path}")
            offset += written
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(stage, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            f"could not atomically install {path}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            stage.unlink()
        except FileNotFoundError:
            pass


def _write_immutable(
    path: Path,
    body: bytes,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            mode,
        )
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written < 1:
                _fail(f"immutable write made no progress for {path}")
            offset += written
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _fsync_directory(path.parent)
    except FileExistsError:
        existing = _stable_read(
            path,
            label=f"existing immutable {path.name}",
            maximum=max(len(body), 1),
            uid=uid,
            gid=gid,
            modes=frozenset({mode}),
        )
        if existing != body:
            _fail(f"immutable target {path} already contains different bytes")
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            f"could not install immutable {path}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _rename_noreplace(
    source: Path, destination: Path, *, portable: bool = False
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        if portable:
            try:
                destination.lstat()
            except FileNotFoundError:
                source.rename(destination)
                return
            raise FileExistsError(destination)
        _fail("activation requires Linux renameat2")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(destination)
    raise OSError(error, os.strerror(error), destination)


def _validate_bundle(
    bundle: Path,
    *,
    paths: ActivationPaths,
    expected: Mapping[str, bytes],
) -> None:
    _validate_directory(
        bundle,
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o750,
        label="Palimpsest China bundle",
    )
    try:
        names = {child.name for child in bundle.iterdir()}
    except OSError as exc:
        raise PalimpsestChinaActivationError("bundle cannot be listed") from exc
    if names != set(expected):
        _fail("bundle members changed")
    for name, body in expected.items():
        installed = _stable_read(
            bundle / name,
            label=f"installed bundle {name}",
            maximum=_MAXIMUM_BY_FILENAME[name],
            uid=paths.root_uid,
            gid=paths.api_gid,
            modes=frozenset({0o440}),
        )
        if installed != body:
            _fail(f"installed bundle {name} differs from reviewed input")


def _install_bundle(
    source_files: Mapping[str, Path], *, paths: ActivationPaths
) -> tuple[Path, dict[str, str]]:
    if frozenset(source_files) != _BUNDLE_FILENAMES:
        _fail("activation requires the exact eleven handoff files")
    bodies: dict[str, bytes] = {}
    for name, source in source_files.items():
        source = Path(source)
        if not paths.portable:
            _validate_protected_path(
                source,
                uid=paths.root_uid,
                label=f"operator source {name}",
            )
        bodies[name] = _stable_read(
            source,
            label=f"operator source {name}",
            maximum=_MAXIMUM_BY_FILENAME[name],
            uid=paths.root_uid,
            gid=paths.root_gid,
            modes=frozenset({0o400, 0o600}),
        )
    hashes = {name: _digest(body) for name, body in sorted(bodies.items())}
    identifier = _bundle_id(hashes)
    target = paths.state_root / identifier
    if target.exists() or target.is_symlink():
        _validate_bundle(target, paths=paths, expected=bodies)
        return target, hashes

    stage = paths.state_root / f".bundle-stage-{secrets.token_hex(16)}"
    try:
        os.mkdir(stage, 0o700)
        os.chown(stage, paths.root_uid, paths.api_gid)
        for name, body in bodies.items():
            _write_immutable(
                stage / name,
                body,
                uid=paths.root_uid,
                gid=paths.api_gid,
                mode=0o440,
            )
        os.chmod(stage, 0o750)
        _fsync_directory(stage)
        try:
            _rename_noreplace(stage, target, portable=paths.portable)
        except FileExistsError:
            _validate_bundle(target, paths=paths, expected=bodies)
        _fsync_directory(paths.state_root)
        _validate_bundle(target, paths=paths, expected=bodies)
        return target, hashes
    finally:
        if stage.exists() and not stage.is_symlink():
            for name in bodies:
                try:
                    (stage / name).unlink()
                except FileNotFoundError:
                    pass
            try:
                stage.rmdir()
            except FileNotFoundError:
                pass


def _validate_lock(descriptor: int, path: Path, *, paths: ActivationPaths) -> None:
    opened = os.fstat(descriptor)
    visible = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_uid != paths.root_uid
        or opened.st_gid != paths.root_gid
        or stat.S_IMODE(opened.st_mode) != 0o600
        or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
    ):
        _fail(f"lock metadata is unsafe: {path}")


@contextmanager
def _exclusive_lock(
    path: Path, *, paths: ActivationPaths, create: bool
) -> Iterator[int]:
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        if create:
            try:
                descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                os.fchown(descriptor, paths.root_uid, paths.root_gid)
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                _fsync_directory(path.parent)
            except FileExistsError:
                descriptor = os.open(path, flags)
        else:
            descriptor = os.open(path, flags)
        _validate_lock(descriptor, path, paths=paths)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail(f"lock remained busy for 300 seconds: {path}")
            time.sleep(min(0.1, remaining))
        _validate_lock(descriptor, path, paths=paths)
        yield descriptor
    except OSError as exc:
        raise PalimpsestChinaActivationError(f"cannot acquire lock {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def _deployment_lock(
    *, paths: ActivationPaths, descriptor: int | None
) -> Iterator[None]:
    if descriptor is None:
        with _exclusive_lock(paths.deploy_lock, paths=paths, create=False):
            yield
        return
    if (
        isinstance(descriptor, bool)
        or not isinstance(descriptor, int)
        or descriptor < 0
    ):
        _fail("provided deploy lock descriptor is invalid")
    _validate_lock(descriptor, paths.deploy_lock, paths=paths)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "provided deploy lock descriptor is not exclusively held"
        ) from exc
    _validate_lock(descriptor, paths.deploy_lock, paths=paths)
    yield


def _required_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} is missing")
    return value


def _candidate_from_context(
    sources: BundleSources,
    *,
    attest_dir: Path | None = None,
) -> dict[str, Any]:
    context = load_accepted_export(
        sources.manifest,
        sources.artifact,
        sources.acceptance,
        input_ledger_path=sources.input_ledger,
        availability_path=sources.availability,
        producer_commit_evidence_path=sources.producer_commit_evidence,
        producer_main_evidence_path=sources.producer_main_evidence,
        handoff_path=sources.handoff,
        checksums_path=sources.checksums,
        lineage_chain_path=sources.lineage_chain,
        lineage_evidence_path=sources.lineage_evidence,
        attest_dir=attest_dir,
        now=datetime.now(UTC),
    )
    if not context.owner_attested or not isinstance(context.producer, Mapping):
        _fail("candidate lacks owner-attested serving authority")
    workflow = context.producer.get("workflow_run")
    if not isinstance(workflow, Mapping):
        _fail("candidate lacks an authoritative producer workflow")
    expiry = context.source_decision.get("expires_at")
    _timestamp(expiry, label="candidate rights expiry")
    commit = _required_mapping(
        context.producer_commit_evidence,
        label="candidate producer commit evidence",
    )
    main = _required_mapping(
        context.producer_main_evidence,
        label="candidate producer main evidence",
    )
    handoff = _required_mapping(
        context.handoff_receipt,
        label="candidate handoff receipt",
    )
    checksums = _required_mapping(
        context.checksum_subject,
        label="candidate checksum subject",
    )
    lineage = _required_mapping(
        context.governed_lineage,
        label="candidate governed lineage",
    )
    lineage_evidence = _required_mapping(
        lineage.get("evidence"),
        label="candidate governed lineage evidence",
    )
    if context.availability_receipt_sha256 is None:
        _fail("candidate lacks an availability receipt")
    if context.acceptance_sha256 is None or context.acceptance_signer_key_id is None:
        _fail("candidate lacks an acceptance authority")
    files = {
        "manifest.json": context.manifest_sha256,
        "artifact.jsonl": context.artifact_sha256,
        "input-ledger.jsonl": context.input_ledger_sha256,
        "availability.json": context.availability_receipt_sha256,
        "github-commit.json": commit.get("sha256"),
        "github-main-branch.json": main.get("sha256"),
        "handoff-receipt.json": handoff.get("sha256"),
        "SHA256SUMS": checksums.get("sha256"),
        "china-econ-wdi-lineage-chain.jsonl": lineage.get("sha256"),
        "github-commit-lineage-evidence.jsonl": lineage_evidence.get("sha256"),
        "acceptance.json": context.acceptance_sha256,
    }
    return {
        "schema": CANDIDATE_SCHEMA,
        "files": _file_hashes(files, label="candidate files"),
        "signer_key_id": context.acceptance_signer_key_id,
        "accepted_at": context.accepted_at,
        "rights_expires_at": expiry,
        "producer_repository": context.producer.get("repository"),
        "producer_sha": context.producer.get("commit_sha"),
        "producer_workflow_run_id": workflow.get("run_id"),
    }


def guarded_verify_main(arguments: list[str]) -> int:
    """Unprivileged exact-runtime child used by the installed launcher."""

    if len(arguments) != len(_BUNDLE_FILE_SPECS):
        _fail("guarded candidate verification requires exactly eleven paths")
    candidate = _candidate_from_context(
        BundleSources(*(Path(value) for value in arguments))
    )
    print(_canonical(candidate).decode("utf-8"), end="")
    return 0


_CHILD_CODE = """
import pathlib
import sys
release = pathlib.Path(sys.argv.pop(1))
sys.path.insert(0, str(release))
from seiche import palimpsest_china_activation as activation
expected = release / "seiche" / "palimpsest_china_activation.py"
if pathlib.Path(activation.__file__) != expected:
    raise SystemExit("activation module resolved outside the exact runtime")
raise SystemExit(activation.guarded_verify_main(sys.argv[1:]))
""".strip()


def _parse_candidate(body: bytes, *, expected: Mapping[str, str]) -> dict[str, Any]:
    candidate = _strict_json(body, label="candidate verification result")
    _exact_keys(candidate, _CANDIDATE_KEYS, label="candidate verification result")
    if candidate["schema"] != CANDIDATE_SCHEMA:
        _fail("candidate verification schema changed")
    files = _file_hashes(candidate["files"], label="candidate.files")
    expected_files = _file_hashes(dict(expected), label="installed files")
    if files != expected_files:
        _fail("candidate verifier returned mismatched runtime file hashes")
    _sha(candidate["signer_key_id"], label="candidate.signer_key_id")
    _timestamp(candidate["accepted_at"], label="candidate.accepted_at")
    expiry = _timestamp(
        candidate["rights_expires_at"], label="candidate.rights_expires_at"
    )
    if expiry <= datetime.now(UTC):
        _fail("candidate rights expired during activation")
    if candidate["producer_repository"] != "beepboop2025/palimpsest":
        _fail("candidate producer repository changed")
    _git_sha(candidate["producer_sha"], label="candidate.producer_sha")
    run_id = candidate["producer_workflow_run_id"]
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        _fail("candidate producer run id is invalid")
    return candidate


def _verify_candidate(
    bundle: Path,
    *,
    hashes: Mapping[str, str],
    paths: ActivationPaths,
) -> dict[str, Any]:
    sources = BundleSources(*(bundle / spec.filename for spec in _BUNDLE_FILE_SPECS))
    if paths.portable:
        try:
            candidate = _candidate_from_context(
                sources,
                attest_dir=paths.attest_dir,
            )
        except (OSError, PalimpsestChinaIntakeError) as exc:
            raise PalimpsestChinaActivationError(
                "portable candidate verification failed"
            ) from exc
        return _parse_candidate(_canonical(candidate), expected=hashes)

    command = [
        str(paths.runuser),
        "-u",
        paths.api_user,
        "--",
        str(paths.env_program),
        "-i",
        "HOME=/var/lib/seiche",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "PATH=/usr/bin:/bin",
        "PYTHONNOUSERSITE=1",
        str(paths.python),
        "-I",
        "-B",
        "-c",
        _CHILD_CODE,
        str(paths.runtime_release),
        *(str(path) for path in sources.files().values()),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONNOUSERSITE": "1",
            },
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PalimpsestChinaActivationError(
            "unprivileged candidate verifier could not run"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:1000]
        _fail(f"unprivileged candidate verifier rejected the bundle: {detail}")
    if len(result.stdout) > 64 * 1024:
        _fail("unprivileged candidate verifier returned excessive output")
    return _parse_candidate(result.stdout, expected=hashes)


def _validate_receipt(
    receipt: Mapping[str, Any], *, expected_path: Path | None = None
) -> None:
    _exact_keys(receipt, _RECEIPT_KEYS, label="activation receipt")
    if receipt["schema"] != ACTIVATION_RECEIPT_SCHEMA:
        _fail("activation receipt schema changed")
    for key in ("activation_id", "bundle_id", "signer_key_id"):
        _sha(receipt[key], label=f"activation receipt.{key}")
    files = _file_hashes(receipt["files"], label="activation receipt.files")
    if receipt["bundle_id"] != _bundle_id(files):
        _fail("activation receipt bundle identity changed")
    _git_sha(receipt["release_sha"], label="activation receipt.release_sha")
    _git_sha(receipt["producer_sha"], label="activation receipt.producer_sha")
    if receipt["producer_repository"] != "beepboop2025/palimpsest":
        _fail("activation receipt producer repository changed")
    run_id = receipt["producer_workflow_run_id"]
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        _fail("activation receipt producer run id is invalid")
    accepted_at = _timestamp(
        receipt["accepted_at"], label="activation receipt.accepted_at"
    )
    expires_at = _timestamp(
        receipt["rights_expires_at"], label="activation receipt.rights_expires_at"
    )
    recorded_at = _timestamp(
        receipt["recorded_at"], label="activation receipt.recorded_at"
    )
    if (
        expires_at <= accepted_at
        or recorded_at < accepted_at
        or expires_at <= recorded_at
    ):
        _fail("activation receipt clocks are inconsistent")
    previous = receipt["previous_receipt_sha256"]
    if previous is not None:
        _sha(previous, label="activation receipt.previous_receipt_sha256")
    proof = receipt["runtime_proof"]
    if type(proof) is not dict:
        _fail("activation receipt runtime proof is malformed")
    _exact_keys(proof, _PROOF_KEYS, label="activation receipt.runtime_proof")
    if proof["schema"] != RUNTIME_PROOF_SCHEMA:
        _fail("activation receipt runtime proof schema changed")
    for key in ("api_url", "rest_path", "mcp_path"):
        value = proof[key]
        if type(value) is not str or not value or len(value) > 1024:
            _fail(f"activation receipt runtime_proof.{key} is invalid")
    if (
        not proof["api_url"].startswith("http://127.0.0.1:")
        or proof["api_url"].endswith("/")
        or proof["rest_path"] != "/api/v2/world-markets?section=china_macro"
        or proof["mcp_path"] != "/mcp"
    ):
        _fail("activation receipt runtime proof endpoints changed")
    for surface in ("rest", "mcp"):
        served_files = _file_hashes(
            proof[f"{surface}_files"],
            label=f"activation receipt.runtime_proof.{surface}_files",
        )
        if served_files != files:
            _fail(f"activation receipt {surface.upper()} proof files changed")
        served_signer = _sha(
            proof[f"{surface}_signer_key_id"],
            label=f"activation receipt.runtime_proof.{surface}_signer_key_id",
        )
        if served_signer != receipt["signer_key_id"]:
            _fail(f"activation receipt {surface.upper()} proof signer changed")
    verified_at = _timestamp(
        proof["verified_at"], label="activation receipt.runtime_proof.verified_at"
    )
    if verified_at < accepted_at or verified_at > recorded_at:
        _fail("activation receipt proof clock is inconsistent")
    if receipt["activation_id"] != _activation_id(
        bundle_id=receipt["bundle_id"],
        release_sha=receipt["release_sha"],
    ):
        _fail("activation receipt identity changed")
    if expected_path is not None and expected_path.name != (
        f"{receipt['activation_id']}.json"
    ):
        _fail("activation receipt filename does not bind its bundle")


def _read_receipt(
    path: Path, *, paths: ActivationPaths
) -> tuple[dict[str, Any], bytes]:
    body = _stable_read(
        path,
        label="activation receipt",
        maximum=16 * 1024,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    receipt = _strict_json(body, label="activation receipt")
    _validate_receipt(receipt, expected_path=path)
    return receipt, body


def _validate_active(value: Mapping[str, Any], *, paths: ActivationPaths) -> None:
    _exact_keys(value, _ACTIVE_KEYS, label="active marker")
    if value["schema"] != ACTIVE_MARKER_SCHEMA:
        _fail("active marker schema changed")
    for key in ("activation_id", "bundle_id", "receipt_sha256"):
        _sha(value[key], label=f"active marker.{key}")
    _git_sha(value["release_sha"], label="active marker.release_sha")
    files = _file_hashes(value["files"], label="active marker.files")
    if value["bundle_id"] != _bundle_id(files):
        _fail("active marker bundle identity changed")
    _timestamp(value["activated_at"], label="active marker.activated_at")
    if value["activation_id"] != _activation_id(
        bundle_id=value["bundle_id"], release_sha=value["release_sha"]
    ):
        _fail("active marker identity changed")
    expected = paths.receipts_dir / f"{value['activation_id']}.json"
    if value["receipt_path"] != str(expected):
        _fail("active marker receipt path changed")


def _read_active(
    paths: ActivationPaths,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    marker = paths.active_marker
    if not marker.exists() and not marker.is_symlink():
        return None
    body = _stable_read(
        marker,
        label="active marker",
        maximum=4096,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    active = _strict_json(body, label="active marker")
    _validate_active(active, paths=paths)
    receipt_path = Path(active["receipt_path"])
    receipt, receipt_body = _read_receipt(receipt_path, paths=paths)
    if (
        _digest(receipt_body) != active["receipt_sha256"]
        or receipt["activation_id"] != active["activation_id"]
        or receipt["bundle_id"] != active["bundle_id"]
        or receipt["release_sha"] != active["release_sha"]
        or receipt["files"] != active["files"]
    ):
        _fail("active marker does not bind its immutable receipt")
    activated_at = _timestamp(
        active["activated_at"], label="active marker.activated_at"
    )
    if activated_at < _timestamp(
        receipt["recorded_at"], label="activation receipt.recorded_at"
    ) or activated_at >= _timestamp(
        receipt["rights_expires_at"], label="activation receipt.rights_expires_at"
    ):
        _fail("active marker clock falls outside its proved rights interval")
    return active, receipt


def _validate_pending(value: Mapping[str, Any]) -> None:
    _exact_keys(value, _PENDING_KEYS, label="pending activation marker")
    if value["schema"] != PENDING_SCHEMA:
        _fail("pending activation marker schema changed")
    _sha(
        value["candidate_activation_id"],
        label="pending activation candidate_activation_id",
    )
    _sha(value["candidate_bundle_id"], label="pending activation candidate_bundle_id")
    candidate_files = _file_hashes(
        value["candidate_files"],
        label="pending activation candidate_files",
    )
    if value["candidate_bundle_id"] != _bundle_id(candidate_files):
        _fail("pending activation bundle identity changed")
    previous = value["previous_activation_id"]
    if previous is not None:
        _sha(previous, label="pending activation previous_activation_id")
    _timestamp(value["started_at"], label="pending activation started_at")


def _read_pending(paths: ActivationPaths) -> dict[str, Any] | None:
    if not paths.pending_marker.exists() and not paths.pending_marker.is_symlink():
        return None
    body = _stable_read(
        paths.pending_marker,
        label="pending activation marker",
        maximum=8192,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o400}),
    )
    pending = _strict_json(body, label="pending activation marker")
    _validate_pending(pending)
    return pending


def _remove_controlled_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    if not path.exists() and not path.is_symlink():
        return
    _stable_read(
        path,
        label=label,
        maximum=maximum,
        uid=uid,
        gid=gid,
        modes=frozenset({mode}),
    )
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise PalimpsestChinaActivationError(f"could not remove {label}") from exc


def _render_env(bundle: Path) -> bytes:
    lines = [
        f"{spec.environment}={bundle / spec.filename}\n" for spec in _BUNDLE_FILE_SPECS
    ]
    try:
        return "".join(lines).encode("ascii")
    except UnicodeEncodeError as exc:
        raise PalimpsestChinaActivationError(
            "runtime bundle path must be ASCII"
        ) from exc


def _render_dropin(bundle: Path, *, env_file: Path) -> bytes:
    return (
        "[Unit]\n"
        f"RequiresMountsFor={bundle} {env_file}\n\n"
        "[Service]\n"
        f"EnvironmentFile={env_file}\n"
        f"ReadOnlyPaths={bundle} {env_file}\n"
    ).encode("ascii")


def _snapshot(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    maximum: int,
) -> _FileSnapshot:
    if not path.exists() and not path.is_symlink():
        return _FileSnapshot(path, None, uid, gid, mode)
    body = _stable_read(
        path,
        label=f"pre-activation {path.name}",
        maximum=maximum,
        uid=uid,
        gid=gid,
        modes=frozenset({mode}),
    )
    return _FileSnapshot(path, body, uid, gid, mode)


def _restore(snapshot: _FileSnapshot) -> None:
    if snapshot.body is None:
        if snapshot.path.exists() or snapshot.path.is_symlink():
            metadata = snapshot.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != snapshot.uid
                or metadata.st_gid != snapshot.gid
                or stat.S_IMODE(metadata.st_mode) != snapshot.mode
            ):
                _fail(f"refusing to remove unsafe rollback target {snapshot.path}")
            snapshot.path.unlink()
            _fsync_directory(snapshot.path.parent)
        return
    _atomic_write(
        snapshot.path,
        snapshot.body,
        uid=snapshot.uid,
        gid=snapshot.gid,
        mode=snapshot.mode,
    )


def _economic_context(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    china = payload.get("china_macro")
    if not isinstance(china, Mapping):
        _fail("China REST/MCP proof omitted china_macro")
    economic = china.get("economic_context")
    if economic is None:
        return None
    if not isinstance(economic, Mapping):
        _fail("China REST/MCP economic context is malformed")
    return economic


def _assert_projection(
    payload: Mapping[str, Any], expected: Mapping[str, Any] | None
) -> None:
    economic = _economic_context(payload)
    if expected is None:
        if economic is not None:
            _fail("rollback proof still exposes a China economic bundle")
        return
    if economic is None:
        _fail("activation proof did not expose the accepted China context")
    provenance = economic.get("provenance")
    if not isinstance(provenance, Mapping):
        _fail("activation proof returned malformed China provenance")
    files = _file_hashes(expected.get("files"), label="served expected files")
    commit = provenance.get("producer_commit_evidence")
    main = provenance.get("producer_main_evidence")
    handoff = provenance.get("handoff_receipt")
    checksums = provenance.get("checksum_subject")
    lineage = provenance.get("governed_lineage")
    lineage_evidence = lineage.get("evidence") if isinstance(lineage, Mapping) else None
    exact = {
        "manifest.json": provenance.get("manifest_sha256"),
        "artifact.jsonl": provenance.get("artifact_sha256"),
        "input-ledger.jsonl": provenance.get("input_ledger_sha256"),
        "availability.json": provenance.get("availability_receipt_sha256"),
        "github-commit.json": (
            commit.get("sha256") if isinstance(commit, Mapping) else None
        ),
        "github-main-branch.json": (
            main.get("sha256") if isinstance(main, Mapping) else None
        ),
        "handoff-receipt.json": (
            handoff.get("sha256") if isinstance(handoff, Mapping) else None
        ),
        "SHA256SUMS": (
            checksums.get("sha256") if isinstance(checksums, Mapping) else None
        ),
        "china-econ-wdi-lineage-chain.jsonl": (
            lineage.get("sha256") if isinstance(lineage, Mapping) else None
        ),
        "github-commit-lineage-evidence.jsonl": (
            lineage_evidence.get("sha256")
            if isinstance(lineage_evidence, Mapping)
            else None
        ),
        "acceptance.json": provenance.get("acceptance_sha256"),
    }
    if (
        economic.get("schema") != "seiche.palimpsest-china-economic-context.v1"
        or economic.get("context_only") is not True
        or economic.get("scoring_eligible") is not False
        or economic.get("cn_cny_gauge_eligible") is not False
        or provenance.get("owner_attestation") != "ed25519"
        or provenance.get("acceptance_signer_key_id") != expected.get("signer_key_id")
        or exact != files
    ):
        _fail("activation proof returned the wrong China economic authority")


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, method=method, headers=headers)

    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(
            self,
            request: Request,
            file_pointer: Any,
            code: int,
            message: str,
            headers: Any,
            new_url: str,
        ) -> None:
            del request, file_pointer, code, message, headers, new_url
            return None

    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            if response.status != 200:
                _fail(f"proof endpoint returned HTTP {response.status}")
            payload = response.read(4 * 1024 * 1024 + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise PalimpsestChinaActivationError(
            f"proof endpoint is unavailable: {url}"
        ) from exc
    if len(payload) > 4 * 1024 * 1024:
        _fail("proof endpoint response is too large")
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "proof response is not strict UTF-8"
        ) from exc

    def reject_constant(value: str) -> None:
        raise PalimpsestChinaActivationError(
            f"proof response contains non-finite JSON number {value}"
        )

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                _fail(f"proof response contains duplicate key {key!r}")
            out[key] = value
        return out

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "proof response is not valid JSON"
        ) from exc
    if type(value) is not dict:
        _fail("proof response must be an object")
    return value


def _probe_rest_and_mcp(
    *, paths: ActivationPaths, expected: Mapping[str, Any] | None
) -> None:
    rest = _http_json(f"{paths.api_url}/api/v2/world-markets?section=china_macro")
    _assert_projection(rest, expected)
    call = _canonical(
        {
            "jsonrpc": "2.0",
            "id": "palimpsest-china-activation-proof",
            "method": "tools/call",
            "params": {
                "name": "world_markets_context",
                "arguments": {"section": "china_macro"},
            },
        }
    )
    rpc = _http_json(f"{paths.api_url}/mcp", method="POST", body=call)
    if (
        rpc.get("jsonrpc") != "2.0"
        or rpc.get("id") != "palimpsest-china-activation-proof"
    ):
        _fail("MCP activation proof response identity changed")
    result = rpc.get("result")
    content = result.get("content") if isinstance(result, Mapping) else None
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], Mapping)
        or content[0].get("type") != "text"
        or not isinstance(content[0].get("text"), str)
    ):
        _fail("MCP activation proof response is malformed")
    try:
        mcp_payload = json.loads(content[0]["text"])
    except json.JSONDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "MCP activation proof text is not JSON"
        ) from exc
    if not isinstance(mcp_payload, dict):
        _fail("MCP activation proof payload is not an object")
    _assert_projection(mcp_payload, expected)


def _systemctl(paths: ActivationPaths, *arguments: str) -> None:
    try:
        result = subprocess.run(
            [str(paths.systemctl), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
            },
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PalimpsestChinaActivationError(
            f"systemctl {' '.join(arguments)} could not run"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:1000]
        _fail(f"systemctl {' '.join(arguments)} failed: {detail}")


def _served_proof(
    *, paths: ActivationPaths, expected: Mapping[str, Any]
) -> dict[str, Any]:
    files = _file_hashes(expected.get("files"), label="served proof files")
    signer_key_id = _sha(
        expected.get("signer_key_id"),
        label="served proof signer_key_id",
    )
    return {
        "schema": RUNTIME_PROOF_SCHEMA,
        "api_url": paths.api_url,
        "rest_path": "/api/v2/world-markets?section=china_macro",
        "mcp_path": "/mcp",
        "rest_files": files,
        "rest_signer_key_id": signer_key_id,
        "mcp_files": files,
        "mcp_signer_key_id": signer_key_id,
        "verified_at": _now_text(),
    }


def _restart_and_probe(
    *, paths: ActivationPaths, expected: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    if paths.portable:
        return (
            None if expected is None else _served_proof(paths=paths, expected=expected)
        )
    _systemctl(paths, "daemon-reload")
    _systemctl(paths, "restart", "seiche-api.service")
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    last_error = ""
    while True:
        try:
            _probe_rest_and_mcp(paths=paths, expected=expected)
            return (
                None
                if expected is None
                else _served_proof(
                    paths=paths,
                    expected=expected,
                )
            )
        except PalimpsestChinaActivationError as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail(f"REST/MCP activation proof timed out: {last_error}")
        time.sleep(min(PROBE_INTERVAL_SECONDS, remaining))


def _active_expected(
    active_receipt: tuple[dict[str, Any], dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if active_receipt is None:
        return None
    active, _receipt = active_receipt
    return {
        "files": dict(active["files"]),
        "signer_key_id": _receipt["signer_key_id"],
    }


def _validate_bundle_hashes(
    bundle: Path, *, expected: Mapping[str, str], paths: ActivationPaths
) -> None:
    files = _file_hashes(dict(expected), label="installed bundle files")
    _validate_directory(
        bundle,
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o750,
        label="installed Palimpsest China bundle",
    )
    try:
        names = {entry.name for entry in bundle.iterdir()}
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "installed Palimpsest China bundle cannot be listed"
        ) from exc
    if names != _BUNDLE_FILENAMES:
        _fail("installed Palimpsest China bundle members changed")
    for spec in _BUNDLE_FILE_SPECS:
        body = _stable_read(
            bundle / spec.filename,
            label=f"installed bundle {spec.filename}",
            maximum=spec.maximum,
            uid=paths.root_uid,
            gid=paths.api_gid,
            modes=frozenset({0o440}),
        )
        if _digest(body) != files[spec.filename]:
            _fail(f"installed bundle {spec.filename} digest changed")


def _assert_config_consistency(
    active_receipt: tuple[dict[str, Any], dict[str, Any]] | None,
    *,
    paths: ActivationPaths,
) -> None:
    exists = [
        path.exists() or path.is_symlink()
        for path in (paths.env_file, paths.dropin_file, paths.active_marker)
    ]
    if active_receipt is None:
        if any(exists):
            _fail("partial Palimpsest China activation state requires operator review")
        return
    if not all(exists):
        _fail("active marker exists without complete API configuration")
    active, _receipt = active_receipt
    bundle = paths.state_root / active["bundle_id"]
    _validate_bundle_hashes(bundle, expected=active["files"], paths=paths)
    env = _stable_read(
        paths.env_file,
        label="active Palimpsest China environment",
        maximum=4096,
        uid=paths.root_uid,
        gid=paths.api_gid,
        modes=frozenset({0o640}),
    )
    dropin = _stable_read(
        paths.dropin_file,
        label="active Palimpsest China API drop-in",
        maximum=4096,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o644}),
    )
    if env != _render_env(bundle) or dropin != _render_dropin(
        bundle, env_file=paths.env_file
    ):
        _fail("active marker and API configuration disagree")


def _configured_body(
    path: Path,
    *,
    label: str,
    uid: int,
    gid: int,
    mode: int,
) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    return _stable_read(
        path,
        label=label,
        maximum=4096,
        uid=uid,
        gid=gid,
        modes=frozenset({mode}),
    )


def _recover_pending(paths: ActivationPaths) -> None:
    """Finish or roll back a transaction interrupted between atomic writes."""

    pending = _read_pending(paths)
    if pending is None:
        return
    candidate_bundle = paths.state_root / pending["candidate_bundle_id"]
    _validate_bundle_hashes(
        candidate_bundle,
        expected=pending["candidate_files"],
        paths=paths,
    )
    active = _read_active(paths)
    active_id = active[0]["activation_id"] if active is not None else None
    if active_id == pending["candidate_activation_id"]:
        if active is None or active[0]["files"] != pending["candidate_files"]:
            _fail("completed pending activation does not bind its candidate files")
        _assert_config_consistency(active, paths=paths)
        _restart_and_probe(paths=paths, expected=_active_expected(active))
        _remove_controlled_file(
            paths.pending_marker,
            label="completed pending activation marker",
            maximum=8192,
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o400,
        )
        return
    if active_id != pending["previous_activation_id"]:
        _fail("pending activation does not bind the current active marker")

    previous_bundle = (
        paths.state_root / active[0]["bundle_id"] if active is not None else None
    )
    candidate_env = _render_env(candidate_bundle)
    candidate_dropin = _render_dropin(candidate_bundle, env_file=paths.env_file)
    previous_env = _render_env(previous_bundle) if previous_bundle is not None else None
    previous_dropin = (
        _render_dropin(previous_bundle, env_file=paths.env_file)
        if previous_bundle is not None
        else None
    )
    current_env = _configured_body(
        paths.env_file,
        label="interrupted activation environment",
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o640,
    )
    current_dropin = _configured_body(
        paths.dropin_file,
        label="interrupted activation drop-in",
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o644,
    )
    if current_env not in {None, candidate_env, previous_env} or current_dropin not in {
        None,
        candidate_dropin,
        previous_dropin,
    }:
        _fail("interrupted activation configuration was modified unexpectedly")

    if previous_bundle is None:
        _remove_controlled_file(
            paths.env_file,
            label="interrupted activation environment",
            maximum=4096,
            uid=paths.root_uid,
            gid=paths.api_gid,
            mode=0o640,
        )
        _remove_controlled_file(
            paths.dropin_file,
            label="interrupted activation drop-in",
            maximum=4096,
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o644,
        )
    else:
        assert previous_env is not None and previous_dropin is not None
        _atomic_write(
            paths.env_file,
            previous_env,
            uid=paths.root_uid,
            gid=paths.api_gid,
            mode=0o640,
        )
        _atomic_write(
            paths.dropin_file,
            previous_dropin,
            uid=paths.root_uid,
            gid=paths.root_gid,
            mode=0o644,
        )
    _restart_and_probe(paths=paths, expected=_active_expected(active))
    _remove_controlled_file(
        paths.pending_marker,
        label="rolled-back pending activation marker",
        maximum=8192,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o400,
    )


def _receipt_floor(
    candidate: Mapping[str, Any],
    *,
    bundle_id: str,
    paths: ActivationPaths,
) -> None:
    """Reject a newly accepted bundle older than any retained activation."""

    try:
        entries = sorted(paths.receipts_dir.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "activation receipts cannot be enumerated"
        ) from exc
    accepted_at = _timestamp(candidate["accepted_at"], label="candidate.accepted_at")
    run_id = candidate["producer_workflow_run_id"]
    for entry in entries:
        if entry.name.startswith(".") or not entry.name.endswith(".json"):
            _fail("activation receipts directory contains an unexpected member")
        receipt, _body = _read_receipt(entry, paths=paths)
        if receipt["bundle_id"] == bundle_id:
            continue
        if accepted_at <= _timestamp(
            receipt["accepted_at"], label="retained receipt.accepted_at"
        ):
            _fail("candidate acceptance clock would roll back retained authority")
        if run_id <= receipt["producer_workflow_run_id"]:
            _fail("candidate producer run id would roll back retained authority")


def _create_receipt(
    candidate: Mapping[str, Any],
    *,
    bundle_id: str,
    previous: tuple[dict[str, Any], dict[str, Any]] | None,
    runtime_proof: Mapping[str, Any],
    paths: ActivationPaths,
) -> tuple[dict[str, Any], bytes, Path]:
    previous_digest: str | None = None
    if previous is not None:
        previous_active, _previous_receipt = previous
        previous_path = Path(previous_active["receipt_path"])
        _loaded, previous_body = _read_receipt(previous_path, paths=paths)
        previous_digest = _digest(previous_body)

    activation_id = _activation_id(
        bundle_id=bundle_id,
        release_sha=paths.release_sha,
    )
    receipt_path = paths.receipts_dir / f"{activation_id}.json"
    expected = {
        "activation_id": activation_id,
        "bundle_id": bundle_id,
        "release_sha": paths.release_sha,
        "files": dict(candidate["files"]),
        "producer_repository": candidate["producer_repository"],
        "producer_sha": candidate["producer_sha"],
        "producer_workflow_run_id": candidate["producer_workflow_run_id"],
        "signer_key_id": candidate["signer_key_id"],
        "accepted_at": candidate["accepted_at"],
        "rights_expires_at": candidate["rights_expires_at"],
        "previous_receipt_sha256": previous_digest,
    }
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt, body = _read_receipt(receipt_path, paths=paths)
        if any(receipt[key] != value for key, value in expected.items()):
            _fail("existing activation receipt differs from the candidate")
        return receipt, body, receipt_path

    verified_at = _timestamp(
        runtime_proof.get("verified_at"),
        label="runtime proof.verified_at",
    )
    recorded_at = _now_text()
    if verified_at > _timestamp(recorded_at, label="activation recorded_at"):
        _fail("runtime proof is later than its activation receipt")
    receipt = {
        "schema": ACTIVATION_RECEIPT_SCHEMA,
        **expected,
        "runtime_proof": dict(runtime_proof),
        "recorded_at": recorded_at,
    }
    _validate_receipt(receipt, expected_path=receipt_path)
    body = _canonical(receipt)
    _write_immutable(
        receipt_path,
        body,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o400,
    )
    return receipt, body, receipt_path


def _validate_activation_paths(paths: ActivationPaths) -> None:
    if _GIT_SHA_RE.fullmatch(paths.release_sha) is None:
        _fail("activation release SHA is invalid")
    if not paths.portable:
        fixed = (
            paths.state_root == PRODUCTION_STATE_ROOT
            and paths.env_file == PRODUCTION_ENV_FILE
            and paths.dropin_file == PRODUCTION_DROPIN_FILE
            and paths.deploy_lock == PRODUCTION_DEPLOY_LOCK
            and paths.activation_lock == PRODUCTION_ACTIVATION_LOCK
            and paths.runtime_release
            == PRODUCTION_RUNTIME_ROOT / "releases" / paths.release_sha
            and paths.python == PRODUCTION_PYTHON
            and paths.systemctl == PRODUCTION_SYSTEMCTL
            and paths.runuser == PRODUCTION_RUNUSER
            and paths.env_program == PRODUCTION_ENV
            and paths.api_url == PRODUCTION_API_URL
            and paths.api_user == PRODUCTION_API_USER
            and paths.root_uid == 0
            and paths.root_gid == 0
            and paths.attest_dir is None
        )
        if not fixed:
            _fail("production activation paths or identities changed")
        try:
            account = pwd.getpwnam(PRODUCTION_API_USER)
        except (KeyError, OSError) as exc:
            raise PalimpsestChinaActivationError(
                "production Seiche account is unavailable"
            ) from exc
        if (
            account.pw_uid <= 0
            or account.pw_gid <= 0
            or paths.api_uid != account.pw_uid
            or paths.api_gid != account.pw_gid
        ):
            _fail("production Seiche account identity changed")
    _validate_directory(
        paths.state_root,
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o750,
        label="Palimpsest China state root",
        validate_ancestry=not paths.portable,
    )
    _validate_directory(
        paths.receipts_dir,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o700,
        label="Palimpsest China receipts root",
    )
    _validate_directory(
        paths.env_file.parent,
        uid=paths.root_uid,
        gid=paths.api_gid,
        mode=0o750,
        label="Seiche environment root",
        validate_ancestry=not paths.portable,
    )
    _validate_directory(
        paths.dropin_file.parent,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o755,
        label="Seiche API drop-in root",
        validate_ancestry=not paths.portable,
    )
    _validate_directory(
        paths.deploy_lock.parent,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o700,
        label="Seiche deploy lock root",
        validate_ancestry=not paths.portable,
    )
    if paths.activation_lock.parent != paths.deploy_lock.parent:
        _fail("activation lock must share the protected deploy lock root")
    if not paths.api_url.startswith("http://127.0.0.1:") or paths.api_url.endswith("/"):
        _fail("activation API URL must be a fixed loopback origin")


def _runtime_command(*arguments: str, label: str) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            [
                str(PRODUCTION_GIT),
                "-c",
                f"safe.directory={PRODUCTION_REPOSITORY}",
                *arguments,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
            },
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PalimpsestChinaActivationError(f"{label} could not run") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:1000]
        _fail(f"{label} failed: {detail}")
    return result


def _validate_runtime_release(paths: ActivationPaths) -> None:
    """Bind activation to the exact deployed, pinned-SSH-signed checkout."""

    if paths.portable:
        return
    for executable, label in (
        (paths.python, "activation Python runtime"),
        (paths.runuser, "runuser runtime"),
        (paths.env_program, "empty-environment runtime"),
        (paths.systemctl, "systemd control runtime"),
        (PRODUCTION_GIT, "Git runtime"),
        (PRODUCTION_SSH_KEYGEN, "SSH signature runtime"),
    ):
        if not executable.is_absolute() or not os.access(executable, os.X_OK):
            _fail(f"deployed {label} is unavailable")
        _validate_protected_path(
            executable,
            uid=paths.root_uid,
            label=f"deployed {label}",
        )
        try:
            resolved = executable.resolve(strict=True)
            metadata = resolved.stat()
        except OSError as exc:
            raise PalimpsestChinaActivationError(
                f"deployed {label} cannot be resolved"
            ) from exc
        _validate_protected_path(
            resolved,
            uid=paths.root_uid,
            label=f"resolved {label}",
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != paths.root_uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not stat.S_IMODE(metadata.st_mode) & 0o111
        ):
            _fail(f"deployed {label} metadata is unsafe")
    deployed = _stable_read(
        PRODUCTION_DEPLOYED_SHA,
        label="deployed release marker",
        maximum=64,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o600}),
    )
    if deployed != f"{paths.release_sha}\n".encode("ascii"):
        _fail("deployed release marker changed before activation")
    allowed_signers = _stable_read(
        PRODUCTION_ALLOWED_SIGNERS,
        label="release allowed-signers policy",
        maximum=4096,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o444}),
    )
    try:
        signer_line = allowed_signers.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PalimpsestChinaActivationError(
            "release allowed-signers policy is not ASCII"
        ) from exc
    if (
        not signer_line.endswith("\n")
        or signer_line.count("\n") != 1
        or not signer_line.startswith(f"{PRODUCTION_RELEASE_PRINCIPAL} ssh-ed25519 ")
    ):
        _fail("release allowed-signers policy is not one pinned principal")
    head = _runtime_command(
        "-C",
        str(PRODUCTION_REPOSITORY),
        "rev-parse",
        "HEAD",
        label="deployed release resolution",
    ).stdout
    if head != f"{paths.release_sha}\n".encode("ascii"):
        _fail("application checkout does not match the deployed release marker")
    author = _runtime_command(
        "-C",
        str(PRODUCTION_REPOSITORY),
        "show",
        "-s",
        "--format=%ae",
        paths.release_sha,
        label="deployed release author verification",
    ).stdout
    if author != f"{PRODUCTION_RELEASE_PRINCIPAL}\n".encode("ascii"):
        _fail("deployed release author is not the pinned release principal")
    _runtime_command(
        "-C",
        str(PRODUCTION_REPOSITORY),
        "-c",
        "gpg.format=ssh",
        "-c",
        f"gpg.ssh.allowedSignersFile={PRODUCTION_ALLOWED_SIGNERS}",
        "-c",
        f"gpg.ssh.program={PRODUCTION_SSH_KEYGEN}",
        "verify-commit",
        paths.release_sha,
        label="deployed release signature verification",
    )
    _validate_directory(
        PRODUCTION_RUNTIME_ROOT,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o755,
        label="Palimpsest China trusted runtime root",
        validate_ancestry=True,
    )
    pointer = _stable_read(
        PRODUCTION_RUNTIME_ROOT / "current-sha",
        label="Palimpsest China trusted runtime pointer",
        maximum=64,
        uid=paths.root_uid,
        gid=paths.root_gid,
        modes=frozenset({0o444}),
    )
    if pointer != f"{paths.release_sha}\n".encode("ascii"):
        _fail("Palimpsest China trusted runtime pointer changed")
    releases = PRODUCTION_RUNTIME_ROOT / "releases"
    _validate_directory(
        releases,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o555,
        label="Palimpsest China trusted releases root",
    )
    _validate_directory(
        paths.runtime_release,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o555,
        label="Palimpsest China trusted release",
    )
    package = paths.runtime_release / "seiche"
    _validate_directory(
        package,
        uid=paths.root_uid,
        gid=paths.root_gid,
        mode=0o555,
        label="Palimpsest China trusted package",
    )
    runtime_sources = {
        "__init__.py": "backend/seiche/__init__.py",
        "china_economic_focus.py": "backend/seiche/china_economic_focus.py",
        "nbs_trust.py": "backend/seiche/nbs_trust.py",
        "palimpsest_china_activation.py": (
            "backend/seiche/palimpsest_china_activation.py"
        ),
        "palimpsest_china_intake.py": "backend/seiche/palimpsest_china_intake.py",
    }
    try:
        package_names = {entry.name for entry in package.iterdir()}
    except OSError as exc:
        raise PalimpsestChinaActivationError(
            "Palimpsest China trusted package cannot be listed"
        ) from exc
    if package_names != set(runtime_sources):
        _fail("Palimpsest China trusted package members changed")
    for name, repository_path in runtime_sources.items():
        installed = _stable_read(
            package / name,
            label=f"Palimpsest China trusted module {name}",
            maximum=2 * 1024 * 1024,
            uid=paths.root_uid,
            gid=paths.root_gid,
            modes=frozenset({0o444}),
            minimum=0 if name == "__init__.py" else 1,
        )
        committed = _runtime_command(
            "-C",
            str(PRODUCTION_REPOSITORY),
            "show",
            f"{paths.release_sha}:{repository_path}",
            label=f"Palimpsest China committed module {name}",
        ).stdout
        if installed != committed:
            _fail(f"trusted runtime module {name} differs from the signed release")


def activate_bundle(
    sources: BundleSources,
    *,
    paths: ActivationPaths,
    deploy_lock_descriptor: int | None = None,
) -> dict[str, Any]:
    """Install, verify, switch, prove, or roll back one exact bundle."""

    _validate_activation_paths(paths)

    with _deployment_lock(paths=paths, descriptor=deploy_lock_descriptor):
        _validate_runtime_release(paths)
        with _exclusive_lock(paths.activation_lock, paths=paths, create=True):
            _recover_pending(paths)
            bundle, hashes = _install_bundle(sources.files(), paths=paths)
            candidate = _verify_candidate(bundle, hashes=hashes, paths=paths)
            identifier = _bundle_id(hashes)
            if bundle.name != identifier:
                _fail("bundle identity changed after verification")

            previous = _read_active(paths)
            _assert_config_consistency(previous, paths=paths)
            expected = {
                "files": dict(hashes),
                "signer_key_id": candidate["signer_key_id"],
            }
            current_activation_id = _activation_id(
                bundle_id=identifier,
                release_sha=paths.release_sha,
            )
            if (
                previous is not None
                and previous[0]["activation_id"] == current_activation_id
            ):
                _restart_and_probe(paths=paths, expected=expected)
                return {
                    "schema": ACTIVE_MARKER_SCHEMA,
                    "status": "already_active",
                    "active": previous[0],
                    "receipt": previous[1],
                }
            _receipt_floor(candidate, bundle_id=identifier, paths=paths)

            snapshots = (
                _snapshot(
                    paths.env_file,
                    uid=paths.root_uid,
                    gid=paths.api_gid,
                    mode=0o640,
                    maximum=4096,
                ),
                _snapshot(
                    paths.dropin_file,
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o644,
                    maximum=4096,
                ),
                _snapshot(
                    paths.active_marker,
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o400,
                    maximum=4096,
                ),
            )
            pending = {
                "schema": PENDING_SCHEMA,
                "candidate_activation_id": current_activation_id,
                "candidate_bundle_id": identifier,
                "candidate_files": dict(hashes),
                "previous_activation_id": (
                    previous[0]["activation_id"] if previous is not None else None
                ),
                "started_at": _now_text(),
            }
            _validate_pending(pending)
            _atomic_write(
                paths.pending_marker,
                _canonical(pending),
                uid=paths.root_uid,
                gid=paths.root_gid,
                mode=0o400,
            )
            changed = False
            try:
                _atomic_write(
                    paths.env_file,
                    _render_env(bundle),
                    uid=paths.root_uid,
                    gid=paths.api_gid,
                    mode=0o640,
                )
                changed = True
                _atomic_write(
                    paths.dropin_file,
                    _render_dropin(bundle, env_file=paths.env_file),
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o644,
                )
                runtime_proof = _restart_and_probe(paths=paths, expected=expected)
                if runtime_proof is None:
                    _fail("candidate activation did not produce a REST/MCP proof")
                receipt, receipt_body, receipt_path = _create_receipt(
                    candidate,
                    bundle_id=identifier,
                    previous=previous,
                    runtime_proof=runtime_proof,
                    paths=paths,
                )
                activated_at = _now_text()
                if _timestamp(
                    activated_at,
                    label="active marker.activated_at",
                ) >= _timestamp(
                    receipt["rights_expires_at"],
                    label="activation receipt.rights_expires_at",
                ):
                    _fail("candidate rights expired before activation commit")
                marker = {
                    "schema": ACTIVE_MARKER_SCHEMA,
                    "activation_id": current_activation_id,
                    "bundle_id": identifier,
                    "release_sha": paths.release_sha,
                    "receipt_path": str(receipt_path),
                    "receipt_sha256": _digest(receipt_body),
                    "files": dict(hashes),
                    "activated_at": activated_at,
                }
                _validate_active(marker, paths=paths)
                _atomic_write(
                    paths.active_marker,
                    _canonical(marker),
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o400,
                )
            except BaseException as activation_error:
                if not changed:
                    raise
                rollback_error: BaseException | None = None
                try:
                    for snapshot in snapshots:
                        _restore(snapshot)
                    _restart_and_probe(
                        paths=paths,
                        expected=_active_expected(previous),
                    )
                except BaseException as exc:
                    rollback_error = exc
                if rollback_error is not None:
                    raise PalimpsestChinaActivationError(
                        "activation failed and rollback could not be proven: "
                        f"activation={activation_error}; rollback={rollback_error}"
                    ) from rollback_error
                _remove_controlled_file(
                    paths.pending_marker,
                    label="rolled-back pending activation marker",
                    maximum=8192,
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o400,
                )
                raise PalimpsestChinaActivationError(
                    f"activation failed; prior API configuration restored: {activation_error}"
                ) from activation_error

            try:
                _remove_controlled_file(
                    paths.pending_marker,
                    label="completed pending activation marker",
                    maximum=8192,
                    uid=paths.root_uid,
                    gid=paths.root_gid,
                    mode=0o400,
                )
            except PalimpsestChinaActivationError as exc:
                raise PalimpsestChinaActivationError(
                    "activation committed and proved, but pending-marker cleanup failed; "
                    "the active marker remains authoritative"
                ) from exc

            return {
                "schema": ACTIVE_MARKER_SCHEMA,
                "status": "activated",
                "active": marker,
                "receipt": receipt,
            }


__all__ = [
    "ACTIVATION_RECEIPT_SCHEMA",
    "ACTIVE_MARKER_SCHEMA",
    "ActivationPaths",
    "BundleSources",
    "CANDIDATE_SCHEMA",
    "PENDING_SCHEMA",
    "PRODUCTION_ACTIVATION_LOCK",
    "PRODUCTION_API_URL",
    "PRODUCTION_DEPLOY_LOCK",
    "PRODUCTION_DROPIN_FILE",
    "PRODUCTION_ENV_FILE",
    "PRODUCTION_PYTHON",
    "PRODUCTION_RUNTIME_ROOT",
    "PRODUCTION_STATE_ROOT",
    "RUNTIME_PROOF_SCHEMA",
    "PalimpsestChinaActivationError",
    "activate_bundle",
    "guarded_verify_main",
]
