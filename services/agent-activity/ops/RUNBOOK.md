# Operations

Install only this service's committed subtree at
`/opt/seiche-agent-activity/releases/<40-character-sha>`, verify the archive SHA256,
then run `bash <release>/ops/install.sh <sha>` as root. Each release has its own
virtual environment and hash-pinned dependencies. The installer adds one Caddy
import under `/run/lock/seiche-railway-edge.lock` and refuses concurrent changes.
It validates the complete candidate before reloading Caddy. It never changes
the network, learning, commerce or other product release symlinks.

Service: `seiche-agent-activity`; bind `127.0.0.1:18804`.
State: `/var/lib/seiche-agent-activity`, owner service user, 0700.
Credentials: `/etc/seiche-agent-activity`, 0700; files 0600.
Backups: `/var/backups/seiche-agent-activity`, 0700; daily, seven days retained.
Public edge forwards `/activity/*` to `/agents/activity/*` on the origin.
Both root forms serve directly because the existing edge intentionally rejects redirects.

## Verify and monitor

```sh
systemctl status seiche-agent-activity --no-pager
systemctl list-timers 'seiche-agent-activity*'
curl -fsS https://agents.seiche.info/activity/health
curl -fsS https://agents.seiche.info/activity/v1/coverage
journalctl -u seiche-agent-activity-collect -n 20 --no-pager
```

Health is process/storage availability, not successful source collection. Inspect
each source's status, capture clock and collection window. Checkpoints show the
captured block range and observed head; historical coverage before the starting
block is uncollected. The collector checks due sources every five minutes. Sources
have individual intervals; failures retry after at most 15 minutes without advancing
the failed checkpoint. A failure is retained alongside prior observations.

`ops/check.py` writes attention states to the local journal and exits 2 for missing,
failed or stale configured sources and low disk. Systemd accepts that exit as a
completed check; it does not mean collection succeeded. No external notification
is sent. Configure an authorized alert receiver separately if desired.

Base's free RPC is best-effort and rate-limited. The service stops on HTTP 429 and
paces requests; it does not rotate identities or retry aggressively. For sustained
production collection, provision a dedicated Base RPC. Store a service-owned 0600
`/etc/seiche-agent-activity/base-rpc.json` containing `endpoint` and a reviewed
`review_ref`. The current allowlist accepts Base Mainnet Alchemy and QuickNode HTTPS
hosts. The endpoint may contain a key; it is not retained in receipts or logs.
Other providers require an explicit code review of the allowlist. Missing config
uses the public endpoint and reports any failure.

## Contributor operations

Run CLI commands as `seiche-agent-activity` with
`PYTHONPATH=/opt/seiche-agent-activity/current/src` and the release `.venv/bin/python`.
Use `python -m agent_activity.cli --state /var/lib/seiche-agent-activity` followed by:

- `applications`: private pilot requests, no automatic outbound messaging.
- `enroll --organization NAME --authority operator|broker|merchant|payment --grant-ref REF --independent-ref REF --days 90 --output PRIVATE_FILE`: writes a new credential privately. Add `--resale-ref REF` only for a separate actual grant.
- `review-identity --claim-id ID --review-ref REF`: independent attribution review. Add `--publish` only if public account observation and retention were separately authorized.
- `reconcile`: match submitted live fills to independent account evidence.
- `weekly-export --week YYYY-MM-DD --output PRIVATE_FILE`: closed Monday-to-Monday UTC cohort candidate, not automatic delivery.
- `export-status --id ID`: revalidate rights and evidence immediately before any delivery. A copied file cannot revoke itself; delivery systems must enforce this check.

Do not pass credentials as shell arguments or print them into evidence logs. Deliver
new contributor credentials through an independently authorized private channel.
The public contributor DELETE `/v1/me` revokes access and deletes active private rows.

## Backup and recovery

```sh
systemctl start seiche-agent-activity-backup.service
```

Backups use an online SQLite snapshot and authenticated encrypted chunks with
archive binding, ordered indices and a final database digest. Store the backup key
separately from copied archives. Preserve both in the owner's existing private
backup process. A daily server timer alone does not establish off-host recovery.

Run `restore --bundle PRIVATE_ENCRYPTED_FILE --key-file PRIVATE_KEY` with a new
`--state` destination. The restore verifies all chunks and SQLite integrity, then
revokes all contributor credentials and removes private submissions, claims,
requests and exports before returning. Re-enroll contributors from current grants;
do not revive withdrawn data from an older backup. Never aim a drill at active state.
The deployment also installs `com.liquilens.agent-activity-backup` on the owner's
Mac. It runs hourly through `ssd-data run`, verifies the ProductData mount, copies
only encrypted archives, checks SHA256 and retains seven days. Its pinned script
is retained at `/Users/mrinal/SSDWorkspace/artifacts/agent-activity-ops-20261014/`;
`ops/offhost-backup.py` records the deployed implementation. Check its private
`offhost-status.json` and the LaunchAgent exit status. This runs when the Mac and
its SSDs are available; it is not a continuously available second server. It
reports stale remote backups and never puts the recovery key beside the archive.

Public collection history can resume from its verified checkpoint. Automatic source
recurrence and automatic backup recurrence must be observed separately from manual runs.

## Rollback

To unpublish only this route, run `python3 <release>/ops/publish-route.py --remove`.
Stop/disable only `seiche-agent-activity` and its two timers. Do not restore an entire
old Caddyfile over another session's changes. For a code rollback, atomically point
`current` at the prior immutable release, update `ACTIVITY_RELEASE` in `service.env`,
and restart only this service. Each release's own virtual environment remains intact.
Keep state and encrypted archives for reviewed recovery; do not delete them to roll back.
