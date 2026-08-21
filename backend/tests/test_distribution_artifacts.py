"""Fail-closed contracts for Seiche's public distribution artifacts.

This file deliberately uses only the Python standard library. It runs in an
otherwise empty Python 3.12 environment before a container or release gets
publishing authority.
"""

from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "https://github.com/beepboop2025/seiche"
WEBSITE = "https://seiche.info"
LICENSE_EXPRESSION = "AGPL-3.0-or-later"
CANONICAL_TITLE = "Seiche: World-markets evidence terminal"
CANONICAL_DESCRIPTION = (
    "Money, FX and capital-market evidence with source clocks, canonical citations "
    "and explicit limits. Seiche is research software, not investment advice; "
    "missing and stale evidence remains explicit."
)
OWNED_WORKFLOWS = (
    ROOT / ".github/workflows/distribution-contracts.yml",
    ROOT / ".github/workflows/publish-container.yml",
    ROOT / ".github/workflows/publish-mcp.yml",
    ROOT / ".github/workflows/publish-pypi.yml",
    ROOT / ".github/workflows/registry-publish.yml",
)
PUBLICATION_WORKFLOWS = (
    ROOT / ".github/workflows/publish-container.yml",
    ROOT / ".github/workflows/publish-mcp.yml",
    ROOT / ".github/workflows/publish-pypi.yml",
    ROOT / ".github/workflows/registry-publish.yml",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _json(path: str) -> dict:
    value = json.loads(_read(path))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def _cff_scalar(document: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", document, re.MULTILINE)
    if match is None:
        raise AssertionError(f"CITATION.cff has no top-level {key!r}")
    value = match.group(1)
    if value.startswith('"'):
        return json.loads(value)
    return value


class ScientificMetadataContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project = tomllib.loads(_read("backend/pyproject.toml"))["project"]
        cls.server = _json("server.json")
        cls.zenodo = _json(".zenodo.json")
        cls.codemeta = _json("codemeta.json")
        cls.citation = _read("CITATION.cff")

    def test_release_identity_is_consistent(self) -> None:
        version = self.project["version"]
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(self.server["version"], version)
        self.assertEqual(self.server["packages"][0]["version"], version)
        self.assertEqual(_cff_scalar(self.citation, "version"), version)
        self.assertEqual(self.zenodo["version"], version)
        self.assertEqual(self.codemeta["version"], version)
        self.assertEqual(
            _cff_scalar(self.citation, "repository-artifact"),
            f"https://pypi.org/project/seiche/{version}/",
        )
        self.assertEqual(
            self.codemeta["downloadUrl"], f"https://pypi.org/project/seiche/{version}/"
        )

    def test_release_date_and_tag_url_are_consistent_without_a_self_sha(self) -> None:
        version = self.project["version"]
        release_date = _cff_scalar(self.citation, "date-released")
        self.assertEqual(self.zenodo["publication_date"], release_date)
        self.assertEqual(self.codemeta["datePublished"], release_date)
        self.assertRegex(release_date, r"^\d{4}-\d{2}-\d{2}$")

        self.assertNotRegex(self.citation, r"(?m)^commit:")
        self.assertIn(
            f'value: "{REPOSITORY}/tree/v{version}"',
            self.citation,
        )
        self.assertIn('description: "Immutable Git release tag"', self.citation)

    def test_repository_license_and_web_identity_are_consistent(self) -> None:
        self.assertEqual(self.project["license"], LICENSE_EXPRESSION)
        self.assertEqual(self.server["repository"]["url"], REPOSITORY)
        self.assertEqual(self.server["websiteUrl"], WEBSITE)
        self.assertEqual(_cff_scalar(self.citation, "license"), LICENSE_EXPRESSION)
        self.assertEqual(_cff_scalar(self.citation, "repository-code"), REPOSITORY)
        self.assertEqual(_cff_scalar(self.citation, "url"), WEBSITE)
        self.assertEqual(self.zenodo["license"], LICENSE_EXPRESSION)
        self.assertEqual(self.zenodo["access_right"], "open")
        self.assertEqual(self.zenodo["upload_type"], "software")
        self.assertEqual(self.codemeta["codeRepository"], REPOSITORY)
        self.assertEqual(self.codemeta["url"], WEBSITE)
        self.assertEqual(
            self.codemeta["license"],
            f"https://spdx.org/licenses/{LICENSE_EXPRESSION}.html",
        )

    def test_scientific_descriptions_preserve_the_research_boundary(self) -> None:
        self.assertEqual(_cff_scalar(self.citation, "title"), CANONICAL_TITLE)
        self.assertEqual(self.zenodo["title"], CANONICAL_TITLE)
        self.assertEqual(self.codemeta["name"], CANONICAL_TITLE)
        self.assertEqual(_cff_scalar(self.citation, "abstract"), CANONICAL_DESCRIPTION)
        self.assertEqual(self.zenodo["description"], CANONICAL_DESCRIPTION)
        self.assertEqual(self.codemeta["description"], CANONICAL_DESCRIPTION)
        self.assertTrue(CANONICAL_DESCRIPTION.startswith(self.server["description"]))
        self.assertIn("not investment advice", CANONICAL_DESCRIPTION.lower())
        self.assertIn("missing and stale evidence remains explicit", CANONICAL_DESCRIPTION)
        author_name = self.project["authors"][0]["name"]
        self.assertIn(f'family-names: "{author_name}"', self.citation)
        self.assertEqual(self.zenodo["creators"][0]["name"], author_name)
        self.assertEqual(self.codemeta["author"][0]["name"], author_name)
        self.assertEqual(self.codemeta["author"][0]["@type"], "Person")
        self.assertTrue(self.codemeta["isAccessibleForFree"])

    def test_metadata_has_discovery_keywords_and_related_sources(self) -> None:
        required = {"financial research", "money markets", "point-in-time data"}
        self.assertTrue(required.issubset(set(self.zenodo["keywords"])))
        self.assertTrue(required.issubset(set(self.codemeta["keywords"])))
        related = {
            item["identifier"] for item in self.zenodo["related_identifiers"]
        }
        self.assertIn(REPOSITORY, related)
        self.assertIn("Cite primary data providers", self.zenodo["notes"])


class ContainerContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = _read("Dockerfile")
        cls.dockerignore = _read(".dockerignore")
        cls.compose = _read("compose.yaml")

    def test_base_images_and_context_are_pinned_and_minimal(self) -> None:
        from_lines = [
            line for line in self.dockerfile.splitlines() if line.startswith("FROM ")
        ]
        self.assertEqual(len(from_lines), 3)
        for line in from_lines:
            self.assertRegex(line, r"@sha256:[0-9a-f]{64}(?:\s+AS\s+[\w-]+)?$")
        self.assertNotIn("COPY . ", self.dockerfile)
        self.assertNotRegex(self.dockerfile, r"(?m)^ADD\s")
        first_rule = next(
            line.strip()
            for line in self.dockerignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        self.assertEqual(first_rule, "**")
        for forbidden in ("!.git", "!backend/data", "!backend/tests", "!node_modules"):
            self.assertNotIn(forbidden, self.dockerignore)

    def test_runtime_is_non_root_and_has_a_liveness_probe(self) -> None:
        self.assertIn("USER 65532:65532", self.dockerfile)
        self.assertNotIn("USER root", self.dockerfile)
        self.assertIn("cgr.dev/chainguard/python:latest@sha256:", self.dockerfile)
        self.assertIn("python -m pip uninstall --yes pip", self.dockerfile)
        runtime_stage = self.dockerfile.split(" AS runtime\n", maxsplit=1)[1]
        self.assertIn(
            'RUN ["/usr/bin/python", "-c", "import ensurepip, pathlib, shutil; '
            'shutil.rmtree(pathlib.Path(ensurepip.__file__).parent)"]',
            runtime_stage,
        )
        self.assertNotRegex(runtime_stage, r"(?m)^RUN\s+(?!\[)")
        self.assertIn("HEALTHCHECK", self.dockerfile)
        self.assertIn("http://127.0.0.1:8787/api", self.dockerfile)
        self.assertNotIn("127.0.0.1:8787/api/health", self.dockerfile)
        self.assertIn('VOLUME ["/app/backend/data"]', self.dockerfile)
        self.assertIn('"--host", "0.0.0.0"', self.dockerfile)

    def test_runtime_has_required_oci_labels(self) -> None:
        required = {
            "title",
            "description",
            "authors",
            "url",
            "documentation",
            "source",
            "version",
            "revision",
            "created",
            "licenses",
            "base.name",
            "base.digest",
        }
        found = set(re.findall(r"org\.opencontainers\.image\.([\w.]+)=", self.dockerfile))
        self.assertTrue(required.issubset(found), required - found)
        self.assertIn(f'org.opencontainers.image.licenses="{LICENSE_EXPRESSION}"', self.dockerfile)
        self.assertIn(f'ARG SOURCE={REPOSITORY}', self.dockerfile)

    def test_compose_keeps_only_data_writable(self) -> None:
        for required in (
            'user: "65532:65532"',
            "read_only: true",
            "cap_drop:",
            "- ALL",
            "- no-new-privileges:true",
            "SEICHE_ENV: container",
            "seiche-data:/app/backend/data",
            "/tmp:rw,noexec,nosuid,size=64m",
            "healthcheck:",
        ):
            self.assertIn(required, self.compose)
        self.assertNotIn("privileged: true", self.compose)
        self.assertNotIn("network_mode: host", self.compose)
        self.assertNotIn("SEICHE_ENV: production", self.compose)


class WorkflowContracts(unittest.TestCase):
    def test_every_external_action_is_commit_pinned(self) -> None:
        for path in OWNED_WORKFLOWS:
            document = path.read_text(encoding="utf-8")
            uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", document, re.MULTILINE)
            self.assertTrue(uses, path)
            for reference in uses:
                if reference.startswith("./"):
                    continue
                with self.subTest(workflow=path.name, action=reference):
                    self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")

    def test_container_publication_has_sbom_and_two_provenance_layers(self) -> None:
        workflow = _read(".github/workflows/publish-container.yml")
        for required in (
            "packages: write",
            "id-token: write",
            "attestations: write",
            "ghcr.io/${{ github.repository }}",
            "platforms: linux/amd64,linux/arm64",
            "sbom: true",
            "provenance: mode=max",
            "actions/attest-build-provenance@",
            "subject-digest: ${{ steps.build.outputs.digest }}",
            "push-to-registry: true",
        ):
            self.assertIn(required, workflow)
        self.assertIn("release tag", workflow)
        self.assertIn('f"v{version}"', workflow)

    def test_distribution_ci_covers_every_public_distribution_surface(self) -> None:
        workflow = _read(".github/workflows/distribution-contracts.yml")
        for required_path in (
            '"README.md"',
            '"backend/tests/test_distribution_listings.py"',
            '"clients/**"',
            '"distribution/**"',
            '"integrations/datacommons/**"',
            '"integrations/mcp-clients/**"',
            '"integrations/openbb/**"',
            '"notebooks/**"',
            '"ops/deploy/release-allowed-signers"',
        ):
            with self.subTest(path=required_path):
                self.assertGreaterEqual(workflow.count(required_path), 2)
        for required_check in (
            "offline-distribution:",
            "backend/tests/test_distribution_listings.py",
            "distribution/datasets/test_distribution_kit.py",
            "distribution/datasets/stage.py --validate-only",
            "clients/python/world_markets.py",
            "node --check clients/javascript/world-markets.mjs",
            "node --test clients/javascript/world-markets.test.mjs",
            "R client syntax and offline contract",
            "validate_world_markets_contract",
        ):
            self.assertIn(required_check, workflow)

    def test_openbb_provider_has_an_isolated_package_gate(self) -> None:
        workflow = _read(".github/workflows/distribution-contracts.yml")
        job = workflow.split("  openbb-provider:\n", maxsplit=1)[1].split(
            "  contracts-and-container:\n", maxsplit=1
        )[0]
        for required in (
            "working-directory: integrations/openbb",
            "python -m pytest -q",
            "ruff check --no-cache .",
            "ruff format --check --no-cache .",
            "openbb-build",
            "openbb-core==1.6.13",
            "poetry-core==2.4.1",
            "--no-build-isolation --no-deps .",
            "python -m build --no-isolation",
            "python -m twine check",
        ):
            self.assertIn(required, job)
        self.assertNotIn("openbb", workflow.split("  contracts-and-container:\n", 1)[1])

    def test_every_publication_requires_the_signed_release_policy(self) -> None:
        allowed_signers = [
            line.strip()
            for line in _read("ops/deploy/release-allowed-signers").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(allowed_signers)
        for signer in allowed_signers:
            self.assertRegex(
                signer,
                r"^beepboop2025@users\.noreply\.github\.com "
                r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}$",
            )
        for path in PUBLICATION_WORKFLOWS:
            workflow = path.read_text(encoding="utf-8")
            with self.subTest(workflow=path.name):
                for required in (
                    "release_tag:",
                    "required: true",
                    "fetch-depth: 0",
                    'git cat-file -t "$RELEASE_TAG"',
                    'git rev-parse "$RELEASE_TAG^{commit}"',
                    "beepboop2025@users.noreply.github.com",
                    "gpg.ssh.allowedSignersFile=ops/deploy/release-allowed-signers",
                    "verify-commit HEAD",
                    'verify-tag "$RELEASE_TAG"',
                ):
                    self.assertIn(required, workflow)
                if path.name == "publish-container.yml":
                    self.assertIn(
                        're.fullmatch(r"[0-9a-f]{40}", revision)', workflow
                    )
                else:
                    self.assertIn(
                        '[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]]', workflow
                    )
        self.assertGreaterEqual(
            _read(".github/workflows/publish-mcp.yml").count('verify-tag "$RELEASE_TAG"'),
            2,
        )

    def test_pypi_manual_dispatch_is_tag_bound_and_build_tool_is_pinned(self) -> None:
        workflow = _read(".github/workflows/publish-pypi.yml")
        self.assertIn("RELEASE_TAG: ${{ inputs.release_tag || github.ref_name }}", workflow)
        self.assertIn('ref: ${{ env.RELEASE_TAG }}', workflow)
        self.assertIn('"build==1.5.0"', workflow)
        self.assertNotIn("pip install --quiet build\n", workflow)

    def test_distribution_ci_fails_closed_on_package_vulnerabilities(self) -> None:
        workflows = {
            "distribution-contracts.yml": (
                _read(".github/workflows/distribution-contracts.yml"),
                "image-ref: seiche:contract",
            ),
            "publish-container.yml": (
                _read(".github/workflows/publish-container.yml"),
                "image-ref: seiche:release-scan",
            ),
        }
        for name, (workflow, image_reference) in workflows.items():
            with self.subTest(workflow=name):
                for required in (
                    "aquasecurity/trivy-action@",
                    image_reference,
                    "scanners: vuln",
                    "vuln-type: os,library",
                    "severity: CRITICAL,HIGH",
                    "ignore-unfixed: false",
                    'exit-code: "1"',
                    "version: v0.70.0",
                ):
                    self.assertIn(required, workflow)

    def test_release_verifies_pypi_without_reuploading(self) -> None:
        workflow = _read(".github/workflows/publish-mcp.yml")
        for forbidden in (
            "PYPI_API_TOKEN",
            "TWINE_PASSWORD",
            "twine upload",
            "gh-action-pypi-publish",
        ):
            self.assertNotIn(forbidden, workflow)
        for required in (
            "verify-pypi:",
            "https://pypi.org/pypi/",
            "https://files.pythonhosted.org/",
            "sha256",
            "yanked",
            "License-Expression",
            "wheel source differs from checkout",
            "sdist source differs from checkout",
            "needs: verify-pypi",
        ):
            self.assertIn(required, workflow)

    def test_container_smoke_proves_package_bootstraps_are_absent(self) -> None:
        workflow = _read(".github/workflows/distribution-contracts.yml")
        for required in (
            'importlib.util.find_spec("pip")',
            'importlib.util.find_spec("ensurepip")',
            '"/home/nonroot/venv/bin/pip"',
            '"/usr/local/bin/pip"',
            'os.getuid() != 65532',
        ):
            self.assertIn(required, workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
