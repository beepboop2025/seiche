# Public source-rights projection (2026-10-28)

This engineering policy is not a legal opinion, a blanket data license, or an
attestation that every public product route has completed a rights review.

## Reviewed Financial Evidence paths

The capital-market packet and table withhold current FRED-sourced VIX and ICE
BofA high-yield OAS observations, the dependent funding/TELL readings, and the
FRED-dependent official-bid block. The money-market packet and table withhold
FRED-tagged observations and histories plus their dependent market narratives
and policy-spread readings. Identity, source URLs, observation dates, units,
restriction reasons and terms links remain visible. Calling a number derived
does not itself establish permission to redistribute it.

This follows the source-specific permission gap documented at:
- https://fred.stlouisfed.org/legal/terms/
- https://fred.stlouisfed.org/series/BAMLH0A0HYM2
- https://fred.stlouisfed.org/series/VIXCLS

FRED's current terms include separate MCP Connector terms; this policy does
not assert that all AI use is forbidden. ICE's series notice and the VIX
citation notice are different conditions. No written permission covering the
current hosted redistribution was established in this review.

The filter runs on source fetches, and again on table projection so an older
upstream or cached packet cannot bypass it. Unrelated sources are unchanged.
Original acquired response hashes remain acquisition receipts. When the
returned document changes, publication_projection declares that it is a
projection and gives a separate SHA-256 over canonical returned JSON. No
FRED capture is relabelled as an original-publisher capture.

The existing funding-review contract uses separately qualified original
publisher acquisitions. This patch does not substitute old FRED values into
that contract or change its observations. China metadata-only restrictions
remain in force. It introduces no customer-license or private-data feature.

## Limits and release acceptance

The Seiche US pack separately marks fred_daily/fred_weekly metadata-only;
direct NYFed and Treasury adapters retain their existing policy. Current
policy must be checked at read time even for previously stored rows.

This patch does not claim rights approval for other Seiche endpoints,
prebuilt market payloads, FX/Estuary readings, or all third-party data in the
fleet. Review those source paths separately before broader attestations.

Before activation, verify the exact deployed source revision, run packet and
table regression tests, restart the owning service to drop old process
caches, then read back public packets and tables. Confirm withheld values
and histories remain absent, original-source values remain correctly
labelled, and dates/rights/projection hashes survive. Provider SUCCESS alone
is not evidence of those public outputs.
