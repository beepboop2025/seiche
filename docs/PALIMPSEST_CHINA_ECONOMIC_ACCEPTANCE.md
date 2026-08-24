# Palimpsest China economic acceptance

Seiche consumes the Palimpsest China economic export only after a Seiche
operator signs a domain-separated acceptance claim. The claim directly binds
the exact manifest and artifact hashes; manifest v3 in turn pins the exact
input-ledger and current-availability receipt bytes. The signature is not a
World Bank signature and it does not expand the upstream licence.

Manifest v1 and the already-published exact manifest v2 remain parseable for
offline historical review only. Claim generation, receipt assembly, installed
loading, and public authority all require manifest v3 with a non-null,
successful exact-main push declaration and both supplemental inputs.

The accepted panel remains annual structural context. It cannot enter the
CN-CNY gauge, a market observation, a score, a forecast, or a trade signal.
CFETS and ChinaMoney value rows remain hard denied.

## Offline acceptance

1. Copy the exact Palimpsest manifest, exported JSONL artifact, raw observation
   ledger, current WDI availability receipt, raw `github-commit.json`,
   `handoff-receipt.json`, `SHA256SUMS`,
   `china-econ-wdi-lineage-chain.jsonl`, and
   `github-commit-lineage-evidence.jsonl` into a protected review area. Keep the
   policy, series registry, live-check receipt, live raw response, and GitHub
   provenance attestation beside them for the independent review. Inspect every
   receipt and source decision.
2. Require manifest v3. Its `producer` declaration must name repository
   `beepboop2025/palimpsest`, the exact current-main commit, and a completed
   successful `push` run of `.github/workflows/tests.yml` whose `head_sha`
   equals that commit. The manifest fields are self-declared metadata, not a
   GitHub signature. Independently inspect the GitHub API/run and the workflow's
   bundle attestation before proceeding. Verify the downloaded checksum subject
   against the exact source commit and release-reviewed workflow identity:

   ```bash
   gh attestation verify "$PALIMPSEST_SHA256SUMS_PATH" \
     --repo beepboop2025/palimpsest \
     --signer-workflow beepboop2025/palimpsest/.github/workflows/tests.yml \
     --source-digest "$PALIMPSEST_PRODUCER_SHA" \
     --source-ref refs/heads/main \
     --deny-self-hosted-runners
   ```

   This external build-provenance attestation is the workflow proof. The
   locator fields inside the manifest are not. Reparse the exact raw
   `github-commit.json` response and require `author.login=beepboop2025`,
   `committer.login=web-flow`, at least two unique parents, and GitHub
   `verification.verified=true`, `reason=valid`. Seiche repeats those checks and
   binds the raw file hash into the owner-signed acceptance receipt.
3. Verify the complete governed first-parent history, not only the producer's
   immediate parent. Rebuild the exact chain for the three governed paths from
   detached source history and the retained raw GitHub responses. Every
   path-changing commit must be a reviewed, GitHub-verified multi-parent merge;
   registry evolution must be append-only and ledger bytes must be exact prefix
   extensions. `evaluated_at_commit_sha` must equal the manifest producer SHA.
   `tip_commit_sha` is the newest governed-path-changing commit and can predate
   the producer after unrelated merges. Confirm the chain/evidence root, tip,
   record counts, hashes, Git tree objects, and final governed receipts.
4. Recompute and compare the exact manifest, artifact, input-ledger,
   availability-receipt, policy, series-registry, handoff, lineage, raw commit,
   live-check, and live-response hashes and byte counts. Require the attested
   `SHA256SUMS` to contain the exact twelve sorted subjects and no extras. A run
   URL alone does not bind ignored `data/review` bytes.
5. Verify the withdrawal boundary. The availability receipt commits the exact
   current numeric identities, withdrawn historic identities, and projectable
   source/internal series sets. Any source series with a previously numeric
   identity now absent or null must be omitted in full; an older numeric row
   cannot become the public "current" fallback. The artifact must contain one
   exact latest ledger row per remaining projectable identity, selected by
   `(revision, released_at, collected_at, observation_id)`.
