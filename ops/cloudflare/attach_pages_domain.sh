#!/usr/bin/env bash
# One-time: attach seiche.info to the 'seiche' Cloudflare Pages project, so
# the Pages deployment (and its _headers security headers) serves the real
# domain instead of just seiche.pages.dev. Idempotent: skips the POST when
# the domain is already attached. Reads CLOUDFLARE_API_TOKEN (Pages:Edit +
# Zone:Read) and CLOUDFLARE_ACCOUNT_ID from the environment.
set -euo pipefail

API="https://api.cloudflare.com/client/v4"
DOMAIN="seiche.info"
PROJECT="seiche"

: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN first — see ops/cloudflare/README.md}"
: "${CLOUDFLARE_ACCOUNT_ID:?set CLOUDFLARE_ACCOUNT_ID first — see ops/cloudflare/README.md}"

AUTH="Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"

# Preflight: the zone must exist in this account (also proves Zone:Read).
zone_id=$(curl -sf -H "$AUTH" "${API}/zones?name=${DOMAIN}" \
  | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(r[0]["id"] if r else "")')
if [ -z "$zone_id" ]; then
  echo "no zone for ${DOMAIN} in this account — add the domain to Cloudflare first" >&2
  exit 1
fi
echo "zone ${DOMAIN}: ${zone_id}"

if curl -sf -H "$AUTH" "${API}/accounts/${CLOUDFLARE_ACCOUNT_ID}/pages/projects/${PROJECT}/domains/${DOMAIN}" >/dev/null; then
  echo "domain ${DOMAIN} already attached to project '${PROJECT}' — nothing to do"
else
  echo "attaching ${DOMAIN} to project '${PROJECT}'…"
  curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
    "${API}/accounts/${CLOUDFLARE_ACCOUNT_ID}/pages/projects/${PROJECT}/domains" \
    -d "{\"name\":\"${DOMAIN}\"}" >/dev/null
  echo "attached."
fi

# Where validation stands right now.
curl -sf -H "$AUTH" "${API}/accounts/${CLOUDFLARE_ACCOUNT_ID}/pages/projects/${PROJECT}/domains/${DOMAIN}" \
  | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print("domain {name}: {status}".format(**r))'

cat <<EOF

Next steps:
  - Cloudflare validates the domain (fast: the zone is already on Cloudflare
    and proxied). Re-run this script to re-check the status.
  - Once active, https://${DOMAIN} serves from Pages with the security
    headers from frontend/public/_headers. Verify:
      curl -sI https://${DOMAIN}/ | grep -i strict-transport-security
  - The GitHub Pages mirror (seiche-site repo) stays up as fallback; it just
    never sends custom headers.
EOF
