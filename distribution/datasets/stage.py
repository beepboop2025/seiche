#!/usr/bin/env python3
"""Validate and symlink-stage the rights-reviewed Seiche research dataset.

This script is intentionally offline and fail closed. It accepts exactly the
two already tracked direct-OFR Data Commons CSVs, their pinned hashes, ten
variables, and 11,163 records. It never reads Seiche's runtime cache and never
copies or uploads observation data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import tomllib

KIT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = KIT_ROOT.parents[1]
DATA_COMMONS_ROOT = REPO_ROOT / "integrations" / "datacommons"
OBSERVATION_ROOT = DATA_COMMONS_ROOT / "input" / "observations"
NOTEBOOK = REPO_ROOT / "notebooks" / "seiche_direct_ofr_research.ipynb"
SOFTWARE_VERSION = tomllib.loads(
    (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]

EXPECTED_FIELDS = (
    "entity",
    "variable",
    "date",
    "value",
    "unit",
    "measurementMethod",
    "observationPeriod",
)
EXPECTED_TOTAL_ROWS = 11_163
EXPECTED_TOTAL_SERIES = 10
EXPECTED_ENTITY = "country/USA"
DATASET_VERSION = "0.1.0-draft"
HUGGING_FACE_LICENSE_NAME = "us-government-work-ofr-credit-requested"
EXPECTED_COMMIT = "93e83bbc592098fc2f6465ffb49c5e872d61c018"
RAW_PREFIX = (
    "https://raw.githubusercontent.com/beepboop2025/seiche/"
    f"{EXPECTED_COMMIT}/integrations/datacommons/input/observations/"
)
PINNED_SOURCE_TREE = (
    "https://github.com/beepboop2025/seiche/tree/"
    f"{EXPECTED_COMMIT}/integrations/datacommons"
)
METADATA_PUBLICATION_TREE = (
    "https://github.com/beepboop2025/seiche/tree/"
    f"v{SOFTWARE_VERSION}/distribution/datasets"
)

REPO_VARIABLES = frozenset(
    {
        "seiche/OfrDvpOvernightOpenRepo_Mean_InterestRate_FinancialInstrument",
        "seiche/OfrDvpRepoTotal_Sum_Amount_FinancialTransaction",
        "seiche/OfrGcfOvernightOpenRepo_Mean_InterestRate_FinancialInstrument",
        "seiche/OfrGcfOvernightOpenRepo_Sum_Amount_FinancialTransaction",
        "seiche/OfrTriPartyOvernightOpenRepo_Mean_InterestRate_FinancialInstrument",
        "seiche/OfrTriPartyRepoTotal_Sum_Amount_FinancialTransaction",
    }
)
MMF_VARIABLES = frozenset(
    {
        "seiche/OfrMoneyMarketFundRepoFederalReserve_Sum_Amount_Investment",
        "seiche/OfrMoneyMarketFundRepoFicc_Sum_Amount_Investment",
        "seiche/OfrMoneyMarketFundRepoTotal_Sum_Amount_Investment",
        "seiche/OfrMoneyMarketFundTotal_Sum_Amount_Investment",
    }
)
EXPECTED_VARIABLES = REPO_VARIABLES | MMF_VARIABLES
EXPECTED_MNEMONICS = frozenset(
    {
        "DVP_VOL",
        "TRI_VOL",
        "DVP_RATE_OO",
        "TRI_RATE_OO",
        "GCF_RATE_OO",
        "GCF_VOL_OO",
        "MMF_TOT",
        "MMF_REPO_FICC",
        "MMF_REPO_FED",
        "MMF_REPO_TOT",
    }
)

SOURCE_SPECS = {
    "ofr_repo_markets.csv": {
        "path": OBSERVATION_ROOT / "ofr_repo_markets.csv",
        "rows": 10_489,
        "bytes": 1_466_225,
        "sha256": "307ae6ad5bbe8653c3bd4abf63449d4229b0fbba1ee66014d91f813e866d3a4a",
        "variables": REPO_VARIABLES,
        "method": "seiche/OfrPreliminaryRelease",
    },
    "ofr_money_market_funds.csv": {
        "path": OBSERVATION_ROOT / "ofr_money_market_funds.csv",
        "rows": 674,
        "bytes": 94_606,
        "sha256": "1d0975b69dcb6f3465e957679b21bacae02695145b1e77563dae3258d8b524cd",
        "variables": MMF_VARIABLES,
        "method": "seiche/OfrMonthlyRevisedSeries",
    },
}

JSON_DOCUMENTS = (
    "manifest.json",
    "kaggle/dataset-metadata.json",
    "croissant.json",
    "datapackage.json",
    "dcat.jsonld",
    "ro-crate-metadata.json",
    "datacite-draft.json",
)
DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/\S+", flags=re.IGNORECASE)
MARKDOWN_LINK_TARGET_PATTERN = re.compile(r"!?\[[^]]*\]\(([^)\s]+)")


class ValidationError(RuntimeError):
    """A distribution invariant changed or an unreviewed input appeared."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON at {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_csv(
    name: str, spec: dict[str, Any]
) -> tuple[int, set[str], set[tuple[str, str, str]]]:
    path = spec["path"]
    _require(
        path.is_file() and not path.is_symlink(),
        f"canonical source is not a regular file: {path}",
    )
    _require(
        path.parent.resolve() == OBSERVATION_ROOT.resolve(),
        f"source escaped reviewed directory: {path}",
    )
    _require(path.stat().st_size == spec["bytes"], f"byte count changed for {name}")
    _require(_sha256(path) == spec["sha256"], f"SHA-256 changed for {name}")

    row_count = 0
    variables: set[str] = set()
    keys: set[tuple[str, str, str]] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _require(
            tuple(reader.fieldnames or ()) == EXPECTED_FIELDS,
            f"field contract changed for {name}",
        )
        for line_number, row in enumerate(reader, start=2):
            row_count += 1
            _require(
                row["entity"] == EXPECTED_ENTITY,
                f"unexpected entity in {name}:{line_number}",
            )
            _require(
                row["variable"] in spec["variables"],
                f"unreviewed variable in {name}:{line_number}",
            )
            try:
                date.fromisoformat(row["date"])
                value = float(row["value"])
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    f"invalid date/value in {name}:{line_number}"
                ) from exc
            _require(math.isfinite(value), f"non-finite value in {name}:{line_number}")
            _require(
                row["unit"] in {"Percent", "USDollar"},
                f"unexpected unit in {name}:{line_number}",
            )
            _require(
                row["measurementMethod"] == spec["method"],
                f"method drift in {name}:{line_number}",
            )
            _require(
                row["observationPeriod"] == "P1D",
                f"period drift in {name}:{line_number}",
            )
            key = (row["entity"], row["variable"], row["date"])
            _require(
                key not in keys, f"duplicate observation key in {name}:{line_number}"
            )
            keys.add(key)
            variables.add(row["variable"])

    _require(row_count == spec["rows"], f"row count changed for {name}")
    _require(variables == set(spec["variables"]), f"series set changed for {name}")
    return row_count, variables, keys


