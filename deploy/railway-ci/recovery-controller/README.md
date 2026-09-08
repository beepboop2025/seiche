# Railway recovery execution

The first reviewed operation, `verify-existing`, downloads the exact immutable
versions of the recovery successfully exported and attested by GitHub run
34241275921. It repeats the original filesystem, Palimpsest China and PostgreSQL
18 restore checks on Railway. It requests no new production export, writes no
S3 object, and makes no production authority change.

The image normalizes its immutable code to root-owned files that the isolated
restore UID can read, with traversable directories and no unprivileged writes.
An actual-image test reads and hashes every manifest member as that UID, imports
the original recovery modules, and checks the packaged restore script. Runtime
credentials remain in a separate private directory and are never in the image.

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


## Prepared recurring execution and genuine attestation tail

`prepare.py --recurring-source <full SHA> --production-target <private JSON>
--execution-public-key <public hex file>` adds the original governed export,
restore, and Object Lock scripts from `fff0eabb26088292451edaef10bdd203c074a992`.
Only execution-variable names and private authentication-header transport differ.
GitHub's mask directive is replaced by a private bearer header file, so native
logs and child arguments never carry the download bearer. Current
main is read as data: the complete executable backend/helper input set and all
pinned bytes must match, including `governance/railway-control-signers.json` at
the repository root. The image tests load that registry through the production
command validator's default path. Backend tests and the existing dispatch content directory
are excluded from runtime admission. The original strict 26-hour monitor still
runs first with its three existing isolated inputs.

Activation requires `RECOVERY_OPERATION=export-recurring` and
`RECOVERY_CONFIRMATION=EXPORT_WITHOUT_AUTHORITY_CHANGE`. The registered production
recovery key (`RECOVERY_CONTROL_SIGNING_KEY_PEM`) and separate evidence-only key
(`RECOVERY_EXECUTION_SIGNING_KEY_PEM`) are validated before an export can begin.
The evidence key is never registered for production commands. The controller has
no GitHub token, code autodeploy trigger, or public endpoint. Its restore process
runs with the existing separate UID, closed file descriptors, no credentials or
production DSN, immutable input files and a sticky output directory; all processes
must quiesce before the original trusted Object Lock stage resumes.

Use a 7,200-second outer deadline: the original monitor has at most 1,800 seconds,
and export, restore and sealing together retain the original 5,400-second job
budget. Per-stage limits cannot extend that aggregate. One volume-backed lock and
one replica prevent overlapping native jobs. The daily native schedule is intended
for `31 2 * * *`; its thin GitHub tail starts at `46 4 * * *`. Keep the original
GitHub daily exporter active until real native execution and the genuine OIDC tail
have both passed. Manual production controls retain their protected boundaries.

A separate signed daily index links the unchanged original 15 locked members and
three exact receipt/proof versions. The index itself is an additional locked
object, outside that closed manifest. A current-day existing index suppresses a
duplicate export only after bucket policy, strict monitor, fresh runtime, signature,
all receipt versions and original restore counts pass before/after checks. Only an
explicit provider missing-key response can permit an initial export; errors and
conflicts fail closed.

The expected native image digest is supplied after its image is built and reviewed,
then bound independently by an owner-SSH-signed installation receipt. That receipt
binds actual Railway API evidence, the exact deployment/image/source/manifest,
service/project/environment and evidence public key. A changed deployment requires
a new installation receipt. The tail verifies this receipt and queries production
runtime live using the existing production-scoped token; it receives no new
validation-project API credential. The native controller remains the restore
executor, and only GitHub's original attestation action supplies the GitHub OIDC
issuer. An owner-signed installation receipt is installation-time evidence, not a
claim of continuous live image inspection.
