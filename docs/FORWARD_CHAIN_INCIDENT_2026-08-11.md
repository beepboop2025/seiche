# NZ-NZD forward-chain topology incident — 2026-08-11

## Decision

The existing `nz-nzd-local-forward-v1` rows are immutable incident evidence.
They must not be deleted, updated, relinked, or copied into a synthetic linear
history. Payload, record, and id hashes remain valid, but repeated parents left
the gauge graph with two heads and the overview graph with three heads.

Current NZ evidence starts at a new, hash-bound generation:
`nz-nzd-local-forward-v2`. The calibration components are unchanged. The id
change is the explicit evidence-protocol boundary, and because
`calibration_id` is part of every snapshot and forward-record hash it cannot be
silently confused with v1.

Promotion validation counts only the active calibration generation. It still
reports every historical product generation and labels a structurally invalid
one `QUARANTINED_INTEGRITY_INCIDENT`. A clean v2 chain begins as `PENDING`; v1
history never makes it mature and is never presented as having passed.

## Invariants

- Parent hashes, not `created_at`, define topology. Equal timestamps and reverse
  lexical hash order are valid when the parent edge is valid.
- A non-empty append target must have exactly one root, one head, no fork, no
  missing predecessor, no cycle, no orphan, and valid payload/record/id hashes.
- An empty generation may append exactly one genesis record.
- Every chain except the explicitly quarantined NZ-NZD v1 generation has a
  database uniqueness constraint on
  `(market_id, product, calibration_id, previous_record_hash)`. The exception
  is deliberately narrow so the migration preserves the known forks without
  weakening other active v1 writers.
- All authority remains research-only and fail-closed. A valid chain is
  necessary evidence, not permission to publish a model claim or execute.

## Production migration runbook

1. Stop every process that can append forward records: `seiche-api.service`,
   `seiche-market-worker.service`, `seiche-market-backfill.service`, and any
   ad-hoc `market-collect`, `market-backfill`, or `market-worker` process. The
   API warmer is a writer because its US snapshot path appends forward records.
   Stop any live timer/service that can reactivate the API, then prove every
   writer is inactive. The normal deploy wrapper stops only the two market
   daemons, so it is not sufficient by itself for this first rollout. The
   installer refuses an incomplete migration while the API, market daemons, or
   validation service is active, but operator quiescence remains the primary
   boundary.
2. Commit a fresh market backup and complete its scratch-database restore
   check while writers remain stopped. This is the recovery point; do not use
   application rollback as a reason to discard newer evidence.
3. Capture the v1 row count and the ordered tuples below for both products, and
   retain the output with the incident record:

   ```sql
   SELECT product, count(*) AS rows,
          array_agg(
            ARRAY[record_id, snapshot_id, previous_record_hash, record_hash]
            ORDER BY record_id
          ) AS immutable_identity
     FROM forward_validation_records
    WHERE market_id = 'NZ-NZD'
      AND calibration_id = 'nz-nzd-local-forward-v1'
    GROUP BY product
    ORDER BY product;
   ```

4. Confirm no v2 rows exist before the first rollout:

   ```sql
   SELECT count(*)
     FROM forward_validation_records
    WHERE calibration_id = 'nz-nzd-local-forward-v2';
   ```

   The required result is zero. A non-zero result needs a separate topology
   review before creating the partial unique index.
5. Confirm no duplicate children exist outside the known NZ-v1 quarantine:

   ```sql
   SELECT market_id, product, calibration_id, previous_record_hash, count(*)
     FROM forward_validation_records
    WHERE NOT (
      market_id = 'NZ-NZD'
      AND calibration_id = 'nz-nzd-local-forward-v1'
    )
    GROUP BY market_id, product, calibration_id, previous_record_hash
   HAVING count(*) > 1;
   ```

   The required result is no rows. Any result is a separate incident that must
   be quarantined or resolved without rewriting evidence before migration.
6. Deploy the application/schema change. PostgreSQL 11 or newer is required
   and enforced before migration. The installer initializes the repository
   while writers are stopped; PostgreSQL adds
   `chain_generation SMALLINT NOT NULL DEFAULT 1`, then creates the generation
   index and the narrowly partial unique-child index. SQLite performs the
   equivalent additive column migration. There is no v1 data rewrite or
   backfill.
7. Start only the updated writer. The first eligible NZ gauge and overview
   materialization creates independent v2 genesis links. Re-run the query from
   step 3 and require byte-for-byte identical tuples and counts.
8. Run the forward gate with a frozen policy and inspect both the active and
   quarantine metrics:

   ```text
   seiche market-validate --market NZ-NZD --check forward_paper_record \
     --minimum-forward-records 250 --minimum-forward-span-days 365
   ```

   Expected immediately after rollout: active generation v2, integrity intact,
   maturity `PENDING`, and v1 gauge/overview reported as quarantined incident
   evidence. Promotion remains blocked by outcome review and the other pack
   gates.

## Operational risks

- PostgreSQL creates the unique index during schema initialization, not with
  `CONCURRENTLY`; it can briefly block writers. Keep writers stopped and use a
  maintenance window appropriate to the table size.
- SQLite `ALTER TABLE` and index creation require a write lock. A second local
  process must not hold the database open for writing during first startup.
- The validation runner policy advances to v2 because topology and required
  product semantics changed. All prior runner-v1 artifacts are intentionally
  stale; the NZ calibration id also changes its pack fingerprint. Neither old
  artifact class can authorize promotion after rollout.
- Once any v2 record exists, rolling the writer back to the old code is unsafe:
  the old writer selects a head by timestamp across generations and does not
  fail closed on v1 forks. If application rollback is necessary, keep forward
  recording disabled until the fixed writer is restored.
- A uniqueness violation on append is an incident signal, not a retryable
  invitation to pick a different parent. Leave the attempted snapshot sealed,
  stop the writer, and inspect topology before resuming.
- Before the first v2 row, application rollback is allowed only with all
  writers stopped; leave the additive schema in place. After the first v2 row,
  old-code API, worker, backfill, and ad-hoc collection must never be started.
  Forward-fix the application without restoring an older database snapshot.
