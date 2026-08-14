# HK-HKD forward-payload canonicalization incident — 2026-08-14

## Decision

The HK-HKD forward chain is structurally intact.  One immutable gauge record
committed a Python JSON preimage containing a signed zero, after which
PostgreSQL `JSONB` stored and returned the numerically equivalent unsigned
zero.  The row and its descendants must not be updated, deleted, relinked, or
rehash-migrated.

Validation policy v3 contains one identity-bound compatibility declaration for
that record.  It accepts the row only after all of the following match the
audited incident: record, snapshot, market, product, cutoffs, calibration,
creation time, parent, stored payload hash, decoded payload hash, and JSON
pointer.  Replacing only that pointer with `-0.0` must reproduce the committed
payload hash, and the committed payload hash must reproduce the record hash.
Any other mismatch remains `FAIL`.

New snapshot and forward-record writers normalize floating zero to `0.0`
before canonical serialization, hashing, or persistence.  Both writers hash
and pass the same canonical UTF-8 JSON bytes into their persistence boundary.
SQLite preserves that text; PostgreSQL `JSONB` may store and return its own
normalized representation.

## Audited record

- `record_id` and `record_hash`:
  `35baac2858797c1673f23fcab723b8d40ef31945737a1c18f35753b51b23eb94`
- `snapshot_id`:
  `975fc5531c444b4123e058d29633fbecca95cd0100c2774e1b3943aeedc0fb7a`
- chain: `HK-HKD/gauge/hk-hkd-local-forward-v1`
- knowledge cutoff: `2026-08-11T16:41:17+00:00`
- parent:
  `38f929072db4c6cc157ccf5466a02f4967450ff9d8c4fa8fbcb9565a3721640e`
- pointer: `/components/0/kernel/value`
- committed preimage value: `-0.0`
- decoded `JSONB` value: `0.0`
- committed payload hash:
  `8a68b214f624e63406bdc1005dab46fdd86bca740fb98233b2c8aaf1dfbb61ea`
- decoded canonical payload hash:
  `c18a56ed0f58a2ff22daa829f8894714f1e104180448aaa10199296ee6a45722`

Replaying the 27 live gauge records through policy v3 produces `PASS`, zero
payload/record/id/link mismatches, and exactly one
`legacy_jsonb_signed_zero_compatibility_records` entry.  The head remains the
original record hash above.

## NZ-NZD disposition

The earlier NZ-NZD v1 failure is a different topology incident.  Those gauge
and overview forks remain immutable and reported as
`QUARANTINED_INTEGRITY_INCIDENT`; active evidence uses
`nz-nzd-local-forward-v2`.  On 2026-08-14 the live v2 chains passed integrity
with zero payload, record, or link mismatches.  Do not apply the HK
representation compatibility to NZ, and do not rewrite the v1 forks.  See
`docs/FORWARD_CHAIN_INCIDENT_2026-08-11.md`.

## Database implications

There is no schema or data migration.  In particular, do not issue `UPDATE`,
`DELETE`, or a corrective reinsert against `forward_validation_records` or
`market_snapshots`.  The compatibility declaration preserves the committed
preimage in reviewed code; policy-v2 failure artifacts remain immutable
incident evidence.  Policy v3 writes new evidence artifacts and makes older
runner versions stale for promotion.

## Production rollout and reconciliation

Use the normal reviewed-main deployment boundary.  Do not copy patched modules
into the live checkout.  Before releasing, create a fresh backup and complete
its scratch restore check:

```bash
BACKUP_BEFORE=$(find /var/backups/seiche-market -mindepth 1 -maxdepth 1 \
  -type d -name '20??????T??????Z' -print | LC_ALL=C sort | tail -n 1)
systemctl reset-failed seiche-market-backup.service
systemctl restart seiche-market-backup.service
systemctl show seiche-market-backup.service --property=Result --value | \
  grep -qx success
BACKUP_AFTER=$(find /var/backups/seiche-market -mindepth 1 -maxdepth 1 \
  -type d -name '20??????T??????Z' -print | LC_ALL=C sort | tail -n 1)
test -n "$BACKUP_AFTER" && test "$BACKUP_AFTER" != "$BACKUP_BEFORE"
test -f "$BACKUP_AFTER/SHA256SUMS"

RESTORE_STATUS=/var/lib/seiche/validation/backup-restore-check.status
CHECKED_BEFORE=$(sed -n 's/^checked_at=//p' "$RESTORE_STATUS" 2>/dev/null || true)
systemctl reset-failed seiche-market-restore-check.service
systemctl restart seiche-market-restore-check.service
systemctl show seiche-market-restore-check.service --property=Result --value | \
  grep -qx success
CHECKED_AFTER=$(sed -n 's/^checked_at=//p' "$RESTORE_STATUS")
test -n "$CHECKED_AFTER" && test "$CHECKED_AFTER" != "$CHECKED_BEFORE"
grep -Fqx "snapshot=$(basename "$BACKUP_AFTER")" "$RESTORE_STATUS"
cat "$RESTORE_STATUS"
```

