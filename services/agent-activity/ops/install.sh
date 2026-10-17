#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run the reviewed installer as root'; exit 1; }
activity_sha=${1:?exact committed source SHA required}
[[ $activity_sha =~ ^[a-f0-9]{40}$ ]] || exit 1
activity_root=/opt/seiche-agent-activity
activity_release="$activity_root/releases/$activity_sha"
[[ -f "$activity_release/ops/requirements.lock" ]] || exit 1
exec 9>/run/lock/seiche-agent-activity-deploy.lock
flock --exclusive --nonblock 9
[[ $(df --output=avail -B1 /opt | tail -1) -gt 5368709120 ]] || { echo 'Less than 5 GiB available'; exit 1; }
if ! id seiche-agent-activity >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/seiche-agent-activity --no-create-home --shell /usr/sbin/nologin seiche-agent-activity
fi
install -d -m 0700 -o seiche-agent-activity -g seiche-agent-activity /var/lib/seiche-agent-activity /etc/seiche-agent-activity /var/backups/seiche-agent-activity
if [[ ! -x "$activity_release/.venv/bin/python" ]]; then
  python3 -m venv "$activity_release/.venv"
  "$activity_release/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir --require-hashes -r "$activity_release/ops/requirements.lock"
fi
chown -R root:root "$activity_release"
chmod -R a+rX "$activity_release"
if [[ ! -f /etc/seiche-agent-activity/operator.json ]]; then
  runuser -u seiche-agent-activity -- env PYTHONPATH="$activity_release/src" PYTHONDONTWRITEBYTECODE=1 "$activity_release/.venv/bin/python" -m agent_activity.cli --state /var/lib/seiche-agent-activity init --operator-output /etc/seiche-agent-activity/operator.json --backup-key-output /etc/seiche-agent-activity/backup.key
fi
printf 'ACTIVITY_RELEASE=%s\n' "$activity_sha" > /etc/seiche-agent-activity/service.env
chown seiche-agent-activity:seiche-agent-activity /etc/seiche-agent-activity/service.env
activity_previous=$(readlink "$activity_root/current" || true)
ln -s "$activity_release" "$activity_root/current.next"
mv -Tf "$activity_root/current.next" "$activity_root/current"
for activity_unit in "$activity_release"/ops/seiche-agent-activity*.service "$activity_release"/ops/seiche-agent-activity*.timer; do
  install -m 0644 "$activity_unit" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now seiche-agent-activity.service
systemctl restart seiche-agent-activity.service
activity_healthy=false
for activity_attempt in {1..15}; do
  if curl --fail --silent http://127.0.0.1:18804/health | "$activity_release/.venv/bin/python" -c 'import json,sys; assert json.load(sys.stdin)["release"]==sys.argv[1]' "$activity_sha" 2>/dev/null; then
    activity_healthy=true
    break
  fi
  sleep 1
done
if [[ $activity_healthy != true ]]; then
  if [[ -n $activity_previous ]]; then
    ln -s "$activity_previous" "$activity_root/current.rollback"
    mv -Tf "$activity_root/current.rollback" "$activity_root/current"
    printf 'ACTIVITY_RELEASE=%s\n' "$(basename "$activity_previous")" > /etc/seiche-agent-activity/service.env
    systemctl restart seiche-agent-activity.service
  else
    systemctl stop seiche-agent-activity.service
  fi
  echo 'Canary health failed; public route not changed'
  exit 1
fi
install -m 0644 "$activity_release/ops/agent-activity.caddy" /etc/caddy/agent-activity.caddy
python3 "$activity_release/ops/publish-route.py"
systemctl enable --now seiche-agent-activity-collect.timer seiche-agent-activity-backup.timer
echo "activity_release=$activity_sha"
