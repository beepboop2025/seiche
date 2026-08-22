# OpenBB publication and listing packet

`openbb-seiche` is an independently versioned OpenBB provider and router. The
package is ready for PyPI trusted publication, but neither a PyPI page nor an
OpenBB documentation entry is claimed until its public receipt exists.

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
  -f release_tag=v0.11.0 \
  -f openbb_version=0.1.0
```

## Public verification

After publication, all of these must pass before requesting an OpenBB listing:

```bash
python -m venv /tmp/openbb-seiche-public
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

## Official OpenBB provider-list request

OpenBB's provider page explicitly invites published extensions through a pull
request. The current upstream source is:

`OpenBB-finance/openbb-docs/content/odp/python/extensions/providers/index.mdx`

Once the PyPI receipt is live, add this row to the unofficial third-party table
using the table's then-current formatting:

```text
openbb-seiche | Seiche funding-liquidity and world-markets evidence connector | pip install openbb-seiche | None | -
```

The pull request should link the PyPI version, this repository, the package
tests, and Seiche's research-not-advice boundary. Keep the ledger `prepared`
until OpenBB merges the request and the rendered documentation page is public.
