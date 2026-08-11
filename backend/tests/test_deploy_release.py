"""Release-boundary contracts, exercised without a host or external network."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[2]
CADDY_INSTALLER = ROOT / "ops" / "deploy" / "install-caddy.sh"
EXTERNAL_SMOKE = ROOT / "ops" / "deploy" / "external-route-smoke.sh"
CADDYFILE = ROOT / "ops" / "Caddyfile"
EXTERNAL_ROUTES = ROOT / "ops" / "deploy" / "external-smoke-routes.txt"
WORLD_MODEL_DELIVERY_INSTALLER = (
    ROOT / "ops" / "deploy" / "install-world-model-delivery-relay.sh"
)
FORCED_DEPLOY = ROOT / "ops" / "deploy" / "trigger-forced-deploy.sh"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-hetzner.yml"
BOX_UPDATE = ROOT / "ops" / "deploy" / "box-update.sh"
DEPLOY_WRAPPER = ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh"
RELEASE_POLLER = ROOT / "ops" / "deploy" / "seiche-release-poll.sh"
RELEASE_POLLER_INSTALLER = ROOT / "ops" / "deploy" / "install-release-poller.sh"
RELEASE_POLLER_SERVICE = ROOT / "ops" / "deploy" / "seiche-release-poll.service"
RELEASE_POLLER_TIMER = ROOT / "ops" / "deploy" / "seiche-release-poll.timer"
RELEASE_ALLOWED_SIGNERS = ROOT / "ops" / "deploy" / "release-allowed-signers"
MARKET_INSTALLER = ROOT / "ops" / "deploy" / "install-market-platform.sh"
PULL_UNIT = ROOT / "ops" / "deploy" / "seiche-pull.service"
PROMOTION_UNIT = ROOT / "ops" / "deploy" / "seiche-snapshot-promote.service"
LEGACY_UPDATE_RETIRER = (
    ROOT / "ops" / "deploy" / "retire-legacy-update-units.sh"
)
PYPROJECT = ROOT / "backend" / "pyproject.toml"


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -u\n" + body)
    path.chmod(0o755)
    return path


def _git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _release_signature_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("OpenSSH is required for the release-signature contract")

    repository = tmp_path / "signed-repository"
    _git("init", "-b", "main", str(repository), cwd=tmp_path)
    _git("config", "user.name", "Seiche Release", cwd=repository)
    _git("config", "user.email", "release@example.invalid", cwd=repository)
    signing_key = tmp_path / "release-signing-key"
    subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(signing_key)],
        check=True,
    )
    _git("config", "gpg.format", "ssh", cwd=repository)
    _git("config", "user.signingkey", str(signing_key), cwd=repository)
    _git("config", "commit.gpgsign", "true", cwd=repository)

    public_key = signing_key.with_suffix(".pub").read_text(encoding="ascii").split()
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text(
        f"release@example.invalid {public_key[0]} {public_key[1]}\n",
        encoding="ascii",
    )
    allowed_signers.chmod(0o444)
    runuser = _executable(
        tmp_path / "runuser",
        'if [ "$1" = -u ]; then shift 2; fi\n'
        'if [ "${1:-}" = -- ]; then shift; fi\n'
        'exec "$@"\n',
    )
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_APP_DIR": str(repository),
        "SEICHE_CONTROL_USER": "release-test",
        "SEICHE_CONTROL_RUNUSER": str(runuser),
        "SEICHE_CONTROL_PYTHON": sys.executable,
        "SEICHE_CONTROL_ALLOWED_SIGNERS": str(allowed_signers),
        "SEICHE_CONTROL_SIGNING_PRINCIPAL": "release@example.invalid",
        "SEICHE_CONTROL_SIGNER_UID": str(os.getuid()),
        "SEICHE_CONTROL_SIGNER_GID": str(os.getgid()),
        "SEICHE_CONTROL_SIGNER_MODE": "444",
        "SEICHE_CONTROL_SSH_KEYGEN": ssh_keygen,
    }
    return repository, env


def _commit_release(repository: Path, message: str, *, signed: bool = True) -> str:
    (repository / "release-marker.txt").write_text(f"{message}\n", encoding="utf-8")
    _git("add", "release-marker.txt", cwd=repository)
    command = ["git"]
    if not signed:
        command.extend(["-c", "commit.gpgsign=false"])
    command.extend(["commit", "-m", message])
    subprocess.run(command, cwd=repository, check=True, capture_output=True, text=True)
    return _git("rev-parse", "HEAD", cwd=repository)


def _verify_release_signature(
    environment: dict[str, str], target: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$SEICHE_POLLER"; verify_target_signature "$SEICHE_TARGET"',
        ],
        env=environment
        | {"SEICHE_POLLER": str(RELEASE_POLLER), "SEICHE_TARGET": target},
        text=True,
        capture_output=True,
        check=False,
    )


def _legacy_retirement_fixture(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    systemd_dir = tmp_path / "systemd"
    state_dir = tmp_path / "deploy-state"
    fake_state = tmp_path / "fake-systemctl"
    systemd_dir.mkdir()
    state_dir.mkdir()
    fake_state.mkdir()
    (systemd_dir / "timers.target.wants").mkdir()
    (systemd_dir / "multi-user.target.wants").mkdir()

    service = systemd_dir / "seiche-update.service"
    timer = systemd_dir / "seiche-update.timer"
    service.write_text("[Service]\nExecStart=/home/seiche/update.sh\n")
    timer.write_text("[Timer]\nOnCalendar=*-*-* 05:30:00 UTC\n")
    service.chmod(0o644)
    timer.chmod(0o644)
    (fake_state / "seiche-update.timer.active").touch()
    (fake_state / "seiche-update.timer.enabled").touch()
    (fake_state / "seiche-update.service.enabled").touch()
    (systemd_dir / "timers.target.wants" / timer.name).symlink_to(timer)
    (systemd_dir / "multi-user.target.wants" / service.name).symlink_to(service)

    fake_systemctl = _executable(
        tmp_path / "systemctl",
        """
state=${FAKE_SYSTEMCTL_STATE:?}
units=${SEICHE_SYSTEMD_DIR:?}
printf '%s\n' "$*" >>"$state/calls.log"
command=${1:?}
shift
case "$command" in
  is-active)
    unit=${1:?}
    if [ -f "$state/$unit.state" ]; then
      unit_state=$(cat "$state/$unit.state")
      echo "$unit_state"
      case "$unit_state" in
        active|activating|reloading|deactivating|maintenance|refreshing) exit 0 ;;
        *) exit 3 ;;
      esac
    fi
    if [ -f "$state/$unit.active" ]; then
      echo active
      exit 0
    fi
    echo inactive
    exit 3
    ;;
  is-enabled)
    unit=${1:?}
    if [ -L "$units/$unit" ] && [ "$(readlink "$units/$unit")" = /dev/null ]; then
      echo masked
      exit 1
    fi
    if [ -f "$state/$unit.enabled" ]; then
      echo enabled
      exit 0
    fi
    echo disabled
    exit 1
    ;;
  disable)
    for argument in "$@"; do
      case "$argument" in
        --*) ;;
        *)
          if [ -L "$units/$argument" ] \
              && [ "$(readlink "$units/$argument")" = /dev/null ]; then
            exit 1
          fi
          ;;
      esac
    done
    for argument in "$@"; do
      case "$argument" in
        --*) ;;
        *)
          rm -f -- "$state/$argument.active" "$state/$argument.enabled"
          rm -f -- "$state/$argument.state"
          rm -f -- "$units/timers.target.wants/$argument"
          rm -f -- "$units/multi-user.target.wants/$argument"
          ;;
      esac
    done
    ;;
  stop)
    rm -f -- "$state/${1:?}.active" "$state/${1:?}.state"
    ;;
  mask)
    for unit in "$@"; do
      case "$unit" in --*) continue ;; esac
      rm -f -- "$units/$unit"
      ln -s /dev/null "$units/$unit"
      rm -f -- "$state/$unit.active" "$state/$unit.enabled" "$state/$unit.state"
    done
    ;;
  daemon-reload) ;;
  *) exit 64 ;;
esac
""",
    )
    fake_stat = tmp_path / "stat"
    fake_stat.write_text(
        """#!/usr/bin/env python3
import os
import stat
import sys

if len(sys.argv) != 4 or sys.argv[1] != "-c":
    raise SystemExit(64)
