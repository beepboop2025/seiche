#!/usr/bin/env bash
# Upload and restore-verify one SSE-C object in an external Object Lock bucket.
set -euo pipefail
umask 0077

fail() {
    echo "seiche S3 Object Lock client: $*" >&2
    exit 1
}

for name in \
    AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION \
    S3_ENDPOINT S3_BUCKET S3_SSE_C_KEY_B64; do
    [ -n "${!name:-}" ] || fail "$name is required"
done

case "$S3_ENDPOINT" in
    https://*) ;;
    *) fail "S3 endpoint must use HTTPS" ;;
esac
[ "${S3_ENDPOINT%/}" = "$S3_ENDPOINT" ] \
    || fail "S3 endpoint must not have a trailing slash"
printf '%s' "$S3_BUCKET" | grep -Eq '^[a-z0-9][a-z0-9.-]{1,62}$' \
    || fail "S3 bucket is invalid"
printf '%s' "$AWS_DEFAULT_REGION" | grep -Eq '^[a-z0-9][a-z0-9-]{0,31}$' \
    || fail "S3 region is invalid"
export AWS_REQUEST_CHECKSUM_CALCULATION=when_required
export AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
export AWS_EC2_METADATA_DISABLED=true

WORK_ROOT=$(mktemp -d "${RUNNER_TEMP:-/tmp}/seiche-s3-object-lock.XXXXXX")
RESTORE_TEMP=""
HASHER_PID=""
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    if [ -n "$HASHER_PID" ] && kill -0 "$HASHER_PID" 2>/dev/null; then
        kill "$HASHER_PID" 2>/dev/null || true
        wait "$HASHER_PID" 2>/dev/null || true
    fi
    case "$WORK_ROOT" in
        "${RUNNER_TEMP:-/tmp}"/seiche-s3-object-lock.*) rm -rf -- "$WORK_ROOT" ;;
    esac
    if [ -n "$RESTORE_TEMP" ] && [ -f "$RESTORE_TEMP" ] \
            && [ ! -L "$RESTORE_TEMP" ]; then
        rm -f -- "$RESTORE_TEMP"
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

KEY_PATH="$WORK_ROOT/sse-c.key"
[ "$(stat -c '%a:%u:%g' "$WORK_ROOT")" = \
    "700:$(id -u):$(id -g)" ] || fail "private work directory metadata is unsafe"
sse_key_b64=$S3_SSE_C_KEY_B64
unset S3_SSE_C_KEY_B64
printf '%s' "$sse_key_b64" | base64 --decode >"$KEY_PATH" \
    || fail "SSE-C key is not valid base64"
chmod 0600 "$KEY_PATH"
[ -f "$KEY_PATH" ] && [ ! -L "$KEY_PATH" ] \
    || fail "SSE-C key path is unsafe"
[ "$(stat -c '%a:%u:%g:%s' "$KEY_PATH")" = \
    "600:$(id -u):$(id -g):32" ] || fail "SSE-C key metadata is unsafe"
canonical_key=$(base64 -w0 <"$KEY_PATH")
[ "$canonical_key" = "$sse_key_b64" ] || fail "SSE-C key is not canonical base64"
key_md5=$(openssl dgst -md5 -binary "$KEY_PATH" | base64 -w0)
unset sse_key_b64 canonical_key
aws --version 2>&1 | grep -Eq '^aws-cli/2\.36\.35 '

SSE_ARGS=(
    --sse-customer-algorithm AES256
    --sse-customer-key "fileb://$KEY_PATH"
)
AWS_ARGS=(--endpoint-url "$S3_ENDPOINT" --no-cli-pager)

