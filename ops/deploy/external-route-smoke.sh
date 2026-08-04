#!/usr/bin/env bash
# Verify that routes are reachable through public DNS + Caddy, not merely from
# the localhost FastAPI health check. Override BASE_URL/ROUTES_FILE in tests.
set -euo pipefail

BASE_URL="${SEICHE_EXTERNAL_BASE_URL:-https://api.seiche.info}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROUTES_FILE="${SEICHE_EXTERNAL_ROUTES_FILE:-$SCRIPT_DIR/external-smoke-routes.txt}"

while read -r method path expected extra; do
    case "$method" in
        ""|'#'*) continue ;;
    esac
    if [ -n "${extra:-}" ] || [ "$method" != "GET" ]; then
        echo "FAIL: unsafe or malformed external smoke definition: $method $path $expected ${extra:-}" >&2
        exit 1
    fi
    actual=$(curl --silent --show-error --location --max-time 20 \
        --request "$method" --output /dev/null --write-out '%{http_code}' \
        "${BASE_URL%/}$path")
    if [ "$actual" != "$expected" ]; then
        echo "FAIL: $method $path returned $actual through $BASE_URL (expected $expected)." >&2
        exit 1
    fi
    echo "external smoke: $method $path -> $actual"
done < "$ROUTES_FILE"
