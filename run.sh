#!/usr/bin/env bash
# Seiche dev runner: backend :8787 + frontend :5173
set -e
cd "$(dirname "$0")"
(cd backend && .venv/bin/uvicorn seiche.api:app --port 8787) &
BACK=$!
trap "kill $BACK 2>/dev/null" EXIT
cd frontend && npm run dev
