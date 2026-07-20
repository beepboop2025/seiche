#!/usr/bin/env bash
# check_headers.sh — regression guard for the live security posture.
#
# Holds seiche.info / api.seiche.info to the LiquiLens house standard:
# HSTS (includeSubDomains + preload), CSP, X-Frame-Options DENY, nosniff,
# Referrer-Policy, Permissions-Policy — on the API's 404s too, which must
# also leak no stack trace or on-box paths — plus the http→https and
# www→apex redirects. Any MISSING or WRONG header fails the run.
#
# Six plain GET requests, one second apart — a health check, not a probe.
# Exits 0 on all-green, 1 otherwise. Run: ops/security/check_headers.sh

set -u

SITE="https://seiche.info/"
API="https://api.seiche.info"
CURL="curl -sS --connect-timeout 10 --max-time 20"

command -v curl >/dev/null 2>&1 || { echo "check_headers: curl is required" >&2; exit 2; }

TMP=$(mktemp -d "${TMPDIR:-/tmp}/seiche-headers.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

FAILS=0
TARGETS=(); CHECKS=(); RESULTS=(); DETAILS=()

record() {  # TARGET CHECK PASS|FAIL DETAIL
    TARGETS+=("$1"); CHECKS+=("$2"); RESULTS+=("$3"); DETAILS+=("$4")
    if [ "$3" = FAIL ]; then FAILS=$((FAILS + 1)); fi
}

hdr() {  # hdr TAG NAME -> first matching header value, trimmed
    grep -i "^$2:" "$TMP/h.$1" | head -n 1 | cut -d: -f2- | tr -d '\r' \
        | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

need_present() {  # TAG TARGET NAME
    local v; v=$(hdr "$1" "$3")
    if [ -n "$v" ]; then record "$2" "$3" PASS "$v"
    else record "$2" "$3" FAIL "MISSING"; fi
}

need_exact() {  # TAG TARGET NAME WANT (case-insensitive)
    local v; v=$(hdr "$1" "$3")
    if [ -z "$v" ]; then record "$2" "$3" FAIL "MISSING"; return; fi
    if [ "$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')" = \
         "$(printf '%s' "$4" | tr '[:upper:]' '[:lower:]')" ]; then
        record "$2" "$3" PASS "$v"
    else
        record "$2" "$3" FAIL "WRONG: got '$v', want '$4'"
    fi
}

need_contains() {  # TAG TARGET NAME NEEDLE... (case-insensitive, all must appear)
    local tag=$1 target=$2 name=$3; shift 3
    local v; v=$(hdr "$tag" "$name")
    if [ -z "$v" ]; then record "$target" "$name" FAIL "MISSING"; return; fi
    local n lacking=""
    for n in "$@"; do
        printf '%s' "$v" | grep -qiF -- "$n" || lacking="$lacking '$n'"
    done
    if [ -z "$lacking" ]; then record "$target" "$name" PASS "$v"
    else record "$target" "$name" FAIL "WRONG: lacking$lacking in '$v'"; fi
}

need_server_clean() {  # TAG TARGET — Server header absent or carries no version
    local v; v=$(hdr "$1" "Server")
    if [ -z "$v" ]; then record "$2" "Server" PASS "absent"
    elif printf '%s' "$v" | grep -q '[0-9]'; then
        record "$2" "Server" FAIL "WRONG: discloses version ('$v')"
    else record "$2" "Server" PASS "'$v' (no version)"; fi
}

need_redirect() {  # TAG URL TARGET ALLOWED-STATUSES LOCATION-REGEX
    local tag=$1 url=$2 target=$3 statuses=$4 re=$5
    local status loc
    status=$($CURL -D "$TMP/h.$tag" -o /dev/null -w '%{http_code}' "$url") || status=000
    loc=$(hdr "$tag" "Location")
    if printf '%s' " $statuses " | grep -q " $status "; then
        if printf '%s' "$loc" | grep -qE "$re"; then
            record "$target" "redirect ($status)" PASS "-> $loc"
        else
            record "$target" "redirect ($status)" FAIL "WRONG Location: '$loc'"
        fi
    else
        record "$target" "redirect" FAIL "WRONG status $status (want $statuses), Location '$loc'"
    fi
}

api_headers() {  # TAG TARGET — the API's standard header set
    need_contains "$1" "$2" "Strict-Transport-Security" "includeSubDomains" "preload"
    need_contains "$1" "$2" "Content-Security-Policy" "default-src 'none'"
    need_exact   "$1" "$2" "X-Frame-Options" "DENY"
    need_exact   "$1" "$2" "X-Content-Type-Options" "nosniff"
    need_present "$1" "$2" "Referrer-Policy"
    need_present "$1" "$2" "Permissions-Policy"
}

# (a) the site
status=$($CURL -D "$TMP/h.site" -o /dev/null -w '%{http_code}' "$SITE") || status=000
[ "$status" = 200 ] || record "seiche.info" "status" FAIL "WRONG status $status (want 200)"
need_contains "site" "seiche.info" "Strict-Transport-Security" "includeSubDomains" "preload"
need_contains "site" "seiche.info" "Content-Security-Policy" "frame-ancestors 'none'" "object-src 'none'"
need_exact   "site" "seiche.info" "X-Frame-Options" "DENY"
need_exact   "site" "seiche.info" "X-Content-Type-Options" "nosniff"
need_exact   "site" "seiche.info" "Referrer-Policy" "strict-origin-when-cross-origin"
need_present "site" "seiche.info" "Permissions-Policy"
sleep 1

# (b) the API's public window
status=$($CURL -D "$TMP/h.api" -o /dev/null -w '%{http_code}' "$API/api/overview") || status=000
[ "$status" = 200 ] || record "api.seiche.info" "status" FAIL "WRONG status $status (want 200)"
api_headers "api" "api.seiche.info"
need_server_clean "api" "api.seiche.info"
sleep 1

# (c) a 404 must still carry the headers and leak nothing
status=$($CURL -D "$TMP/h.404" -o "$TMP/b.404" -w '%{http_code}' "$API/definitely-not-here") || status=000
if [ "$status" = 404 ]; then record "api.seiche.info (404)" "status" PASS "404"
else record "api.seiche.info (404)" "status" FAIL "WRONG status $status (want 404)"; fi
api_headers "404" "api.seiche.info (404)"
leak=""
grep -qiF "Traceback" "$TMP/b.404" && leak="$leak Traceback"
grep -qF "/opt/" "$TMP/b.404" && leak="$leak /opt/"
grep -qiF "uvicorn" "$TMP/b.404" && leak="$leak uvicorn"
if [ -z "$leak" ]; then record "api.seiche.info (404)" "no stack/path leak" PASS "clean body"
else record "api.seiche.info (404)" "no stack/path leak" FAIL "WRONG: body leaks$leak"; fi
sleep 1

# (d) plain http upgrades to https, (e) www folds into the apex
need_redirect "h1" "http://seiche.info/"      "http://seiche.info"     "301 308"         '^https://seiche\.info/'
need_redirect "h2" "http://api.seiche.info/"  "http://api.seiche.info" "301 308"         '^https://api\.seiche\.info/'
need_redirect "h3" "https://www.seiche.info/" "www.seiche.info"        "301 302 307 308" '^https://seiche\.info/?$'

# report
echo
printf '%-24s %-26s %-6s %s\n' TARGET CHECK RESULT DETAIL
printf '%-24s %-26s %-6s %s\n' "------------------------" "--------------------------" "------" "------"
i=0
while [ $i -lt ${#TARGETS[@]} ]; do
    printf '%-24s %-26s %-6s %.90s\n' "${TARGETS[$i]}" "${CHECKS[$i]}" "${RESULTS[$i]}" "${DETAILS[$i]}"
    i=$((i + 1))
done
echo
total=${#TARGETS[@]}
echo "$total checks, $((total - FAILS)) passed, $FAILS failed"
if [ $FAILS -gt 0 ]; then
    echo "RESULT: FAIL"
    exit 1
fi
echo "RESULT: PASS"
