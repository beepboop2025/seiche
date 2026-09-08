# Railway recovery execution

The first reviewed operation, `verify-existing`, downloads the exact immutable
versions of the recovery successfully exported and attested by GitHub run
34241275921. It repeats the original filesystem, Palimpsest China and PostgreSQL
18 restore checks on Railway. It requests no new production export, writes no
S3 object, and makes no production authority change.

`prepare.py` verifies the owner-signed controller commit and both original
GitHub receipt attestations. Controller files and the original backend/helpers
are copied from immutable Git objects. The resulting image carries their hash
manifest and the reviewed storage destination. The 15 original bundle members,
receipt version, sizes and hashes are pinned by the original attested receipt.
Original SSE-C, COMPLIANCE retention and downloaded-content checks remain active.

The root controller alone receives the seven `RECOVERY_S3_*` variables. The
restore process runs as UID 65532 with a fresh environment, closed inherited file
descriptors, no supplementary groups, no new privileges and no core dumps. It
cannot read the controller's keys or process environment, replace controller
code or overwrite the original receipts. PostgreSQL runs as its own UID with a
fresh local cluster, no production mount and only a loopback listener. All
candidate processes stop before their output is accepted.

`restore.sh` retains the original body with one changed output filename, leaving
the locked original restore proof intact. A strict adapter translates its two
exact Docker PostgreSQL commands to the pinned local PG18 binaries; all other
operations and targets fail. Compatibility names `GITHUB_WORKSPACE` and
`GITHUB_REPOSITORY` identify the original script's path and governance policy.
They do not identify its executor. The separate verification receipt explicitly
records `execution_platform: railway`, the actual Railway deployment and the
historical GitHub proof run. No GitHub OIDC issuer is synthesized.

Deploy only the reviewed assembled context, disconnected from repository
autodeployment, into the dedicated validation service. Keep one replica, restart
policy NEVER, no public endpoint and no shared or PR-environment variables.
`/evidence` retains bounded JSON verification receipts; downloaded backups and
restored databases are temporary. This first stage has no cron. Daily export,
Object Lock publication, governed acknowledgment and the genuine GitHub OIDC
tail remain on the original workflow until their complete replacements pass.

Rollback requires no production action: stop this independent verifier. Existing
production recovery policy, objects, keys and original workflow remain available.