probe_bucket() {
    local output="$1" raw="$WORK_ROOT/object-lock.json" \
        versioning="$WORK_ROOT/versioning.json"
    [ -n "$output" ] || fail "bucket proof output is required"
    aws "${AWS_ARGS[@]}" s3api get-object-lock-configuration \
        --bucket "$S3_BUCKET" >"$raw"
    aws "${AWS_ARGS[@]}" s3api get-bucket-versioning \
        --bucket "$S3_BUCKET" >"$versioning"
    INPUT="$raw" VERSIONING="$versioning" OUTPUT="$output" python3 -I -S - <<'PY'
import json
import os
from pathlib import Path

value = json.loads(Path(os.environ["INPUT"]).read_text())
value = value.get("ObjectLockConfiguration", value)
versioning = json.loads(Path(os.environ["VERSIONING"]).read_text())
rule = value.get("Rule", {}).get("DefaultRetention", {})
days = rule.get("Days")
if (
    value.get("ObjectLockEnabled") != "Enabled"
    or rule.get("Mode") != "COMPLIANCE"
    or not isinstance(days, int)
    or isinstance(days, bool)
    or days < 30
    or versioning.get("Status") != "Enabled"
):
    raise SystemExit("bucket default COMPLIANCE retention is not at least 30 days")
Path(os.environ["OUTPUT"]).write_text(
    json.dumps(
        {
            "object_lock_enabled": True,
            "versioning_enabled": True,
            "default_mode": "COMPLIANCE",
            "default_days": days,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n"
)
PY
}

head_object() {
    local key="$1" output="$2" version_id="${3:-}"
    local version_args=()
    if [ -n "$version_id" ]; then
        version_args=("--version-id=$version_id")
    fi
    aws "${AWS_ARGS[@]}" s3api head-object --bucket "$S3_BUCKET" --key "$key" \
        "${version_args[@]}" "${SSE_ARGS[@]}" >"$output" 2>/dev/null
}

validate_head() {
    local raw="$1" output="$2" expected_sha="$3" expected_size="$4" \
        downloaded_sha="$5" expected_version_id="$6"
    RAW="$raw" OUTPUT="$output" EXPECTED_SHA="$expected_sha" \
        EXPECTED_SIZE="$expected_size" KEY_MD5="$key_md5" \
        DOWNLOADED_SHA="$downloaded_sha" \
        EXPECTED_VERSION_ID="$expected_version_id" python3 -I -S - <<'PY'
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

head = json.loads(Path(os.environ["RAW"]).read_text())
retained = datetime.fromisoformat(
    str(head.get("ObjectLockRetainUntilDate", "")).replace("Z", "+00:00")
).astimezone(UTC)
expected_sha = os.environ["EXPECTED_SHA"]
if (
    head.get("ContentLength") != int(os.environ["EXPECTED_SIZE"])
    or head.get("Metadata", {}).get("sha256") != expected_sha
    or os.environ["DOWNLOADED_SHA"] != expected_sha
    or head.get("ObjectLockMode") != "COMPLIANCE"
    or retained < datetime.now(UTC) + timedelta(days=29)
    or head.get("SSECustomerAlgorithm") != "AES256"
    or head.get("SSECustomerKeyMD5") != os.environ["KEY_MD5"]
    or head.get("VersionId") != os.environ["EXPECTED_VERSION_ID"]
    or head["VersionId"] == "null"
):
    raise SystemExit("immutable SSE-C object proof failed")
head.pop("SSECustomerKeyMD5", None)
head["SSECustomerKeyVerified"] = True
head["DownloadedSHA256"] = os.environ["DOWNLOADED_SHA"]
Path(os.environ["OUTPUT"]).write_text(
    json.dumps(head, sort_keys=True, separators=(",", ":")) + "\n"
)
PY
}

put_verify() {
    local source="$1" key="$2" output="$3" source_sha size content_md5 \
        head_json="$WORK_ROOT/object.head.json" response="$WORK_ROOT/put.json" \
        fifo="$WORK_ROOT/download.fifo" download_hash="$WORK_ROOT/download.sha256" \
        downloaded_sha version_id
    [ -f "$source" ] && [ ! -L "$source" ] || fail "source is not a regular file"
    printf '%s' "$key" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]{0,900}$' \
        || fail "object key is invalid"
    case "/$key/" in */../*) fail "object key contains a parent segment" ;; esac
    source_sha=$(sha256sum "$source" | awk '{print $1}')
    size=$(stat -c %s "$source")
    [ "$size" -le 5368709120 ] || fail "object exceeds the single-PUT limit"
    content_md5=$(openssl dgst -md5 -binary "$source" | base64 -w0)

    if ! head_object "$key" "$head_json"; then
        aws "${AWS_ARGS[@]}" s3api put-object \
            --bucket "$S3_BUCKET" --key "$key" --body "$source" \
            --content-md5 "$content_md5" --metadata "sha256=$source_sha" \
            "${SSE_ARGS[@]}" >"$response"
        head_object "$key" "$head_json" \
            || fail "post-upload HEAD failed"
    fi

    version_id=$(HEAD="$head_json" python3 -I -S - <<'PY'
import json
import os
from pathlib import Path
value = json.loads(Path(os.environ["HEAD"]).read_text()).get("VersionId")
if not isinstance(value, str) or not value or value == "null":
    raise SystemExit("S3 version ID is missing")
print(value)
PY
    )
    head_object "$key" "$head_json" "$version_id" \
        || fail "version-pinned HEAD failed"
    mkfifo "$fifo"
    sha256sum <"$fifo" >"$download_hash" &
    HASHER_PID=$!
    if ! aws "${AWS_ARGS[@]}" s3api get-object \
            --bucket "$S3_BUCKET" --key "$key" "--version-id=$version_id" \
            "${SSE_ARGS[@]}" "$fifo" >/dev/null; then
        kill "$HASHER_PID" 2>/dev/null || true
        wait "$HASHER_PID" 2>/dev/null || true
        HASHER_PID=""
        rm -f -- "$fifo"
        fail "version-pinned download failed"
    fi
    wait "$HASHER_PID"
    HASHER_PID=""
    rm -f -- "$fifo"
    downloaded_sha=$(awk '{print $1}' "$download_hash")
    validate_head \
        "$head_json" "$output" "$source_sha" "$size" "$downloaded_sha" "$version_id"
}

get_verify() {
    local key="$1" version_id="$2" expected_sha="$3" destination="$4" \
        head_json="$WORK_ROOT/restore.head.json" destination_parent restored_sha
    printf '%s' "$key" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]{0,900}$' \
        || fail "object key is invalid"
    case "/$key/" in */../*) fail "object key contains a parent segment" ;; esac
    printf '%s' "$version_id" | grep -Eq '^[A-Za-z0-9._~+/=-]+$' \
        || fail "version ID is invalid"
    [ "$version_id" != null ] || fail "null version ID is invalid"
    printf '%s' "$expected_sha" | grep -Eq '^[0-9a-f]{64}$' \
        || fail "expected SHA-256 is invalid"
    [ ! -e "$destination" ] && [ ! -L "$destination" ] \
        || fail "restore destination already exists"
    destination_parent=$(dirname -- "$destination")
    [ -d "$destination_parent" ] && [ ! -L "$destination_parent" ] \
        || fail "restore destination parent is unsafe"
    [ "$(stat -c '%a:%u:%g' "$destination_parent")" = \
        "700:$(id -u):$(id -g)" ] \
        || fail "restore destination parent must be private and caller-owned"
    head_object "$key" "$head_json" "$version_id" \
        || fail "version-pinned restore HEAD failed"
    HEAD="$head_json" EXPECTED_SHA="$expected_sha" VERSION_ID="$version_id" \
        KEY_MD5="$key_md5" python3 -I -S - <<'PY'
import json
import os
from pathlib import Path

head = json.loads(Path(os.environ["HEAD"]).read_text())
if (
    head.get("Metadata", {}).get("sha256") != os.environ["EXPECTED_SHA"]
    or head.get("ObjectLockMode") != "COMPLIANCE"
    or head.get("SSECustomerAlgorithm") != "AES256"
    or head.get("SSECustomerKeyMD5") != os.environ["KEY_MD5"]
    or head.get("VersionId") != os.environ["VERSION_ID"]
    or not isinstance(head.get("ContentLength"), int)
    or isinstance(head.get("ContentLength"), bool)
    or head["ContentLength"] < 0
):
    raise SystemExit("version-pinned restore HEAD is invalid")
PY
    RESTORE_TEMP=$(mktemp "$destination_parent/.seiche-s3-restore.XXXXXX")
    chmod 0600 "$RESTORE_TEMP"
    aws "${AWS_ARGS[@]}" s3api get-object \
        --bucket "$S3_BUCKET" --key "$key" "--version-id=$version_id" \
        "${SSE_ARGS[@]}" "$RESTORE_TEMP" >/dev/null
    restored_sha=$(sha256sum "$RESTORE_TEMP" | awk '{print $1}')
    [ "$restored_sha" = "$expected_sha" ] \
        || fail "restored object SHA-256 does not match its locked receipt"
    python3 -I -S - "$RESTORE_TEMP" <<'PY'
import os
import sys

descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
    ln -- "$RESTORE_TEMP" "$destination" \
        || fail "restore destination appeared during verification"
    rm -f -- "$RESTORE_TEMP"
    RESTORE_TEMP=""
    python3 -I -S - "$destination_parent" <<'PY'
import os
import sys

descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

case "${1:-}" in
    probe-bucket)
        [ "$#" = 2 ] || fail "usage: $0 probe-bucket OUTPUT"
        probe_bucket "$2"
        ;;
    put-verify)
        [ "$#" = 4 ] || fail "usage: $0 put-verify SOURCE KEY OUTPUT"
        put_verify "$2" "$3" "$4"
        ;;
    get-verify)
        [ "$#" = 5 ] \
            || fail "usage: $0 get-verify KEY VERSION_ID SHA256 DESTINATION"
        get_verify "$2" "$3" "$4" "$5"
        ;;
    *) fail "operation must be probe-bucket, put-verify, or get-verify" ;;
esac
