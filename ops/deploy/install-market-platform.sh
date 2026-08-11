#!/usr/bin/env bash
# Idempotently provision the canonical market data plane on the production box.
set -euo pipefail

APP_DIR="${SEICHE_APP_DIR:-/home/seiche/app}"
ENV_DIR="${SEICHE_ENV_DIR:-/etc/seiche}"
STATE_DIR="${SEICHE_MARKET_STATE_DIR:-/var/lib/seiche}"
BACKUP_DIR="${SEICHE_MARKET_BACKUP_DIR:-/var/backups/seiche-market}"
EXPORT_READER_GROUP="${SEICHE_FUNDING_EXPORT_READER_GROUP:-seiche-world-model-readers}"
FUNDING_EXPORT_DIR="$STATE_DIR/exports/us-usd-funding-core-v1"
FUNDING_EXPORT_FILE="$FUNDING_EXPORT_DIR/us-usd-funding-core-v1.json"
DELIVERY_ENV_FILE="${SEICHE_WORLD_MODEL_DELIVERY_ENV_FILE:-$ENV_DIR/world-model-delivery.env}"
DELIVERY_PATH=/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json
DELIVERY_READER_GROUP=liquilens-world-model-readers

PACKAGES=()
if ! command -v psql >/dev/null 2>&1; then
    PACKAGES+=(postgresql)
fi
if ! command -v setfacl >/dev/null 2>&1; then
    PACKAGES+=(acl)
fi
if [ ! -x /usr/bin/setpriv ]; then
    PACKAGES+=(util-linux)
fi
if [ "${#PACKAGES[@]}" -gt 0 ]; then
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${PACKAGES[@]}"
fi
[ -x /usr/bin/setpriv ] || {
    echo "market platform: /usr/bin/setpriv is required for sandboxed PostgreSQL backups" >&2
    exit 1
}
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
    "$STATE_DIR" "$STATE_DIR/raw" "$STATE_DIR/normalized" "$STATE_DIR/backfill" \
    "$STATE_DIR/validation" "$STATE_DIR/exports" \
    "$FUNDING_EXPORT_DIR"
install -d -o root -g seiche -m 0750 "$ENV_DIR"
install -d -o root -g root -m 0700 "$BACKUP_DIR"

# Give the future Lab runtime access to only the stable funding-core export.
# The group is provisioned independently of the consumer account so a Seiche
# deploy does not depend on another repository already being installed.  The
# Lab installer may add its dedicated account to this group later.  Execute-
# only ACLs on the ancestors prevent group members from listing or reading any
# other Seiche state or export.
case "$EXPORT_READER_GROUP" in
    ''|*[!a-zA-Z0-9_.-]*)
        echo "market platform: invalid funding export reader group" >&2
        exit 1
        ;;
esac
if ! getent group "$EXPORT_READER_GROUP" >/dev/null; then
    groupadd --system "$EXPORT_READER_GROUP"
fi
setfacl -m "g:$EXPORT_READER_GROUP:--x" "$STATE_DIR" "$STATE_DIR/exports"
chown seiche:"$EXPORT_READER_GROUP" "$FUNDING_EXPORT_DIR"
chmod 2750 "$FUNDING_EXPORT_DIR"
if [ -f "$FUNDING_EXPORT_FILE" ] && [ ! -L "$FUNDING_EXPORT_FILE" ]; then
    chown seiche:"$EXPORT_READER_GROUP" "$FUNDING_EXPORT_FILE"
    chmod 0640 "$FUNDING_EXPORT_FILE"
elif [ -e "$FUNDING_EXPORT_FILE" ] || [ -L "$FUNDING_EXPORT_FILE" ]; then
    echo "market platform: funding export target is not a regular file" >&2
    exit 1
fi

ENV_STAGE=$(mktemp "$ENV_DIR/.market.env.XXXXXX")
VALIDATION_STAGE=""
BACKUP_STAGE=""
RESTORE_STAGE=""
cleanup() {
    rm -f -- "$ENV_STAGE" "$VALIDATION_STAGE" "$BACKUP_STAGE" "$RESTORE_STAGE"
}
trap cleanup EXIT
cat >"$ENV_STAGE" <<EOF
SEICHE_DATABASE_URL=postgresql:///seiche?host=/var/run/postgresql&port=$POSTGRES_PORT
SEICHE_RAW_CAPTURE_DIR=$STATE_DIR/raw
SEICHE_NORMALIZED_DIR=$STATE_DIR/normalized
SEICHE_BACKFILL_STATE_DIR=$STATE_DIR/backfill
SEICHE_VALIDATION_DIR=$STATE_DIR/validation
SEICHE_USD_FUNDING_CORE_EXPORT_DIR=$FUNDING_EXPORT_DIR
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
install -m 0644 "$APP_DIR/ops/deploy/seiche-market-validation.service" \
    /etc/systemd/system/seiche-market-validation.service