def _validate_upstream_receipts() -> None:
    upstream = _load_json(DATA_COMMONS_ROOT / "generated-manifest.json")
    _require(
        upstream.get("schema") == "seiche.datacommons-build-manifest.v1",
        "unexpected upstream manifest schema",
    )
    policy = str(upstream.get("source_policy", ""))
    _require(
        "Direct OFR API only" in policy,
        "upstream source policy is no longer direct-OFR only",
    )
    outputs = {Path(item["path"]).name: item for item in upstream.get("outputs", [])}
    _require(set(outputs) == set(SOURCE_SPECS), "upstream outputs changed")
    for name, spec in SOURCE_SPECS.items():
        _require(
            outputs[name].get("row_count") == spec["rows"],
            f"upstream row receipt changed for {name}",
        )
        _require(
            outputs[name].get("sha256") == spec["sha256"],
            f"upstream hash receipt changed for {name}",
        )
    series = upstream.get("series")
    _require(
        isinstance(series, list) and len(series) == EXPECTED_TOTAL_SERIES,
        "upstream series count changed",
    )
    _require(
        {item.get("variable") for item in series} == EXPECTED_VARIABLES,
        "upstream variable allowlist changed",
    )
    for item in series:
        url = str(item.get("source_url", ""))
        _require(
            url.startswith("https://data.financialresearch.gov/v1/series/full?"),
            "non-OFR source URL admitted",
        )
        _require("fred" not in url.lower(), "FRED URL admitted into direct-OFR tranche")

    eligibility_path = DATA_COMMONS_ROOT / "eligibility.csv"
    with eligibility_path.open(encoding="utf-8", newline="") as handle:
        eligibility = list(csv.DictReader(handle))
    candidates = [
        row
        for row in eligibility
        if row.get("status") == "candidate_eligible_for_review"
    ]
    _require(len(candidates) == 2, "candidate eligibility tranche changed")
    candidate_mnemonics = {
        mnemonic
        for row in candidates
        for mnemonic in str(row.get("seiche_mnemonics", "")).split(";")
        if mnemonic
    }
    _require(
        candidate_mnemonics == EXPECTED_MNEMONICS, "eligible mnemonic allowlist changed"
    )
    excluded_statuses = {
        row.get("status") for row in eligibility if row not in candidates
    }
    _require(
        "blocked_fred_terms" in excluded_statuses, "FRED exclusion receipt is missing"
    )
    _require(
        "blocked_semantic_mismatch" in excluded_statuses,
        "primary-dealer mismatch receipt is missing",
    )

    rights = (DATA_COMMONS_ROOT / "RIGHTS_AND_SOURCES.md").read_text(encoding="utf-8")
    for marker in (
        "ten OFR-produced series",
        "Seiche-derived series are ready",
        "No request or CLA action was taken",
        "FRED-backed values",
    ):
        _require(marker in rights, f"rights audit marker is missing: {marker}")


