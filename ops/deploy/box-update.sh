#!/bin/bash
# Safe auto-update: pull main, install production extras, test, restart.
# Roll back if the install OR the tests fail. Output is logged to
# /tmp/seiche-update.log and NEVER suppressed — a broken pip install must not
# pass silently (that is how the editable install rotted before).
set -u
cd /home/seiche/app || exit 1
LOG=/tmp/seiche-update.log
: > "$LOG"

PREV=$(git rev-parse HEAD)
# The sha whose code is actually RUNNING, recorded by the wrapper after a
# healthy restart. HEAD matching origin/main is NOT proof there is nothing to
# do: a deploy killed between pull and restart leaves the new tree on disk
# with the old process serving it, and the old early-exit here made that
# state permanent (2026-07-28) — workflow_dispatch re-ran, matched SHAs, and
# declared victory. Unknown (missing file) means deploy.
DEPLOYED=$(cat /home/seiche/.seiche-deployed-sha 2>/dev/null || true)
git fetch -q origin main
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
  if [ "$DEPLOYED" = "$(git rev-parse HEAD)" ]; then
    exit 0
  fi
  echo "HEAD already at $(git rev-parse --short HEAD) but the running service is ${DEPLOYED:-unknown}: re-running install+smoke so the wrapper can restart onto it"
else
  git reset -q --hard origin/main
fi

rollback() {
  echo "ROLLING BACK to ${PREV:0:7}: $1 (see $LOG)" >&2
  echo "=== deploy gate failure: last 200 log lines ===" >&2
  tail -n 200 "$LOG" >&2 || true
  git reset -q --hard "$PREV"
  timeout -k 30 600 backend/.venv/bin/pip install -q -e "./backend[notary]" >>"$LOG" 2>&1 || true
  exit 1
}

# Every gate below runs under an ON-BOX timeout. The only ceiling used to be
# the GH job's 30 minutes, and hitting it kills the SSH CLIENT: the remote
# run keeps going with nobody watching, which is exactly how seven orphaned
# pytest runs accumulated on 2026-07-28 and starved 14 of 16 cores. coreutils
# timeout runs the command in its own process group and signals the whole
# group, so a wedged pip or pytest dies HERE, the rollback path runs, and
# nothing is ever orphaned. Budgets sit well inside the GH ceiling so the GH
# timeout stays an outer backstop that never fires first.
echo "=== pip install $(date -u +%FT%TZ) ===" >>"$LOG"
if ! timeout -k 30 600 backend/.venv/bin/pip install -q -e \
        "./backend[notary,collectors,postgres]" >>"$LOG" 2>&1; then
  rollback "pip install failed or timed out"
fi

# SMOKE GATE, not the full suite.
#
# ROOT CAUSE, found 2026-07-29 after four deploys died at 25, 55, 120 and 330
# minutes. The suite was never slow: it passes here in 11m31s. It could not
# EXIT. The old gate passed --pystack-threshold=300, and pytest-pystack spawns
# the `pystack` binary by name. That binary lives in backend/.venv/bin, which
# is NOT on PATH here, because this script calls the venv's python directly
# instead of activating the venv. So the plugin's monitor process died with
# FileNotFoundError, its multiprocessing queue lost its reader, and the feeder
# thread blocked forever writing into a dead pipe. At interpreter shutdown
# pytest joined that thread and hung, permanently, AFTER reporting 731 passed.
# No timeout could ever have been long enough.
#
# Two guards, either of which is sufficient: this gate no longer passes the
# flag, and PATH below now carries the venv so a future re-add cannot resurrect
# it. CI does not hit this because pip install there puts pystack on PATH.
#
# Depth is not lost by running less here. publish.yml runs the same full suite
# on a clean runner for this same commit; re-running it on the box bought no
# signal about the code, it only decided whether to restart a service. What
# that decision needs is: does this tree import, does the API construct, do the
# letter and the public surfaces still render. That is what runs below, and
# the wrapper's health check plus this script's rollback catch the rest.
#
# If a deploy ever needs the full suite here, run it by hand; do not put it
# back in the restart path.
export PATH="/home/seiche/app/backend/.venv/bin:$PATH"
# Nine files, collects 232 tests as of this commit. If a commit grows or
# shrinks this subset, update this count in the same commit — the number is
# how a reader of the deploy log knows the gate ran what it claims to run.
SMOKE="tests/test_dispatch_daily.py tests/test_dispatch_pages.py \
tests/test_citability.py tests/test_mcp_server.py tests/test_notary.py \
tests/test_attest.py tests/test_api_v2_markets.py \
tests/test_market_materialize.py tests/test_deploy_release.py"

echo "=== import smoke $(date -u +%FT%TZ) ===" >>"$LOG"
if ! timeout -k 30 120 backend/.venv/bin/python -c \
        "import seiche.api, seiche.assemble, seiche.dispatch_daily, seiche.market_runtime, seiche.sources.official" \
        >>"$LOG" 2>&1; then
  rollback "the tree does not import (or the import wedged)"
fi

echo "=== pytest smoke $(date -u +%FT%TZ) ===" >>"$LOG"
if (cd backend && timeout -k 30 600 ../backend/.venv/bin/python -m pytest $SMOKE -q -x) >>"$LOG" 2>&1; then
  echo "updated to $(git rev-parse --short HEAD) — install ok, smoke green"
  exit 0
else
  rollback "smoke tests failed or timed out"
fi