install -m 0644 "$APP_DIR/ops/deploy/seiche-market-validation.timer" \
    /etc/systemd/system/seiche-market-validation.timer
install -m 0644 "$APP_DIR/ops/deploy/seiche-market-backup.service" \
    /etc/systemd/system/seiche-market-backup.service
install -m 0644 "$APP_DIR/ops/deploy/seiche-market-backup.timer" \
    /etc/systemd/system/seiche-market-backup.timer
install -m 0644 "$APP_DIR/ops/deploy/seiche-market-restore-check.service" \
    /etc/systemd/system/seiche-market-restore-check.service
install -m 0644 "$APP_DIR/ops/deploy/seiche-market-restore-check.timer" \
    /etc/systemd/system/seiche-market-restore-check.timer

# The base unit documents the default production path. A drop-in resets the
# writable sandbox to the configured state root, keeping ProtectSystem=strict
# compatible with SEICHE_MARKET_STATE_DIR overrides.
install -d -m 0755 /etc/systemd/system/seiche-market-validation.service.d
VALIDATION_STAGE=$(mktemp \
    /etc/systemd/system/seiche-market-validation.service.d/.state-path.XXXXXX)
cat >"$VALIDATION_STAGE" <<EOF
[Service]
ReadWritePaths=
ReadWritePaths=$STATE_DIR/validation
EOF
chmod 0644 "$VALIDATION_STAGE"
mv -f "$VALIDATION_STAGE" \
    /etc/systemd/system/seiche-market-validation.service.d/state-path.conf
VALIDATION_STAGE=""

# Backup units are repository templates with production defaults. Drop-ins
# keep their sandboxes exact when an operator uses supported path overrides.
install -d -m 0755 /etc/systemd/system/seiche-market-backup.service.d
BACKUP_STAGE=$(mktemp \
    /etc/systemd/system/seiche-market-backup.service.d/.paths.XXXXXX)
cat >"$BACKUP_STAGE" <<EOF
[Service]
Environment=SEICHE_APP_DIR=$APP_DIR
Environment=SEICHE_MARKET_STATE_DIR=$STATE_DIR
Environment=SEICHE_MARKET_BACKUP_DIR=$BACKUP_DIR
ReadOnlyPaths=
ReadOnlyPaths=$APP_DIR $STATE_DIR
ReadWritePaths=
ReadWritePaths=$BACKUP_DIR /run/lock
EOF
chmod 0644 "$BACKUP_STAGE"
mv -f "$BACKUP_STAGE" \
    /etc/systemd/system/seiche-market-backup.service.d/paths.conf
BACKUP_STAGE=""

install -d -m 0755 /etc/systemd/system/seiche-market-restore-check.service.d
RESTORE_STAGE=$(mktemp \
    /etc/systemd/system/seiche-market-restore-check.service.d/.paths.XXXXXX)
cat >"$RESTORE_STAGE" <<EOF
[Service]
Environment=SEICHE_APP_DIR=$APP_DIR
Environment=SEICHE_MARKET_STATE_DIR=$STATE_DIR
Environment=SEICHE_MARKET_BACKUP_DIR=$BACKUP_DIR
ReadOnlyPaths=
ReadOnlyPaths=$APP_DIR $BACKUP_DIR
ReadWritePaths=
ReadWritePaths=$STATE_DIR/validation /run/lock
EOF
chmod 0644 "$RESTORE_STAGE"
mv -f "$RESTORE_STAGE" \
    /etc/systemd/system/seiche-market-restore-check.service.d/paths.conf
RESTORE_STAGE=""