def _validate_metadata() -> None:
    documents = {name: _load_json(KIT_ROOT / name) for name in JSON_DOCUMENTS}
    serialized = {
        name: json.dumps(document, sort_keys=True)
        for name, document in documents.items()
    }
    for name, text in serialized.items():
        _require(
            DOI_PATTERN.search(text) is None, f"real DOI unexpectedly present in {name}"
        )

    manifest = documents["manifest.json"]
    _require(
        manifest.get("schema") == "seiche.research-distribution-manifest.v1",
        "kit manifest schema changed",
    )
    _require(
        manifest.get("publication_status") == "draft_not_submitted",
        "kit is not marked draft/not submitted",
    )
    _require(manifest.get("doi") is None, "kit manifest unexpectedly has a DOI")
    _require(manifest.get("version") == DATASET_VERSION, "kit semantic version changed")
    _require(
        manifest.get("provenance")
        == {
            "metadata_publication": METADATA_PUBLICATION_TREE,
            "source_revision": PINNED_SOURCE_TREE,
        },
        "kit source/publication provenance changed",
    )
    _require(
        manifest.get("record_count") == EXPECTED_TOTAL_ROWS,
        "kit total row receipt changed",
    )
    _require(
        manifest.get("series_count") == EXPECTED_TOTAL_SERIES,
        "kit series receipt changed",
    )
    _require(
        set(manifest.get("series", [])) == EXPECTED_VARIABLES,
        "kit variable allowlist changed",
    )
    source_rows = {row.get("name"): row for row in manifest.get("source_files", [])}
    _require(set(source_rows) == set(SOURCE_SPECS), "kit source file list changed")
    for name, spec in SOURCE_SPECS.items():
        row = source_rows[name]
        _require(
            row.get("sha256") == spec["sha256"], f"kit hash receipt changed for {name}"
        )
        _require(
            row.get("record_count") == spec["rows"],
            f"kit row receipt changed for {name}",
        )
        _require(
            row.get("bytes") == spec["bytes"], f"kit byte receipt changed for {name}"
        )
        _require(
            row.get("content_url") == RAW_PREFIX + name,
            f"unpinned content URL for {name}",
        )
        local = (KIT_ROOT / row.get("local_path", "")).resolve()
        _require(
            local == spec["path"].resolve(),
            f"local source reference changed for {name}",
        )

    kaggle = documents["kaggle/dataset-metadata.json"]
    _require(
        kaggle.get("id") == "seiche-info/seiche-audited-direct-ofr-time-series",
        "Kaggle id changed",
    )
    _require(
        kaggle.get("licenses") == [{"name": "other"}], "Kaggle rights marker changed"
    )
    kaggle_serialized = json.dumps(kaggle).lower()
    _require(
        "draft" not in kaggle_serialized and "not submitted" not in kaggle_serialized,
        "Kaggle upload metadata contains stale publication-state copy",
    )
    kaggle_description = str(kaggle.get("description", ""))
    _require(
        "NOT SUBMITTED" not in kaggle_description, "Kaggle upload description is stale"
    )
    for marker in (
        "NO DOI ASSIGNED",
        "rights-reviewed",
        "not investment advice",
        "Office of Financial Research",
    ):
        _require(
            marker in kaggle_description, f"Kaggle upload marker missing: {marker}"
        )
    _require(
        {item.get("path") for item in kaggle.get("resources", [])} == set(SOURCE_SPECS),
        "Kaggle resources changed",
    )

    croissant = documents["croissant.json"]
    _require(
        croissant.get("conformsTo") == "http://mlcommons.org/croissant/1.0",
        "Croissant version changed",
    )
    _require(
        croissant.get("seiche:publicationStatus") == "draft_not_submitted",
        "Croissant draft marker missing",
    )
    _require(
        croissant.get("name") == "seiche-audited-direct-ofr-research-snapshot",
        "Croissant machine name changed",
    )
    _require(
        croissant.get("datePublished") == "2026-08-21",
        "Croissant publication date changed",
    )
    _require(
        croissant.get("version") == DATASET_VERSION,
        "Croissant semantic version changed",
    )
    _require(
        croissant.get("creator", {}).get("name") == "Seiche",
        "Croissant snapshot creator changed",
    )
    _require(
        croissant.get("provider", {}).get("name") == "Office of Financial Research",
        "Croissant source producer changed",
    )
    cite_as = str(croissant.get("citeAs", ""))
    _require(
        cite_as.startswith("@misc{seiche_direct_ofr_2026,"),
        "Croissant citation changed",
    )
    _require(
        croissant.get("url") == METADATA_PUBLICATION_TREE,
        "Croissant landing page is not the versioned metadata publication",
    )
    _require(
        f"url={{{METADATA_PUBLICATION_TREE}}}" in cite_as,
        "Croissant citation does not resolve to the versioned metadata publication",
    )
    _require(
        croissant.get("seiche:sourceRevision") == PINNED_SOURCE_TREE,
        "Croissant source revision is not commit-pinned",
    )
    context = croissant.get("@context")
    _require(isinstance(context, dict), "Croissant context must be an inline object")
    for term, expected in {
        "conformsTo": "dct:conformsTo",
        "distribution": None,
        "equivalentProperty": "cr:equivalentProperty",
        "fileObject": "cr:fileObject",
        "recordSet": "cr:recordSet",
        "samplingRate": "cr:samplingRate",
        "source": "cr:source",
    }.items():
        actual = context.get(term)
        if expected is not None:
            _require(
                actual == expected, f"Croissant context mapping changed for {term}"
            )
        else:
            _require(
                term not in context,
                f"Croissant schema.org term was unexpectedly overridden: {term}",
            )
    croissant_files = {
        item.get("name"): item for item in croissant.get("distribution", [])
    }
    _require(
        set(croissant_files) == set(SOURCE_SPECS), "Croissant distribution changed"
    )
    record_sets = {item.get("@id"): item for item in croissant.get("recordSet", [])}
    _require(
        set(record_sets) == {"repo_observations", "mmf_observations"},
        "Croissant record sets changed",
    )
    _require(
        "repo_csv" in json.dumps(record_sets["repo_observations"]),
        "Croissant repo extraction is unbound",
    )
    _require(
        "mmf_csv" in json.dumps(record_sets["mmf_observations"]),
        "Croissant MMF extraction is unbound",
    )
    for name, spec in SOURCE_SPECS.items():
        _require(
            croissant_files[name].get("contentUrl") == RAW_PREFIX + name,
            f"Croissant URL changed for {name}",
        )
        _require(
            croissant_files[name].get("sha256") == spec["sha256"],
            f"Croissant hash changed for {name}",
        )

    package = documents["datapackage.json"]
    _require(package.get("profile") == "data-package", "Frictionless profile changed")
    _require(
        package.get("publication_status") == "draft_not_submitted",
        "Frictionless draft marker missing",
    )
    _require(
        package.get("version") == DATASET_VERSION,
        "Frictionless semantic version changed",
    )
    _require(
        package.get("homepage") == METADATA_PUBLICATION_TREE,
        "Frictionless homepage is not version-pinned",
    )
    package_roles = {
        (item.get("title"), item.get("role"))
        for item in package.get("contributors", [])
    }
    _require(
        package_roles
        == {("Seiche", "author"), ("Office of Financial Research", "contributor")},
        "Frictionless creator/source attribution changed",
    )
    resources = {
        Path(item.get("path", "")).name: item for item in package.get("resources", [])
    }
    _require(set(resources) == set(SOURCE_SPECS), "Frictionless resources changed")
    for name, spec in SOURCE_SPECS.items():
        _require(
            resources[name].get("path") == RAW_PREFIX + name,
            f"Frictionless URL changed for {name}",
        )
        _require(
            resources[name].get("hash") == f"sha256:{spec['sha256']}",
            f"Frictionless hash changed for {name}",
        )
        _require(
            resources[name].get("row_count") == spec["rows"],
            f"Frictionless rows changed for {name}",
        )

    dcat = documents["dcat.jsonld"]
    dcat_text = serialized["dcat.jsonld"]
    _require(
        any(node.get("@type") == "dcat:Dataset" for node in dcat.get("@graph", [])),
        "DCAT Dataset missing",
    )
    _require("draft_not_submitted" in dcat_text, "DCAT draft marker missing")
    dcat_datasets = [
        node for node in dcat.get("@graph", []) if node.get("@type") == "dcat:Dataset"
    ]
    _require(len(dcat_datasets) == 1, "DCAT Dataset missing or duplicated")
    dcat_dataset = dcat_datasets[0]
    _require(
        dcat_dataset.get("dcat:version") == DATASET_VERSION,
        "DCAT semantic version changed",
    )
    _require(
        dcat_dataset.get("seiche:seriesCount") == EXPECTED_TOTAL_SERIES
        and dcat_dataset.get("seiche:recordCount") == EXPECTED_TOTAL_ROWS,
        "DCAT series or record receipt changed",
    )
    _require(
        dcat_dataset.get("dct:creator") == {"@id": "https://seiche.info/#organization"},
        "DCAT curator changed",
    )
    _require(
        dcat_dataset.get("dcat:landingPage") == {"@id": METADATA_PUBLICATION_TREE}
        and dcat_dataset.get("seiche:sourceRevision") == {"@id": PINNED_SOURCE_TREE},
        "DCAT source/publication provenance changed",
    )
    dcat_sources = {item.get("@id") for item in dcat_dataset.get("dct:source", [])}
    _require(
        dcat_sources
        == {
            "https://www.financialresearch.gov/short-term-funding-monitor/datasets/repo/",
            "https://www.financialresearch.gov/short-term-funding-monitor/datasets/mmf/",
        },
        "DCAT upstream dataset links changed",
    )
    for name, spec in SOURCE_SPECS.items():
        _require(RAW_PREFIX + name in dcat_text, f"DCAT URL missing for {name}")
        _require(spec["sha256"] in dcat_text, f"DCAT hash missing for {name}")
    dcat_distributions = {
        node.get("@id"): node
        for node in dcat.get("@graph", [])
        if node.get("@type") == "dcat:Distribution"
    }
    expected_distribution_types = {
        "https://seiche.info/datasets/direct-ofr/#repo-csv": "P1D",
        "https://seiche.info/datasets/direct-ofr/#mmf-csv": "P1M",
    }
    _require(
        set(dcat_distributions) == set(expected_distribution_types),
        "DCAT distributions changed",
    )
    for identifier, resolution in expected_distribution_types.items():
        distribution = dcat_distributions[identifier]
        _require(
            distribution.get("dcat:temporalResolution")
            == {"@value": resolution, "@type": "xsd:duration"},
            f"DCAT temporal resolution lost its datatype for {identifier}",
        )
        checksum = distribution.get("spdx:checksum", {}).get("spdx:checksumValue")
        _require(
            isinstance(checksum, dict)
            and checksum.get("@type") == "xsd:hexBinary"
            and checksum.get("@value")
            in {spec["sha256"] for spec in SOURCE_SPECS.values()},
            f"DCAT checksum lost its datatype for {identifier}",
        )

    crate = documents["ro-crate-metadata.json"]
    graph = crate.get("@graph", [])
    _require(
        crate.get("@context") == "https://w3id.org/ro/crate/1.3/context",
        "RO-Crate version changed",
    )
    roots = [
        node
        for node in graph
        if node.get("@id") == "./" and node.get("@type") == "Dataset"
    ]
    _require(len(roots) == 1, "RO-Crate root Dataset missing or duplicated")
    root = roots[0]
    _require(
        root.get("datePublished") == "2026-08-21", "RO-Crate publication date changed"
    )
    _require(
        root.get("version") == DATASET_VERSION, "RO-Crate semantic version changed"
    )
    _require(
        root.get("url") == METADATA_PUBLICATION_TREE,
        "RO-Crate URL is not version-pinned",
    )
    _require(
        "identifier" not in root,
        "RO-Crate draft must not claim a persistent identifier",
    )
    _require(
        root.get("creator") == {"@id": "https://seiche.info/#organization"},
        "RO-Crate curator changed",
    )
    based_on = {item.get("@id") for item in root.get("isBasedOn", [])}
    _require(
        based_on
        == {
            "https://www.financialresearch.gov/short-term-funding-monitor/datasets/repo/",
            "https://www.financialresearch.gov/short-term-funding-monitor/datasets/mmf/",
        },
        "RO-Crate upstream dataset links changed",
    )
    license_id = root.get("license", {}).get("@id")
    _require(
        any(
            node.get("@id") == license_id
            and node.get("@type") == "CreativeWork"
            and node.get("name")
            for node in graph
        ),
        "RO-Crate license entity missing",
    )
    _require(
        "not submitted" in serialized["ro-crate-metadata.json"].lower(),
        "RO-Crate draft marker missing",
    )
    for name, spec in SOURCE_SPECS.items():
        _require(
            RAW_PREFIX + name in serialized["ro-crate-metadata.json"],
            f"RO-Crate URL missing for {name}",
        )
        _require(
            spec["sha256"] in serialized["ro-crate-metadata.json"],
            f"RO-Crate hash missing for {name}",
        )

    datacite = documents["datacite-draft.json"]
    _require(
        datacite.get("publicationStatus") == "draft_not_submitted",
        "DataCite draft marker missing",
    )
    _require(
        datacite.get("doi") is None and datacite.get("url") is None,
        "DataCite identifiers must remain empty",
    )
    _require(
        datacite.get("version") == DATASET_VERSION, "DataCite semantic version changed"
    )
    _require(
        datacite.get("types", {}).get("resourceTypeGeneral") == "Dataset",
        "DataCite resource type changed",
    )
    _require(
        datacite.get("creators") == [{"name": "Seiche", "nameType": "Organizational"}]
        and datacite.get("titles"),
        "DataCite snapshot creator or mandatory title changed",
    )
    _require(
        {
            (item.get("name"), item.get("contributorType"))
            for item in datacite.get("contributors", [])
        }
        == {("Office of Financial Research", "Producer")},
        "DataCite source producer attribution changed",
    )
    datacite_relations = {
        (item.get("relatedIdentifier"), item.get("relationType"))
        for item in datacite.get("relatedIdentifiers", [])
    }
    _require(
        (PINNED_SOURCE_TREE, "IsDerivedFrom") in datacite_relations
        and (METADATA_PUBLICATION_TREE, "IsSupplementTo") in datacite_relations,
        "DataCite source/publication provenance changed",
    )

    card = (KIT_ROOT / "huggingface" / "README.md").read_text(encoding="utf-8")
    _require(
        card.startswith("---\n") and "pretty_name:" in card,
        "Hugging Face card metadata missing",
    )
    frontmatter = card.split("---\n", maxsplit=2)[1]
    license_name_match = re.search(
        r"^license_name:\s*(.*?)\s*$",
        frontmatter,
        re.MULTILINE,
    )
    _require(license_name_match is not None, "Hugging Face license_name missing")
    license_name = license_name_match.group(1)
    _require(
        license_name == HUGGING_FACE_LICENSE_NAME
        and re.fullmatch(r"[a-z0-9-.]+", license_name) is not None,
        "Hugging Face license_name is not a native-schema-safe slug",
    )
    _require(
        "draft, not submitted" not in card.lower(), "Hugging Face upload card is stale"
    )
    for stale_copy in ("proposed dataset", "staged by reference"):
        _require(
            stale_copy not in card.lower(),
            f"Hugging Face upload card retains prospective copy: {stale_copy}",
        )
    link_targets = MARKDOWN_LINK_TARGET_PATTERN.findall(card)
    _require(link_targets, "Hugging Face upload card has no source links")
    for target in link_targets:
        _require(
            target.startswith(("https://", "#")),
            f"Hugging Face upload card has a non-standalone link: {target}",
        )
    for marker in (
        "Public listing status is receipt-tracked",
        "No DOI",
        "num_examples: 11163",
    ):
        _require(marker in card, f"Hugging Face marker missing: {marker}")
    for name, spec in SOURCE_SPECS.items():
        _require(
            name in card and spec["sha256"] in card,
            f"Hugging Face receipt missing for {name}",
        )


