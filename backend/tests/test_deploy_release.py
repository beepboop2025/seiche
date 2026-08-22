"""Release-boundary contracts, exercised without a host or external network."""

from __future__ import annotations

import json
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
LEGACY_INSTALLER = ROOT / "ops" / "deploy" / "install.sh"
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
MARKET_WORKER = ROOT / "ops" / "deploy" / "seiche-market-worker.service"
SOURCE_WORKER = ROOT / "ops" / "deploy" / "seiche-source-worker.service"
DATA_READINESS_SERVICE = ROOT / "ops" / "deploy" / "seiche-data-readiness.service"
DATA_READINESS_TIMER = ROOT / "ops" / "deploy" / "seiche-data-readiness.timer"
PULL_UNIT = ROOT / "ops" / "deploy" / "seiche-pull.service"
PROMOTION_UNIT = ROOT / "ops" / "deploy" / "seiche-snapshot-promote.service"
LEGACY_UPDATE_RETIRER = ROOT / "ops" / "deploy" / "retire-legacy-update-units.sh"
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


def _commit_automation_content(
    repository: Path,
    files: dict[str, str],
    *,
    message: str = "dispatch: generated edition",
    author: str = "desk@seiche.info",
) -> str:
    _git("config", "user.email", author, cwd=repository)
    for relative, body in files.items():
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
    _git("add", "--all", cwd=repository)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", message],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return _git("rev-parse", "HEAD", cwd=repository)


def _classify_automation_content(
    environment: dict[str, str], target: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$SEICHE_POLLER"; is_inert_automation_content_commit "$SEICHE_TARGET"',
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
        "SEICHE_SHA256SUM_BIN": shutil.which("sha256sum") or "/usr/bin/sha256sum",
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


def _caddy_env(
    tmp_path: Path, *, reject_new_reload: bool = False
) -> tuple[dict, Path, Path]:
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
    validation = next(
        line for line in log.splitlines() if line.startswith("caddy validate")
    )
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


def test_caddy_access_log_redacts_credential_query_values():
    caddy = CADDYFILE.read_text()
    access_log = caddy[caddy.index("(accesslog) {") : caddy.index("api.seiche.info {")]

    assert "format filter {" in access_log
    assert "wrap json" in access_log
    assert "request>uri query {" in access_log
    for name in ("api_key", "api-key", "access_token", "token"):
        assert f"replace {name} [REDACTED]" in access_log
    assert "format json" not in access_log


def test_openai_domain_challenge_is_runtime_gated_and_fail_closed():
    caddy = CADDYFILE.read_text(encoding="utf-8")
    marker = "# OpenAI plugin domain verification is deliberately dark"
    block = caddy[
        caddy.index(marker) : caddy.index("    @public {", caddy.index(marker))
    ]
    challenge_path = "/.well-known/openai-apps-challenge"
    token_placeholder = "{env.OPENAI_APPS_CHALLENGE_TOKEN}"
    token_pattern = r"^[A-Za-z0-9][A-Za-z0-9._~=-]{15,511}$"

    enabled = block.index("@openai_apps_challenge_enabled {")
    enabled_handler = block.index("handle @openai_apps_challenge_enabled {")
    fallback = block.index("@openai_apps_challenge_unavailable path")
    fallback_handler = block.index("handle @openai_apps_challenge_unavailable {")

    assert enabled < enabled_handler < fallback < fallback_handler
    assert block.count(f"path {challenge_path}") == 2
    assert "method GET HEAD" in block
    assert f"vars_regexp openai_apps_token {token_placeholder} {token_pattern}" in block
    assert f'respond "{token_placeholder}" 200' in block
    assert 'header Cache-Control "no-store, no-transform"' in block
    assert 'header Content-Type "text/plain; charset=utf-8"' in block
    assert 'respond "not here" 404' in block[fallback_handler:]

    # Runtime placeholders cannot change Caddyfile syntax. Parse-time
    # substitution, file serving, and proxying would all weaken that boundary.
    assert "{$OPENAI_APPS_CHALLENGE_TOKEN" not in block
    assert "file_server" not in block
    assert "reverse_proxy" not in block
    assert "handle_path" not in block

    runbook = (ROOT / "integrations" / "openai" / "SUBMISSION.md").read_text(
        encoding="utf-8"
    )
    assert token_pattern in runbook
    assert "systemctl restart caddy" in runbook
    assert "cmp -s" in runbook
    assert 'test "$status" = 404' in runbook
    assert "Never reuse an old value" in runbook


def test_caddy_exposes_only_the_sanitized_editorial_memory_projection():
    caddy = CADDYFILE.read_text(encoding="utf-8")
    marker = "@editorial_memory path /editorial/memory.json"
    start = caddy.index(marker)
    end = caddy.index("\n    }", start)
    block = caddy[start:end]

    assert "root * /var/lib/myquant-editorial-public" in block
    assert "uri strip_prefix /editorial" in block
    assert "file_server" in block
    assert 'Cache-Control "public, max-age=300, no-transform"' in block
    assert "handle_path /editorial/*" not in caddy
    assert "/mnt/HC_Volume_106588294/myquant-intelligence" not in block


def test_api_dropin_disables_unredacted_uvicorn_access_log():
    installer = MARKET_INSTALLER.read_text()
    api_dropin = installer[
        installer.index('cat >"$DROPIN"') : installer.index(
            'mv -f "$DROPIN"', installer.index('cat >"$DROPIN"')
        )
    ]

    assert "Environment=UVICORN_ACCESS_LOG=false" in api_dropin
    assert "ExecStart=" not in api_dropin


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
    */api/money-markets)
        type=application/json
        body='{{"ok":true,"schema":"seiche.money-market-desk.v1","sections":[{{"id":"policy_corridor"}},{{"id":"secured_distributions"}},{{"id":"repo_segments"}},{{"id":"unsecured_funding"}},{{"id":"bills_cash_curve"}},{{"id":"liquidity_buffers"}},{{"id":"mmf_plumbing"}}]}}'
        ;;
    */api/oil-funding)
        type=application/json; body='{{"schema":"seiche.oil-funding.v1"}}' ;;
    */api/estuary)
        type=application/json; body='{{"schema":"seiche.estuary.v1"}}' ;;
    */api/v2/markets)
        type=application/json; body='{{"schema":"seiche.markets.v2"}}' ;;
    */api/v2/money-markets)
        type=application/json
        body='{{"ok":true,"schema":"seiche.global-money-markets.v1","coverage":{{"declared_markets":11,"expansion_markets":52,"global_discovery_universe":63}},"expansion_ledger":[],"read_faults":[]}}'
        ;;
    */api/v2/world-markets)
        type=application/json
        body='{{"ok":true,"schema":"seiche.world-markets.v1","scope":{{"coverage_claim":"curated_partial_non_exhaustive"}}}}'
        ;;
    */api/v2/coverage)
        type=application/json; body='{{"schema":"seiche.coverage.v2"}}' ;;
    */api/v2/global/tide)
        type=application/json; body='{{"schema":"seiche.global-tide.v2"}}' ;;
    */api/subscribe) type=application/json; body='{{"gates_nothing":true}}' ;;
    */.well-known/mcp.json)
        type=application/json
        body='{{"canonicalCatalog":"https://seiche.info/.well-known/ai-catalog.json","servers":[{{"name":"io.github.beepboop2025/seiche","url":"https://api.seiche.info/mcp"}}]}}'
        ;;
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
if [ "${{SMOKE_SCENARIO:-success}}" = usd_partial ] && [[ "$url" = */api/money-markets ]]; then
    body='{{"ok":false,"schema":"seiche.money-market-desk.v1","sections":[]}}'
