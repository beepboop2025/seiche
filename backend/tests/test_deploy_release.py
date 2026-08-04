"""Release-boundary contracts, exercised without a host or external network."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
CADDY_INSTALLER = ROOT / "ops" / "deploy" / "install-caddy.sh"
EXTERNAL_SMOKE = ROOT / "ops" / "deploy" / "external-route-smoke.sh"
EXTERNAL_ROUTES = ROOT / "ops" / "deploy" / "external-smoke-routes.txt"


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -u\n" + body)
    path.chmod(0o755)
    return path


def _caddy_env(tmp_path: Path, *, reject_new_reload: bool = False) -> tuple[dict, Path, Path]:
    source = tmp_path / "repo.Caddyfile"
    installed = tmp_path / "installed.Caddyfile"
    calls = tmp_path / "calls.log"
    source.write_text("NEW\n")
    installed.write_text("OLD\n")

    caddy = _executable(
        tmp_path / "caddy",
        f'''echo "caddy $* $(tr -d '\\n' < "${{SEICHE_CADDY_DEST}}")" >> "{calls}"
if [ "$1" = validate ]; then exit 0; fi
if [ "${{REJECT_NEW_RELOAD:-0}}" = 1 ] && grep -q NEW "${{SEICHE_CADDY_DEST}}"; then exit 1; fi
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
    env = {
        **os.environ,
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
    assert "caddy validate" in log
    assert "caddy reload" in log


def test_caddy_reload_failure_restores_previous_config_and_stays_red(tmp_path):
    env, installed, calls = _caddy_env(tmp_path, reject_new_reload=True)
    result = subprocess.run(
        ["bash", str(CADDY_INSTALLER)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert installed.read_text() == "OLD\n"
    log = calls.read_text()
    assert "caddy reload --config" in log and "NEW" in log
    assert "systemctl reload caddy NEW" in log
    assert "caddy reload --config" in log and "OLD" in log
    assert "previous Caddyfile restored and reloaded" in result.stdout


def test_external_smoke_definition_includes_subscribe_and_is_mockable(tmp_path):
    definitions = EXTERNAL_ROUTES.read_text()
    assert "GET /api/subscribe 200" in definitions
    calls = tmp_path / "curl.log"
    curl = _executable(
        tmp_path / "curl",
        f'''echo "$*" >> "{calls}"
printf 200
''',
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "SEICHE_EXTERNAL_BASE_URL": "https://edge.invalid",
        "SEICHE_EXTERNAL_ROUTES_FILE": str(EXTERNAL_ROUTES),
    }
    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "https://edge.invalid/api/subscribe" in calls.read_text()


def test_wrapper_runs_edge_sync_on_new_and_already_running_release():
    wrapper = (ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh").read_text()
    assert wrapper.count("deploy_caddy ||") == 2
    assert "already running ${AFTER:0:7} — checking edge config" in wrapper
