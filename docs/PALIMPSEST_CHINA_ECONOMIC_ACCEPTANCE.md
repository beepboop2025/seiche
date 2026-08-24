# Palimpsest China economic acceptance

Seiche consumes the Palimpsest China economic export only after a Seiche
operator signs a domain-separated acceptance claim. The signature is an owner
attestation over the exact manifest and artifact hashes; it is not a World Bank
signature and it does not expand the upstream licence.

The accepted panel remains annual structural context. It cannot enter the
CN-CNY gauge, a market observation, a score, a forecast, or a trade signal.
CFETS and ChinaMoney value rows remain hard denied.

## Offline acceptance

1. Copy the exact Palimpsest manifest and JSONL artifact into a review area.
   Independently inspect the manifest's policy, source decisions, artifact
   receipt, and pinned `series_registry` receipt.
2. Emit the exact claim bytes. Use the current UTC time; future-dated claims
   are rejected.

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli claim \
     palimpsest-china-economic-export-v1-manifest.json \
     palimpsest-china-economic-export-v1.jsonl \
     --accepted-at 2026-08-24T12:02:00Z \
     --signer-key-id "$SEICHE_OPERATOR_PUBLIC_KEY" > acceptance-claim.json
   ```

3. Sign `acceptance-claim.json` with the offline Ed25519 operator key. Never
   copy the private key to the application host.
4. Assemble the receipt. Production uses the public keys pinned in
   `seiche.nbs_trust`; a protected `--attest-dir` is for non-hosted validation.

   ```bash
   python -m seiche.palimpsest_china_acceptance_cli receipt \
     palimpsest-china-economic-export-v1-manifest.json \
     palimpsest-china-economic-export-v1.jsonl \
     --accepted-at 2026-08-24T12:02:00Z \
     --signer-key-id "$SEICHE_OPERATOR_PUBLIC_KEY" \
     --signature "$DETACHED_SIGNATURE_HEX" > acceptance.json
   ```

5. Install the three single-link regular files into an operator-controlled,
   read-only location. Configure:

   - `SEICHE_PALIMPSEST_CHINA_MANIFEST_PATH`
   - `SEICHE_PALIMPSEST_CHINA_ARTIFACT_PATH`
   - `SEICHE_PALIMPSEST_CHINA_ACCEPTANCE_PATH`

6. Run `verify` before changing service configuration:

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