fi
if [ "${{SMOKE_SCENARIO:-success}}" = atlas_read_fault ] && [[ "$url" = */api/v2/money-markets ]]; then
    body='{{"ok":true,"schema":"seiche.global-money-markets.v1","coverage":{{"declared_markets":11,"expansion_markets":52,"global_discovery_universe":63}},"expansion_ledger":[],"read_faults":[{{"source":"canonical_repository"}}]}}'
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
        "GET|/api/money-markets|200|application/json|"
        '"schema":"seiche.money-market-desk.v1"'
    ) in definitions
    for identity in (
        '"ok":true',
        '"id":"policy_corridor"',
        '"id":"secured_distributions"',
        '"id":"repo_segments"',
        '"id":"unsecured_funding"',
        '"id":"bills_cash_curve"',
        '"id":"liquidity_buffers"',
        '"id":"mmf_plumbing"',
    ):
        assert f"GET|/api/money-markets|200|application/json|{identity}" in definitions
    assert (
        'GET|/api/oil-funding|200|application/json|"schema":"seiche.oil-funding.v1"'
    ) in definitions
    assert (
        'GET|/api/estuary|200|application/json|"schema":"seiche.estuary.v1"'
    ) in definitions
    assert (
        'GET|/api/v2/markets|200|application/json|"schema":"seiche.markets.v2"'
    ) in definitions
    assert (
        "GET|/api/v2/money-markets|200|application/json|"
        '"schema":"seiche.global-money-markets.v1"'
    ) in definitions
    for identity in (
        '"ok":true',
        '"declared_markets":11',
        '"expansion_markets":52',
        '"global_discovery_universe":63',
        '"expansion_ledger":[',
        '"read_faults":[]',
    ):
        assert (
            f"GET|/api/v2/money-markets|200|application/json|{identity}" in definitions
        )
    for identity in (
        '"schema":"seiche.world-markets.v1"',
        '"coverage_claim":"curated_partial_non_exhaustive"',
    ):
        assert (
            f"GET|/api/v2/world-markets|200|application/json|{identity}" in definitions
        )
    assert (
        'GET|/api/v2/coverage|200|application/json|"schema":"seiche.coverage.v2"'
    ) in definitions
    assert (
        'GET|/api/v2/global/tide|200|application/json|"schema":"seiche.global-tide.v2"'
    ) in definitions
    assert 'GET|/api/subscribe|200|application/json|"gates_nothing":true' in definitions
    for identity in (
        '"canonicalCatalog":"https://seiche.info/.well-known/ai-catalog.json"',
        '"name":"io.github.beepboop2025/seiche"',
        '"url":"https://api.seiche.info/mcp"',
    ):
        assert (
            f"GET|/.well-known/mcp.json|200|application/json|{identity}" in definitions
        )
    assert ('GET|/riptide/|200|application/json|"name": "riptide"') in definitions
    assert (
        'GET|/riptide/openapi.json|200|application/json|"title": "Riptide Public API"'
    ) in definitions
    assert (
        "GET|/palimpsest/osint/osint-china.json|200|application/json|"
        '"schema": "palimpsest-nemesis.public-snapshot"'
    ) in definitions
    env, calls = _smoke_env(tmp_path)
    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "https://edge.invalid/api/subscribe" in calls.read_text()
    assert "https://edge.invalid/.well-known/mcp.json" in calls.read_text()
    assert "--location" not in EXTERNAL_SMOKE.read_text()


def test_public_deploy_docs_retire_the_incompatible_legacy_installer():
    readme = (ROOT / "README.md").read_text()
    assert "/opt/seiche" not in readme
    assert "host release poller" in readme
    assert "Auto-deploy on every merge to main" not in readme
    workflow = DEPLOY_WORKFLOW.read_text()
    assert "workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    result = subprocess.run(
        ["bash", str(LEGACY_INSTALLER)], text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "retired" in result.stderr
    assert "RELEASE-POLLER.md" in result.stderr


