"""Host-free behavioral tests for market backup and restore-check scripts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from seiche import nbs_intake as nbs
from seiche import nbs_trust

ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = ROOT / "ops" / "deploy" / "seiche-market-backup.sh"
RESTORE_SCRIPT = ROOT / "ops" / "deploy" / "seiche-market-restore-check.sh"
TEST_NBS_RUNTIME_SHA = "b" * 40
TEST_NBS_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
TEST_NBS_PUBLIC_KEY = TEST_NBS_PRIVATE_KEY.public_key().public_bytes_raw().hex()
TEST_UNTRUSTED_NBS_PUBLIC_KEY = (
    Ed25519PrivateKey.from_private_bytes(bytes(reversed(range(32))))
    .public_key()
    .public_bytes_raw()
    .hex()
)


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
    copy = _executable(
        tools / "cp",
        """
        import os
        from pathlib import Path
        import shutil
        import sys

        args = sys.argv[1:]
        if args[:2] != ["-R", "--"] or len(args) != 4:
            raise SystemExit(12)
        if any(arg == "-a" or arg.startswith("--preserve") for arg in args):
            raise SystemExit(13)
        source = Path(args[2])
        destination = Path(args[3])
        for item in source.iterdir():
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copyfile(item, target)
        with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as handle:
            handle.write("cp " + " ".join(args) + "\\n")
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
        with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as handle:
            handle.write("tar " + " ".join(args) + "\\n")
        if "--create" in args:
            target = Path(args[args.index("--file") + 1])
            prefix = b"api-data-archive" if target.name == "api-data.tgz" else b"state-archive"
            target.write_bytes(prefix + b"-payload")
        elif "--list" in args:
            target = Path(args[args.index("--file") + 1])
            payload = target.read_bytes()
            if not payload.startswith((b"state-archive", b"api-data-archive")):
                raise SystemExit(10)
            if payload.startswith(b"api-data-archive"):
                print("api-data/")
            else:
                print("seiche/")
                print("seiche-nbs/")
        elif "--extract" in args:
            archive = Path(args[args.index("--file") + 1])
            target = Path(args[args.index("--directory") + 1])
            if archive.read_bytes().startswith(b"api-data-archive"):
                restored = target / "api-data"
                restored.mkdir(parents=True)
                import sqlite3
                with sqlite3.connect(restored / "seiche.sqlite") as database:
                    database.execute("CREATE TABLE restored (value TEXT)")
            else:
                restored = target / "seiche" / "raw"
                restored.mkdir(parents=True)
                (restored / "capture.json").write_text("official evidence\\n")
                fixture = os.environ.get("FAKE_NBS_STORE_FIXTURE")
                nbs_store = target / "seiche-nbs"
                if fixture:
                    import shutil
                    shutil.copytree(Path(fixture), nbs_store, symlinks=True)
                else:
                    (nbs_store / "restricted").mkdir(parents=True)
                    (nbs_store / "public" / "revisions").mkdir(parents=True)
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
        "SEICHE_CP_BIN": str(copy),
        "SEICHE_SHA256SUM_BIN": str(sha256sum),
        "SEICHE_SYNC_BIN": str(sync),
        "SEICHE_DATE_BIN": str(date),
        "SEICHE_PYTHON_BIN": sys.executable,
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
    recovery_proof = tmp_path / "recovery-proof"
    recovery_proof.mkdir(mode=0o750)
    (state / "raw" / "capture.json").write_text("official evidence\n")
    nbs_state = state.parent / "seiche-nbs"
    (nbs_state / "restricted").mkdir(parents=True)
    (nbs_state / "public" / "revisions").mkdir(parents=True)
    api_data = tmp_path / "app" / "backend" / "data"
    api_data.mkdir(parents=True)
    with sqlite3.connect(api_data / "seiche.sqlite") as database:
        database.execute("CREATE TABLE accounts (username TEXT PRIMARY KEY)")
        database.execute("INSERT INTO accounts VALUES ('researcher')")
    (api_data / "brief-cache.json").write_text('{"status":"real"}\n')
    backup = tmp_path / "backups"
    marker = tmp_path / "deployed-sha"
    marker.write_text("a" * 40 + "\n")
    nbs_runtime = _install_test_nbs_runtime(tmp_path)
    env.update(
        {
            "SEICHE_MARKET_STATE_DIR": str(state),
            "SEICHE_NBS_STATE_DIR": str(nbs_state),
            "SEICHE_API_DATA_DIR": str(api_data),
            "SEICHE_MARKET_BACKUP_DIR": str(backup),
            "SEICHE_DEPLOYED_SHA_PATH": str(marker),
            "SEICHE_NBS_RUNTIME_ROOT": str(nbs_runtime),
            "SEICHE_RESTORE_STATUS_PATH": str(
                recovery_proof / "backup-restore-check.status"
            ),
            "SEICHE_BACKUP_STAMP": "20260810T020000Z",
            "SEICHE_BACKUP_RETENTION_DAYS": "21",
        }
    )
    return state, backup, marker


