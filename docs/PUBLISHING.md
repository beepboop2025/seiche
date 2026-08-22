# Publishing Seiche release surfaces

Everything in the repo is ready (`server.json`, PyPI ownership proof in
`backend/README.md`). What remains needs credentials or a live deploy, so it's
listed here as a runbook rather than automated blind.

## Release prerequisites

1. **Pin the release signer outside the release commit.** Configure the
   repository variable `RELEASE_SIGNING_KEY_FINGERPRINT` to the sole reviewed
   key fingerprint before running any publisher:
   ```bash
   expected="$(ssh-keygen -E sha256 -lf ops/deploy/release-allowed-signers | awk '{print $2}')"
   test "$expected" = "SHA256:yhoa/PIDMM6M/ZennILp8jtRJy5pArncJRARbQssTMI"
   gh variable set RELEASE_SIGNING_KEY_FINGERPRINT \
     --repo beepboop2025/seiche --body "$expected"
   ```
   The file inside the tag supplies Git's allowed-signers syntax; the GitHub
   variable is the independent trust root that prevents a candidate commit
   from approving its own replacement key.

2. **Deploy the exact signed commit.** Fast-forward the reviewed SSH-signed
   release commit to `main`, then wait for the host release poller to record the
   same 40-character SHA as active. The hosted `deploy-hetzner` workflow is a
   disabled manual recovery path, not an automatic controller. Confirm the
   production MCP endpoint only after the exact-SHA deploy and data-readiness
   receipts are green:
   ```bash
   curl -sX POST https://api.seiche.info/mcp -H 'content-type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
   ```
3. **Create and push the annotated SSH-signed version tag.** A tag push starts
   `publish-pypi.yml`. Four separate runners enforce the boundary: an
   unprivileged build produces reproducible candidates; an independent verifier
   re-authenticates the tag, main ancestry, and external signer fingerprint and
   compares the candidates with a third pristine Git archive; a permissionless
   smoke runner executes only a disposable download; and the environment-gated
   OIDC runner downloads a fresh copy of the immutable verified artifact,
   rechecks canonical names and hashes without importing package code, and
   publishes it. Package execution therefore cannot mutate the published copy.
   Recovery dispatch is bound to that same immutable tag:
   ```bash
   gh workflow run publish-pypi.yml \
     --repo beepboop2025/seiche \
     --ref v0.11.0 \
     -f release_tag=v0.11.0
   ```
   Do not run a local token-backed `twine upload`: PyPI versions are immutable,
   and bypassing the signed-tag/OIDC gate would sever the artifact-to-commit
   receipt. Restore the trusted workflow instead.

   Seiche keeps the package version identical across `backend/pyproject.toml`,
   `server.json`, runtime discovery, and the AI catalog. Publish that exact
   package before the immutable MCP Registry entry; do not advertise a package
   version that PyPI cannot resolve.
   The `mcp-name: io.github.beepboop2025/seiche` line in `backend/README.md`
   ships in the package description — that's how the registry proves you own it.

4. **Wait for the immutable PyPI receipt.** Do not create the GitHub Release
   while the tag-triggered PyPI workflow is queued or failing. Watch that run to
   completion, then confirm PyPI exposes exactly one wheel and one source archive
   for `0.11.0` and retain their server-reported SHA-256 digests:
   ```bash
   gh run list --repo beepboop2025/seiche \
     --workflow publish-pypi.yml --event push --limit 10
   gh run watch RUN_ID --repo beepboop2025/seiche --exit-status
   curl --fail --show-error --silent \
     https://pypi.org/pypi/seiche/0.11.0/json |
     jq -r '.urls[] | [.filename, .digests.sha256] | @tsv'
   ```
   The workflow already verifies the immutable PyPI bytes against the signed
   tag. The owner receipt records the successful run URL, filenames, and both
   digests; a merely existing project page is insufficient.

