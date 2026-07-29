#!/bin/bash
# Safe auto-update: pull main, install (with the notary extra), test, restart.
# Roll back if the install OR the tests fail. Output is logged to
# /tmp/seiche-update.log and NEVER suppressed — a broken pip install must not
# pass silently (that is how the editable install rotted before).
set -u
cd /home/seiche/app || exit 1
LOG=/tmp/seiche-update.log
: > "$LOG"

PREV=$(git rev-parse HEAD)
git fetch -q origin main
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
  exit 0
fi
git reset -q --hard origin/main

rollback() {
  echo "ROLLING BACK to ${PREV:0:7}: $1 (see $LOG)" >&2
  git reset -q --hard "$PREV"
  backend/.venv/bin/pip install -q -e "./backend[notary]" >>"$LOG" 2>&1 || true
  exit 1
}

echo "=== pip install $(date -u +%FT%TZ) ===" >>"$LOG"
if ! backend/.venv/bin/pip install -q -e "./backend[notary]" >>"$LOG" 2>&1; then
  rollback "pip install failed"
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
SMOKE="tests/test_dispatch_daily.py tests/test_dispatch_pages.py \
tests/test_citability.py tests/test_mcp_server.py tests/test_notary.py \
tests/test_attest.py"

echo "=== import smoke $(date -u +%FT%TZ) ===" >>"$LOG"
if ! backend/.venv/bin/python -c "import seiche.api, seiche.assemble, seiche.dispatch_daily" >>"$LOG" 2>&1; then
  rollback "the tree does not import"
fi

echo "=== pytest smoke $(date -u +%FT%TZ) ===" >>"$LOG"
if (cd backend && ../backend/.venv/bin/python -m pytest $SMOKE -q -x) >>"$LOG" 2>&1; then
  echo "updated to $(git rev-parse --short HEAD) — install ok, smoke green"
  exit 0
else
  rollback "smoke tests failed"
fi