def _validate_notebook() -> None:
    notebook = _load_json(NOTEBOOK)
    _require(notebook.get("nbformat") == 4, "notebook is not nbformat 4")
    _require(
        isinstance(notebook.get("cells"), list) and notebook["cells"],
        "notebook has no cells",
    )
    all_source: list[str] = []
    for index, cell in enumerate(notebook["cells"]):
        source = cell.get("source", [])
        _require(
            isinstance(source, (str, list)), f"notebook cell {index} has invalid source"
        )
        all_source.append(source if isinstance(source, str) else "".join(source))
        if cell.get("cell_type") == "code":
            _require(
                cell.get("execution_count") is None,
                f"notebook cell {index} has execution state",
            )
            _require(
                cell.get("outputs") == [], f"notebook cell {index} has hidden output"
            )
            try:
                compile(all_source[-1], f"{NOTEBOOK.name}:cell-{index}", "exec")
            except SyntaxError as exc:
                raise ValidationError(
                    f"notebook cell {index} does not compile: {exc}"
                ) from exc
    text = "\n".join(all_source)
    _require(
        "Authorization:" not in text and "Bearer " not in text,
        "notebook contains an authorization value",
    )
    _require(
        "api_key" not in text.lower() and "access_token" not in text.lower(),
        "notebook contains a credential field",
    )
    _require(
        "draft" in text.lower() and "not submitted" in text.lower(),
        "notebook draft boundary missing",
    )
    _require(EXPECTED_COMMIT in text, "notebook commit pin is missing")
    _require(
        "https://raw.githubusercontent.com/beepboop2025/seiche/" in text
        and "integrations/datacommons/input/observations" in text,
        "notebook raw-data root is missing",
    )
    for name, spec in SOURCE_SPECS.items():
        _require(name in text, f"notebook does not reference {name}")
        _require(spec["sha256"] in text, f"notebook does not verify {name}")


