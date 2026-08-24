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
    "Money, FX, capital-market and metadata-only China macro evidence with source "
    "clocks and limits. Canonical citations remain explicit. Seiche is research "
    "software, not investment advice; "
    "missing and stale evidence remains explicit."
)
OWNED_WORKFLOWS = (
    ROOT / ".github/workflows/audit-distribution-receipts.yml",
    ROOT / ".github/workflows/distribution-contracts.yml",
    ROOT / ".github/workflows/publish-container.yml",
    ROOT / ".github/workflows/publish-mcp.yml",
    ROOT / ".github/workflows/publish-openbb.yml",
    ROOT / ".github/workflows/publish-pypi.yml",
)
PUBLICATION_WORKFLOWS = (
    ROOT / ".github/workflows/publish-container.yml",
    ROOT / ".github/workflows/publish-mcp.yml",
    ROOT / ".github/workflows/publish-openbb.yml",
    ROOT / ".github/workflows/publish-pypi.yml",
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
        self.assertEqual(self.zenodo["version"], version)
        self.assertEqual(_cff_scalar(self.citation, "version"), version)
        self.assertEqual(self.codemeta["version"], version)
        self.assertEqual(
            _cff_scalar(self.citation, "repository-artifact"),
            f"https://pypi.org/project/seiche/{version}/",
        )
        self.assertEqual(
            self.codemeta["downloadUrl"], f"https://pypi.org/project/seiche/{version}/"
        )

    def test_pypi_readme_carries_the_registry_ownership_marker(self) -> None:
        marker = "mcp-name: io.github.beepboop2025/seiche"
        self.assertIn(marker, _read("backend/README.md").splitlines())

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
        self.assertEqual(self.project["license-files"], ["LICENSE"])
        self.assertEqual(_read("backend/LICENSE"), _read("LICENSE"))
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
        active_integration_paths = [
            "frontend/index.html",
            "integrations/datacommons/DATA_REQUEST.md",
            "integrations/datacommons/RIGHTS_AND_SOURCES.md",
            "integrations/datacommons/eligibility.csv",
            *sorted(
                str(path.relative_to(ROOT))
                for path in (ROOT / "integrations/hermes/skills").glob("*/SKILL.md")
            ),
        ]
        stale_spdx = re.compile(r"AGPL-3\.0(?!-or-later)")
        for path in active_integration_paths:
            with self.subTest(path=path):
                self.assertNotRegex(_read(path), stale_spdx)

    def test_zenodo_matches_the_documented_github_authoring_contract(self) -> None:
        self.assertNotIn("$schema", self.zenodo)
        self.assertEqual(self.zenodo["license"], LICENSE_EXPRESSION)
        self.assertEqual(self.zenodo["version"], self.project["version"])
        self.assertEqual(self.zenodo["language"], "eng")
        self.assertEqual(
            set(self.zenodo),
            {
                "title",
                "description",
                "creators",
                "publication_date",
                "version",
                "upload_type",
                "language",
                "access_right",
                "license",
                "keywords",
                "related_identifiers",
                "notes",
            },
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
        self.assertIn(
            "missing and stale evidence remains explicit", CANONICAL_DESCRIPTION
        )
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
        related = {item["identifier"] for item in self.zenodo["related_identifiers"]}
        self.assertIn(REPOSITORY, related)
        self.assertIn("Cite primary data providers", self.zenodo["notes"])


class PublicCatalogContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ai_catalog = _json("frontend/public/.well-known/ai-catalog.json")
        cls.product_card = _json("frontend/public/product-card.json")
        cls.money_catalog = _json("frontend/public/money-markets/catalog.json")
        cls.dcat = _json("distribution/datasets/dcat.jsonld")

    def test_machine_media_and_license_identifiers_are_canonical(self) -> None:
        mcp_entry = next(
            entry
            for entry in self.ai_catalog["entries"]
            if entry["identifier"] == "urn:air:seiche.info:mcp:funding-stress"
        )
        self.assertEqual(mcp_entry["type"], "application/json")
        self.assertEqual(self.product_card["product"]["license"], LICENSE_EXPRESSION)
        self.assertEqual(
            self.money_catalog["rights_boundary"]["code_license"],
            LICENSE_EXPRESSION,
        )
        self.assertNotIn("AGPL-3.0 covers", _read("frontend/public/product-card.json"))
        self.assertNotIn(
            "AGPL-3.0 covers", _read("frontend/public/money-markets/catalog.json")
        )

        headers = _read("frontend/public/_headers")
        ai_headers = headers.split("/.well-known/ai-catalog.json\n", maxsplit=1)[
            1
        ].split("\n\n", maxsplit=1)[0]
        self.assertIn("Content-Type: application/json; charset=utf-8", ai_headers)
        self.assertNotIn("application/ai-catalog+json", headers)

    def test_dcat_identity_has_a_deployable_landing_and_catalog(self) -> None:
        canonical = f"{WEBSITE}/datasets/direct-ofr/"
        graph = {item["@id"]: item for item in self.dcat["@graph"]}
        self.assertIn(f"{canonical}#catalog", graph)
        self.assertIn(canonical, graph)
        self.assertEqual(
            graph[f"{canonical}#catalog"]["dcat:landingPage"]["@id"], canonical
        )
        self.assertEqual(
            graph[canonical]["dcat:landingPage"]["@id"],
            f"{REPOSITORY}/tree/v0.11.1/distribution/datasets",
        )

        self.assertEqual(
            _read("frontend/public/datasets/direct-ofr/catalog.jsonld"),
            _read("distribution/datasets/dcat.jsonld"),
        )
        landing = _read("frontend/public/datasets/direct-ofr/index.html")
        self.assertIn(f'<link rel="canonical" href="{canonical}"', landing)
        self.assertIn(
            'href="https://seiche.info/datasets/direct-ofr/catalog.jsonld"', landing
        )
        self.assertIn("11,163", landing)
        self.assertIn("draft, not submitted", landing.lower())
        self.assertIn("AGPL-3.0-or-later", landing)
        self.assertIn('<section id="catalog"', landing)

        headers = _read("frontend/public/_headers")
        dcat_headers = headers.split(
            "/datasets/direct-ofr/catalog.jsonld\n", maxsplit=1
        )[1].split("\n\n", maxsplit=1)[0]
        self.assertIn("Content-Type: application/ld+json; charset=utf-8", dcat_headers)
        self.assertIn("Access-Control-Allow-Origin: *", dcat_headers)

    def test_directory_inventory_count_and_release_order_are_explicit(self) -> None:
        clients = _read("integrations/mcp-clients/README.md")
        self.assertIn("seven additional live indexes", clients)
        self.assertNotIn("eight additional live indexes", clients)

        publishing = _read("docs/PUBLISHING.md")
        ordered_markers = (
            "Wait for the immutable PyPI receipt",
            "Confirm the Zenodo GitHub integration is enabled before releasing",
            "Create one GitHub Release from the verified tag",
            "Receipt the three release-triggered surfaces",
        )
        positions = [publishing.index(marker) for marker in ordered_markers]
        self.assertEqual(positions, sorted(positions))
        for workflow in (
            "publish-pypi.yml",
            "publish-mcp.yml",
            "publish-openbb.yml",
        ):
            command = publishing.split(f"gh workflow run {workflow}", maxsplit=1)[1]
            command = command.split("```", maxsplit=1)[0]
            self.assertIn("--ref v0.11.1", command)
            self.assertIn("release_tag=v0.11.1", command)
        openbb_submission = _read("integrations/openbb/SUBMISSION.md")
        openbb_command = openbb_submission.split(
            "gh workflow run publish-openbb.yml", maxsplit=1
        )[1].split("```", maxsplit=1)[0]
        self.assertIn("--repo beepboop2025/seiche", openbb_command)
        self.assertIn("--ref v0.11.1", openbb_command)
        self.assertIn("release_tag=v0.11.1", openbb_command)
        self.assertIn("openbb_version=0.1.0", openbb_command)
        self.assertIn("python3 -m venv /tmp/openbb-seiche-public", openbb_submission)
        self.assertNotIn("\npython -m venv ", openbb_submission)
        distribution = _read("docs/DISTRIBUTION.md")
        self.assertIn("OpenBB-finance/awesome-openbb", publishing)
        self.assertNotIn("OpenBB's docs", publishing)
        self.assertIn("`awesome-openbb` ecosystem-list", distribution)
        self.assertNotIn("documentation pull request", distribution)
        self.assertIn("OpenBB-finance/openbb-docs", openbb_submission)
        self.assertIn("not the documentation repository", openbb_submission)
        self.assertNotIn("gh workflow run publish-container.yml", publishing)
        self.assertIn(
            "refuses to replace a higher bare-semantic version",
            _read("docs/DISTRIBUTION.md"),
        )
        container_recovery = publishing.split(
            "gh run rerun WORKFLOW_RUN_ID", maxsplit=1
        )[1].split("```", maxsplit=1)[0]
        self.assertIn("--repo beepboop2025/seiche --failed", container_recovery)


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
        found = set(
            re.findall(r"org\.opencontainers\.image\.([\w.]+)=", self.dockerfile)
        )
        self.assertTrue(required.issubset(found), required - found)
        self.assertIn(
            f'org.opencontainers.image.licenses="{LICENSE_EXPRESSION}"', self.dockerfile
        )
        self.assertIn(f"ARG SOURCE={REPOSITORY}", self.dockerfile)

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


class OpenBBPackageContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = tomllib.loads(_read("integrations/openbb/pyproject.toml"))
        cls.project = cls.document["project"]

    def test_metadata_uses_pep_621_and_pep_639(self) -> None:
        self.assertEqual(self.project["name"], "openbb-seiche")
        self.assertEqual(self.project["version"], "0.1.0")
        self.assertEqual(self.project["license"], LICENSE_EXPRESSION)
        self.assertEqual(self.project["license-files"], ["LICENSE"])
        self.assertEqual(
            self.project["authors"],
            [
                {
                    "name": "Mrinal",
                    "email": "beepboop2025@users.noreply.github.com",
                }
            ],
        )
        self.assertEqual(_read("integrations/openbb/LICENSE"), _read("LICENSE"))
        self.assertNotIn("license", self.document["tool"]["poetry"])
        self.assertNotIn("plugins", self.document["tool"]["poetry"])

    def test_entry_points_and_build_backend_are_exact(self) -> None:
        self.assertEqual(
            self.project["entry-points"],
            {
                "openbb_provider_extension": {
                    "seiche": "openbb_seiche:seiche_provider"
                },
                "openbb_core_extension": {
                    "seiche": "openbb_seiche.router.seiche_router:router"
                },
            },
        )
        self.assertEqual(
            self.document["build-system"],
            {
                "requires": ["poetry-core==2.4.1"],
                "build-backend": "poetry.core.masonry.api",
            },
        )
        self.assertIn(
            f"openbb-seiche/{self.project['version']} (+https://seiche.info)",
            _read("integrations/openbb/openbb_seiche/models/_client.py"),
        )


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

    def test_container_publication_preserves_scanned_bytes_and_attests_subjects(
        self,
    ) -> None:
        workflow = _read(".github/workflows/publish-container.yml")
        build_job, remainder = workflow.split("  build:\n", maxsplit=1)[1].split(
            "\n  scan:\n", maxsplit=1
        )
        scan_job, remainder = remainder.split("\n  publish:\n", maxsplit=1)
        publish_job, remainder = remainder.split("\n  attest:\n", maxsplit=1)
        attest_job, prove_job = remainder.split("\n  prove-public:\n", maxsplit=1)
        for required in (
            "packages: write",
            "id-token: write",
            "attestations: write",
            "ghcr.io/${{ github.repository }}",
            "platforms: linux/amd64",
            "platforms: linux/arm64",
            "outputs: type=oci",
            "provenance: false",
            "sbom: false",
            "artifact-ids: ${{ needs.build.outputs.candidate_artifact_id }}",
            "Validate and materialize the exact OCI layouts",
            'trivy image \\\n            --input "${AMD_LAYOUT}@${AMD_DIGEST}"',
            'trivy image \\\n            --input "${ARM_LAYOUT}@${ARM_DIGEST}"',
            "--format cyclonedx",
            "scan-receipt.json",
            "regctl image import",
            "regctl index create",
            "regctl image copy --force-recursive",
            'fixed_tags=("$VERSION" "$RELEASE_TAG" "sha-${REVISION:0:12}")',
            "current-latest-index.json",
            "latest-decision.txt",
            "current GHCR latest index bytes do not match its digest",
            "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
            "actions/attest-build-provenance@",
            "subject-digest: ${{ env.AMD_DIGEST }}",
            "subject-digest: ${{ env.ARM_DIGEST }}",
            "subject-digest: ${{ env.ROOT_DIGEST }}",
            "push-to-registry: true",
            "Prove the index, both platforms, and attestations are public",
            'anonymous_docker_config="${RUNNER_TEMP}/anonymous-docker-config"',
            'DOCKER_CONFIG="$anonymous_docker_config"',
            'docker pull --platform linux/amd64 "$IMAGE_NAME@$ROOT_DIGEST"',
            'docker pull --platform linux/arm64 "$IMAGE_NAME@$ROOT_DIGEST"',
            "--bundle-from-oci",
            '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/publish-container.yml"',
            '--source-ref "refs/tags/$RELEASE_TAG"',
            '--source-digest "$REVISION"',
            "--predicate-type https://cyclonedx.org/bom",
            "gh_2.98.0_linux_amd64.tar.gz",
            "3b8ac6b30336802fc1a858d7c084e11cdf24ac1a761ca90b68022d7d729208de",
            "public attestation verification is empty",
        ):
            self.assertIn(required, workflow)
        self.assertIn("release tag", workflow)
        self.assertIn('f"v{version}"', workflow)
        self.assertEqual(workflow.count("docker/build-push-action@"), 2)
        self.assertEqual(workflow.count("outputs: type=oci"), 2)
        self.assertEqual(workflow.count("provenance: false"), 2)
        self.assertEqual(workflow.count("sbom: false"), 2)
        self.assertNotIn("push: true", workflow)
        self.assertIn("actions/checkout@", build_job)
        self.assertIn("docker/build-push-action@", build_job)
        self.assertNotIn("packages: write", build_job)
        self.assertNotIn("id-token: write", build_job)
        self.assertNotIn("actions/checkout@", scan_job)
        self.assertNotIn("docker/build-push-action@", scan_job)
        self.assertNotIn("packages: write", scan_job)
        self.assertIn("permissions: {}", scan_job)
        self.assertIn("environment: ghcr-release", publish_job)
        self.assertIn("packages: write", publish_job)
        self.assertNotIn("id-token: write", publish_job)
        self.assertNotIn("attestations: write", publish_job)
        self.assertNotIn("actions/attest-build-provenance@", publish_job)
        self.assertNotIn("actions/checkout@", publish_job)
        self.assertNotIn("docker/build-push-action@", publish_job)
        self.assertIn("needs: [build, scan, publish]", attest_job)
        self.assertIn("environment: ghcr-release", attest_job)
        self.assertIn("id-token: write", attest_job)
        self.assertIn("attestations: write", attest_job)
        self.assertEqual(attest_job.count("actions/attest@"), 2)
        self.assertEqual(attest_job.count("create-storage-record: false"), 3)
        self.assertNotIn("actions/checkout@", attest_job)
        self.assertNotIn("docker/build-push-action@", attest_job)
        self.assertIn("needs: [build, scan, publish, attest]", prove_job)
        self.assertIn("permissions: {}", prove_job)
        self.assertNotIn("actions/checkout@", prove_job)
        self.assertNotIn("docker/login-action@", prove_job)
        self.assertIn("env -u GH_TOKEN -u GITHUB_TOKEN", prove_job)
        self.assertIn("retention-days: 30", build_job)

    def test_distribution_ci_covers_every_public_distribution_surface(self) -> None:
        workflow = _read(".github/workflows/distribution-contracts.yml")
        for required_path in (
            '"README.md"',
            '"backend/tests/test_distribution_listings.py"',
            '"backend/tests/test_distribution_receipt_auditor.py"',
            '"backend/scripts/ard_coverage.py"',
            '"backend/tests/test_ai_discovery.py"',
            '"backend/tests/test_ard_coverage.py"',
            '"backend/tests/test_public_money_market_discovery.py"',
            '"backend/tests/test_public_dataset_deploy.py"',
            '"backend/tests/test_python_artifact_verifier.py"',
            '"clients/**"',
            '"distribution/**"',
            '"integrations/datacommons/**"',
            '"integrations/mcp-clients/**"',
            '"integrations/openbb/**"',
            '"notebooks/**"',
            '"ops/deploy/release-allowed-signers"',
            '"ops/release/verify_public_dataset.py"',
            '"ops/release/audit_distribution_receipts.py"',
        ):
            with self.subTest(path=required_path):
                self.assertGreaterEqual(workflow.count(required_path), 2)
        for required_check in (
            "offline-distribution:",
            "backend/tests/test_ai_discovery.py",
            "backend/tests/test_ard_coverage.py",
            "backend/tests/test_distribution_listings.py",
            "backend/tests/test_distribution_receipt_auditor.py",
            "backend/tests/test_public_money_market_discovery.py",
            "backend/tests/test_public_dataset_deploy.py",
            "backend/tests/test_python_artifact_verifier.py",
            "distribution/datasets/test_distribution_kit.py",
            "ops/release/verify_public_dataset.py",
            "ops/release/audit_distribution_receipts.py",
            "distribution/datasets/stage.py --validate-only",
            "clients/python/world_markets.py",
            "node --check clients/javascript/world-markets.mjs",
            "node --test clients/javascript/world-markets.test.mjs",
            'receipt["client_limits"]["timeout_seconds"] == 1.25',
            "receipt.client_limits.timeout_ms, 1000",
            "receipt$client_limits$timeout_seconds, 2",
            "R client syntax and offline contract",
            "validate_world_markets_contract",
            "ruff format --check --no-cache",
            "native-dataset-metadata:",
            '"huggingface_hub==1.28.0"',
            "DatasetCard.load",
            '"kaggle==2.2.4"',
            "KaggleApi.__new__(KaggleApi)",
            "api.validate_dataset_string",
            "api.validate_resources",
            "Kaggle 2.2.4 native ID/resource and upload inventory",
            '"mlcroissant==1.0.22"',
            "mlc.Dataset",
            '"frictionless==5.18.1"',
            "Package(package_descriptor",
            '"rdflib==7.5.0"',
            "Graph().parse",
            "isomorphic(source_dcat, served_dcat)",
            "ZENODO_RDM_SHA: 63864033c870734cea9cfae07b6968945e412ba3",
            "8d31bb2c9422fbda37f3dc05f1cb26f43cffccbfa3c1fa6f8cca31b73d38ac7a",
            "054947a28ed9fcd6b23d96204420f5c4c4ed5bb2ac19102dbbe2bb23775e2aa3",
            "version = SanitizedUnicode()",
            'language = fields.Method(deserialize="load_language")',
            "Zenodo documented authoring metadata and pinned adapter: valid",
            "RO-Crate 1.3 JSON-LD graph: valid",
        ):
            self.assertIn(required_check, workflow)
        self.assertNotIn("legacyrecord.json", workflow)
        self.assertNotIn('"jsonschema==4.25.1"', workflow)

    def test_openbb_runtime_matrix_covers_every_advertised_minor(self) -> None:
        workflow = _read(".github/workflows/distribution-contracts.yml")
        job = workflow.split("  openbb-runtime-compatibility:\n", maxsplit=1)[1].split(
            "  openbb-provider:\n", maxsplit=1
        )[0]
        for required in (
            'python: ["3.10", "3.11", "3.12", "3.13", "3.14"]',
            "python-version: ${{ matrix.python }}",
            "--no-build-isolation --no-deps .",
            "python -m pytest -q --ignore=tests/test_artifacts.py",
        ):
            self.assertIn(required, job)

    def test_openbb_provider_has_an_isolated_package_gate(self) -> None:
        workflow = _read(".github/workflows/distribution-contracts.yml")
        job = workflow.split("  openbb-provider:\n", maxsplit=1)[1].split(
            "  contracts-and-container:\n", maxsplit=1
        )[0]
        for required in (
            "working-directory: integrations/openbb",
            'python-version: "3.12.12"',
            "fetch-depth: 0",
            "python -m pytest -q",
            "ruff check --no-cache .",
            "ruff format --check --no-cache .",
            "openbb-build",
            "openbb-core==1.6.13",
            "poetry-core==2.4.1",
            "--no-build-isolation --no-deps .",
            "python -m build --no-isolation",
            "python -m twine check",
            'SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"',
            'git -C "$GITHUB_WORKSPACE" archive HEAD',
            "touch -t 202001010000",
            "touch -t 202401010000",
            'cmp --silent "$artifact"',
            "python verify_artifacts.py",
            "Smoke wheel and sdist in separate clean environments",
            'version("openbb-seiche") == "0.1.0"',
        ):
            self.assertIn(required, job)
        self.assertEqual(
            job.count('git -C "$GITHUB_WORKSPACE" archive HEAD'),
            2,
        )
        self.assertNotIn("openbb", workflow.split("  contracts-and-container:\n", 1)[1])

    def test_openbb_publication_is_signed_reproducible_and_oidc_only(self) -> None:
        workflow = _read(".github/workflows/publish-openbb.yml")
        build_job, remainder = workflow.split("  build:\n", maxsplit=1)[1].split(
            "\n  verify:\n", maxsplit=1
        )
        verify_job, remainder = remainder.split("\n  smoke:\n", maxsplit=1)
        smoke_job, publish_job = remainder.split("\n  publish:\n", maxsplit=1)
        for required in (
            "workflow_dispatch:",
            "openbb_version:",
            "group: publish-openbb-${{ inputs.release_tag }}-${{ inputs.openbb_version }}",
            "cancel-in-progress: false",
            "OPENBB_VERSION: ${{ inputs.openbb_version }}",
            'test "$OPENBB_VERSION" = "$package_version"',
            "environment: pypi-openbb",
            "id-token: write",
            'python-version: "3.12.12"',
            'SOURCE_DATE_EPOCH="$(git show -s --format=%ct "$RELEASE_TAG^{commit}")"',
            'git archive "$RELEASE_TAG^{commit}"',
            "touch -t 202001010000",
            "touch -t 202401010000",
            'cmp --silent "$artifact"',
            "openbb-pristine-source/integrations/openbb/verify_artifacts.py",
            "python -m twine check openbb-candidate/*",
            "Smoke both verified OpenBB artifact formats",
            'version("openbb-seiche") == os.environ["EXPECTED_VERSION"]',
            "pypa/gh-action-pypi-publish@",
            "packages-dir: openbb-verified",
            "skip-existing: true",
            'project_name = "openbb-seiche"',
            "https://pypi.org/pypi/",
            "https://files.pythonhosted.org/",
            "Rehash bytes and reconcile any existing immutable PyPI subset",
            "Poll PyPI for the exact complete immutable OpenBB inventory",
            "foreign or duplicate PyPI file",
            "existing PyPI file is yanked",
            "actions/upload-artifact@",
            "actions/download-artifact@",
            "Verify candidate against a third pristine signed source archive",
            "Record exact verified distribution identity and digests",
            "persist-credentials: false",
            "RELEASE_SIGNING_KEY_FINGERPRINT: ${{ vars.RELEASE_SIGNING_KEY_FINGERPRINT }}",
            "permissions: {}",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn("id-token: write", build_job)
        self.assertNotIn("id-token: write", verify_job)
        self.assertIn("version=\"$(python -I -S - <<'PY'", verify_job)
        self.assertNotIn("environment: pypi-openbb", build_job)
        self.assertNotIn("environment: pypi-openbb", verify_job)
        self.assertNotIn("gh-action-pypi-publish", build_job)
        self.assertNotIn("gh-action-pypi-publish", verify_job)
        self.assertIn("needs: build", verify_job)
        self.assertIn("needs: verify", smoke_job)
        self.assertIn("permissions: {}", smoke_job)
        self.assertNotIn("actions/checkout@", smoke_job)
        self.assertIn("needs: [verify, smoke]", publish_job)
        self.assertIn("environment: pypi-openbb", publish_job)
        self.assertEqual(publish_job.count("id-token: write"), 1)
        publish_permissions = publish_job.split("    permissions:\n", maxsplit=1)[
            1
        ].split("    env:\n", maxsplit=1)[0]
        self.assertEqual(publish_permissions, "      id-token: write\n")
        self.assertNotIn("actions/checkout@", publish_job)
        self.assertNotIn("actions/setup-python@", publish_job)
        self.assertNotIn("pip install", publish_job)
        self.assertNotIn("Smoke both verified OpenBB artifact formats", publish_job)
        self.assertNotIn("Refuse an existing immutable OpenBB PyPI version", workflow)
        self.assertIn(
            "candidate_artifact_name: ${{ steps.candidate_identity.outputs.artifact_name }}",
            build_job,
        )
        self.assertIn(
            "name: ${{ needs.build.outputs.candidate_artifact_name }}", verify_job
        )
        self.assertIn(
            "verified_artifact_name: "
            "${{ steps.artifact_identity.outputs.verified_artifact_name }}",
            verify_job,
        )
        self.assertIn(
            "name: ${{ needs.verify.outputs.verified_artifact_name }}", smoke_job
        )
        self.assertIn(
            "name: ${{ needs.verify.outputs.verified_artifact_name }}", publish_job
        )
        self.assertIn("python -I -S", verify_job)
        self.assertIn("openbb-pristine-source", verify_job)
        self.assertNotIn("openbb-build", verify_job)
        self.assertNotIn("entry_points", verify_job)
        self.assertIn("openbb-build", smoke_job)
        self.assertIn("entry_points", smoke_job)
        self.assertIn("hashlib.sha256(path.read_bytes()).hexdigest()", publish_job)
        self.assertIn('f"openbb_seiche-{version}-py3-none-any.whl"', publish_job)
        self.assertIn('f"openbb_seiche-{version}.tar.gz"', publish_job)
        self.assertIn("existing PyPI SHA-256 differs", publish_job)
        self.assertIn("published PyPI body hash differs", publish_job)
        self.assertIn('output.write("needs_upload=true\\n")', publish_job)
        self.assertIn("needs_upload={str(seen != set(expected)).lower()}", publish_job)
        self.assertIn(
            "if: steps.pypi_state.outputs.needs_upload == 'true'", publish_job
        )
        self.assertNotIn(
            "name: openbb-seiche-candidate-${{ github.run_attempt }}", workflow
        )
        self.assertNotIn(
            "name: openbb-seiche-verified-${{ github.run_attempt }}", workflow
        )
        self.assertNotIn("password:", workflow)
        self.assertNotIn("twine upload", workflow)
        self.assertNotIn("types: [published]", workflow)
        self.assertEqual(workflow.count("id-token: write"), 1)
        self.assertLess(
            workflow.index("Upload the immutable verified OpenBB distributions"),
            workflow.index("Smoke both verified OpenBB artifact formats"),
        )
        self.assertLess(
            workflow.index("Smoke both verified OpenBB artifact formats"),
            workflow.index("Publish OpenBB extension to PyPI"),
        )
        self.assertLess(
            publish_job.index("Rehash bytes and reconcile"),
            publish_job.index("Publish OpenBB extension to PyPI"),
        )
        self.assertLess(
            publish_job.index("Publish OpenBB extension to PyPI"),
            publish_job.index(
                "Poll PyPI for the exact complete immutable OpenBB inventory"
            ),
        )

    def test_mcp_registry_has_one_exact_pypi_gated_publisher(self) -> None:
        publishers = []
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            workflow = path.read_text(encoding="utf-8")
            if (
                "login github-oidc" in workflow
                and '"$PUBLISHER_BIN" publish' in workflow
            ):
                publishers.append(path.name)
        self.assertEqual(publishers, ["publish-mcp.yml"])
        self.assertFalse((ROOT / ".github/workflows/registry-publish.yml").exists())

        workflow = _read(".github/workflows/publish-mcp.yml")
        verify_job, registry_job = workflow.split("  verify-pypi:\n", maxsplit=1)[
            1
        ].split("\n  registry:\n", maxsplit=1)
        self.assertNotIn("id-token: write", verify_job)
        self.assertIn("needs: verify-pypi", registry_job)
        self.assertIn("environment: mcp-registry", registry_job)
        self.assertIn(
            "group: publish-mcp-${{ github.event.release.tag_name || inputs.release_tag }}",
            workflow,
        )
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertEqual(registry_job.count("id-token: write"), 1)
        self.assertNotIn("actions/checkout@", registry_job)
        self.assertNotIn("actions/setup-python@", registry_job)
        self.assertNotIn("verify-commit", registry_job)
        self.assertNotIn("verify-tag", registry_job)
        self.assertIn(
            "manifest_artifact_name: "
            "${{ steps.registry_manifest.outputs.manifest_artifact_name }}",
            verify_job,
        )
        self.assertIn("name: ${{ env.MANIFEST_ARTIFACT_NAME }}", registry_job)
        self.assertIn("Verify the transferred manifest", registry_job)
        self.assertIn("Reconcile the exact MCP Registry version", registry_job)
        self.assertIn("?include_deleted=true", registry_job)
        self.assertIn('output.write("needs_publish=true\\n")', registry_job)
        self.assertIn('output.write("needs_publish=false\\n")', registry_job)
        self.assertGreaterEqual(
            registry_job.count("steps.registry_state.outputs.needs_publish == 'true'"),
            2,
        )
        self.assertIn("normalize(remote) != normalize(local)", registry_job)
        self.assertIn('official.get("status") != "active"', registry_job)
        self.assertIn(
            "Poll and verify the exact public MCP Registry record", registry_job
        )
        self.assertLess(
            registry_job.index("Reconcile the exact MCP Registry version"),
            registry_job.index("Install mcp-publisher"),
        )
        self.assertLess(
            registry_job.index("Publish the verified server.json"),
            registry_job.index("Poll and verify the exact public MCP Registry record"),
        )

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
                    "fetch-depth: 0",
                    'git cat-file -t "$RELEASE_TAG"',
                    'git rev-parse "$RELEASE_TAG^{commit}"',
                    "git fetch --no-tags origin",
                    '"+refs/heads/main:refs/remotes/origin/main"',
                    'git merge-base --is-ancestor "$release_sha" refs/remotes/origin/main',
                    "beepboop2025@users.noreply.github.com",
                    "gpg.ssh.allowedSignersFile=ops/deploy/release-allowed-signers",
                    "verify-commit HEAD",
                    'verify-tag "$RELEASE_TAG"',
                    "persist-credentials: false",
                    "RELEASE_SIGNING_KEY_FINGERPRINT: ${{ vars.RELEASE_SIGNING_KEY_FINGERPRINT }}",
                    "ssh-keygen",
                ):
                    self.assertIn(required, workflow)
                if path.name == "publish-container.yml":
                    self.assertIn("release:\n    types: [published]", workflow)
                    self.assertNotIn("workflow_dispatch:", workflow)
                    self.assertIn(
                        'test "$GITHUB_REF" = "refs/tags/$RELEASE_TAG"', workflow
                    )
                    self.assertIn('test "$GITHUB_SHA" = "$release_sha"', workflow)
                    self.assertIn('re.fullmatch(r"[0-9a-f]{40}", revision)', workflow)
                else:
                    self.assertIn("release_tag:", workflow)
                    self.assertIn("required: true", workflow)
                    self.assertIn('[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]]', workflow)
        mcp = _read(".github/workflows/publish-mcp.yml")
        self.assertEqual(mcp.count('verify-tag "$RELEASE_TAG"'), 1)
        self.assertEqual(
            mcp.count(
                'git merge-base --is-ancestor "$release_sha" refs/remotes/origin/main'
            ),
            1,
        )
        self.assertIn("Upload the verified MCP Registry manifest", mcp)
        self.assertIn("without executing candidate code", mcp)

    def test_pypi_manual_dispatch_is_tag_bound_and_build_chain_is_reproducible(
        self,
    ) -> None:
        workflow = _read(".github/workflows/publish-pypi.yml")
        build_job, remainder = workflow.split("  build:\n", maxsplit=1)[1].split(
            "\n  verify:\n", maxsplit=1
        )
        verify_job, remainder = remainder.split("\n  smoke:\n", maxsplit=1)
        smoke_job, publish_job = remainder.split("\n  publish:\n", maxsplit=1)
        self.assertIn(
            "RELEASE_TAG: ${{ inputs.release_tag || github.ref_name }}", workflow
        )
        self.assertIn(
            "group: publish-pypi-${{ inputs.release_tag || github.ref_name }}",
            workflow,
        )
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("ref: ${{ env.RELEASE_TAG }}", workflow)
        for required in (
            'python-version: "3.12.12"',
            '"build==1.5.0"',
            '"packaging==26.3"',
            '"pyproject-hooks==1.2.0"',
            '"twine==7.0.0"',
            'SOURCE_DATE_EPOCH="$(git show -s --format=%ct "$RELEASE_TAG^{commit}")"',
            "export SOURCE_DATE_EPOCH",
            'git archive "$RELEASE_TAG^{commit}" | tar -x',
            "touch -t 202001010000",
            "touch -t 202401010000",
            'cmp --silent "$artifact" "${RUNNER_TEMP}/dist-second/$name"',
            "source-verification/ops/release/verify_python_artifacts.py",
            '--repository-license "${RUNNER_TEMP}/source-verification/LICENSE"',
            "python -m twine check candidate-dist/*",
            "sha256sum candidate-dist/*",
            "for artifact_kind in wheel sdist",
            "install_flags=(--no-build-isolation --no-deps)",
            '"$smoke/bin/seiche" --help',
            "actions/upload-artifact@",
            "actions/download-artifact@",
            "Verify exact bytes against a third pristine signed archive",
            "Record the exact verified distribution identity and digests",
            "Rehash bytes and reconcile any existing immutable PyPI subset",
            "Poll PyPI for the exact complete immutable inventory",
            "skip-existing: true",
            "foreign or duplicate PyPI file",
            "existing PyPI file is yanked",
            "persist-credentials: false",
            "RELEASE_SIGNING_KEY_FINGERPRINT: ${{ vars.RELEASE_SIGNING_KEY_FINGERPRINT }}",
            "permissions: {}",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn("id-token: write", build_job)
        self.assertNotIn("id-token: write", verify_job)
        self.assertNotIn("environment: pypi", build_job)
        self.assertNotIn("environment: pypi", verify_job)
        self.assertNotIn("gh-action-pypi-publish", build_job)
        self.assertNotIn("gh-action-pypi-publish", verify_job)
        self.assertIn("needs: build", verify_job)
        self.assertIn("needs: verify", smoke_job)
        self.assertIn("permissions: {}", smoke_job)
        self.assertNotIn("actions/checkout@", smoke_job)
        self.assertIn("needs: [verify, smoke]", publish_job)
        self.assertIn("environment: pypi", publish_job)
        self.assertEqual(publish_job.count("id-token: write"), 1)
        publish_permissions = publish_job.split("    permissions:\n", maxsplit=1)[
            1
        ].split("    env:\n", maxsplit=1)[0]
        self.assertEqual(
            publish_permissions,
            "      id-token: write   # the OIDC handshake with PyPI\n",
        )
        self.assertNotIn("actions/checkout@", publish_job)
        self.assertNotIn("actions/setup-python@", publish_job)
        self.assertNotIn("pip install", publish_job)
        self.assertNotIn('seiche" --help', publish_job)
        self.assertNotIn('seiche" --help', verify_job)
        self.assertIn('seiche" --help', smoke_job)
        self.assertIn(
            "candidate_artifact_name: ${{ steps.candidate_identity.outputs.artifact_name }}",
            build_job,
        )
        self.assertIn(
            "name: ${{ needs.build.outputs.candidate_artifact_name }}", verify_job
        )
        self.assertIn(
            "verified_artifact_name: "
            "${{ steps.artifact_identity.outputs.verified_artifact_name }}",
            verify_job,
        )
        self.assertIn(
            "name: ${{ needs.verify.outputs.verified_artifact_name }}", smoke_job
        )
        self.assertIn(
            "name: ${{ needs.verify.outputs.verified_artifact_name }}", publish_job
        )
        self.assertIn("python -I -S", verify_job)
        self.assertIn("source-verification", verify_job)
        self.assertIn("hashlib.sha256(path.read_bytes()).hexdigest()", publish_job)
        self.assertIn('f"seiche-{version}-py3-none-any.whl"', publish_job)
        self.assertIn('f"seiche-{version}.tar.gz"', publish_job)
        self.assertIn("existing PyPI SHA-256 differs", publish_job)
        self.assertIn("published PyPI body hash differs", publish_job)
        self.assertIn('output.write("needs_upload=true\\n")', publish_job)
        self.assertIn("needs_upload={str(seen != set(expected)).lower()}", publish_job)
        self.assertIn(
            "if: steps.pypi_state.outputs.needs_upload == 'true'", publish_job
        )
        self.assertNotIn(
            "name: seiche-pypi-candidate-${{ github.run_attempt }}", workflow
        )
        self.assertNotIn(
            "name: seiche-pypi-verified-${{ github.run_attempt }}", workflow
        )
        self.assertEqual(workflow.count("id-token: write"), 1)
        self.assertNotIn("pip install --quiet build\n", workflow)
        self.assertLess(
            workflow.index("Upload the immutable verified distributions"),
            workflow.index("Smoke both verified artifact formats"),
        )
        self.assertLess(
            workflow.index("Smoke both verified artifact formats"),
            workflow.index("Publish to PyPI"),
        )
        self.assertLess(
            publish_job.index("Rehash bytes and reconcile"),
            publish_job.index("Publish to PyPI"),
        )
        self.assertLess(
            publish_job.index("Publish to PyPI"),
            publish_job.index("Poll PyPI for the exact complete immutable inventory"),
        )

    def test_package_backend_and_ci_are_reproducibility_pinned(self) -> None:
        build_system = tomllib.loads(_read("backend/pyproject.toml"))["build-system"]
        self.assertEqual(build_system["build-backend"], "hatchling.build")
        self.assertEqual(
            build_system["requires"],
            [
                "hatchling==1.32.0",
                "packaging==26.3",
                "pathspec==1.1.1",
                "pluggy==1.6.0",
                "tomlkit==0.15.1",
                "trove-classifiers==2026.6.1.19",
            ],
        )
        hatch = tomllib.loads(_read("backend/pyproject.toml"))["tool"]["hatch"]["build"]
        self.assertTrue(hatch["reproducible"])
        self.assertNotIn("ignore-vcs", hatch)
        self.assertEqual(hatch["targets"]["wheel"]["packages"], ["seiche"])
        self.assertEqual(hatch["targets"]["sdist"]["only-include"], ["seiche"])
        workflow = _read(".github/workflows/distribution-contracts.yml")
        verifier = _read("ops/release/verify_python_artifacts.py")
        for required in (
            "seiche-package:",
            "Prove reproducibility across independent source trees",
            'python-version: "3.12.12"',
            'SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"',
            'printf \'SOURCE_DATE_EPOCH=%s\\n\' "$SOURCE_DATE_EPOCH" >> "$GITHUB_ENV"',
            "git archive HEAD | tar -x",
            "touch -t 202001010000",
            "touch -t 202401010000",
            'cmp --silent "$artifact"',
            "python ops/release/verify_python_artifacts.py",
            '--repository-license "${RUNNER_TEMP}/source-first/LICENSE"',
            "for artifact_kind in wheel sdist",
            "install_flags=(--no-build-isolation --no-deps)",
            '"$smoke/bin/seiche" --help',
        ):
            self.assertIn(required, workflow)
        for required in (
            "member.mtime == epoch",
            "member.uid == member.gid == 0",
            'member.uname == member.gname == ""',
            "sdist contains missing or unreviewed members",
            "wheel contains missing or unreviewed members",
            "wheel RECORD inventory differs from the archive",
            "backend/LICENSE differs from the repository AGPL text",
            "License-File: LICENSE",
            "MAX_EXPANDED_BYTES",
            "_bounded_gzip_decompress",
            "metadata.get_all",
            "sdist contains a non-regular member",
            "wheel and sdist metadata differ byte-for-byte",
        ):
            self.assertIn(required, verifier)

    def test_distribution_ci_fails_closed_on_package_vulnerabilities(self) -> None:
        contracts = _read(".github/workflows/distribution-contracts.yml")
        for required in (
            "aquasecurity/trivy-action@",
            "image-ref: seiche:contract",
            "scanners: vuln",
            "vuln-type: os,library",
            "severity: CRITICAL,HIGH",
            "ignore-unfixed: false",
            'exit-code: "1"',
            "version: v0.74.0",
        ):
            self.assertIn(required, contracts)

        publication = _read(".github/workflows/publish-container.yml")
        for required in (
            "trivy_0.74.0_Linux-64bit.tar.gz",
            "2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a",
            "sha256sum --check --strict",
            "trivy image",
            "--scanners vuln",
            "--pkg-types os,library",
            "--severity CRITICAL,HIGH",
            "--ignore-unfixed=false",
            "--exit-code 1",
        ):
            self.assertIn(required, publication)
        self.assertEqual(publication.count("--exit-code 1"), 2)

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
            "PYPI_DIST_DIR",
            "destination.write_bytes(body)",
            "ops/release/verify_python_artifacts.py",
            '--repository-license "$SOURCE_AUTHORITY/LICENSE"',
            'SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"',
            "needs: verify-pypi",
            "source-authority",
            "python -I -S",
            "Upload the verified MCP Registry manifest",
            "seiche-mcp-registry-manifest",
            "server_json_sha256",
            "Verify the transferred manifest without executing candidate code",
        ):
            self.assertIn(required, workflow)

    def test_container_smoke_proves_package_bootstraps_are_absent(self) -> None:
        workflow = _read(".github/workflows/distribution-contracts.yml")
        for required in (
            'importlib.util.find_spec("pip")',
            'importlib.util.find_spec("ensurepip")',
            '"/home/nonroot/venv/bin/pip"',
            '"/usr/local/bin/pip"',
            "os.getuid() != 65532",
        ):
            self.assertIn(required, workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
