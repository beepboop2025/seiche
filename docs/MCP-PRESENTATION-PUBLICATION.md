# Hosted MCP presentation publication

The developer quickstart is a static `frontend/public/developers.html` file.
It is intentionally outside the standalone frontend overlay contract. Publish
it through the ordinary full publisher with a fresh exact-source catalog
receipt, retaining the frontend-only verifier and its path boundaries.

This candidate declares corpus publication receipt
`market-corpus-receipt-corpus-7cb1695c6affa707-r33`. The receipt revision changes
only publication binding. Corpus version, release ID, baseline counts, content
hashes and rights remain unchanged. Keep the previous r32 tag on its original
subject. Preparation does not assert that r33 has been signed or published.

Before signing the new receipt, compare all packaged Seiche files against the
signed `v0.16.2` subject, including README, license and build metadata. The
package and running backend must remain unchanged; a package difference needs
its own application release. Review and merge the exact candidate through CI,
then sign r33 on that exact merged source using the independently pinned owner
key. Run the ordinary catalog verifier, including its live PyPI, runtime and
deep corpus checks. Do not substitute a frontend receipt or move an old tag.

Assemble the full controller from that same source without a separate engine
pin. Hold only the existing full publisher's schedule, then select
`PUBLICATION_SOURCE_MODE=catalog` and the r33 catalog receipt; clear the old
source-equivalence setting. Upload the exact controller once. This path runs
the complete engine suite and build, includes the static developer page, and
retains the sealed recovery archive and public verification requirements.

Verify the served developer page, all three copy commands and the funding
example on both the canonical domain and Pages origin. Independently check the
published catalog and generated evidence. Restore the original four-hour
schedule only after acceptance and clean exit. Scheduled refreshes remain pinned
to this controller's signed source; later main commits cannot enter implicitly.

Retain the previous controller, source-equivalence tag, variable values and
mirror archive for recovery. No backend deployment, database replacement,
new package version or additional publication writer is part of this change.
