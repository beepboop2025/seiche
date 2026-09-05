"""Authority boundaries for HTTPS control and bounded PostgreSQL health probes."""

from __future__ import annotations

from pathlib import Path
import io
import json
import os
import re
import runpy
import signal
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
CUTOVER = ROOT / ".github" / "workflows" / "railway-stateful-cutover.yml"
RECOVERY = ROOT / ".github" / "workflows" / "railway-stateful-recovery.yml"
SHADOW = ROOT / ".github" / "workflows" / "railway-stateful-shadow.yml"
TELEGRAM = ROOT / ".github" / "workflows" / "railway-telegram.yml"
CADDY = ROOT / "ops" / "Caddyfile"

CONTROL_PATH = "/api/internal/v1/railway-control/commands"
RECOVERY_PATH = "/api/internal/v1/railway-control/recovery"
RESULT_MARKER = "SEICHE_RAILWAY_STATEFUL_RESULT_V1="


def _workflow(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_runtime_control_has_no_arbitrary_native_file_or_shell_transport() -> None:
    for path in (CUTOVER, RECOVERY):
        text = _workflow(path)
        assert re.search(r"\brailway\s+ssh\b", text, re.IGNORECASE) is None
        assert (
            re.search(
                r"\brailway\s+volume\b[^\n]*(?:\\\n[^\n]*)*\bfiles\b",
                text,
                re.IGNORECASE,
            )
            is None
        )
        assert re.search(r"\bsftp\b|\bscp\b", text, re.IGNORECASE) is None


def test_postgres_probe_refuses_other_commands_and_instances(tmp_path, monkeypatch):
    workflow = _workflow(RECOVERY)
    scripts = re.findall(r"<<'PYPROBE'\n(.*?)\n          PYPROBE", workflow, re.S)
    assert len(scripts) == 2 and scripts[0] == scripts[1]
    setup = textwrap.dedent(scripts[0])
    instance = "00000000-0000-4000-8000-000000000001"
    for key, value in {
        "PROBE_SSH_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n",
        "RAILWAY_PROJECT_ID": "project",
        "RAILWAY_ENVIRONMENT_ID": "environment",
        "RAILWAY_POSTGRES_SERVICE_ID": "postgres",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_PATH": str(tmp_path / "job-path"),
        "GITHUB_ENV": str(tmp_path / "job-env"),
    }.items():
        monkeypatch.setenv(key, value)
    response = {
        "data": {
            "environment": {"id": "environment", "projectId": "project"},
            "serviceInstance": {
                "id": instance,
                "environmentId": "environment",
                "serviceId": "postgres",
            },
        }
    }
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda args, **kw: (
            "SSH_AGENT_PID=12345;\n" if args[0] == "ssh-agent" else json.dumps(response)
        ),
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0)
    )
    exec(compile(setup, "probe-setup", "exec"), {})
    root = tmp_path / "postgres-health-probe"
    assert (root / "identity").stat().st_mode & 0o777 == 0o600
    assert "SSH_AUTH_SOCK=" in (tmp_path / "job-env").read_text()
    assert (root / "agent-pid").read_text() == "12345"
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(returncode=0),
    )
    command = (
        "PGHOST=localhost PGPORT=5432 PGSSLMODE=disable psql -t -A -F',' -q -c \""
        "SELECT archived_count, coalesce(last_archived_time::text, ''), failed_count, "
        "coalesce(last_failed_time::text, ''), coalesce(((pg_last_committed_xact()).timestamp)::text, '') "
        'FROM pg_stat_archiver"'
    ).encode()
    target = instance + "@ssh.railway.com"
    args = ["ssh", "-o", "StrictHostKeyChecking=accept-new", target, "sh", "-s"]
    for argv, body, allowed in (
        (args, command, True),
        (args, b"printenv", False),
        (args, b"x" * 16385, False),
        (args[:3] + ["other@ssh.railway.com"] + args[4:], command, False),
        (args + ["extra"], command, False),
    ):
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(body)))
        before = len(calls)
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(root / "ssh"), run_name="__main__")
        assert (result.value.code == 0) is allowed
        assert len(calls) - before == int(allowed)
    assert calls[0][0][-3:] == [target, "sh", "-s"]
    assert "StrictHostKeyChecking=yes" in calls[0][0]
    assert calls[0][1]["input"] == command


