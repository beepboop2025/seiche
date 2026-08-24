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
