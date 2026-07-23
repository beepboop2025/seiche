#!/usr/bin/env bash
# Cache Rules for the seiche.info SITE hosts — idempotent, safe to re-run.
#
# Why: on 2026-07-23 the zone's cache hit rate was 15.58%. Cloudflare only
# caches static extensions by default, so the HTML shell ("/" — 42% of all
# requests) and the live JSON board were both marked "Dynamic" and went to
# origin every single time: 631 of 982 requests in 24h.
#
# seiche.info is GitHub Pages behind the Cloudflare proxy, and GitHub Pages
# cannot send custom cache headers (see frontend/public/_headers), so the
# only place to fix this is at the edge.
#
# SAFETY — read before editing the expressions:
# The seiche.info ZONE also contains api.seiche.info, groundcheck.seiche.info
# and breach.seiche.info. groundcheck serves PAID x402 responses and the API
# serves live per-subscriber data. A zone-wide cache rule would cache both.
# Every rule below is therefore pinned to the two static site hosts, and the
# API hosts must never be added.
#
# TTLs: the publish workflow refreshes site data every 4h (cron 23 2,6,...),
# so a 5-minute edge TTL on the board can never serve a stale regime, while
# /assets/* filenames are content-hashed by vite and are immutable.
#
# Usage: CLOUDFLARE_API_TOKEN=... ops/cloudflare/cache_rules.sh
# Token needs Zone:Read AND Zone:Cache Rules:Edit on the seiche.info zone.
set -euo pipefail

: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN — needs Zone:Cache Rules:Edit}"
ZONE_NAME="${ZONE_NAME:-seiche.info}"
API="https://api.cloudflare.com/client/v4"
AUTH="Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
SITE_HOSTS='http.host in {"seiche.info" "www.seiche.info"}'

say() { printf '%s\n' "$*" >&2; }

# --- zone id -----------------------------------------------------------------
zone_json="$(curl -sS -H "$AUTH" "${API}/zones?name=${ZONE_NAME}")"
if [ "$(printf '%s' "$zone_json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["success"])')" != "True" ]; then
  say "FAILED to read zone. Token is missing Zone:Read, or is invalid:"
  printf '%s\n' "$zone_json" | python3 -m json.tool >&2 || true
  exit 1
fi
ZONE_ID="$(printf '%s' "$zone_json" | python3 -c 'import json,sys;z=json.load(sys.stdin)["result"];print(z[0]["id"] if z else "")')"
[ -n "$ZONE_ID" ] || { say "zone ${ZONE_NAME} not found on this account"; exit 1; }
say "zone ${ZONE_NAME} -> ${ZONE_ID}"

# --- build the ruleset -------------------------------------------------------
# Rule order is explicit and non-overlapping: the assets rule matches only
# /assets/*, the site rule matches everything else. No merge ambiguity.
rules_payload="$(python3 - "$SITE_HOSTS" <<'PY'
import json, sys
hosts = sys.argv[1]
rules = [
    {
        "description": "seiche site: immutable hashed assets (1y)",
        "expression": f'({hosts} and starts_with(http.request.uri.path, "/assets/"))',
        "action": "set_cache_settings",
        "action_parameters": {
            "cache": True,
            "edge_ttl": {"mode": "override_origin", "default": 31536000},
            "browser_ttl": {"mode": "override_origin", "default": 31536000},
        },
    },
    {
        "description": "seiche site: HTML shell + board JSON (5m edge)",
        "expression": f'({hosts} and not starts_with(http.request.uri.path, "/assets/"))',
        "action": "set_cache_settings",
        "action_parameters": {
            "cache": True,
            "edge_ttl": {"mode": "override_origin", "default": 300},
            "browser_ttl": {"mode": "override_origin", "default": 60},
        },
    },
]
print(json.dumps({"rules": rules,
                  "description": "seiche.info static-site caching (ops/cloudflare/cache_rules.sh)"}))
PY
)"

# --- upsert into the cache-settings phase ------------------------------------
PHASE="http_request_cache_settings"
resp="$(curl -sS -X PUT -H "$AUTH" -H "Content-Type: application/json" \
  --data "$rules_payload" \
  "${API}/zones/${ZONE_ID}/rulesets/phases/${PHASE}/entrypoint")"

ok="$(printf '%s' "$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("success"))')"
if [ "$ok" != "True" ]; then
  say "FAILED to write cache rules:"
  printf '%s\n' "$resp" | python3 -m json.tool >&2 || true
  say ""
  say "If this says the token lacks permission, add 'Zone -> Cache Rules -> Edit'"
  say "for the ${ZONE_NAME} zone to the CLOUDFLARE_API_TOKEN and re-run."
  exit 1
fi

printf '%s' "$resp" | python3 -c '
import json, sys
r = json.load(sys.stdin)["result"]
print(f"OK — ruleset {r[\"id\"]} v{r.get(\"version\")}")
for rule in r.get("rules", []):
    p = rule.get("action_parameters", {})
    print(f"  - {rule[\"description\"]}: edge {p.get(\"edge_ttl\",{}).get(\"default\")}s")
'
say ""
say "Verify from a client:  curl -sI https://seiche.info/ | grep -i cf-cache-status"
say "(first hit MISS, second hit HIT)"
