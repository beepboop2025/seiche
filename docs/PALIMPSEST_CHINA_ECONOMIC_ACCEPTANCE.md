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
   ledger, current WDI availability receipt, policy, series registry, checksum
   subject, and provenance attestation into a protected review area.
   Independently inspect every receipt and source decision.
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
   locator fields inside the manifest are not.
3. Recompute and compare the exact manifest, artifact, input-ledger,
   availability-receipt, policy, and series-registry hashes and byte counts.
   Confirm that the attested checksum subject names those reviewed bytes. A run
   URL alone does not bind ignored `data/review` bytes.
4. Verify the withdrawal boundary. The availability receipt commits the exact
   current numeric identities, withdrawn historic identities, and projectable
   source/internal series sets. Any source series with a previously numeric
   identity now absent or null must be omitted in full; an older numeric row
   cannot become the public "current" fallback. The artifact must contain one
   exact latest ledger row per remaining projectable identity, selected by
   `(revision, released_at, collected_at, observation_id)`.
5. Emit the exact claim bytes. Use the current UTC time; future-dated claims
   are rejected.

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli claim \
     palimpsest-china-economic-export-v1-manifest.json \
     palimpsest-china-economic-export-v1.jsonl \
     --input-ledger china-econ-wdi-observations.jsonl \
     --availability-receipt china-econ-wdi-latest.json \
     --accepted-at 2026-08-24T12:02:00Z \
     --signer-key-id "$SEICHE_OPERATOR_PUBLIC_KEY" \
     --confirm-github-run-attestation-verified \
     --confirm-exact-input-hashes-verified > acceptance-claim.json
   ```

6. Sign `acceptance-claim.json` with the offline Ed25519 operator key. Never
   copy the private key to the application host.
7. Assemble the receipt. Production uses the public keys pinned in
   `seiche.nbs_trust`; a protected `--attest-dir` is for non-hosted validation.
   The live NBS notary key is explicitly outside this trust set. Until a
   dedicated offline Palimpsest owner public key is added by a signed Seiche
   release, production receipt assembly and loading fail closed by design.

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli receipt \
     palimpsest-china-economic-export-v1-manifest.json \
     palimpsest-china-economic-export-v1.jsonl \
     --input-ledger china-econ-wdi-observations.jsonl \
     --availability-receipt china-econ-wdi-latest.json \
     --accepted-at 2026-08-24T12:02:00Z \
     --signer-key-id "$SEICHE_OPERATOR_PUBLIC_KEY" \
     --signature "$DETACHED_SIGNATURE_HEX" \
     --confirm-github-run-attestation-verified \
     --confirm-exact-input-hashes-verified > acceptance.json
   ```

8. Keep the five single-link regular files in the protected operator staging
   area: manifest, artifact, input ledger, availability receipt, and Seiche
   acceptance receipt. Do not copy paths into `market.env` or manually edit the
   API service. Production activation remains blocked until the signed Seiche
   release contains the dedicated installer/rollback wrapper and offline public
   key.

   A non-hosted validation environment may configure:

   - `SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH`
   - `SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH`
   - `SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH`
   - `SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH`
   - `SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH`

9. Run `verify` before any activation transaction:

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli verify \
     "$SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH" \
     "$SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH" \
     "$SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH" \
     --input-ledger "$SEICHE_PALIMPSEST_CHINA_INPUT_LEDGER_PATH" \
     --availability-receipt "$SEICHE_PALIMPSEST_CHINA_AVAILABILITY_PATH"
   ```

Every load rechecks that the signed acceptance clock is not in the future and
that the WDI rights decision has not expired. Exact immutable inputs are
verified once per process and cached by all five file identities; a changed
manifest, artifact, input ledger, availability receipt, acceptance receipt, or
explicit trust policy receives a new cache key and is fully reverified.

The public REST/MCP projection reports total current coverage but returns only
the six editorially featured observations per money/capital channel. Annual
identity history stays in the accepted offline artifact and revision vintages
stay in the exact input ledger instead of entering every agent prompt or
anonymous response.
