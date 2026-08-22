"""Host-free tests for the pinned Hetzner Volume startup boundary."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "deploy" / "seiche-storage-preflight.py"
UNIT = ROOT / "ops" / "deploy" / "seiche-storage-preflight.service"
INSTALLER = ROOT / "ops" / "deploy" / "install-market-platform.sh"

SPEC = importlib.util.spec_from_file_location("seiche_storage_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
storage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = storage
SPEC.loader.exec_module(storage)


def _config_text(
    mount_path: Path,
    state_path: Path,
    nbs_path: Path,
    backup_path: Path,
    **replacements: str,
) -> str:
    values = {
        "SEICHE_STORAGE_SCHEMA": "seiche.storage-volume.v2",
        "SEICHE_STORAGE_MOUNT_PATH": str(mount_path),
        "SEICHE_STORAGE_EXPECTED_SOURCE": "/dev/disk/by-id/scsi-0HC_Volume_test",
        "SEICHE_STORAGE_EXPECTED_UUID": "11111111-2222-4333-8444-555555555555",
        "SEICHE_STORAGE_EXPECTED_FSTYPE": "ext4",
        "SEICHE_STORAGE_STATE_PATH": str(state_path),
        "SEICHE_STORAGE_NBS_PATH": str(nbs_path),
        "SEICHE_STORAGE_BACKUP_PATH": str(backup_path),
        "SEICHE_STORAGE_EXPECTED_STATE_FSROOT": "/state",
        "SEICHE_STORAGE_EXPECTED_NBS_FSROOT": "/nbs",
        "SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT": "/backups",
        "SEICHE_STORAGE_MIN_FREE_BLOCKS": "1",
        "SEICHE_STORAGE_MIN_FREE_INODES": "1",
    }
    values.update(replacements)
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _fixture(tmp_path: Path) -> tuple[object, Path, Path, Path, Path, str]:
    mount_path = tmp_path / "volume"
    state_path = tmp_path / "state"
    nbs_path = tmp_path / "nbs"
    backup_path = tmp_path / "backup"
    for path in (mount_path, state_path, nbs_path, backup_path):
        path.mkdir()
    nbs_path.chmod(0o750)
    config = storage.parse_config_text(
        _config_text(mount_path, state_path, nbs_path, backup_path)
    )
    metadata = mount_path.stat()
    major_minor = f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}"
    return config, mount_path, state_path, nbs_path, backup_path, major_minor


def _mount_lookup(config: object, major_minor: str):
    def lookup(path: Path):
        fsroot = "/"
        if path == config.state_path:
            fsroot = str(config.expected_state_fsroot)
        elif path == config.nbs_path:
            fsroot = str(config.expected_nbs_fsroot)
        elif path == config.backup_path:
            fsroot = str(config.expected_backup_fsroot)
        return storage.MountRecord(
            target=config.mount_path if path == config.mount_path else path,
            source="/dev/test",
            fstype="ext4",
            uuid="11111111-2222-4333-8444-555555555555",
            major_minor=major_minor,
            fsroot=storage.PurePosixPath(fsroot),
        )

    return lookup


def test_closed_config_contract_parses_without_shell_evaluation(tmp_path: Path) -> None:
    mount_path = tmp_path / "volume"
    state_path = tmp_path / "state"
    nbs_path = tmp_path / "nbs"
    backup_path = tmp_path / "backup"

    config = storage.parse_config_text(
        "# pinned by the cutover receipt\n"
        + _config_text(mount_path, state_path, nbs_path, backup_path)
    )

    assert config.mount_path == mount_path
    assert config.nbs_path == nbs_path
    assert config.expected_fstype == "ext4"
    assert config.min_free_blocks == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SEICHE_STORAGE_EXPECTED_NBS_FSROOT", "/state"),
        ("SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT", "/nbs/snapshots"),
        ("SEICHE_STORAGE_EXPECTED_STATE_FSROOT", "/backups/state"),
    ],
)
def test_config_rejects_aliased_or_pairwise_nested_filesystem_roots(
    tmp_path: Path, key: str, value: str
) -> None:
    content = _config_text(
        tmp_path / "volume",
        tmp_path / "state",
        tmp_path / "nbs",
        tmp_path / "backup",
        **{key: value},
    )

    with pytest.raises(storage.PreflightError, match="pairwise non-nested"):
        storage.parse_config_text(content)


def test_config_rejects_v1_or_missing_nbs_contract(tmp_path: Path) -> None:
    content = _config_text(
        tmp_path / "volume",
        tmp_path / "state",
        tmp_path / "nbs",
        tmp_path / "backup",
    )

    with pytest.raises(storage.PreflightError, match="schema is unsupported"):
        storage.parse_config_text(
            content.replace("seiche.storage-volume.v2", "seiche.storage-volume.v1", 1)
        )

    without_nbs = "\n".join(
        line
        for line in content.splitlines()
        if not line.startswith("SEICHE_STORAGE_NBS_PATH=")
        and not line.startswith("SEICHE_STORAGE_EXPECTED_NBS_FSROOT=")
    )
    with pytest.raises(storage.PreflightError, match="configuration is missing"):
        storage.parse_config_text(without_nbs + "\n")


def test_config_rejects_aliased_guarded_paths(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    content = _config_text(
        tmp_path / "volume",
        shared,
        shared,
        tmp_path / "backup",
    )

    with pytest.raises(storage.PreflightError, match="paths must be distinct"):
        storage.parse_config_text(content)


@pytest.mark.parametrize(
    ("state_suffix", "nbs_suffix", "backup_suffix"),
    [
        ("state", "state/nbs", "backup"),
        ("state/child", "nbs", "state"),
        ("state", "backup", "backup/nbs"),
    ],
)
def test_config_rejects_pairwise_nested_guarded_paths(
    tmp_path: Path,
    state_suffix: str,
    nbs_suffix: str,
    backup_suffix: str,
) -> None:
    content = _config_text(
        tmp_path / "volume",
        tmp_path / state_suffix,
        tmp_path / nbs_suffix,
        tmp_path / backup_suffix,
    )

    with pytest.raises(
        storage.PreflightError, match="paths must be pairwise non-nested"
    ):
        storage.parse_config_text(content)


@pytest.mark.parametrize(
    "mutation",
    [
        "UNKNOWN=value\n",
        "SEICHE_STORAGE_SCHEMA=seiche.storage-volume.v2\n",
        "SEICHE_STORAGE_MIN_FREE_BLOCKS=0\n",
        "SEICHE_STORAGE_EXPECTED_UUID=00000000-0000-0000-0000-000000000000\n",
    ],
)
def test_config_rejects_unknown_duplicate_or_non_fail_closed_values(
    tmp_path: Path, mutation: str
) -> None:
    content = _config_text(
        tmp_path / "volume",
        tmp_path / "state",
        tmp_path / "nbs",
        tmp_path / "backup",
    )
    key = mutation.split("=", 1)[0]
    if key in content:
        content = (
            "\n".join(
                line for line in content.splitlines() if not line.startswith(f"{key}=")
            )
            + "\n"
        )
    if key == "SEICHE_STORAGE_SCHEMA":
        content += mutation
        content += mutation
    else:
        content += mutation

    with pytest.raises(storage.PreflightError):
        storage.parse_config_text(content)


def test_success_proves_identity_capacity_and_all_three_durable_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, mount_path, state_path, nbs_path, backup_path, major_minor = _fixture(
        tmp_path
    )
    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(config, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    free_blocks, free_inodes = storage.verify_storage(config, require_root=False)

    assert free_blocks >= 1
    assert free_inodes >= 1
    assert not list(state_path.glob(".seiche-storage-preflight.*"))
    assert not list(nbs_path.glob(".seiche-storage-preflight.*"))
    assert not list(backup_path.glob(".seiche-storage-preflight.*"))
    assert mount_path.is_dir()


def test_plain_directory_on_root_filesystem_is_not_a_mountpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    lookup = _mount_lookup(config, major_minor)

    def inherited_mount(path: Path):
        record = lookup(path)
        if path == config.mount_path:
            return storage.MountRecord(
                target=tmp_path,
                source=record.source,
                fstype=record.fstype,
                uuid=record.uuid,
                major_minor=record.major_minor,
                fsroot=record.fsroot,
            )
        return record

    monkeypatch.setattr(storage, "_mount_for_path", inherited_mount)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(storage.PreflightError, match="only a directory"):
        storage.verify_storage(config, require_root=False)


def test_configured_mountpoint_must_be_volume_filesystem_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    correct = _mount_lookup(config, major_minor)

    def bind_as_primary(path: Path):
        record = correct(path)
        if path == config.mount_path:
            return storage.MountRecord(
                target=record.target,
                source=record.source,
                fstype=record.fstype,
                uuid=record.uuid,
                major_minor=record.major_minor,
                fsroot=storage.PurePosixPath("/state"),
            )
        return record

    monkeypatch.setattr(storage, "_mount_for_path", bind_as_primary)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(storage.PreflightError, match="not the filesystem root"):
        storage.verify_storage(config, require_root=False)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("uuid", "UUID mismatch"),
        ("fstype", "filesystem type mismatch"),
        ("major_minor", "expected block device"),
    ],
)
def test_expected_volume_identity_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    message: str,
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    correct = _mount_lookup(config, major_minor)

    def mismatch(path: Path):
        record = correct(path)
        if path != config.mount_path:
            return record
        values = {
            "target": record.target,
            "source": record.source,
            "fstype": record.fstype,
            "uuid": record.uuid,
            "major_minor": record.major_minor,
            "fsroot": record.fsroot,
        }
        values[field] = "wrong" if field != "major_minor" else "8:99"
        return storage.MountRecord(**values)

    monkeypatch.setattr(storage, "_mount_for_path", mismatch)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(storage.PreflightError, match=message):
        storage.verify_storage(config, require_root=False)


@pytest.mark.parametrize(
    ("path_attribute", "label"),
    [
        ("state_path", "state"),
        ("nbs_path", "NBS"),
        ("backup_path", "backup"),
    ],
)
def test_each_guarded_path_on_another_device_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_attribute: str,
    label: str,
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    correct = _mount_lookup(config, major_minor)

    def split_mount(path: Path):
        record = correct(path)
        if path == getattr(config, path_attribute):
            return storage.MountRecord(
                target=record.target,
                source="/dev/root",
                fstype=record.fstype,
                uuid=record.uuid,
                major_minor="8:99",
                fsroot=record.fsroot,
            )
        return record

    monkeypatch.setattr(storage, "_mount_for_path", split_mount)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(storage.PreflightError, match=f"{label} path is not backed"):
        storage.verify_storage(config, require_root=False)


@pytest.mark.parametrize(
    ("path_attribute", "label"),
    [
        ("state_path", "state"),
        ("nbs_path", "NBS"),
        ("backup_path", "backup"),
    ],
)
def test_each_bind_path_must_be_an_exact_mountpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_attribute: str,
    label: str,
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    correct = _mount_lookup(config, major_minor)

    def inherited_mount(path: Path):
        record = correct(path)
        if path == getattr(config, path_attribute):
            return storage.MountRecord(
                target=config.mount_path,
                source=record.source,
                fstype=record.fstype,
                uuid=record.uuid,
                major_minor=record.major_minor,
                fsroot=record.fsroot,
            )
        return record

    monkeypatch.setattr(storage, "_mount_for_path", inherited_mount)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(storage.PreflightError, match=f"{label} path is not an exact"):
        storage.verify_storage(config, require_root=False)


@pytest.mark.parametrize(
    ("path_attribute", "label"),
    [
        ("state_path", "state"),
        ("nbs_path", "NBS"),
        ("backup_path", "backup"),
    ],
)
def test_each_bind_path_filesystem_identity_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_attribute: str,
    label: str,
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    correct = _mount_lookup(config, major_minor)

    def wrong_root(path: Path):
        record = correct(path)
        if path == getattr(config, path_attribute):
            return storage.MountRecord(
                target=record.target,
                source=record.source,
                fstype=record.fstype,
                uuid=record.uuid,
                major_minor=record.major_minor,
                fsroot=storage.PurePosixPath("/wrong-root"),
            )
        return record

    monkeypatch.setattr(storage, "_mount_for_path", wrong_root)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(
        storage.PreflightError, match=f"{label} path filesystem root mismatch"
    ):
        storage.verify_storage(config, require_root=False)


def test_nbs_bind_filesystem_type_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    correct = _mount_lookup(config, major_minor)

    def wrong_fstype(path: Path):
        record = correct(path)
        if path != config.nbs_path:
            return record
        return storage.MountRecord(
            target=record.target,
            source=record.source,
            fstype="xfs",
            uuid=record.uuid,
            major_minor=record.major_minor,
            fsroot=record.fsroot,
        )

    monkeypatch.setattr(storage, "_mount_for_path", wrong_fstype)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(
        storage.PreflightError, match="NBS path filesystem type is inconsistent"
    ):
        storage.verify_storage(config, require_root=False)


def test_live_bind_roots_are_rechecked_for_pairwise_nesting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    nested = replace(
        config,
        expected_nbs_fsroot=storage.PurePosixPath("/state/nbs"),
    )
    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(nested, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(storage.PreflightError, match="pairwise non-nested"):
        storage.verify_storage(nested, require_root=False)


def test_bind_paths_do_not_need_to_repeat_primary_mount_uuid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    correct = _mount_lookup(config, major_minor)

    def bind_mount(path: Path):
        record = correct(path)
        if path != config.mount_path:
            return storage.MountRecord(
                target=record.target,
                source=record.source,
                fstype=record.fstype,
                uuid="",
                major_minor=record.major_minor,
                fsroot=record.fsroot,
            )
        return record

    monkeypatch.setattr(storage, "_mount_for_path", bind_mount)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    storage.verify_storage(config, require_root=False)


@pytest.mark.parametrize("symlink_is_ancestor", [False, True])
def test_guarded_paths_reject_final_or_ancestor_symlinks(
    tmp_path: Path, symlink_is_ancestor: bool
) -> None:
    mount_path = tmp_path / "volume"
    state_path = tmp_path / "state"
    backup_path = tmp_path / "backup"
    real_parent = tmp_path / "real-nbs-parent"
    real_nbs = real_parent / "nbs"
    for path in (mount_path, state_path, backup_path, real_nbs):
        path.mkdir(parents=True)

    if symlink_is_ancestor:
        nbs_parent = tmp_path / "nbs-parent-link"
        nbs_parent.symlink_to(real_parent, target_is_directory=True)
        nbs_path = nbs_parent / "nbs"
    else:
        nbs_path = tmp_path / "nbs-link"
        nbs_path.symlink_to(real_nbs, target_is_directory=True)

    config = storage.parse_config_text(
        _config_text(mount_path, state_path, nbs_path, backup_path)
    )

    with pytest.raises(storage.PreflightError, match="symlink"):
        storage.verify_storage(config, require_root=False)
    assert not list(state_path.iterdir())
    assert not list(real_nbs.iterdir())
    assert not list(backup_path.iterdir())


def test_guarded_path_rejects_a_regular_file(tmp_path: Path) -> None:
    mount_path = tmp_path / "volume"
    state_path = tmp_path / "state"
    nbs_path = tmp_path / "nbs-file"
    backup_path = tmp_path / "backup"
    for path in (mount_path, state_path, backup_path):
        path.mkdir()
    nbs_path.write_text("not a mountpoint\n")
    config = storage.parse_config_text(
        _config_text(mount_path, state_path, nbs_path, backup_path)
    )

    with pytest.raises(storage.PreflightError, match="non-directory ancestor"):
        storage.verify_storage(config, require_root=False)
    assert nbs_path.read_text() == "not a mountpoint\n"


@pytest.mark.parametrize(
    ("uid_offset", "gid_offset"),
    [(1, 0), (0, 1)],
)
def test_nbs_root_rejects_wrong_uid_or_gid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    uid_offset: int,
    gid_offset: int,
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, _major_minor = _fixture(
        tmp_path
    )
    monkeypatch.setattr(
        storage,
        "_expected_nbs_owner",
        lambda *, require_root: (
            os.geteuid() + uid_offset,
            os.getegid() + gid_offset,
        ),
    )

    with pytest.raises(storage.PreflightError, match="root:seiche mode 0750"):
        storage.verify_storage(config, require_root=False)


def test_nbs_root_rejects_wrong_mode(
    tmp_path: Path,
) -> None:
    config, _mount_path, _state_path, nbs_path, _backup_path, _major_minor = _fixture(
        tmp_path
    )
    nbs_path.chmod(0o770)

    with pytest.raises(storage.PreflightError, match="mode=0770"):
        storage.verify_storage(config, require_root=False)


def test_production_nbs_owner_uses_the_named_non_root_seiche_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def group_lookup(name: str) -> SimpleNamespace:
        observed.append(name)
        return SimpleNamespace(gr_gid=1234)

    monkeypatch.setattr(storage.grp, "getgrnam", group_lookup)

    assert storage._expected_nbs_owner(require_root=True) == (0, 1234)
    assert observed == ["seiche"]


@pytest.mark.parametrize("group", [None, SimpleNamespace(gr_gid=0)])
def test_production_nbs_owner_rejects_missing_or_root_group(
    monkeypatch: pytest.MonkeyPatch, group: SimpleNamespace | None
) -> None:
    def group_lookup(_name: str) -> SimpleNamespace:
        if group is None:
            raise KeyError("seiche")
        return group

    monkeypatch.setattr(storage.grp, "getgrnam", group_lookup)

    with pytest.raises(storage.PreflightError, match="seiche group"):
        storage._expected_nbs_owner(require_root=True)


def test_expected_nbs_path_is_a_cli_and_verification_contract(
    tmp_path: Path,
) -> None:
    config, _mount_path, _state_path, _nbs_path, _backup_path, _major_minor = _fixture(
        tmp_path
    )
    expected = tmp_path / "operator-nbs"

    options = storage._parser().parse_args(["--nbs-path", str(expected)])
    assert options.nbs_path == expected
    with pytest.raises(storage.PreflightError, match="installer NBS path"):
        storage.verify_storage(
            config,
            expected_nbs_path=expected,
            require_root=False,
        )


def test_findmnt_parser_requires_and_preserves_filesystem_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (
        '{"filesystems":[{"target":"/var/lib/seiche",'
        '"source":"/dev/sdb[/state]","fstype":"ext4","uuid":null,'
        '"maj:min":"8:16","fsroot":"/state"}]}'
    )
    monkeypatch.setattr(
        storage.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=payload),
    )

    record = storage._mount_for_path(Path("/var/lib/seiche"))

    assert record.uuid == ""
    assert record.fsroot == storage.PurePosixPath("/state")


@pytest.mark.parametrize(
    ("free_blocks", "free_inodes", "message"),
    [(0, 10, "free blocks"), (10, 0, "free inodes")],
)
def test_capacity_thresholds_fail_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    free_blocks: int,
    free_inodes: int,
    message: str,
) -> None:
    config, _mount_path, state_path, nbs_path, backup_path, major_minor = _fixture(
        tmp_path
    )
    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(config, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)
    monkeypatch.setattr(
        storage.os,
        "fstatvfs",
        lambda _descriptor: SimpleNamespace(f_bavail=free_blocks, f_favail=free_inodes),
    )

    with pytest.raises(storage.PreflightError, match=message):
        storage.verify_storage(config, require_root=False)
    assert not list(state_path.iterdir())
    assert not list(nbs_path.iterdir())
    assert not list(backup_path.iterdir())


def test_capacity_gate_precedes_durable_probes_through_all_three_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, mount_path, state_path, nbs_path, backup_path, major_minor = _fixture(
        tmp_path
    )
    events: list[object] = []
    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(config, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    mount_identity = (mount_path.stat().st_dev, mount_path.stat().st_ino)
    guarded_identities = {
        path: (path.stat().st_dev, path.stat().st_ino)
        for path in (state_path, nbs_path, backup_path)
    }

    def capacity(descriptor: int) -> SimpleNamespace:
        metadata = os.fstat(descriptor)
        assert (metadata.st_dev, metadata.st_ino) == mount_identity
        events.append(("capacity", mount_path))
        return SimpleNamespace(f_bavail=10, f_favail=10)

    def probe(path: Path, descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        assert (metadata.st_dev, metadata.st_ino) == guarded_identities[path]
        events.append(("probe", path))

    monkeypatch.setattr(storage.os, "fstatvfs", capacity)
    monkeypatch.setattr(storage, "_probe_write_and_fsync", probe)

    storage.verify_storage(config, require_root=False)

    assert events == [
        ("capacity", mount_path),
        ("probe", state_path),
        ("probe", nbs_path),
        ("probe", backup_path),
    ]


def test_durable_probe_retains_authenticated_nbs_descriptor_if_path_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, state_path, nbs_path, _backup_path, major_minor = _fixture(
        tmp_path
    )
    original_identity = (nbs_path.stat().st_dev, nbs_path.stat().st_ino)
    detached_nbs = tmp_path / "detached-nbs"
    fallback_nbs = nbs_path
    original_probe = storage._probe_write_and_fsync
    observed_nbs_identity: tuple[int, int] | None = None

    def redirect_before_nbs_probe(path: Path, descriptor: int) -> None:
        nonlocal observed_nbs_identity
        if path == state_path:
            nbs_path.rename(detached_nbs)
            fallback_nbs.mkdir()
            fallback_nbs.chmod(0o750)
        if path == fallback_nbs:
            metadata = os.fstat(descriptor)
            observed_nbs_identity = (metadata.st_dev, metadata.st_ino)
        original_probe(path, descriptor)

    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(config, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)
    monkeypatch.setattr(storage, "_probe_write_and_fsync", redirect_before_nbs_probe)

    storage.verify_storage(config, require_root=False)

    assert observed_nbs_identity == original_identity
    assert not list(detached_nbs.iterdir())
    assert not list(fallback_nbs.iterdir())


def test_fsync_failure_is_cleaned_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, state_path, nbs_path, backup_path, major_minor = _fixture(
        tmp_path
    )
    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(config, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(storage.os, "fsync", fail_fsync)

    with pytest.raises(storage.PreflightError, match="write/fsync probe failed"):
        storage.verify_storage(config, require_root=False)
    assert not list(state_path.glob(".seiche-storage-preflight.*"))
    assert not list(nbs_path.glob(".seiche-storage-preflight.*"))
    assert not list(backup_path.glob(".seiche-storage-preflight.*"))


def test_every_durable_data_consumer_is_mount_guarded() -> None:
    guarded_units = [
        "seiche-market-worker.service",
        "seiche-source-worker.service",
        "seiche-market-backfill.service",
        "seiche-market-validation.service",
        "seiche-market-backup.service",
        "seiche-market-restore-check.service",
        "seiche-data-readiness.service",
        "seiche-release-poll.service",
    ]

    for name in guarded_units:
        unit = (ROOT / "ops" / "deploy" / name).read_text()
        assert "Requires=seiche-storage-preflight.service" in unit, name
        assert "After=" in unit and "seiche-storage-preflight.service" in unit, name
        assert (
            "RequiresMountsFor=/var/lib/seiche /var/lib/seiche-nbs "
            "/var/backups/seiche-market"
        ) in unit, name

    preflight = UNIT.read_text()
    assert "Type=oneshot" in preflight
    assert "RemainAfterExit" not in preflight
    assert "OnFailure=undertow-failure-alert@%n.service" in preflight
    assert "PrivateDevices=true" not in preflight
    assert (
        "ExecStart=/usr/bin/python3 -I -B "
        "/etc/seiche/libexec/seiche-storage-preflight.py "
        "--config /etc/seiche/storage-volume.env "
        "--state-path /var/lib/seiche "
        "--nbs-path /var/lib/seiche-nbs "
        "--backup-path /var/backups/seiche-market"
    ) in preflight
    assert (
        "ReadWritePaths=/var/lib/seiche /var/lib/seiche-nbs /var/backups/seiche-market"
    ) in preflight


def test_storage_preflight_python_isolation_ignores_hostile_pythonpath(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "sitecustomize-executed"
    (tmp_path / "sitecustomize.py").write_text(
        f"open({str(marker)!r}, 'w').write('executed')\n"
    )
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", "pass"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_installer_preflights_before_creating_data_paths_and_guards_api() -> None:
    installer = INSTALLER.read_text()
    helper_install = installer.index(
        'mv -f "$STORAGE_PREFLIGHT_STAGE" "$STORAGE_PREFLIGHT_INSTALLED"'
    )
    unit_install = installer.index('"$STORAGE_PREFLIGHT_UNIT_DESTINATION"')
    daemon_reload = installer.index("systemctl daemon-reload", unit_install)
    preflight = installer.index("systemctl start seiche-storage-preflight.service")
    postgres_start = installer.index("systemctl enable --now postgresql")
    state_creation = installer.index("install -d -o seiche -g seiche -m 0750")
    worker_install = installer.index("WORKER_UNIT_STAGE_DIR=$(mktemp")

    assert helper_install < unit_install < daemon_reload < preflight
    assert preflight < postgres_start < state_creation
    assert unit_install < worker_install
    assert '/usr/bin/sync "$STORAGE_PREFLIGHT_INSTALL_DIR"' in installer
    assert '/usr/bin/python3 "$STORAGE_PREFLIGHT_SOURCE" \\' not in installer
    api_dropin = installer[installer.index('cat >"$DROPIN" <<EOF') :]
    assert "Requires=seiche-storage-preflight.service" in api_dropin
    assert "After=seiche-storage-preflight.service" in api_dropin
    assert "RequiresMountsFor=$STATE_DIR $NBS_STATE_DIR $BACKUP_DIR" in api_dropin
    assert "combined candidate systemd graph failed verification" in installer

    release_poller = (
        ROOT / "ops" / "deploy" / "seiche-release-poll.service"
    ).read_text()
    assert "PrivateDevices=true" in release_poller


def test_deploy_rollback_captures_storage_artifacts_and_timer_state() -> None:
    wrapper = (ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh").read_text()
    manifest = wrapper[
        wrapper.index("DATA_UNIT_NAMES=(") : wrapper.index('DATA_UNIT_ROLLBACK_DIR=""')
    ]

    for name in (
        "seiche-storage-preflight.service",
        "seiche-market-validation.timer",
        "seiche-market-backup.timer",
        "seiche-market-restore-check.timer",
        "/etc/seiche/libexec/seiche-storage-preflight.py",
        "/etc/seiche/libexec/seiche-nbs-intake.py",
        "/etc/seiche/libexec/seiche-market-backup.sh",
        "/etc/seiche/libexec/seiche-market-restore-check.sh",
        "/etc/systemd/system/seiche-api.service.d/market-platform.conf",
        "/etc/systemd/system/seiche-release-poll.service.d/storage-volume.conf",
        "/etc/systemd/system/seiche-market-validation.service.d/state-path.conf",
        "/etc/systemd/system/seiche-market-backup.service.d/paths.conf",
        "/etc/systemd/system/seiche-market-restore-check.service.d/paths.conf",
        "/opt/seiche-nbs-intake/current-sha",
    ):
        assert name in manifest

    for timer in ("VALIDATION", "BACKUP", "RESTORE"):
        assert f"{timer}_TIMER_WAS_ACTIVE" in wrapper
        assert f"{timer}_TIMER_WAS_ENABLED" in wrapper

    restore_start = wrapper.index("restore_preupdate_data_units() {")
    restore = wrapper[
        restore_start : wrapper.index("restore_market_services() {", restore_start)
    ]
    for unit in (
        "seiche-market-validation.timer",
        "seiche-market-backup.timer",
        "seiche-market-restore-check.timer",
    ):
        assert f"systemctl enable {unit}" in restore
        assert f"systemctl disable {unit}" in restore
        assert f"systemctl is-enabled --quiet {unit}" in restore
