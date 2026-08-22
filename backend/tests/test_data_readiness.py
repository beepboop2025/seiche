"""Host-free behavior tests for the Seiche data-readiness systemd gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import grp
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "deploy" / "seiche-data-readiness.sh"
SERVICE = ROOT / "ops" / "deploy" / "seiche-data-readiness.service"
TIMER = ROOT / "ops" / "deploy" / "seiche-data-readiness.timer"
INSTALLER = ROOT / "ops" / "deploy" / "install-market-platform.sh"
RESTORE_CHECK = ROOT / "ops" / "deploy" / "seiche-market-restore-check.sh"
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def _health(
    *,
    generated_at: datetime | None = None,
    faults: list[object] | None = None,
    provenance: list[object] | None = None,
) -> str:
    return json.dumps(
        {
            "generated_at": (generated_at or NOW - timedelta(minutes=1)).isoformat(),
            "version": "test",
            "faults": [] if faults is None else faults,
            "provenance": [] if provenance is None else provenance,
        }
    )


def _layout(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    tools = tmp_path / "tools"
    tools.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    snapshot = backup_dir / (NOW - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
    snapshot.mkdir()
    (snapshot / "SHA256SUMS").write_text("verified\n")
    recovery_proof_dir = tmp_path / "recovery-proof"
    recovery_proof_dir.mkdir(mode=0o750)
    # The release gate runs with UMask=0077, which narrows mkdir(0750) to 0700.
    # Set the fixture's production contract explicitly so these host-free tests
    # do not depend on the invoking shell or systemd unit's ambient umask.
    recovery_proof_dir.chmod(0o750)
    restore_receipt = recovery_proof_dir / "backup-restore-check.status"
    restore_receipt.write_text(
        "schema=seiche.market-backup-restore-check.v2\n"
        f"checked_at={(NOW - timedelta(hours=1)).isoformat()}\n"
        "snapshot=20260822T020000Z\n"
        f"deployed_sha={'a' * 40}\n"
        "critical_table_counts=11|12|13|14\n"
        "critical_table_count_floor=11|12|13|14\n"
        "database_restore=pass\n"
        "state_archive_restore=pass\n"
        "api_data_archive_restore=pass\n"
        "research_only=true\n"
        "can_publish=false\n"
        "can_execute=false\n"
    )
    restore_receipt.chmod(0o640)
    deployed_sha_path = tmp_path / "deployed-sha"
    deployed_sha_path.write_text("a" * 40 + "\n")
    unit_calls = tmp_path / "unit-calls.log"

    curl = _executable(
        tools / "curl",
        """
        import os
        from pathlib import Path
        import sys

        if os.environ.get("FAKE_CURL_FAIL") == "1":
            raise SystemExit(22)
        args = sys.argv[1:]
        output = Path(args[args.index("--output") + 1])
        output.write_text(os.environ["FAKE_HEALTH"])
        """,
    )
    systemctl = _executable(
        tools / "systemctl",
        """
        import os
        from pathlib import Path
        import sys

        args = sys.argv[1:]
        if args[:2] not in (["is-active", "--quiet"], ["is-enabled", "--quiet"]):
            raise SystemExit(98)
        unit = args[2]
        with Path(os.environ["FAKE_UNIT_CALLS"]).open("a") as handle:
            handle.write(unit + "\\n")
        if args[0] == "is-active" and unit == os.environ.get("FAKE_INACTIVE_UNIT"):
            raise SystemExit(3)
        if args[0] == "is-enabled" and unit == os.environ.get("FAKE_DISABLED_UNIT"):
            raise SystemExit(1)
        raise SystemExit(0)
        """,
    )
    df = _executable(
        tools / "df",
        """
        import os
        import sys

        inode = "-Pi" in sys.argv[1:]
        percent = os.environ[
            "FAKE_INODE_PERCENT" if inode else "FAKE_DISK_PERCENT"
        ]
        print("Filesystem 1024-blocks Used Available Capacity Mounted on")
        print(f"fake 100 10 90 {percent}% /")
        """,
    )

    env = {
        **os.environ,
        "TMPDIR": str(tmp_dir),
        "SEICHE_CURL_BIN": str(curl),
        "SEICHE_PYTHON_BIN": sys.executable,
        "SEICHE_SYSTEMCTL_BIN": str(systemctl),
        "SEICHE_DF_BIN": str(df),
        "SEICHE_MKTEMP_BIN": shutil.which("mktemp") or "/usr/bin/mktemp",
        "SEICHE_RM_BIN": shutil.which("rm") or "/bin/rm",
        "SEICHE_DATA_READINESS_HEALTH_URL": "http://127.0.0.1:8787/api/health",
        "SEICHE_DATA_READINESS_BACKUP_DIR": str(backup_dir),
        "SEICHE_DATA_READINESS_RESTORE_RECEIPT": str(restore_receipt),
        "SEICHE_DATA_READINESS_RECEIPT_UID": str(os.getuid()),
        "SEICHE_DATA_READINESS_RECEIPT_GROUP": grp.getgrgid(os.getgid()).gr_name,
        "SEICHE_DATA_READINESS_DEPLOYED_SHA_PATH": str(deployed_sha_path),
        "SEICHE_DATA_READINESS_OFFSITE_ENV_FILE": str(tmp_path / "offsite-backup.env"),
        "SEICHE_DATA_READINESS_OFFSITE_STATUS_PATH": str(
            tmp_path / "offsite-state" / "status.json"
        ),
        "SEICHE_DATA_READINESS_OFFSITE_UID": str(os.getuid()),
        "SEICHE_DATA_READINESS_OFFSITE_GID": str(os.getgid()),
        "SEICHE_DATA_READINESS_NOW_EPOCH": str(int(NOW.timestamp())),
        "SEICHE_DATA_READINESS_REQUIRED_UNITS": (
            "seiche-api.service seiche-market-worker.service "
            "seiche-source-worker.service "
            "seiche-data-readiness.timer"
        ),
        "SEICHE_DATA_READINESS_DISK_PATHS": str(tmp_path),
        "FAKE_HEALTH": _health(
            provenance=[
                {
                    "series": "discontinued-reference",
                    "staleness": "dead",
                    "status": "DEAD",
                },
                {"series": "slow-official-release", "staleness": "stale"},
            ]
        ),
        "FAKE_UNIT_CALLS": str(unit_calls),
        "FAKE_DISK_PERCENT": "12",
        "FAKE_INODE_PERCENT": "9",
    }
    return env, backup_dir, restore_receipt


def _run(tmp_path: Path, **updates: str) -> subprocess.CompletedProcess[str]:
    env, _, _ = _layout(tmp_path)
    env.update(updates)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def _write_offsite_config(env: dict[str, str], *, canary: str = "0") -> Path:
    path = Path(env["SEICHE_DATA_READINESS_OFFSITE_ENV_FILE"])
    path.write_text(
        "SEICHE_OFFSITE_BACKUP_BUCKET=seiche-recovery\n"
        "SEICHE_OFFSITE_BACKUP_PREFIX=seiche/market-backups/v1\n"
        "SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE=anchor\n"
        "SEICHE_OFFSITE_BACKUP_WRITE_ENABLED=1\n"
        f"SEICHE_OFFSITE_BACKUP_CANARY={canary}\n"
        "SEICHE_OFFSITE_BACKUP_KEY_ID=market-key-2026-08-v1\n"
        "SEICHE_OFFSITE_BACKUP_DESTINATION_ID=hetzner-primary-v1\n"
        "SEICHE_OFFSITE_BACKUP_RETENTION_MODE=COMPLIANCE\n"
        "SEICHE_OFFSITE_BACKUP_RETENTION_DAYS=90\n"
    )
    path.chmod(0o600)
    return path


def _write_offsite_status(
    env: dict[str, str],
    *,
    verified_at: datetime | None = None,
    bucket: str = "seiche-recovery",
    object_lock: dict[str, object] | None = None,
    current_status: str = "success",
) -> Path:
    path = Path(env["SEICHE_DATA_READINESS_OFFSITE_STATUS_PATH"])
    path.parent.mkdir(mode=0o700)
    path.parent.chmod(0o700)
    prefix = "seiche/market-backups/v1"
    endpoint = "https://nbg1.your-objectstorage.com"
    region = "nbg1"
    snapshot_id = "20260822T020000Z"
    attempt_id = "20260822T052000Z-1234"
    lock = object_lock or {"mode": "COMPLIANCE", "days": 90}
    destination = {
        "id": "hetzner-primary-v1",
        "endpoint": endpoint,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
    }
    receipt_key = f"{prefix}/canary/v1/RECEIPT.json"
    path.write_text(
        json.dumps(
            {
                "schema": "seiche.market-offsite-backup-status.v1",
                "status": current_status,
                "observed_at": (NOW - timedelta(minutes=30)).isoformat(),
                "attempt_id": attempt_id,
                "snapshot_id": snapshot_id,
                "source_revision": "b" * 40,
                "provider": "hetzner-object-storage",
                "bucket": bucket,
                "prefix": prefix,
                "key_id": "market-key-2026-08-v1",
                "destination": destination,
                "ciphertext_sha256": "c" * 64,
                "ciphertext_bytes": 123456,
                "ciphertext_version_id": "ciphertext-version-1",
                "ciphertext_etag": '"0123456789abcdef"',
                "checksum_version_id": "checksum-version-1",
                "checksum_etag": '"1234567890abcdef"',
                "source_inventory_sha256": "d" * 64,
                "source_content_set_sha256": "e" * 64,
                "object_lock": lock,
                "remote_receipt_key": receipt_key,
                "remote_receipt_version_id": "receipt-version-1",
                "remote_receipt_etag": '"2345678901abcdef"',
                "restore_verified": current_status == "success",
                "failure_class": (
                    "operational_failure" if current_status == "failed" else None
                ),
                "last_success": {
                    "attempt_id": attempt_id,
                    "snapshot_id": snapshot_id,
                    "source_revision": "b" * 40,
                    "bucket": bucket,
                    "prefix": prefix,
                    "key_id": "market-key-2026-08-v1",
                    "destination": destination,
                    "ciphertext_sha256": "c" * 64,
                    "ciphertext_bytes": 123456,
                    "ciphertext_version_id": "ciphertext-version-1",
                    "ciphertext_etag": '"0123456789abcdef"',
                    "checksum_version_id": "checksum-version-1",
                    "checksum_etag": '"1234567890abcdef"',
                    "source_inventory_sha256": "d" * 64,
                    "source_content_set_sha256": "e" * 64,
                    "remote_receipt_key": receipt_key,
                    "remote_receipt_version_id": "receipt-version-1",
                    "remote_receipt_etag": '"2345678901abcdef"',
                    "object_lock": lock,
                    "restore_verified": True,
                    "verified_at": (
                        verified_at or NOW - timedelta(hours=1)
                    ).isoformat(),
                },
            }
        )
        + "\n"
    )
    path.chmod(0o600)
    return path


def test_healthy_host_passes_and_ignores_stale_discontinued_provenance(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "seiche data readiness: ready\n"
    assert result.stderr == ""


def test_unconfigured_offsite_does_not_gate_readiness(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr


def test_canary_mode_does_not_require_offsite_status(tmp_path: Path) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_config(env, canary="1")

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_orphan_offsite_status_does_not_configure_the_monitor(
    tmp_path: Path,
) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_status(env, verified_at=NOW - timedelta(days=30))

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("current_status", ["success", "running", "failed"])
def test_scheduled_mode_accepts_fresh_restore_verified_last_success(
    tmp_path: Path, current_status: str
) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_config(env)
    _write_offsite_status(env, current_status=current_status)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "drift",
    [
        "version",
        "receipt_key",
        "endpoint",
        "top_bucket",
        "top_lock",
        "status",
    ],
)
def test_scheduled_mode_rejects_impossible_producer_status_shape(
    tmp_path: Path, drift: str
) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_config(env)
    status_path = _write_offsite_status(env)
    document = json.loads(status_path.read_text())
    if drift == "version":
        document["last_success"]["ciphertext_version_id"] = "has spaces"
    elif drift == "receipt_key":
        document["last_success"]["remote_receipt_key"] = (
            "seiche/market-backups/v1/custom/RECEIPT.json"
        )
    elif drift == "endpoint":
        document["last_success"]["destination"]["endpoint"] = "wrong.example"
    elif drift == "top_bucket":
        document["bucket"] = "wrong-recovery"
    elif drift == "top_lock":
        document["object_lock"] = {"mode": "GOVERNANCE", "days": 90}
    else:
        document["status"] = "complete"
    status_path.write_text(json.dumps(document) + "\n")

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: offsite backup proof missing or invalid\n"
    )


def test_scheduled_mode_requires_status(tmp_path: Path) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_config(env)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: offsite backup proof missing or invalid\n"
    )


@pytest.mark.parametrize(
    ("verified_at", "expected"),
    [
        (NOW - timedelta(hours=36, seconds=1), "offsite backup proof stale"),
        (
            NOW + timedelta(minutes=5, seconds=1),
            "offsite backup proof timestamp is in the future",
        ),
    ],
)
def test_scheduled_mode_rejects_stale_and_future_proofs(
    tmp_path: Path, verified_at: datetime, expected: str
) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_config(env)
    _write_offsite_status(env, verified_at=verified_at)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stderr == f"seiche data readiness: {expected}\n"


def test_release_repair_bypass_skips_only_offsite_freshness(tmp_path: Path) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_config(env)
    _write_offsite_status(env, verified_at=NOW - timedelta(days=30))
    env["SEICHE_DATA_READINESS_SKIP_OFFSITE"] = "1"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_invalid_release_repair_bypass_fails_configuration(tmp_path: Path) -> None:
    result = _run(tmp_path, SEICHE_DATA_READINESS_SKIP_OFFSITE="yes")

    assert result.returncode == 1
    assert result.stderr == "seiche data readiness: configuration invalid\n"


@pytest.mark.parametrize("drift", ["bucket", "object_lock"])
def test_scheduled_mode_rejects_destination_or_object_lock_mismatch(
    tmp_path: Path, drift: str
) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_config(env)
    if drift == "bucket":
        _write_offsite_status(env, bucket="wrong-recovery")
    else:
        _write_offsite_status(env, object_lock={"mode": "GOVERNANCE", "days": 90})

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: offsite backup proof missing or invalid\n"
    )


@pytest.mark.parametrize("drift", ["symlink", "hardlink", "mode", "owner", "parent"])
def test_scheduled_mode_rejects_unsafe_status_identity(
    tmp_path: Path, drift: str
) -> None:
    env, _, _ = _layout(tmp_path)
    _write_offsite_config(env)
    status = _write_offsite_status(env)
    if drift == "symlink":
        target = status.with_name("status-target.json")
        target.write_text(status.read_text())
        target.chmod(0o600)
        status.unlink()
        status.symlink_to(target)
    elif drift == "hardlink":
        os.link(status, status.with_name("status-hardlink.json"))
    elif drift == "mode":
        status.chmod(0o640)
    elif drift == "owner":
        env["SEICHE_DATA_READINESS_OFFSITE_UID"] = str(os.getuid() + 1)
    else:
        status.parent.chmod(0o750)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    expected = (
        "offsite backup configuration invalid"
        if drift == "owner"
        else "offsite backup proof missing or invalid"
    )
    assert result.stderr == f"seiche data readiness: {expected}\n"


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        ("not-json", "API health JSON invalid"),
        (
            _health(generated_at=NOW - timedelta(seconds=901)),
            "API snapshot stale",
        ),
        (
            _health(
                faults=[
                    {
                        "source": "official-feed",
                        "status": "FAILED",
                        "category": "SOURCE_ERROR",
                        "detail": "Bearer private-token",
                    }
                ]
            ),
            "API health reports critical faults",
        ),
        (
            _health(
                faults=[
                    {
                        "source": "official-collector-worker",
                        "status": "OVERDUE",
                        "category": "WORKER_HEALTH",
                    }
                ]
            ),
            "collector heartbeat unhealthy",
        ),
        (
            _health(
                faults=[
                    {
                        "source": "official-collector-worker",
                        "status": "MISSING",
                        "category": "WORKER_HEALTH",
                    }
                ]
            ),
            "collector heartbeat unhealthy",
        ),
    ],
)
def test_health_contract_failures_are_fixed_privacy_safe_reasons(
    tmp_path: Path, health: str, expected: str
) -> None:
    result = _run(tmp_path, FAKE_HEALTH=health)

    assert result.returncode == 1
    assert f"seiche data readiness: {expected}\n" in result.stderr
    assert "private-token" not in result.stderr
    assert "official-feed" not in result.stderr
    assert "not-json" not in result.stderr


def test_health_fetch_failure_is_nonzero_without_reflecting_curl_details(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        FAKE_CURL_FAIL="1",
        SEICHE_DATA_READINESS_HEALTH_URL="https://secret.example/private-token",
    )

    assert result.returncode == 1
    assert result.stderr == "seiche data readiness: API health fetch failed\n"
    assert "secret.example" not in result.stderr


def test_proof_only_skips_operational_health_units_and_disk(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        SEICHE_DATA_READINESS_PROOF_ONLY="1",
        FAKE_CURL_FAIL="1",
        FAKE_INACTIVE_UNIT="seiche-market-worker.service",
        FAKE_DISK_PERCENT="99",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "seiche data readiness: ready\n"


def test_stale_backup_and_restore_receipt_fail_at_default_limits(
    tmp_path: Path,
) -> None:
    env, backup_dir, restore_receipt = _layout(tmp_path)
    for child in backup_dir.iterdir():
        shutil.rmtree(child)
    stale_snapshot = backup_dir / (NOW - timedelta(hours=36, seconds=1)).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    stale_snapshot.mkdir()
    (stale_snapshot / "SHA256SUMS").write_text("verified\n")
    fresh_checked_at = f"checked_at={(NOW - timedelta(hours=1)).isoformat()}"
    stale_checked_at = f"checked_at={(NOW - timedelta(days=8, seconds=1)).isoformat()}"
    restore_receipt.write_text(
        restore_receipt.read_text().replace(fresh_checked_at, stale_checked_at)
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert "seiche data readiness: backup artifact stale\n" in result.stderr
    assert "seiche data readiness: restore receipt stale\n" in result.stderr


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("api", "API health generated_at is in the future"),
        ("backup", "backup artifact timestamp is in the future"),
        ("restore", "restore receipt timestamp is in the future"),
    ],
)
def test_future_timestamps_beyond_clock_skew_fail_closed(
    tmp_path: Path,
    surface: str,
    expected: str,
) -> None:
    env, backup_dir, restore_receipt = _layout(tmp_path)
    future = NOW + timedelta(seconds=301)
    if surface == "api":
        env["FAKE_HEALTH"] = _health(generated_at=future)
    elif surface == "backup":
        for child in backup_dir.iterdir():
            shutil.rmtree(child)
        snapshot = backup_dir / future.strftime("%Y%m%dT%H%M%SZ")
        snapshot.mkdir()
        (snapshot / "SHA256SUMS").write_text("verified\n")
    else:
        current = f"checked_at={(NOW - timedelta(hours=1)).isoformat()}"
        restore_receipt.write_text(
            restore_receipt.read_text().replace(
                current,
                f"checked_at={future.isoformat()}",
            )
        )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stderr == f"seiche data readiness: {expected}\n"


def test_missing_backup_and_restore_receipt_fail_closed(tmp_path: Path) -> None:
    env, backup_dir, restore_receipt = _layout(tmp_path)
    shutil.rmtree(backup_dir)
    restore_receipt.unlink()

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert "seiche data readiness: backup artifact missing\n" in result.stderr
    assert (
        "seiche data readiness: restore receipt missing or invalid\n" in result.stderr
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "schema=seiche.market-backup-restore-check.v2",
            "schema=seiche.market-backup-restore-check.v1",
        ),
        ("database_restore=pass", "database_restore=failed"),
        ("state_archive_restore=pass", "state_archive_restore=failed"),
        ("api_data_archive_restore=pass", "api_data_archive_restore=failed"),
        ("research_only=true", "research_only=false"),
        ("can_publish=false", "can_publish=true"),
        ("can_execute=false", "can_execute=true"),
        ("critical_table_counts=11|12|13|14", "critical_table_counts=11|12"),
    ],
)
def test_restore_receipt_requires_the_complete_v2_pass_contract(
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    env, _, restore_receipt = _layout(tmp_path)
    restore_receipt.write_text(restore_receipt.read_text().replace(old, new))

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: restore receipt missing or invalid\n"
    )


def test_restore_receipt_must_belong_to_the_current_deployed_release(
    tmp_path: Path,
) -> None:
    env, _, restore_receipt = _layout(tmp_path)
    restore_receipt.write_text(
        restore_receipt.read_text().replace(
            "deployed_sha=" + "a" * 40,
            "deployed_sha=" + "b" * 40,
        )
    )
    mismatch = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert mismatch.returncode == 1
    assert mismatch.stderr == (
        "seiche data readiness: restore receipt belongs to a different release\n"
    )


@pytest.mark.parametrize("marker_kind", ["missing", "symlink", "invalid"])
def test_deployed_release_sha_marker_fails_closed(
    tmp_path: Path,
    marker_kind: str,
) -> None:
    env, _, _ = _layout(tmp_path)
    marker = Path(env["SEICHE_DATA_READINESS_DEPLOYED_SHA_PATH"])
    if marker_kind == "missing":
        marker.unlink()
    elif marker_kind == "symlink":
        target = tmp_path / "deployed-sha-target"
        target.write_text("a" * 40 + "\n")
        marker.unlink()
        marker.symlink_to(target)
    else:
        marker.write_text("not-a-git-sha\n")

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: deployed release SHA missing or invalid\n"
    )


@pytest.mark.parametrize(
    "unsafe_kind",
    ["owner", "mode", "hardlink", "parent_mode", "symlink"],
)
def test_restore_receipt_identity_and_directory_are_fail_closed(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    env, _, receipt = _layout(tmp_path)
    if unsafe_kind == "owner":
        env["SEICHE_DATA_READINESS_RECEIPT_UID"] = str(os.getuid() + 1)
    elif unsafe_kind == "mode":
        receipt.chmod(0o660)
    elif unsafe_kind == "hardlink":
        os.link(receipt, receipt.with_name("forged-hardlink"))
    elif unsafe_kind == "parent_mode":
        receipt.parent.chmod(0o770)
    else:
        target = receipt.with_name("forged-target")
        target.write_text(receipt.read_text())
        target.chmod(0o640)
        receipt.unlink()
        receipt.symlink_to(target)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: restore receipt missing or invalid\n"
    )


def test_inactive_required_unit_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path, FAKE_INACTIVE_UNIT="seiche-market-worker.service")

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: required unit inactive: seiche-market-worker.service\n"
    )


def test_active_but_disabled_required_timer_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path, FAKE_DISABLED_UNIT="seiche-data-readiness.timer")

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: required timer disabled: seiche-data-readiness.timer\n"
    )


@pytest.mark.parametrize(
    ("variable", "expected"),
    [
        ("FAKE_DISK_PERCENT", "disk usage critical"),
        ("FAKE_INODE_PERCENT", "inode usage critical"),
    ],
)
def test_disk_and_inode_usage_fail_at_the_critical_threshold(
    tmp_path: Path, variable: str, expected: str
) -> None:
    result = _run(tmp_path, **{variable: "90"})

    assert result.returncode == 1
    assert result.stderr == f"seiche data readiness: {expected}\n"


def test_systemd_units_are_alerting_hardened_and_five_minutely() -> None:
    script = SCRIPT.read_text()
    service = SERVICE.read_text()
    timer = TIMER.read_text()
    required_defaults = next(
        line for line in script.splitlines() if "DEFAULT_REQUIRED_UNITS=" in line
    )

    assert "seiche-source-worker.service" in required_defaults
    assert "OnFailure=undertow-failure-alert@%n.service" in service
    assert "Type=oneshot" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=read-only" in service
    assert "CapabilityBoundingSet=\n" in service
    assert "ReadOnlyPaths=/home/seiche/app" in service
    assert "-/var/lib/seiche-offsite-backup" in service
    assert "SEICHE_DATA_READINESS_SKIP_OFFSITE" not in service
    assert "IPAddressAllow=localhost" in service
    assert "After=seiche-market-worker.service seiche-source-worker.service" in timer
    assert "OnCalendar=*-*-* *:00/5:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=seiche-data-readiness.service" in timer


def test_capability_free_readiness_service_can_traverse_restore_receipt_tree() -> None:
    """Keep the unit identity aligned with installer and receipt permissions."""

    service = SERVICE.read_text()
    installer = INSTALLER.read_text()
    restore_check = RESTORE_CHECK.read_text()

    assert "User=root\n" in service
    assert "Group=seiche\n" in service
    assert "CapabilityBoundingSet=\n" in service
    assert "install -d -o seiche -g seiche -m 0750 \\\n" in installer
    assert '"$STATE_DIR" "$STATE_DIR/raw"' in installer
    assert 'install -d -o root -g seiche -m 0750 "$RECOVERY_PROOF_DIR"' in installer
    assert "/var/lib/seiche-recovery-proof" in service
    assert 'chown root:seiche "$STATUS_STAGE"' in restore_check
    assert 'chmod 0640 "$STATUS_STAGE"' in restore_check
