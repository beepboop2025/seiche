# Import the retained NY Fed history

`seiche nyfed-history-import` is a bounded offline operation for the retained
September 9, 2026 archive. It reparses the original annual API responses and
normalizes 76,116 observations across the 34 declared NY Fed instruments. It
never loads the archive's earlier canonical JSONL or SQLite projection.

The trusted SHA256 of `artifact-inventory.json` is:

```
a286ffab4a1ed69355083055eade2c76d344d89a6c952c7de68b0735cb58f997
```

Keep this independently reviewed pin when staging a copy of the archive. Do not
replace it with a freshly computed digest merely to accept changed files. All
198 inventory members, source capture receipts, original raw hashes, official
methodology and terms captures are checked before import state or database
writes. Relative paths must remain contained, with no symlinks or traversal.
The original `private_root` recorded in metadata is only a logical path prefix;
the importer reads files under the explicitly selected staged directory.

## Validate before activation

Run the dry run from the qualified successor application release:

```sh
seiche nyfed-history-import \
  --archive-root /var/lib/seiche-platform/imports/nyfed-history-20260909 \
  --inventory-sha256 a286ffab4a1ed69355083055eade2c76d344d89a6c952c7de68b0735cb58f997 \
  --state-path /var/lib/seiche-platform/import-state/nyfed-history-20260909.json \
  --dry-run
```

Dry run reparses and checks the complete archive, emits a JSON receipt, and does
not initialize a repository, migrate a database, create a state directory,
reserve an ingestion clock, run collectors, or change source freshness. Use
private output files if retaining the receipt; it includes capture provenance.

The expected receipt has `status=PASS`, `accepted_observations=76116`,
`instruments=34`, and `publication_time_policy=unknown`. Missing source fields
remain explicit: the four percentiles for SOFR, TGCR and BGCR are absent on
2019-05-31 and 2021-08-05. No zero or copied percentile substitutes for those 24
unavailable observations. All 3,936 EFFR rows before 2016-03-01 stay native-only:
the earlier mean does not become a modern volume-weighted median. SOFRAI retains
its own value date, distinct from the overnight benchmarks' effective dates.

## Execute and resume

Production execution requires the qualified nullable-publication application
and storage migrations to be deployed and accepted first. Preserve the current
release's application/recovery handoff and use its documented operational locks;
this CLI does not authorize a release transition or change scheduler ownership.
Select the actual production repository through the existing deployment
configuration. After the dry run passes, execute the same command without
`--dry-run` and retain stdout as the import receipt.

The state path must be outside the immutable archive and on protected durable
storage included in portable recovery. The importer creates its state file with
mode 0600 and newly created parent directories with mode 0700. A file lock
serializes invocations sharing this path. Do not delete, edit, relocate to an
empty substitute, or regenerate this state to retry a failed import. Re-run the
same command with the same archive, pin and durable state path. The fsynced
intent and stored rows preserve the first-ingestion identity across partial
batch completion. A changed normalized projection or conflicting persisted
identity fails closed and requires review; it is not an invitation to edit the
saved clock or hashes.

Repository transactions append only previously absent
`market/instrument/event/source` keys. SQLite uses a write transaction and
PostgreSQL serializes the bounded import batch against collector inserts.
Existing canonical values, vintages, rights metadata and record hashes survive
unchanged, even when a retained capture disagrees. The receipt accounts for all
accepted source observations as imported or preserved existing observations.
For an empty store, the first import inserts 76,116 rows and an exact retry
inserts zero. Production may correctly insert fewer rows because recent live
history already exists.

The importer records actual present-day Seiche ingestion as `knowledge_time`.
It never offers a caller-supplied knowledge-time override, never uses event date
or historical capture time as new Seiche knowledge, and never claims historical
point-in-time availability. Publication time is JSON/SQL null, quality is
`provisional`, and staleness is `unknown`. Original capture timestamps (including
subseconds), URIs and response hashes remain in the state and receipt; each new
revision identity binds its import and raw capture hash. Ordinary live
collectors keep their default clock behavior.

## Accept the result

Check the import receipt and canonical readback: source scope and expected
counts; unknown publication in REST/MCP and CSV; exact basis-point, USD-million,
average and index values; no imported rows before first ingestion; current live
observations and source-freshness markers unchanged. Do not generate or rewrite
historical forecasts to accompany this import.

After production data changes, complete the release's own portable export,
isolated restore, immutable offsite readback and acceptance flow. The earlier
application recovery receipt does not prove that the newly imported database
and its durable state can be recovered. Include the staged raw archive or its
independently restorable private evidence location in the recovery handoff.
An isolated development import alone is not a production activation claim.

NY Fed reference-rate use remains subject to the retained terms, attribution,
non-endorsement and applicable third-party notices. Preserve the existing
product notices and the receipt's rights notice when presenting or distributing
the source data. This operation grants no additional source rights.