def _run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)], env=env, text=True, capture_output=True, check=False
    )


def _rewrite_manifest_and_inventory(
    snapshot: Path,
    transform,
) -> None:
    """Model a recomputed, internally consistent but untrusted snapshot."""

    manifest = snapshot / "manifest.env"
    manifest.write_text(transform(manifest.read_text()))
    inventory = []
    for line in (snapshot / "SHA256SUMS").read_text().splitlines():
        _, name = line.split("  ", 1)
        digest = hashlib.sha256((snapshot / name).read_bytes()).hexdigest()
        inventory.append(f"{digest}  {name}")
    (snapshot / "SHA256SUMS").write_text("\n".join(inventory) + "\n")


def _canonical_json(record: object) -> bytes:
    return json.dumps(
        record,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _install_test_nbs_runtime(tmp_path: Path) -> Path:
    """Install one user-owned analogue of the sealed production runtime."""

    runtime = tmp_path / "nbs-runtime"
    releases = runtime / "releases"
    release = releases / TEST_NBS_RUNTIME_SHA
    package = release / "seiche"
    package.mkdir(parents=True)
    sources = ROOT / "backend" / "seiche"
    for name in ("__init__.py", "nbs_intake.py", "nbs_trust.py"):
        body = (sources / name).read_text()
        if name == "nbs_trust.py":
            production_keys = sorted(nbs_trust.PRODUCTION_TRUSTED_OPERATOR_KEYS)
            assert production_keys
            body = body.replace(production_keys[0], TEST_NBS_PUBLIC_KEY, 1)
            assert TEST_NBS_PUBLIC_KEY in body
        destination = package / name
        destination.write_text(body)
        destination.chmod(0o444)
    package.chmod(0o555)
    release.chmod(0o555)
    releases.chmod(0o555)
    pointer = runtime / "current-sha"
    pointer.write_text(TEST_NBS_RUNTIME_SHA + "\n")
    pointer.chmod(0o444)
    runtime.chmod(0o755)
    return runtime


def _create_valid_nbs_public_store(root: Path) -> str:
    """Create one deterministic signed head for the restore-check fixture."""

    root.chmod(0o750)
    (root / "restricted").chmod(0o700)
    (root / "public").chmod(0o750)
    (root / "public" / "revisions").chmod(0o2750)
    private_key = TEST_NBS_PRIVATE_KEY
    public_key = TEST_NBS_PUBLIC_KEY
    raw = (
        b"\xef\xbb\xbfNBS browser export\r\n"
        b"Indicators\t,July 2026\t\r\n"
        b"Consumer Price Index (The same month last year=100)\t,100.5\t\r\n"
        b"Data Sources: National Bureau of Statistics\t,\r\n"
    )
    binding = nbs.NBS_SERIES_BINDINGS["CN.NBS.CPI_INDEX"]
    manifest = {
        "schema": nbs.NBS_EXPORT_SCHEMA,
        "dataset": nbs.NBS_DATASET,
        "export_id": "nbs-restore-fixture-r1",
        "predecessor_export_id": None,
        "predecessor_manifest_sha256": None,
        "commitment_nonce": "01" * 32,
        "publisher": nbs.NBS_PUBLISHER,
        "knowledge_time": "2026-08-22T10:00:00Z",
        "source_url": nbs.NBS_BROWSER_SOURCE_URL,
        "sources": [binding.manifest_dict()],
        "records": [
            {
                "series_id": binding.series_id,
                "period": "2026-07",
                "value": "100.5",
            }
        ],
        "raw_evidence": {
            "filename": "nbs-restore-fixture.csv",
            "format": nbs.NBS_RAW_FORMAT,
            "media_type": "text/csv",
            "month_headers": [{"period": "2026-07", "raw_header": "July 2026"}],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        },
        "publication_policy": dict(nbs.NBS_PUBLICATION_POLICY),
    }
    claim = nbs.build_signature_claim(
        manifest,
        signed_at="2026-08-22T10:05:00Z",
        signer_key_id=public_key,
    )
    signature = {
        **claim,
        "signature": private_key.sign(nbs.encode_signature_claim(claim)).hex(),
    }
    inputs = root.parent / "nbs-restore-input"
    inputs.mkdir()
    manifest_path = inputs / "manifest.json"
    signature_path = inputs / "signature.json"
    raw_path = inputs / "nbs-restore-fixture.csv"
    manifest_path.write_bytes(_canonical_json(manifest))
    signature_path.write_bytes(_canonical_json(signature))
    raw_path.write_bytes(raw)
    trust = root.parent / "nbs-restore-trust"
    trust.mkdir()
    (trust / "trusted_operator_keys").write_text(public_key + "\n")
    nbs.NBSIntakeStore(root, attest_dir=str(trust)).ingest(
        manifest_path,
        signature_path,
        raw_path,
    )
    return public_key


def _mutate_nbs_store(root: Path, mutation: str) -> None:
    export_id = "nbs-restore-fixture-r1"
    export = root / "restricted" / "exports" / export_id
    manifest_path = export / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    raw_sha256 = manifest["raw_evidence"]["sha256"]
    raw_path = root / "restricted" / "objects" / "sha256" / raw_sha256[:2] / raw_sha256
    restricted_head = root / "restricted" / "exports" / ".head.json"
    public_head = root / "public" / "revisions" / ".head.json"
    projection = root / "public" / "revisions" / f"{export_id}.json"

    if mutation == "missing_raw":
        raw_path.unlink()
    elif mutation == "corrupt_raw":
        raw_path.write_bytes(b"corrupt restricted raw evidence")
    elif mutation == "missing_manifest":
        manifest_path.unlink()
    elif mutation == "corrupt_manifest":
        manifest_path.write_bytes(b"{}")
    elif mutation == "missing_signature":
        (export / "signature.json").unlink()
    elif mutation == "corrupt_signature":
        (export / "signature.json").write_bytes(b"{}")
    elif mutation == "missing_restricted_head":
        restricted_head.unlink()
    elif mutation == "corrupt_restricted_head":
        restricted_head.write_bytes(b"{}")
    elif mutation == "missing_public_head":
        public_head.unlink()
    elif mutation == "corrupt_public_head":
        public_head.write_bytes(b"{}")
    elif mutation == "orphan_raw_object":
        orphan_sha256 = "f" * 64
        if orphan_sha256 == raw_sha256:
            orphan_sha256 = "e" * 64
        bucket = root / "restricted" / "objects" / "sha256" / orphan_sha256[:2]
        bucket.mkdir(mode=0o700, exist_ok=True)
        orphan = bucket / orphan_sha256
        orphan.write_bytes(b"unreferenced restricted evidence")
        orphan.chmod(0o600)
    elif mutation == "extra_root_entry":
        (root / "unexpected").write_text("unknown topology")
    elif mutation == "symlink_raw_object":
        source = root.parent / "external-raw-evidence"
        source.write_bytes(raw_path.read_bytes())
        raw_path.unlink()
        raw_path.symlink_to(source)
    elif mutation == "restricted_public_mismatch":
        projection.write_bytes(projection.read_bytes() + b"\n")
    else:  # pragma: no cover - test helper contract
        raise AssertionError(f"unknown NBS mutation: {mutation}")


def _runtime_paths(env: dict[str, str]) -> tuple[Path, Path, Path, Path]:
    runtime = Path(env["SEICHE_NBS_RUNTIME_ROOT"])
    release = runtime / "releases" / TEST_NBS_RUNTIME_SHA
    package = release / "seiche"
    return runtime, release, package, runtime / "current-sha"


def _prepare_restore(
    tmp_path: Path,
) -> tuple[dict[str, str], Path, Path]:
    env, calls = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    backup_result = _run(BACKUP_SCRIPT, env)
    assert backup_result.returncode == 0, backup_result.stdout + backup_result.stderr
    env["SEICHE_RESTORE_SNAPSHOT"] = str(backup / "20260810T020000Z")
    return env, calls, backup


def _assert_runtime_rejected(
    env: dict[str, str], calls: Path
) -> subprocess.CompletedProcess[str]:
    result = _run(RESTORE_SCRIPT, env)
    assert result.returncode != 0
    assert "restored NBS evidence store failed strict validation" in result.stderr
    assert "100.5" not in result.stderr
    assert "corrupt restricted raw evidence" not in result.stderr
    assert not Path(env["SEICHE_RESTORE_STATUS_PATH"]).exists()
    assert not any(
        line.startswith("createdb ") for line in calls.read_text().splitlines()
    )
    return result


def test_backup_commits_verified_snapshot_and_never_replaces_it(tmp_path: Path):
    env, calls = _tools(tmp_path)
    state, backup, _ = _layout(tmp_path, env)

    result = _run(BACKUP_SCRIPT, env)

    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = backup / "20260810T020000Z"
    assert {item.name for item in snapshot.iterdir()} == {
        "SHA256SUMS",
        "deployed-sha.txt",
        "manifest.env",
        "api-data.tgz",
        "seiche.dump",
        "table-counts.txt",
        "var-lib-seiche.tgz",
    }
    manifest = (snapshot / "manifest.env").read_text()
    assert "schema=seiche.market-backup.v3" in manifest
    assert f"nbs_state_root={env['SEICHE_NBS_STATE_DIR']}" in manifest
    assert "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1" in manifest
    assert "nbs_full_store_audit_result=required_at_restore" in manifest
    assert f"api_data_root={env['SEICHE_API_DATA_DIR']}" in manifest
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
    assert "cp -R --" in log
    nbs_state = Path(env["SEICHE_NBS_STATE_DIR"])
    assert (
        f"--directory {state.parent} {state.name} "
        f"--directory {nbs_state.parent} {nbs_state.name}"
    ) in log
    assert (backup.stat().st_mode & 0o777) == 0o700
    assert all(
        (member.stat().st_mode & 0o777) == 0o600 for member in snapshot.iterdir()
    )


def test_backup_ignores_untrusted_pythonpath_for_sqlite_snapshot(
    tmp_path: Path,
) -> None:
    env, _calls = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    poison = tmp_path / "poison"
    poison.mkdir()
    marker = tmp_path / "untrusted-sqlite-import-ran"
    (poison / "sqlite3.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
        "raise RuntimeError('untrusted sqlite3 imported')\n"
    )
    env["PYTHONPATH"] = str(poison)

    result = _run(BACKUP_SCRIPT, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (backup / "20260810T020000Z").is_dir()
    assert not marker.exists()


def test_backup_accepts_append_only_writes_during_snapshot(tmp_path: Path):
    env, _ = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    env["FAKE_COUNTS_CALLS"] = str(tmp_path / "count-calls")
    env["FAKE_COUNTS_SEQUENCE"] = "11|12|13|14,12|12|13|14"

    result = _run(BACKUP_SCRIPT, env)

    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = backup / "20260810T020000Z"
    assert (snapshot / "table-counts.txt").read_text() == "11|12|13|14\n"
    assert (
        "critical_table_count_semantics=pre_dump_lower_bound"
        in (snapshot / "manifest.env").read_text()
    )


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
    _state, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    snapshot = backup / "20260810T020000Z"
    env["SEICHE_RESTORE_SNAPSHOT"] = str(snapshot)

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode == 0, result.stdout + result.stderr
    status_path = Path(env["SEICHE_RESTORE_STATUS_PATH"])
    status = status_path.read_text()
    assert "snapshot=20260810T020000Z" in status
    assert "critical_table_counts=11|12|13|14" in status
    assert "critical_table_count_floor=11|12|13|14" in status
    assert "schema=seiche.market-backup-restore-check.v4" in status
    assert "database_restore=pass" in status
    assert "state_archive_restore=pass" in status
    assert "nbs_public_revision_store=not_onboarded" in status
    assert "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1" in status
    assert "nbs_full_store_audit_result=not_onboarded" in status
    assert "api_data_archive_restore=pass" in status
    assert not list(status_path.parent.glob(".backup-state-restore.*"))
    assert not list(status_path.parent.glob(".backup-api-data-restore.*"))
    assert "can_publish=false" in status
    log = calls.read_text()
    assert "setpriv " in log
    assert "createdb --template=template0" in log
    assert sum(line.startswith("dropdb ") for line in log.splitlines()) == 1
    assert "--port=5544" in log


def test_restore_strictly_verifies_a_signed_nbs_public_head(tmp_path: Path) -> None:
    env, _ = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    nbs_root = Path(env["SEICHE_NBS_STATE_DIR"])
    _create_valid_nbs_public_store(nbs_root)
    env["FAKE_NBS_STORE_FIXTURE"] = str(nbs_root)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    env["SEICHE_RESTORE_SNAPSHOT"] = str(backup / "20260810T020000Z")

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode == 0, result.stdout + result.stderr
    status = Path(env["SEICHE_RESTORE_STATUS_PATH"]).read_text()
    assert "nbs_public_revision_store=verified_head\n" in status
    assert "nbs_full_store_audit_result=verified_head\n" in status


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_raw",
        "corrupt_raw",
        "missing_manifest",
        "corrupt_manifest",
        "missing_signature",
        "corrupt_signature",
        "missing_restricted_head",
        "corrupt_restricted_head",
        "missing_public_head",
        "corrupt_public_head",
        "orphan_raw_object",
        "extra_root_entry",
        "symlink_raw_object",
        "restricted_public_mismatch",
    ],
)
def test_restore_rejects_incomplete_or_divergent_nbs_full_store(
    tmp_path: Path,
    mutation: str,
) -> None:
    env, calls = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    nbs_root = Path(env["SEICHE_NBS_STATE_DIR"])
    _create_valid_nbs_public_store(nbs_root)
    env["FAKE_NBS_STORE_FIXTURE"] = str(nbs_root)
    if mutation != "symlink_raw_object":
        _mutate_nbs_store(nbs_root, mutation)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    if mutation == "symlink_raw_object":
        # The live backup boundary already rejects symlinks. Mutate the fake
        # extracted fixture after snapshot creation to exercise restore too.
        _mutate_nbs_store(nbs_root, mutation)
    env["SEICHE_RESTORE_SNAPSHOT"] = str(backup / "20260810T020000Z")

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert "restored NBS evidence store failed strict validation" in result.stderr
    assert "100.5" not in result.stderr
    assert "corrupt restricted raw evidence" not in result.stderr
    assert not Path(env["SEICHE_RESTORE_STATUS_PATH"]).exists()
    assert not any(
        line.startswith("createdb ") for line in calls.read_text().splitlines()
    )