5. **Confirm the Zenodo GitHub integration is enabled before releasing.** Sign
   in at <https://zenodo.org/account/settings/github/>, synchronize repositories,
   and verify that `beepboop2025/seiche` is toggled **On**. Zenodo archives new
   GitHub releases only after repository enablement. The repository's
   [`.zenodo.json` follows Zenodo's documented GitHub authoring format](https://help.zenodo.org/docs/github/describe-software/zenodo-json/) and
   explicitly carries version `0.11.0`, language `eng`, `upload_type`, license,
   access, creators, related identifiers, and the research boundary. Because
   Zenodo ignores `CITATION.cff` whenever `.zenodo.json` is present, all required
   release metadata must remain complete in this file. CI pins and checks the
   current Zenodo-RDM GitHub adapter that translates these legacy authoring
   fields into an RDM deposit; the old internal `legacyrecord.json` deposit
   schema is not the raw `.zenodo.json` authoring contract.
   Confirm that no release exists yet:
   ```bash
   if gh release view v0.11.0 --repo beepboop2025/seiche >/dev/null 2>&1; then
     echo "v0.11.0 already released; inspect receipts instead of recreating it" >&2
     exit 1
   fi
   ```

6. **Create one GitHub Release from the verified tag.** This is the event that
   starts Zenodo archival plus the MCP and GHCR publishers. It must happen only
   after steps 4 and 5 are green:
   ```bash
   gh release create v0.11.0 \
     --repo beepboop2025/seiche \
     --verify-tag \
     --title "Seiche 0.11.0" \
     --generate-notes
   ```
   Do not delete and recreate the release as a retry mechanism. Use the
   tag-bound workflow recovery commands below so every receipt retains one
   release identity.

7. **Receipt the three release-triggered surfaces.** Treat these as separate
   outcomes even though the GitHub Release starts them concurrently:

   - **Zenodo:** wait for the integration to archive `v0.11.0`; open the record,
     confirm version `0.11.0`, software type, open access, creator, license, and
     GitHub relationship, then record the version DOI, concept DOI, record URL,
     and archive checksum. Do not claim a DOI while only `.zenodo.json` exists.
   - **MCP Registry:** watch `publish-mcp.yml`, then query the owner namespace and
     retain the workflow URL plus the returned `0.11.0` record. Recovery must use
     the tag as both workflow ref and explicit input:
     ```bash
     gh workflow run publish-mcp.yml --repo beepboop2025/seiche \
       --ref v0.11.0 -f release_tag=v0.11.0
     curl --fail --show-error --silent \
       'https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fseiche/versions/latest'
     ```
   - **GHCR:** the first package version is private by default. Change
     `ghcr.io/beepboop2025/seiche` to **Public**, then rerun only the failed
     `prove-public` job in the original exact-tag workflow run. Do not dispatch
     another build or repeat publication and attestations:
     ```bash
     gh run rerun WORKFLOW_RUN_ID --repo beepboop2025/seiche --failed
     anonymous_config="$(mktemp -d)"
     for platform in linux/amd64 linux/arm64; do
       DOCKER_CONFIG="$anonymous_config" docker pull --platform "$platform" \
         ghcr.io/beepboop2025/seiche@sha256:INDEX_DIGEST
     done
     ```
     The workflow itself anonymously verifies the root provenance and both
     child-manifest CycloneDX attestations from GHCR with exact signer-workflow,
     source-ref, and source-digest constraints. Retain the index and child
     digests, all three attestation results, successful workflow URL, and both
     anonymous digest-pull results. An authenticated owner pull is not a public
     availability receipt. `publish-container.yml` must remain the exclusive
     package writer because GHCR has no atomic compare-and-swap update for tags.

   Add only durable public URLs and immutable identities to
   `distribution/submissions.csv`; keep a surface `prepared` or `pending` until
   its own checks above pass.

8. **Publish and submit the OpenBB extension.** Configure the PyPI pending or
   trusted publisher for project `openbb-seiche`, workflow
   `publish-openbb.yml`, and environment `pypi-openbb`. Invoke the workflow
   explicitly with both independently versioned identities. Its unprivileged
   build job owns the version gate, per-version concurrency lock, exact PyPI
   existence preflight, and reproducibility proof. Independent verification,
   permissionless smoke execution, and minimal OIDC publication then use the
   same four-runner boundary as the main package:
   ```bash
   gh workflow run publish-openbb.yml \
     --repo beepboop2025/seiche \
     --ref v0.11.0 \
     -f release_tag=v0.11.0 \
     -f openbb_version=0.1.0
   ```
   After the immutable PyPI page and clean-install receipt are public, use
   [`integrations/openbb/SUBMISSION.md`](../integrations/openbb/SUBMISSION.md)
   to open the prepared provider-list pull request against OpenBB's docs. Do not
   describe the extension as OpenBB-listed until that pull request is merged.

## 1. Official MCP registry (registry.modelcontextprotocol.io)

This is Seiche's primary owner-published namespace. Many downstream catalogues
ingest it, while curated and direct-submission directories retain their own
review and ownership state.

```bash
# install the publisher CLI
brew install mcp-publisher            # or the prebuilt binary from the registry releases

# validate the manifest without publishing
curl --fail --show-error --silent \
  -H 'content-type: application/json' \
  --data-binary @server.json \
  https://registry.modelcontextprotocol.io/v0.1/validate

# authenticate to the io.github.beepboop2025/* namespace (opens a browser)
mcp-publisher login github

# publish
mcp-publisher publish
```

`server.json` already declares both a **PyPI package** (stdio) and the **remote
HTTP endpoint**. If the remote entry is rejected for the GitHub namespace, the
alternative is the DNS-verified `info.seiche` namespace (you own seiche.info and
have the Cloudflare API token): add a TXT record and
`mcp-publisher login dns --domain seiche.info …`. See the runbook comments.

The normal path is `.github/workflows/publish-mcp.yml`: a GitHub Release starts
it after PyPI, and its manual dispatch is the only recovery path. Both routes
verify immutable PyPI wheel and sdist bytes against a pristine archive of the
externally authenticated tag. That job transfers only a hash-bound
`server.json`; the environment-gated OIDC job has no source checkout and runs
only the checksum-pinned publisher. The browser-login commands above are an
owner-controlled last resort, not an equivalent receipt.

## 2. Aggregator registries (submit after the official listing)

Most of these pull from the official registry automatically; a few take direct
submissions. Use the dated, receipt-backed
[`distribution/MCP_DIRECTORIES.md`](../distribution/MCP_DIRECTORIES.md) inventory
as the only directory runbook. It records which entries already exist, which
must be claimed or refreshed, and the current reviewed submission mechanism;
do not duplicate that time-sensitive table here.

Add the topics `mcp`, `model-context-protocol`, `mcp-server` to the GitHub repo
so the crawlers find it.

## 3. Client-native distribution

- **Claude Code plugin** — a one-line `claude mcp add` in the README already
  works; a plugin entry makes it one click.
- **ChatGPT / Codex connector** — the remote `https://api.seiche.info/mcp`
  endpoint is the connector URL.

## Operations that still need an owner

1. Land and receipt the exact signed hosted runtime before advertising its version.
2. Push the signed tag, wait for the immutable PyPI receipt, and confirm the
   Zenodo repository toggle before creating the single GitHub Release.
3. Preserve separate Zenodo DOI, MCP Registry, and anonymous GHCR receipts.
4. Use browser OAuth only if the OIDC workflow is unavailable.
5. Submit to any aggregator that does not ingest the official registry.
6. Publish `openbb-seiche` through OIDC and retain both the PyPI and accepted
   OpenBB documentation receipts.
