#!/usr/bin/env bash
# Install only the read-only relay; never start the retired Seiche API/writers.
set -euo pipefail
umask 0077

SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
ROOT=/opt/seiche-world-model-relay
ENV_FILE=/etc/seiche/world-model-delivery.env
EXPORT=/var/lib/liquilens-world-model/export/us-usd-funding-core-v2.json
UNIT=seiche-world-model-relay.service
fail() { printf 'standalone world-model relay: %s\n' "$*" >&2; exit 1; }

[ "${EUID:-$(id -u)}" -eq 0 ] || fail 'must run as root'
[ -f "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ] || fail 'existing relay configuration missing'
[ "$(stat -c '%U:%G:%a' "$ENV_FILE")" = root:seiche:640 ] || fail 'unsafe relay configuration permissions'
[ "$(stat -c '%U:%G:%a' "$EXPORT")" = liquilens-world-model:liquilens-world-model-readers:440 ] || fail 'unsafe signed export permissions'
runuser -u seiche -- test -r "$EXPORT" || fail 'existing reader cannot read the export'

for name in world_model_relay.py world_model_delivery.py; do
    [ -f "$SOURCE/backend/seiche/$name" ] && [ ! -L "$SOURCE/backend/seiche/$name" ] || fail 'unsafe source module'
done
[ ! -L "$ROOT" ] || fail 'installation root cannot be a symlink'
install -d -o root -g root -m 0755 "$ROOT" "$ROOT/releases"
digest=$(cat "$SOURCE/backend/seiche/world_model_relay.py" "$SOURCE/backend/seiche/world_model_delivery.py" | sha256sum | cut -d ' ' -f 1)
release="$ROOT/releases/$digest"
if [ ! -d "$release" ]; then
    install -d -o root -g root -m 0755 "$release"
    install -o root -g root -m 0444 "$SOURCE/backend/seiche/world_model_relay.py" "$SOURCE/backend/seiche/world_model_delivery.py" "$release/"
fi
for name in world_model_relay.py world_model_delivery.py; do
    cmp -- "$SOURCE/backend/seiche/$name" "$release/$name" || fail 'installed source differs'
done
if [ -L "$ROOT/current" ]; then
    readlink "$ROOT/current" > "$ROOT/previous-release"
elif [ -e "$ROOT/current" ]; then
    fail 'current installation is not a release link'
fi
ln -s "$release" "$ROOT/.current-$$"
mv -Tf "$ROOT/.current-$$" "$ROOT/current"
install -o root -g root -m 0644 "$SOURCE/ops/deploy/$UNIT" "/etc/systemd/system/$UNIT"
systemd-analyze verify "/etc/systemd/system/$UNIT"
systemctl daemon-reload
systemctl enable "$UNIT"
systemctl restart "$UNIT"
systemctl is-active --quiet "$UNIT" || fail 'relay did not start'
if ! /usr/bin/python3 -I - <<'PY'
import time
import urllib.error
import urllib.request

deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    try:
        urllib.request.urlopen(
            "http://127.0.0.1:8788/api/internal/v1/world-model/us-usd-funding-core-v2",
            timeout=1,
        ).close()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SystemExit(0)
    except OSError:
        pass
    time.sleep(0.2)
raise SystemExit(1)
PY
then
    systemctl stop "$UNIT"
    fail 'relay did not become ready with authentication required'
fi
printf 'standalone world-model relay: installed source digest=%s on loopback:8788; Caddy routing is a separate scoped change\n' "$digest"