@pytest.mark.parametrize("mutation", ["malformed", "symlink", "mode", "hardlink"])
def test_restore_rejects_unsafe_sealed_runtime_pointer(
    tmp_path: Path, mutation: str
) -> None:
    env, calls, _ = _prepare_restore(tmp_path)
    runtime, _release, _package, pointer = _runtime_paths(env)
    if mutation == "malformed":
        pointer.chmod(0o644)
        pointer.write_text("not-a-release\n")
        pointer.chmod(0o444)
    elif mutation == "symlink":
        pointer.unlink()
        pointer.symlink_to(runtime / "releases")
    elif mutation == "mode":
        pointer.chmod(0o644)
    else:
        os.link(pointer, tmp_path / "current-sha-hardlink")

    _assert_runtime_rejected(env, calls)


@pytest.mark.parametrize(
    "mutation",
    [
        "root_symlink",
        "release_symlink",
        "release_mode",
        "extra_module",
        "module_mode",
        "module_hardlink",
    ],
)
def test_restore_rejects_unsafe_sealed_runtime_tree(
    tmp_path: Path, mutation: str
) -> None:
    env, calls, _ = _prepare_restore(tmp_path)
    runtime, release, package, _pointer = _runtime_paths(env)
    if mutation == "root_symlink":
        alias = tmp_path / "nbs-runtime-alias"
        alias.symlink_to(runtime, target_is_directory=True)
        env["SEICHE_NBS_RUNTIME_ROOT"] = str(alias)
    elif mutation == "release_symlink":
        releases = runtime / "releases"
        releases.chmod(0o755)
        real_release = releases / ("c" * 40)
        release.rename(real_release)
        release.symlink_to(real_release, target_is_directory=True)
        releases.chmod(0o555)
    elif mutation == "release_mode":
        release.chmod(0o755)
    elif mutation == "extra_module":
        package.chmod(0o755)
        unexpected = package / "unexpected.py"
        unexpected.write_text("raise RuntimeError('must not import')\n")
        unexpected.chmod(0o444)
        package.chmod(0o555)
    elif mutation == "module_mode":
        (package / "nbs_intake.py").chmod(0o644)
    else:
        os.link(package / "nbs_trust.py", tmp_path / "nbs-trust-hardlink.py")

    _assert_runtime_rejected(env, calls)


