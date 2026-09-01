#!/usr/bin/env python3
"""Fail closed before publishing versioned AI-catalog release pointers.

The repository can land a release candidate on ``main`` before the signed tag,
runtime cutover, and immutable PyPI files exist.  Static publication must not
turn that normal staging interval into a dangling public package reference.
This verifier binds each catalog server to its own signed release tag, then
proves the corresponding runtimes and immutable package receipts are public.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MCP_NAME = "io.github.beepboop2025/seiche"
MCP_ENTRY = "urn:air:seiche.info:mcp:funding-stress"
MCP_URL = "https://api.seiche.info/mcp"
PYPI_PROJECT = "seiche"
AI_CATALOG_PATH = "frontend/public/.well-known/ai-catalog.json"
MARKET_CORPUS_ENTRY = "urn:air:seiche.info:mcp:market-corpus"
MARKET_CORPUS_NAME = "io.github.beepboop2025/seiche-market-corpus"
MARKET_CORPUS_MCP_URL = "https://api.seiche.info/api/v2/corpus/mcp"
MARKET_CORPUS_CATALOG_URL = "https://api.seiche.info/api/v2/corpus/v1/catalog"
MARKET_CORPUS_HEALTH_URL = "https://api.seiche.info/api/v2/corpus/healthz?deep=true"
MARKET_CORPUS_DISCOVERY_URL = "https://api.seiche.info/.well-known/mcp.json"
MARKET_CORPUS_TAG_PREFIX = "market-corpus-v"
MARKET_CORPUS_RECEIPT_TAG_PREFIX = "market-corpus-receipt-"
MARKET_CORPUS_RECEIPT_REVISION = "r9"
MARKET_CORPUS_EXPECTED_FLOWS = 29
MARKET_CORPUS_EXPECTED_BULK_FLAT_FLOWS = 27
MARKET_CORPUS_EXPECTED_API_ONLY_FLOWS = 1
MARKET_CORPUS_EXPECTED_REGISTRY_ONLY_FLOWS = 1
MARKET_CORPUS_EXPECTED_AGGREGATE_ROWS = 76_342_888
MARKET_CORPUS_EXPECTED_ENGINE_DATASETS = 1_122
MARKET_CORPUS_EXPECTED_ENGINE_VERIFIED_OBJECTS = 1_110
MARKET_CORPUS_EXPECTED_ENGINE_ATTEMPTS = 1_118
MARKET_CORPUS_EXPECTED_ENGINE_RECOVERED_OBJECTS = 8
MARKET_CORPUS_TOOLS = (
    "corpus_catalog",
    "list_datasets",
    "inspect_dataset",
    "bis_observations",
    "bis_records",
    "bis_flow_manifest",
    "seiche_markets",
    "seiche_observations",
    "corpus_health",
)
INDEPENDENT_CATALOG_ENTRIES = frozenset({MARKET_CORPUS_ENTRY})
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
CORPUS_RELEASE_RE = re.compile(r"corpus-[0-9a-f]{16}")
CORPUS_RECEIPT_TAG_RE = re.compile(
    r"market-corpus-receipt-corpus-[0-9a-f]{16}-r[1-9][0-9]*"
)

# The catalog is a multi-server envelope, so it is checked semantically below:
# every entry present in the signed tag remains semantically identical, while
# independently versioned servers may be added without republishing Seiche's
# immutable Python package.
RELEASE_IDENTITY_PATHS = (
    "backend/README.md",
    "backend/pyproject.toml",
    "backend/seiche/assemble.py",
    "server.json",
)


class PublicationGateError(RuntimeError):
    """The catalog cannot yet be published truthfully."""


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise PublicationGateError(f"release input is not a regular file: {path}")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise PublicationGateError(f"release input is too large: {path}")
    return _load_json_bytes(raw, label=str(path))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_number(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_json_bytes(raw: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PublicationGateError(f"{label} is not strict UTF-8 JSON") from exc


def _json_identity(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PublicationGateError("catalog contains a non-JSON value") from exc


def _catalog_entries_by_identifier(catalog: Any) -> dict[str, dict[str, Any]]:
    entries = catalog.get("entries") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        raise PublicationGateError("AI catalog has no entry inventory")
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PublicationGateError("AI catalog contains a non-object entry")
        identifier = entry.get("identifier")
        if not isinstance(identifier, str) or not identifier:
            raise PublicationGateError("AI catalog entry has no identifier")
        if identifier in indexed:
            raise PublicationGateError("AI catalog contains a duplicate identifier")
        indexed[identifier] = entry
    return indexed


def _canonical_catalog_entry(catalog: Any) -> dict[str, Any]:
    entries = _catalog_entries_by_identifier(catalog)
    if MCP_ENTRY not in entries:
        raise PublicationGateError(
            "AI catalog does not contain one canonical MCP entry"
        )
    return entries[MCP_ENTRY]


def _market_corpus_catalog_entry(catalog: Any) -> dict[str, Any]:
    entries = _catalog_entries_by_identifier(catalog)
    if MARKET_CORPUS_ENTRY not in entries:
        raise PublicationGateError("AI catalog omits the Market Atlas corpus server")
    return entries[MARKET_CORPUS_ENTRY]


def _catalog_identifier_order(catalog: Any) -> list[str]:
    _catalog_entries_by_identifier(catalog)
    return [entry["identifier"] for entry in catalog["entries"]]


def _validate_market_corpus_position(catalog: Any) -> int:
    identifiers = _catalog_identifier_order(catalog)
    try:
        canonical_position = identifiers.index(MCP_ENTRY)
        corpus_position = identifiers.index(MARKET_CORPUS_ENTRY)
    except ValueError as exc:
        raise PublicationGateError(
            "Market Atlas catalog placement is malformed"
        ) from exc
    if corpus_position != canonical_position + 1:
        raise PublicationGateError(
            "Market Atlas catalog entry is not immediately after canonical MCP"
        )
    return corpus_position


def _validate_market_corpus_entry(entry: Any) -> str:
    if not isinstance(entry, dict) or entry.get("identifier") != MARKET_CORPUS_ENTRY:
        raise PublicationGateError("Market Atlas catalog identity is malformed")
    version = entry.get("version")
    data = entry.get("data")
    metadata = entry.get("metadata")
    capabilities = entry.get("capabilities")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise PublicationGateError("Market Atlas catalog version is malformed")
    if not isinstance(data, dict) or not isinstance(metadata, dict):
        raise PublicationGateError("Market Atlas catalog metadata is malformed")
    if (
        entry.get("type") != "application/json"
        or data.get("name") != MARKET_CORPUS_NAME
        or data.get("version") != version
        or data.get("websiteUrl") != "https://seiche.info/#corpus"
        or data.get("remotes")
        != [{"type": "streamable-http", "url": MARKET_CORPUS_MCP_URL}]
        or capabilities != list(MARKET_CORPUS_TOOLS)
        or entry.get("prompts") != []
        or entry.get("resourceTemplates") != []
        or metadata.get("authentication") != "none"
        or metadata.get("access") != "public-read-only-rights-gated"
        or metadata.get("publicToolCount") != len(MARKET_CORPUS_TOOLS)
        or not _zero_int(metadata.get("publicPromptCount"))
        or not _zero_int(metadata.get("publicResourceCount"))
        or metadata.get("catalog") != MARKET_CORPUS_CATALOG_URL
        or metadata.get("health") != MARKET_CORPUS_HEALTH_URL
        or metadata.get("humanPage") != "https://seiche.info/#corpus"
        or metadata.get("availabilityClaim")
        != "declared_endpoint_verify_with_corpus_health"
        or metadata.get("requestTimeMonolithScan") is not False
    ):
        raise PublicationGateError("Market Atlas catalog contract is inconsistent")
    return version


def _market_corpus_publication_receipt(entry: Any) -> dict[str, Any]:
    metadata = entry.get("metadata") if isinstance(entry, dict) else None
    encoded = metadata.get("publicationReceipt") if isinstance(metadata, dict) else None
    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise PublicationGateError(
            "Market Atlas signed publication receipt is malformed"
        )
    receipt = _load_json_bytes(
        encoded.encode("utf-8"), label="Market Atlas signed publication receipt"
    )
    expected_keys = {
        "schemaVersion",
        "tag",
        "releaseId",
        "indexSha256",
        "indexArtifactId",
        "inventorySha256",
        "bisFlows",
        "bisBulkFlat",
        "bisApiOnly",
        "bisRegistryOnly",
        "bisAggregateRows",
        "engineDatasets",
        "engineVerifiedObjects",
        "engineAttempts",
        "engineRecoveredObjects",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != expected_keys
        or encoded.encode("utf-8") != _json_identity(receipt)
    ):
        raise PublicationGateError(
            "Market Atlas signed publication receipt is malformed"
        )
    release_id = receipt.get("releaseId")
    index_sha256 = receipt.get("indexSha256")
    artifact_id = receipt.get("indexArtifactId")
    if not (
        receipt.get("schemaVersion") == "1.0.0"
        and isinstance(release_id, str)
        and CORPUS_RELEASE_RE.fullmatch(release_id) is not None
        and receipt.get("tag")
        == (
            f"{MARKET_CORPUS_RECEIPT_TAG_PREFIX}{release_id}-"
            f"{MARKET_CORPUS_RECEIPT_REVISION}"
        )
        and CORPUS_RECEIPT_TAG_RE.fullmatch(str(receipt.get("tag") or "")) is not None
        and isinstance(index_sha256, str)
        and SHA256_RE.fullmatch(index_sha256) is not None
        and artifact_id
        == f"liquilens-engine-public-index-v1:{release_id}:{index_sha256}"
        and SHA256_RE.fullmatch(str(receipt.get("inventorySha256") or "")) is not None
        and _exact_int(receipt.get("bisFlows"), MARKET_CORPUS_EXPECTED_FLOWS)
        and _exact_int(
            receipt.get("bisBulkFlat"),
            MARKET_CORPUS_EXPECTED_BULK_FLAT_FLOWS,
        )
        and _exact_int(
            receipt.get("bisApiOnly"),
            MARKET_CORPUS_EXPECTED_API_ONLY_FLOWS,
        )
        and _exact_int(
            receipt.get("bisRegistryOnly"),
            MARKET_CORPUS_EXPECTED_REGISTRY_ONLY_FLOWS,
        )
        and receipt["bisFlows"]
        == receipt["bisBulkFlat"] + receipt["bisApiOnly"] + receipt["bisRegistryOnly"]
        and _exact_int(
            receipt.get("bisAggregateRows"),
            MARKET_CORPUS_EXPECTED_AGGREGATE_ROWS,
        )
        and _exact_int(
            receipt.get("engineDatasets"),
            MARKET_CORPUS_EXPECTED_ENGINE_DATASETS,
        )
        and _exact_int(
            receipt.get("engineVerifiedObjects"),
            MARKET_CORPUS_EXPECTED_ENGINE_VERIFIED_OBJECTS,
        )
        and _exact_int(
            receipt.get("engineAttempts"),
            MARKET_CORPUS_EXPECTED_ENGINE_ATTEMPTS,
        )
        and _exact_int(
            receipt.get("engineRecoveredObjects"),
            MARKET_CORPUS_EXPECTED_ENGINE_RECOVERED_OBJECTS,
        )
    ):
        raise PublicationGateError(
            "Market Atlas signed publication receipt is inconsistent"
        )
    return receipt


def _without_market_corpus_publication_receipt(entry: dict[str, Any]) -> Any:
    projected = _load_json_bytes(
        _json_identity(entry), label="Market Atlas catalog identity"
    )
    metadata = projected.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("publicationReceipt", None)
    return projected


def _verify_market_corpus_version_tagged_identity(
    current_catalog: Any, tagged_catalog: Any
) -> dict[str, Any]:
    current_entry = _market_corpus_catalog_entry(current_catalog)
    tagged_entry = _market_corpus_catalog_entry(tagged_catalog)
    _validate_market_corpus_entry(current_entry)
    _validate_market_corpus_entry(tagged_entry)
    current_position = _validate_market_corpus_position(current_catalog)
    tagged_position = _validate_market_corpus_position(tagged_catalog)
    if (
        _json_identity(_without_market_corpus_publication_receipt(current_entry))
        != _json_identity(_without_market_corpus_publication_receipt(tagged_entry))
        or current_position != tagged_position
    ):
        raise PublicationGateError(
            "Market Atlas catalog identity or placement differs from its signed version tag"
        )
    return current_entry


def _verify_market_corpus_tagged_identity(
    current_catalog: Any, tagged_catalog: Any
) -> dict[str, Any]:
    current_entry = _market_corpus_catalog_entry(current_catalog)
    tagged_entry = _market_corpus_catalog_entry(tagged_catalog)
    _validate_market_corpus_entry(current_entry)
    _validate_market_corpus_entry(tagged_entry)
    current_position = _validate_market_corpus_position(current_catalog)
    tagged_position = _validate_market_corpus_position(tagged_catalog)
    if (
        _json_identity(current_entry) != _json_identity(tagged_entry)
        or current_position != tagged_position
    ):
        raise PublicationGateError(
            "Market Atlas catalog identity or placement differs from its signed release tag"
        )
    return current_entry


def _read_tagged_json(root: Path, tag: str, path: str) -> Any:
    object_name = f"{tag}:{path}"
    size_text = _run_git_bytes(root, "cat-file", "-s", object_name).stdout.strip()
    try:
        size = int(size_text)
    except ValueError as exc:
        raise PublicationGateError("signed release catalog size is malformed") from exc
    if size < 0 or size > MAX_JSON_BYTES:
        raise PublicationGateError("signed release catalog is too large")
    raw = _run_git_bytes(root, "show", object_name).stdout
    if len(raw) != size:
        raise PublicationGateError("signed release catalog size changed while reading")
    return _load_json_bytes(raw, label="signed release catalog")


def _verify_catalog_release_entries(root: Path, tagged_catalog: Any) -> None:
    current_catalog = _read_json(root / AI_CATALOG_PATH)
    if not isinstance(current_catalog, dict) or not isinstance(tagged_catalog, dict):
        raise PublicationGateError("AI catalog release envelope is malformed")
    current_envelope = {
        key: value for key, value in current_catalog.items() if key != "entries"
    }
    tagged_envelope = {
        key: value for key, value in tagged_catalog.items() if key != "entries"
    }
    if _json_identity(current_envelope) != _json_identity(tagged_envelope):
        raise PublicationGateError("AI catalog release envelope differs from the tag")
    current_entries = _catalog_entries_by_identifier(current_catalog)
    tagged_entries = _catalog_entries_by_identifier(tagged_catalog)
    current_frozen = [
        (entry["identifier"], _json_identity(entry))
        for entry in current_catalog["entries"]
        if entry["identifier"] not in INDEPENDENT_CATALOG_ENTRIES
    ]
    tagged_frozen = [
        (entry["identifier"], _json_identity(entry))
        for entry in tagged_catalog["entries"]
        if entry["identifier"] not in INDEPENDENT_CATALOG_ENTRIES
    ]
    unknown = set(current_entries).difference(
        tagged_entries, INDEPENDENT_CATALOG_ENTRIES
    )
    if unknown or current_frozen != tagged_frozen:
        raise PublicationGateError("signed AI catalog entries differ from the tag")
    _validate_market_corpus_entry(_market_corpus_catalog_entry(current_catalog))


def verify_local_identity(root: Path) -> tuple[str, dict[str, Any]]:
    server = _read_json(root / "server.json")
    catalog = _read_json(root / AI_CATALOG_PATH)
    if not isinstance(server, dict) or server.get("name") != MCP_NAME:
        raise PublicationGateError("server.json has the wrong MCP identity")
    version = server.get("version")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise PublicationGateError("server.json has a non-canonical release version")

    packages = server.get("packages")
    if not isinstance(packages, list):
        raise PublicationGateError("server.json has no package inventory")
    pypi = [
        package
        for package in packages
        if isinstance(package, dict)
        and package.get("registryType") == "pypi"
        and package.get("identifier") == PYPI_PROJECT
    ]
    if len(pypi) != 1 or pypi[0].get("version") != version:
        raise PublicationGateError("server.json does not bind one exact PyPI package")

    entry = _canonical_catalog_entry(catalog)
    if entry.get("data") != server or entry.get("version") != version:
        raise PublicationGateError(
            "AI catalog and server.json release identities differ"
        )
    _validate_market_corpus_entry(_market_corpus_catalog_entry(catalog))
    _validate_market_corpus_position(catalog)

    capabilities = entry.get("capabilities")
    prompts = entry.get("prompts")
    resource_templates = entry.get("resourceTemplates")
    metadata = entry.get("metadata")
    if not all(
        isinstance(value, list) for value in (capabilities, prompts, resource_templates)
    ):
        raise PublicationGateError("AI catalog omits a public MCP inventory")
    if not isinstance(metadata, dict) or (
        metadata.get("publicToolCount") != len(capabilities)
        or metadata.get("publicPromptCount") != len(prompts)
        or metadata.get("publicResourceCount") != len(resource_templates)
    ):
        raise PublicationGateError("AI catalog MCP inventory counts are inconsistent")

    pyproject_path = root / "backend/pyproject.toml"
    if pyproject_path.is_symlink() or not pyproject_path.is_file():
        raise PublicationGateError("backend/pyproject.toml is not a regular file")
    if pyproject_path.stat().st_size > MAX_JSON_BYTES:
        raise PublicationGateError("backend/pyproject.toml is too large")
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PublicationGateError("backend/pyproject.toml is invalid") from exc
    if pyproject.get("project", {}).get("name") != PYPI_PROJECT or (
        pyproject.get("project", {}).get("version") != version
    ):
        raise PublicationGateError("package and server release identities differ")

    readme_path = root / "backend/README.md"
    if readme_path.is_symlink() or not readme_path.is_file():
        raise PublicationGateError("backend/README.md is not a regular file")
    if readme_path.stat().st_size > MAX_JSON_BYTES:
        raise PublicationGateError("backend/README.md is too large")
    try:
        readme_lines = readme_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PublicationGateError("backend/README.md is not UTF-8") from exc
    if f"mcp-name: {MCP_NAME}" not in readme_lines:
        raise PublicationGateError(
            "published package README lacks the MCP ownership proof"
        )
    return version, entry


def _run_git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and result.returncode != 0:
        raise PublicationGateError(f"git {' '.join(args)} failed")
    return result


def _run_git_bytes(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise PublicationGateError(f"git {' '.join(args)} failed")
    return result


def _signing_git_config(root: Path, signer_fingerprint: str) -> str:
    allowed = root / "ops/deploy/release-allowed-signers"
    if allowed.is_symlink() or not allowed.is_file():
        raise PublicationGateError("release allowed-signers trust root is unsafe")
    fingerprints = []
    for line in subprocess.run(
        ["ssh-keygen", "-E", "sha256", "-lf", str(allowed)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            fingerprints.append(fields[1])
    if fingerprints != [signer_fingerprint]:
        raise PublicationGateError(
            "repository signer trust root differs from the workflow pin"
        )
    return f"gpg.ssh.allowedSignersFile={allowed}"


def _verify_annotated_signed_tag(
    root: Path, *, tag: str, head: str, git_config: str
) -> str:
    if _run_git(root, "cat-file", "-t", tag).stdout.strip() != "tag":
        raise PublicationGateError(f"release identity is not an annotated tag: {tag}")
    target = _run_git(root, "rev-parse", f"{tag}^{{commit}}").stdout.strip()
    if COMMIT_RE.fullmatch(target) is None:
        raise PublicationGateError(f"release tag target is malformed: {tag}")
    if _run_git(
        root, "merge-base", "--is-ancestor", target, head, check=False
    ).returncode:
        raise PublicationGateError(f"release tag is not an ancestor: {tag}")
    _run_git(root, "-c", git_config, "verify-commit", target)
    _run_git(root, "-c", git_config, "verify-tag", tag)
    return target


def _verify_worktree_identity(root: Path, expected_sha: str) -> None:
    diff = _run_git(
        root,
        "diff",
        "--quiet",
        expected_sha,
        "--",
        AI_CATALOG_PATH,
        *RELEASE_IDENTITY_PATHS,
        check=False,
    )
    if diff.returncode == 1:
        raise PublicationGateError("release identity working tree differs from HEAD")
    if diff.returncode != 0:
        raise PublicationGateError(
            "release identity working tree could not be verified"
        )
    source = root / AI_CATALOG_PATH
    committed = _run_git_bytes(root, "show", f"{expected_sha}:{AI_CATALOG_PATH}").stdout
    if source.is_symlink() or not source.is_file() or source.read_bytes() != committed:
        raise PublicationGateError("AI catalog source bytes differ from HEAD")


def verify_published_catalog(root: Path, published_path: Path) -> None:
    source = root / AI_CATALOG_PATH
    candidate = (
        published_path if published_path.is_absolute() else root / published_path
    )
    if candidate.is_symlink() or not candidate.is_file():
        raise PublicationGateError("published catalog is not a regular file")
    published = candidate.resolve()
    try:
        published.relative_to(root)
    except ValueError as exc:
        raise PublicationGateError(
            "published catalog escapes the release root"
        ) from exc
    source_bytes = source.read_bytes()
    published_bytes = published.read_bytes()
    if (
        len(published_bytes) > MAX_JSON_BYTES
        or source_bytes != published_bytes
        or _json_identity(_load_json_bytes(published_bytes, label=str(published)))
        != _json_identity(_read_json(source))
    ):
        raise PublicationGateError("published catalog bytes differ from source")


def verify_signed_release(
    root: Path, *, version: str, expected_sha: str, signer_fingerprint: str
) -> str:
    if COMMIT_RE.fullmatch(expected_sha) is None:
        raise PublicationGateError("expected publication SHA is malformed")
    if FINGERPRINT_RE.fullmatch(signer_fingerprint) is None:
        raise PublicationGateError("release signer fingerprint is malformed")
    head = _run_git(root, "rev-parse", "HEAD").stdout.strip()
    if head != expected_sha:
        raise PublicationGateError("publication checkout differs from the workflow SHA")
    _verify_worktree_identity(root, expected_sha)

    tag = f"v{version}"
    git_config = _signing_git_config(root, signer_fingerprint)
    target = _verify_annotated_signed_tag(
        root, tag=tag, head=head, git_config=git_config
    )
    diff = _run_git(
        root,
        "diff",
        "--quiet",
        target,
        head,
        "--",
        *RELEASE_IDENTITY_PATHS,
        check=False,
    )
    if diff.returncode == 1:
        raise PublicationGateError("release identity files differ from the signed tag")
    if diff.returncode != 0:
        raise PublicationGateError("release identity diff could not be verified")
    _verify_catalog_release_entries(root, _read_tagged_json(root, tag, AI_CATALOG_PATH))

    return tag


def verify_market_corpus_release(
    root: Path, *, expected_sha: str, signer_fingerprint: str
) -> tuple[str, dict[str, Any]]:
    catalog = _read_json(root / AI_CATALOG_PATH)
    entry = _market_corpus_catalog_entry(catalog)
    version = _validate_market_corpus_entry(entry)
    tag = f"{MARKET_CORPUS_TAG_PREFIX}{version}"
    git_config = _signing_git_config(root, signer_fingerprint)
    _verify_annotated_signed_tag(
        root, tag=tag, head=expected_sha, git_config=git_config
    )
    tagged_catalog = _read_tagged_json(root, tag, AI_CATALOG_PATH)
    _verify_market_corpus_version_tagged_identity(catalog, tagged_catalog)

    receipt = _market_corpus_publication_receipt(entry)
    receipt_tag = receipt["tag"]
    receipt_target = _verify_annotated_signed_tag(
        root, tag=receipt_tag, head=expected_sha, git_config=git_config
    )
    if receipt_target != expected_sha:
        raise PublicationGateError(
            "Market Atlas publication receipt tag does not target the workflow SHA"
        )
    receipt_catalog = _read_tagged_json(root, receipt_tag, AI_CATALOG_PATH)
    _verify_market_corpus_tagged_identity(catalog, receipt_catalog)
    return receipt_tag, entry


def _request_bytes(
    request: urllib.request.Request, *, max_bytes: int, expected_host: str
) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname != expected_host:
                raise PublicationGateError(
                    f"publication receipt redirected off {expected_host}"
                )
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > max_bytes:
                raise PublicationGateError("publication receipt exceeds its size limit")
            body = response.read(max_bytes + 1)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise PublicationGateError(
            f"publication receipt fetch failed: {request.full_url}"
        ) from exc
    if len(body) > max_bytes:
        raise PublicationGateError("publication receipt exceeds its size limit")
    return body


def _open_bytes(url: str, *, max_bytes: int, expected_host: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "seiche-publication-gate/1",
        },
    )
    return _request_bytes(request, max_bytes=max_bytes, expected_host=expected_host)


def _fetch_json(url: str, *, expected_host: str) -> Any:
    body = _open_bytes(url, max_bytes=MAX_JSON_BYTES, expected_host=expected_host)
    return _load_json_bytes(body, label=f"publication receipt {url}")


def _post_json(url: str, payload: Any, *, expected_host: str) -> Any:
    body = _json_identity(payload)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "seiche-publication-gate/1",
        },
    )
    response = _request_bytes(
        request, max_bytes=MAX_JSON_BYTES, expected_host=expected_host
    )
    return _load_json_bytes(response, label=f"publication receipt {url}")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _zero_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _exact_int(value: Any, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def verify_market_corpus_receipts(
    entry: dict[str, Any],
    *,
    fetch_json: Callable[..., Any] = _fetch_json,
    post_json: Callable[..., Any] = _post_json,
) -> dict[str, Any]:
    version = _validate_market_corpus_entry(entry)
    signed = _market_corpus_publication_receipt(entry)
    health = fetch_json(MARKET_CORPUS_HEALTH_URL, expected_host="api.seiche.info")
    release_id = health.get("release_id") if isinstance(health, dict) else None
    checks = health.get("checks") if isinstance(health, dict) else None
    deep = checks.get("deep") if isinstance(checks, dict) else None
    all_flow = deep.get("bis_all_flow_receipt") if isinstance(deep, dict) else None
    deep_engine = deep.get("engine_index") if isinstance(deep, dict) else None
    if not (
        isinstance(health, dict)
        and health.get("schema_version") == "1.0.0"
        and health.get("service") == "liquilens-market-corpus"
        and health.get("status") == "ok"
        and release_id == signed["releaseId"]
        and isinstance(deep, dict)
        and deep.get("ok") is True
        and _exact_int(deep.get("bis_flows"), signed["bisFlows"])
        and _exact_int(deep.get("datasets"), signed["engineDatasets"])
        and isinstance(all_flow, dict)
        and all_flow.get("status") == "complete"
        and _exact_int(
            all_flow.get("expected_count"),
            signed["bisBulkFlat"],
        )
        and _exact_int(
            all_flow.get("materialized_count"),
            signed["bisBulkFlat"],
        )
        and _zero_int(all_flow.get("error_count"))
        and _exact_int(all_flow.get("aggregate_row_count"), signed["bisAggregateRows"])
        and _exact_int(
            all_flow.get("sampled_shard_count"),
            signed["bisBulkFlat"],
        )
        and SHA256_RE.fullmatch(str(all_flow.get("sha256") or "")) is not None
        and isinstance(deep_engine, dict)
        and deep_engine.get("artifact_id") == signed["indexArtifactId"]
        and deep_engine.get("index_sha256") == signed["indexSha256"]
        and _exact_int(deep_engine.get("attempt_count"), signed["engineAttempts"])
        and _exact_int(deep_engine.get("object_count"), signed["engineVerifiedObjects"])
        and _exact_int(
            deep_engine.get("recovered_object_count"),
            signed["engineRecoveredObjects"],
        )
        and _zero_int(deep_engine.get("unresolved_object_count"))
    ):
        raise PublicationGateError(
            "Market Atlas runtime is not deeply healthy and fully materialized"
        )

    catalog = fetch_json(MARKET_CORPUS_CATALOG_URL, expected_host="api.seiche.info")
    corpora = catalog.get("corpora") if isinstance(catalog, dict) else None
    engine = corpora.get("liquilens_engine") if isinstance(corpora, dict) else None
    bis = corpora.get("bis") if isinstance(corpora, dict) else None
    seiche = corpora.get("seiche") if isinstance(corpora, dict) else None
    if not (
        isinstance(catalog, dict)
        and catalog.get("schema_version") == "1.0.0"
        and catalog.get("service") == "liquilens-market-corpus"
        and catalog.get("release_id") == release_id
        and catalog.get("index_sha256") == signed["indexSha256"]
        and catalog.get("index_sha256") == deep_engine.get("index_sha256")
        and catalog.get("index_artifact_id") == signed["indexArtifactId"]
        and catalog.get("index_artifact_id") == deep_engine.get("artifact_id")
        and isinstance(engine, dict)
        and _exact_int(engine.get("datasets"), signed["engineDatasets"])
        and engine.get("datasets") == deep.get("datasets")
        and _exact_int(engine.get("verified_objects"), signed["engineVerifiedObjects"])
        and engine.get("verified_objects") == deep_engine.get("object_count")
        and _exact_int(engine.get("attempts"), signed["engineAttempts"])
        and _exact_int(
            engine.get("successful_attempts"), signed["engineVerifiedObjects"]
        )
        and _exact_int(
            engine.get("failed_attempts"),
            signed["engineAttempts"] - signed["engineVerifiedObjects"],
        )
        and _exact_int(
            engine.get("recovered_objects"), signed["engineRecoveredObjects"]
        )
        and _zero_int(engine.get("unresolved_objects"))
        and engine.get("unresolved_objects")
        == deep_engine.get("unresolved_object_count")
        and isinstance(bis, dict)
        and _exact_int(bis.get("flows"), signed["bisFlows"])
        and bis.get("flows") == deep.get("bis_flows")
        and _exact_int(bis.get("bulk_flat"), signed["bisBulkFlat"])
        and _exact_int(bis.get("api_only"), signed["bisApiOnly"])
        and _exact_int(bis.get("registry_only"), signed["bisRegistryOnly"])
        and bis.get("flows")
        == bis.get("bulk_flat") + bis.get("api_only") + bis.get("registry_only")
        and bis.get("bulk_flat") == all_flow.get("expected_count")
        and bis.get("inventory_sha256") == signed["inventorySha256"]
        and bis.get("inventory_sha256") == deep.get("bis_inventory_sha256")
        and isinstance(seiche, dict)
        and seiche.get("status") == "ok"
        and _positive_int(seiche.get("market_count"))
        and _positive_int(seiche.get("source_count"))
    ):
        raise PublicationGateError(
            "Market Atlas catalog differs from its deep-health release"
        )

    discovery = fetch_json(MARKET_CORPUS_DISCOVERY_URL, expected_host="api.seiche.info")
    servers = discovery.get("servers") if isinstance(discovery, dict) else None
    discovery_matches = [
        server
        for server in servers or []
        if isinstance(server, dict) and server.get("name") == MARKET_CORPUS_NAME
    ]
    if len(discovery_matches) != 1 or not (
        discovery_matches[0].get("version") == version
        and discovery_matches[0].get("transport") == "streamable-http"
        and discovery_matches[0].get("url") == MARKET_CORPUS_MCP_URL
        and discovery_matches[0].get("availability")
        == entry["metadata"]["availabilityClaim"]
        and discovery_matches[0].get("health") == MARKET_CORPUS_HEALTH_URL
    ):
        raise PublicationGateError(
            "Market Atlas public discovery differs from its signed catalog"
        )

    request_id = "market-corpus-publication-proof"
    tools = post_json(
        MARKET_CORPUS_MCP_URL,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/list",
            "params": {},
        },
        expected_host="api.seiche.info",
    )
    result = tools.get("result") if isinstance(tools, dict) else None
    tool_rows = result.get("tools") if isinstance(result, dict) else None
    tool_names = (
        [row.get("name") for row in tool_rows if isinstance(row, dict)]
        if isinstance(tool_rows, list)
        else None
    )
    if not (
        isinstance(tools, dict)
        and tools.get("jsonrpc") == "2.0"
        and tools.get("id") == request_id
        and "error" not in tools
        and tool_names == list(MARKET_CORPUS_TOOLS)
        and len(tool_rows) == len(MARKET_CORPUS_TOOLS)
    ):
        raise PublicationGateError(
            "Market Atlas MCP tools differ from the signed catalog"
        )

    return {
        "version": version,
        "releaseId": release_id,
        "catalogSha256": catalog["index_sha256"],
        "allFlowReceiptSha256": all_flow["sha256"],
        "datasets": engine["datasets"],
        "bisFlows": bis["flows"],
        "bisRows": all_flow["aggregate_row_count"],
        "tools": tool_names,
    }


def verify_public_receipts(
    version: str,
    *,
    fetch_json: Callable[..., Any] = _fetch_json,
    fetch_bytes: Callable[..., bytes] = _open_bytes,
) -> dict[str, Any]:
    quoted = urllib.parse.quote(version, safe="")
    pypi = fetch_json(
        f"https://pypi.org/pypi/{PYPI_PROJECT}/{quoted}/json",
        expected_host="pypi.org",
    )
    if not isinstance(pypi, dict) or pypi.get("info", {}).get("name") != PYPI_PROJECT:
        raise PublicationGateError("PyPI receipt has the wrong project identity")
    if pypi.get("info", {}).get("version") != version:
        raise PublicationGateError("PyPI receipt has the wrong release version")
    urls = pypi.get("urls")
    if not isinstance(urls, list) or len(urls) != 2:
        raise PublicationGateError(
            "PyPI receipt must contain exactly two distributions"
        )

    expected = {
        f"seiche-{version}-py3-none-any.whl": "bdist_wheel",
        f"seiche-{version}.tar.gz": "sdist",
    }
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact in urls:
        if not isinstance(artifact, dict):
            raise PublicationGateError("PyPI receipt contains a malformed distribution")
        filename = artifact.get("filename")
        if filename not in expected or filename in seen:
            raise PublicationGateError(
                "PyPI receipt contains a foreign or duplicate file"
            )
        if (
            artifact.get("packagetype") != expected[filename]
            or artifact.get("yanked") is not False
        ):
            raise PublicationGateError(
                "PyPI distribution type or yanked state is invalid"
            )
        digest = artifact.get("digests", {}).get("sha256")
        size = artifact.get("size")
        url = artifact.get("url")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise PublicationGateError("PyPI distribution lacks a canonical SHA-256")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise PublicationGateError("PyPI distribution lacks a positive byte size")
        if not isinstance(url, str):
            raise PublicationGateError("PyPI distribution lacks an artifact URL")
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "files.pythonhosted.org"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise PublicationGateError("PyPI distribution URL is not canonical")
        body = fetch_bytes(
            url, max_bytes=MAX_ARTIFACT_BYTES, expected_host="files.pythonhosted.org"
        )
        if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            raise PublicationGateError(
                "PyPI distribution bytes differ from their receipt"
            )
        seen.add(filename)
        artifacts.append({"filename": filename, "sha256": digest, "size": size})
    if set(expected) != seen:
        raise PublicationGateError("PyPI receipt is missing a canonical distribution")

    health = fetch_json(
        f"https://api.seiche.info/api/health?release={quoted}",
        expected_host="api.seiche.info",
    )
    health_version = health.get("version") if isinstance(health, dict) else None
    if (
        not isinstance(health_version, str)
        or health_version.split(maxsplit=1)[0] != version
    ):
        raise PublicationGateError(
            "public runtime has not activated the release version"
        )
    if health.get("faults") != []:
        raise PublicationGateError("public runtime is not strictly fault-free")

    discovery = fetch_json(
        f"https://api.seiche.info/.well-known/mcp.json?release={quoted}",
        expected_host="api.seiche.info",
    )
    servers = discovery.get("servers") if isinstance(discovery, dict) else None
    matches = [
        server
        for server in servers or []
        if isinstance(server, dict) and server.get("name") == MCP_NAME
    ]
    if len(matches) != 1 or not (
        matches[0].get("version") == version
        and matches[0].get("url") == MCP_URL
        and matches[0].get("status") == "active"
    ):
        raise PublicationGateError("public MCP discovery has not activated the release")

    return {
        "version": version,
        "artifacts": sorted(artifacts, key=lambda item: item["filename"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--signer-fingerprint", required=True)
    parser.add_argument("--published-catalog", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        version, _entry = verify_local_identity(root)
        tag = verify_signed_release(
            root,
            version=version,
            expected_sha=args.expected_sha,
            signer_fingerprint=args.signer_fingerprint,
        )
        corpus_tag, corpus_entry = verify_market_corpus_release(
            root,
            expected_sha=args.expected_sha,
            signer_fingerprint=args.signer_fingerprint,
        )
        receipt = verify_public_receipts(version)
        corpus_receipt = verify_market_corpus_receipts(corpus_entry)
        if args.published_catalog is not None:
            verify_published_catalog(root, args.published_catalog)
    except (PublicationGateError, OSError, subprocess.SubprocessError) as exc:
        print(f"catalog publication blocked: {exc}", file=sys.stderr)
        return 1
    receipt.update(
        {
            "releaseTag": tag,
            "revision": args.expected_sha,
            "independentCatalogReleases": [
                {"identifier": MARKET_CORPUS_ENTRY, "tag": corpus_tag, **corpus_receipt}
            ],
        }
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
