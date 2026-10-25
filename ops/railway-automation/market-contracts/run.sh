#!/usr/bin/env bash
set -euo pipefail
cd /app
export HOME=/root
export PATH="/usr/lib/postgresql/17/bin:$PATH"
export SEICHE_TEST_POSTGRES_URL=postgresql://seiche:seiche-test-only@127.0.0.1:5432/seiche_test
install -d -m 700 -o postgres -g postgres /tmp/postgres
runuser -u postgres -- initdb -D /tmp/postgres/data -U seiche --auth=trust
cleanup() { runuser -u postgres -- pg_ctl -D /tmp/postgres/data -m immediate -w stop >/dev/null 2>&1 || true; }
trap cleanup EXIT
if ! runuser -u postgres -- pg_ctl -D /tmp/postgres/data -l /tmp/postgres/server.log -o '-h 127.0.0.1 -k /tmp/postgres -p 5432 -F' -w start; then
  cat /tmp/postgres/server.log >&2
  exit 1
fi
createdb -h 127.0.0.1 -U seiche seiche_test
chown -R runner:runner /app
python - <<'PYTHON'
from pathlib import Path
import subprocess, yaml
workflow=yaml.safe_load(Path('.github/workflows/market-platform-ci.yml').read_text())
steps={step.get('name'): step for step in workflow['jobs']['contracts']['steps']}
for name, timeout_seconds in (
    ('Run market architecture and PostgreSQL contracts', 1800),
    ('Run Railway release, snapshot, stateful, and Telegram contracts', 1200),
    ('Run Telegram bot contracts', 1200),
    ('Prove the Linux control API and root journal permission boundary', 1200),
):
    # The first stage contains over 1,800 tests and can exceed 20 minutes on
    # a cold builder. Keep all workflow commands intact and the run bounded.
    print(f'RAILWAY_MARKET_CONTRACTS_STEP {name} timeout_seconds={timeout_seconds}',flush=True)
    command = ['bash','-euo','pipefail','-c',steps[name]['run']]
    # This test starts as root, then drops to the real runtime UID itself.
    # Dropping first would require sudo to regain privileges, which BuildKit's
    # no-new-privileges policy correctly forbids. Keep the other suites unprivileged.
    if name != 'Prove the Linux control API and root journal permission boundary':
        command = ['sudo','-u','runner','-H',
            '--preserve-env=SEICHE_TEST_POSTGRES_URL,PYTHONPATH,OMP_NUM_THREADS,MKL_NUM_THREADS,OPENBLAS_NUM_THREADS,NUMEXPR_NUM_THREADS',
            *command]
    subprocess.run(command,check=True,timeout=timeout_seconds)
PYTHON
printf 'RAILWAY_MARKET_CONTRACTS_BUILD_PASS source=%s\n' "${RAILWAY_GIT_COMMIT_SHA:-unavailable}"