6. Emit the exact claim bytes. Claim preparation is the only online Seiche
   acceptance step: it requires `GH_TOKEN` or `GITHUB_TOKEN`, fetches the exact
   authenticated GitHub REST `branches/main` response, validates that
   `commit.sha` still equals the manifest producer SHA, and creates the new
   `github-main-branch.json` path without overwriting an existing file. Only
   after the complete response body arrives, the command captures the UTC time
   and uses it for both `producer_main_evidence.observed_at` and `accepted_at`.
   A handoff whose producer is no longer current main cannot receive a new
   signature.

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli claim \
     palimpsest-china-economic-export-v3-manifest.json \
     palimpsest-china-economic-export-v1.jsonl \
     --input-ledger china-econ-wdi-observations.jsonl \
     --availability-receipt china-econ-wdi-latest.json \
     --producer-commit-evidence github-commit.json \
     --producer-main-evidence github-main-branch.json \
     --handoff-receipt handoff-receipt.json \
     --checksum-subject SHA256SUMS \
     --lineage-chain china-econ-wdi-lineage-chain.jsonl \
     --lineage-evidence github-commit-lineage-evidence.jsonl \
     --signer-key-id "$SEICHE_OPERATOR_PUBLIC_KEY" \
     --confirm-github-run-attestation-verified \
     --confirm-exact-input-hashes-verified \
     --confirm-producer-raw-identity-verified \
     --confirm-detached-first-parent-lineage-rebuild-verified \
     --confirm-current-main-branch-evidence-verified \
     --confirm-rights-freshness-reviewed > acceptance-claim.json
   ```

7. Move only `acceptance-claim.json` to the offline signer and sign it with the
   offline Ed25519 operator key. Never copy the private key to the claim host or
   application host.
8. Assemble the receipt. Production uses the public keys pinned in
   `seiche.nbs_trust`; a protected `--attest-dir` is for non-hosted validation.
   The live NBS notary key is explicitly outside this trust set. Until a
   dedicated offline Palimpsest owner public key is added by a signed Seiche
   release, production receipt assembly and loading fail closed by design.
   Copy the exact `accepted_at` value from the signed claim into
   `SEICHE_ACCEPTED_AT`; receipt assembly must reproduce that clock exactly.

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli receipt \
     palimpsest-china-economic-export-v3-manifest.json \
     palimpsest-china-economic-export-v1.jsonl \
     --input-ledger china-econ-wdi-observations.jsonl \
     --availability-receipt china-econ-wdi-latest.json \
     --producer-commit-evidence github-commit.json \
     --producer-main-evidence github-main-branch.json \
     --handoff-receipt handoff-receipt.json \
     --checksum-subject SHA256SUMS \
     --lineage-chain china-econ-wdi-lineage-chain.jsonl \
     --lineage-evidence github-commit-lineage-evidence.jsonl \
     --accepted-at "$SEICHE_ACCEPTED_AT" \
     --signer-key-id "$SEICHE_OPERATOR_PUBLIC_KEY" \
     --signature "$DETACHED_SIGNATURE_HEX" \
     --confirm-github-run-attestation-verified \
     --confirm-exact-input-hashes-verified \
     --confirm-producer-raw-identity-verified \
     --confirm-detached-first-parent-lineage-rebuild-verified \
     --confirm-current-main-branch-evidence-verified \
     --confirm-rights-freshness-reviewed > acceptance.json
   ```

   Receipt assembly does not contact GitHub. It rereads all ten exact signed
   input files and must reproduce the signed claim byte for byte. A later
   legitimate Palimpsest main advance does not retroactively invalidate an
   unexpired, already signed bundle.

9. Keep all eleven single-link regular runtime files in the protected operator
   staging area: manifest, artifact, input ledger, availability receipt, raw
   producer commit evidence, raw main-branch evidence, handoff receipt,
   `SHA256SUMS`, lineage chain, raw lineage evidence, and the Seiche acceptance
   receipt. Do not copy paths into `market.env` or manually edit the API
   service. Production activation remains blocked until the signed Seiche
   release contains the dedicated installer/rollback wrapper and offline public
   key.

   A non-hosted validation environment may configure:

   - `SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH`
   - `SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH`
   - `SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH`
   - `SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH`
   - `SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH`
   - `SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH`
   - `SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH`
   - `SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH`
   - `SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH`
   - `SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH`
   - `SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH`

