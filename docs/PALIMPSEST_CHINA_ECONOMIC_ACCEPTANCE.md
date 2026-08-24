# Palimpsest China economic acceptance

Seiche consumes the Palimpsest China economic export only after a Seiche
operator signs a domain-separated acceptance claim. The signature is an owner
attestation over the exact manifest and artifact hashes; it is not a World Bank
signature and it does not expand the upstream licence.

Manifest v1 remains parseable for offline historical review only. Claim
generation, receipt assembly, installed loading, and public authority all
require manifest v2 with a non-null, successful exact-main push declaration.

The accepted panel remains annual structural context. It cannot enter the
CN-CNY gauge, a market observation, a score, a forecast, or a trade signal.
CFETS and ChinaMoney value rows remain hard denied.

## Offline acceptance

1. Copy the exact Palimpsest manifest and JSONL artifact into a review area.
   Independently inspect the manifest's policy, source decisions, artifact
   receipt, and pinned `series_registry` receipt.
2. Require manifest v2. Its `producer` declaration must name repository
   `beepboop2025/palimpsest`, the exact current-main commit, and a completed
   successful `push` run of `.github/workflows/tests.yml` whose `head_sha`
   equals that commit. The manifest fields are self-declared metadata, not a
   GitHub signature. Independently inspect the GitHub API/run and the workflow's
   bundle attestation before proceeding. Verify the downloaded checksum subject
   against the exact source commit and protected workflow:

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
3. Recompute and compare the exact manifest, artifact, input-ledger, policy,
   and series-registry hashes and byte counts. Confirm that the attested export
   artifact is the bundle under review. A run URL alone does not bind ignored
   `data/review` bytes.
4. Emit the exact claim bytes. Use the current UTC time; future-dated claims
   are rejected.

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli claim \
     palimpsest-china-economic-export-v1-manifest.json \
     palimpsest-china-economic-export-v1.jsonl \
     --accepted-at 2026-08-24T12:02:00Z \
     --signer-key-id "$SEICHE_OPERATOR_PUBLIC_KEY" \
     --confirm-github-run-attestation-verified \
     --confirm-exact-input-hashes-verified > acceptance-claim.json
   ```

5. Sign `acceptance-claim.json` with the offline Ed25519 operator key. Never
   copy the private key to the application host.
6. Assemble the receipt. Production uses the public keys pinned in
   `seiche.nbs_trust`; a protected `--attest-dir` is for non-hosted validation.

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli receipt \
     palimpsest-china-economic-export-v1-manifest.json \
     palimpsest-china-economic-export-v1.jsonl \
     --accepted-at 2026-08-24T12:02:00Z \
     --signer-key-id "$SEICHE_OPERATOR_PUBLIC_KEY" \
     --signature "$DETACHED_SIGNATURE_HEX" \
     --confirm-github-run-attestation-verified \
     --confirm-exact-input-hashes-verified > acceptance.json
   ```

7. Install the three single-link regular files into an operator-controlled,
   read-only location. Configure:

   - `SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH`
   - `SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH`
   - `SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH`

8. Run `verify` before changing service configuration:

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli verify \
     "$SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH" \
     "$SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH" \
     "$SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH"
   ```

Every load rechecks that the signed acceptance clock is not in the future and
that the WDI rights decision has not expired. Exact immutable inputs are
verified once per process and cached by file identity; a changed manifest,
artifact, receipt, or explicit trust policy receives a new cache key and is
fully reverified.

The public REST/MCP projection reports total current coverage but returns only
the six editorially featured observations per money/capital channel. Full
history stays in the accepted offline artifact instead of entering every agent
prompt or anonymous response.
