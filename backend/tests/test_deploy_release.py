"""Release-boundary contracts, exercised without a host or external network."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[2]
CADDY_INSTALLER = ROOT / "ops" / "deploy" / "install-caddy.sh"
EXTERNAL_SMOKE = ROOT / "ops" / "deploy" / "external-route-smoke.sh"
CADDYFILE = ROOT / "ops" / "Caddyfile"
EXTERNAL_ROUTES = ROOT / "ops" / "deploy" / "external-smoke-routes.txt"
FORCED_DEPLOY = ROOT / "ops" / "deploy" / "trigger-forced-deploy.sh"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-hetzner.yml"
BOX_UPDATE = ROOT / "ops" / "deploy" / "box-update.sh"
DEPLOY_WRAPPER = ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh"
PULL_UNIT = ROOT / "ops" / "deploy" / "seiche-pull.service"
PYPROJECT = ROOT / "backend" / "pyproject.toml"


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


def test_wrapper_runs_edge_sync_on_new_and_already_running_release():
    wrapper = (ROOT / "ops" / "deploy" / "seiche-deploy-wrapper.sh").read_text()
    assert wrapper.count("deploy_caddy ||") == 2
    assert "already running ${AFTER:0:7} — checking edge config" in wrapper
    assert "/api/v2/coverage" in wrapper
    assert "systemctl is-active --quiet postgresql" in wrapper
    assert wrapper.index("systemctl stop seiche-market-worker.service") < wrapper.index(
        "bash /home/seiche/update.sh"
    )
    assert wrapper.count("restore_market_services") == 5
    assert "previous market services restored" in wrapper
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
    caddy = CADDYFILE.read_text()

    assert 'psql -tAc "SHOW port"' in installer
    assert "host=/var/run/postgresql&port=$POSTGRES_PORT" in installer
    assert "could not resolve the PostgreSQL cluster port" in installer
    assert 'connection.execute("SELECT 1")' in installer
    assert "SEICHE_RAW_CAPTURE_DIR=$STATE_DIR/raw" in installer
    assert '"$STATE_DIR/validation"' in installer
    assert "SEICHE_VALIDATION_DIR=$STATE_DIR/validation" in installer
    assert "seiche-market-validation.service" in installer
    assert "seiche-market-validation.timer" in installer
    assert "ReadWritePaths=$STATE_DIR/validation" in installer
    assert "systemctl enable --now seiche-market-validation.timer" in installer
    assert 'SEICHE_DEFER_MARKET_START:-0}' in installer
    assert (
        "SEICHE_USD_FUNDING_CORE_EXPORT_DIR=$STATE_DIR/exports/"
        "us-usd-funding-core-v1"
    ) in installer
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
    assert "/api/v2/*" in caddy


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

    health = wrapper[
        wrapper.index("HEALTHY=\"\"") : wrapper.index('if [ -n "$HEALTHY" ]')
    ]
    assert health.index("market_health") < health.index("deploy_pull_unit")
    assert health.index("deploy_pull_unit") < health.index("HEALTHY=1")
    already = wrapper[
        wrapper.index('if [ "$BEFORE" = "$AFTER" ] &&') : wrapper.index(
            'if [ "$BEFORE" = "$AFTER" ]; then'
        )
    ]
    assert "deploy_pull_unit" in already
    assert already.index("deploy_pull_unit") < already.index("restore_market_services")


def test_palimpest_osint_edge_is_an_exact_static_allowlist():
    caddy = CADDYFILE.read_text()
    assert "handle_path /palimpsest/osint/*" not in caddy
    assert (
        "@palimpsest_osint path /palimpsest/osint/osint-china.json "
        "/palimpsest/osint/osint-china.json.hmac-sha256"
    ) in caddy
    assert "root * /var/lib/palimpsest-nemesis/public" in caddy
    osint_block = caddy[caddy.index("@palimpsest_osint path") : caddy.index("# Palimpsest MCP")]
    assert 'header Cache-Control "no-store"' in osint_block
    assert "stale-if-error" not in osint_block
    assert "uri strip_prefix /palimpsest/osint" in osint_block
    assert "reverse_proxy" not in osint_block
