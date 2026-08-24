"""Host-free authority-fence tests for the Phase-5 Railway cutover."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from seiche import stateful_cutover as cutover
from seiche import stateful_migration as migration


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "deploy" / "seiche-railway-cutover-fence.sh"
EDGE_SCRIPT = ROOT / "ops" / "deploy" / "seiche-railway-edge-mode.sh"
COMMIT = "a" * 40
TREE = "b" * 40


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _fake_systemctl(path: Path) -> Path:
    return _executable(
        path,
        f"""#!{sys.executable}
import hashlib
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_SYSTEMCTL_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
command = sys.argv[1]
arguments = [value for value in sys.argv[2:] if value != "--quiet"]

def save():
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

if command == "is-active":
    raise SystemExit(0 if state[arguments[-1]]["active"] else 3)
if command == "is-enabled":
    unit = arguments[-1]
    if state[unit]["masked"]:
        print("masked-runtime")
        raise SystemExit(0)
    if state[unit]["enabled"]:
        print("enabled")
        raise SystemExit(0)
    print("disabled")
    raise SystemExit(1)
if command == "is-failed":
    raise SystemExit(1)
if command in {{"stop", "disable", "enable"}}:
    unit = arguments[-1]
    state.setdefault(unit, {{"active": False, "enabled": False, "masked": False}})
    if command == "stop":
        state[unit]["active"] = False
    else:
        state[unit]["enabled"] = command == "enable"
    save()
    raise SystemExit(0)
if command in {{"mask", "unmask"}}:
    unit = arguments[-1]
    state[unit]["masked"] = command == "mask"
    save()
    raise SystemExit(0)
if command == "start":
    unit = arguments[-1]
    if unit == "seiche-market-backup.service":
        snapshot = Path(os.environ["FAKE_BACKUP_DIR"]) / "20260823T031000Z"
        snapshot.mkdir()
        members = {{
            "seiche.dump": b"postgres-dump\\n",
            "var-lib-seiche.tgz": b"state-archive\\n",
            "palimpsest-china.tgz": b"palimpsest-china-state-archive\\n",
            "palimpsest-china-state.json": (
                b'{{"schema":"seiche.palimpsest-china-activation-state.v1"}}\\n'
            ),
            "api-data.tgz": b"api-archive\\n",
            "table-counts.txt": b"10|20|30|40\\n",
            "deployed-sha.txt": (os.environ["FAKE_EXPECTED_SHA"] + "\\n").encode(),
            "manifest.env": b"schema=fixture\\n",
        }}
        inventory = []
        for name in {list(migration._BACKUP_MEMBERS)!r}:
            body = members[name]
            (snapshot / name).write_bytes(body)
            inventory.append(f"{{hashlib.sha256(body).hexdigest()}}  {{name}}")
        (snapshot / "SHA256SUMS").write_text("\\n".join(inventory) + "\\n", encoding="ascii")
    elif unit == "seiche-market-restore-check.service":
        Path(os.environ["FAKE_RESTORE_STATUS"]).write_text(
            "schema=seiche.market-backup-restore-check.v5\\n"
            "snapshot=20260823T031000Z\\n"
            "source_backup_schema=seiche.market-backup.v4\\n"
            f"deployed_sha={{os.environ['FAKE_EXPECTED_SHA']}}\\n"
            "database_restore=pass\\n"
            "state_archive_restore=pass\\n"
            "palimpsest_china_state_archive_restore=verified\\n"
            "palimpsest_china_state_audit_contract=seiche.palimpsest-china-activation-state.v1\\n"
            "api_data_archive_restore=pass\\n",
            encoding="utf-8",
        )
    else:
        state[unit]["active"] = True
        save()
    raise SystemExit(0)