def test_restore_ignores_untrusted_pythonpath_for_sealed_imports(
    tmp_path: Path,
) -> None:
    env, _calls, _ = _prepare_restore(tmp_path)
    poison = tmp_path / "poison"
    poison_package = poison / "seiche"
    poison_package.mkdir(parents=True)
    marker = tmp_path / "untrusted-import-ran"
    (poison_package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
    )
    env["PYTHONPATH"] = str(poison)

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


def test_restore_uses_only_the_sealed_nbs_trust_key_policy(tmp_path: Path) -> None:
    env, calls, _ = _prepare_restore(tmp_path)
    nbs_root = Path(env["SEICHE_NBS_STATE_DIR"])
    _create_valid_nbs_public_store(nbs_root)
    env["FAKE_NBS_STORE_FIXTURE"] = str(nbs_root)
    _runtime, _release, package, _pointer = _runtime_paths(env)
    trust_module = package / "nbs_trust.py"
    trust_module.chmod(0o644)
    original = trust_module.read_text()
    changed = original.replace(
        TEST_NBS_PUBLIC_KEY,
        TEST_UNTRUSTED_NBS_PUBLIC_KEY,
        1,
    )
    assert changed != original
    trust_module.write_text(changed)
    trust_module.chmod(0o444)

    _assert_runtime_rejected(env, calls)


