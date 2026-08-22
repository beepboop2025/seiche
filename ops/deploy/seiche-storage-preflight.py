#!/usr/bin/env python3
"""Fail closed unless Seiche's durable paths are on the pinned volume."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import errno
import grp
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import subprocess
import sys
from typing import NoReturn
import uuid


SCHEMA = "seiche.storage-volume.v2"
DEFAULT_CONFIG_PATH = Path("/etc/seiche/storage-volume.env")
FINDMNT = Path("/usr/bin/findmnt")
_KEYS = {
    "SEICHE_STORAGE_SCHEMA",
    "SEICHE_STORAGE_MOUNT_PATH",
    "SEICHE_STORAGE_EXPECTED_SOURCE",
    "SEICHE_STORAGE_EXPECTED_UUID",
    "SEICHE_STORAGE_EXPECTED_FSTYPE",
    "SEICHE_STORAGE_STATE_PATH",
    "SEICHE_STORAGE_NBS_PATH",
    "SEICHE_STORAGE_BACKUP_PATH",
    "SEICHE_STORAGE_EXPECTED_STATE_FSROOT",
    "SEICHE_STORAGE_EXPECTED_NBS_FSROOT",
    "SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT",
    "SEICHE_STORAGE_MIN_FREE_BLOCKS",
    "SEICHE_STORAGE_MIN_FREE_INODES",
}


class PreflightError(RuntimeError):
    """The storage boundary cannot be proven."""


@dataclass(frozen=True)
class StorageConfig:
    mount_path: Path
    expected_source: Path
    expected_uuid: str
    expected_fstype: str
    state_path: Path
    nbs_path: Path
    backup_path: Path
    expected_state_fsroot: PurePosixPath
    expected_nbs_fsroot: PurePosixPath
    expected_backup_fsroot: PurePosixPath
    min_free_blocks: int
    min_free_inodes: int


@dataclass(frozen=True)
class MountRecord:
    target: Path
    source: str
    fstype: str
    uuid: str
    major_minor: str
    fsroot: PurePosixPath


def _fail(message: str) -> NoReturn:
    raise PreflightError(message)


def _absolute_path(value: str, key: str, *, device: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute() or path == Path("/"):
        _fail(f"{key} must be an absolute non-root path")
    if str(PurePosixPath(value)) != value:
        _fail(f"{key} is not canonical")
    if any(character.isspace() or ord(character) < 32 for character in value):
        _fail(f"{key} contains unsafe whitespace")
    if device and Path("/dev") not in path.parents:
        _fail(f"{key} must name a device below /dev")
    return path


def _positive_integer(value: str, key: str) -> int:
    if not value.isascii() or not value.isdecimal():
        _fail(f"{key} must be a positive integer")
    parsed = int(value)
    if parsed < 1:
        _fail(f"{key} must be a positive integer")
    return parsed


def _filesystem_root(value: str, key: str) -> PurePosixPath:
    root = PurePosixPath(value)
    if not root.is_absolute() or root == PurePosixPath("/") or ".." in root.parts:
        _fail(f"{key} must be an absolute non-root filesystem path")
    if any(character.isspace() or ord(character) < 32 for character in value):
        _fail(f"{key} contains unsafe whitespace")
    if str(root) != value:
        _fail(f"{key} is not canonical")
    return root


def _nested_paths(first: PurePosixPath, second: PurePosixPath) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def _require_distinct_non_nested_fsroots(
    roots: tuple[PurePosixPath, ...],
) -> None:
    for index, first in enumerate(roots):
        for second in roots[index + 1 :]:
            if _nested_paths(first, second):
                _fail(
                    "state, NBS, and backup filesystem roots must be distinct "
                    "and pairwise non-nested"
                )


def parse_config_text(content: str) -> StorageConfig:
    """Parse a closed, non-shell configuration contract."""
    values: dict[str, str] = {}
    for number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            _fail(f"configuration line {number} has no equals sign")
        key, value = line.split("=", 1)
        if key not in _KEYS:
            _fail(f"configuration line {number} has unknown key {key!r}")
        if key in values:
            _fail(f"configuration key {key!r} is duplicated")
        if not value or value != value.strip() or "\x00" in value:
            _fail(f"configuration key {key!r} has an unsafe value")
        values[key] = value

    missing = sorted(_KEYS - values.keys())
    if missing:
        _fail(f"configuration is missing {', '.join(missing)}")
    if values["SEICHE_STORAGE_SCHEMA"] != SCHEMA:
        _fail("configuration schema is unsupported")

    expected_uuid = values["SEICHE_STORAGE_EXPECTED_UUID"]
    try:
        parsed_uuid = uuid.UUID(expected_uuid)
    except ValueError:
        _fail("SEICHE_STORAGE_EXPECTED_UUID is not canonical")
    if str(parsed_uuid) != expected_uuid.lower() or parsed_uuid.int == 0:
        _fail("SEICHE_STORAGE_EXPECTED_UUID is not canonical")
    expected_fstype = values["SEICHE_STORAGE_EXPECTED_FSTYPE"]
    if not expected_fstype.isascii() or any(
        not (character.islower() or character.isdigit() or character in "._+-")
        for character in expected_fstype
    ):
        _fail("SEICHE_STORAGE_EXPECTED_FSTYPE is not canonical")

    config = StorageConfig(
        mount_path=_absolute_path(
            values["SEICHE_STORAGE_MOUNT_PATH"], "SEICHE_STORAGE_MOUNT_PATH"
        ),
        expected_source=_absolute_path(
            values["SEICHE_STORAGE_EXPECTED_SOURCE"],
            "SEICHE_STORAGE_EXPECTED_SOURCE",
            device=True,
        ),
        expected_uuid=expected_uuid,
        expected_fstype=expected_fstype,
        state_path=_absolute_path(
            values["SEICHE_STORAGE_STATE_PATH"], "SEICHE_STORAGE_STATE_PATH"
        ),
        nbs_path=_absolute_path(
            values["SEICHE_STORAGE_NBS_PATH"], "SEICHE_STORAGE_NBS_PATH"
        ),
        backup_path=_absolute_path(
            values["SEICHE_STORAGE_BACKUP_PATH"], "SEICHE_STORAGE_BACKUP_PATH"
        ),
        expected_state_fsroot=_filesystem_root(
            values["SEICHE_STORAGE_EXPECTED_STATE_FSROOT"],
            "SEICHE_STORAGE_EXPECTED_STATE_FSROOT",
        ),
        expected_nbs_fsroot=_filesystem_root(
            values["SEICHE_STORAGE_EXPECTED_NBS_FSROOT"],
            "SEICHE_STORAGE_EXPECTED_NBS_FSROOT",
        ),
        expected_backup_fsroot=_filesystem_root(
            values["SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT"],
            "SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT",
        ),
        min_free_blocks=_positive_integer(
            values["SEICHE_STORAGE_MIN_FREE_BLOCKS"],
            "SEICHE_STORAGE_MIN_FREE_BLOCKS",
        ),
        min_free_inodes=_positive_integer(
            values["SEICHE_STORAGE_MIN_FREE_INODES"],
            "SEICHE_STORAGE_MIN_FREE_INODES",
        ),
    )
    if len({config.state_path, config.nbs_path, config.backup_path}) != 3:
        _fail("state, NBS, and backup paths must be distinct")
    guarded_paths = (config.state_path, config.nbs_path, config.backup_path)
    for index, first in enumerate(guarded_paths):
        for second in guarded_paths[index + 1 :]:
            if _nested_paths(PurePosixPath(first), PurePosixPath(second)):
                _fail("state, NBS, and backup paths must be pairwise non-nested")
    _require_distinct_non_nested_fsroots(
        (
            config.expected_state_fsroot,
            config.expected_nbs_fsroot,
            config.expected_backup_fsroot,
        )
    )
    return config


def load_config(path: Path, *, require_secure_file: bool = True) -> StorageConfig:
    if require_secure_file:
        try:
            metadata = path.lstat()
        except OSError as exc:
            _fail(f"cannot stat configuration: {exc.strerror or exc}")
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or mode not in {0o600, 0o640}
            or metadata.st_nlink != 1
        ):
            _fail(
                "configuration must be root:root, 0600/0640, regular, and single-link"
            )
    try:
        return parse_config_text(path.read_text(encoding="ascii"))
    except UnicodeError:
        _fail("configuration must contain ASCII text")
    except OSError as exc:
        _fail(f"cannot read configuration: {exc.strerror or exc}")


def _mount_for_path(path: Path) -> MountRecord:
    try:
        result = subprocess.run(
            [
                str(FINDMNT),
                "--json",
                "--target",
                str(path),
                "--output",
                "TARGET,SOURCE,FSTYPE,UUID,MAJ:MIN,FSROOT",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        payload = json.loads(result.stdout)
        rows = payload["filesystems"]
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise ValueError("unexpected filesystem count")
        row = rows[0]
        fields = {
            key: row.get(key)
            for key in ("target", "source", "fstype", "maj:min", "fsroot")
        }
        if any(not isinstance(value, str) or not value for value in fields.values()):
            raise ValueError("missing mount identity field")
        mounted_uuid = row.get("uuid")
        if mounted_uuid is None:
            mounted_uuid = ""
        if not isinstance(mounted_uuid, str):
            raise ValueError("invalid mount UUID field")
        return MountRecord(
            target=Path(fields["target"]),
            source=fields["source"],
            fstype=fields["fstype"],
            uuid=mounted_uuid,
            major_minor=fields["maj:min"],
            fsroot=PurePosixPath(fields["fsroot"]),
        )
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        KeyError,
        ValueError,
    ) as exc:
        _fail(f"cannot resolve mount identity for {path}: {exc}")


def _open_directory_nofollow(path: Path, label: str) -> int:
    """Open an absolute directory while rejecting symlinks in every component."""
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.anchor,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        for component in path.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _fail(
            f"{label} is unavailable or has a symlink/non-directory ancestor: "
            f"{exc.strerror or exc}"
        )


def _canonical_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        _fail(f"{label} is unavailable: {exc.strerror or exc}")
    if resolved != path:
        _fail(f"{label} or one of its parents is a symlink")
    descriptor = _open_directory_nofollow(path, label)
    os.close(descriptor)
    return path


def _expected_nbs_owner(*, require_root: bool) -> tuple[int, int]:
    if not require_root:
        return os.geteuid(), os.getegid()
    try:
        seiche_group = grp.getgrnam("seiche")
    except KeyError:
        _fail("the seiche group is unavailable")
    if seiche_group.gr_gid <= 0:
        _fail("the seiche group must have a non-root numeric gid")
    return 0, seiche_group.gr_gid


def _verify_nbs_root_metadata(descriptor: int, *, require_root: bool) -> None:
    metadata = os.fstat(descriptor)
    expected_uid, expected_gid = _expected_nbs_owner(require_root=require_root)
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or mode != 0o750
    ):
        _fail(
            "NBS root must be root:seiche mode 0750 "
            f"(got uid={metadata.st_uid} gid={metadata.st_gid} mode={mode:04o})"
        )


def _descriptor_major_minor(descriptor: int, label: str) -> str:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        _fail(f"cannot stat {label}: {exc.strerror or exc}")
    return f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}"


def _source_major_minor(path: Path) -> str:
    try:
        metadata = path.stat()
    except OSError as exc:
        _fail(f"expected source is unavailable: {exc.strerror or exc}")
    if not stat.S_ISBLK(metadata.st_mode):
        _fail("expected source is not a block device")
    return f"{os.major(metadata.st_rdev)}:{os.minor(metadata.st_rdev)}"


def _probe_write_and_fsync(path: Path, directory: int) -> None:
    probe_name = f".seiche-storage-preflight.{os.getpid()}.{secrets.token_hex(8)}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            probe_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        payload = b"seiche-storage-preflight-v2\n"
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written < 1:
                raise OSError(errno.EIO, "short write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.unlink(probe_name, dir_fd=directory)
        os.fsync(directory)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(probe_name, dir_fd=directory)
        except OSError:
            pass
        _fail(f"write/fsync probe failed for {path}: {exc.strerror or exc}")


def verify_storage(
    config: StorageConfig,
    *,
    expected_state_path: Path | None = None,
    expected_nbs_path: Path | None = None,
    expected_backup_path: Path | None = None,
    require_root: bool = True,
) -> tuple[int, int]:
    """Prove mount identity, capacity, and durable writes for all data paths."""
    if require_root and os.geteuid() != 0:
        _fail("preflight must run as root")
    if expected_state_path is not None and config.state_path != expected_state_path:
        _fail("configured state path does not match the installer state path")
    if expected_nbs_path is not None and config.nbs_path != expected_nbs_path:
        _fail("configured NBS path does not match the installer NBS path")
    if expected_backup_path is not None and config.backup_path != expected_backup_path:
        _fail("configured backup path does not match the installer backup path")

    paths = {
        "configured mountpoint": _canonical_directory(
            config.mount_path, "configured mountpoint"
        ),
        "state path": _canonical_directory(config.state_path, "state path"),
        "NBS path": _canonical_directory(config.nbs_path, "NBS path"),
        "backup path": _canonical_directory(config.backup_path, "backup path"),
    }

    # These descriptors pin the exact directories that are authenticated
    # below. A detach cannot redirect this process's later probes to fallback
    # root-disk directories while the descriptors remain open.
    with ExitStack() as descriptors_lifetime:
        descriptors: dict[str, int] = {}
        identities: dict[str, tuple[int, int]] = {}
        for label, path in paths.items():
            descriptor = _open_directory_nofollow(path, label)
            descriptors_lifetime.callback(os.close, descriptor)
            descriptors[label] = descriptor
            metadata = os.fstat(descriptor)
            identities[label] = (metadata.st_dev, metadata.st_ino)

        if len(set(identities.values())) != len(identities):
            _fail("volume root and guarded paths must resolve to distinct directories")
        _verify_nbs_root_metadata(descriptors["NBS path"], require_root=require_root)

        mount_path = paths["configured mountpoint"]
        mount = _mount_for_path(mount_path)
        if mount.target != mount_path:
            _fail("configured mountpoint is only a directory on another filesystem")
        _canonical_directory(mount.target, "reported mount target")
        if mount.fsroot != PurePosixPath("/"):
            _fail("configured mountpoint is not the filesystem root")
        if mount.fstype != config.expected_fstype:
            _fail(
                "filesystem type mismatch: "
                f"expected {config.expected_fstype}, got {mount.fstype}"
            )
        if mount.uuid.lower() != config.expected_uuid.lower():
            _fail("filesystem UUID mismatch")
        source_major_minor = _source_major_minor(config.expected_source)
        if mount.major_minor != source_major_minor:
            _fail("mounted source does not match the expected block device")
        if (
            _descriptor_major_minor(
                descriptors["configured mountpoint"], "configured mountpoint"
            )
            != source_major_minor
        ):
            _fail("configured mountpoint device identity is inconsistent")

        records: list[tuple[str, Path, PurePosixPath, MountRecord]] = []
        for label, expected_fsroot in (
            ("state path", config.expected_state_fsroot),
            ("NBS path", config.expected_nbs_fsroot),
            ("backup path", config.expected_backup_fsroot),
        ):
            path = paths[label]
            record = _mount_for_path(path)
            if record.target != path:
                _fail(f"{label} is not an exact mountpoint")
            _canonical_directory(record.target, f"reported {label} mount target")
            if (
                record.major_minor != source_major_minor
                or _descriptor_major_minor(descriptors[label], label)
                != source_major_minor
            ):
                _fail(f"{label} is not backed by the expected volume")
            if record.fstype != config.expected_fstype:
                _fail(f"{label} filesystem type is inconsistent")
            if record.fsroot != expected_fsroot:
                _fail(
                    f"{label} filesystem root mismatch: expected {expected_fsroot}, "
                    f"got {record.fsroot}"
                )
            records.append((label, path, expected_fsroot, record))
        _require_distinct_non_nested_fsroots(
            tuple(record.fsroot for _label, _path, _root, record in records)
        )

        try:
            capacity = os.fstatvfs(descriptors["configured mountpoint"])
        except OSError as exc:
            _fail(f"cannot read filesystem capacity: {exc.strerror or exc}")
        free_blocks = int(capacity.f_bavail)
        free_inodes = int(capacity.f_favail)
        if free_blocks < config.min_free_blocks:
            _fail(
                f"free blocks below minimum: {free_blocks} < {config.min_free_blocks}"
            )
        if free_inodes < config.min_free_inodes:
            _fail(
                f"free inodes below minimum: {free_inodes} < {config.min_free_inodes}"
            )

        for label, _expected_fsroot in (
            ("state path", config.expected_state_fsroot),
            ("NBS path", config.expected_nbs_fsroot),
            ("backup path", config.expected_backup_fsroot),
        ):
            path = paths[label]
            _canonical_directory(path, label)
            if label == "NBS path":
                _verify_nbs_root_metadata(descriptors[label], require_root=require_root)
            _probe_write_and_fsync(path, descriptors[label])

        # A retained descriptor makes each probe safe, while this final live
        # check prevents a detached/replaced visible mount from being reported
        # healthy to the consumers that start after the preflight exits.
        if _mount_for_path(mount_path) != mount:
            _fail("configured mountpoint changed during preflight")
        for label, path, _expected_fsroot, record in records:
            if _mount_for_path(path) != record:
                _fail(f"{label} mount identity changed during preflight")
        return free_blocks, free_inodes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--nbs-path", type=Path)
    parser.add_argument("--backup-path", type=Path)
    return parser


def main(arguments: list[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        config = load_config(options.config)
        free_blocks, free_inodes = verify_storage(
            config,
            expected_state_path=options.state_path,
            expected_nbs_path=options.nbs_path,
            expected_backup_path=options.backup_path,
        )
    except PreflightError as exc:
        print(f"seiche storage preflight: {exc}", file=sys.stderr)
        return 1
    print(
        "seiche storage preflight: ok "
        f"mount={config.mount_path} source={config.expected_source} "
        f"uuid={config.expected_uuid} fstype={config.expected_fstype} "
        f"free_blocks={free_blocks} free_inodes={free_inodes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
