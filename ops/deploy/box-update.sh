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

# SMOKE GATE, not the full suite. Measured 2026-07-28: the full suite is 731
# tests and takes ~8 minutes on a laptop, but over TWO HOURS here, and three
# deploys were killed by their job timeout mid-run. The cause is not the
# profiler (memray measured at ~4% overhead, inside noise); it is CPU
# contention. This box also serves the API, whose board rebuild is itself
# heavy, plus the Undertow relay and the desk bots, on four SHARED vCPUs.
#
# Depth is not lost. publish.yml runs the same full suite, with memray and
# pystack, on a clean GitHub runner for this same commit. Re-running it here
# buys no additional signal about the code; it only decides whether to restart
# a service. What that decision actually needs is: does this tree import, does
# the API construct, do the letter and the public surfaces still render. That
# is what runs below, in about a minute, and the wrapper's health check plus
# this script's rollback still catch anything that gets past it.
#
# If a deploy ever needs the full suite here, run it by hand; do not put it
# back in the restart path.
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