def validate_kit() -> dict[str, Any]:
    """Run every offline rights, integrity, metadata, and notebook check."""

    total_rows = 0
    all_variables: set[str] = set()
    all_keys: set[tuple[str, str, str]] = set()
    for name, spec in SOURCE_SPECS.items():
        rows, variables, keys = _validate_csv(name, spec)
        _require(
            all_keys.isdisjoint(keys), f"duplicate keys cross source boundary in {name}"
        )
        total_rows += rows
        all_variables.update(variables)
        all_keys.update(keys)
    _require(total_rows == EXPECTED_TOTAL_ROWS, "combined row count changed")
    _require(all_variables == EXPECTED_VARIABLES, "combined variable allowlist changed")
    _require(
        len(all_variables) == EXPECTED_TOTAL_SERIES, "combined series count changed"
    )
    _validate_upstream_receipts()
    _validate_metadata()
    _validate_notebook()
    return {
        "status": "valid",
        "publication_status": "draft_not_submitted",
        "doi": None,
        "source": "direct Office of Financial Research only",
        "files": len(SOURCE_SPECS),
        "series": len(all_variables),
        "records": total_rows,
        "observation_data_duplicated": False,
        "platform_upload_payloads": "publication_ready",
    }


def _symlink(target: Path, link: Path) -> None:
    _require(
        not link.exists() and not link.is_symlink(),
        f"staging link already exists: {link}",
    )
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(target, start=link.parent))


