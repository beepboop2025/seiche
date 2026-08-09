"""Producer contracts for the Mac-to-box NYX heartbeat."""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCER = ROOT / "ops" / "fleet-watchdog" / "fleet-mac-heartbeat.sh"


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\nset -u\n" + body)
    path.chmod(0o755)
    return path


def _run_producer(
    tmp_path: Path,
    *,
    launchctl_output: str,
    launchctl_status: int = 0,
    ssh_status: int = 0,
    box: str = "root@box.invalid",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    config = home / ".config" / "fleet-watchdog"
    config.mkdir(parents=True)
    (config / "box").write_text(box)

    _executable(
        bin_dir / "launchctl",
        """printf '%s' "${FAKE_LAUNCHCTL_OUTPUT:-}"
exit "${FAKE_LAUNCHCTL_STATUS:-0}"
""",
    )
    _executable(
        bin_dir / "ssh",
        """for arg in "$@"; do
    printf '%s\\0' "$arg"
done > "$FAKE_SSH_ARGS"
exit "${FAKE_SSH_STATUS:-0}"
""",
    )

    ssh_args = tmp_path / "ssh.args"
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "FAKE_LAUNCHCTL_OUTPUT": launchctl_output,
        "FAKE_LAUNCHCTL_STATUS": str(launchctl_status),
        "FAKE_SSH_ARGS": str(ssh_args),
        "FAKE_SSH_STATUS": str(ssh_status),
    }
    result = subprocess.run(
        ["/bin/bash", str(PRODUCER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    raw_args = ssh_args.read_bytes().split(b"\0") if ssh_args.exists() else []
    args = [arg.decode() for arg in raw_args if arg]
    return result, args


@pytest.mark.parametrize(
    ("launchctl_output", "launchctl_status", "expected"),
    [
        pytest.param(
            "gui/501/bridge = {\n"
            "\tstate = running\n"
            "\tendpoints = {\n"
            "\t\tstate = active\n"
            "\t}\n"
            "\tevent triggers = {\n"
            "\t\tstate = active\n"
            "\t}\n"
            "}\n",
            0,
            "nyx=1",
            id="running-with-nested-states",
        ),
        pytest.param(
            "gui/501/bridge = {\n\tstate = not running\n}\n",
            0,
            "nyx=0",
            id="loaded-not-running",
        ),
        pytest.param("gui/501/bridge = {\n}\n", 0, "nyx=0", id="no-state"),
        pytest.param("", 113, "nyx=0", id="missing"),
    ],
)
def test_producer_reports_only_a_running_launchd_job_as_healthy(
    tmp_path, launchctl_output, launchctl_status, expected
):
    result, ssh_args = _run_producer(
        tmp_path,
        launchctl_output=launchctl_output,
        launchctl_status=launchctl_status,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert ssh_args[:5] == [
        "-o",
        "ConnectTimeout=15",
        "-o",
        "BatchMode=yes",
        "--",
    ]
    assert ssh_args[5] == "root@box.invalid"
    assert f"printf '%s\\n' '{expected}'" in ssh_args[6]


def test_failed_launchctl_cannot_claim_running_from_its_output(tmp_path):
    result, ssh_args = _run_producer(
        tmp_path,
        launchctl_output="\tstate = running\n",
        launchctl_status=113,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "printf '%s\\n' 'nyx=0'" in ssh_args[6]


@pytest.mark.parametrize(
    "launchctl_output",
    [
        "\t\tstate = running\n",
        "\tstate = running\n\tstate = running\n",
        "\tstate = waiting\n\t\tstate = running\n",
    ],
)
def test_ambiguous_or_nested_launchd_state_fails_closed(tmp_path, launchctl_output):
    result, ssh_args = _run_producer(
        tmp_path,
        launchctl_output=launchctl_output,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "printf '%s\\n' 'nyx=0'" in ssh_args[6]


def test_ssh_failure_is_the_producer_exit_status(tmp_path):
    result, ssh_args = _run_producer(
        tmp_path,
        launchctl_output="\tstate = running\n",
        ssh_status=255,
    )

    assert ssh_args
    assert result.returncode == 255


@pytest.mark.parametrize("existing_kind", ["hard-link", "symlink"])
def test_remote_write_is_atomic_and_receiver_compatible(tmp_path, existing_kind):
    result, ssh_args = _run_producer(
        tmp_path,
        launchctl_output="\tstate = running\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    remote = ssh_args[6]
    assert "umask 077" in remote
    assert '[ ! -L "$heartbeat_dir" ] || exit 1' in remote
    assert "install -d -o root -g root -m 0700" in remote
    assert '[ -d "$heartbeat_dir" ] && [ ! -L "$heartbeat_dir" ] || exit 1' in remote
    assert 'mktemp "$heartbeat_dir/.mac.heartbeat.XXXXXX"' in remote
    assert 'chown 0:0 "$heartbeat_tmp"' in remote
    assert 'chmod 0644 "$heartbeat_tmp"' in remote
    assert 'mv -fT -- "$heartbeat_tmp" "$heartbeat_path"' in remote
    assert "trap cleanup EXIT" in remote
    assert "trap 'exit 1' HUP INT TERM" in remote
    assert remote.index("printf ") < remote.index("chown ")
    assert remote.index("chown ") < remote.index("chmod ")
    assert remote.index("chmod ") < remote.index("mv -fT ")
    assert '> "$heartbeat_path"' not in remote

    # Exercise the captured Linux command locally. These wrappers retain the
    # ownership and GNU mv contracts while avoiding a privileged chown and
    # macOS's lack of mv -T in the test process.
    remote_dir = tmp_path / "remote-fleet-watchdog"
    remote_dir.mkdir()
    victim = tmp_path / "do-not-overwrite"
    victim.write_text("old\n")
    heartbeat = remote_dir / "mac.heartbeat"
    if existing_kind == "hard-link":
        os.link(victim, heartbeat)
    else:
        heartbeat.symlink_to(victim)

    remote_bin = tmp_path / "remote-bin"
    remote_bin.mkdir()
    calls = tmp_path / "remote.calls"
    _executable(
        remote_bin / "install",
        """[ "$#" -eq 8 ] || exit 90
[ "$1 $2 $3 $4 $5 $6 $7" = "-d -o root -g root -m 0700" ] || exit 91
/bin/mkdir -p "$8"
/bin/chmod 0700 "$8"
printf 'install %s\\n' "$*" >> "$FAKE_REMOTE_CALLS"
""",
    )
    _executable(
        remote_bin / "chown",
        """[ "$#" -eq 2 ] || exit 92
[ "$1" = "0:0" ] || exit 93
printf 'chown %s\\n' "$*" >> "$FAKE_REMOTE_CALLS"
""",
    )
    _executable(
        remote_bin / "mv",
        """[ "$#" -eq 4 ] || exit 94
[ "$1 $2" = "-fT --" ] || exit 95
printf 'before=' >> "$FAKE_REMOTE_CALLS"
/bin/cat "$4" >> "$FAKE_REMOTE_CALLS"
printf 'incoming=' >> "$FAKE_REMOTE_CALLS"
/bin/cat "$3" >> "$FAKE_REMOTE_CALLS"
/bin/mv -f "$3" "$4"
""",
    )
    local_remote = remote.replace(
        "heartbeat_dir=/var/lib/fleet-watchdog",
        f"heartbeat_dir={shlex.quote(str(remote_dir))}",
        1,
    )
    execution = subprocess.run(
        ["/bin/bash", "-c", local_remote],
        env={
            **os.environ,
            "PATH": f"{remote_bin}:{os.environ.get('PATH', '')}",
            "FAKE_REMOTE_CALLS": str(calls),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert execution.returncode == 0, execution.stdout + execution.stderr
    assert heartbeat.read_text() == "nyx=1\n"
    assert victim.read_text() == "old\n"
    assert not heartbeat.is_symlink()
    heartbeat_stat = heartbeat.stat()
    assert stat.S_ISREG(heartbeat_stat.st_mode)
    assert stat.S_IMODE(heartbeat_stat.st_mode) == 0o644
    assert heartbeat_stat.st_nlink == 1
    assert list(remote_dir.glob(".mac.heartbeat.*")) == []
    remote_calls = calls.read_text()
    assert "chown 0:0 " in remote_calls
    assert "before=old\nincoming=nyx=1\n" in remote_calls


def test_remote_write_rejects_a_symlinked_destination_directory(tmp_path):
    result, ssh_args = _run_producer(
        tmp_path,
        launchctl_output="\tstate = running\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    heartbeat_dir = tmp_path / "remote-fleet-watchdog"
    heartbeat_dir.symlink_to(victim_dir, target_is_directory=True)
    remote = ssh_args[6].replace(
        "heartbeat_dir=/var/lib/fleet-watchdog",
        f"heartbeat_dir={shlex.quote(str(heartbeat_dir))}",
        1,
    )

    execution = subprocess.run(
        ["/bin/bash", "-c", remote],
        text=True,
        capture_output=True,
        check=False,
    )

    assert execution.returncode != 0
    assert not (victim_dir / "mac.heartbeat").exists()
    assert list(victim_dir.glob(".mac.heartbeat.*")) == []


def test_box_contents_remain_one_ssh_host_argument(tmp_path):
    marker = tmp_path / "must-not-exist"
    hostile_box = f"root@box.invalid; touch {marker}"

    result, ssh_args = _run_producer(
        tmp_path,
        launchctl_output="\tstate = running\n",
        box=hostile_box,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(ssh_args) == 7
    assert ssh_args[4] == "--"
    assert ssh_args[5] == hostile_box
    assert not marker.exists()
