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
for name in (
    'Run market architecture and PostgreSQL contracts',
    'Run Railway release, snapshot, stateful, and Telegram contracts',
    'Run Telegram bot contracts',
    'Prove the Linux control API and root journal permission boundary',
):
    print('RAILWAY_MARKET_CONTRACTS_STEP '+name,flush=True)
    subprocess.run(['sudo','-u','runner','-H',
        '--preserve-env=SEICHE_TEST_POSTGRES_URL,PYTHONPATH,OMP_NUM_THREADS,MKL_NUM_THREADS,OPENBLAS_NUM_THREADS,NUMEXPR_NUM_THREADS',
        'bash','-euo','pipefail','-c',steps[name]['run']],check=True,timeout=1200)
PYTHON
printf 'RAILWAY_MARKET_CONTRACTS_BUILD_PASS source=%s\n' "${RAILWAY_GIT_COMMIT_SHA:-unavailable}"
