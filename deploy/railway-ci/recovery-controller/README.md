# Railway recovery execution

The signed controller admits at most 6 GiB of immutable recovery objects, with
the existing additional 32 MiB allowance for recurring workspace proofs. Both
historical verification and recurring restoration require five times the actual
input size plus 1 GiB of free scratch space on the selected filesystem. The
larger ceiling accommodates observed snapshot growth beyond the former 3 GiB
limit; it does not increase provider resources or extend job deadlines. Object
versions, content hashes, signatures, retention and isolated restore checks are
unchanged. A replacement image must complete the full actual-image qualification
and signed installation procedure before use.

Native continuity probes retain the original 15-second total deadline per
endpoint. Connection setup has a three-second limit and may be attempted up to
three times. Each response can use the remaining part of the original deadline;
an established connection is not abandoned at five seconds. HTTP failures,
redirects, TLS errors, malformed
responses and responses arriving after the deadline fail immediately. The
original continuity failure marker still prevents acceptance. Retry/failure
diagnostics identify the endpoint, timings and curl status without credentials.
The assembler requires the exact original probe commands before substituting
this transport; upstream workflow drift is rejected.

Recurring assembly reads receipt-readiness and total-job timing from the signed
current recovery workflow and pins that workflow in runtime source admission.
The reviewed limits are 75 minutes for receipt readiness and 120 minutes for the
whole export job. The original export adapter receives that readiness value;
its former 45-minute parent-process cutoff is removed. Preflight, download,
restore and sealing share one decreasing job budget. A new stage never resets
that deadline, and no integrity, continuity or freshness check is skipped.
The recurring service must retain its explicit 7,200-second outer command;
the Docker default remains the historical read-only qualification command.

The existing pinned Python and PostgreSQL image manifests are fetched from
Docker's ECR Public publisher. Their manifest bytes were verified against the
same Docker Hub digests; base versions and restore behavior are unchanged.

After an export resumes collection, the final runtime check may wait up to
three minutes within the existing aggregate export deadline for a faulted
snapshot to recover. Each attempt uses the original origin and public health
validator. The accepted deployment, source and replica must still match the
pre-export identity. Persistent faults, stale evidence and identity errors
prevent execution sealing; this wait never repeats the export or upload.

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
The recurring workspace explicitly grants directory traversal after creation
under the private umask. For a recurring assembly, `verify-existing` restores the
previously attested backup through that same workspace and `restore-native.sh`.
It preserves the downloaded original proof and retains the new restore proof
separately, without requesting production work or writing any S3 object.

`prepare.py` verifies the owner-signed controller commit and both original
GitHub receipt attestations. Controller files and the original backend/helpers
are copied from immutable Git objects. The resulting image carries their hash
manifest and the reviewed storage destination. The 15 original bundle members,
receipt version, sizes and hashes are pinned by the original attested receipt.
Original SSE-C, COMPLIANCE retention and downloaded-content checks remain active.

The historical case does not expire after 26 hours: it is checked with
`require_fresh=False`. It still rejects future or inverted sealing times and
insufficient retention. Each actual object must retain at least 29 days of
COMPLIANCE protection at verification time. Current execution indexes and the
daily monitor retain their original 26-hour freshness requirement.

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
restored databases are temporary. The historical `verify-existing` operation has
no cron. The recurring operation described below now performs daily export,
restore, Object Lock publication and governed acknowledgment on Railway; the
original GitHub workflow supplies only the scheduled OIDC tail. Its explicit
manual recovery operations remain available as a protected fallback.

Rollback requires no production action: stop this independent verifier. Existing
production recovery policy, objects, keys and original workflow remain available.


## Recurring execution and genuine attestation tail

`prepare.py --recurring-source <full SHA> --production-target <private JSON>
--execution-public-key <public hex file>` adds the original governed export,
restore, and Object Lock scripts from `fff0eabb26088292451edaef10bdd203c074a992`.
Execution-variable names, private authentication-header transport and the
explicitly reviewed receipt-readiness duration differ as described above.
GitHub's mask directive is replaced by a private bearer header file, so native
logs and child arguments never carry the download bearer. Current
main is read as data: the complete executable backend/helper input set and all
pinned bytes must match, including `governance/railway-control-signers.json` at
the repository root. The image tests load that registry through the production
command validator's default path. Backend tests and the existing dispatch content directory
are excluded from runtime admission. The original strict 26-hour monitor still
runs first with its three existing isolated inputs.

When rebuilding the controller while the accepted application remains on an
older release, pass `--application-source <full SHA>` alongside
`--recurring-source`. The monitor verifies that this runtime commit is signed by
the established owner and is an ancestor of the selected verifier revision.
Its health checks remain pinned to that runtime; helper bytes and workflow
semantics remain pinned to the separately selected verifier. The assembled
policy records `runtime_source` without changing the historical qualification
case's `application_source`. Reproduction must supply the same option. Omitting
it preserves the existing single-revision behavior. The signed installation,
strict freshness checks and original export/restore gates remain required.

A replacement can be assembled with `--export-not-before YYYY-MM-DD`. This UTC
date is part of the image manifest and therefore bound by the signed installation.
It may defer the first export by at most one day. Before that date, installation,
signer, source and strict monitor checks still run, then the controller records
`RAILWAY_NATIVE_RECOVERY_READY_NO_EXPORT` and exits without accessing storage or
requesting an export. This is readiness evidence only, never a recovery receipt
or proof of recurrence. From the named date onward, all original storage, index,
export, restore and sealing gates apply. The guard does not authorize replacing
another installation's immutable daily index.