10. Run `verify` before any activation transaction:

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli verify \
     "$SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH" \
     "$SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH" \
     "$SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH" \
     --input-ledger "$SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH" \
     --availability-receipt "$SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH" \
     --producer-commit-evidence \
       "$SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH" \
     --producer-main-evidence \
       "$SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH" \
     --handoff-receipt "$SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH" \
     --checksum-subject "$SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH" \
     --lineage-chain "$SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH" \
     --lineage-evidence "$SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH"
   ```

11. A signed Seiche release installs an inert root launcher at
    `/etc/seiche/libexec/seiche-palimpsest-china-activate.py` and a matching
    root-owned runtime at
    `/opt/seiche-palimpsest-china/releases/<seiche-release-sha>/`. Installation
    does not create the API environment/drop-in and does not confer authority.
    Once the final hosted v3 handoff exists, its acceptance receipt has been
    signed by a dedicated release-pinned offline key, and all eleven source
    files are regular, single-link, root:root mode `0400` or `0600` beneath
    root-owned non-writable traversal, a root operator invokes the launcher in
    this exact order:

    ```bash
    /etc/seiche/libexec/seiche-palimpsest-china-activate.py \
      "$SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_PRODUCER_COMMIT_EVIDENCE_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_PRODUCER_MAIN_EVIDENCE_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_HANDOFF_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_CHECKSUMS_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_LINEAGE_CHAIN_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_LINEAGE_EVIDENCE_PATH" \
      "$SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH"
    ```

    The launcher takes the Palimpsest China transaction lock outside the normal
    deploy, market-backup, and activation locks, rechecks the exact deployed SHA
    and its pinned SSH signature, and installs a root:seiche `0750`
    versioned bundle at `/var/lib/seiche-palimpsest-china/<bundle-id>/` with
    exact single-link root:seiche `0440` files, and verifies it as the
    unprivileged `seiche` user under an empty environment.
    It writes only the dedicated `/etc/seiche/palimpsest-china.env` and API
    drop-in, restarts the API, and requires both REST and MCP to serve all
    eleven exact accepted hashes before committing a fsynced activation receipt
    and atomic active marker. The marker and the REST/MCP economic context are
    explicitly `provisional`; publication of those live bytes is not a
    durability claim. The receipt records separate REST and MCP file-hash and
    signer commitments. A failure before the marker is committed restores and
    re-proves the prior configuration. A failure after marker commit never
    rolls the live tree back: the exact activation remains live-but-provisional
    and only that same activation ID may resume. A different bundle is refused
    until the live one is durable. A fsynced pending marker makes an interrupted
    multi-file switch recoverable on the next locked run. Retained receipts
    reject an older acceptance clock, activation clock, or producer workflow
    run. No path is copied into `market.env`.

    A release upgrading an exact-parent
    `seiche.palimpsest-china-active.v1` marker treats that live activation as
    provisional before doing anything else. REST and MCP add the provisional
    label even while the legacy eleven-path environment is still running.
    Under the transaction, deploy, and activation locks, the launcher accepts
    only the fully validated canonical v1 schema and its already-bound
    immutable receipt, restarts and proves the provisional API, then atomically
    commits v2. The v2 marker embeds the exact canonical v1 bytes, their
    SHA-256, and an equal semantic projection. It retains the historical
    activation ID and release SHA when a later signed Seiche release resumes
    the same exact bundle. This migration never infers owner acceptance or
    durability; malformed or unknown legacy bytes fail unchanged, and the
    migrated activation must still complete restore-v5, scheduled offsite-v4,
    final live audit, and the outside-tree durability seal. That seal records
    the current trusted proof release, which must equal the embedded restore's
    deployed SHA and the scheduled offsite source revision; the marker and its
    immutable activation receipt separately retain the historical publication
    release. A crash before the marker rename leaves v1 provisional and
    retryable only as that same live bundle; a committed v2 is idempotent and
    is never rewritten back to v1.

    After provisional publication, the launcher releases the inner market and
    deploy locks while retaining the transaction lock. It creates one exact
    durability request, then requires a new local backup, isolated restore-v5,
    scheduled offsite-v4 round trip, and a final byte-for-byte live state audit.
    Every proof must name the same activation ID, deployed release, canonical
    activation-state tree digest, and snapshot; the offsite proof also binds the
    downloaded immutable `RECEIPT.json` digest. Only then is a root-owned mode
    `0400` `seiche.palimpsest-china-activation-durability.v1` receipt committed
    outside the backed-up tree under
    `/var/lib/seiche-recovery-proof/palimpsest-china-durability/`. Durability
    deliberately does not rewrite the marker: authority is the conjunction of the
    unchanged provisional marker and that exact outside-tree receipt. The
    receipt embeds the exact activation-time restore-v5 bytes and their digest,
    so that proof remains locally verifiable after the mutable latest-restore
    status advances or the ordinary 21-day snapshot retention removes the
    historical local directory; it also retains the scheduled offsite-v4
    immutable remote receipt key, digest, and verified clock. A retry of the
    same activation reuses an already exact immutable proof or resumes the
    missing backup/offsite stages; any disagreement fails closed. Later signed Seiche
    releases do not relabel unchanged activation data: readiness accepts only
    v5/v4 successor proofs for the same activation ID and canonical tree, with
    null pending state and clocks at or after the sealed proofs. The release
    wrapper refuses to advance at all while the live activation is provisional.
    A total host/volume restore that loses the outside-tree durability receipt
    is intentionally fail-closed: the restored marker is provisional and
    unready until an operator resumes that same activation ID and creates a new
    exact restore, scheduled immutable offsite proof, final audit, and local
    seal. A different activation or release cannot bypass that replay.

Every load rechecks that the signed acceptance clock is not in the future and
that the WDI rights decision has not expired. Exact immutable inputs are
verified once per process and cached by all eleven runtime file identities; a
changed manifest, artifact, input ledger, availability receipt, commit evidence,
main-branch evidence, handoff, checksum subject, lineage chain, lineage raw
evidence, acceptance receipt, or explicit trust policy receives a new cache key
and is fully reverified.

The public REST/MCP projection reports total current coverage but returns only
the six editorially featured observations per money/capital channel. Annual
identity history stays in the accepted offline artifact and revision vintages
stay in the exact input ledger instead of entering every agent prompt or
anonymous response.

The intake accepts at most 512 distinct series. That ceiling is independent of
the 64 MiB artifact, 128 MiB durable-ledger, and 100,000-record limits; all four
bounds apply. Public REST/MCP responses stay compact even at the series ceiling.
