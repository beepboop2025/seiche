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

Install the original written permission as
`/etc/seiche/cfets-licence-evidence.pdf`, `root:seiche` mode `0640`, with one
hard link. Runtime opens that fixed file with `O_NOFOLLOW` and verifies its
actual content hash; an arbitrary digest in the approval declaration is not
an entitlement. Create `/etc/seiche/cfets-approval.conf` with exactly these 13
newline-terminated fields (field order is not significant):

```text
schema=seiche.cfets-approval.v2
publisher=China Foreign Exchange Trade System
endpoints=https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/fdr-settings.json,https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/fdr-chrt.csv,https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis
upstream_products=FDR007,SHIBOR_ON
canonical_outputs=CN.CFETS.FDR007,CN.CFETS.SHIBOR_ON
collection_scope=automated_bounded_fdr007_and_shibor_on_history
permitted_use=internal_research_only
publication=prohibited
raw_response_retention=prohibited
retained_projection=event_date,value
licence_evidence_path=/etc/seiche/cfets-licence-evidence.pdf
licence_evidence_sha256=<SHA-256 of the retained written permission>
valid_until=<YYYY-MM-DD>
```

`valid_until` is the earlier of the permission's actual expiry and the next
operator review date, and may be no more than 366 days ahead. The artifact
permits internal research collection only. It does not authorize publishing
raw values, derived analytics, bulk data, or a public CN-CNY gauge; those uses
require a separately reviewed rights generation and code change.

FDR007 is the published fixing calculated from underlying DR007 transactions;
it is not the DR007 transaction-weighted market rate. Seiche therefore uses
the canonical output `CN.CFETS.FDR007` and never relabels this endpoint as
DR007.

Set the approval artifact to `root:seiche` mode `0640`, with one hard link. Compute the
SHA-256 of those exact bytes, including the final newline, then install
`/etc/seiche/cfets-access.env` as `root:seiche` mode `0640`:

```text
SEICHE_CFETS_APPROVAL_PATH=/etc/seiche/cfets-approval.conf
SEICHE_CFETS_APPROVAL_SHA256=<SHA-256 of cfets-approval.conf>
```

Run the normal reviewed deployment. `install-market-platform.sh` rejects
symlinks, wrong owners or modes, unknown or missing fields, scope changes,
approval or evidence digest mismatches, invalid dates, expired approval, and
review horizons longer than 366 days before replacing either collector unit.

## Runtime and lineage behavior

The market worker and one-shot backfill load `cfets-access.env` optionally and
read only the fixed approval and evidence paths. The adapter safely opens both
artifacts without following their final symlinks, validates metadata and exact
scope, hashes their bytes, and compares both digests to their pins. It validates
the same approval generation before every request, after every response, and
again after reducing a response to its permitted fields. The collector
supervisor independently repeats availability validation immediately before
the raw-capture, normalized-batch, and observation sinks. The separately
approved schema endpoint must name the chart columns and graphs exactly before
the headerless FDR chart is accepted. Redirects are not followed.

Upstream bodies exist only transiently in process memory. Before a document can
reach raw-capture persistence, the adapter reduces it to the approved
`event_date,value` projection and marks `raw_response_retained=false`. FDR001,
FDR014, other SHIBOR tenors, and unrelated response metadata are discarded.
Expiry, deletion, permission revocation, artifact rotation, schema drift, or
out-of-window rows stop collection before another request or retained write.

Successful documents and observation revision IDs carry a non-secret rights
generation derived from the approval-artifact digest. A rotated approval is
append-only lineage, not an overwrite of observations collected under an older
scope.

## Revocation and recovery

To revoke access, stop the market worker and any backfill, remove
`/etc/seiche/cfets-access.env`, move the approval and licence-evidence artifacts
into the restricted operator evidence archive, and restart through the normal deployment path.
The next collection is policy-unavailable with zero requests; cached records
are preserved and remain subject to `metadata_only` redaction.

After a legitimate renewal, create a new artifact, recompute its environment
pin, deploy, and verify the collector run, rights-generation lineage, normal
backup, scratch restore, and data-readiness receipt. Never hot-patch the module
or run a concurrent manual backfill.
