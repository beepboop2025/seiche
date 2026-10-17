# Current evidence and daily information demand

The central gateway measures information requested from free product routes.
It does not observe an agent's unrelated browsing, portfolio, orders or purchases.
Registered credentials support repeat-use counts; anonymous requests remain
unidentified. Provider inventories and dataset downloads are separate context.

## Current data

`GET /v1/data-status/core` publishes a bounded source audit for LiquiLens,
Seiche and Undertow. `ops/seiche-core-freshness.timer` runs hourly. The audit
atomically updates `core-freshness/latest.json` and preserves the first check
of each UTC day in `core-freshness/daily/`. These are metadata-only audits,
not source refreshes. Set `SEICHE_CORE_FRESHNESS_REPORT` for the serving process.
Reports older than two hours cease to be current. A failed current-view check
or unavailable endpoint fails the job; known source gaps remain explicitly
partial. Never equate a successful job with universal freshness.

The audit covers vetted institution dossiers and disclosure reviews, the actual
crypto series dates, stablecoin preview limits, Seiche's native source clocks,
and Undertow's declared stale/restricted inputs and public measure subset.
Native model, source-admission and decision gates remain authoritative.

Fresh collection does not imply a new economic release. Quarterly and annual
filings use their native age policies. Seiche's fetch time and observation date
stay separate. Retired series remain historical. Unknown source coverage cannot
receive a 100 percent readiness claim.

Wake current projections omit expired collections. Dated receipts stay in the
retained ledger for historical analysis. The dashboard independently checks
each source receipt's clock and identity when reading its generated report;
rewriting a report never extends an old collection's lifetime.

## Owned product demand

`GET /v1/operator/demand-history` uses the separate operator report credential.
It returns only completed UTC days within existing 30-day operational retention,
with bounded dataset labels and request/outcome/registered-credential counts.
It excludes declared synthetic/internal traffic and discovery. No arguments,
prompts, raw IPs or agent IDs are returned. Unknown traffic can still include
monitors. Returning credentials are not verified autonomous trading agents.
Workspace erasure and expiry apply to the underlying rows immediately; this
endpoint does not create a second retained copy of participant data.

The owner dashboard displays this history. Its two research targets are:

| Model track | Candidate outcome | Admission needed |
|---|---|---|
| Market liquidity | Future bid-ask spread, depth and order-flow imbalance | Permitted dated market labels, chronological joins and cost-aware evaluation |
| Economic demand | Future service usage and released demand statistics | Permitted usage or publication vintages and separate chronological evaluation |

Information-demand features may be common to the two tracks. Targets, datasets
and evaluations must remain separate. Operational collection is not a training
grant. `model_training=false` and `production_model_eligible=false` remain until
the separate contribution/rights process and prospective evaluation qualify a
dataset. Do not train on rewritten vintages or infer outcomes from requests.

`ops/seiche-fleet-activity.timer` exports the fixed host digest hourly using
`--print-only`, which sends no Telegram. The privileged observer writes a
root-owned, group-readable aggregate file; Wake only reads that file through
`WAKE_FLEET_REPORT`. Add `fleet` to explicitly selected Wake collection sources.
The adapter preserves the observer's original time and rejects expired files.
The app receives neither host SSH keys nor access to the system journal.

Fleet samples are overlapping 24-hour dispatch windows with incomplete host
coverage. LiquiLens and active Railway Seiche completion traffic are outside that
host sample. Central gateway requests are measured independently. Do not add
these sources into a supposed unique-agent count or daily market-flow total.