def test_postgres_probe_refuses_wrong_project_before_writing_key(tmp_path, monkeypatch):
    setup = textwrap.dedent(
        re.findall(r"<<'PYPROBE'\n(.*?)\n          PYPROBE", _workflow(RECOVERY), re.S)[
            0
        ]
    )
    for key, value in {
        "PROBE_SSH_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture",
        "RAILWAY_PROJECT_ID": "expected",
        "RAILWAY_ENVIRONMENT_ID": "env",
        "RAILWAY_POSTGRES_SERVICE_ID": "pg",
        "RUNNER_TEMP": str(tmp_path),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *a, **kw: json.dumps(
            {"data": {"environment": {"id": "env", "projectId": "wrong"}}}
        ),
    )
    with pytest.raises(AssertionError):
        exec(compile(setup, "probe-setup", "exec"), {})
    assert not (tmp_path / "postgres-health-probe").exists()


@pytest.fixture
def short_socket_root():
    # AF_UNIX paths have a smaller limit than macOS pytest temp directories.
    with tempfile.TemporaryDirectory(prefix="seiche-probe-", dir="/tmp") as directory:
        yield Path(directory)


def test_postgres_probe_loads_secret_without_final_newline(short_socket_root, monkeypatch):
    """GitHub's secret transport must still yield an OpenSSH-readable identity."""
    tmp_path = short_socket_root
    generated = tmp_path / "generated-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(generated)],
        check=True,
    )
    setup = textwrap.dedent(
        re.findall(r"<<'PYPROBE'\n(.*?)\n          PYPROBE", _workflow(RECOVERY), re.S)[0]
    )
    for key, value in {
        "PROBE_SSH_KEY": generated.read_text().rstrip().replace("\n", "\r\n"),
        "RAILWAY_PROJECT_ID": "project",
        "RAILWAY_ENVIRONMENT_ID": "env",
        "RAILWAY_POSTGRES_SERVICE_ID": "pg",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_PATH": str(tmp_path / "job-path"),
        "GITHUB_ENV": str(tmp_path / "job-env"),
    }.items():
        monkeypatch.setenv(key, value)
    response = {"data": {
        "environment": {"id": "env", "projectId": "project"},
        "serviceInstance": {"id": "00000000-0000-4000-8000-000000000001",
                            "environmentId": "env", "serviceId": "pg"},
    }}
    real_check_output = subprocess.check_output
    monkeypatch.setattr(subprocess, "check_output", lambda args, **kw:
        json.dumps(response) if args[0] == "railway" else real_check_output(args, **kw))
    try:
        exec(compile(setup, "probe-setup", "exec"), {})
        installed = tmp_path / "postgres-health-probe" / "identity"
        actual_public = real_check_output(["ssh-keygen", "-y", "-f", str(installed)], text=True)
        expected_public = real_check_output(["ssh-keygen", "-y", "-f", str(generated)], text=True)
        assert actual_public == expected_public
    finally:
        pid_file = tmp_path / "postgres-health-probe" / "agent-pid"
        if pid_file.exists():
            os.kill(int(pid_file.read_text()), signal.SIGTERM)


def test_detached_checkout_bundles_advertise_the_exact_head() -> None:
    for path in (SHADOW, CUTOVER, TELEGRAM):
        text = _workflow(path)
        source_var = "SOURCE_SHA" if path == CUTOVER else "GITHUB_SHA"
        assert f'test "$(git rev-parse \'HEAD^{{commit}}\')" = "${source_var}"' in text
        assert 'git bundle create "$UPLOAD_ROOT/source.bundle" HEAD' in text
        assert (
            'git bundle create "$UPLOAD_ROOT/source.bundle" "$GITHUB_SHA"' not in text
        )
        assert 'git bundle list-heads "$UPLOAD_ROOT/source.bundle"' in text
        assert f'"${source_var} HEAD"' in text