raise SystemExit(64)
""",
    )


def _fixture(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    root = tmp_path.resolve()
    app = root / "app"
    receipts = root / "receipts"
    backups = root / "backups"
    state_dir = root / "cutover"
    for directory in (app, receipts, backups):
        directory.mkdir()
    deployed = root / "deployed-sha"
    deployed.write_text(COMMIT + "\n", encoding="ascii")
    for suffix in ("release", "recovery"):
        (receipts / f"{COMMIT}.{suffix}.json").write_bytes(
            _canonical({"schema": f"fixture.{suffix}"})
        )
    shadow = root / "shadow.json"
    shadow.write_bytes(_canonical({"schema": "fixture.shadow"}))
    restore = root / "restore.status"
    systemctl_state = root / "systemctl.json"
    initial = {
        name: {
            "active": name in {"seiche-api.service", "seiche-market-worker.service"},
            "enabled": name.endswith(".timer") or name == "seiche-api.service",
            "masked": False,
        }
        for name in cutover.FENCED_UNITS
    }
    systemctl_state.write_text(json.dumps(initial, sort_keys=True), encoding="utf-8")
    fake_systemctl = _fake_systemctl(root / "systemctl")
    fake_git = _executable(
        root / "git",
        "#!/bin/sh\n"
        f"if [ \"${{4:-}}\" = HEAD ]; then printf '%s\\n' '{COMMIT}'; "
        f"else printf '%s\\n' '{TREE}'; fi\n",
    )
    fake_date = _executable(
        root / "date",
        '#!/bin/sh\ncase "$*" in\n'
        "  *'-d +4 hours'*) printf '%s\\n' '2026-08-23T07:00:00Z' ;;\n"
        "  *) printf '%s\\n' '2026-08-23T03:00:00Z' ;;\n"
        "esac\n",
    )
    fake_flock = _executable(root / "flock", "#!/bin/sh\nexit 0\n")
    environment = {
        **os.environ,
        "SEICHE_CUTOVER_TEST_MODE": "1",
        "SEICHE_CUTOVER_APP_DIR": str(app),
        "SEICHE_CUTOVER_DEPLOYED_STATE": str(deployed),
        "SEICHE_CUTOVER_CONTROL_RECEIPTS": str(receipts),
        "SEICHE_CUTOVER_SHADOW_RECEIPT": str(shadow),
        "SEICHE_CUTOVER_BACKUP_DIR": str(backups),
        "SEICHE_CUTOVER_RESTORE_STATUS": str(restore),
        "SEICHE_CUTOVER_STATE_DIR": str(state_dir),
        "SEICHE_CUTOVER_LOCK_PATH": str(root / "cutover.lock"),
        "SEICHE_CUTOVER_SYSTEMCTL": str(fake_systemctl),
        "SEICHE_CUTOVER_PYTHON": sys.executable,
        "SEICHE_CUTOVER_FLOCK": str(fake_flock),
        "SEICHE_CUTOVER_GIT": str(fake_git),
        "SEICHE_CUTOVER_DATE": str(fake_date),
        "FAKE_SYSTEMCTL_STATE": str(systemctl_state),
        "FAKE_BACKUP_DIR": str(backups),
        "FAKE_RESTORE_STATUS": str(restore),
        "FAKE_EXPECTED_SHA": COMMIT,
    }
    return environment, state_dir, systemctl_state


def _run(
    environment: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_prepare_rollback_and_activation_boundary(tmp_path: Path) -> None:
    environment, state_dir, systemctl_state = _fixture(tmp_path)

    prepared = _run(environment, "prepare", COMMIT)
    assert prepared.returncode == 0, prepared.stderr
    fence_body = (state_dir / "AUTHORITY-FENCE.json").read_bytes()
    fence = json.loads(fence_body)
    assert fence["snapshot"]["id"] == "20260823T031000Z"
    assert fence["snapshot"]["source_revision"] == COMMIT
    assert set(fence["units"]) == set(cutover.FENCED_UNITS)
    assert all(row["runtime_masked"] is True for row in fence["units"].values())
    frozen_state = json.loads(systemctl_state.read_text(encoding="utf-8"))
    assert all(not row["active"] and row["masked"] for row in frozen_state.values())

    rollback_environment = {
        **environment,
        "SEICHE_CUTOVER_ROLLBACK_CONFIRM": "RAILWAY_CANDIDATE_STOPPED_NO_WRITERS",
    }
    rolled_back = _run(rollback_environment, "rollback", COMMIT)
    assert rolled_back.returncode == 0, rolled_back.stderr
    restored_state = json.loads(systemctl_state.read_text(encoding="utf-8"))
    assert restored_state["seiche-api.service"] == {
        "active": True,
        "enabled": True,
        "masked": False,
    }
    assert restored_state["seiche-source-worker.service"] == {
        "active": False,
        "enabled": False,
        "masked": False,
    }

    activation = {
        "schema": cutover.ACTIVATION_RECEIPT_SCHEMA,
        "commit": COMMIT,
        "request_id": "1" * 64,
        "candidate_receipt_sha256": "2" * 64,
        "grant_sha256": "3" * 64,
        "fence_sha256": hashlib.sha256(fence_body).hexdigest(),
        "railway": {"deployment_id": "fixture"},
        "authority": {
            "mode": "production",
            "source": "railway",
            "hetzner_writers_frozen": True,
            "railway_writers_started": True,
            "public_traffic_enabled": True,
        },
        "workers": {
            "market": {"command": ["python", "market-worker"], "process_started": True},
            "source": {"command": ["python", "source-worker"], "process_started": True},
        },
        "public": {
            "base_url": "https://api.seiche.info",
            "probe_sha256": "4" * 64,
        },
        "activated_at": "2026-08-23T03:15:00Z",
        "workers_started_at": "2026-08-23T03:15:01Z",
        "research_only": True,
        "can_publish": False,
        "can_execute": False,
    }
    activation_path = tmp_path / "activation.json"
    activation_body = _canonical(activation)
    activation_path.write_bytes(activation_body)
    finalized = _run(
        environment,
        "finalize",
        COMMIT,
        str(activation_path),
        hashlib.sha256(activation_body).hexdigest(),
    )
    assert finalized.returncode == 0, finalized.stderr

    refused = _run(rollback_environment, "rollback", COMMIT)
    assert refused.returncode != 0
    assert "forbidden after Railway activation" in refused.stderr


def test_edge_switch_is_receipted_secret_safe_and_pre_activation_only(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    state_dir = root / "state"
    state_dir.mkdir()
    now = datetime.now(UTC).replace(microsecond=0)
    fence = {
        "schema": cutover.FENCE_SCHEMA,
        "commit": COMMIT,
        "authority": {
            "source": "hetzner",
            "state": "frozen",
            "writers_frozen": True,
            "api_stopped": True,
            "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        },
        "can_activate_railway": True,
    }
    (state_dir / "AUTHORITY-FENCE.json").write_bytes(_canonical(fence))
    caddyfile = root / "Caddyfile"
    caddyfile.write_text("fixture\n", encoding="utf-8")
    calls = root / "calls.log"
    fake_caddy = _executable(
        root / "caddy",
        f"#!/bin/sh\nprintf 'caddy %s\\n' \"$1\" >>'{calls}'\nexit 0\n",
    )
    fake_systemctl = _executable(
        root / "systemctl",
        f"#!/bin/sh\nprintf 'systemctl %s\\n' \"$*\" >>'{calls}'\nexit 0\n",
    )
    fake_flock = _executable(root / "flock", "#!/bin/sh\nexit 0\n")
    deployment = "11111111-1111-4111-8111-111111111111"
    fake_curl = _executable(
        root / "curl",
        f"""#!{sys.executable}