def test_restore_rejects_package_that_redirects_module_origins(tmp_path: Path) -> None:
    env, calls, _ = _prepare_restore(tmp_path)
    _runtime, _release, package, _pointer = _runtime_paths(env)
    poison_package = tmp_path / "redirected" / "seiche"
    poison_package.mkdir(parents=True)
    (poison_package / "nbs_intake.py").write_text("")
    (poison_package / "nbs_trust.py").write_text("")
    package_init = package / "__init__.py"
    package_init.chmod(0o644)
    package_init.write_text(f"__path__ = [{str(poison_package)!r}]\n")
    package_init.chmod(0o444)

    result = _assert_runtime_rejected(env, calls)

    assert "wrong origin" in result.stderr


def test_restore_rejects_rechecksummed_manifest_that_breaks_v2_contract(
    tmp_path: Path,
) -> None:
    env, calls = _tools(tmp_path)
    _state, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    snapshot = backup / "20260810T020000Z"
    env["SEICHE_RESTORE_SNAPSHOT"] = str(snapshot)

    _rewrite_manifest_and_inventory(
        snapshot,
        lambda manifest: manifest.replace(
            "schema=seiche.market-backup.v3\n",
            "schema=untrusted.foreign.snapshot\n",
        ).replace(
            "research_only=true\ncan_publish=false\ncan_execute=false\n",
            "research_only=false\ncan_publish=true\ncan_execute=true\n",
        ),
    )

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert result.stderr == (
        "seiche market restore check: snapshot manifest contract is invalid\n"
    )
    assert not Path(env["SEICHE_RESTORE_STATUS_PATH"]).exists()
    assert not any(
        line.startswith("createdb ") for line in calls.read_text().splitlines()
    )


