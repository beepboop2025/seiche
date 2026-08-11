"""Host-free behavioral tests for market backup and restore-check scripts."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = ROOT / "ops" / "deploy" / "seiche-market-backup.sh"
RESTORE_SCRIPT = ROOT / "ops" / "deploy" / "seiche-market-restore-check.sh"


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def _tools(tmp_path: Path) -> tuple[dict[str, str], Path]:
    tools = tmp_path / "tools"
    tools.mkdir()
    calls = tmp_path / "calls.log"
    setpriv = _executable(
        tools / "setpriv",
        """
        import os
        import subprocess
        import sys

        args = sys.argv[1:]
        if args[:5] != [
            "--reuid=postgres",
            "--regid=5432",
            "--init-groups",
            "--inh-caps=-all",
            "--",
        ]:
            raise SystemExit(97)
        with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as handle:
            handle.write("setpriv " + " ".join(args[5:]) + "\\n")
        raise SystemExit(subprocess.run(args[5:], check=False).returncode)
        """,
    )
    psql = _executable(
        tools / "psql",
        """
        import os
        import sys

        joined = " ".join(sys.argv[1:])
        if "SHOW port" in joined:
            print(os.environ.get("FAKE_POSTGRES_PORT", "5544"))
        else:
            calls_path = os.environ.get("FAKE_COUNTS_CALLS")
            index = 0
            if calls_path:
                from pathlib import Path
                path = Path(calls_path)
                if path.exists():
                    index = int(path.read_text())
                path.write_text(str(index + 1))
            values = os.environ.get("FAKE_COUNTS_SEQUENCE", "").split(",")
            print(values[min(index, len(values) - 1)] if values[0] else os.environ.get("FAKE_COUNTS", "11|12|13|14"))
        """,
    )
    pg_dump = _executable(
        tools / "pg_dump",
        """
        import os
        import sys

        if os.environ.get("FAKE_DUMP_FAIL") == "1":
            raise SystemExit(8)
        sys.stdout.buffer.write(os.environ.get("FAKE_DUMP", "database-dump-payload").encode())
        """,
    )
    tar = _executable(
        tools / "tar",
        """
        import os
        from pathlib import Path
        import sys

        if os.environ.get("FAKE_TAR_FAIL") == "1":
            raise SystemExit(9)
        args = sys.argv[1:]
        if "--create" in args:
            target = Path(args[args.index("--file") + 1])
            target.write_bytes(b"state-archive-payload")
        elif "--list" in args:
            target = Path(args[args.index("--file") + 1])
            if not target.read_bytes().startswith(b"state-archive"):
                raise SystemExit(10)
            print("seiche/")
        elif "--extract" in args:
            target = Path(args[args.index("--directory") + 1])
            restored = target / "seiche" / "raw"
            restored.mkdir(parents=True)
            (restored / "capture.json").write_text("official evidence\\n")
        """,
    )
    sha256sum = _executable(
        tools / "sha256sum",
        """
        import hashlib
        from pathlib import Path
        import sys

        args = sys.argv[1:]
        if "--check" in args:
            manifest = Path(args[-1])
            for line in manifest.read_text().splitlines():
                digest, name = line.split("  ", 1)
                observed = hashlib.sha256(Path(name).read_bytes()).hexdigest()
                if observed != digest:
                    raise SystemExit(1)
            raise SystemExit(0)
        for raw in args:
            path = Path(raw)
            print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        """,
    )
    sync = _executable(tools / "sync", "raise SystemExit(0)\n")
    pg_restore = _executable(
        tools / "pg_restore",
        """
        import os
        import sys

        sys.stdin.buffer.read()
        if os.environ.get("FAKE_RESTORE_FAIL") == "1":
            raise SystemExit(11)
        print("TABLE public canonical_observations")
        """,
    )
    database_tool = """
        import os
        import sys

        with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as handle:
            handle.write("{name} " + " ".join(sys.argv[1:]) + "\\n")
    """
    createdb = _executable(tools / "createdb", database_tool.format(name="createdb"))
    dropdb = _executable(tools / "dropdb", database_tool.format(name="dropdb"))
    date = _executable(tools / "date", 'print("2026-08-10T08:00:00Z")\n')
    identity = _executable(
        tools / "id",
        """
        import sys

        if sys.argv[1:] != ["-g", "postgres"]:
            raise SystemExit(96)
        print("5432")
        """,
    )
    env = {
        **os.environ,
        "SEICHE_ALLOW_NON_ROOT_BACKUP_TEST": "1",
        "SEICHE_ID_BIN": str(identity),
        "SEICHE_SETPRIV_BIN": str(setpriv),
        "SEICHE_PSQL_BIN": str(psql),
        "SEICHE_PG_DUMP_BIN": str(pg_dump),
        "SEICHE_PG_RESTORE_BIN": str(pg_restore),
        "SEICHE_CREATEDB_BIN": str(createdb),
        "SEICHE_DROPDB_BIN": str(dropdb),
        "SEICHE_TAR_BIN": str(tar),
        "SEICHE_SHA256SUM_BIN": str(sha256sum),
        "SEICHE_SYNC_BIN": str(sync),
        "SEICHE_DATE_BIN": str(date),
        "SEICHE_BACKUP_MIN_DUMP_BYTES": "1",
        "FAKE_CALLS": str(calls),
    }
    return env, calls


def _layout(tmp_path: Path, env: dict[str, str]) -> tuple[Path, Path, Path]:
    state = tmp_path / "state" / "seiche"
    (state / "raw").mkdir(parents=True)
    (state / "normalized").mkdir()
    (state / "exports").mkdir()
    (state / "validation").mkdir()
    (state / "raw" / "capture.json").write_text("official evidence\n")
    backup = tmp_path / "backups"
    marker = tmp_path / "deployed-sha"
    marker.write_text("a" * 40 + "\n")
    env.update(
        {
            "SEICHE_MARKET_STATE_DIR": str(state),
            "SEICHE_MARKET_BACKUP_DIR": str(backup),
            "SEICHE_DEPLOYED_SHA_PATH": str(marker),
            "SEICHE_BACKUP_STAMP": "20260810T020000Z",
            "SEICHE_BACKUP_RETENTION_DAYS": "21",
        }
    )
    return state, backup, marker


def _run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)], env=env, text=True, capture_output=True, check=False
    )


def test_backup_commits_verified_snapshot_and_never_replaces_it(tmp_path: Path):
    env, calls = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)

    result = _run(BACKUP_SCRIPT, env)

    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = backup / "20260810T020000Z"
    assert {item.name for item in snapshot.iterdir()} == {
        "SHA256SUMS",
        "deployed-sha.txt",
        "manifest.env",
        "seiche.dump",
        "table-counts.txt",
        "var-lib-seiche.tgz",
    }
    manifest = (snapshot / "manifest.env").read_text()
    assert "schema=seiche.market-backup.v1" in manifest
    assert "postgres_port=5544" in manifest
    assert "research_only=true" in manifest
    assert "can_publish=false" in manifest
    assert "can_execute=false" in manifest
    assert not list(backup.glob(".stage-*"))
    first_dump = (snapshot / "seiche.dump").read_bytes()
    repeated = _run(BACKUP_SCRIPT, env)
    assert repeated.returncode != 0
    assert "refusing to replace existing snapshot" in repeated.stderr
    assert (snapshot / "seiche.dump").read_bytes() == first_dump
    log = calls.read_text()
    assert "setpriv " in log
    assert "SHOW port" in log
    assert "--port=5544" in log


def test_backup_rejects_a_database_that_changes_during_snapshot(tmp_path: Path):
    env, _ = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    env["FAKE_COUNTS_CALLS"] = str(tmp_path / "count-calls")
    env["FAKE_COUNTS_SEQUENCE"] = "11|12|13|14,12|12|13|14"

    result = _run(BACKUP_SCRIPT, env)

    assert result.returncode != 0
    assert "counts changed during snapshot" in result.stderr
    assert not (backup / "20260810T020000Z").exists()
    assert not list(backup.glob(".stage-*"))


def test_backup_failure_preserves_committed_snapshot_and_cleans_stage(tmp_path: Path):
    env, _ = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    committed = backup / "20260810T020000Z" / "SHA256SUMS"
    before = committed.read_bytes()
    env["SEICHE_BACKUP_STAMP"] = "20260811T020000Z"
    env["FAKE_TAR_FAIL"] = "1"

    failed = _run(BACKUP_SCRIPT, env)

    assert failed.returncode != 0
    assert committed.read_bytes() == before
    assert not (backup / "20260811T020000Z").exists()
    assert not list(backup.glob(".stage-*"))


def test_restore_check_uses_scratch_database_and_commits_status(tmp_path: Path):
    env, calls = _tools(tmp_path)
    state, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    snapshot = backup / "20260810T020000Z"
    env["SEICHE_RESTORE_SNAPSHOT"] = str(snapshot)

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode == 0, result.stdout + result.stderr
    status = (state / "validation" / "backup-restore-check.status").read_text()
    assert "snapshot=20260810T020000Z" in status
    assert "critical_table_counts=11|12|13|14" in status
    assert "database_restore=pass" in status
    assert "state_archive_restore=pass" in status
    assert not list((state / "validation").glob(".backup-state-restore.*"))
    assert "can_publish=false" in status
    log = calls.read_text()
    assert "setpriv " in log
    assert "createdb --template=template0" in log
    assert sum(line.startswith("dropdb ") for line in log.splitlines()) == 1
    assert "--port=5544" in log


def test_failed_restore_drops_scratch_and_preserves_last_good_status(tmp_path: Path):
    env, calls = _tools(tmp_path)
    state, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    env["SEICHE_RESTORE_SNAPSHOT"] = str(backup / "20260810T020000Z")
    assert _run(RESTORE_SCRIPT, env).returncode == 0
    status_path = state / "validation" / "backup-restore-check.status"
    before = status_path.read_bytes()
    env["FAKE_COUNTS"] = "99|12|13|14"

    failed = _run(RESTORE_SCRIPT, env)

    assert failed.returncode != 0
    assert "counts do not match" in failed.stderr
    assert status_path.read_bytes() == before
    assert (
        sum(line.startswith("dropdb ") for line in calls.read_text().splitlines()) == 2
    )


def test_restore_rejects_snapshot_outside_backup_root(tmp_path: Path):
    env, _ = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    backup.mkdir()
    outside = tmp_path / "20260810T020000Z"
    outside.mkdir()
    env["SEICHE_RESTORE_SNAPSHOT"] = str(outside)

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert "outside the configured backup directory" in result.stderr