from pathlib import Path
import sys

arguments = sys.argv[1:]
body = Path(arguments[arguments.index("--output") + 1])
headers = Path(arguments[arguments.index("--dump-header") + 1])
body.write_text('{{"version":"0.11.0"}}\\n', encoding="utf-8")
headers.write_text(
    "HTTP/2 200\\r\\n"
    "X-Seiche-Railway-Authority: candidate\\r\\n"
    "X-Seiche-Railway-Deployment: {deployment}\\r\\n"
    "X-Seiche-Release-SHA: {COMMIT}\\r\\n\\r\\n",
    encoding="iso-8859-1",
)
print("200", end="")
""",
    )
    edge_env = root / "etc" / "railway-edge.env"
    dropin = root / "systemd" / "railway-edge.conf"
    token = "edge-token-" + "x" * 40
    environment = {
        **os.environ,
        "SEICHE_EDGE_TEST_MODE": "1",
        "SEICHE_EDGE_CADDYFILE": str(caddyfile),
        "SEICHE_EDGE_ENV_FILE": str(edge_env),
        "SEICHE_EDGE_DROPIN": str(dropin),
        "SEICHE_EDGE_STATE_DIR": str(state_dir),
        "SEICHE_EDGE_SYSTEMCTL": str(fake_systemctl),
        "SEICHE_EDGE_CADDY": str(fake_caddy),
        "SEICHE_EDGE_CURL": str(fake_curl),
        "SEICHE_EDGE_PYTHON": sys.executable,
        "SEICHE_EDGE_FLOCK": str(fake_flock),
        "SEICHE_EDGE_LOCK_PATH": str(root / "edge.lock"),
        "SEICHE_EDGE_CONFIRM": "RAILWAY_CANDIDATE_RECEIPTED_READ_ONLY",
        "SEICHE_RAILWAY_ORIGIN": "https://fixture.up.railway.app",
        "SEICHE_RAILWAY_EDGE_TOKEN": token,
    }
    activated = subprocess.run(
        ["bash", str(EDGE_SCRIPT), "railway", COMMIT, deployment],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert activated.returncode == 0, activated.stderr
    assert edge_env.stat().st_mode & 0o777 == 0o600
    assert token in edge_env.read_text(encoding="utf-8")
    receipt = (state_dir / "edge-railway.json").read_text(encoding="utf-8")
    assert token not in receipt
    assert hashlib.sha256(token.encode()).hexdigest() in receipt
    assert "systemctl daemon-reload" in calls.read_text(encoding="utf-8")
    assert "systemctl restart caddy" in calls.read_text(encoding="utf-8")

    rollback_environment = {
        **environment,
        "SEICHE_EDGE_CONFIRM": "RAILWAY_CANDIDATE_STOPPED_NO_WRITERS",
    }
    restored = subprocess.run(
        ["bash", str(EDGE_SCRIPT), "local"],
        env=rollback_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert restored.returncode == 0, restored.stderr
    assert not edge_env.exists()
    assert not dropin.exists()

    (state_dir / "activation-ack.json").write_bytes(
        _canonical({"authority": "railway"})
    )
    refused = subprocess.run(
        ["bash", str(EDGE_SCRIPT), "local"],
        env=rollback_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert refused.returncode != 0
    assert "forbidden after Railway activation" in refused.stderr
