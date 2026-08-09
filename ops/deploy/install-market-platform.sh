#!/usr/bin/env bash
# Idempotently provision the canonical market data plane on the production box.
set -euo pipefail

APP_DIR="${SEICHE_APP_DIR:-/home/seiche/app}"
ENV_DIR="${SEICHE_ENV_DIR:-/etc/seiche}"
STATE_DIR="${SEICHE_MARKET_STATE_DIR:-/var/lib/seiche}"

if ! command -v psql >/dev/null 2>&1; then
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q postgresql
fi
systemctl enable --now postgresql

# Debian assigns the next free port when another local service already owns
# 5432. The production host has a Docker-published database on 5432, so the
# native cluster runs on 5433. Ask the cluster selected by pg_wrapper instead
# of assuming the default socket name.
POSTGRES_PORT=$(runuser -u postgres -- psql -tAc "SHOW port" | tr -d '[:space:]')
case "$POSTGRES_PORT" in
    ''|*[!0-9]*)
        echo "market platform: could not resolve the PostgreSQL cluster port" >&2
        exit 1
        ;;
esac

if ! runuser -u postgres -- psql -tAc \
        "SELECT 1 FROM pg_roles WHERE rolname='seiche'" | grep -qx 1; then
    runuser -u postgres -- createuser --no-createdb --no-createrole --no-superuser seiche
fi
if ! runuser -u postgres -- psql -tAc \
        "SELECT 1 FROM pg_database WHERE datname='seiche'" | grep -qx 1; then
    runuser -u postgres -- createdb --owner=seiche seiche
fi

install -d -o seiche -g seiche -m 0750 \
    "$STATE_DIR" "$STATE_DIR/raw" "$STATE_DIR/normalized" "$STATE_DIR/backfill"
install -d -o root -g seiche -m 0750 "$ENV_DIR"

ENV_STAGE=$(mktemp "$ENV_DIR/.market.env.XXXXXX")
cleanup() { rm -f -- "$ENV_STAGE"; }
trap cleanup EXIT
cat >"$ENV_STAGE" <<EOF
SEICHE_DATABASE_URL=postgresql:///seiche?host=/var/run/postgresql&port=$POSTGRES_PORT
SEICHE_RAW_CAPTURE_DIR=$STATE_DIR/raw
SEICHE_NORMALIZED_DIR=$STATE_DIR/normalized
SEICHE_BACKFILL_STATE_DIR=$STATE_DIR/backfill
SEICHE_CANONICAL_START=2000-01-01
EOF
chown root:seiche "$ENV_STAGE"
chmod 0640 "$ENV_STAGE"
mv -f "$ENV_STAGE" "$ENV_DIR/market.env"
ENV_STAGE=""

# Fail before changing service units if the application user cannot reach the
# exact socket/port written above. pg_wrapper succeeding as postgres is not a
# substitute for validating the DSN the API and collectors will actually use.
runuser -u seiche -- env \
    SEICHE_DATABASE_URL="postgresql:///seiche?host=/var/run/postgresql&port=$POSTGRES_PORT" \
    "$APP_DIR/backend/.venv/bin/python" -c \
    'import os, psycopg; connection = psycopg.connect(os.environ["SEICHE_DATABASE_URL"]); connection.execute("SELECT 1").fetchone(); connection.close()'

install -m 0644 "$APP_DIR/ops/deploy/seiche-market-worker.service" \
    /etc/systemd/system/seiche-market-worker.service
install -m 0644 "$APP_DIR/ops/deploy/seiche-market-backfill.service" \
    /etc/systemd/system/seiche-market-backfill.service

# The production API unit predates this repository's unit template.  A drop-in
# adds only the shared repository environment and writable evidence root.
install -d -m 0755 /etc/systemd/system/seiche-api.service.d
DROPIN=$(mktemp /etc/systemd/system/seiche-api.service.d/.market-platform.XXXXXX)
cat >"$DROPIN" <<EOF
[Service]
EnvironmentFile=-$ENV_DIR/market.env
ReadWritePaths=$STATE_DIR
EOF
chmod 0644 "$DROPIN"
mv -f "$DROPIN" /etc/systemd/system/seiche-api.service.d/market-platform.conf

systemctl daemon-reload
systemctl enable seiche-market-worker.service
# Submit both jobs together so the worker's After= relationship holds on the
# first rollout. A failed source can make the backfill unit red, but cannot
# prevent the persistent worker or other packs from starting afterward.
if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then
    systemctl start --no-block seiche-market-backfill.service seiche-market-worker.service
fi

echo "market platform: PostgreSQL on socket port $POSTGRES_PORT, evidence directories and collector units ready"
