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
docker run --rm --network "$network" --volume "$scratch/proof:/proof" \
  "$image" pg_dump --host "$server" --username postgres \
    --dbname source_fixture --format custom --file /proof/evidence.dump
docker run --rm --network "$network" --volume "$scratch/proof:/proof:ro" \
  "$image" pg_restore --exit-on-error --no-owner --no-privileges \
    --host "$server" --username postgres --dbname restored_fixture /proof/evidence.dump
query="SELECT json_agg(t ORDER BY id)::text FROM (SELECT id, amount::text, payload FROM evidence) t;"
source_rows=$(docker exec "$server" psql -X -qAt -U postgres -d source_fixture -c "$query")
restored_rows=$(docker exec "$server" psql -X -qAt -U postgres -d restored_fixture -c "$query")
test -n "$source_rows"
test "$source_rows" = "$restored_rows"
printf '%s\n' 'PostgreSQL 18 runtime dump/restore round-trip passed'
