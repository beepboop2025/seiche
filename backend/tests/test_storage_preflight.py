"""Host-free tests for the pinned Hetzner Volume startup boundary."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
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
    backup_path: Path,
    **replacements: str,
) -> str:
    values = {
        "SEICHE_STORAGE_SCHEMA": "seiche.storage-volume.v1",
        "SEICHE_STORAGE_MOUNT_PATH": str(mount_path),
        "SEICHE_STORAGE_EXPECTED_SOURCE": "/dev/disk/by-id/scsi-0HC_Volume_test",
        "SEICHE_STORAGE_EXPECTED_UUID": "11111111-2222-4333-8444-555555555555",
        "SEICHE_STORAGE_EXPECTED_FSTYPE": "ext4",
        "SEICHE_STORAGE_STATE_PATH": str(state_path),
        "SEICHE_STORAGE_BACKUP_PATH": str(backup_path),
        "SEICHE_STORAGE_EXPECTED_STATE_FSROOT": "/state",
        "SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT": "/backups",
        "SEICHE_STORAGE_MIN_FREE_BLOCKS": "1",
        "SEICHE_STORAGE_MIN_FREE_INODES": "1",
    }
    values.update(replacements)
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _fixture(tmp_path: Path) -> tuple[object, Path, Path, Path, str]:
    mount_path = tmp_path / "volume"
    state_path = tmp_path / "state"
    backup_path = tmp_path / "backup"
    for path in (mount_path, state_path, backup_path):
        path.mkdir()
    config = storage.parse_config_text(
        _config_text(mount_path, state_path, backup_path)
    )
    metadata = mount_path.stat()
    major_minor = f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}"
    return config, mount_path, state_path, backup_path, major_minor


def _mount_lookup(config: object, major_minor: str):
    def lookup(path: Path):
        fsroot = "/"
        if path == config.state_path:
            fsroot = str(config.expected_state_fsroot)
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
    backup_path = tmp_path / "backup"

    config = storage.parse_config_text(
        "# pinned by the cutover receipt\n"
        + _config_text(mount_path, state_path, backup_path)
    )

    assert config.mount_path == mount_path
    assert config.expected_fstype == "ext4"
    assert config.min_free_blocks == 1


def test_config_rejects_aliased_or_nested_filesystem_roots(tmp_path: Path) -> None:
    content = _config_text(
        tmp_path / "volume",
        tmp_path / "state",
        tmp_path / "backup",
        SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT="/state/backups",
    )

    with pytest.raises(storage.PreflightError, match="distinct and non-nested"):
        storage.parse_config_text(content)


@pytest.mark.parametrize(
    "mutation",
    [
        "UNKNOWN=value\n",
        "SEICHE_STORAGE_SCHEMA=seiche.storage-volume.v1\n",
        "SEICHE_STORAGE_MIN_FREE_BLOCKS=0\n",
        "SEICHE_STORAGE_EXPECTED_UUID=00000000-0000-0000-0000-000000000000\n",
    ],
)
def test_config_rejects_unknown_duplicate_or_non_fail_closed_values(
    tmp_path: Path, mutation: str
) -> None:
    content = _config_text(tmp_path / "volume", tmp_path / "state", tmp_path / "backup")
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


def test_success_proves_identity_capacity_and_both_durable_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, mount_path, state_path, backup_path, major_minor = _fixture(tmp_path)
    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(config, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    free_blocks, free_inodes = storage.verify_storage(config, require_root=False)

    assert free_blocks >= 1
    assert free_inodes >= 1
    assert not list(state_path.glob(".seiche-storage-preflight.*"))
    assert not list(backup_path.glob(".seiche-storage-preflight.*"))
    assert mount_path.is_dir()


def test_plain_directory_on_root_filesystem_is_not_a_mountpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _backup_path, major_minor = _fixture(tmp_path)
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
    config, _mount_path, _state_path, _backup_path, major_minor = _fixture(tmp_path)
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
    config, _mount_path, _state_path, _backup_path, major_minor = _fixture(tmp_path)
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


def test_state_or_backup_on_another_device_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _backup_path, major_minor = _fixture(tmp_path)
    correct = _mount_lookup(config, major_minor)

    def split_mount(path: Path):
        record = correct(path)
        if path == config.backup_path:
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

    with pytest.raises(storage.PreflightError, match="backup path is not backed"):
        storage.verify_storage(config, require_root=False)


def test_bind_path_must_be_an_exact_mountpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _backup_path, major_minor = _fixture(tmp_path)
    correct = _mount_lookup(config, major_minor)

    def inherited_mount(path: Path):
        record = correct(path)
        if path == config.state_path:
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

    with pytest.raises(storage.PreflightError, match="state path is not an exact"):
        storage.verify_storage(config, require_root=False)


def test_bind_path_filesystem_root_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _backup_path, major_minor = _fixture(tmp_path)
    correct = _mount_lookup(config, major_minor)

    def wrong_root(path: Path):
        record = correct(path)
        if path == config.backup_path:
            return storage.MountRecord(
                target=record.target,
                source=record.source,
                fstype=record.fstype,
                uuid=record.uuid,
                major_minor=record.major_minor,
                fsroot=storage.PurePosixPath("/state"),
            )
        return record

    monkeypatch.setattr(storage, "_mount_for_path", wrong_root)
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    with pytest.raises(storage.PreflightError, match="filesystem root mismatch"):
        storage.verify_storage(config, require_root=False)


def test_bind_paths_do_not_need_to_repeat_primary_mount_uuid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, _state_path, _backup_path, major_minor = _fixture(tmp_path)
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
    config, _mount_path, state_path, backup_path, major_minor = _fixture(tmp_path)
    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(config, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)
    monkeypatch.setattr(
        storage.os,
        "statvfs",
        lambda _path: SimpleNamespace(f_bavail=free_blocks, f_favail=free_inodes),
    )

    with pytest.raises(storage.PreflightError, match=message):
        storage.verify_storage(config, require_root=False)
    assert not list(state_path.iterdir())
    assert not list(backup_path.iterdir())


def test_fsync_failure_is_cleaned_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _mount_path, state_path, backup_path, major_minor = _fixture(tmp_path)
    monkeypatch.setattr(storage, "_mount_for_path", _mount_lookup(config, major_minor))
    monkeypatch.setattr(storage, "_source_major_minor", lambda _path: major_minor)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(storage.os, "fsync", fail_fsync)

    with pytest.raises(storage.PreflightError, match="write/fsync probe failed"):
        storage.verify_storage(config, require_root=False)
    assert not list(state_path.glob(".seiche-storage-preflight.*"))
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
        assert "RequiresMountsFor=/var/lib/seiche /var/backups/seiche-market" in unit, (
            name
        )

    preflight = UNIT.read_text()
    assert "Type=oneshot" in preflight
    assert "RemainAfterExit" not in preflight
    assert "OnFailure=undertow-failure-alert@%n.service" in preflight
    assert "PrivateDevices=true" not in preflight
    assert "ReadWritePaths=/var/lib/seiche /var/backups/seiche-market" in preflight


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
    assert "RequiresMountsFor=$STATE_DIR $BACKUP_DIR" in api_dropin
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
        "/etc/systemd/system/seiche-api.service.d/market-platform.conf",
        "/etc/systemd/system/seiche-release-poll.service.d/storage-volume.conf",
        "/etc/systemd/system/seiche-market-validation.service.d/state-path.conf",
        "/etc/systemd/system/seiche-market-backup.service.d/paths.conf",
        "/etc/systemd/system/seiche-market-restore-check.service.d/paths.conf",
    ):
        assert name in manifest

    for timer in ("VALIDATION", "BACKUP", "RESTORE"):
        assert f"{timer}_TIMER_WAS_ACTIVE" in wrapper
        assert f"{timer}_TIMER_WAS_ENABLED" in wrapper

    restore = wrapper[
        wrapper.index("restore_preupdate_data_units() {") : wrapper.index(
            "trap 'cleanup_preupdate_data_units || true' EXIT"
        )
    ]
    for unit in (
        "seiche-market-validation.timer",
        "seiche-market-backup.timer",
        "seiche-market-restore-check.timer",
    ):
        assert f"systemctl enable {unit}" in restore
        assert f"systemctl disable {unit}" in restore
        assert f"systemctl is-enabled --quiet {unit}" in restore
