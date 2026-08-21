# CFETS collection approval boundary

## Default decision

Seiche does not collect CFETS/ChinaMoney values by default. The `CN-CNY`
source remains visible in the catalog as `metadata_only`, but missing or
invalid approval produces an `UNAVAILABLE` collector result before any HTTP
request. Palimpsest's native censorship readings remain enabled; its
CFETS-derived `china-econ-history` mirror is not requested.

Do not create the files below merely to make a deployment or backfill pass.
Provision them only after the operator has written permission or a licence
covering automated collection of the named datasets from the production IP and
the stated cadence. The service-data terms are published at
<https://www.chinamoney.com.cn/english/svcmds/>.

## Content-bound approval artifact

Retain the original permission evidence in root-controlled records and compute
its SHA-256. Create `/etc/seiche/cfets-approval.conf` with exactly these eight
newline-terminated fields (field order is not significant):

```text
schema=seiche.cfets-approval.v1
publisher=China Foreign Exchange Trade System
datasets=CN.CFETS.DR007,CN.CFETS.SHIBOR_ON
collection_scope=automated_fdr007_and_shibor_history
permitted_use=internal_research_only
publication=prohibited
licence_evidence_sha256=<SHA-256 of the retained written permission>
valid_until=<YYYY-MM-DD>
```

`valid_until` is the earlier of the permission's actual expiry and the next
operator review date, and may be no more than 366 days ahead. The artifact
permits internal research collection only. It does not authorize publishing
raw values, derived analytics, bulk data, or a public CN-CNY gauge; those uses
require a separately reviewed rights generation and code change.

Set the artifact to `root:seiche` mode `0640`, with one hard link. Compute the
SHA-256 of those exact bytes, including the final newline, then install
`/etc/seiche/cfets-access.env` as `root:seiche` mode `0640`:

```text
SEICHE_CFETS_APPROVAL_PATH=/etc/seiche/cfets-approval.conf
SEICHE_CFETS_APPROVAL_SHA256=<SHA-256 of cfets-approval.conf>
```

Run the normal reviewed deployment. `install-market-platform.sh` rejects
symlinks, wrong owners or modes, unknown or missing fields, scope changes,
digest mismatches, invalid dates, expired approval, and review horizons longer
than 366 days before replacing either collector unit.

## Runtime and lineage behavior

The market worker and one-shot backfill load `cfets-access.env` optionally and
read only the fixed approval path. The adapter safely opens the artifact
without following its final symlink, validates the opened file's metadata and
exact schema, hashes its bytes, and compares that digest to the environment
pin. It repeats the full validation before the FDR007 request and before every
bounded SHIBOR history window. Expiry, deletion, permission revocation, or
artifact rotation therefore stops the next request.

Successful documents and observation revision IDs carry a non-secret rights
generation derived from the approval-artifact digest. A rotated approval is
append-only lineage, not an overwrite of observations collected under an older
scope.

## Revocation and recovery

To revoke access, stop the market worker and any backfill, remove
`/etc/seiche/cfets-access.env`, move the approval artifact into the restricted
operator evidence archive, and restart through the normal deployment path.
The next collection is policy-unavailable with zero requests; cached records
are preserved and remain subject to `metadata_only` redaction.

After a legitimate renewal, create a new artifact, recompute its environment
pin, deploy, and verify the collector run, rights-generation lineage, normal
backup, scratch restore, and data-readiness receipt. Never hot-patch the module
or run a concurrent manual backfill.
