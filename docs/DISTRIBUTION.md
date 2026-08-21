# Distribution, catalogs, and container trust

Seiche publishes one version identity across PyPI, the official MCP Registry,
scientific metadata, and the GHCR container. The repository contracts reject a
release when those surfaces drift.

The repository front door lists every supported surface in
[`README.md`](../README.md#use-seiche-everywhere). Public publication state is
tracked separately in [`distribution/submissions.csv`](../distribution/submissions.csv):
only rows with a durable public receipt may be marked `verified`. Local clients,
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

- the release version, such as `0.10.1`;
- the Git tag, such as `v0.10.1`;
- the source commit, prefixed with `sha-`;
- `latest` for a non-prerelease GitHub Release.

Each GHCR publication is multi-platform for `linux/amd64` and `linux/arm64`.
BuildKit attaches an SBOM and maximum-mode provenance, and GitHub publishes a
separate build-provenance attestation bound to the pushed manifest digest.

```bash
gh attestation verify \
  oci://ghcr.io/beepboop2025/seiche:0.10.1 \
  --repo beepboop2025/seiche
```

Use a digest, not a mutable tag, when promoting an image into a controlled
environment.

## Release invariants

The distribution contract checks all of the following before publication:

- the package, MCP listing, citation, Zenodo, CodeMeta, and OCI version fields
  agree;
- citation and catalog metadata use the canonical repository, license, release
  date, versioned tag URL, and research boundary; these static files do not
  embed a self-referential commit SHA, so a release PR can pass before its tag
  exists;
- every external GitHub Action is pinned to a full commit;
- every publishing path requires an annotated SSH-signed version tag, an
  SSH-signed commit by the pinned release author, and the repository's reviewed
  allowed-signers policy before it receives publication authority;
- base container images are pinned to multi-platform manifest digests;
- the image stays non-root, has a healthcheck, and carries OCI source,
  revision, license, and version labels;
- the distroless runtime has no shell or package manager, and both CI and the
  release preflight reject high or critical operating-system and library
  vulnerabilities;
- the Docker context is an allowlist containing only build inputs;
- listing claims, rights-reviewed dataset metadata and hashes, research notebook
  code cells, and the offline Python, JavaScript, and R client contracts pass
  independently; the OpenBB provider has its own isolated package gate;
- the PyPI wheel and source archive are present, not yanked, hash-correct, and
  byte-for-byte identical to the release checkout for every shipped Python
  source file.

PyPI is immutable. Creating a GitHub Release therefore verifies the existing
PyPI artifacts and then publishes the MCP Registry record. It never attempts to
upload the same package version a second time.

Seiche is research software and not investment advice. Publications should cite
the primary data providers for any underlying observations as well as the
Seiche software release.
