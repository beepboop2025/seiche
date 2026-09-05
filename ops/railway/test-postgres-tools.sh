#!/usr/bin/env bash
# Round-trip with the exact copied runtime tools, on an isolated Docker network.
set -euo pipefail
umask 077
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
scratch=$(mktemp -d)
suffix=$(basename "$scratch" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
network="seiche-pg-tools-$suffix"
server="seiche-pg-source-$suffix"
image="seiche-pg-tools:$suffix"
postgres_image=$(sed -n 's/^FROM \([^ ]*\) AS postgres-client$/\1/p' "$root/ops/railway/Dockerfile.stateful")
[[ "$postgres_image" =~ ^postgres:18\.6-bookworm@sha256:[a-f0-9]{64}$ ]]
cleanup() {
  docker rm --force --volumes "$server" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
  rm -rf -- "$scratch"
}
trap cleanup EXIT
mkdir "$scratch/context" "$scratch/proof"
docker build --target stateful-tools -t "$image" \
  -f "$root/ops/railway/Dockerfile.stateful" "$scratch/context"
docker run --rm --interactive --network none \
  --volume "$root:/source:ro" \
  "$image" python -I -B - <<'PY'
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, "/source/backend")
from seiche import stateful_application as application

with tempfile.TemporaryDirectory() as directory:
    key = Path(directory) / "ephemeral"
    subprocess.run([application.SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    application.OWNER_PUBLIC_KEY = key.with_suffix(".pub").read_text().strip()
    unsigned = {"schema": application.SIGNED_SCHEMA, "purpose": "activate", "payload": {"fixture": True}}
    signed = subprocess.run(
        [application.SSH_KEYGEN, "-Y", "sign", "-f", str(key), "-n", application.SIGNATURE_NAMESPACE],
        input=application.canonical(unsigned), capture_output=True, check=True,
    )
    envelope = {**unsigned, "signature": signed.stdout.decode()}
    assert application.validate_approval(envelope, "activate") == {"fixture": True}
    envelope["payload"] = {"fixture": False}
    try:
        application.validate_approval(envelope, "activate")
    except application.ApplicationContractError:
        pass
    else:
        raise AssertionError("runtime accepted a tampered signed approval")
print("Linux runtime SSH approval verification and tamper rejection passed")
spec = importlib.util.spec_from_file_location("application_builder", "/source/ops/railway/build_application_context.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    root.chmod(0o755)
    previous_mask = os.umask(0o077)
    try:
        builder.stage_parent(root / "parent", {"candidate": {"immutable": True}})
    finally:
        os.umask(previous_mask)
    def runtime_user():
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    subprocess.run([sys.executable, "-I", "-B", "-c",
        "import json,sys; from pathlib import Path; p=Path(sys.argv[1]); assert json.loads(p.read_bytes())=={'immutable':True}; "
        "assert not __import__('os').access(p,__import__('os').W_OK)",
        str(root / "parent/candidate.json")], preexec_fn=runtime_user, check=True)
    (root / "parent").chmod(0o700)
print("Linux runtime UID can read but cannot rewrite staged parent receipts")
PY
docker network create --internal "$network" >/dev/null
docker run --detach --name "$server" --network "$network" \
  --env POSTGRES_HOST_AUTH_METHOD=trust "$postgres_image" >/dev/null
ready=0
for _ in {1..30}; do
  if docker exec "$server" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
test "$ready" = 1
docker exec -i "$server" psql -X -q -U postgres -v ON_ERROR_STOP=1 <<'SQL'
CREATE DATABASE source_fixture;
CREATE DATABASE restored_fixture;
\connect source_fixture
CREATE TABLE evidence (id integer PRIMARY KEY, amount numeric(18,4), payload jsonb);
INSERT INTO evidence VALUES (1, 12345.6789, '{"verified": true}'), (2, NULL, '{"missing": true}');
SQL
# The dump must retain the exported view even when another writer commits.
coproc snapshot_session { docker exec -i "$server" psql -X -qAt -U postgres -d source_fixture -v ON_ERROR_STOP=1; }
snapshot_pid=$!
printf '%s\n' 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;' 'SELECT pg_export_snapshot();' >&"${snapshot_session[1]}"
IFS= read -r snapshot_id <&"${snapshot_session[0]}"
[[ "$snapshot_id" =~ ^[0-9A-F]{8}-[0-9A-F]{8}-[0-9]+$ ]]
docker exec "$server" psql -X -q -U postgres -d source_fixture -v ON_ERROR_STOP=1 \
  -c "INSERT INTO evidence VALUES (3, 999, '{\"later_commit\":true}');"
docker run --rm --network "$network" --volume "$scratch/proof:/proof" \
  "$image" pg_dump --host "$server" --username postgres \
    --dbname source_fixture --snapshot "$snapshot_id" --format custom --file /proof/evidence.dump
printf '%s\n' 'ROLLBACK;' '\q' >&"${snapshot_session[1]}"
wait "$snapshot_pid"
docker run --rm --network "$network" --volume "$scratch/proof:/proof:ro" \
  "$image" pg_restore --exit-on-error --no-owner --no-privileges \
    --host "$server" --username postgres --dbname restored_fixture /proof/evidence.dump
query="SELECT json_agg(t ORDER BY id)::text FROM (SELECT id, amount::text, payload FROM evidence) t;"
source_rows=$(docker exec "$server" psql -X -qAt -U postgres -d source_fixture -c "SELECT json_agg(t ORDER BY id)::text FROM (SELECT id, amount::text, payload FROM evidence WHERE id <= 2) t;")
restored_rows=$(docker exec "$server" psql -X -qAt -U postgres -d restored_fixture -c "$query")
test -n "$source_rows"
test "$source_rows" = "$restored_rows"
test "$(docker exec "$server" psql -X -qAt -U postgres -d source_fixture -c 'SELECT count(*) FROM evidence;')" = 3
printf '%s\n' 'PostgreSQL 18 runtime snapshot dump/restore excludes later committed writes'
