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

mcp_post() {
    label="$1"
    request_body="$2"
    meta=$(curl --silent --show-error --max-time 20 --max-redirs 0 \
        --request POST --header 'Content-Type: application/json' \
        --header 'Accept: application/json' --data-binary "$request_body" \
        --output "$BODY_FILE" --write-out '%{http_code}|%{content_type}' \
        "${BASE_URL%/}/mcp")
    IFS='|' read -r actual actual_type meta_extra <<< "$meta"
    if [ -n "${meta_extra:-}" ] || [ "$actual" != 200 ]; then
        echo "FAIL: MCP $label returned malformed metadata or status '$meta'." >&2
        exit 1
    fi
    case "${actual_type,,}" in
        application/json*) ;;
        *)
            echo "FAIL: MCP $label returned content type '$actual_type'." >&2
            exit 1
            ;;
    esac
}

mcp_post tools/list \
    '{"jsonrpc":"2.0","id":"trade-safety-list","method":"tools/list"}'
if ! grep -Fq -- '"name":"trade_safety_risk_context"' "$BODY_FILE"; then
    echo "FAIL: MCP tools/list omitted trade_safety_risk_context." >&2
    exit 1
fi
echo "external smoke: POST /mcp tools/list -> trade_safety_risk_context"

mcp_post tools/call \
    '{"jsonrpc":"2.0","id":"trade-safety-call","method":"tools/call","params":{"name":"trade_safety_risk_context","arguments":{}}}'
for identity in \
        '"schema":"seiche.risk-context.v1"' \
        '"status":"available"' \
        '"real_money_eligible":false' \
        '"can_authorize_order":false' \
        '"request_time_network":false' \
        '"attestation_state":"not_evaluated"'; do
    if ! grep -Fq -- "$identity" "$BODY_FILE"; then
        echo "FAIL: MCP tools/call omitted Trade Safety identity '$identity'." >&2
        exit 1
    fi
done
echo "external smoke: POST /mcp tools/call -> fail-closed Trade Safety context"
