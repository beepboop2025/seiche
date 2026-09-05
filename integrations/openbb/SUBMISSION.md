# OpenBB publication and listing packet

`openbb-seiche` is an independently versioned OpenBB provider and router. The
package is ready for PyPI trusted publication, but neither a PyPI page nor an
OpenBB ecosystem listing is claimed until its public receipt exists.

## Publication gate

An owner explicitly runs `.github/workflows/publish-openbb.yml` with the signed
Seiche tag and independently declared OpenBB version. That workflow checks both
version identities plus the annotated tag and release commit, builds twice from
independent timestamp-perturbed Git archives, verifies the exact wheel and
source contents, smoke-installs both formats, and publishes through PyPI OIDC.
The trusted-publisher identity is:

- owner: `beepboop2025`
- repository: `seiche`
- workflow: `publish-openbb.yml`
- environment: `pypi-openbb`
- package: `openbb-seiche`

Do not substitute a local token or `twine upload`. If the pending publisher is
not configured, leave the ledger at `prepared` and restore the OIDC path.

```bash
gh workflow run publish-openbb.yml \
  --repo beepboop2025/seiche \
  --ref v0.12.2 \
  -f release_tag=v0.12.2 \
  -f openbb_version=0.1.0
```

## Public verification

After publication, all of these must pass before requesting an OpenBB listing:

```bash
python3 -m venv /tmp/openbb-seiche-public
/tmp/openbb-seiche-public/bin/python -m pip install \
  "openbb-core==1.6.13" "openbb-seiche==0.1.0"
/tmp/openbb-seiche-public/bin/openbb-build
/tmp/openbb-seiche-public/bin/python - <<'PY'
from importlib.metadata import entry_points, version

assert version("openbb-seiche") == "0.1.0"
for group in ("openbb_provider_extension", "openbb_core_extension"):
    matches = [entry for entry in entry_points(group=group) if entry.name == "seiche"]
    assert len(matches) == 1
    assert matches[0].load() is not None
PY
```

Record both the immutable PyPI version URL and the successful GitHub Actions
run in `distribution/submissions.csv`.

## Current OpenBB ecosystem-list request

Do not submit a provider-table row to `OpenBB-finance/openbb-docs`. In July
2026, an OpenBB maintainer closed
[openbb-docs PR #165](https://github.com/OpenBB-finance/openbb-docs/pull/165)
and directed the third-party provider author to
[`OpenBB-finance/awesome-openbb`](https://github.com/OpenBB-finance/awesome-openbb),
not the documentation repository, because the provider list is changing for
ODP v5. Reconfirm that route immediately before submission; upstream review
policy can change.

The current target is a pull request against
[`OpenBB-finance/awesome-openbb/README.md`](https://github.com/OpenBB-finance/awesome-openbb/blob/main/README.md).
That repository describes itself as a curated list of community-built OpenBB
apps, data connectors, and integrations. It has a merged precedent for a
pip-installable extension in
[awesome-openbb PR #3](https://github.com/OpenBB-finance/awesome-openbb/pull/3),
but the maintainer requested and tested an OpenBB Workspace app before merging
it. A provider-only contribution is therefore review-gated; a pending pull
request is not evidence that this package shape will be accepted.

After the immutable PyPI `0.1.0` receipt is live, add this block under the
then-current **Applications → Live data** format:

```markdown
**Seiche**: Source-clocked funding-liquidity and world-markets evidence for OpenBB, plus signed metadata-only China macro provenance with explicit rights and research-not-advice boundaries.
- Open source: [github.com/beepboop2025/seiche](https://github.com/beepboop2025/seiche)
- PyPI: [openbb-seiche](https://pypi.org/project/openbb-seiche/0.1.0/)
- Install: `pip install openbb-seiche`
- OpenBB usage: `obb.seiche.funding_stress(provider="seiche")` or `obb.seiche.world_markets(selector="china_macro", provider="seiche")`
- API required: None; the extension reads Seiche's anonymous hosted API
- Author: [beepboop2025](https://github.com/beepboop2025)
```

The pull-request body should link the immutable PyPI version, its successful
trusted-publishing run, this repository and signed release, the OpenBB package
tests, and Seiche's research-not-advice boundary. State plainly that the current
artifact is a Python provider/router, not a hosted OpenBB Workspace app. If a
maintainer requests an app or widgets, keep the ledger `prepared`; build and
test that real surface before changing the submission copy.

Keep `distribution/submissions.csv` at `prepared`, with an empty `receipt_url`,
while publication or review is pending. Change it to `listed` only after the
upstream README contains the Seiche entry; then record both the merged pull
request and the rendered README entry. A community-list merge is a listing
receipt, not an OpenBB endorsement or approval of Seiche's financial claims.
