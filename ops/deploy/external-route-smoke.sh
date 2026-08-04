#!/usr/bin/env bash
# Verify that routes are reachable through public DNS + Caddy, not merely from
# the localhost FastAPI health check. Override BASE_URL/ROUTES_FILE in tests.
set -euo pipefail

BASE_URL="${SEICHE_EXTERNAL_BASE_URL:-https://api.seiche.info}"
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROUTES_FILE="${SEICHE_EXTERNAL_ROUTES_FILE:-$SCRIPT_DIR/external-smoke-routes.txt}"

BODY_FILE=$(mktemp)
cleanup() { rm -f -- "$BODY_FILE"; }
trap cleanup EXIT

while IFS='|' read -r method path expected expected_type body_identity extra; do
    case "$method" in
        ""|'#'*) continue ;;
    esac
    if [ -n "${extra:-}" ] || [ "$method" != "GET" ] \
            || [ -z "$path" ] || [ -z "$expected" ] \
            || [ -z "$expected_type" ] || [ -z "$body_identity" ]; then
        echo "FAIL: unsafe or malformed external smoke definition: $method|$path|$expected|$expected_type|$body_identity|${extra:-}" >&2
        exit 1
    fi
    meta=$(curl --silent --show-error --max-time 20 --max-redirs 0 \
        --request "$method" --output "$BODY_FILE" \
        --write-out '%{http_code}|%{content_type}' \
        "${BASE_URL%/}$path")
    IFS='|' read -r actual actual_type meta_extra <<< "$meta"
    if [ -n "${meta_extra:-}" ]; then
        echo "FAIL: malformed curl metadata for $method $path: $meta" >&2
        exit 1
    fi
    if [ "$actual" != "$expected" ]; then
        echo "FAIL: $method $path returned $actual through $BASE_URL (expected $expected)." >&2
        exit 1
    fi
    case "${actual_type,,}" in
        "${expected_type,,}"*) ;;
        *)
            echo "FAIL: $method $path returned content type '$actual_type' (expected $expected_type...)." >&2
            exit 1
            ;;
    esac
    if ! grep -Fq -- "$body_identity" "$BODY_FILE"; then
        echo "FAIL: $method $path returned $actual but not its route identity '$body_identity'." >&2
        exit 1
    fi
    echo "external smoke: $method $path -> $actual $actual_type [$body_identity]"
done < "$ROUTES_FILE"
