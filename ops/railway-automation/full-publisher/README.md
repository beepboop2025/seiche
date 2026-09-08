# Full Seiche engine publisher

Trusted Railway controller for the existing `publish.yml` job. It preserves the
signed backend release/catalog/runtime/PyPI gate before any expensive build.
The separate frontend-only receipt cannot authorize new engine output.

No repository autodeploy or public endpoint. Build and install commands execute
as UID10001 without deployment credentials; all descendants are stopped before
bounded regular-file output is sealed. Git and Cloudflare writes are performed
only by the immutable reviewed controller after fresh current-main checks.

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