def test_activation_uses_attested_input_and_signed_origin_command() -> None:
    text = _workflow(CUTOVER)
    assert "authority_fence_base64:" in text
    assert "candidate_run_id:" in text
    assert "gh attestation verify" in text
    assert (
        "--signer-workflow beepboop2025/seiche/.github/workflows/railway-stateful-cutover.yml"
        in text
    )
    assert 'find "$EVIDENCE_ROOT" -mindepth 1 ! -type f' in text
    assert "SEICHE_RAILWAY_ACTIVATION_SIGNING_KEY_PEM" in text
    assert "control.prepare_unsigned_command(" in text
    assert "control.ACTIVATION_OPERATION" in text
    assert f'"$RAILWAY_ORIGIN{CONTROL_PATH}"' in text
    assert '--header "X-Seiche-Edge-Token: $RAILWAY_EDGE_TOKEN"' in text
    assert "actions: write" in text
    assert "railway-stateful-recovery.yml/dispatches" in text
    assert "SUBMISSION_REPLICA_ID" in text
    assert "item.logged_at_unix_ns" in text
    assert RESULT_MARKER in text
    assert "--filter 'SEICHE_RAILWAY_STATEFUL_RESULT_V1='" in text


def test_recovery_uses_masked_bounded_capability_and_closed_member_set() -> None:
    text = _workflow(RECOVERY)
    logical = text.replace("\\\n", " ")
    match = re.search(
        r'download_bearer=\$\(cat "\$bearer_path"\).*?for member in (.*?); do',
        logical,
        re.DOTALL,
    )
    assert match is not None
    members = match.group(1).split()
    assert members == [
        "activation-receipt.json",
        "candidate-receipt.json",
        "shadow-receipt.json",
        "request.json",
        "recovery-receipt.json",
        "seiche.dump",
        "var-lib-seiche.tgz",
        "palimpsest-china.tgz",
        "palimpsest-china-state.json",
        "api-data.tgz",
        "table-counts.txt",
        "deployed-sha.txt",
        "manifest.env",
        "SHA256SUMS",
    ]
    assert "seiche.railway-recovery-export-request.v2" in text
    assert 'echo "::add-mask::$download_bearer"' in text
    assert "Authorization: Bearer $download_bearer" in text
    assert "download_bearer_sha256" in text
    assert "download_expires_at" in text
    assert f'"$RAILWAY_ORIGIN{RECOVERY_PATH}/$request_id/$member"' in text
    assert "SEICHE_RAILWAY_RECOVERY_SIGNING_KEY_PEM" in text
    assert "control.RECOVERY_EXPORT_OPERATION" in text
    assert "control.OFFSITE_ACKNOWLEDGMENT_OPERATION" in text
    assert "extract_latest_recovery_pair" in text
    assert text.count("current_replica()") == 2
    assert "SUBMISSION_REPLICA_ID" in text
    assert "item.logged_at_unix_ns" in text
    assert "activation_candidates" in text
    assert RESULT_MARKER in text
    assert "--filter 'SEICHE_RAILWAY_STATEFUL_RESULT_V1='" in text


def test_public_edge_refuses_all_stateful_control_route_names() -> None:
    text = CADDY.read_text(encoding="utf-8")
    start = text.index("@railway_stateful_control_private")
    end = text.index("# Agent Room", start)
    handler = text[start:end]
    for path in (
        CONTROL_PATH,
        RECOVERY_PATH,
        f"{RECOVERY_PATH}/*",
        "/api/internal/v1/stateful-control",
        "/api/internal/v1/recovery-exports/*",
    ):
        assert path in handler
    assert 'respond "not here" 404' in handler
    assert "reverse_proxy" not in handler
    assert "seiche_stateful_upstream" not in handler