def test_restore_rejects_duplicate_or_unknown_manifest_fields(tmp_path: Path) -> None:
    env, _ = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    snapshot = backup / "20260810T020000Z"
    env["SEICHE_RESTORE_SNAPSHOT"] = str(snapshot)
    _rewrite_manifest_and_inventory(
        snapshot,
        lambda manifest: manifest + "schema=seiche.market-backup.v3\nunknown=x\n",
    )

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert "snapshot manifest contract is invalid" in result.stderr


@pytest.mark.parametrize(
    "mutation",
    ["v2_schema", "wrong_nbs_root", "wrong_audit_contract", "claimed_audit_result"],
)
def test_restore_rejects_pre_nbs_or_drifted_full_store_contract(
    tmp_path: Path,
    mutation: str,
) -> None:
    env, calls = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    snapshot = backup / "20260810T020000Z"
    env["SEICHE_RESTORE_SNAPSHOT"] = str(snapshot)

    replacements = {
        "v2_schema": (
            "schema=seiche.market-backup.v3",
            "schema=seiche.market-backup.v2",
        ),
        "wrong_nbs_root": (
            f"nbs_state_root={env['SEICHE_NBS_STATE_DIR']}",
            "nbs_state_root=/var/lib/foreign-nbs",
        ),
        "wrong_audit_contract": (
            "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1",
            "nbs_full_store_audit_contract=untrusted.audit.v1",
        ),
        "claimed_audit_result": (
            "nbs_full_store_audit_result=required_at_restore",
            "nbs_full_store_audit_result=verified_head",
        ),
    }
    old, new = replacements[mutation]
    _rewrite_manifest_and_inventory(
        snapshot, lambda manifest: manifest.replace(old, new)
    )

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert result.stderr == (
        "seiche market restore check: snapshot manifest contract is invalid\n"
    )
    assert not Path(env["SEICHE_RESTORE_STATUS_PATH"]).exists()
    assert not any(
        line.startswith("createdb ") for line in calls.read_text().splitlines()
    )