Activation requires `RECOVERY_OPERATION=export-recurring` and
`RECOVERY_CONFIRMATION=EXPORT_WITHOUT_AUTHORITY_CHANGE`. Before taking a lock or
creating export evidence, the versioned installation gate requires owner-signed
`installation.json` and `policy.json`, with their `.sig` files, under
`/evidence/native/admissions/<actual deployment ID>/`. The installation uses the
existing `seiche-railway-recovery-installation-v1` SSH namespace; the tail policy
uses `seiche-railway-recovery-tail-policy-v1`. Both documents must be canonical
and match the actual deployment, configured image digest, project, environment,
service, source, image manifest, evidence key and reviewed tail inputs and targets.
The volume supplies signed data only; admission code remains part of the image.
The operator must verify the protected tail configuration and publish the complete
admission directory atomically. Absent data waits for at most 1,800 seconds;
present invalid data fails immediately. Read-only `verify-existing` qualification
does not enter this gate. The registered production
recovery key (`RECOVERY_CONTROL_SIGNING_KEY_PEM`) and separate evidence-only key
(`RECOVERY_EXECUTION_SIGNING_KEY_PEM`) are validated before an export can begin.
The evidence key is never registered for production commands. The controller has
no GitHub token, code autodeploy trigger, or public endpoint. Its restore process
runs with the existing separate UID, closed file descriptors, no credentials or
production DSN, immutable input files and a sticky output directory; all processes
must quiesce before the original trusted Object Lock stage resumes.

Use a 7,200-second outer deadline, including any installation wait. The original
monitor has at most 1,800 seconds,
and export, restore and sealing together retain the original 5,400-second job
budget. Per-stage limits and admission waits cannot extend the outer deadline;
they cannot all consume their nominal maxima in one execution. One volume-backed lock and
one replica prevent overlapping native jobs. The daily native schedule is
`31 2 * * *`; its thin GitHub tail starts at `46 4 * * *`, after the two-hour
monitor/export window plus 15 minutes. GitHub no longer schedules the original
monitor/export jobs. Explicit `monitor`, `export-recovery`, `resume-offsite`,
`preflight-offsite` and `configure-native-backups` remain unchanged manual fallbacks
with their original protected credentials, confirmations and production gates.
The tail can also be dispatched as `attest-native-recovery` with confirmation
`ATTEST_TODAYS_REVIEWED_NATIVE_RECOVERY`. Its first step requires the protected
`RECOVERY_NATIVE_TAIL_ENABLED=1` before checkout or secret inputs. The completed
encrypted migration handoff is removed. No manual fallback runs automatically
when the native controller or attestation fails.

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

## Scoped source-health admission

Normal recovery health remains strict. An owner-signed admission may qualify the
exact reviewed `HTTP_ERROR` / `FAILED` source faults for one operation, for at most
four hours. It does not grant source access, suppress the recorded faults, change
snapshot clocks, or authorize an export. All existing provider identity, backup,
PITR, storage, confirmation, signed-command and restore checks still apply.

The shared closed contract is `ops/release/source_degradation.py`. The
`source_health_admission` workflow input is one JSON object with exactly
`intent_id` (64 lowercase hex), `body_b64` (canonical admission bytes), and
`signature_b64` (owner SSH signature in `seiche-source-degradation-v1`). The
workflow reconstructs source, target, deployment and operation context. The
signed body supplies the owner's expected version constraint, never a value
inferred from a live response. Monitor-only checks record the observed run and
assessment without acquiring storage credentials or consuming an intent.

For a manual export, dispatch does not yet know the GitHub run ID. The operator
must first persist an atomic, durable local binding from admission digest and
intent to the observed run ID and attempt. Reuse is allowed only for that exact
operation while the admission is valid. Only after that binding succeeds may the
operator sign `claim_document(assessment, execution=...)`, where execution is:

```json
{"kind":"github_actions","repository":"beepboop2025/seiche","run_id":"123","run_attempt":"1"}
```

Sign those canonical claim bytes with the established owner key in the distinct
SSH namespace `seiche-source-degradation-execution-claim-v1`. Upload this canonical
object (including the trailing newline) into the existing protected SSE-C,
versioned COMPLIANCE store:

```json
{"schema":"seiche.source-degradation-execution-envelope.v1","claim_b64":"...","signature_b64":"..."}
```

The exact object key is
`S3_PREFIX/source-health-claims/INTENT/github-actions/RUN_ID/RUN_ATTEMPT/claim.json`.
Its `sha256` metadata must hash the complete envelope bytes. The workflow performs
only HEAD and version-pinned GET on this object. It waits at most 600 seconds only
for an explicit missing-object response, validates retention/encryption, owner
signature, admission digest, context, actual run/attempt and expiry, then observes
both health edges again before the original export command. Invalid objects,
access denial and uncertain transport outcomes fail closed. It never substitutes
HEAD followed by PUT for atomic intent binding. This matters because the current
Hetzner store does not support conditional writes on versioned buckets.

Manual evidence is retained as `source-health-assessment.json` in the monitor
artifact and `proof/source-health-claim.json` in the export artifact. The original
export request ID and command schema are unchanged.

Native admission is delivered after the image build inside the owner-signed tail
`policy.json`, avoiding a manifest/signature cycle. Add all four fields together:
`source_health_admission`, `application_source_sha`, `application_deployment_id`,
and `application_expected_version`. Its context binds the actual controller
source, deployment, manifest digest and exact UTC execution day, plus the reviewed
application source/deployment/target. The installation gate captures and verifies
these bytes before the daily lock. The monitor and pre/post-export checks retain
the degraded assessment; expiry/day are revalidated immediately before export.
The existing execution lock and immutable daily index remain in force. No
admission is an assertion that a later natural recurrence has succeeded.