def stage_by_reference(destination: Path) -> dict[str, Any]:
    """Create non-destructive publication layouts using symlinks only."""

    report = validate_kit()
    destination = destination.expanduser().resolve()
    _require(
        destination != REPO_ROOT.resolve(), "refusing to stage over the repository root"
    )
    _require(
        destination != Path.home().resolve(),
        "refusing to stage over the home directory",
    )
    if destination.exists():
        _require(
            destination.is_dir(), "staging destination exists and is not a directory"
        )
        _require(not any(destination.iterdir()), "staging destination must be empty")
    else:
        destination.mkdir(parents=True)

    _symlink(
        KIT_ROOT / "huggingface" / "README.md",
        destination / "huggingface" / "README.md",
    )
    _symlink(
        KIT_ROOT / "kaggle" / "dataset-metadata.json",
        destination / "kaggle" / "dataset-metadata.json",
    )
    for name, spec in SOURCE_SPECS.items():
        _symlink(spec["path"], destination / "huggingface" / "data" / name)
        _symlink(spec["path"], destination / "kaggle" / name)
    for name in (
        "manifest.json",
        "croissant.json",
        "datapackage.json",
        "dcat.jsonld",
        "ro-crate-metadata.json",
        "datacite-draft.json",
    ):
        _symlink(KIT_ROOT / name, destination / "metadata" / name)
    return {**report, "staged_at": str(destination), "stage_uses_symlinks": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--validate-only", action="store_true", help="validate without creating files"
    )
    action.add_argument(
        "--stage",
        type=Path,
        metavar="DIRECTORY",
        help="create an empty symlink-only staging tree",
    )
    args = parser.parse_args()
    report = validate_kit() if args.validate_only else stage_by_reference(args.stage)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