# A relay is disabled unless the separately provisioned, root-controlled
# credential file exists.  When it does, validate its exact three-line shape
# without sourcing or printing the bearer secret, then converge only the API
# identity's membership in Lab's dedicated export-reader group.
if [ -e "$DELIVERY_ENV_FILE" ] || [ -L "$DELIVERY_ENV_FILE" ]; then
    [ -f "$DELIVERY_ENV_FILE" ] && [ ! -L "$DELIVERY_ENV_FILE" ] || {
        echo "market platform: world-model delivery env is not a regular file" >&2
        exit 1
    }
    [ "$(stat -c '%U:%G:%a' "$DELIVERY_ENV_FILE")" = "root:seiche:640" ] || {
        echo "market platform: world-model delivery env ownership/mode is unsafe" >&2
        exit 1
    }
    if ! { [ "$(wc -l <"$DELIVERY_ENV_FILE" | tr -d '[:space:]')" = "3" ] \
        && grep -Fxq "SEICHE_WORLD_MODEL_DELIVERY_PATH=$DELIVERY_PATH" \
            "$DELIVERY_ENV_FILE" \
        && grep -Eq '^SEICHE_WORLD_MODEL_DELIVERY_BEARER_TOKEN=[0-9a-f]{64}$' \
            "$DELIVERY_ENV_FILE" \
        && grep -Eq '^SEICHE_WORLD_MODEL_DELIVERY_MAX_BYTES=[0-9]+$' \
            "$DELIVERY_ENV_FILE"; }; then
        echo "market platform: world-model delivery env contract is invalid" >&2
        exit 1
    fi
    DELIVERY_MAX_BYTES=$(sed -n \
        's/^SEICHE_WORLD_MODEL_DELIVERY_MAX_BYTES=//p' "$DELIVERY_ENV_FILE")
    [ "$DELIVERY_MAX_BYTES" -ge 1 ] \
        && [ "$DELIVERY_MAX_BYTES" -le 5242880 ] || {
        echo "market platform: world-model delivery byte limit is unsafe" >&2
        exit 1
    }
    getent group "$DELIVERY_READER_GROUP" >/dev/null || {
        echo "market platform: Lab delivery reader group is not provisioned" >&2
        exit 1
    }
    if ! id -nG seiche | tr ' ' '\n' | grep -Fxq "$DELIVERY_READER_GROUP"; then
        usermod -a -G "$DELIVERY_READER_GROUP" seiche
    fi
    if [ ! -f "$DELIVERY_PATH" ] || [ -L "$DELIVERY_PATH" ]; then
        echo "market platform: exact signed Lab delivery is not readable" >&2
        exit 1
    fi
    [ "$(stat -c '%U:%G:%a' /var/lib/liquilens-world-model)" \
        = "liquilens-world-model:$DELIVERY_READER_GROUP:710" ] \
        && [ "$(stat -c '%U:%G:%a' /var/lib/liquilens-world-model/export)" \
        = "liquilens-world-model:$DELIVERY_READER_GROUP:2750" ] \
        && [ "$(stat -c '%U:%G:%a' "$DELIVERY_PATH")" \
        = "liquilens-world-model:$DELIVERY_READER_GROUP:440" ] || {
        echo "market platform: Lab delivery permission boundary is unsafe" >&2
        exit 1
    }
    if ! runuser -u seiche -- test -r "$DELIVERY_PATH"; then
        echo "market platform: exact signed Lab delivery is not readable" >&2
        exit 1
    fi
fi

# The production API unit predates this repository's unit template.  A drop-in
# adds only the shared repository environment and writable evidence root.
install -d -m 0755 /etc/systemd/system/seiche-api.service.d
DROPIN=$(mktemp /etc/systemd/system/seiche-api.service.d/.market-platform.XXXXXX)
cat >"$DROPIN" <<EOF
[Service]
EnvironmentFile=-$ENV_DIR/market.env
EnvironmentFile=-$DELIVERY_ENV_FILE
ReadWritePaths=$STATE_DIR
EOF
chmod 0644 "$DROPIN"
mv -f "$DROPIN" /etc/systemd/system/seiche-api.service.d/market-platform.conf

systemctl daemon-reload
systemctl enable seiche-market-worker.service
# Validation is an independent read/audit schedule. Starting the timer does not
# wait for a run and must not participate in the API/collector deploy gate.
systemctl enable --now seiche-market-validation.timer
# Backups and restore checks are independent of API deployment and never start
# a data collection. Enabling their timers is therefore safe while a candidate
# release remains behind the deploy health gate.
systemctl enable --now \
    seiche-market-backup.timer seiche-market-restore-check.timer
# Submit both jobs together so the worker's After= relationship holds on the
# first rollout. A failed source can make the backfill unit red, but cannot
# prevent the persistent worker or other packs from starting afterward.
if [ "${SEICHE_DEFER_MARKET_START:-0}" != "1" ]; then
    systemctl start --no-block seiche-market-backfill.service seiche-market-worker.service
fi

echo "market platform: PostgreSQL on socket port $POSTGRES_PORT, narrow funding export ACL, evidence directories, backups and collector units ready"
