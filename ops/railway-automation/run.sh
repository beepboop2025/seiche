#!/usr/bin/env bash
set -euo pipefail
cd /app
test -z "${GITHUB_TOKEN:-}${GH_TOKEN:-}${RAILWAY_TOKEN:-}${RAILWAY_API_TOKEN:-}${CLOUDFLARE_API_TOKEN:-}"
[[ "${RAILWAY_GIT_COMMIT_SHA:-}" =~ ^[0-9a-f]{40}$ ]]
case "${CHECK_KIND:?Set CHECK_KIND to the reviewed check name}" in
  security-headers)
    bash ops/security/check_headers.sh
    ;;
  distribution-receipts)
    python -I -S ops/release/audit_distribution_receipts.py --timeout-seconds 15
    ;;
  discovery-coverage)
    status=0
    python backend/scripts/ard_coverage.py --json-out /tmp/ard-coverage.json || status=$?
    if [[ -f /tmp/ard-coverage.json ]]; then cat /tmp/ard-coverage.json; fi
    test "$status" -eq 0
    ;;
  *) echo "Unsupported check: $CHECK_KIND" >&2; exit 2 ;;
esac
printf 'RAILWAY_PUBLIC_CHECK_PASS check=%s source=%s deployment=%s\n' \
  "$CHECK_KIND" "$RAILWAY_GIT_COMMIT_SHA" "${RAILWAY_DEPLOYMENT_ID:-unavailable}"
