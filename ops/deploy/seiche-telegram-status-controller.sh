#!/usr/bin/env bash
# Status-only forced command for recurring Phase-7 monitoring.

set -euo pipefail
set -f
umask 0077

fail() {
  printf 'seiche Telegram status controller: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "root identity is required"
[ "$(id -g)" -eq 0 ] || fail "root group is required"
read -r action request_id extra <<<"${SSH_ORIGINAL_COMMAND:-}"
[ "$action" = status ] || fail "only status is allowed"
[[ "$request_id" =~ ^[0-9a-f]{64}$ ]] || fail "request id is invalid"
[ -z "${extra:-}" ] || fail "status command has too many arguments"

unset SSH_ORIGINAL_COMMAND
exec /etc/seiche/libexec/seiche-telegram-migration-controller.sh \
  status "$request_id"
