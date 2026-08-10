#!/usr/bin/env bash
# Opt in the Seiche API to relaying one opaque, signed Lab delivery envelope.
set -euo pipefail
umask 0077

ENV_DIR="${SEICHE_ENV_DIR:-/etc/seiche}"
ENV_FILE="${SEICHE_WORLD_MODEL_DELIVERY_ENV_FILE:-$ENV_DIR/world-model-delivery.env}"
TOKEN_FILE="${SEICHE_WORLD_MODEL_DELIVERY_TOKEN_FILE:-${1:-}}"
DELIVERY_PATH=/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json
DELIVERY_READER_GROUP=liquilens-world-model-readers
API_USER=seiche
MAX_BYTES="${SEICHE_WORLD_MODEL_DELIVERY_MAX_BYTES:-2097152}"
HARD_MAX_BYTES=5242880

fail() {
    echo "world-model delivery relay: $*" >&2
    exit 1
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "must run as root"
case "$ENV_DIR" in
    /*) ;;
    *) fail "environment directory must be absolute" ;;
esac
[ "$ENV_DIR" != "/" ] || fail "refusing a filesystem-root environment directory"
[ "$ENV_FILE" = "$ENV_DIR/world-model-delivery.env" ] \
    || fail "environment file must use the dedicated Seiche credential path"
[ ! -L "$ENV_DIR" ] || fail "environment directory cannot be a symlink"
if [ -e "$ENV_FILE" ] || [ -L "$ENV_FILE" ]; then
    [ -f "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ] \
        || fail "existing environment target is not a regular file"
fi
case "$TOKEN_FILE" in
    /*) ;;
    *) fail "provide an absolute root-owned bearer-token file path" ;;
esac
[ -f "$TOKEN_FILE" ] && [ ! -L "$TOKEN_FILE" ] \
    || fail "bearer-token source must be a regular non-symlink file"
[ "$(stat -c '%u:%a' "$TOKEN_FILE")" = "0:400" ] \
    || [ "$(stat -c '%u:%a' "$TOKEN_FILE")" = "0:600" ] \
    || fail "bearer-token source must be root-owned mode 0400 or 0600"

TOKEN=$(<"$TOKEN_FILE")
[[ "$TOKEN" =~ ^[0-9a-f]{64}$ ]] \
    || fail "bearer token must be exactly 32 random bytes encoded as lowercase hex"
case "$MAX_BYTES" in
    ''|*[!0-9]*) fail "maximum bytes must be an integer" ;;
esac
[ "$MAX_BYTES" -ge 1 ] && [ "$MAX_BYTES" -le "$HARD_MAX_BYTES" ] \
    || fail "maximum bytes must be between 1 and $HARD_MAX_BYTES"

id -u "$API_USER" >/dev/null 2>&1 || fail "Seiche API identity is not provisioned"
getent group "$DELIVERY_READER_GROUP" >/dev/null \
    || fail "Lab delivery reader group is not provisioned"
if ! id -nG "$API_USER" | tr ' ' '\n' | grep -Fxq "$DELIVERY_READER_GROUP"; then
    usermod -a -G "$DELIVERY_READER_GROUP" "$API_USER"
fi
[ -f "$DELIVERY_PATH" ] && [ ! -L "$DELIVERY_PATH" ] \
    || fail "exact signed Lab export is missing or unsafe"
[ "$(stat -c '%U:%G:%a' /var/lib/liquilens-world-model)" \
    = "liquilens-world-model:$DELIVERY_READER_GROUP:710" ] \
    || fail "Lab delivery root does not enforce the reader-only boundary"
[ "$(stat -c '%U:%G:%a' /var/lib/liquilens-world-model/export)" \
    = "liquilens-world-model:$DELIVERY_READER_GROUP:2750" ] \
    || fail "Lab export directory does not enforce the reader-only boundary"
[ "$(stat -c '%U:%G:%a' "$DELIVERY_PATH")" \
    = "liquilens-world-model:$DELIVERY_READER_GROUP:440" ] \
    || fail "signed Lab export ownership/mode is unsafe"
runuser -u "$API_USER" -- test -r "$DELIVERY_PATH" \
    || fail "Seiche API identity cannot read the exact signed Lab export"

install -d -o root -g "$API_USER" -m 0750 "$ENV_DIR"
STAGE=$(mktemp "$ENV_DIR/.world-model-delivery.env.XXXXXX")
cleanup() {
    [ -z "${STAGE:-}" ] || rm -f -- "$STAGE"
}
trap cleanup EXIT
printf '%s\n' \
    "SEICHE_WORLD_MODEL_DELIVERY_PATH=$DELIVERY_PATH" \
    "SEICHE_WORLD_MODEL_DELIVERY_BEARER_TOKEN=$TOKEN" \
    "SEICHE_WORLD_MODEL_DELIVERY_MAX_BYTES=$MAX_BYTES" >"$STAGE"
chown root:"$API_USER" "$STAGE"
chmod 0640 "$STAGE"
mv -f -- "$STAGE" "$ENV_FILE"
STAGE=""

echo "world-model delivery relay: provisioned exact opaque export; restart seiche-api to activate"
