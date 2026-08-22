# Seiche market-data backups

`install-market-platform.sh` provisions two independent maintenance jobs for
the canonical PostgreSQL-backed market data plane. They do not collect data,
run a model, publish research, or grant execution authority.

## Narrow funding-export access

The installer creates the system group `seiche-world-model-readers`. Members
can traverse `/var/lib/seiche` and `/var/lib/seiche/exports`, but cannot list
either directory. The group can read only the setgid directory:

```text
/var/lib/seiche/exports/us-usd-funding-core-v1
```

The Seiche writer remains the directory owner. Its atomic temporary files
inherit the reader group and are committed as mode `0640`. A consumer installer
may later add its dedicated, unprivileged account to the group. Do not add a
consumer to the broader `seiche` group: that group also reaches private Seiche
configuration and unrelated state.

The group name can be changed with
`SEICHE_FUNDING_EXPORT_READER_GROUP` during installation. No consumer account,
model package, signing key, or object-storage credential is created here; those
belong to the consuming repository.

## Daily snapshot

`seiche-market-backup.timer` runs at 02:00 UTC with up to ten minutes of jitter,
after the current funding export window and before the host's heavier 03:15 UTC
jobs. The service is low-priority and capped at half a CPU and 1 GiB RAM.

Every run:

1. asks the selected native PostgreSQL cluster for its live port instead of
   assuming `5432` or `5433`;
2. writes a custom-format `pg_dump` of database `seiche`;
3. archives `/var/lib/seiche` with numeric ownership, ACLs, and xattrs;
4. copies the API/legacy data directory and replaces its live SQLite files
   with a transactionally consistent online backup verified by
   `PRAGMA quick_check`;
5. records a pre-dump lower bound for all four append-only/upsert-only market
   tables and the deployed Git SHA;
6. validates all dump/archive catalogues, then writes research-only/no-
   authority metadata and a `SHA256SUMS` inventory;
7. verifies that inventory, flushes the staged filesystem, and atomically renames the
   staging directory to `/var/backups/seiche-market/<UTC stamp>`; and
8. only after that commit, removes timestamped local snapshots older than the
   configured retention (21 days by default).

A failed run removes its hidden staging directory and cannot replace a prior
snapshot. Snapshot files are root-only mode `0600`; directories are `0700`.

Operator checks:

```sh
systemctl list-timers seiche-market-backup.timer
systemctl start seiche-market-backup.service
journalctl -u seiche-market-backup.service -n 100 --no-pager
```

## Weekly restore check

`seiche-market-restore-check.timer` runs Sundays at 07:30 UTC with up to fifteen
minutes of jitter. It selects the newest committed snapshot, verifies every
checksum, extracts the state tar archive into a temporary directory under the
recovery-proof root, verifies the restored API SQLite database with
`PRAGMA quick_check`, and restores the dump into a uniquely named scratch
database. Temporary extraction happens inside that dedicated root-controlled
directory. Every restored critical-table count must meet or exceed the
recorded
pre-dump floor before the filesystem scratch trees and scratch database are
removed. This lets continuous ingestion proceed during `pg_dump` without
turning ordinary appends into false backup failures.

The production `seiche` database and live state tree are never restore targets.
A trap drops the scratch database after either success or failure. A successful
check atomically records its receipt at:

```text
/var/lib/seiche-recovery-proof/backup-restore-check.status
```

Both services share `/run/lock/seiche-market-backup.lock`, so a manual check
cannot overlap an active backup. Failures enter systemd's failed-unit state and
use the production node's existing failure-alert handler.

Operator checks:

```sh
systemctl start seiche-market-restore-check.service
journalctl -u seiche-market-restore-check.service -n 100 --no-pager
cat /var/lib/seiche-recovery-proof/backup-restore-check.status
```

## Continuous collection and data readiness

`seiche-source-worker.service` refreshes the broad legacy/engine source cache
every five minutes. Source-native TTLs still govern upstream requests, so the
poll loop does not bypass publisher limits. The `Type=notify` service becomes
ready only after its first durable source sweep. Its systemd watchdog proves
process liveness, while the repository heartbeat expires when acquisition can
no longer complete; these are deliberately different signals.

`seiche-data-readiness.timer` runs every five minutes and fails closed when:

- the API snapshot is older than 15 minutes, future-dated beyond five minutes
  of clock skew, or contains collector/critical faults;
- either collector heartbeat is missing or overdue;
- the newest backup is older than 36 hours or implausibly future-dated;
- the exact v2 restore receipt is missing, invalid, older than eight days, or
  future-dated;
- a required service/timer is inactive, or a required timer is disabled for
  the next boot; or
- block or inode use reaches 90 percent on a monitored filesystem.

On a fresh host or the first v2 rollout, installation does not enable the
readiness timer immediately. It starts the source worker, runs a real backup,
restores and checks that snapshot in isolation, executes readiness once without
requiring its not-yet-active timer, and enables the timer only after that proof
passes. Any failed stage leaves the timer disabled and the deployment nonzero.

Operator checks:

```sh
systemctl status seiche-source-worker.service seiche-data-readiness.timer
systemctl start seiche-data-readiness.service
journalctl -u seiche-data-readiness.service -n 100 --no-pager
```

## Durability boundary

These units create and exercise **local** recovery points. They do not upload
anything. No Hetzner Object Storage, S3, Restic, rclone, or MinIO credential is
read or provisioned, and the unrelated Econ MinIO service on the same node must
not be reused as an off-node backup.

Production disaster recovery still requires a separately reviewed, private
off-node destination with least-privilege credentials, retention, and a full
operator restore drill. Until that exists, loss of the node's single disk can
remove both the live data and these local snapshots.
