#!/usr/bin/env bash
# Retired fail-closed entrypoint. The former installer provisioned an /opt
# layout and service names that never matched the canonical production host.
set -euo pipefail

cat >&2 <<'EOF'
FATAL: ops/deploy/install.sh is retired and intentionally performs no changes.
Seiche has no unattended first-VPS bootstrap. Existing production uses
/home/seiche/app and the signed controller documented in:
  ops/deploy/RELEASE-POLLER.md
EOF
exit 1