If the backup reports that critical counts changed, quiesce the API and market
writers before repeating this block; do not accept an older successful unit
result or restore receipt as the release recovery point.

Merge the reviewed change to `main` and use the currently active GitHub Actions
forced-command deployment.  A push to `main` triggers it automatically; an
operator may rerun that exact tip from a trusted workstation if necessary:

```bash
git fetch origin main
REVIEWED_SHA=$(git rev-parse origin/main)
test "${#REVIEWED_SHA}" -eq 40
gh workflow run deploy-hetzner.yml --repo beepboop2025/seiche --ref main \
  -f target_sha="$REVIEWED_SHA"
DEPLOY_RUN_ID=$(gh run list --repo beepboop2025/seiche \
  --workflow deploy-hetzner.yml --branch main --event workflow_dispatch \
  --commit "$REVIEWED_SHA" \
  --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$DEPLOY_RUN_ID" --repo beepboop2025/seiche --exit-status
DEPLOYED_SHA=$(ssh liquilens-hetzner \
  'cat /var/lib/seiche-deploy/deployed-sha')
test "$DEPLOYED_SHA" = "$REVIEWED_SHA"
ssh liquilens-hetzner \
  'systemctl is-active seiche-api.service seiche-market-worker.service'
```

If `seiche-release-poll.timer` is later made the formally active controller,
follow `ops/deploy/RELEASE-POLLER.md` instead.  Do not enable it ad hoc while
the GitHub deployment trigger is active.  On the host, the resulting checks are:

```bash
read -r -p 'Reviewed 40-character commit: ' EXPECTED_SHA
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(cat /var/lib/seiche-deploy/deployed-sha)" = "$EXPECTED_SHA"
systemctl is-active seiche-api.service seiche-market-worker.service
```

The wrapper owns writer quiescence, tests, API restart, snapshot activation,
market-service restart, health checks, and rollback.  Do not run a second
manual deploy concurrently.

After deployment, run the scoped gate.  `market.env` is systemd syntax, so pass
its assignments through `env`; never source it because the DSN contains `&`:

```bash
mapfile -t MARKET_ENV < <(
  sed -n '/^SEICHE_[A-Z0-9_]*=/p' /etc/seiche/market.env
)
runuser -u seiche -- env "${MARKET_ENV[@]}" \
  /home/seiche/app/backend/.venv/bin/seiche market-validate \
    --market HK-HKD --check forward_paper_record \
    --minimum-forward-records 0 --minimum-forward-span-days 0
```

Expected integrity metrics are:

- `chain_integrity_status`: `PASS`;
- `payload_hash_mismatches`, `record_hash_mismatches`,
  `record_id_mismatches`, and `link_mismatches`: all zero;
- `legacy_jsonb_signed_zero_compatibility_records`: exactly one; and
- the compatibility entry names only the audited record and pointer above.

The overall gate remains `PENDING`, not promotion-ready, until forward outcome
review and a frozen maturity policy exist.  That is expected and must not be
converted to `PASS` administratively.

Once the scoped result is confirmed, clear the old policy-v2 unit failure and
produce the normal all-market policy-v3 evidence set:

```bash
systemctl reset-failed seiche-market-validation.service
systemctl start seiche-market-validation.service
systemctl show seiche-market-validation.service \
  --property=Result,ExecMainStatus --no-pager
```

`Result=success` with `ExecMainStatus=2` is expected while research gates are
pending.  A status of `1` is still a hard failure and requires inspection.

Finally, prove new gauge records extend the immutable incident head rather
than replacing it:

```sql
SELECT record_id, payload_hash, previous_record_hash, record_hash
  FROM forward_validation_records
 WHERE record_id =
   '35baac2858797c1673f23fcab723b8d40ef31945737a1c18f35753b51b23eb94';

SELECT record_id, previous_record_hash, created_at
  FROM forward_validation_records
 WHERE market_id = 'HK-HKD'
   AND product = 'gauge'
   AND calibration_id = 'hk-hkd-local-forward-v1'
   AND created_at > '2026-08-11T16:41:18.087325+00:00'
 ORDER BY created_at, record_id
 LIMIT 1;
```

The first query must retain the audited hashes byte-for-byte.  Once a new
gauge materialization occurs, the second query's `previous_record_hash` must be
the audited record ID.  If either assertion fails, stop forward writers and
treat it as a new integrity incident; do not broaden the compatibility map.