@pytest.mark.parametrize("scenario", ("usd_partial", "atlas_read_fault"))
def test_external_smoke_rejects_incomplete_money_market_contracts(tmp_path, scenario):
    env, _ = _smoke_env(tmp_path, scenario)

    result = subprocess.run(
        ["bash", str(EXTERNAL_SMOKE)], env=env, text=True, capture_output=True
    )

    assert result.returncode != 0
    assert "FAIL:" in result.stderr


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
        "SEICHE_EXPECTED_TARGET_SHA": "a" * 40,
        "SEICHE_SSH_BIN": str(ssh),
    }
    result = subprocess.run(
        ["bash", str(FORCED_DEPLOY)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    assert len(lines) == 2
    assert all(line.endswith(f"<root@192.0.2.10><deploy {'a' * 40}>") for line in lines)
    workflow = DEPLOY_WORKFLOW.read_text()
    assert "target_sha:" in workflow
    assert 'SEICHE_EXPECTED_TARGET_SHA="$TARGET_SHA"' in workflow
    assert "SEICHE_DEPLOY_DEFER_WAIT_SECONDS=600" in workflow
    assert "SEICHE_DEPLOY_DEFER_RETRY_SECONDS=30" in workflow
    assert workflow.index("trigger-forced-deploy.sh") < workflow.index(
        "external-route-smoke.sh"
    )


def _forced_deploy_result(
    tmp_path: Path,
    ssh_body: str,
    *,
    wait_seconds: int,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    calls = tmp_path / "ssh-calls"
    ssh = _executable(
        tmp_path / "ssh",
        f'printf "call\\n" >>"{calls}"\n{ssh_body}',
    )
    key = tmp_path / "key"
    known = tmp_path / "known_hosts"
    key.write_text("test-only")
    known.write_text("test-only")
    env = os.environ | {
        "SEICHE_DEPLOY_HOST": "192.0.2.10",
        "SEICHE_DEPLOY_KEY_FILE": str(key),
        "SEICHE_KNOWN_HOSTS_FILE": str(known),
        "SEICHE_EXPECTED_TARGET_SHA": "a" * 40,
        "SEICHE_SSH_BIN": str(ssh),
        "SEICHE_DEPLOY_SLEEP_BIN": str(Path(shutil.which("true") or "/usr/bin/true")),
        "SEICHE_DEPLOY_DEFER_WAIT_SECONDS": str(wait_seconds),
        "SEICHE_DEPLOY_DEFER_RETRY_SECONDS": "1",
    }
    return (
        subprocess.run(
            ["bash", str(FORCED_DEPLOY)],
            env=env,
            text=True,
            capture_output=True,
        ),
        calls,
    )


def test_forced_command_retries_only_a_safe_defer(tmp_path):
    counter = tmp_path / "counter"
    counter.write_text("0\n")
    result, calls = _forced_deploy_result(
        tmp_path,
        (
            f'count=$(cat "{counter}")\n'
            "count=$((count + 1))\n"
            f'printf "%s\\n" "$count" >"{counter}"\n'
            '[ "$count" -gt 1 ] || exit 75\n'
        ),
        wait_seconds=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text().splitlines() == ["call", "call", "call"]
    assert "safely deferred; retrying" in result.stdout


def test_forced_command_gives_each_pass_its_own_defer_window(tmp_path):
    counter = tmp_path / "counter"
    counter.write_text("0\n")
    result, calls = _forced_deploy_result(
        tmp_path,
        (
            f'count=$(cat "{counter}")\n'
            "count=$((count + 1))\n"
            f'printf "%s\\n" "$count" >"{counter}"\n'
            'if [ "$count" -eq 1 ]; then sleep 2; exit 0; fi\n'
            '[ "$count" -gt 2 ] || exit 75\n'
        ),
        wait_seconds=2,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text().splitlines() == ["call", "call", "call"]
    assert "pass 2/2 safely deferred; retrying" in result.stdout


@pytest.mark.parametrize("status", [1, 42, 255])
def test_forced_command_preserves_real_failures(tmp_path, status):
    result, calls = _forced_deploy_result(
        tmp_path,
        f"exit {status}\n",
        wait_seconds=10,
    )

    assert result.returncode == status
    assert calls.read_text().splitlines() == ["call"]
    assert "retrying" not in result.stdout


def test_forced_command_returns_deferred_at_its_bound(tmp_path):
    result, calls = _forced_deploy_result(
        tmp_path,
        "exit 75\n",
        wait_seconds=0,
    )

    assert result.returncode == 75
    assert calls.read_text().splitlines() == ["call"]
    assert "remained safely deferred after 0s" in result.stderr


def test_forced_command_refuses_an_unbound_target(tmp_path):
    key = tmp_path / "key"
    known = tmp_path / "known_hosts"
    key.write_text("test-only")
    known.write_text("test-only")
    env = {
        **os.environ,
        "SEICHE_DEPLOY_HOST": "192.0.2.10",
        "SEICHE_DEPLOY_KEY_FILE": str(key),
        "SEICHE_KNOWN_HOSTS_FILE": str(known),
        "SEICHE_SSH_BIN": "/usr/bin/false",
    }
    env.pop("SEICHE_EXPECTED_TARGET_SHA", None)

    result = subprocess.run(
        ["bash", str(FORCED_DEPLOY)], env=env, text=True, capture_output=True
    )

    assert result.returncode != 0
    assert "SEICHE_EXPECTED_TARGET_SHA is required" in result.stderr


def test_box_smoke_installs_its_declared_async_test_plugin():
    optional = tomllib.loads(PYPROJECT.read_text())["project"]["optional-dependencies"]
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
    target = wrapper.index("TARGET=$LATEST")
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
    assert (
        "healthy candidate code remains running and no rollback was attempted"
        in wrapper
    )
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
    healthy_release = wrapper[wrapper.index('if [ -n "$HEALTHY" ]') :]
    assert healthy_release.index("start_market_services") < healthy_release.index(
        "deploy_caddy ||"
    )


def test_wrapper_quiesces_and_restores_source_worker_and_readiness_timer():
    wrapper = DEPLOY_WRAPPER.read_text()
    admission = wrapper.index("if ! admit_shared_host; then")
    source_capture = wrapper.index('SOURCE_WORKER_WAS_ACTIVE=""', admission)
    source_enabled_capture = wrapper.index(
        'SOURCE_WORKER_WAS_ENABLED=""', source_capture
    )
    timer_capture = wrapper.index(
        'READINESS_TIMER_WAS_ACTIVE=""', source_enabled_capture
    )
    enabled_capture = wrapper.index('READINESS_TIMER_WAS_ENABLED=""', timer_capture)
    unit_capture = wrapper.index(
        "if ! capture_preupdate_data_units; then", enabled_capture
    )
    timer_stop = wrapper.index(
        "systemctl stop seiche-data-readiness.timer seiche-data-readiness.service",
        unit_capture,
    )
    writer_stop = wrapper.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service",
        timer_stop,
    )
    update = wrapper.index("bash /home/seiche/update.sh", writer_stop)

    assert (
        source_capture
        < source_enabled_capture
        < timer_capture
        < enabled_capture
        < unit_capture
        < timer_stop
        < writer_stop
        < update
    )
    assert "seiche-source-worker.service" in wrapper[writer_stop:update]

    restore = wrapper[
        wrapper.index("restore_market_services() {") : wrapper.index(
            "start_market_services() {"
        )
    ]
    assert 'SOURCE_WORKER_WAS_ACTIVE" ]' in restore
    assert 'READINESS_TIMER_WAS_ACTIVE" ]' in restore
    restore_source = restore.index("systemctl start seiche-source-worker.service")
    restore_timer = restore.index(
        "systemctl start --no-block seiche-data-readiness.timer"
    )
    assert restore_source < restore_timer
    assert "systemctl start --no-block seiche-source-worker.service" not in restore

    start = wrapper[
        wrapper.index("start_market_services() {") : wrapper.index(
            'MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""'
        )
    ]
    assert "systemctl reset-failed" in start
    assert "seiche-market-worker.service seiche-source-worker.service" in start
    assert "seiche-market-backfill.service seiche-market-worker.service" in start
    candidate_market = start.index("if ! systemctl start")
    candidate_source = start.index("ensure_source_worker_ready")
    candidate_timer = start.index("activate_data_readiness_after_proof")
    assert candidate_market < candidate_source < candidate_timer
    assert "systemctl start --no-block" not in start
    assert "systemctl start --no-block seiche-source-worker.service" not in start
    assert "seiche-source-worker.service seiche-data-readiness.timer" not in start

    rollback = wrapper[wrapper.index("# A red warm-up") :]
    rollback_timer_stop = rollback.index(
        "systemctl stop seiche-data-readiness.timer seiche-data-readiness.service"
    )
    rollback_writer_stop = rollback.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service"
    )
    reset = rollback.index('reset -q --hard "$DEPLOYED"')
    restored = rollback.index("restore_market_services", reset)
    assert rollback_timer_stop < rollback_writer_stop < reset < restored
    assert "seiche-source-worker.service" in rollback[rollback_writer_stop:reset]


def test_wrapper_waits_for_market_worker_before_readiness(tmp_path: Path):
    wrapper = DEPLOY_WRAPPER.read_text()
    helper = wrapper[
        wrapper.index("start_market_services() {") : wrapper.index(
            'MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""'
        )
    ]
    state = tmp_path / "state"
    state.mkdir()
    fake_systemctl = _executable(
        tmp_path / "systemctl",
        """
state=${FAKE_DATA_STATE:?}
printf 'systemctl %s\n' "$*" >>"$state/calls.log"
case "$*" in
  "reset-failed seiche-market-worker.service seiche-source-worker.service")
    exit 0
    ;;
  "start seiche-market-backfill.service seiche-market-worker.service")
    touch "$state/market-worker.ready"
    exit 0
    ;;
  *--no-block*)
    exit 91
    ;;
  *)
    exit 92
    ;;
esac
""",
    )
    harness = f"""
ensure_source_worker_ready() {{
  [ -f "$FAKE_DATA_STATE/market-worker.ready" ] || return 81
  printf '%s\n' source-ready >>"$FAKE_DATA_STATE/calls.log"
}}
activate_data_readiness_after_proof() {{
  [ -f "$FAKE_DATA_STATE/market-worker.ready" ] || return 82
  printf '%s\n' readiness >>"$FAKE_DATA_STATE/calls.log"
}}
{helper}
start_market_services
"""

    result = subprocess.run(
        ["bash", "-c", harness],
        env=os.environ
        | {
            "FAKE_DATA_STATE": str(state),
            "PATH": f"{fake_systemctl.parent}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "calls.log").read_text().splitlines() == [
        "systemctl reset-failed seiche-market-worker.service seiche-source-worker.service",
        "systemctl start seiche-market-backfill.service seiche-market-worker.service",
        "source-ready",
        "readiness",
    ]


def test_wrapper_never_activates_readiness_after_source_start_failure(
    tmp_path: Path,
) -> None:
    wrapper = DEPLOY_WRAPPER.read_text()
    helper = wrapper[
        wrapper.index("start_market_services() {") : wrapper.index(
            'MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=""'
        )
    ]
    fake_systemctl = _executable(
        tmp_path / "systemctl",
        """
case "$*" in
  "reset-failed seiche-market-worker.service seiche-source-worker.service") exit 0 ;;
  "start seiche-market-backfill.service seiche-market-worker.service") exit 0 ;;
  *) exit 92 ;;
esac
""",
    )
    harness = f"""
ensure_source_worker_ready() {{ return 83; }}
activate_data_readiness_after_proof() {{ touch "$FAKE_ACTIVATED"; }}
{helper}
start_market_services
"""
    activated = tmp_path / "activated"

    result = subprocess.run(
        ["bash", "-c", harness],
        env=os.environ
        | {
            "FAKE_ACTIVATED": str(activated),
            "PATH": f"{fake_systemctl.parent}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert not activated.exists()


def test_wrapper_starts_source_worker_before_strict_candidate_health():
    wrapper = DEPLOY_WRAPPER.read_text()

    accepted_branch = wrapper.index(
        'if [ "$BEFORE" = "$AFTER" ] && [ "$DEPLOYED" = "$AFTER" ]'
    )
    normal_branch = wrapper.index('HEALTHY=""', accepted_branch)
    accepted_body = wrapper[accepted_branch:normal_branch]
    assert accepted_body.index("ensure_source_worker_ready") < accepted_body.index(
        'candidate_health_wait 900 "$AFTER"'
    )

    normal_body = wrapper[normal_branch:]
    assert normal_body.index("ensure_source_worker_ready") < normal_body.index(
        'candidate_health_wait 900 "$AFTER"'
    )


def test_wrapper_restores_the_worker_unit_when_candidate_code_rolls_back():
    wrapper = DEPLOY_WRAPPER.read_text()
    helper = wrapper[
        wrapper.index("restore_preupdate_market_worker_unit()") : wrapper.index(
            "restore_preupdate_api()"
        )
    ]

    assert 'git -C "$APP" show' in helper
    assert '"${restore_sha}:ops/deploy/seiche-market-worker.service"' in helper
    assert 'systemd-analyze verify "$candidate"' in helper
    assert 'mv -f "$candidate" "$destination"' in helper
    assert helper.index('mv -f "$candidate" "$destination"') < helper.index(
        "systemctl daemon-reload"
    )
    assert "MARKET_WORKER_WAS_ENABLED" in helper
    assert "systemctl enable seiche-market-worker.service" in helper
    assert "systemctl disable seiche-market-worker.service" in helper
    assert "systemctl is-enabled --quiet seiche-market-worker.service" in helper

    deploy = wrapper.index("MARKET_WORKER_UNIT_MAY_HAVE_CHANGED=1")
    provision = wrapper.index("deploy_market_platform ||", deploy)
    assert deploy < provision

    recovery = wrapper[
        wrapper.index("restore_pre_restart_services()") : wrapper.index(
            "systemctl stop seiche-market-worker.service"
        )
    ]
    assert (
        recovery.index("restore_preupdate_market_worker_unit")
        < recovery.index("restore_preupdate_data_units")
        < recovery.index("restore_quiesced_api")
        < recovery.index("restore_market_services")
    )

    rollback = wrapper[wrapper.index("rolling the service back to") :]
    assert (
        rollback.index("restore_preupdate_market_worker_unit")
        < rollback.index("restore_preupdate_data_units")
        < rollback.index("systemctl restart seiche-api")
        < rollback.index("restore_market_services")
    )


def test_wrapper_restores_exact_predeploy_data_units_and_readiness_timer_state():
    wrapper = DEPLOY_WRAPPER.read_text()
    capture = wrapper[
        wrapper.index("capture_preupdate_data_units() {") : wrapper.index(
            "cleanup_data_unit_restore_stage() {"
        )
    ]
    restore = wrapper[
        wrapper.index("restore_preupdate_data_units() {") : wrapper.index(
            "trap 'cleanup_preupdate_data_units || true' EXIT"
        )
    ]
    unit_names_start = wrapper.index("DATA_UNIT_NAMES=(")
    unit_names = wrapper[
        unit_names_start : wrapper.index("DATA_UNIT_ROLLBACK_DIR", unit_names_start)
    ]

    for unit in (
        "seiche-market-backfill.service",
        "seiche-source-worker.service",
        "seiche-data-readiness.service",
        "seiche-data-readiness.timer",
    ):
        assert unit in unit_names
    assert 'mktemp -d "$DEPLOY_RUNTIME_DIR/.data-units.XXXXXX"' in capture
    assert '[ -L "$destination" ] || [ ! -f "$destination" ]' in capture
    assert 'cp -p -- "$destination"' in capture
    assert '"$DATA_UNIT_ROLLBACK_DIR/$unit.present"' in capture
    assert '"$DATA_UNIT_ROLLBACK_DIR/$unit.absent"' in capture

    assert 'systemd-analyze verify "${candidates[@]}"' in restore
    assert 'mv -f "$stage/$unit" "$destination"' in restore
    assert 'rm -f -- "$destination"' in restore
    assert restore.index('mv -f "$stage/$unit" "$destination"') < restore.index(
        "systemctl daemon-reload"
    )
    assert "SOURCE_WORKER_WAS_ENABLED" in restore
    assert "READINESS_TIMER_WAS_ENABLED" in restore
    assert "systemctl enable seiche-source-worker.service" in restore
    assert "systemctl disable seiche-source-worker.service" in restore
    assert "systemctl is-enabled --quiet seiche-source-worker.service" in restore
    assert "systemctl enable seiche-data-readiness.timer" in restore
    assert "systemctl disable seiche-data-readiness.timer" in restore
    assert "systemctl is-enabled --quiet seiche-data-readiness.timer" in restore

    capture_call = wrapper.index("if ! capture_preupdate_data_units; then")
    quiesce = wrapper.index(
        "systemctl stop seiche-data-readiness.timer seiche-data-readiness.service",
        capture_call,
    )
    provision_flag = wrapper.index("DATA_UNITS_MAY_HAVE_CHANGED=1", quiesce)
    provision = wrapper.index("deploy_market_platform ||", provision_flag)
    assert capture_call < quiesce < provision_flag < provision

    recovery = wrapper[wrapper.index("restore_pre_restart_services() {") : quiesce]
    assert (
        recovery.index("restore_preupdate_market_worker_unit")
        < recovery.index("restore_preupdate_data_units")
        < recovery.index("restore_quiesced_api")
        < recovery.index("restore_market_services")
    )

    rollback = wrapper[wrapper.index("rolling the service back to") :]
    assert (
        rollback.index("restore_preupdate_market_worker_unit")
        < rollback.index("restore_preupdate_data_units")
        < rollback.index("systemctl restart seiche-api")
        < rollback.index("rollback_health_wait 480")
        < rollback.index("restore_market_services")
    )


def _run_readiness_activation_helper(
    script_path: Path,
    tmp_path: Path,
    *,
    readiness_mode: str,
    fail_command: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    script = script_path.read_text()
    helper_start = script.index("DATA_READINESS_PREFLIGHT_REQUIRED_UNITS=")
    if script_path == DEPLOY_WRAPPER:
        helper_end = script.index("start_market_services() {", helper_start)
    else:
        helper_end = script.index(
            'if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then',
            helper_start,
        )
    helper = script[helper_start:helper_end]
    local_bash = shutil.which("bash")
    assert local_bash is not None
    helper = helper.replace("/usr/bin/bash", local_bash)

    app = tmp_path / "app"
    readiness = app / "ops" / "deploy" / "seiche-data-readiness.sh"
    readiness.parent.mkdir(parents=True)
    _executable(
        readiness,
        """
state=${FAKE_DATA_STATE:?}
count_file=$state/readiness-count
count=0
[ ! -f "$count_file" ] || count=$(cat "$count_file")
count=$((count + 1))
printf '%s\n' "$count" >"$count_file"
kind=full
[ "${SEICHE_DATA_READINESS_PROOF_ONLY:-0}" != "1" ] || kind=proof
printf 'readiness %s %s\n' "$kind" "${SEICHE_DATA_READINESS_REQUIRED_UNITS:-}" >>"$state/calls.log"
case "${FAKE_READINESS_MODE:?}" in
  current) exit 0 ;;
  fresh) [ "$kind" = full ] || [ "$count" -gt 1 ] ;;
  operational-fail) [ "$kind" = proof ] ;;
  always-fail) exit 1 ;;
  *) exit 64 ;;
esac
""",
    )
    state = tmp_path / "state"
    state.mkdir()
    fake_systemctl = _executable(
        tmp_path / "systemctl",
        """
state=${FAKE_DATA_STATE:?}
printf 'systemctl %s\n' "$*" >>"$state/calls.log"
if [ "$*" = "${FAKE_FAIL_COMMAND:-}" ]; then
  exit 1
fi
if [ "$*" = "enable --now seiche-data-readiness.timer" ]; then
  touch "$state/readiness-timer.enabled"
fi
""",
    )
    environment = os.environ | {
        "APP": str(app),
        "APP_DIR": str(app),
        "FAKE_DATA_STATE": str(state),
        "FAKE_READINESS_MODE": readiness_mode,
        "FAKE_FAIL_COMMAND": fail_command,
        "PATH": f"{fake_systemctl.parent}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", "-c", f"{helper}\nactivate_data_readiness_after_proof"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    calls = (state / "calls.log").read_text().splitlines()
    return result, calls, state


@pytest.mark.parametrize("script_path", [DEPLOY_WRAPPER, MARKET_INSTALLER])
def test_fresh_v2_host_proves_backup_restore_and_readiness_before_timer(
    script_path: Path, tmp_path: Path
):
    result, calls, state = _run_readiness_activation_helper(
        script_path, tmp_path, readiness_mode="fresh"
    )

    assert result.returncode == 0, result.stderr
    assert [call.split()[0] for call in calls] == [
        "readiness",
        "systemctl",
        "systemctl",
        "readiness",
        "readiness",
        "systemctl",
    ]
    assert calls[1:] == [
        "systemctl start seiche-market-backup.service",
        "systemctl start seiche-market-restore-check.service",
        calls[3],
        calls[4],
        "systemctl enable --now seiche-data-readiness.timer",
    ]
    assert calls[0].startswith("readiness proof ")
    assert calls[0] == calls[3]
    assert calls[4].startswith("readiness full ")
    assert "seiche-data-readiness.timer" not in calls[4]
    assert (state / "readiness-timer.enabled").is_file()


@pytest.mark.parametrize("script_path", [DEPLOY_WRAPPER, MARKET_INSTALLER])
def test_current_v2_proof_activates_timer_without_redundant_restore_drill(
    script_path: Path, tmp_path: Path
):
    result, calls, state = _run_readiness_activation_helper(
        script_path, tmp_path, readiness_mode="current"
    )

    assert result.returncode == 0, result.stderr
    assert len(calls) == 3
    assert calls[0].startswith("readiness proof ")
    assert calls[1].startswith("readiness full ")
    assert calls[2] == "systemctl enable --now seiche-data-readiness.timer"
    assert (state / "readiness-timer.enabled").is_file()


@pytest.mark.parametrize("script_path", [DEPLOY_WRAPPER, MARKET_INSTALLER])
@pytest.mark.parametrize(
    ("readiness_mode", "fail_command"),
    [
        ("fresh", "start seiche-market-backup.service"),
        ("fresh", "start seiche-market-restore-check.service"),
        ("always-fail", ""),
        ("operational-fail", ""),
        ("current", "enable --now seiche-data-readiness.timer"),
    ],
)
def test_readiness_bootstrap_failures_leave_timer_disabled(
    script_path: Path,
    tmp_path: Path,
    readiness_mode: str,
    fail_command: str,
):
    result, calls, state = _run_readiness_activation_helper(
        script_path,
        tmp_path,
        readiness_mode=readiness_mode,
        fail_command=fail_command,
    )

    assert result.returncode != 0
    assert calls[0].startswith("readiness proof ")
    assert not (state / "readiness-timer.enabled").exists()
    if readiness_mode == "operational-fail":
        assert len(calls) == 2
        assert calls[1].startswith("readiness full ")
        assert not any(
            call.startswith("systemctl start seiche-market-backup") for call in calls
        )
    if fail_command != "enable --now seiche-data-readiness.timer":
        assert "systemctl enable --now seiche-data-readiness.timer" not in calls


def test_market_platform_units_are_independent_and_postgres_backed():
    installer = (ROOT / "ops" / "deploy" / "install-market-platform.sh").read_text()
    worker = MARKET_WORKER.read_text()
    source_worker = SOURCE_WORKER.read_text()
    readiness_service = DATA_READINESS_SERVICE.read_text()
    readiness_timer = DATA_READINESS_TIMER.read_text()
    backfill = (ROOT / "ops" / "deploy" / "seiche-market-backfill.service").read_text()
    validation = (
        ROOT / "ops" / "deploy" / "seiche-market-validation.service"
    ).read_text()
    validation_timer = (
        ROOT / "ops" / "deploy" / "seiche-market-validation.timer"
    ).read_text()
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
    assert "seiche-source-worker.service" in installer
    assert "seiche-data-readiness.service" in installer
    assert "seiche-data-readiness.timer" in installer
    assert "ReadWritePaths=$RECOVERY_PROOF_DIR" in installer
    assert "systemctl enable --now seiche-market-validation.timer" in installer
    readiness_boundary = installer.index("DATA_READINESS_PREFLIGHT_REQUIRED_UNITS=")
    activation_reload = installer.rindex(
        "systemctl daemon-reload", 0, readiness_boundary
    )
    early_enable = installer[activation_reload:readiness_boundary]
    assert (
        "seiche-data-readiness.timer"
        not in early_enable.split(
            "systemctl enable --now seiche-market-validation.timer", 1
        )[0]
    )
    assert "SEICHE_DEFER_MARKET_START:-0}" in installer
    worker_verify = installer.index("worker unit failed verification")
    worker_install = installer.index(
        'mv -f "$WORKER_UNIT_STAGE_DIR/seiche-market-worker.service"'
    )
    assert installer.index("systemd-analyze verify", 0, worker_verify) < worker_install
    data_verify = installer.index("data-plane units failed verification")
    source_install = installer.index(
        'mv -f "$DATA_UNIT_STAGE_DIR/seiche-source-worker.service"'
    )
    readiness_install = installer.index(
        'mv -f "$DATA_UNIT_STAGE_DIR/seiche-data-readiness.timer"'
    )
    backfill_install = installer.index(
        'mv -f "$DATA_UNIT_STAGE_DIR/seiche-market-backfill.service"'
    )
    assert (
        '"$DATA_UNIT_STAGE_DIR/seiche-market-backfill.service"'
        in installer[installer.index("cleanup() {") : data_verify]
    )
    assert installer.index("systemd-analyze verify", worker_install, data_verify) < (
        source_install
    )
    assert data_verify < source_install < readiness_install < backfill_install
    assert "SEICHE_FUNDING_EXPORT_READER_GROUP" in installer
    assert 'groupadd --system "$EXPORT_READER_GROUP"' in installer
    assert 'setfacl -m "g:$EXPORT_READER_GROUP:--x"' in installer
    assert 'chmod 2750 "$FUNDING_EXPORT_DIR"' in installer
    assert 'chmod 0640 "$FUNDING_EXPORT_FILE"' in installer
    assert "setfacl -R" not in installer
    assert 'find "$FUNDING_EXPORT_DIR"' not in installer
    funding_acl = installer[: installer.index("ENV_STAGE=")]
    assert "usermod" not in funding_acl
    assert 'FUNDING_EXPORT_DIR="$STATE_DIR/exports/us-usd-funding-core-v1"' in installer
    assert "SEICHE_USD_FUNDING_CORE_EXPORT_DIR=$FUNDING_EXPORT_DIR" in installer
    assert "seiche-market-backfill.service seiche-market-worker.service" in installer
    installer_start = installer[
        installer.index('if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then') :
    ]
    installer_source = installer_start.index(
        "systemctl start seiche-source-worker.service"
    )
    installer_timer = installer_start.index("activate_data_readiness_after_proof")
    assert installer_source < installer_timer
    assert "systemctl start --no-block" not in installer_start
    assert (
        "systemctl start --no-block seiche-source-worker.service" not in installer_start
    )
    assert (
        "seiche-source-worker.service seiche-data-readiness.timer"
        not in installer_start
    )
    assert "ExecStart=/home/seiche/app/backend/.venv/bin/seiche market-worker" in worker
    assert "EnvironmentFile=-/etc/seiche/rbnz-access.env" in worker
    assert "EnvironmentFile=-/etc/seiche/bok-ecos.env" in worker
    assert "Restart=always" in worker
    assert "OnFailure=undertow-failure-alert@%n.service" in worker
    assert "StartLimitIntervalSec=15min" in worker
    assert "StartLimitBurst=5" in worker
    assert (
        "ExecStart=/home/seiche/app/backend/.venv/bin/seiche "
        "source-worker --poll-seconds 300"
    ) in source_worker
    assert "Type=notify" in source_worker
    assert "NotifyAccess=main" in source_worker
    assert "WatchdogSec=180" in source_worker
    assert "TimeoutStartSec=15min" in source_worker
    assert "Restart=always" in source_worker
    assert "OnFailure=undertow-failure-alert@%n.service" in source_worker
    assert "StartLimitIntervalSec=15min" in source_worker
    assert "StartLimitBurst=5" in source_worker
    assert "CapabilityBoundingSet=\n" in source_worker
    assert "ProtectSystem=strict" in source_worker
    assert "ProtectHome=read-only" in source_worker
    assert "MemoryMax=2G" in source_worker
    assert "TasksMax=128" in source_worker
    assert "ReadWritePaths=/home/seiche/app/backend/data" in source_worker
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in source_worker
    assert "OnFailure=undertow-failure-alert@%n.service" in readiness_service
    assert (
        "After=seiche-market-worker.service seiche-source-worker.service"
        in readiness_timer
    )
    assert "Type=oneshot" in backfill
    assert "EnvironmentFile=-/etc/seiche/rbnz-access.env" in backfill
    assert "EnvironmentFile=-/etc/seiche/bok-ecos.env" in backfill
    assert "TimeoutStartSec=2h" in backfill
    assert "CPUQuota=100%" in backfill
    assert "CPUWeight=10" in backfill
    assert "IOWeight=10" in backfill
    assert "Nice=10" in backfill
    assert (
        "ExecStart=/home/seiche/app/backend/.venv/bin/seiche market-validate"
        in validation
    )
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
    assert '"$CP_BIN" -R -- "$API_DATA_DIR/." "$API_STAGE/"' in backup_script
    assert "cp -a --" not in backup_script
    assert "CPUQuota=50%" in backup
    assert "MemoryMax=1G" in backup
    assert "ProtectSystem=strict" in backup
    assert "RestrictAddressFamilies=AF_UNIX" in backup
    assert "NoNewPrivileges=true" in backup
    assert "RestrictSUIDSGID=true" in backup
    assert "CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_SETGID CAP_SETUID" in backup
    assert "CAP_CHOWN" not in backup
    assert "AmbientCapabilities=CAP_SETGID CAP_SETUID" in backup
    backup_capabilities = next(
        line
        for line in backup.splitlines()
        if line.startswith("CapabilityBoundingSet=")
    )
    assert "CAP_CHOWN" not in backup_capabilities
    assert "ReadWritePaths=/var/backups/seiche-market /run/lock" in backup
    assert (
        "ReadOnlyPaths=/home/seiche/app /var/lib/seiche /var/lib/seiche-deploy"
        in backup
    )
    assert "/var/lib/seiche-deploy/deployed-sha" in backup_script
    assert "OnCalendar=*-*-* 02:00:00 UTC" in backup_timer
    assert "RandomizedDelaySec=10m" in backup_timer
    assert "Persistent=true" in backup_timer
    assert "ExecStart=/usr/bin/flock --wait 300" in restore
    assert "seiche-market-restore-check.sh" in restore
    assert "ReadOnlyPaths=/home/seiche/app /var/backups/seiche-market" in restore
    assert "ReadWritePaths=/var/lib/seiche-recovery-proof /run/lock" in restore
    assert "CAP_CHOWN" in restore
    assert "CAP_DAC_OVERRIDE" in restore
    assert "NoNewPrivileges=true" in restore
    assert "RestrictSUIDSGID=true" in restore
    assert "AmbientCapabilities=CAP_SETGID CAP_SETUID" in restore
    assert "OnCalendar=Sun *-*-* 07:30:00 UTC" in restore_timer
    assert "RandomizedDelaySec=15m" in restore_timer
    assert "Persistent=true" in restore_timer
    assert "/api/v2/*" in caddy
    assert "RBNZ_ACCESS_ENV_FILE=/etc/seiche/rbnz-access.env" in installer
    assert "SEICHE_RBNZ_ACCESS_ENV_FILE" not in installer
    assert "RBNZ access env ownership/mode is unsafe" in installer
    assert "SEICHE_RBNZ_ACCESS_APPROVAL_SHA256=[0-9a-f]{64}" in installer
    assert "SEICHE_RBNZ_ACCESS_APPROVAL_VALID_UNTIL=[0-9]{4}" in installer
    assert "BOK_ECOS_ENV_FILE=/etc/seiche/bok-ecos.env" in installer
    assert "SEICHE_BOK_ECOS_ENV_FILE" not in installer
    assert "BOK ECOS env ownership/mode is unsafe" in installer
    assert "SEICHE_BOK_ECOS_API_KEY=[A-Za-z0-9]{8,128}" in installer
    assert 'wc -l <"$BOK_ECOS_ENV_FILE"' in installer


def test_cfets_approval_artifact_is_validated_and_wired_to_both_collectors():
    installer = MARKET_INSTALLER.read_text()
    worker = MARKET_WORKER.read_text()
    backfill = (ROOT / "ops" / "deploy" / "seiche-market-backfill.service").read_text()
    source_worker = SOURCE_WORKER.read_text()
    runbook = (ROOT / "docs" / "CFETS_ACCESS_BOUNDARY.md").read_text()

    for unit in (worker, backfill):
        assert "EnvironmentFile=-/etc/seiche/cfets-access.env" in unit
        assert "ReadOnlyPaths=-/etc/seiche/cfets-approval.conf" in unit
        assert "-/etc/seiche/cfets-licence-evidence.pdf" in unit
    assert "cfets-access.env" not in source_worker
    assert "cfets-approval.conf" not in source_worker

    assert "CFETS_ACCESS_ENV_FILE=/etc/seiche/cfets-access.env" in installer
    assert "CFETS_APPROVAL_FILE=/etc/seiche/cfets-approval.conf" in installer
    assert (
        "CFETS_LICENCE_EVIDENCE_FILE=/etc/seiche/cfets-licence-evidence.pdf"
        in installer
    )
    assert "SEICHE_CFETS_ACCESS_ENV_FILE" not in installer
    assert "SEICHE_CFETS_APPROVAL_FILE" not in installer
    assert "CFETS access env ownership/mode is unsafe" in installer
    assert "CFETS approval artifact ownership/mode is unsafe" in installer
    assert "CFETS approval artifact size is unsafe" in installer
    assert "CFETS approval artifact contract is invalid" in installer
    assert "CFETS approval artifact digest mismatch" in installer
    assert "CFETS licence evidence ownership/mode is unsafe" in installer
    assert "CFETS licence evidence size is unsafe" in installer
    assert "CFETS licence evidence digest mismatch" in installer
    assert "CFETS approval review window is unsafe" in installer
    assert "CFETS approval artifacts have no access env pin" in installer
    assert "SEICHE_CFETS_APPROVAL_PATH=$CFETS_APPROVAL_FILE" in installer
    assert "SEICHE_CFETS_APPROVAL_SHA256=[0-9a-f]{64}" in installer
    assert "stat -c '%U:%G:%a:%h' \"$CFETS_APPROVAL_FILE\"" in installer
    assert '/usr/bin/sha256sum "$CFETS_APPROVAL_FILE"' in installer
    assert "schema=seiche.cfets-approval.v2" in installer
    assert "upstream_products=FDR007,SHIBOR_ON" in installer
    assert "canonical_outputs=CN.CFETS.FDR007,CN.CFETS.SHIBOR_ON" in installer
    assert (
        "collection_scope=automated_bounded_fdr007_and_shibor_on_history"
        in installer
    )
    assert "permitted_use=internal_research_only" in installer
    assert "publication=prohibited" in installer
    assert "raw_response_retention=prohibited" in installer
    assert "retained_projection=event_date,value" in installer
    assert "licence_evidence_path=$CFETS_LICENCE_EVIDENCE_FILE" in installer
    assert "/usr/bin/sha256sum" in installer
    assert '"$CFETS_LICENCE_EVIDENCE_FILE" | cut' in installer
    assert "CFETS_REVIEW_DAYS" in installer
    assert '"$CFETS_REVIEW_DAYS" -gt 366' in installer
    assert installer.index("CFETS approval artifact digest mismatch") < installer.index(
        "WORKER_UNIT_STAGE_DIR=$(mktemp"
    )

    for contract in (
        "root:seiche",
        "0640",
        "internal_research_only",
        "publication=prohibited",
        "no more than 366 days",
        "before every",
    ):
        assert contract in runbook


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
    assert not (systemd_dir / "timers.target.wants" / "seiche-update.timer").exists()
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
    runbook = (ROOT / "docs" / "FORWARD_CHAIN_INCIDENT_2026-08-11.md").read_text()

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
    exact_file = "/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json"

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
    private_edge = caddy[caddy.index("@release_health path") : caddy.index("@public {")]

    assert f"@release_health path {route}" in private_edge
    assert 'respond "not here" 404' in private_edge
    assert "reverse_proxy" not in private_edge
    public_edge = caddy[caddy.index("@public {") : caddy.index("@login {")]
    assert route not in public_edge


def test_event_analysis_edge_is_post_only_and_excluded_from_public_get():
    caddy = CADDYFILE.read_text()
    route = "/api/event-analysis"
    public_matcher = caddy[caddy.index("@public {") : caddy.index("@event_analysis {")]
    event_handler = caddy[caddy.index("@event_analysis {") : caddy.index("@login {")]
    other_post_matcher = caddy[
        caddy.index("@login {") : caddy.index("handle @public {")
    ]

    assert "method GET HEAD" in public_matcher
    assert route not in public_matcher
    assert "method POST" in event_handler
    assert route in event_handler
    assert "request_body" in event_handler
    assert "max_size 8KiB" in event_handler
    assert "max_size 8KB" not in event_handler
    assert "reverse_proxy 127.0.0.1:8787" in event_handler
    assert "/api/auth/login" not in event_handler
    assert route not in other_post_matcher
    assert "/api/auth/login" in other_post_matcher
    assert caddy.count(route) == 1


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
        wrapper.index('HEALTHY=""') : wrapper.index('if [ -n "$HEALTHY" ]')
    ]
    assert "if systemctl restart seiche-api; then" in health
    assert "RESTARTED=1" in health
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
    assert "if ! systemctl is-active --quiet seiche-api; then" in already
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
    assert already.index("deploy_pull_unit") < already.index("promote_snapshot_handoff")
    promotion_failure = already[already.index("promote_snapshot_handoff ||") :]
    assert "restore_market_services" in promotion_failure
    assert "healthy running candidate kept in place" in promotion_failure
    assert "accepted release did not recover strict health" in already


def test_market_health_matches_the_candidate_registry_without_a_count_literal():
    wrapper = DEPLOY_WRAPPER.read_text()
    health = wrapper[
        wrapper.index("market_health()") : wrapper.index("promote_snapshot_handoff()")
    ]

    assert "from seiche.markets.registry import default_registry" in health
    assert "expected={pack.market_id for pack in default_registry().list()}" in health
    assert 'actual=[market["market_id"] for market in p["markets"]]' in health
    assert "len(actual) == len(expected) and set(actual) == expected" in health
    assert 'len(p["markets"]) ==' not in health


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
        "ExecStart=/home/seiche/app/backend/.venv/bin/python -m seiche.release_promote"
    ) in unit
    assert (
        "ExecStopPost=+/usr/bin/rm -f /run/seiche-release/promotion-request.json"
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
    assert (
        'mv -f "$PROMOTION_UNIT_STAGE_DIR/seiche-snapshot-promote.service"' in installer
    )
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
    assert (
        'printf \'{"expected_sha":"%s","activation_token":"%s"}\\n\'' in request_writer
    )
    assert "chown root:seiche" in request_writer
    assert "chmod 0640" in request_writer
    assert 'mv -f "$stage" "$PROMOTION_REQUEST"' in request_writer
    assert "/etc/seiche/market.env" not in wrapper
    assert "source /etc/seiche" not in wrapper
    assert "eval " not in wrapper
    assert 'git -C "$APP" diff-index --quiet "$AFTER" --' in wrapper
    assert "--others --exclude-standard -- backend" in wrapper
    assert "--others --ignored --exclude-standard -- backend" in wrapper
    assert "$0 !~ /^backend\\/\\.venv\\//" in wrapper
    assert "$0 !~ /\\/__pycache__\\//" in wrapper
    assert 'if ! AFTER=$(runuser -u seiche -- git -C "$APP" rev-parse HEAD)' in wrapper
    unresolved = wrapper[
        wrapper.index("if ! AFTER=$(runuser") : wrapper.index(
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
    assert (
        state_writer.index('/usr/bin/sync -f "$stage"')
        < state_writer.index('mv -f "$stage" "$STATE"')
        < state_writer.index('/usr/bin/sync "$DEPLOY_STATE_DIR"')
    )
    assert 'SEICHE_DEPLOYED_SHA="$DEPLOYED"' in wrapper
    assert "/home/seiche/.seiche-deployed-sha" not in wrapper
    assert "DEPLOYED=${SEICHE_DEPLOYED_SHA:-}" in BOX_UPDATE.read_text()
    deploy_lock = wrapper.index("flock --nonblock 9")
    assert "DEPLOY_RUNTIME_DIR=/run/seiche-deploy" in wrapper[:deploy_lock]
    assert (
        'install -d -o root -g root -m 0700 "$DEPLOY_RUNTIME_DIR"'
        in wrapper[:deploy_lock]
    )
    assert 'exec 9>"$DEPLOY_LOCK"' in wrapper[:deploy_lock]
    assert "another seiche deployment is still running" in wrapper
    assert deploy_lock < wrapper.index("# The sha whose code is actually RUNNING")


def test_deploy_controller_pins_a_locally_tested_target_before_quiescing():
    wrapper = DEPLOY_WRAPPER.read_text()
    resolved = wrapper.index(
        'LATEST=$(runuser -u seiche -- git -C "$APP" rev-parse origin/main)'
    )
    constrained = wrapper.index("EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}")
    stopped = wrapper.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service"
    )
    checked = wrapper[constrained:stopped]

    assert resolved < constrained < stopped
    assert 'valid_release_sha "$EXPECTED_TARGET"' in checked
    assert '"$EXPECTED_TARGET" "$LATEST"' in checked
    assert "reviewed target is not a fetched commit on main" in checked
    assert "TARGET=$EXPECTED_TARGET" in checked
    assert "SSH_ORIGINAL_COMMAND" in checked
    assert "exit 1" in checked


def test_deploy_requires_a_stable_quiet_host_before_quiescing_services():
    wrapper = DEPLOY_WRAPPER.read_text()
    helper_start = wrapper.index("admit_shared_host() {")
    helper_end = wrapper.index("write_deployed_state()", helper_start)
    helper = wrapper[helper_start:helper_end]
    target = wrapper.index("TARGET=$LATEST", helper_end)
    admission = wrapper.index("if ! admit_shared_host; then", target)
    capture = wrapper.index('MARKET_WORKER_WAS_ACTIVE=""', admission)
    stop = wrapper.index(
        "systemctl stop seiche-market-worker.service seiche-market-backfill.service",
        capture,
    )

    for marker in (
        "/usr/bin/getconf _NPROCESSORS_ONLN",
        "cpus * 0.75",
        "sample <= 3",
        "</proc/loadavg",
        "load_five",
        '-v observed="$load_one"',
        '-v observed="$load_five"',
        "observed <= limit",
        "sleep 10",
        "production unchanged",
    ):
        assert marker in helper
    assert "SEICHE_DEPLOY_MAX" not in helper
    assert "one-minute load" in helper
    assert "five-minute load" in helper
    assert target < admission < capture < stop
    assert "exit 75" in wrapper[admission:capture]
    admission_case = wrapper.index(
        'case "${SEICHE_DEPLOY_ADMISSION_ONLY:-0}" in', helper_start
    )
    admission_only = wrapper[
        admission_case : wrapper.index('DEPLOYED_STATE_RENAMED=""', helper_end)
    ]
    assert "SEICHE_DEPLOY_ADMISSION_ONLY" in admission_only
    assert "forced deploy cannot request admission-only mode" in admission_only
    forced_request_rejected = admission_only.index(
        "forced deploy cannot request admission-only mode"
    )
    admission_call = admission_only.index("if admit_shared_host; then")
    admitted = admission_only.index("exit 0", admission_call)
    deferred = admission_only.index("exit 75", admitted)
    assert forced_request_rejected < admission_call < admitted < deferred

    comparator = "BEGIN { exit !(observed <= limit) }"
    for observed, expected in (("11.99", 0), ("12.00", 0), ("12.01", 1)):
        result = subprocess.run(
            [
                "/usr/bin/awk",
                "-v",
                f"observed={observed}",
                "-v",
                "limit=12.00",
                comparator,
            ],
            check=False,
        )
        assert result.returncode == expected


def test_release_poller_gates_one_exact_detached_candidate_before_deploy():
    poller = RELEASE_POLLER.read_text()
    selected = poller.index(
        'TARGET=$(as_service git -C "$APP_DIR" rev-parse origin/main)'
    )
    inert_content = poller.index(
        'if is_inert_automation_content_commit "$TARGET"', selected
    )
    signature = poller.index('verify_target_signature "$TARGET"', inert_content)
    admission = poller.index("SEICHE_DEPLOY_ADMISSION_ONLY=1", signature)
    detached = poller.index(
        'as_service git -C "$APP_DIR" worktree add --detach "$CANDIDATE_DIR" "$TARGET"'
    )
    full_gate = poller.index('"$VENV/bin/python" -m pytest backend/tests -q', detached)
    refetched = poller.index(
        'as_service git -C "$APP_DIR" fetch -q origin main', full_gate
    )
    superseded = poller.index('if [ "$LATEST" != "$TARGET" ]', refetched)
    gate_receipt = poller.index('write_receipt gate "$GATE_RECEIPT"', superseded)
    gate_only = poller.index('if [ "$GATE_ONLY" = 1 ]', gate_receipt)
    post_gate_admission = poller.index("wait_for_post_gate_admission", gate_only)
    post_gate_refetch = poller.index(
        'as_service git -C "$APP_DIR" fetch -q origin main', post_gate_admission
    )
    post_gate_superseded = poller.index(
        'if [ "$LATEST" != "$TARGET" ]', post_gate_refetch
    )
    deploy_status = poller.index("DEPLOY_STATUS=0", post_gate_superseded)
    deployed = poller.index(
        'SEICHE_EXPECTED_TARGET_SHA="$TARGET" "$DEPLOY_WRAPPER"', deploy_status
    )

    assert (
        selected
        < inert_content
        < signature
        < admission
        < detached
        < full_gate
        < refetched
        < superseded
        < gate_receipt
        < gate_only
        < post_gate_admission
        < post_gate_refetch
        < post_gate_superseded
        < deploy_status
        < deployed
    )
    assert 'CANDIDATE_PARENT="$STATE_DIR/candidates"' in poller
    assert 'install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700' in poller
    assert 'exec 8>"$CONTROL_LOCK"' in poller
    assert "flock --nonblock 8" in poller
    assert "ADMISSION_STATUS=0" in poller[signature:detached]
    assert 'case "$ADMISSION_STATUS"' in poller[signature:detached]
    assert "deferred with production unchanged" in poller[signature:detached]
    assert '"$CANDIDATE_DIR/backend[dev,collectors]"' in poller
    gate_slice = poller[detached:gate_receipt]
    assert 'as_service "$TIMEOUT"' in gate_slice
    assert "EnvironmentFile" not in gate_slice
    assert "production unchanged" in poller[superseded:gate_receipt]
    assert "gate-only success" in poller[gate_only:deployed]
    post_gate_slice = poller[post_gate_admission:deploy_status]
    assert "POST_GATE_ADMISSION_STATUS" in post_gate_slice
    assert "bounded post-gate wait" in post_gate_slice
    assert "after post-gate admission" in post_gate_slice
    assert "during post-gate admission" in post_gate_slice
    assert "production unchanged" in post_gate_slice
    after_deploy = poller[deploy_status:]
    assert "DEPLOY_STATUS=0" in after_deploy
    assert 'case "$DEPLOY_STATUS"' in after_deploy
    assert "shared host became busy" in after_deploy


def _post_gate_admission(
    tmp_path: Path,
    wrapper_body: str,
    *,
    wait_seconds: int,
    sleep: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    wrapper = _executable(tmp_path / "admission-wrapper", wrapper_body)
    env = os.environ | {
        "SEICHE_CONTROL_LIBRARY_ONLY": "1",
        "SEICHE_CONTROL_DEPLOY_WRAPPER": str(wrapper),
        "SEICHE_CONTROL_ADMISSION_WAIT_SECONDS": str(wait_seconds),
        "SEICHE_CONTROL_ADMISSION_RETRY_SECONDS": "1",
    }
    if sleep is not None:
        env["SEICHE_CONTROL_SLEEP"] = str(sleep)
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; wait_for_post_gate_admission',
            "seiche-admission-test",
            str(RELEASE_POLLER),
        ],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


def test_post_gate_admission_retries_a_safe_deferral(tmp_path):
    counter = tmp_path / "counter"
    counter.write_text("0\n")
    true = Path(shutil.which("true") or "/usr/bin/true")
    result = _post_gate_admission(
        tmp_path,
        (
            f'count=$(cat "{counter}")\n'
            "count=$((count + 1))\n"
            f'printf "%s\\n" "$count" >"{counter}"\n'
            '[ "$count" -gt 1 ] || exit 75\n'
        ),
        wait_seconds=10,
        sleep=true,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert counter.read_text() == "2\n"
    assert "retrying admission" in result.stdout


@pytest.mark.parametrize("wrapper_status", [1, 42])
def test_post_gate_admission_preserves_real_probe_failures(tmp_path, wrapper_status):
    result = _post_gate_admission(
        tmp_path,
        f"exit {wrapper_status}\n",
        wait_seconds=0,
    )

    assert result.returncode == wrapper_status


def test_post_gate_admission_returns_deferred_at_its_bound(tmp_path):
    result = _post_gate_admission(
        tmp_path,
        "exit 75\n",
        wait_seconds=0,
    )

    assert result.returncode == 75


def test_release_signature_boundary_accepts_only_the_pinned_signed_identity(tmp_path):
    repository, env = _release_signature_fixture(tmp_path)
    target = _commit_release(repository, "signed release")

    result = _verify_release_signature(env, target)

    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_desk_content_is_inert_only_within_closed_paths(tmp_path):
    repository, env = _release_signature_fixture(tmp_path)
    _commit_release(repository, "signed base")
    target = _commit_automation_content(
        repository,
        {
            "frontend/public/dispatches/edition.md": "public dispatch\n",
            "frontend/public/articles/edition.md": "public article\n",
            "backend/seiche/dispatches/edition.desk.md": "continuation\n",
        },
    )

    result = _classify_automation_content(env, target)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("files", "message", "author"),
    (
        (
            {
                "frontend/public/dispatches/edition.md": "dispatch\n",
                "ops/Caddyfile": "mixed executable configuration\n",
            },
            "dispatch: mixed paths",
            "desk@seiche.info",
        ),
        (
            {"frontend/public/dispatches/edition.md": "dispatch\n"},
            "dispatch: wrong author",
            "intruder@example.invalid",
        ),
        (
            {"frontend/public/dispatches/edition.md": "dispatch\n"},
            "feat: misleading content commit",
            "desk@seiche.info",
        ),
    ),
)
def test_generated_content_never_grants_broader_release_authority(
    tmp_path, files, message, author
):
    repository, env = _release_signature_fixture(tmp_path)
    _commit_release(repository, "signed base")
    target = _commit_automation_content(
        repository,
        files,
        message=message,
        author=author,
    )

    result = _classify_automation_content(env, target)

    assert result.returncode != 0


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
    assert signer.startswith("beepboop2025@users.noreply.github.com ssh-ed25519 ")
    assert "validate_allowed_signers" in poller
    assert "stat.S_IMODE(info.st_mode) != int(mode, 8)" in poller
    assert "info.st_nlink != 1" in poller
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
    assert (
        "wrapper failure never writes"
        in (ROOT / "ops" / "deploy" / "RELEASE-POLLER.md").read_text()
    )


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
        "EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}\nexit 0\n",
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
    assert (
        "restoring the previous release-poller files and timer state" in result.stderr
    )
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
        "EXPECTED_TARGET=${SEICHE_EXPECTED_TARGET_SHA:-}\nexit 0\n",
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
    assert (
        "SEICHE_CONTROL_ALLOWED_SIGNERS=/etc/seiche-release.allowed-signers" in service
    )
    assert "SEICHE_CONTROL_SIGNING_PRINCIPAL=" in service
    assert "ReadOnlyPaths=/etc/seiche-release.allowed-signers" in service
    assert "ExecStart=/usr/local/sbin/seiche-release-poll" in service
    assert "ConditionPathExists" not in service
    assert "TimeoutStartSec=3h" in service
    assert "OnUnitInactiveSec=5min" in timer
    assert "WantedBy=timers.target" in timer


def test_release_poller_allows_only_the_reviewed_setgid_export_boundary():
    service = RELEASE_POLLER_SERVICE.read_text()
    market_installer = MARKET_INSTALLER.read_text()
    writable_paths = {
        path
        for line in service.splitlines()
        if line.startswith("ReadWritePaths=")
        for path in line.removeprefix("ReadWritePaths=").split()
    }
    capabilities = next(
        line.removeprefix("CapabilityBoundingSet=").split()
        for line in service.splitlines()
        if line.startswith("CapabilityBoundingSet=")
    )

    # The production failure this contract guards: systemd must not reject the
    # installer's reviewed 2750 export chmod before candidate health can run.
    assert 'chmod 2750 "$FUNDING_EXPORT_DIR"' in market_installer
    assert "RestrictSUIDSGID=false" in service
    assert "CAP_FSETID" in capabilities
    assert "/var/lib/seiche" in writable_paths

    # Allowing that one setgid collaboration directory does not reopen the
    # controller's host namespace or privilege-escalation surfaces.
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=read-only" in service
    assert "AmbientCapabilities=" in service
    assert capabilities == [
        "CAP_AUDIT_WRITE",
        "CAP_CHOWN",
        "CAP_DAC_OVERRIDE",
        "CAP_DAC_READ_SEARCH",
        "CAP_FOWNER",
        "CAP_FSETID",
        "CAP_KILL",
        "CAP_SETGID",
        "CAP_SETUID",
    ]
    assert "/etc/seiche" in writable_paths
    assert "/etc/systemd/system" in writable_paths
    assert "/etc/caddy" in writable_paths
    assert "/" not in writable_paths
    assert "/opt" not in writable_paths
    assert "/usr" not in writable_paths
    assert "/usr/local" not in writable_paths


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

    assert wrapper.index("market_health", wrapper.index('HEALTHY=""')) < wrapper.index(
        "promote_snapshot_handoff", wrapper.index('HEALTHY=""')
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
    assert "rollback_health_wait 480" in rollback
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
        caddy.index("@palimpsest_osint path") : caddy.index("# Palimpsest BLEEDTHROUGH")
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
        caddy.index("@palimpsest_bleedthrough path") : caddy.index("# Palimpsest MCP")
    ]
    assert 'header Access-Control-Allow-Origin "https://palimpsest.info"' in block
    assert 'header Cache-Control "no-store, no-transform"' in block
    assert 'header Content-Disposition "inline"' in block
    assert "uri strip_prefix /palimpsest/bleedthrough" in block
    assert "root * /var/lib/palimpsest/readings" in block
    assert "file_server" in block
    assert "reverse_proxy" not in block


def test_palimpsest_social_observations_edge_is_an_exact_static_allowlist():
    caddy = CADDYFILE.read_text()
    block = caddy[
        caddy.index("# ScamShield publishes one atomic") : caddy.index(
            "# Palimpsest MCP"
        )
    ]

    fallback = block.index("@palimpsest_social_other path")
    for name in ("latest.json", "versions.jsonl", "hmac.json"):
        route = f"path /palimpsest/social-observations/{name}"
        assert route in block
        assert block.index(route) < fallback
    assert block.count("method GET HEAD") == 3
    assert "handle_path /palimpsest/social-observations/*" not in block
    assert (
        "@palimpsest_social_other path /palimpsest/social-observations "
        "/palimpsest/social-observations/ "
        "/palimpsest/social-observations/*"
    ) in block
    assert 'respond "not here" 404' in block
    assert (
        block.count('header Access-Control-Allow-Origin "https://palimpsest.info"') == 3
    )
    assert block.count('header Cache-Control "no-store, no-transform"') == 3
    assert 'header Content-Type "application/x-ndjson"' in block
    assert block.count("uri strip_prefix /palimpsest/social-observations") == 3
    assert block.count("root * /var/lib/scamshield/social-export/current") == 3
    assert block.count("file_server") == 3
    assert "reverse_proxy" not in block


def test_adapted_social_routes_are_reachable_before_the_site_catch_all():
    caddy = shutil.which("caddy")
    assert caddy is not None, "Caddy is required to validate adapted route reachability"
    result = subprocess.run(  # noqa: S603 - fixed argv invokes the pinned adapter
        [caddy, "adapt", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
        check=True,
        text=True,
        capture_output=True,
    )
    document = json.loads(result.stdout)
    servers = document["apps"]["http"]["servers"]
    api_route = next(
        route
        for server in servers.values()
        for route in server["routes"]
        if any(
            "api.seiche.info" in matcher.get("host", [])
            for matcher in route.get("match", [])
        )
    )
    api_subroute = next(
        handler for handler in api_route["handle"] if handler["handler"] == "subroute"
    )
    routes = api_subroute["routes"]

    def paths(route: dict) -> set[str]:
        return {
            path
            for matcher in route.get("match", [])
            for path in matcher.get("path", [])
        }

    expected = {
        f"/palimpsest/social-observations/{name}"
        for name in ("latest.json", "versions.jsonl", "hmac.json")
    }
    exact_indexes = {
        path: next(index for index, route in enumerate(routes) if path in paths(route))
        for path in expected
    }
    deny_index = next(
        index
        for index, route in enumerate(routes)
        if "/palimpsest/social-observations/*" in paths(route)
    )
    group = routes[deny_index]["group"]
    catch_all_index = next(
        index
        for index, route in enumerate(routes)
        if index > deny_index and route.get("group") == group and not route.get("match")
    )

    assert max(exact_indexes.values()) < deny_index < catch_all_index
    for index in exact_indexes.values():
        assert routes[index]["group"] == group
        matcher = routes[index]["match"]
        assert any(set(item.get("method", [])) == {"GET", "HEAD"} for item in matcher)