fmt, path = sys.argv[2:]
value = os.stat(path, follow_symlinks=False)
rendered = (
    fmt.replace("%u", str(value.st_uid))
    .replace("%g", str(value.st_gid))
    .replace("%a", format(stat.S_IMODE(value.st_mode), "o"))
    .replace("%Y", str(int(value.st_mtime)))
)
print(rendered)
"""
    )
    fake_stat.chmod(0o755)
    env = os.environ | {
        "FAKE_SYSTEMCTL_STATE": str(fake_state),
        "SEICHE_ALLOW_NON_ROOT_RETIRE_TEST": "1",
        "SEICHE_SYSTEMD_DIR": str(systemd_dir),
        "SEICHE_DEPLOY_STATE_DIR": str(state_dir),
        "SEICHE_SYSTEMCTL_BIN": str(fake_systemctl),
        "SEICHE_SYNC_BIN": shutil.which("true") or "/usr/bin/true",
        "SEICHE_CP_BIN": shutil.which("cp") or "/bin/cp",
        "SEICHE_STAT_BIN": str(fake_stat),
        "SEICHE_SHA256SUM_BIN": shutil.which("sha256sum")
        or "/usr/bin/sha256sum",
    }
    return env, systemd_dir, state_dir


def _run_legacy_retirement(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LEGACY_UPDATE_RETIRER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _caddy_env(tmp_path: Path, *, reject_new_reload: bool = False) -> tuple[dict, Path, Path]:
    source = tmp_path / "repo.Caddyfile"
    installed = tmp_path / "installed.Caddyfile"
    calls = tmp_path / "calls.log"
    source.write_text("NEW\n")
    installed.write_text("OLD\n")

    caddy = _executable(
        tmp_path / "caddy",
        f'''config=""
want_config=0
for arg in "$@"; do
    if [ "$want_config" = 1 ]; then config="$arg"; want_config=0; continue; fi
    if [ "$arg" = --config ]; then want_config=1; fi
done
content=MISSING
[ -z "$config" ] || [ ! -f "$config" ] || content=$(tr -d '\\n' < "$config")
echo "caddy $1 config=$config content=$content" >> "{calls}"
if [ "$1" = validate ]; then exit 0; fi
if [ "${{REJECT_NEW_RELOAD:-0}}" = 1 ] && [ "$content" = NEW ]; then exit 1; fi
exit 0
''',
    )
    systemctl = _executable(
        tmp_path / "systemctl",
        f'''echo "systemctl $* $(tr -d '\\n' < "${{SEICHE_CADDY_DEST}}")" >> "{calls}"
if [ "${{REJECT_NEW_RELOAD:-0}}" = 1 ] && grep -q NEW "${{SEICHE_CADDY_DEST}}"; then exit 1; fi
exit 0
''',
    )
    _executable(
        tmp_path / "mv",
        f'''printf 'mv' >> "{calls}"
for arg in "$@"; do printf ' <%s>' "$arg" >> "{calls}"; done
printf '\\n' >> "{calls}"
exec /bin/mv "$@"
''',
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "SEICHE_CADDY_SOURCE": str(source),
        "SEICHE_CADDY_DEST": str(installed),
        "SEICHE_CADDY_BIN": str(caddy),
        "SEICHE_SYSTEMCTL_BIN": str(systemctl),
        "REJECT_NEW_RELOAD": "1" if reject_new_reload else "0",
    }
    return env, installed, calls


def test_caddy_installer_validates_backs_up_installs_and_reloads(tmp_path):
    env, installed, calls = _caddy_env(tmp_path)
    result = subprocess.run(
        ["bash", str(CADDY_INSTALLER)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert installed.read_text() == "NEW\n"
    assert list(tmp_path.glob("installed.Caddyfile.bak-*"))[0].read_text() == "OLD\n"
    log = calls.read_text()
    validation = next(line for line in log.splitlines() if line.startswith("caddy validate"))
    assert "content=NEW" in validation
    assert str(tmp_path / ".installed.Caddyfile.new.") in validation
    assert str(tmp_path / "repo.Caddyfile") not in validation
    assert f"mv <-f> <{tmp_path}/.installed.Caddyfile.new." in log
    assert f"<{installed}>" in log
    assert f"caddy reload config={installed} content=NEW" in log
    assert not list(tmp_path.glob(".installed.Caddyfile.*"))


def test_caddy_reload_failure_restores_previous_config_and_stays_red(tmp_path):
    env, installed, calls = _caddy_env(tmp_path, reject_new_reload=True)
    result = subprocess.run(
        ["bash", str(CADDY_INSTALLER)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert installed.read_text() == "OLD\n"
    log = calls.read_text()
    assert f"caddy reload config={installed} content=NEW" in log
    assert "systemctl reload caddy NEW" in log
    assert f"caddy reload config={installed} content=OLD" in log
    assert f"mv <-f> <{tmp_path}/.installed.Caddyfile.restore." in log
    assert not list(tmp_path.glob(".installed.Caddyfile.*"))
    assert "previous Caddyfile restored and reloaded" in result.stdout


def test_equal_caddyfile_is_validated_and_reloaded_to_heal_runtime(tmp_path):
    env, installed, calls = _caddy_env(tmp_path)
    Path(env["SEICHE_CADDY_SOURCE"]).write_text(installed.read_text())
    result = subprocess.run(
        ["bash", str(CADDY_INSTALLER)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    log = calls.read_text()
    assert f"caddy validate config={installed} content=OLD" in log
    assert f"caddy reload config={installed} content=OLD" in log
    assert "mv " not in log
    assert not list(tmp_path.glob("installed.Caddyfile.bak-*"))


def _smoke_env(tmp_path: Path, scenario: str = "success") -> tuple[dict, Path]:
    calls = tmp_path / "curl.log"
    _executable(
        tmp_path / "curl",
        f'''out=""
url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) out="$2"; shift 2 ;;
        http://*|https://*) url="$1"; shift ;;
        *) shift ;;
    esac
done
echo "$url $*" >> "{calls}"
status=200
case "$url" in
    */api/health)
        type=application/json; body='{{"generated_at":"2026-08-10T00:00:00Z"}}'
        ;;
    */api/public) type=application/json; body='{{"conclusion":"CLEAR"}}' ;;
    */api/oil-funding)
        type=application/json; body='{{"schema":"seiche.oil-funding.v1"}}' ;;
    */api/estuary)
        type=application/json; body='{{"schema":"seiche.estuary.v1"}}' ;;
    */api/v2/markets)
        type=application/json; body='{{"schema":"seiche.markets.v2"}}' ;;
    */api/v2/coverage)
        type=application/json; body='{{"schema":"seiche.coverage.v2"}}' ;;
    */api/v2/global/tide)
        type=application/json; body='{{"schema":"seiche.global-tide.v2"}}' ;;
    */api/subscribe) type=application/json; body='{{"gates_nothing":true}}' ;;
    */mcp) type='text/event-stream; charset=utf-8'; body=': stateless transport' ;;
    */riptide/) type=application/json; body='{{"name": "riptide"}}' ;;
    */riptide/openapi.json)
        type=application/json; body='{{"title": "Riptide Public API"}}'
        ;;
    */palimpsest/osint/osint-china.json)
        type=application/json
        body='{{"schema": "palimpsest-nemesis.public-snapshot"}}'
        ;;
    *) type=text/plain; body='generic' ;;
esac
if [ "${{SMOKE_SCENARIO:-success}}" = redirect ] && [[ "$url" = */api/subscribe ]]; then
    status=302; type=text/html; body='redirecting'
fi
if [ "${{SMOKE_SCENARIO:-success}}" = generic ] && [[ "$url" = */api/subscribe ]]; then
    status=200; type=application/json; body='{{"ok":true}}'
fi
printf '%s' "$body" > "$out"
printf '%s|%s' "$status" "$type"
''',
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "SEICHE_EXTERNAL_BASE_URL": "https://edge.invalid",
        "SEICHE_EXTERNAL_ROUTES_FILE": str(EXTERNAL_ROUTES),
        "SMOKE_SCENARIO": scenario,
    }
    return env, calls


def test_external_smoke_checks_subscribe_identity_without_following_redirects(tmp_path):
    definitions = EXTERNAL_ROUTES.read_text()
    assert 'GET|/api/health|200|application/json|"generated_at"' in definitions
    assert (
        'GET|/api/oil-funding|200|application/json|'
        '"schema":"seiche.oil-funding.v1"'
    ) in definitions
    assert (
        'GET|/api/estuary|200|application/json|'
        '"schema":"seiche.estuary.v1"'
    ) in definitions
    assert (
        'GET|/api/v2/markets|200|application/json|'
        '"schema":"seiche.markets.v2"'
    ) in definitions
    assert (
        'GET|/api/v2/coverage|200|application/json|'
        '"schema":"seiche.coverage.v2"'
    ) in definitions
    assert (
        'GET|/api/v2/global/tide|200|application/json|'
        '"schema":"seiche.global-tide.v2"'
    ) in definitions
    assert 'GET|/api/subscribe|200|application/json|"gates_nothing":true' in definitions
    assert (
        'GET|/riptide/|200|application/json|"name": "riptide"'
    ) in definitions
    assert (
        'GET|/riptide/openapi.json|200|application/json|'
        '"title": "Riptide Public API"'
    ) in definitions
    assert (
        'GET|/palimpsest/osint/osint-china.json|200|application/json|'
        '"schema": "palimpsest-nemesis.public-snapshot"'
    ) in definitions
    env, calls = _smoke_env(tmp_path)
    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "https://edge.invalid/api/subscribe" in calls.read_text()
    assert "--location" not in EXTERNAL_SMOKE.read_text()


def test_riptide_edge_strips_only_its_product_prefix_and_proxies_all_transports():
    caddy = CADDYFILE.read_text()
    assert "@riptide_root path /riptide" in caddy
    assert "handle_path /riptide/*" in caddy
    block = caddy[caddy.index("@riptide_root path") : caddy.index("# AnakE-Nyx")]
    assert block.count("reverse_proxy 127.0.0.1:8797") == 2
    assert "rewrite * /" in block


def test_external_smoke_rejects_redirect(tmp_path):
    env, _ = _smoke_env(tmp_path, "redirect")
    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "/api/subscribe returned 302" in result.stderr


def test_external_smoke_rejects_generic_json_200(tmp_path):
    env, _ = _smoke_env(tmp_path, "generic")
    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "not its route identity" in result.stderr


def test_forced_command_bootstrap_converges_in_one_workflow_run(tmp_path):
    calls = tmp_path / "ssh.log"
    ssh = _executable(
        tmp_path / "ssh",
        f'''for arg in "$@"; do printf '<%s>' "$arg" >> "{calls}"; done
printf '\\n' >> "{calls}"
''',
    )
    key = tmp_path / "key"
    known = tmp_path / "known_hosts"
    key.write_text("test-only")
    known.write_text("test-only")
    env = {
        **os.environ,
        "SEICHE_DEPLOY_HOST": "192.0.2.10",
        "SEICHE_DEPLOY_KEY_FILE": str(key),
        "SEICHE_KNOWN_HOSTS_FILE": str(known),
        "SEICHE_SSH_BIN": str(ssh),
    }
    result = subprocess.run(
        ["bash", str(FORCED_DEPLOY)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    assert len(lines) == 2
    assert all(line.endswith("<root@192.0.2.10><deploy>") for line in lines)
    workflow = DEPLOY_WORKFLOW.read_text()
    assert workflow.index("trigger-forced-deploy.sh") < workflow.index(
        "external-route-smoke.sh"
    )


def test_box_smoke_installs_its_declared_async_test_plugin():
    optional = tomllib.loads(PYPROJECT.read_text())["project"][
        "optional-dependencies"
    ]
    deploy_dependencies = optional["deploy-test"]
    box_update = BOX_UPDATE.read_text()

    assert any(item.startswith("pytest-asyncio") for item in deploy_dependencies)
    assert "./backend[deploy-test,notary,collectors,postgres]" in box_update
    assert "TARGET=${SEICHE_UPDATE_TARGET_SHA:-}" in box_update
    assert 'git reset -q --hard "$TARGET"' in box_update
    assert "git reset -q --hard origin/main" not in box_update


def test_wrapper_runs_edge_sync_on_new_and_already_running_release():
    wrapper = (ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh").read_text()
    assert wrapper.count("deploy_caddy ||") == 2
    assert (
        "already running ${AFTER:0:7} — checking candidate rebuild and edge config"
        in wrapper
    )
    assert "/api/v2/coverage" in wrapper
    assert "systemctl is-active --quiet postgresql" in wrapper
    assert wrapper.index("systemctl stop seiche-market-worker.service") < wrapper.index(
        "bash /home/seiche/update.sh"
    )
    target = wrapper.index('TARGET=$(runuser -u seiche -- git -C "$APP" rev-parse origin/main)')
    quiesce = wrapper.index("systemctl stop seiche-api", target)
    update = wrapper.index("bash /home/seiche/update.sh", quiesce)
    assert target < quiesce < update
    assert 'SEICHE_UPDATE_TARGET_SHA="$TARGET"' in wrapper[quiesce:update]
    assert 'if [ "$BEFORE" != "$TARGET" ] || [ "$DEPLOYED" != "$TARGET" ]' in wrapper
    update_failure = wrapper[update : wrapper.index('AFTER=""', update)]
    assert "restore_pre_restart_services" in update_failure
    assert "application update gate failed; recovery was attempted" in wrapper
    recovery = wrapper[
        wrapper.index("restore_pre_restart_services()") : wrapper.index(
            "systemctl stop seiche-market-worker.service"
        )
    ]
    assert recovery.index("restore_quiesced_api") < recovery.index(
        "restore_market_services"
    )
    assert "market writers remain stopped because api recovery failed" in recovery
    assert "healthy candidate code remains running and no rollback was attempted" in wrapper
    market_installer = wrapper[
        wrapper.index("deploy_market_platform()") : wrapper.index(
            "deploy_market_platform ||"
        )
    ]
    caddy_installer = wrapper[
        wrapper.index("deploy_caddy()") : wrapper.index("deploy_market_platform()")
    ]
    assert "SEICHE_DEFER_MARKET_START=1 bash" in market_installer
    assert "SEICHE_DEFER_MARKET_START" not in caddy_installer
    healthy_release = wrapper[wrapper.index('if [ -n "$HEALTHY" ]'):]
    assert healthy_release.index("start_market_services") < healthy_release.index(
        "deploy_caddy ||"
    )


def test_market_platform_units_are_independent_and_postgres_backed():
    installer = (ROOT / "ops" / "deploy" / "install-market-platform.sh").read_text()
    worker = (ROOT / "ops" / "deploy" / "seiche-market-worker.service").read_text()
    backfill = (ROOT / "ops" / "deploy" / "seiche-market-backfill.service").read_text()
    validation = (ROOT / "ops" / "deploy" / "seiche-market-validation.service").read_text()
    validation_timer = (ROOT / "ops" / "deploy" / "seiche-market-validation.timer").read_text()
    backup = (ROOT / "ops" / "deploy" / "seiche-market-backup.service").read_text()
    backup_script = (ROOT / "ops" / "deploy" / "seiche-market-backup.sh").read_text()
    backup_timer = (ROOT / "ops" / "deploy" / "seiche-market-backup.timer").read_text()
    restore = (
        ROOT / "ops" / "deploy" / "seiche-market-restore-check.service"
    ).read_text()
    restore_timer = (
        ROOT / "ops" / "deploy" / "seiche-market-restore-check.timer"
    ).read_text()
    caddy = CADDYFILE.read_text()

    assert 'psql -tAc "SHOW port"' in installer
    assert '"SHOW server_version_num"' in installer
    assert '"$POSTGRES_VERSION_NUM" -lt 110000' in installer
    assert "host=/var/run/postgresql&port=$POSTGRES_PORT" in installer
    assert "could not resolve the PostgreSQL cluster port" in installer
    assert 'connection.execute("SELECT 1")' in installer
    assert "get_repository().forward_record_count()" in installer
    assert 'FORWARD_MIGRATION_MARKERS" != "1|1|1' in installer
    assert "seiche-api.service seiche-market-worker.service" in installer
    assert "must be inactive before the forward-chain migration" in installer
    assert "duplicate forward children exist outside" in installer
    assert "SEICHE_RAW_CAPTURE_DIR=$STATE_DIR/raw" in installer
    assert '"$STATE_DIR/validation"' in installer
    assert "SEICHE_VALIDATION_DIR=$STATE_DIR/validation" in installer
    assert "seiche-market-validation.service" in installer
    assert "seiche-market-validation.timer" in installer
    assert "ReadWritePaths=$STATE_DIR/validation" in installer
    assert "systemctl enable --now seiche-market-validation.timer" in installer
    assert 'SEICHE_DEFER_MARKET_START:-0}' in installer
    assert "SEICHE_FUNDING_EXPORT_READER_GROUP" in installer
    assert 'groupadd --system "$EXPORT_READER_GROUP"' in installer
    assert 'setfacl -m "g:$EXPORT_READER_GROUP:--x"' in installer
    assert 'chmod 2750 "$FUNDING_EXPORT_DIR"' in installer
    assert 'chmod 0640 "$FUNDING_EXPORT_FILE"' in installer
    assert "setfacl -R" not in installer
    assert "find \"$FUNDING_EXPORT_DIR\"" not in installer
    funding_acl = installer[: installer.index("ENV_STAGE=")]
    assert "usermod" not in funding_acl
    assert 'FUNDING_EXPORT_DIR="$STATE_DIR/exports/us-usd-funding-core-v1"' in installer
    assert "SEICHE_USD_FUNDING_CORE_EXPORT_DIR=$FUNDING_EXPORT_DIR" in installer
    assert "systemctl start --no-block seiche-market-backfill.service" in installer
    assert "ExecStart=/home/seiche/app/backend/.venv/bin/seiche market-worker" in worker
    assert "Restart=always" in worker
    assert "Type=oneshot" in backfill
    assert "TimeoutStartSec=2h" in backfill
    assert "CPUQuota=100%" in backfill
    assert "CPUWeight=10" in backfill
    assert "IOWeight=10" in backfill
    assert "Nice=10" in backfill
    assert "ExecStart=/home/seiche/app/backend/.venv/bin/seiche market-validate" in validation
    assert "--evidence-dir" not in validation
    assert "SuccessExitStatus=2" in validation
    assert "After=network-online.target postgresql.service" in validation
    assert "seiche-market-worker.service" not in validation
    assert "seiche-api.service" not in validation
    assert "OnCalendar=*-*-* 03:15:00 UTC" in validation_timer
    assert "Persistent=true" in validation_timer
    assert "Unit=seiche-market-validation.service" in validation_timer
    assert "seiche-market-backup.service" in installer
    assert "seiche-market-backup.timer" in installer
    assert "seiche-market-restore-check.service" in installer
    assert "seiche-market-restore-check.timer" in installer
    assert "PACKAGES+=(util-linux)" in installer
    assert "/usr/bin/setpriv is required" in installer
    assert "ReadWritePaths=$BACKUP_DIR" in installer
    assert "ReadWritePaths=$STATE_DIR/validation" in installer
    assert "ExecStart=/usr/bin/flock --wait 300" in backup
    assert "seiche-market-backup.sh" in backup
    assert "mountpoint -q" in backup_script
    assert "CPUQuota=50%" in backup
    assert "MemoryMax=1G" in backup
    assert "ProtectSystem=strict" in backup
    assert "RestrictAddressFamilies=AF_UNIX" in backup
    assert "NoNewPrivileges=true" in backup
    assert "RestrictSUIDSGID=true" in backup
    assert "AmbientCapabilities=CAP_SETGID CAP_SETUID" in backup
    assert "ReadWritePaths=/var/backups/seiche-market /run/lock" in backup
    assert "ReadOnlyPaths=/home/seiche/app /var/lib/seiche /var/lib/seiche-deploy" in backup
    assert "/var/lib/seiche-deploy/deployed-sha" in backup_script
    assert "OnCalendar=*-*-* 02:00:00 UTC" in backup_timer
    assert "RandomizedDelaySec=10m" in backup_timer
    assert "Persistent=true" in backup_timer
    assert "ExecStart=/usr/bin/flock --wait 300" in restore
    assert "seiche-market-restore-check.sh" in restore
    assert "ReadOnlyPaths=/home/seiche/app /var/backups/seiche-market" in restore
    assert "ReadWritePaths=/var/lib/seiche/validation /run/lock" in restore
    assert "CAP_CHOWN" in restore
    assert "CAP_DAC_OVERRIDE" in restore
    assert "NoNewPrivileges=true" in restore
    assert "RestrictSUIDSGID=true" in restore
    assert "AmbientCapabilities=CAP_SETGID CAP_SETUID" in restore
    assert "OnCalendar=Sun *-*-* 07:30:00 UTC" in restore_timer
    assert "RandomizedDelaySec=15m" in restore_timer
    assert "Persistent=true" in restore_timer
    assert "/api/v2/*" in caddy


def test_legacy_updater_is_retired_before_other_host_services_change():
    installer = MARKET_INSTALLER.read_text()
    retirer = LEGACY_UPDATE_RETIRER.read_text()

    retirement = installer.index('/usr/bin/bash "$LEGACY_UPDATE_RETIRER"')
    assert retirement < installer.index("systemctl enable --now postgresql")
    assert retirement < installer.index("systemctl daemon-reload")
    assert "seiche-update.service" in retirer
    assert "seiche-update.timer" in retirer
    assert '"$SYSTEMCTL_BIN" disable --now "$TIMER_NAME"' in retirer
    assert '"$SYSTEMCTL_BIN" mask --now "$SERVICE_NAME" "$TIMER_NAME"' in retirer
    assert "ExecStartPost" not in retirer
    assert "GIT_SSH_COMMAND" not in retirer


def test_legacy_updater_retirement_archives_exact_units_and_masks_both(tmp_path):
    env, systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    original_service = (systemd_dir / "seiche-update.service").read_bytes()
    original_timer = (systemd_dir / "seiche-update.timer").read_bytes()

    result = _run_legacy_retirement(env)

    assert result.returncode == 0, result.stderr
    archive = state_dir / "retired-units" / "seiche-update-v1"
    assert (archive / "seiche-update.service").read_bytes() == original_service
    assert (archive / "seiche-update.timer").read_bytes() == original_timer
    assert (archive / "seiche-update.service").stat().st_mode & 0o777 == 0o644
    assert (archive / "seiche-update.timer").stat().st_mode & 0o777 == 0o644
    prestate = (archive / "pre-retirement-state.env").read_text()
    assert "timer_enabled=enabled" in prestate
    assert "timer_active=active" in prestate
    assert (archive / "SHA256SUMS").is_file()
    assert (archive / "STAT").is_file()
    assert (systemd_dir / "seiche-update.service").readlink() == Path("/dev/null")
    assert (systemd_dir / "seiche-update.timer").readlink() == Path("/dev/null")
    assert not (
        systemd_dir / "timers.target.wants" / "seiche-update.timer"
    ).exists()
    assert not (
        systemd_dir / "multi-user.target.wants" / "seiche-update.service"
    ).exists()


def test_legacy_updater_retirement_is_idempotent(tmp_path):
    env, _systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    first = _run_legacy_retirement(env)
    assert first.returncode == 0, first.stderr
    archive = state_dir / "retired-units" / "seiche-update-v1"
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in archive.iterdir()
        if path.is_file()
    }

    second = _run_legacy_retirement(env)

    assert second.returncode == 0, second.stderr
    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in archive.iterdir()
        if path.is_file()
    }
    assert after == before
    calls = Path(env["FAKE_SYSTEMCTL_STATE"], "calls.log").read_text()
    assert calls.count("disable --now seiche-update.timer\n") == 1
    assert calls.count("disable seiche-update.service\n") == 1


def test_legacy_updater_retirement_records_never_present_units(tmp_path):
    env, systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    fake_state = Path(env["FAKE_SYSTEMCTL_STATE"])
    for unit_name in ("seiche-update.service", "seiche-update.timer"):
        (systemd_dir / unit_name).unlink()
        (fake_state / f"{unit_name}.active").unlink(missing_ok=True)
        (fake_state / f"{unit_name}.enabled").unlink(missing_ok=True)
    for wants_dir in ("timers.target.wants", "multi-user.target.wants"):
        for wants_link in (systemd_dir / wants_dir).iterdir():
            wants_link.unlink()

    first = _run_legacy_retirement(env)
    second = _run_legacy_retirement(env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    archive = state_dir / "retired-units" / "seiche-update-v1"
    assert (archive / "seiche-update.service.absent").is_file()
    assert (archive / "seiche-update.timer.absent").is_file()
    assert "seiche-update.service.absent" in (archive / "SHA256SUMS").read_text()
    assert "seiche-update.timer.absent" in (archive / "SHA256SUMS").read_text()


@pytest.mark.parametrize(
    "masked_unit", ("seiche-update.service", "seiche-update.timer")
)
def test_legacy_updater_retirement_rejects_unproven_premasked_units(
    tmp_path, masked_unit
):
    env, systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    fake_state = Path(env["FAKE_SYSTEMCTL_STATE"])
    (systemd_dir / masked_unit).unlink()
    (systemd_dir / masked_unit).symlink_to("/dev/null")
    for unit_name in ("seiche-update.service", "seiche-update.timer"):
        (fake_state / f"{unit_name}.active").unlink(missing_ok=True)
        (fake_state / f"{unit_name}.enabled").unlink(missing_ok=True)
    for wants_dir in ("timers.target.wants", "multi-user.target.wants"):
        for wants_link in (systemd_dir / wants_dir).iterdir():
            wants_link.unlink()

    result = _run_legacy_retirement(env)

    assert result.returncode != 0
    assert "no verified retirement evidence" in result.stderr
    archive = state_dir / "retired-units" / "seiche-update-v1"
    assert not (archive / masked_unit).exists()
    assert not (archive / f"{masked_unit}.absent").exists()
    assert not (archive / "SHA256SUMS").exists()
    assert not (archive / "STAT").exists()
    assert (systemd_dir / masked_unit).readlink() == Path("/dev/null")


def test_legacy_updater_retirement_accepts_failed_state_and_partial_stage(tmp_path):
    env, _systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    fake_state = Path(env["FAKE_SYSTEMCTL_STATE"])
    (fake_state / "seiche-update.service.state").write_text("failed\n")
    archive = state_dir / "retired-units" / "seiche-update-v1"
    archive.mkdir(parents=True)
    interrupted = archive / ".seiche-update.service.archive.partial"
    interrupted.write_text("partial\n")

    result = _run_legacy_retirement(env)

    assert result.returncode == 0, result.stderr
    assert (archive / "seiche-update.service").is_file()
    assert (archive / "seiche-update.timer").is_file()
    assert not interrupted.exists()


def test_legacy_updater_retirement_rejects_collision_and_unsafe_symlink(tmp_path):
    env, systemd_dir, state_dir = _legacy_retirement_fixture(tmp_path)
    archive = state_dir / "retired-units" / "seiche-update-v1"
    archive.mkdir(parents=True)
    (archive / "seiche-update.service").write_text("different\n")

    collision = _run_legacy_retirement(env)

    assert collision.returncode != 0
    assert "archive differs" in collision.stderr
    assert (systemd_dir / "seiche-update.service").is_file()

    (archive / "seiche-update.service").unlink()
    (systemd_dir / "seiche-update.service").unlink()
    (systemd_dir / "seiche-update.service").symlink_to(tmp_path / "unexpected")
    unsafe = _run_legacy_retirement(env)

    assert unsafe.returncode != 0
    assert "unexpected symlink" in unsafe.stderr


def test_forward_incident_runbook_loads_systemd_environment_without_sourcing():
    runbook = (
        ROOT / "docs" / "FORWARD_CHAIN_INCIDENT_2026-08-11.md"
    ).read_text()

    assert "mapfile -t MARKET_ENV" in runbook
    assert 'env "${MARKET_ENV[@]}"' in runbook
    assert "never `source` this file" in runbook
    assert ". /etc/seiche/market.env" not in runbook


def test_private_world_model_delivery_has_an_exact_least_privilege_seam():
    installer = (ROOT / "ops" / "deploy" / "install-market-platform.sh").read_text()
    relay_installer = WORLD_MODEL_DELIVERY_INSTALLER.read_text()
    caddy = CADDYFILE.read_text()
    delivery_docs = (ROOT / "ops" / "deploy" / "WORLD-MODEL-DELIVERY.md").read_text()
    route = "/api/internal/v1/world-model/us-usd-funding-core-v2"
    exact_file = (
        "/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json"
    )

    assert f"path {route}" in caddy
    private_edge = caddy[
        caddy.index("@world_model_delivery {") : caddy.index("@public {")
    ]
    assert 'header Cache-Control "no-store, no-transform"' in private_edge
    assert "reverse_proxy 127.0.0.1:8787" in private_edge
    assert "@world_model_delivery_non_get path" in private_edge
    assert 'respond "not here" 404' in private_edge
    public_edge = caddy[caddy.index("@public {") : caddy.index("@login {")]
    assert route not in public_edge
    assert route not in EXTERNAL_ROUTES.read_text()
    assert f"https://api.seiche.info{route}" in delivery_docs
    assert f"https://seiche.info{route}" not in delivery_docs

    assert "EnvironmentFile=-$DELIVERY_ENV_FILE" in installer
    assert exact_file in installer
    assert "liquilens-world-model-readers" in installer
    assert 'usermod -a -G "$DELIVERY_READER_GROUP" seiche' in installer
    assert 'runuser -u seiche -- test -r "$DELIVERY_PATH"' in installer
    assert exact_file in relay_installer
    assert "SEICHE_WORLD_MODEL_DELIVERY_BEARER_TOKEN=$TOKEN" in relay_installer
    assert "HARD_MAX_BYTES=5242880" in relay_installer
    assert "liquilens-world-model-readers" in relay_installer
    assert "/archive" not in relay_installer
    assert "/latest" not in relay_installer
    assert 'echo "$TOKEN"' not in relay_installer
    assert "setfacl" not in relay_installer


def test_release_health_capability_is_loopback_only():
    caddy = CADDYFILE.read_text()
    route = "/api/internal/v1/release-health"
    private_edge = caddy[
        caddy.index("@release_health path") : caddy.index("@public {")
    ]

    assert f"@release_health path {route}" in private_edge
    assert 'respond "not here" 404' in private_edge
    assert "reverse_proxy" not in private_edge
    public_edge = caddy[caddy.index("@public {") : caddy.index("@login {")]
    assert route not in public_edge


def test_deploy_smoke_runs_private_delivery_contracts():
    update = BOX_UPDATE.read_text()
    workflow = (ROOT / ".github" / "workflows" / "market-platform-ci.yml").read_text()

    assert "tests/test_world_model_delivery.py" in update
    assert "backend/tests/test_world_model_delivery.py" in workflow


def test_deploy_smoke_runs_cache_only_health_contracts():
    update = BOX_UPDATE.read_text()

    assert "tests/test_api_caching.py" in update


def test_pull_unit_reads_the_api_cache_without_owning_snapshot_refresh():
    unit = PULL_UNIT.read_text()

    assert "Requires=seiche-api.service" in unit
    assert "After=network-online.target seiche-api.service" in unit
    assert "WorkingDirectory=/home/seiche/app/backend" in unit
    assert (
        "ExecStart=/home/seiche/app/backend/.venv/bin/seiche alert "
        "--api-url http://127.0.0.1:8787/api/overview "
        "--max-snapshot-age-seconds 3600"
    ) in unit
    assert "--force" not in unit
    assert "SuccessExitStatus=0 2" in unit
    assert "TimeoutStartSec=1200" in unit


def test_deploy_wrapper_converges_pull_unit_only_after_candidate_health():
    wrapper = DEPLOY_WRAPPER.read_text()
    readiness = wrapper[
        wrapper.index("parse_candidate_health()") : wrapper.index("market_health()")
    ]
    candidate_once = readiness[
        readiness.index("candidate_health_once()") : readiness.index(
            "candidate_health_wait()"
        )
    ]
    assert "/api/internal/v1/release-health" in readiness
    assert "require_rebuilt=true" not in candidate_once
    assert "/api/public" not in readiness
    assert 'set(candidate) != {"producer_sha", "activation_token"}' in readiness
    assert 'candidate.get("producer_sha") != expected_sha' in readiness
    assert 're.fullmatch(r"[0-9a-f]{64}"' in readiness
    assert 'sys.stdout.write(candidate["activation_token"])' in readiness
    function = wrapper[
        wrapper.index("deploy_pull_unit()") : wrapper.index("deploy_market_platform ||")
    ]

    assert "systemd-analyze verify" in function
    assert function.index('cp -p "$destination" "$previous"') < function.index(
        'mv -f "$candidate" "$destination"'
    )
    assert "daemon-reload rejected the pull unit; restoring" in function
    assert 'mv -f "$previous" "$destination"' in function
    assert "systemctl start seiche-pull" not in function
    assert "systemctl restart seiche-pull" not in function
    assert "for attempt in 1 2 3" in function
    assert 'candidate_health_once "$AFTER"' in function
    assert 'write_promotion_request "$AFTER" "$ACTIVATION_TOKEN"' in function
    assert 'systemctl start "$PROMOTION_UNIT"' in function
    assert "runuser -u seiche" not in function[function.index("POINT_OF_NO_RETURN") :]

    health = wrapper[
        wrapper.index("HEALTHY=\"\"") : wrapper.index('if [ -n "$HEALTHY" ]')
    ]
    assert 'if systemctl restart seiche-api; then' in health
    assert 'RESTARTED=1' in health
    assert 'if [ -n "$RESTARTED" ] && systemctl is-active' in health
    assert health.index("systemctl restart seiche-api") < health.index(
        "candidate_health_wait"
    )
    assert health.index("market_health") < health.index("deploy_pull_unit")
    assert health.index("deploy_pull_unit") < health.index("promote_snapshot_handoff")
    assert health.index("promote_snapshot_handoff") < health.index("HEALTHY=1")
    already = wrapper[
        wrapper.index('if [ "$BEFORE" = "$AFTER" ] &&') : wrapper.index(
            'if [ "$BEFORE" = "$AFTER" ]; then'
        )
    ]
    assert 'if ! systemctl is-active --quiet seiche-api; then' in already
    assert already.index("systemctl restart seiche-api") < already.index(
        'candidate_health_wait 900 "$AFTER"'
    )
    assert "without moving the checkout" in already
    assert "market writers remain stopped" in already
    assert 'candidate_health_wait 900 "$AFTER"' in already
    assert "market_health" in already
    assert "deploy_pull_unit" in already
    assert "promote_snapshot_handoff" in already
    assert already.index("candidate_health_wait") < already.index("market_health")
    assert already.index("market_health") < already.index("deploy_pull_unit")
    assert already.index("deploy_pull_unit") < already.index(
        "promote_snapshot_handoff"
    )
    promotion_failure = already[already.index("promote_snapshot_handoff ||") :]
    assert "restore_market_services" in promotion_failure
    assert "healthy running candidate kept in place" in promotion_failure
    assert "accepted release did not recover strict health" in already


def test_snapshot_promotion_unit_and_installer_are_fixed_and_sandboxed():
    installer = MARKET_INSTALLER.read_text()
    unit = PROMOTION_UNIT.read_text()

    assert "Type=oneshot" in unit
    assert "User=seiche" in unit
    assert "Group=seiche" in unit
    assert "WorkingDirectory=/home/seiche/app/backend" in unit
    assert "EnvironmentFile=/etc/seiche/market.env" in unit
    assert "EnvironmentFile=/etc/seiche/release.env" in unit
    assert "EnvironmentFile=-/etc/seiche/market.env" not in unit
    assert "EnvironmentFile=-/etc/seiche/release.env" not in unit
    assert (
        "ExecStart=/home/seiche/app/backend/.venv/bin/python "
        "-m seiche.release_promote"
    ) in unit
    assert (
        "ExecStopPost=+/usr/bin/rm -f "
        "/run/seiche-release/promotion-request.json"
    ) in unit
    assert "CapabilityBoundingSet=" in unit
    assert "MemoryMax=1G" in unit
    assert "TasksMax=64" in unit
    assert "OnFailure=undertow-failure-alert@%n.service" in unit
    assert "ProtectSystem=strict" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert unit.count("ReadWritePaths=") == 1
    assert "ReadWritePaths=/home/seiche/app/backend/data" in unit

    assert 'install -d -o root -g seiche -m 0750 "$PROMOTION_REQUEST_DIR"' in installer
    assert 'install -d -o root -g root -m 0700 "$DEPLOY_STATE_DIR"' in installer
    assert "coreutils" in installer
    assert 'dpkg --compare-versions "$SYNC_VERSION" ge 8.24' in installer
    assert "systemd-analyze verify" in installer
    assert "seiche-snapshot-promote.service" in installer
    assert 'mv -f "$PROMOTION_UNIT_STAGE_DIR/seiche-snapshot-promote.service"' in installer
    assert "systemctl enable seiche-snapshot-promote.service" not in installer
    api_dropin = installer[installer.index('cat >"$DROPIN"') :]
    assert "EnvironmentFile=-$ENV_DIR/release.env" in api_dropin


def test_deploy_controller_writes_only_atomic_root_owned_fixed_requests():
    wrapper = DEPLOY_WRAPPER.read_text()
    release_writer = wrapper[
        wrapper.index("write_release_env()") : wrapper.index(
            "write_promotion_request()"
        )
    ]
    request_writer = wrapper[
        wrapper.index("write_promotion_request()") : wrapper.index("# The sha whose")
    ]

    assert "^ [0-9a-f]" not in release_writer
    assert "^[0-9a-f]{40}$" in wrapper
    assert "^[0-9a-f]{64}$" in wrapper
    assert "printf 'SEICHE_RELEASE_SHA=%s\\n'" in release_writer
    assert "chown root:seiche" in release_writer
    assert "chmod 0640" in release_writer
    assert 'mv -f "$stage" "$RELEASE_ENV"' in release_writer
    assert 'printf \'{"expected_sha":"%s","activation_token":"%s"}\\n\'' in request_writer
    assert "chown root:seiche" in request_writer
    assert "chmod 0640" in request_writer
    assert 'mv -f "$stage" "$PROMOTION_REQUEST"' in request_writer
    assert "/etc/seiche/market.env" not in wrapper
    assert "source /etc/seiche" not in wrapper
    assert "eval " not in wrapper
    assert 'git -C "$APP" diff-index --quiet "$AFTER" --' in wrapper
    assert '--others --exclude-standard -- backend' in wrapper
    assert '--others --ignored --exclude-standard -- backend' in wrapper
    assert "$0 !~ /^backend\\/\\.venv\\//" in wrapper
    assert "$0 !~ /\\/__pycache__\\//" in wrapper
    assert 'if ! AFTER=$(runuser -u seiche -- git -C "$APP" rev-parse HEAD)' in wrapper
    unresolved = wrapper[
        wrapper.index('if ! AFTER=$(runuser') : wrapper.index(
            'if [ "$AFTER" != "$TARGET" ]'
        )
    ]
    assert "restore_pre_restart_services" in unresolved
    assert "STATE=$DEPLOY_STATE_DIR/deployed-sha" in wrapper
    assert 'install -d -o root -g root -m 0700 "$DEPLOY_STATE_DIR"' in wrapper
    assert "root:root:700" in wrapper
    assert "root:root:600" in wrapper
    assert 'mktemp "$DEPLOY_STATE_DIR/.deployed-sha.XXXXXX"' in wrapper
    assert 'mv -f "$stage" "$STATE"' in wrapper
    assert '/usr/bin/sync -f "$stage"' in wrapper
    assert '/usr/bin/sync "$DEPLOY_STATE_DIR"' in wrapper
    assert "DEPLOYED_STATE_RENAMED=1" in wrapper
    state_writer = wrapper[
        wrapper.index("write_deployed_state()") : wrapper.index("write_release_env()")
    ]
    assert state_writer.index('/usr/bin/sync -f "$stage"') < state_writer.index(
        'mv -f "$stage" "$STATE"'
    ) < state_writer.index('/usr/bin/sync "$DEPLOY_STATE_DIR"')
    assert 'SEICHE_DEPLOYED_SHA="$DEPLOYED"' in wrapper
    assert "/home/seiche/.seiche-deployed-sha" not in wrapper
    assert "DEPLOYED=${SEICHE_DEPLOYED_SHA:-}" in BOX_UPDATE.read_text()
    deploy_lock = wrapper.index("flock --nonblock 9")
    assert 'DEPLOY_RUNTIME_DIR=/run/seiche-deploy' in wrapper[:deploy_lock]
    assert 'install -d -o root -g root -m 0700 "$DEPLOY_RUNTIME_DIR"' in wrapper[
        :deploy_lock
    ]
    assert 'exec 9>"$DEPLOY_LOCK"' in wrapper[:deploy_lock]
    assert "another seiche deployment is still running" in wrapper
    assert deploy_lock < wrapper.index("# The sha whose code is actually RUNNING")


def test_deploy_controller_pins_a_locally_tested_target_before_quiescing():
    wrapper = DEPLOY_WRAPPER.read_text()
    resolved = wrapper.index(
        'TARGET=$(runuser -u seiche -- git -C "$APP" rev-parse origin/main)'
    )
    constrained = wrapper.index("EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}")
    stopped = wrapper.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service"
    )
    checked = wrapper[constrained:stopped]

    assert resolved < constrained < stopped
    assert 'valid_release_sha "$EXPECTED_TARGET"' in checked
    assert '[ "$TARGET" != "$EXPECTED_TARGET" ]' in checked
    assert "refusing to deploy an untested commit" in checked
    assert "exit 1" in checked


def test_release_poller_gates_one_exact_detached_candidate_before_deploy():
    poller = RELEASE_POLLER.read_text()
    selected = poller.index('TARGET=$(as_service git -C "$APP_DIR" rev-parse origin/main)')
    signature = poller.index('verify_target_signature "$TARGET"', selected)
    detached = poller.index(
        'as_service git -C "$APP_DIR" worktree add --detach "$CANDIDATE_DIR" "$TARGET"'
    )
    full_gate = poller.index(
        '"$VENV/bin/python" -m pytest backend/tests -q', detached
    )
    refetched = poller.index(
        'as_service git -C "$APP_DIR" fetch -q origin main', full_gate
    )
    superseded = poller.index('if [ "$LATEST" != "$TARGET" ]', refetched)
    gate_receipt = poller.index('write_receipt gate "$GATE_RECEIPT"', superseded)
    gate_only = poller.index('if [ "$GATE_ONLY" = 1 ]', gate_receipt)
    deployed = poller.index(
        'SEICHE_EXPECTED_TARGET_SHA="$TARGET" "$DEPLOY_WRAPPER"', gate_only
    )

    assert (
        selected
        < signature
        < detached
        < full_gate
        < refetched
        < superseded
        < gate_receipt
        < gate_only
        < deployed
    )
    assert 'CANDIDATE_PARENT="$STATE_DIR/candidates"' in poller
    assert 'install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700' in poller
    assert 'exec 8>"$CONTROL_LOCK"' in poller
    assert 'flock --nonblock 8' in poller
    assert '"$CANDIDATE_DIR/backend[dev,collectors]"' in poller
    gate_slice = poller[detached:gate_receipt]
    assert 'as_service "$TIMEOUT"' in gate_slice
    assert "EnvironmentFile" not in gate_slice
    assert "production unchanged" in poller[superseded:gate_receipt]
    assert "gate-only success" in poller[gate_only:deployed]


def test_release_signature_boundary_accepts_only_the_pinned_signed_identity(tmp_path):
    repository, env = _release_signature_fixture(tmp_path)
    target = _commit_release(repository, "signed release")

    result = _verify_release_signature(env, target)

    assert result.returncode == 0, result.stdout + result.stderr


def test_unsigned_release_target_is_rejected_before_candidate_execution(tmp_path):
    repository, env = _release_signature_fixture(tmp_path)
    target = _commit_release(repository, "unsigned release", signed=False)

    result = _verify_release_signature(env, target)

    assert result.returncode != 0
    assert "does not carry a valid pinned SSH signature" in result.stderr


def test_wrong_principal_release_target_is_rejected_before_candidate_execution(
    tmp_path,
):
    repository, env = _release_signature_fixture(tmp_path)
    _git("config", "user.email", "intruder@example.invalid", cwd=repository)
    target = _commit_release(repository, "wrong author release")

    result = _verify_release_signature(env, target)

    assert result.returncode != 0
    assert (
        "target commit author is not the pinned release principal: "
        "intruder@example.invalid"
    ) in result.stderr


def test_release_signature_policy_is_fixed_to_one_ed25519_identity():
    signer = RELEASE_ALLOWED_SIGNERS.read_text(encoding="ascii")
    poller = RELEASE_POLLER.read_text()

    assert signer.count("\n") == 1
    assert signer.startswith(
        "beepboop2025@users.noreply.github.com ssh-ed25519 "
    )
    assert "validate_allowed_signers" in poller
    assert 'stat.S_IMODE(info.st_mode) != int(mode, 8)' in poller
    assert 'info.st_nlink != 1' in poller
    assert '-c "gpg.ssh.program=$SSH_KEYGEN"' in poller


def test_release_receipts_are_no_clobber_and_follow_the_rollback_boundary():
    poller = RELEASE_POLLER.read_text()
    writer = poller[poller.index("write_receipt()") :]
    gate = writer.index('write_receipt gate "$GATE_RECEIPT"')
    deploy = writer.index(
        'SEICHE_EXPECTED_TARGET_SHA="$TARGET" "$DEPLOY_WRAPPER"', gate
    )
    exact_health = writer.index('health_matches "$TARGET"', deploy)
    release = writer.index('write_receipt release "$RELEASE_RECEIPT"', exact_health)

    assert 'chmod 0400 "$stage"' in writer
    assert 'ln "$stage" "$path"' in writer
    assert 'mv -n "$stage" "$path"' not in writer
    assert '"conclusion": "success"' in writer
    assert '"gate_receipt_sha256"' in writer
    assert gate < deploy < exact_health < release
    assert "wrapper failure never writes" in (
        ROOT / "ops" / "deploy" / "RELEASE-POLLER.md"
    ).read_text()


def test_release_poller_installer_restores_files_and_timer_on_reload_failure(
    tmp_path,
):
    app = tmp_path / "app"
    source = app / "ops" / "deploy"
    source.mkdir(parents=True)
    for path in (
        RELEASE_POLLER,
        RELEASE_POLLER_SERVICE,
        RELEASE_POLLER_TIMER,
        RELEASE_ALLOWED_SIGNERS,
    ):
        shutil.copy2(path, source / path.name)

    systemd = tmp_path / "systemd"
    binary_dir = tmp_path / "sbin"
    runtime = tmp_path / "run"
    systemd.mkdir()
    binary_dir.mkdir()
    wrapper = _executable(
        tmp_path / "seiche-deploy-wrapper",
        'EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}\nexit 0\n',
    )
    installed = {
        binary_dir / "seiche-release-poll": "old script\n",
        systemd / "seiche-release-poll.service": "old service\n",
        systemd / "seiche-release-poll.timer": "old timer\n",
    }
    for path, body in installed.items():
        path.write_text(body)

    calls = tmp_path / "systemctl.calls"
    reload_count = tmp_path / "reload.count"
    systemctl = _executable(
        tmp_path / "systemctl",
        f'''
printf '%s\n' "$*" >>"{calls}"
case "$1" in
  is-enabled|is-active) exit 0 ;;
  daemon-reload)
    count=0
    [ ! -f "{reload_count}" ] || count=$(cat "{reload_count}")
    count=$((count + 1))
    printf '%s\n' "$count" >"{reload_count}"
    [ "$count" -gt 1 ]
    ;;
  enable|start|disable|stop) exit 0 ;;
  *) exit 64 ;;
esac
''',
    )
    always_ok = _executable(tmp_path / "always-ok", "exit 0\n")
    installed_signer = tmp_path / "seiche-release.allowed-signers"
    env = os.environ | {
        "SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "1",
        "SEICHE_APP_DIR": str(app),
        "SEICHE_SYSTEMD_DIR": str(systemd),
        "SEICHE_RELEASE_POLLER_DEST": str(binary_dir / "seiche-release-poll"),
        "SEICHE_DEPLOY_WRAPPER": str(wrapper),
        "SEICHE_CONTROL_RUNTIME_DIR": str(runtime),
        "SEICHE_SYSTEMCTL_BIN": str(systemctl),
        "SEICHE_SYSTEMD_ANALYZE_BIN": str(always_ok),
        "SEICHE_SYNC_BIN": str(always_ok),
        "SEICHE_FLOCK_BIN": str(always_ok),
        "SEICHE_RELEASE_ALLOWED_SIGNERS_DEST": str(installed_signer),
        "SEICHE_CONTROL_PYTHON": sys.executable,
    }

    result = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "restoring the previous release-poller files and timer state" in result.stderr
    for path, body in installed.items():
        assert path.read_text() == body
    systemctl_calls = calls.read_text().splitlines()
    assert systemctl_calls.count("daemon-reload") == 2
    assert "enable seiche-release-poll.timer" in systemctl_calls
    assert "start seiche-release-poll.timer" in systemctl_calls
    assert not list(systemd.glob(".seiche-release-poll.*"))
    assert installed_signer.read_text(encoding="ascii") == (
        RELEASE_ALLOWED_SIGNERS.read_text(encoding="ascii")
    )
    assert installed_signer.stat().st_mode & 0o777 == 0o444
    assert installed_signer.stat().st_nlink == 1


def test_release_poller_installer_never_replaces_an_existing_signer_pin(tmp_path):
    app = tmp_path / "app"
    source = app / "ops" / "deploy"
    source.mkdir(parents=True)
    for path in (
        RELEASE_POLLER,
        RELEASE_POLLER_SERVICE,
        RELEASE_POLLER_TIMER,
        RELEASE_ALLOWED_SIGNERS,
    ):
        shutil.copy2(path, source / path.name)

    systemd = tmp_path / "systemd"
    binary_dir = tmp_path / "sbin"
    systemd.mkdir()
    binary_dir.mkdir()
    wrapper = _executable(
        tmp_path / "seiche-deploy-wrapper",
        'EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}\nexit 0\n',
    )
    installed_signer = tmp_path / "seiche-release.allowed-signers"
    wrong_pin = (
        "beepboop2025@users.noreply.github.com ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIGX2PaWkr0977OLNJdYgi6QJnX/LBHS7OT+Ea8uzY8/x\n"
    )
    installed_signer.write_text(wrong_pin, encoding="ascii")
    installed_signer.chmod(0o444)
    env = os.environ | {
        "SEICHE_ALLOW_NON_ROOT_INSTALL_TEST": "1",
        "SEICHE_APP_DIR": str(app),
        "SEICHE_SYSTEMD_DIR": str(systemd),
        "SEICHE_RELEASE_POLLER_DEST": str(binary_dir / "seiche-release-poll"),
        "SEICHE_DEPLOY_WRAPPER": str(wrapper),
        "SEICHE_RELEASE_ALLOWED_SIGNERS_DEST": str(installed_signer),
        "SEICHE_CONTROL_PYTHON": sys.executable,
    }

    result = subprocess.run(
        ["bash", str(RELEASE_POLLER_INSTALLER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "refusing to replace the pinned Seiche release signer" in result.stderr
    assert installed_signer.read_text(encoding="ascii") == wrong_pin


def test_release_poller_units_are_inert_until_an_explicit_handoff():
    installer = RELEASE_POLLER_INSTALLER.read_text()
    service = RELEASE_POLLER_SERVICE.read_text()
    timer = RELEASE_POLLER_TIMER.read_text()

    assert "expected-target-SHA safety pin" in installer
    assert 'exec 9>"$CONTROL_LOCK"' in installer
    assert '"$FLOCK" --nonblock 9' in installer
    assert installer.index('mv -f -- "$SCRIPT_NEW" "$SCRIPT_DEST"') < installer.index(
        '"$SYSTEMD_ANALYZE" verify'
    )
    assert "rollback_install" in installer
    assert '"$SYSTEMCTL" disable --now seiche-release-poll.timer' in installer
    assert 'ENABLE="${SEICHE_ENABLE_RELEASE_POLLER:-0}"' in installer
    assert "refusing to replace the pinned Seiche release signer" in installer
    assert 'ln "$SIGNER_STAGE" "$ALLOWED_SIGNERS"' in installer
    assert "SEICHE_CONTROL_ALLOWED_SIGNERS=/etc/seiche-release.allowed-signers" in service
    assert "SEICHE_CONTROL_SIGNING_PRINCIPAL=" in service
    assert "ReadOnlyPaths=/etc/seiche-release.allowed-signers" in service
    assert "ExecStart=/usr/local/sbin/seiche-release-poll" in service
    assert "ConditionPathExists" not in service
    assert "TimeoutStartSec=3h" in service
    assert "OnUnitInactiveSec=5min" in timer
    assert "WantedBy=timers.target" in timer


def test_promotion_is_point_of_no_return_and_rollback_stops_before_reset():
    wrapper = DEPLOY_WRAPPER.read_text()
    promotion = wrapper[
        wrapper.index("promote_snapshot_handoff()") : wrapper.index(
            "deploy_market_platform ||"
        )
    ]
    assert promotion.index('write_deployed_state "$AFTER"') < promotion.index(
        "POINT_OF_NO_RETURN=1"
    )
    assert 'if [ -n "$DEPLOYED_STATE_RENAMED" ]; then' in promotion
    assert promotion.index("POINT_OF_NO_RETURN=1") < promotion.index(
        'systemctl start "$PROMOTION_UNIT"'
    )
    assert promotion.index('systemctl start "$PROMOTION_UNIT"') < promotion.index(
        'candidate_health_wait 120 "$AFTER"'
    )
    assert 'rm -f -- "$PROMOTION_REQUEST"' in promotion

    assert wrapper.index("market_health", wrapper.index("HEALTHY=\"\"")) < wrapper.index(
        "promote_snapshot_handoff", wrapper.index("HEALTHY=\"\"")
    )
    no_rollback = wrapper[
        wrapper.index('if [ -n "$POINT_OF_NO_RETURN" ]') : wrapper.index(
            "# A red warm-up"
        )
    ]
    assert "restore_market_services" in no_rollback
    assert "exit 1" in no_rollback

    rollback = wrapper[wrapper.index("# A red warm-up") :]
    validate = rollback.index('valid_release_sha "$DEPLOYED"')
    verify_commit = rollback.index('rev-parse --verify --quiet "$DEPLOYED^{commit}"')
    stop_api = rollback.index("systemctl stop seiche-api")
    rewrite_release = rollback.index('write_release_env "$DEPLOYED"')
    reset = rollback.index('reset -q --hard "$DEPLOYED"')
    restart = rollback.index("systemctl restart seiche-api")
    assert validate < verify_commit < stop_api < rewrite_release < reset < restart
    assert "systemctl stop seiche-api 2>/dev/null || true" not in rollback
    assert 'rollback_health_wait 480' in rollback
    rollback_health = wrapper[
        wrapper.index("rollback_health_wait()") : wrapper.index("market_health()")
    ]
    assert "require_rebuilt=true" in rollback_health
    assert "candidate_health_once" not in rollback_health


def test_palimpest_osint_edge_is_an_exact_static_allowlist():
    caddy = CADDYFILE.read_text()
    assert "handle_path /palimpsest/osint/*" not in caddy
    assert (
        "@palimpsest_osint path /palimpsest/osint/osint-china.json "
        "/palimpsest/osint/osint-china.json.hmac-sha256"
    ) in caddy
    assert "root * /var/lib/palimpsest-nemesis/public" in caddy
    osint_block = caddy[
        caddy.index("@palimpsest_osint path") : caddy.index(
            "# Palimpsest BLEEDTHROUGH"
        )
    ]
    assert 'header Cache-Control "no-store"' in osint_block
    assert "stale-if-error" not in osint_block
    assert "uri strip_prefix /palimpsest/osint" in osint_block
    assert "reverse_proxy" not in osint_block


def test_palimpsest_bleedthrough_edge_is_an_exact_sanitized_allowlist():
    caddy = CADDYFILE.read_text()
    assert "handle_path /palimpsest/bleedthrough/*" not in caddy
    assert (
        "@palimpsest_bleedthrough path "
        "/palimpsest/bleedthrough/bleedthrough-latest.json "
        "/palimpsest/bleedthrough/bleedthrough-history.jsonl"
    ) in caddy
    block = caddy[
        caddy.index("@palimpsest_bleedthrough path") : caddy.index(
            "# Palimpsest MCP"
        )
    ]
    assert 'header Access-Control-Allow-Origin "https://palimpsest.info"' in block
    assert 'header Cache-Control "no-store, no-transform"' in block
    assert 'header Content-Disposition "inline"' in block
    assert "uri strip_prefix /palimpsest/bleedthrough" in block
    assert "root * /var/lib/palimpsest/readings" in block
    assert "file_server" in block
    assert "reverse_proxy" not in block
