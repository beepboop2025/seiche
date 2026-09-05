# Distribution, catalogs, and container trust

Seiche publishes one version identity across PyPI, the official MCP Registry,
scientific metadata, and the GHCR container. The repository contracts reject a
release when those surfaces drift.

The repository front door lists every supported surface in
[`README.md`](../README.md#use-seiche-everywhere). Public publication state is
tracked separately in [`distribution/submissions.csv`](../distribution/submissions.csv):
only rows with a durable public receipt may be marked `listed`. Local clients,
notebooks, catalog projections, and submission metadata can be complete and
tested while their external status honestly remains `prepared`.

## Run the container

```bash
docker compose up --build
```

The local service is available at `http://127.0.0.1:8787`. Set
`SEICHE_PORT` to choose another host port. The distroless container runs as UID
and GID `65532`, has no shell or package manager, drops Linux capabilities,
enables `no-new-privileges`, and mounts only `/app/backend/data` as writable
persistent storage. It does not contain or require embedded credentials.

The image healthcheck probes `/api`, which is process liveness and discovery.
Data availability is a separate research contract at `/api/health`; a stale or
still-building evidence snapshot must remain visible without causing the
container supervisor to restart a healthy process.

The Compose profile uses container mode so an image built without a Git checkout
remains self-contained. Data is assembled lazily when a data endpoint is
requested. Production operators should preserve Seiche's external release and
data-readiness gates rather than treating this local Compose profile as a
drop-in replacement for the hosted deployment.

## Verify a published image

Published images use these tags:

- the release version, such as `0.12.1`;
- the Git tag, such as `v0.12.1`;
- the first 12 hexadecimal characters of the source commit, prefixed with
  `sha-`;
- `latest` for a non-prerelease GitHub Release.

Before moving `latest`, the publisher hashes and parses the current index and
refuses to replace a higher bare-semantic version with an older release.

Each GHCR publication is multi-platform for `linux/amd64` and `linux/arm64`.
BuildKit exports each platform exactly once as an OCI archive. A source-free job
validates and scans those exact child manifests, and another source-free job
imports the same archive hashes, constructs the two-child OCI index locally, and
copies only that graph to GHCR. Trivy emits one CycloneDX SBOM per platform;
GitHub attests each SBOM to its child-manifest digest and attests provenance to
the final index digest. `publish-container.yml` is the sole authorized writer
for the package's fixed tags; external writers would defeat its best-effort
no-clobber preflight because GHCR does not offer a compare-and-swap tag update.

GitHub creates a newly published container package as private, so the owner must
change the package visibility to **Public** after the first push. Only the final
`prove-public` job performs anonymous reads from an empty Docker configuration.
After changing visibility, rerun the failed job for the same workflow run; do
not dispatch a new build or repeat publication and attestations.

```bash
ANONYMOUS_DOCKER_CONFIG="$(mktemp -d)"
IMAGE=ghcr.io/beepboop2025/seiche
ROOT_DIGEST=sha256:<index-digest>
AMD64_DIGEST=sha256:<amd64-child-digest>
ARM64_DIGEST=sha256:<arm64-child-digest>
RELEASE_TAG=v0.12.1
SOURCE_SHA=<40-hex-signed-commit>
for platform in linux/amd64 linux/arm64; do
  DOCKER_CONFIG="$ANONYMOUS_DOCKER_CONFIG" docker pull \
    --platform "$platform" "$IMAGE@$ROOT_DIGEST"
done
COMMON_ATTESTATION_POLICY=(
  --bundle-from-oci
  --repo beepboop2025/seiche
  --signer-workflow beepboop2025/seiche/.github/workflows/publish-container.yml
  --source-ref "refs/tags/$RELEASE_TAG"
  --source-digest "$SOURCE_SHA"
)
env -u GH_TOKEN -u GITHUB_TOKEN DOCKER_CONFIG="$ANONYMOUS_DOCKER_CONFIG" \
  gh attestation verify "oci://$IMAGE@$ROOT_DIGEST" \
    "${COMMON_ATTESTATION_POLICY[@]}"
for child in "$AMD64_DIGEST" "$ARM64_DIGEST"; do
  env -u GH_TOKEN -u GITHUB_TOKEN DOCKER_CONFIG="$ANONYMOUS_DOCKER_CONFIG" \
    gh attestation verify "oci://$IMAGE@$child" \
      "${COMMON_ATTESTATION_POLICY[@]}" \
      --predicate-type https://cyclonedx.org/bom
done
gh run rerun <workflow-run-id> --repo beepboop2025/seiche --failed
```

Record both anonymous platform pulls, the index and child-manifest digests, and
all three attestation results before changing the GHCR ledger row from
`prepared`. Use the index digest, not a mutable tag, when promoting an image into
a controlled environment.

## Release invariants

The distribution contract checks all of the following before publication:

- the package, MCP listing, citation, Zenodo, CodeMeta, and OCI version fields
  agree;
- citation and catalog metadata use the canonical repository, license, release
  date, versioned tag URL, and research boundary; these static files do not
  embed a self-referential commit SHA, so a release PR can pass before its tag
  exists;
- every external GitHub Action is pinned to a full commit;
- PyPI candidates are built without OIDC authority, independently verified
  against a third pristine signed-source archive, and executed only on a
  separate permissionless smoke runner; the environment-gated publishing jobs
  download a fresh immutable verified artifact, recheck canonical filenames
  and SHA-256 identities without executing package code, and invoke trusted
  publishing;
- every publishing path requires an annotated SSH-signed version tag, an
  SSH-signed commit by the pinned release author, and the repository's reviewed
  allowed-signers policy whose sole fingerprint matches the out-of-repository
  `RELEASE_SIGNING_KEY_FINGERPRINT` variable, and proof that the tagged commit
  is an ancestor of current `origin/main` before it receives authority;
- base container images are pinned to multi-platform manifest digests;
- the image stays non-root, has a healthcheck, and carries OCI source,
  revision, license, and version labels;
- the distroless runtime has no shell or package manager, and both CI and the
  release preflight reject high or critical operating-system and library
  vulnerabilities;
- the Docker context is an allowlist containing only build inputs;
- listing claims, rights-reviewed dataset metadata and hashes, research notebook
  code cells, and the offline Python, JavaScript, and R client contracts pass
  independently; the OpenBB provider uses standardized PEP 621/639 metadata,
  an exact Poetry Core backend pin, independent reproducibility builds, an
  allowlisted artifact verifier, and clean wheel and sdist install gates;
- the PyPI wheel and source archive are present, not yanked, hash-correct, and
  exact-member allowlisted; each public URL's Warehouse `packages/` path is
  bound to its `blake2b_256` digest while SHA-256 remains the artifact receipt;
  artifacts are PEP 639 license-complete, RECORD-valid, and
  byte-for-byte identical to the release checkout for every shipped Python
  source file and the one included VCS ignore file;
- two independently timestamp-perturbed source trees produce byte-identical
  wheels and source archives under the pinned Python/build-backend closure,
  with the signed commit time carried as `SOURCE_DATE_EPOCH` and verified in
  the ZIP, gzip, and tar metadata before PyPI publication; the same reusable
  verifier runs again over immutable PyPI bytes before MCP publication.

PyPI is immutable. Creating a GitHub Release therefore verifies the existing
PyPI artifacts and then publishes the MCP Registry record. It never attempts to
upload the same package version a second time.

The independently versioned `openbb-seiche` package has a separate, manually
invoked `publish-openbb.yml` trusted-publishing path. Its explicit OpenBB-version
input binds 0.1.0 artifacts to the selected signed Seiche commit without
pretending the two packages share a version number. A per-version concurrency
lock and exact PyPI existence preflight reject attempts to retry 0.1.0 on future
Seiche releases. Its build, independent exact verifier, permissionless smoke,
and OIDC-only publisher are separate runners. The OpenBB listing remains
receipt-gated until the PyPI version is public and OpenBB accepts the prepared
`awesome-openbb` ecosystem-list pull request. A merge is a community-listing
receipt, not an OpenBB endorsement of Seiche or its financial claims.

Seiche is research software and not investment advice. Publications should cite
the primary data providers for any underlying observations as well as the
Seiche software release.

## Audit mandatory public receipts

Run the dependency-free, anonymous auditor after publication or whenever the
distribution board needs a current answer:

```bash
python3 -I -S ops/release/audit_distribution_receipts.py
```

The auditor compares the repository's bare semantic version and exact
`server.json` identity with the latest PyPI project record and latest active
official MCP Registry record. The Registry receipt must explicitly be active
and latest, include valid timezone-aware RFC 3339 `statusChangedAt` and
`publishedAt` clocks, and carry a valid `updatedAt` clock when that optional
field is present. Server-card comparison permits the Registry to omit false
defaults only at the MCP schema's exact input and argument paths; identically
named false fields elsewhere remain part of the exact identity. Both surfaces
are mandatory, and a missing, stale, yanked, malformed, or divergent receipt
fails the command.

The network reader is anonymous and GET-only. It ignores process proxy
configuration, accepts only the two literal HTTPS receipt endpoints, refuses
all redirects, and requires both HTTP 200 and exact final-URL equality. Responses
must be identity-encoded JSON and are read through a 2 MiB cap. The command's
timeout is the standard-library timeout for each blocking socket operation, not
a total-transfer deadline. Bounded HTTP error excerpts containing Markdown code
fences are omitted before the JSON report can be embedded in an Actions summary.
The scheduled
[`audit-distribution-receipts.yml`](../.github/workflows/audit-distribution-receipts.yml)
job supplies the independent five-minute overall wall-clock bound and reports
the same JSON contract in the Actions summary.

Optional third-party directories are deliberately excluded because their
independent indexing lag must not make a Seiche release unhealthy. This audit
also never reads or changes `distribution/submissions.csv`; that evidence ledger
remains an explicit operator-reviewed record of durable public receipts.
