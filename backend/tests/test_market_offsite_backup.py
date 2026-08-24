"""Host-free contract tests for encrypted, append-only market backups."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "deploy" / "seiche-market-offsite-backup.sh"
SERVICE = ROOT / "ops" / "deploy" / "seiche-market-offsite-backup.service"
TIMER = ROOT / "ops" / "deploy" / "seiche-market-offsite-backup.timer"
INSTALLER = ROOT / "ops" / "deploy" / "install-market-platform.sh"
DEPLOY_WRAPPER = ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh"
REVISION = "a" * 40
SNAPSHOT_ID = "20260822T020000Z"


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


def _snapshot(
    backup_root: Path,
    *,
    snapshot_id: str = SNAPSHOT_ID,
    extra: bool = False,
    schema: str = "v4",
) -> Path:
    snapshot = backup_root / snapshot_id
    snapshot.mkdir(parents=True)
    if schema not in {"v3", "v4"}:
        raise AssertionError("unsupported test snapshot schema")
    payloads: dict[str, bytes] = {
        "seiche.dump": b"postgres-custom-dump\n",
        "var-lib-seiche.tgz": b"state-archive\n",
        "api-data.tgz": b"api-data-archive\n",
        "table-counts.txt": b"11|12|13|14\n",
        "deployed-sha.txt": f"{REVISION}\n".encode(),
        "manifest.env": (
            f"schema=seiche.market-backup.{schema}\n"
            f"created_at={snapshot_id}\n"
            "database=seiche\n"
            "postgres_port=5433\n"
            "state_root=/var/lib/seiche\n"
            "nbs_state_root=/var/lib/seiche-nbs\n"
            "api_data_root=/home/seiche/app/backend/data\n"
            "critical_table_count_semantics=pre_dump_lower_bound\n"
            "nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1\n"
            "nbs_full_store_audit_result=required_at_restore\n"
            + (
                "palimpsest_china_state_root=/var/lib/seiche-palimpsest-china\n"
                "palimpsest_china_state_audit_contract="
                "seiche.palimpsest-china-activation-state.v1\n"
                "palimpsest_china_state_audit_result=required_at_restore\n"
                if schema == "v4"
                else ""
            )
            + "research_only=true\n"
            "can_publish=false\n"
            "can_execute=false\n"
        ).encode(),
    }
    if schema == "v4":
        audit = {
            "schema": "seiche.palimpsest-china-activation-state.v1",
            "state_root": "/var/lib/seiche-palimpsest-china",
            "tree_sha256": "b" * 64,
            "bundles": [],
            "receipts": [],
            "active_activation_id": None,
            "pending_candidate_activation_id": None,
        }
        payloads["palimpsest-china.tgz"] = b"palimpsest-china-state-archive\n"
        payloads["palimpsest-china-state.json"] = (
            json.dumps(
                audit,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            + b"\n"
        )
    for name, body in payloads.items():
        (snapshot / name).write_bytes(body)
    inventory_names = ["seiche.dump", "var-lib-seiche.tgz"]
    if schema == "v4":
        inventory_names.extend(("palimpsest-china.tgz", "palimpsest-china-state.json"))
    inventory_names.extend(
        ("api-data.tgz", "table-counts.txt", "deployed-sha.txt", "manifest.env")
    )
    inventory = "".join(
        f"{hashlib.sha256(payloads[name]).hexdigest()}  {name}\n"
        for name in inventory_names
    )
    (snapshot / "SHA256SUMS").write_text(inventory)
    if extra:
        (snapshot / "uncommitted.tmp").write_text("not part of the contract\n")
    return snapshot


def _rewrite_inventory(snapshot: Path) -> None:
    names = ["seiche.dump", "var-lib-seiche.tgz"]
    if (snapshot / "palimpsest-china.tgz").exists():
        names.extend(("palimpsest-china.tgz", "palimpsest-china-state.json"))
    names.extend(
        ("api-data.tgz", "table-counts.txt", "deployed-sha.txt", "manifest.env")
    )
    lines = []
    for name in names:
        digest = hashlib.sha256()
        with (snapshot / name).open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        lines.append(f"{digest.hexdigest()}  {name}\n")
    (snapshot / "SHA256SUMS").write_text("".join(lines))


def _rewrite_manifest_fields(
    snapshot: Path,
    *,
    replacements: dict[str, str] | None = None,
    removals: frozenset[str] = frozenset(),
) -> None:
    replacements = replacements or {}
    rewritten = []
    seen = set()
    for line in (snapshot / "manifest.env").read_text().splitlines():
        key, _separator, value = line.partition("=")
        if key in removals:
            continue
        if key in replacements:
            value = replacements[key]
            seen.add(key)
        rewritten.append(f"{key}={value}\n")
    if seen != set(replacements):
        raise AssertionError("test attempted to replace an absent manifest field")
    (snapshot / "manifest.env").write_text("".join(rewritten))
    _rewrite_inventory(snapshot)


def _fake_tools(tmp_path: Path) -> dict[str, Path]:
    tools = tmp_path / "tools"
    tools.mkdir()
    fake_stat = _executable(
        tools / "stat",
        """
        import os
        import sys

        args = sys.argv[1:]
        if args[0] not in {"-c", "-Lc"}:
            raise SystemExit(91)
        if args[1] == "%s":
            print(os.path.getsize(args[-1]))
        else:
            raise SystemExit(92)
        """,
    )
    _executable(
        tools / "realpath",
        """
        from pathlib import Path
        import sys

        print(Path(sys.argv[-1]).resolve(strict=True))
        """,
    )
    _executable(tools / "mountpoint", "raise SystemExit(1)\n")
    fake_flock = _executable(tools / "flock", "raise SystemExit(0)\n")
    fake_date = _executable(
        tools / "date",
        """
        import os
        import sys

        args = sys.argv[1:]
        if "+%Y%m%dT%H%M%SZ" in args:
            print(os.environ.get("FAKE_ATTEMPT_STAMP", "20260822T052000Z"))
        elif "-d" in args:
            value = args[args.index("-d") + 1]
            if value.startswith("2033-"):
                print("2000000000")
            else:
                print(os.environ.get("FAKE_SNAPSHOT_EPOCH", "1800000000"))
        elif "+%s" in args:
            print(os.environ.get("FAKE_NOW_EPOCH", "1800010000"))
        else:
            raise SystemExit(94)
        """,
    )
    fake_df = _executable(
        tools / "df",
        """
        import os

        available = os.environ.get("FAKE_AVAILABLE_KB", "100000000")
        print("Filesystem 1024-blocks Used Available Capacity Mounted on")
        print(f"fake 100000000 0 {available} 0% /fake")
        """,
    )
    fake_sha = _executable(
        tools / "sha256sum",
        """
        import hashlib
        from pathlib import Path
        import sys

        for raw in sys.argv[1:]:
            path = Path(raw)
            print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        """,
    )
    fake_gpg = _executable(
        tools / "gpg",
        """
        from pathlib import Path
        import json
        import os
        import sys

        args = sys.argv[1:]
        if args == ["--dump-options"]:
            print("--force-aead")
            print("--aead-algo")
            raise SystemExit(0)
        with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as handle:
            handle.write(json.dumps(["gpg", *args]) + "\\n")
        if "--decrypt" in args:
            body = Path(args[-1]).read_bytes()
            if not body.startswith(b"AEAD:"):
                raise SystemExit(95)
            sys.stdout.buffer.write(body[5:])
        else:
            target = Path(args[args.index("--output") + 1])
            target.write_bytes(b"AEAD:" + sys.stdin.buffer.read())
        """,
    )
    fake_tar = _executable(
        tools / "tar",
        """
        from pathlib import Path
        import os
        import shutil
        import sys

        args = sys.argv[1:]
        if "--create" in args:
            sys.stdout.buffer.write(b"sealed-snapshot-tar")
            raise SystemExit(0)
        if "--extract" not in args:
            raise SystemExit(96)
        sys.stdin.buffer.read()
        source = Path(os.environ["FAKE_SNAPSHOT_PATH"])
        destination = Path(args[args.index("--directory") + 1]) / source.name
        shutil.copytree(source, destination)
        if os.environ.get("FAKE_RESTORE_TAMPER") == "1":
            (destination / "seiche.dump").write_bytes(b"tampered\\n")
        """,
    )
    fake_rclone = _executable(
        tools / "rclone",
        """
        from pathlib import Path
        import json
        import os
        import shutil
        import sys

        args = sys.argv[1:]
        if args[0] != "copyto":
            raise SystemExit(97)
        source, destination = args[1:3]
        remote_root = Path(os.environ["FAKE_REMOTE_ROOT"])

        def resolve(value: str) -> Path:
            if value.startswith("anchor:"):
                return remote_root / value.split(":", 1)[1]
            return Path(value)

        source_path = resolve(source)
        destination_path = resolve(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination_path)
        with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as handle:
            handle.write(json.dumps(["rclone", *args]) + "\\n")
        """,
    )
    fake_curl = _executable(
        tools / "curl",
        """
        from pathlib import Path
        import hashlib
        import json
        import os
        import shutil
        import sys
        from urllib.parse import urlparse

        args = sys.argv[1:]
        target = Path(args[args.index("--output") + 1])
        url = args[-1]
        remote_root = Path(os.environ["FAKE_REMOTE_ROOT"])
        bucket = os.environ.get("FAKE_BUCKET", "seiche-recovery")
        key = urlparse(url).path.lstrip("/")
        remote_path = remote_root / bucket / key
        authenticated = "--config" in args
        code = "200"
        if "?object-lock" in url:
            mode = os.environ.get("FAKE_LOCK_MODE", "COMPLIANCE")
            target.write_text(
                "<ObjectLockConfiguration>"
                "<ObjectLockEnabled>Enabled</ObjectLockEnabled>"
                f"<Rule><DefaultRetention><Mode>{mode}</Mode>"
                "<Days>90</Days></DefaultRetention></Rule>"
                "</ObjectLockConfiguration>"
            )
        elif "versions=" in args:
            prefix_arg = next(value for value in args if value.startswith("prefix="))
            prefix = prefix_arg.split("=", 1)[1]
            has_versions = any((remote_root / bucket / prefix).parent.rglob("*"))
            entry = ""
            if has_versions:
                entry = f"<Version><Key>{prefix}archive</Key></Version>"
            if os.environ.get("FAKE_DELETE_MARKER_ONLY") == "1":
                entry = f"<DeleteMarker><Key>{prefix}archive</Key></DeleteMarker>"
            target.write_text(
                "<ListVersionsResult>"
                f"{entry}<IsTruncated>false</IsTruncated>"
                "</ListVersionsResult>"
            )
        elif "--head" in args and not authenticated:
            code = "200" if os.environ.get("FAKE_PUBLIC_READABLE") == "1" else "403"
        elif "--head" in args and not remote_path.exists():
            code = "404"
            target.write_text("HTTP/1.1 404 Not Found\\r\\n")
        elif "--head" in args:
            mode = os.environ.get("FAKE_LOCK_MODE", "COMPLIANCE")
            digest = hashlib.sha256(remote_path.read_bytes()).hexdigest()
            target.write_text(
                "HTTP/1.1 200 OK\\r\\n"
                f"x-amz-object-lock-mode: {mode}\\r\\n"
                "x-amz-object-lock-retain-until-date: 2033-05-18T03:33:20Z\\r\\n"
                f"x-amz-version-id: version-{digest[:24]}\\r\\n"
                f'ETag: "{digest[:32]}"\\r\\n'
            )
        elif "--get" in args:
            if not remote_path.exists():
                code = "404"
            else:
                shutil.copyfile(remote_path, target)
                if (
                    os.environ.get("FAKE_CIPHERTEXT_TAMPER") == "1"
                    and target.name == "downloaded.tar.gpg"
                ):
                    target.write_bytes(target.read_bytes() + b"x")
        with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as handle:
            handle.write(json.dumps(["curl", *args]) + "\\n")
        print(code, end="")
        """,
    )
    return {
        "stat": fake_stat,
        "flock": fake_flock,
        "date": fake_date,
        "df": fake_df,
        "sha": fake_sha,
        "gpg": fake_gpg,
        "tar": fake_tar,
        "rclone": fake_rclone,
        "curl": fake_curl,
    }


def _layout(
    tmp_path: Path, *, extra_snapshot_member: bool = False
) -> tuple[dict[str, str], Path, Path, Path]:
    fake = _fake_tools(tmp_path)
    backup_root = tmp_path / "backups"
    snapshot = _snapshot(backup_root, extra=extra_snapshot_member)
    work_root = tmp_path / "work"
    status_path = tmp_path / "state" / "status.json"
    config = tmp_path / "offsite-backup.env"
    config.write_text("test fixture; values arrive through the environment\n")
    passphrase = tmp_path / "offsite-backup.passphrase"
    passphrase.write_text("correct horse battery staple plus entropy\n")
    credential = tmp_path / "object-storage.env"
    credential.write_text("test fixture; no production credential\n")
    deployed_sha = tmp_path / "deployed-sha"
    deployed_sha.write_text(f"{REVISION}\n")
    lock_path = tmp_path / "market-backup.lock"
    lock_path.touch()
    run_lock_path = tmp_path / "market-offsite-backup.lock"
    run_lock_path.touch()
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    calls = tmp_path / "calls.jsonl"
    env = os.environ | {
        "PATH": f"{tmp_path / 'tools'}:{os.environ['PATH']}",
        "SEICHE_OFFSITE_ALLOW_NON_ROOT_TEST": "1",
        "SEICHE_MARKET_BACKUP_DIR": str(backup_root),
        "SEICHE_OFFSITE_BACKUP_WORK_DIR": str(work_root),
        "SEICHE_OFFSITE_BACKUP_STATUS_PATH": str(status_path),
        "SEICHE_OFFSITE_BACKUP_ENV_FILE": str(config),
        "SEICHE_OFFSITE_BACKUP_PASSPHRASE_FILE": str(passphrase),
        "SEICHE_OFFSITE_BACKUP_CREDENTIAL_ENV_FILE": str(credential),
        "SEICHE_DEPLOYED_SHA_PATH": str(deployed_sha),
        "SEICHE_OFFSITE_BACKUP_LOCK_PATH": str(lock_path),
        "SEICHE_OFFSITE_BACKUP_RUN_LOCK_PATH": str(run_lock_path),
        "SEICHE_OFFSITE_BACKUP_BUCKET": "seiche-recovery",
        "SEICHE_OFFSITE_BACKUP_PREFIX": "seiche/market-backups/v1",
        "SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE": "anchor",
        "SEICHE_OFFSITE_BACKUP_WRITE_ENABLED": "1",
        "SEICHE_OFFSITE_BACKUP_CANARY": "1",
        "SEICHE_OFFSITE_BACKUP_KEY_ID": "market-key-2026-08-v1",
        "SEICHE_OFFSITE_BACKUP_DESTINATION_ID": "hetzner-hel1-primary-v1",
        "SEICHE_OFFSITE_BACKUP_RETENTION_MODE": "COMPLIANCE",
        "SEICHE_OFFSITE_BACKUP_RETENTION_DAYS": "90",
        "SEICHE_OFFSITE_BACKUP_MIN_FREE_MB": "256",
        "SEICHE_OFFSITE_BACKUP_SNAPSHOT_ID": SNAPSHOT_ID,
        "SEICHE_OFFSITE_FLOCK_BIN": str(fake["flock"]),
        "SEICHE_OFFSITE_DATE_BIN": str(fake["date"]),
        "SEICHE_OFFSITE_DF_BIN": str(fake["df"]),
        "SEICHE_OFFSITE_SHA256SUM_BIN": str(fake["sha"]),
        "SEICHE_OFFSITE_GPG_BIN": str(fake["gpg"]),
        "SEICHE_OFFSITE_TAR_BIN": str(fake["tar"]),
        "SEICHE_OFFSITE_RCLONE_BIN": str(fake["rclone"]),
        "SEICHE_OFFSITE_CURL_BIN": str(fake["curl"]),
        "SEICHE_OFFSITE_PYTHON_BIN": sys.executable,
        "RCLONE_CONFIG_ANCHOR_ACCESS_KEY_ID": "test-access-key",
        "RCLONE_CONFIG_ANCHOR_SECRET_ACCESS_KEY": "test-secret-key",
        "RCLONE_CONFIG_ANCHOR_ENDPOINT": "https://hel1.example.invalid",
        "RCLONE_CONFIG_ANCHOR_REGION": "hel1",
        "RCLONE_CONFIG_ANCHOR_TYPE": "s3",
        "RCLONE_CONFIG_ANCHOR_PROVIDER": "Other",
        "RCLONE_CONFIG_ANCHOR_ACL": "private",
        "SEICHE_RELEASE_SHA": REVISION,
        "FAKE_SNAPSHOT_PATH": str(snapshot),
        "FAKE_REMOTE_ROOT": str(remote_root),
        "FAKE_CALLS": str(calls),
    }
    return env, status_path, remote_root, calls


def _run(
    env: dict[str, str], *, data_limit_bytes: int | None = None
) -> subprocess.CompletedProcess[str]:
    def limit_data_segment() -> None:
        if data_limit_bytes is None:
            return
        _soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
        resource.setrlimit(resource.RLIMIT_DATA, (data_limit_bytes, hard))

    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        preexec_fn=(
            limit_data_segment
            if data_limit_bytes is not None and sys.platform != "darwin"
            else None
        ),
    )


def _calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_canary_uploads_copy_only_and_commits_round_trip_receipt(tmp_path: Path):
    env, status_path, remote_root, calls_path = _layout(tmp_path)

    result = _run(env)

    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads(status_path.read_text())
    assert status["schema"] == "seiche.market-offsite-backup-status.v3"
    assert status["status"] == "success"
    assert status["restore_verified"] is True
    assert status["source_revision"] == REVISION
    assert status["object_lock"] == {"days": 90, "mode": "COMPLIANCE"}
    assert status["last_success"]["restore_verified"] is True
    assert status["last_success"]["bucket"] == "seiche-recovery"
    assert status["last_success"]["prefix"] == "seiche/market-backups/v1"
    assert status["last_success"]["key_id"] == "market-key-2026-08-v1"
    assert status["last_success"]["destination"]["id"] == ("hetzner-hel1-primary-v1")
    for record in (status, status["last_success"]):
        assert record["source_backup_schema"] == "seiche.market-backup.v4"
        assert record["nbs_state_root"] == "/var/lib/seiche-nbs"
        assert (
            record["nbs_full_store_audit_contract"] == "seiche.nbs-full-store-audit.v1"
        )
        assert record["nbs_full_store_audit_result"] == "required_at_restore"
        assert (
            record["palimpsest_china_state_root"] == "/var/lib/seiche-palimpsest-china"
        )
        assert (
            record["palimpsest_china_state_audit_contract"]
            == "seiche.palimpsest-china-activation-state.v1"
        )
        assert record["palimpsest_china_state_tree_sha256"] == "b" * 64
        assert record["palimpsest_china_state"] == "inactive"
    assert status["last_success"]["ciphertext_version_id"].startswith("version-")
    assert status["last_success"]["remote_receipt_version_id"].startswith("version-")
    assert status["remote_receipt_key"] == (
        "seiche/market-backups/v1/canary/v1/RECEIPT.json"
    )
    assert status["last_success"]["object_lock"] == {
        "days": 90,
        "mode": "COMPLIANCE",
    }
    remote_files = sorted(
        path.name for path in remote_root.rglob("*") if path.is_file()
    )
    assert remote_files == [
        "CIPHERTEXT-SHA256SUMS",
        "RECEIPT.json",
        "seiche-market-backup.tar.gpg",
    ]
    receipt_path = next(remote_root.rglob("RECEIPT.json"))
    receipt = json.loads(receipt_path.read_text())
    assert receipt["schema"] == "seiche.market-offsite-backup-receipt.v3"
    assert receipt["source_backup_schema"] == "seiche.market-backup.v4"
    assert receipt["nbs_state_root"] == "/var/lib/seiche-nbs"
    assert receipt["nbs_full_store_audit_contract"] == "seiche.nbs-full-store-audit.v1"
    assert receipt["nbs_full_store_audit_result"] == "required_at_restore"
    assert receipt["palimpsest_china_state_root"] == "/var/lib/seiche-palimpsest-china"
    assert (
        receipt["palimpsest_china_state_audit_contract"]
        == "seiche.palimpsest-china-activation-state.v1"
    )
    assert receipt["palimpsest_china_state_tree_sha256"] == "b" * 64
    assert receipt["palimpsest_china_state"] == "inactive"
    assert "closed-source-hash" in receipt["verification"]
    assert "palimpsest-state-audits-required-at-restore" in receipt["verification"]
    assert not {
        "nbs_revision_id",
        "nbs_public_head",
        "nbs_restricted_members",
        "nbs_raw_values",
        "nbs_numeric_values",
    }.intersection(receipt)
    rclone_calls = [call for call in _calls(calls_path) if call[0] == "rclone"]
    assert rclone_calls
    assert {call[1] for call in rclone_calls} == {"copyto"}
    assert all("--immutable" in call for call in rclone_calls)
    exact_downloads = [
        call
        for call in _calls(calls_path)
        if call[0] == "curl" and "versionId=" in " ".join(call)
    ]
    assert len(exact_downloads) == 2
    assert all("versionId=version-" in " ".join(call) for call in exact_downloads)
    gpg_call = next(call for call in _calls(calls_path) if call[0] == "gpg")
    assert "--cipher-algo" in gpg_call and "AES256" in gpg_call
    assert "--force-aead" in gpg_call and "OCB" in gpg_call
    assert not list((Path(env["SEICHE_OFFSITE_BACKUP_WORK_DIR"])).glob(".run-*"))


def test_canary_is_exactly_once_and_scheduled_mode_requires_it(tmp_path: Path):
    env, status_path, _remote_root, calls_path = _layout(tmp_path)
    assert _run(env).returncode == 0
    calls_after_canary = _calls(calls_path)

    duplicate = _run(env)

    assert duplicate.returncode != 0
    assert "canary already succeeded" in duplicate.stderr
    assert _calls(calls_path) == calls_after_canary
    assert json.loads(status_path.read_text())["status"] == "success"

    scheduled_env = env | {
        "SEICHE_OFFSITE_BACKUP_CANARY": "0",
        "FAKE_ATTEMPT_STAMP": "20260823T052000Z",
    }
    scheduled = _run(scheduled_env)
    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    assert [call for call in _calls(calls_path) if call[0] == "rclone"] == [
        call for call in calls_after_canary if call[0] == "rclone"
    ]


def test_remote_canary_marker_blocks_retry_when_local_status_is_lost(tmp_path: Path):
    env, status_path, _remote_root, calls_path = _layout(tmp_path)
    assert _run(env).returncode == 0
    rclone_calls = [call for call in _calls(calls_path) if call[0] == "rclone"]
    status_path.unlink()

    retry = _run(env | {"FAKE_ATTEMPT_STAMP": "20260822T062000Z"})

    assert retry.returncode != 0
    assert "operator reconciliation is required" in retry.stderr
    assert [call for call in _calls(calls_path) if call[0] == "rclone"] == rclone_calls


def test_canary_refuses_retained_versions_hidden_by_delete_marker(tmp_path: Path):
    env, status_path, remote_root, calls_path = _layout(tmp_path)
    env["FAKE_DELETE_MARKER_ONLY"] = "1"

    result = _run(env)

    assert result.returncode != 0
    assert "version history exists" in result.stderr
    assert json.loads(status_path.read_text())["status"] == "failed"
    assert not any(remote_root.iterdir())
    assert not [call for call in _calls(calls_path) if call[0] == "rclone"]


def test_scheduled_mode_blocks_unresolved_running_or_receipt_intent(tmp_path: Path):
    env, status_path, _remote_root, calls_path = _layout(tmp_path)
    assert _run(env).returncode == 0
    successful = json.loads(status_path.read_text())
    rclone_calls = [call for call in _calls(calls_path) if call[0] == "rclone"]
    scheduled_env = env | {
        "SEICHE_OFFSITE_BACKUP_CANARY": "0",
        "FAKE_ATTEMPT_STAMP": "20260823T052000Z",
    }

    for schema, state, receipt_key, receipt_version in (
        ("seiche.market-offsite-backup-status.v1", "running", None, None),
        (
            "seiche.market-offsite-backup-status.v2",
            "failed",
            "seiche/market-backups/v1/snapshots/20260823T020000Z/"
            "attempts/20260823T052000Z-99/RECEIPT.json",
            "version-unresolved-receipt",
        ),
    ):
        unresolved = successful | {
            "schema": schema,
            "status": state,
            "attempt_id": "20260823T052000Z-99",
            "snapshot_id": "20260823T020000Z",
            "remote_receipt_key": receipt_key,
            "remote_receipt_version_id": receipt_version,
            "restore_verified": False,
        }
        status_path.write_text(json.dumps(unresolved) + "\n")

        result = _run(scheduled_env)

        assert result.returncode != 0
        assert "prior offsite attempt is unresolved" in result.stderr

    assert [call for call in _calls(calls_path) if call[0] == "rclone"] == rclone_calls


def test_scheduled_mode_without_canary_refuses_every_remote_write(tmp_path: Path):
    env, status_path, remote_root, calls_path = _layout(tmp_path)
    env["SEICHE_OFFSITE_BACKUP_CANARY"] = "0"

    result = _run(env)

    assert result.returncode != 0
    assert "scheduled mode requires a successful first-write canary" in result.stderr
    assert not status_path.exists()
    assert not any(remote_root.iterdir())
    assert not [call for call in _calls(calls_path) if call[0] == "rclone"]


def test_hetzner_bucket_with_period_is_rejected_before_network(tmp_path: Path):
    env, status_path, remote_root, calls_path = _layout(tmp_path)
    env["SEICHE_OFFSITE_BACKUP_BUCKET"] = "invalid.period"

    result = _run(env)

    assert result.returncode != 0
    assert "bucket is missing or malformed" in result.stderr
    assert not status_path.exists()
    assert not any(remote_root.iterdir())
    assert not _calls(calls_path)


def test_old_destination_canary_cannot_authorize_a_new_prefix(tmp_path: Path):
    env, status_path, remote_root, calls_path = _layout(tmp_path)
    assert _run(env).returncode == 0
    calls_after_canary = _calls(calls_path)
    changed = env | {
        "SEICHE_OFFSITE_BACKUP_PREFIX": "seiche/market-backups/v2",
        "SEICHE_OFFSITE_BACKUP_CANARY": "0",
    }

    result = _run(changed)

    assert result.returncode != 0
    assert "scheduled mode requires a successful first-write canary" in result.stderr
    assert _calls(calls_path) == calls_after_canary
    assert json.loads(status_path.read_text())["last_success"]["prefix"] == (
        "seiche/market-backups/v1"
    )
    assert not list(remote_root.rglob("v2"))


def test_old_canary_cannot_authorize_key_or_endpoint_rotation(tmp_path: Path):
    env, status_path, _remote_root, calls_path = _layout(tmp_path)
    assert _run(env).returncode == 0
    rclone_calls = [call for call in _calls(calls_path) if call[0] == "rclone"]

    for changed in (
        {"SEICHE_OFFSITE_BACKUP_KEY_ID": "market-key-2026-09-v2"},
        {"RCLONE_CONFIG_ANCHOR_ENDPOINT": "https://fsn1.example.invalid"},
    ):
        result = _run(
            env
            | changed
            | {
                "SEICHE_OFFSITE_BACKUP_CANARY": "0",
                "FAKE_ATTEMPT_STAMP": "20260823T052000Z",
            }
        )
        assert result.returncode != 0
        assert (
            "scheduled mode requires a successful first-write canary" in result.stderr
        )

    assert [call for call in _calls(calls_path) if call[0] == "rclone"] == rclone_calls
    assert json.loads(status_path.read_text())["last_success"]["key_id"] == (
        "market-key-2026-08-v1"
    )


def test_ciphertext_tamper_fails_and_never_uploads_receipt(tmp_path: Path):
    env, status_path, remote_root, _calls_path = _layout(tmp_path)
    env["FAKE_CIPHERTEXT_TAMPER"] = "1"

    result = _run(env)

    assert result.returncode != 0
    assert "downloaded ciphertext hash differs" in result.stderr
    status = json.loads(status_path.read_text())
    assert status["status"] == "failed"
    assert status["restore_verified"] is False
    assert status["last_success"] is None
    assert not list(remote_root.rglob("RECEIPT.json"))
    assert not list(Path(env["SEICHE_OFFSITE_BACKUP_WORK_DIR"]).glob(".run-*"))


def test_restored_hash_failure_preserves_last_success_and_no_new_receipt(
    tmp_path: Path,
):
    env, status_path, remote_root, _calls_path = _layout(tmp_path)
    assert _run(env).returncode == 0
    first = json.loads(status_path.read_text())["last_success"]
    next_snapshot_id = "20260823T020000Z"
    next_snapshot = _snapshot(
        Path(env["SEICHE_MARKET_BACKUP_DIR"]), snapshot_id=next_snapshot_id
    )
    failed_env = env | {
        "SEICHE_OFFSITE_BACKUP_CANARY": "0",
        "SEICHE_OFFSITE_BACKUP_SNAPSHOT_ID": next_snapshot_id,
        "FAKE_ATTEMPT_STAMP": "20260823T052000Z",
        "FAKE_SNAPSHOT_PATH": str(next_snapshot),
        "FAKE_RESTORE_TAMPER": "1",
    }

    result = _run(failed_env)

    assert result.returncode != 0
    assert "authenticated restore contract" in result.stderr
    status = json.loads(status_path.read_text())
    assert status["status"] == "failed"
    assert status["last_success"] == first
    assert len(list(remote_root.rglob("RECEIPT.json"))) == 1
    assert len(list(remote_root.rglob("seiche-market-backup.tar.gpg"))) == 2


def test_legacy_v2_snapshot_cannot_masquerade_after_rehash(tmp_path: Path):
    env, status_path, remote_root, calls_path = _layout(tmp_path)
    snapshot = Path(env["FAKE_SNAPSHOT_PATH"])
    _rewrite_manifest_fields(
        snapshot,
        replacements={"schema": "seiche.market-backup.v2"},
        removals=frozenset(
            {
                "nbs_state_root",
                "nbs_full_store_audit_contract",
                "nbs_full_store_audit_result",
            }
        ),
    )

    result = _run(env)

    assert result.returncode != 0
    assert "closed backup contract" in result.stderr
    assert not status_path.exists()
    assert not any(remote_root.iterdir())
    assert not _calls(calls_path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("nbs_state_root", None),
        ("nbs_state_root", "/var/lib/seiche-nbs-alias"),
        ("nbs_full_store_audit_contract", "seiche.nbs-full-store-audit.v2"),
        ("nbs_full_store_audit_result", "verified_head"),
    ),
)
def test_snapshot_rejects_changed_nbs_audit_contract_before_network(
    tmp_path: Path, field: str, value: str | None
):
    env, status_path, remote_root, calls_path = _layout(tmp_path)
    snapshot = Path(env["FAKE_SNAPSHOT_PATH"])
    if value is None:
        _rewrite_manifest_fields(snapshot, removals=frozenset({field}))
    else:
        _rewrite_manifest_fields(snapshot, replacements={field: value})

    result = _run(env)

    assert result.returncode != 0
    assert "closed backup contract" in result.stderr
    assert not status_path.exists()
    assert not any(remote_root.iterdir())
    assert not _calls(calls_path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("palimpsest_china_state_root", None),
        ("palimpsest_china_state_root", "/var/lib/seiche-palimpsest-alias"),
        (
            "palimpsest_china_state_audit_contract",
            "seiche.palimpsest-china-activation-state.v2",
        ),
        ("palimpsest_china_state_audit_result", "verified"),
    ),
)
def test_snapshot_rejects_changed_palimpsest_state_contract_before_network(
    tmp_path: Path,
    field: str,
    value: str | None,
) -> None:
    env, status_path, remote_root, calls_path = _layout(tmp_path)
    snapshot = Path(env["FAKE_SNAPSHOT_PATH"])
    if value is None:
        _rewrite_manifest_fields(snapshot, removals=frozenset({field}))
    else:
        _rewrite_manifest_fields(snapshot, replacements={field: value})

    result = _run(env)

    assert result.returncode != 0
    assert "closed backup contract" in result.stderr
    assert not status_path.exists()
    assert not any(remote_root.iterdir())
    assert not _calls(calls_path)


def test_legacy_canary_status_cannot_authorize_scheduled_v3_write(tmp_path: Path):
    env, status_path, _remote_root, calls_path = _layout(tmp_path)
    assert _run(env).returncode == 0
    rclone_calls = [call for call in _calls(calls_path) if call[0] == "rclone"]
    status = json.loads(status_path.read_text())
    status["schema"] = "seiche.market-offsite-backup-status.v1"
    for record in (status, status["last_success"]):
        record["source_backup_schema"] = "seiche.market-backup.v2"
        record.pop("nbs_state_root")
        record.pop("nbs_full_store_audit_contract")
        record.pop("nbs_full_store_audit_result")
    status_path.write_text(json.dumps(status) + "\n")

    result = _run(
        env
        | {
            "SEICHE_OFFSITE_BACKUP_CANARY": "0",
            "FAKE_ATTEMPT_STAMP": "20260823T052000Z",
        }
    )

    assert result.returncode != 0
    assert "scheduled mode requires a successful first-write canary" in result.stderr
    assert [call for call in _calls(calls_path) if call[0] == "rclone"] == rclone_calls


def test_incomplete_snapshot_and_sha_mismatch_fail_before_network(tmp_path: Path):
    env, status_path, remote_root, calls_path = _layout(
        tmp_path, extra_snapshot_member=True
    )
    incomplete = _run(env)
    assert incomplete.returncode != 0
    assert "closed backup contract" in incomplete.stderr
    assert not status_path.exists()
    assert not any(remote_root.iterdir())

    shutil.rmtree(Path(env["SEICHE_MARKET_BACKUP_DIR"]))
    _snapshot(Path(env["SEICHE_MARKET_BACKUP_DIR"]))
    Path(env["SEICHE_DEPLOYED_SHA_PATH"]).write_text(f"{'b' * 40}\n")
    mismatch = _run(env)
    assert mismatch.returncode != 0
    assert "controller release SHAs differ" in mismatch.stderr
    assert not [call for call in _calls(calls_path) if call[0] == "rclone"]

    Path(env["SEICHE_DEPLOYED_SHA_PATH"]).write_text(f"{REVISION}\n")
    env["SEICHE_RELEASE_SHA"] = "c" * 40
    release_mismatch = _run(env)
    assert release_mismatch.returncode != 0
    assert "controller release SHAs differ" in release_mismatch.stderr
    assert not [call for call in _calls(calls_path) if call[0] == "rclone"]


def test_object_lock_policy_and_disk_bounds_fail_closed(tmp_path: Path):
    env, status_path, remote_root, _calls_path = _layout(tmp_path)
    env["FAKE_AVAILABLE_KB"] = "1"
    disk = _run(env)
    assert disk.returncode != 0
    assert "lacks the bounded encryption and restore capacity" in disk.stderr
    assert not status_path.exists()
    assert not any(remote_root.iterdir())

    env.pop("FAKE_AVAILABLE_KB")
    env["FAKE_LOCK_MODE"] = "GOVERNANCE"
    lock = _run(env)
    assert lock.returncode != 0
    assert "default retention is not COMPLIANCE" in lock.stderr
    assert json.loads(status_path.read_text())["status"] == "failed"
    assert not any(remote_root.iterdir())


def test_public_object_access_fails_before_restore_receipt(tmp_path: Path):
    env, status_path, remote_root, _calls_path = _layout(tmp_path)
    env["FAKE_PUBLIC_READABLE"] = "1"

    result = _run(env)

    assert result.returncode != 0
    assert "archive is anonymously readable" in result.stderr
    assert json.loads(status_path.read_text())["status"] == "failed"
    assert not list(remote_root.rglob("RECEIPT.json"))


def test_stale_snapshot_and_stale_private_run_fail_or_clean_safely(tmp_path: Path):
    env, status_path, remote_root, _calls_path = _layout(tmp_path)
    work_root = Path(env["SEICHE_OFFSITE_BACKUP_WORK_DIR"])
    work_root.mkdir()
    stale_run = work_root / ".run-20260821T052000Z-1234.ABC123"
    stale_run.mkdir()
    (stale_run / "plaintext").write_text("root-private interrupted restore\n")
    env["FAKE_NOW_EPOCH"] = "1800200000"

    result = _run(env)

    assert result.returncode != 0
    assert "newest completed snapshot is stale" in result.stderr
    assert not stale_run.exists()
    assert not status_path.exists()
    assert not any(remote_root.iterdir())


def test_snapshot_member_larger_than_unit_memory_is_hashed_streamingly(tmp_path: Path):
    env, status_path, remote_root, _calls_path = _layout(tmp_path)
    payload = Path(env["FAKE_SNAPSHOT_PATH"]) / "seiche.dump"
    with payload.open("wb") as handle:
        handle.truncate(1024 * 1024 * 1024 + 1)
    _rewrite_inventory(Path(env["FAKE_SNAPSHOT_PATH"]))
    env["FAKE_AVAILABLE_KB"] = "1"

    result = _run(env, data_limit_bytes=256 * 1024 * 1024)

    assert result.returncode != 0
    assert "lacks the bounded encryption and restore capacity" in result.stderr
    assert "MemoryError" not in result.stderr
    assert "handle.read(1024 * 1024)" in SCRIPT.read_text()
    assert not status_path.exists()
    assert not any(remote_root.iterdir())


def test_systemd_installer_and_rollback_contracts_are_closed():
    script = SCRIPT.read_text()
    service = SERVICE.read_text()
    timer = TIMER.read_text()
    installer = INSTALLER.read_text()
    wrapper = DEPLOY_WRAPPER.read_text()

    assert "--force-aead --aead-algo OCB" in script
    assert "--s2k-digest-algo SHA512" in script
    assert '"$RCLONE_BIN" copyto' in script
    for forbidden in (
        '"$RCLONE_BIN" delete',
        '"$RCLONE_BIN" purge',
        '"$RCLONE_BIN" sync',
    ):
        assert forbidden not in script
    assert "CREDENTIAL_ENV_FILE" in script
    assert "seiche/market-backups/v1" in script
    assert "snapshot, deployed receipt, and controller release SHAs differ" in script
    assert "--exclusive --nonblock 8" in script
    assert "refusing to clean a mounted stale offsite run" in script
    assert "require_empty_canary_version_history" in script
    assert "--data-urlencode 'versions='" in script
    assert '"ciphertext_version_id"' in script
    assert "download_exact_version" in script
    assert "is anonymously readable" in script

    assert "EnvironmentFile=/root/.config/anchor/object-storage.env" in service
    assert "EnvironmentFile=/etc/seiche/offsite-backup.env" in service
    assert "EnvironmentFile=/etc/seiche/release.env" in service
    assert "AssertPathExists=/etc/seiche/release.env" in service
    assert "User=root\n" in service
    assert "Group=root\n" in service
    assert "SupplementaryGroups=" not in service
    assert "ExecStart=/usr/bin/bash /home/seiche/app" not in service
    assert "AssertFileIsExecutable=/home/seiche/app" not in service
    assert "ReadOnlyPaths=/home/seiche/app" not in service
    assert (
        "AssertFileIsExecutable=/etc/seiche/libexec/"
        "seiche-market-offsite-backup.sh" in service
    )
    assert (
        "ExecStart=/usr/bin/bash /etc/seiche/libexec/"
        "seiche-market-offsite-backup.sh" in service
    )
    assert "/home/seiche/app/ops/deploy/seiche-market-offsite-backup.sh" not in service
    assert "ProtectSystem=strict" in service
    assert "CapabilityBoundingSet=\n" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in service
    assert "ReadWritePaths=/var/cache/seiche-market-offsite-backup" in service
    assert "KillMode=control-group" in service
    assert "OOMPolicy=stop" in service
    assert "OnCalendar=*-*-* 05:20:00 UTC" in timer
    assert "RandomizedDelaySec=20m" in timer
    assert "FixedRandomDelay=true" in timer
    assert "Persistent=true" in timer

    assert "offsite backup configuration is incomplete or unsafe" in installer
    assert "root:root:600:1" in installer
    assert "root:root:400:1" in installer
    assert "offsite_canary_receipt_is_valid" in installer
    assert "SEICHE_OFFSITE_BACKUP_KEY_ID" in installer
    assert "SEICHE_OFFSITE_BACKUP_DESTINATION_ID" in installer
    assert "scheduled offsite backup lacks a valid canary receipt" in installer
    assert "systemctl disable --now seiche-market-offsite-backup.timer" in installer
    assert "systemctl enable seiche-market-offsite-backup.timer" in installer
    assert "OFFSITE_APP_SHA" in installer and "OFFSITE_DEPLOYED_SHA" in installer
    assert (
        "OFFSITE_SCRIPT_INSTALLED=/etc/seiche/libexec/"
        "seiche-market-offsite-backup.sh" in installer
    )
    assert (
        "install_runtime_shell_helper \\\n"
        '    "$OFFSITE_SCRIPT_SOURCE" "$OFFSITE_SCRIPT_INSTALLED"' in installer
    )

    assert "OFFSITE_TIMER_WAS_ACTIVE" in wrapper
    assert "OFFSITE_TIMER_WAS_ENABLED" in wrapper
    assert "seiche-market-offsite-backup.service" in wrapper
    assert "seiche-market-offsite-backup.timer" in wrapper
    assert "candidate offsite backup timer remains enabled after rollback" in wrapper
    assert "/etc/seiche/libexec/seiche-market-offsite-backup.sh" in wrapper
    assert (
        wrapper.count(
            "seiche-market-offsite-backup.timer seiche-market-offsite-backup.service"
        )
        >= 3
    )


def test_signed_asset_pipeline_requires_executable_offsite_script() -> None:
    installer = INSTALLER.read_text()
    wrapper = DEPLOY_WRAPPER.read_text()

    assert '"ops/deploy/seiche-market-offsite-backup.sh": "100755"' in wrapper
    assert 'git_mode not in {"100644", "100755"}' in wrapper
    assert 'mode = 0o755 if git_mode == "100755" else 0o644' in installer
    assert (
        "install_runtime_shell_helper \\\n"
        '    "$OFFSITE_SCRIPT_SOURCE" "$OFFSITE_SCRIPT_INSTALLED"'
    ) in installer