def test_restore_rejects_manifest_for_wrong_database_or_unsafe_roots(
    tmp_path: Path,
) -> None:
    env, _ = _tools(tmp_path)
    _, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    snapshot = backup / "20260810T020000Z"
    env["SEICHE_RESTORE_SNAPSHOT"] = str(snapshot)
    _rewrite_manifest_and_inventory(
        snapshot,
        lambda manifest: (
            manifest.replace("database=seiche", "database=foreign")
            .replace(
                f"state_root={env['SEICHE_MARKET_STATE_DIR']}",
                "state_root=/",
            )
            .replace(
                f"api_data_root={env['SEICHE_API_DATA_DIR']}",
                "api_data_root=relative/data",
            )
        ),
    )

    result = _run(RESTORE_SCRIPT, env)

    assert result.returncode != 0
    assert "snapshot manifest contract is invalid" in result.stderr


def test_failed_restore_drops_scratch_and_preserves_last_good_status(tmp_path: Path):
    env, calls = _tools(tmp_path)
    _state, backup, _ = _layout(tmp_path, env)
    assert _run(BACKUP_SCRIPT, env).returncode == 0
    env["SEICHE_RESTORE_SNAPSHOT"] = str(backup / "20260810T020000Z")
    assert _run(RESTORE_SCRIPT, env).returncode == 0
    status_path = Path(env["SEICHE_RESTORE_STATUS_PATH"])
    before = status_path.read_bytes()
    env["FAKE_COUNTS"] = "1|12|13|14"

    failed = _run(RESTORE_SCRIPT, env)

    assert failed.returncode != 0
    assert "counts fall below the snapshot floor" in failed.stderr
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
