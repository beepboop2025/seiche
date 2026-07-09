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

echo "=== pytest $(date -u +%FT%TZ) ===" >>"$LOG"
if backend/.venv/bin/python -m pytest backend/tests -q >>"$LOG" 2>&1; then
  echo "updated to $(git rev-parse --short HEAD) — install ok, tests green"
  exit 0
else
  rollback "tests failed"
fi
