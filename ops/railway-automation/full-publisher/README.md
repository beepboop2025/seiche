# Full Seiche engine publisher

Trusted Railway controller for the existing `publish.yml` job. It preserves the
signed backend release/catalog/runtime/PyPI gate before any expensive build.
The separate frontend-only receipt cannot authorize new engine output.

No repository autodeploy or public endpoint. Build and install commands execute
as UID10001 without deployment credentials; all descendants are stopped before
bounded regular-file output is sealed. Git and Cloudflare writes are performed
only by the immutable reviewed controller after fresh current-main checks.

Sealed output lives in a new private directory under the trusted release
checkout, so the original catalog verifier's root-containment and exact-byte
checks apply unchanged. The checked-in catalog and release identity files are
never replaced by builder output. Repository tests exercise the actual verifier
against the sealed path, an external path and changed catalog bytes; the minimal
controller image separately checks containment and source/output independence.

Provision a separate evidence volume and service-scoped SITE_DEPLOY_KEY,
CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID and RELEASE_SIGNING_KEY_FINGERPRINT.
PUBLISH_APPLY=0 proves preparation; PUBLISH_APPLY=1 additionally requires the
volume. The original four-hour cron is `23 2,6,10,14,18,22 * * *`, restart NEVER,
one replica, 5400-second total deadline. Activate its schedule only after a full
preparation/publication proof and coordinated retirement of the old publisher.

GDELT history is copied as bounded JSON and retained only after successful
publication. Full tests may be reused only for the identical source and controller
that completed publication. Every data generation still runs the release gate,
renderers, frontend tests/build, artifact gate and public dataset byte proof.

Build bundle includes the exact reviewed publish.yml, verifier hash manifest,
controller source receipt, known hosts and hash-locked social-card requirements.
The original GitHub workflow remains active until an actual passing replacement.

## Bounded engine projection

The optional `PUBLICATION_EQUIVALENCE_TAG=publication-source-equivalence-D`
requires a bundle assembled with separate immutable controller C and engine R
pins. Use `publisher/assemble.py --kind full --source C --engine-source R`;
the static bundle is assembled separately. All four gate blobs are pinned in
both subjects before any verifier import. The separate signed D receipt admits
current publication main H and authenticates C through the original R frontend
contract. A frontend receipt alone still cannot authorize fresh engine output.

H is kept in its own clean checkout and none of its executable, package, build,
renderer or controller files run. The full build begins with pristine signed R,
then materializes only the verifier's complete H desk-data snapshot. Each path
must match the finite desk-data grammar, be a regular `100644` Git blob, match
the exact H object ID and canonical admission digest, and fit the limits of
20,000 files, 8 MiB per file and 128 MiB total. Deletions are restricted to R desk
paths absent from H. The controller validates all inputs before modifying this
disposable build tree. This is explicitly a projection, not a pristine R tree.

All original workflow commands, including the entire engine test suite, execute
from that R-based projection. Test failures block preparation. The original
catalog verifier runs from the separate pristine R checkout before and after
generation; the generated catalog must retain R's bytes. The original runtime
checker additionally binds the live backend and corpus subjects to R and its
signed corpus receipt. Package/corpus checks, builder termination, sealed-file
bounds, durable recovery and credential isolation remain required.

Retained identity separates `publicationSourceSha` H, `controllerSourceSha` C,
`engineSourceSha`/`rendererSourceSha` R, source-equivalence D, the input-manifest
and desk-overlay digests, projection counts and live runtime identity. Main
checks always compare H, including before each public write and after public
verification; mirror compare-and-swap checks bind those writes to the observed
mirror head. The old GitHub full-publish workflow does not consume D and stays
incompatible with this new path. Retire it only after a replacement's complete
preparation, publication and recovery proof has been accepted. No controller
unit test or unsigned receipt preparation is a production-release proof.

Only one publication writer may be active across native static, native full and
GitHub publishers. Mirror compare-and-swap does not serialize Cloudflare writes.
Prepare both controllers without writes and perform controlled serial apply
with counterpart schedules held. After full acceptance, enable only this full
publisher's cron; retire GitHub/static schedules while retaining prior states.
