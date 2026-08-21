"""Host-free behavior tests for the Seiche data-readiness systemd gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    restore_receipt = tmp_path / "backup-restore-check.status"
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
        "SEICHE_DATA_READINESS_DEPLOYED_SHA_PATH": str(deployed_sha_path),
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


def test_healthy_host_passes_and_ignores_stale_discontinued_provenance(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "seiche data readiness: ready\n"
    assert result.stderr == ""


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


def test_inactive_required_unit_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path, FAKE_INACTIVE_UNIT="seiche-market-worker.service")

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: required unit inactive: "
        "seiche-market-worker.service\n"
    )


def test_active_but_disabled_required_timer_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path, FAKE_DISABLED_UNIT="seiche-data-readiness.timer")

    assert result.returncode == 1
    assert result.stderr == (
        "seiche data readiness: required timer disabled: "
        "seiche-data-readiness.timer\n"
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
    assert '"$STATE_DIR/validation"' in installer
    assert 'chown root:seiche "$STATUS_STAGE"' in restore_check
    assert 'chmod 0640 "$STATUS_STAGE"' in restore_check
