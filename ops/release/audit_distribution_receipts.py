#!/usr/bin/env python3
"""Audit Seiche's mandatory public distribution receipts without mutating them.

The repository version is authoritative. A healthy audit requires the latest
public PyPI project record and the latest official MCP Registry record to expose
that exact version. Optional mirrors and submission directories are deliberately
out of scope: their independent indexing lag must not become a release failure.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import http.client
import json
from pathlib import Path
import re
import tomllib
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[2]
PYPI_PROJECT = "seiche"
MCP_SERVER_NAME = "io.github.beepboop2025/seiche"
PYPI_URL = f"https://pypi.org/pypi/{PYPI_PROJECT}/json"
MCP_REGISTRY_URL = (
    "https://registry.modelcontextprotocol.io/v0.1/servers/"
    f"{urllib.parse.quote(MCP_SERVER_NAME, safe='')}/versions/latest"
)
ALLOWED_FETCH_URLS = frozenset({PYPI_URL, MCP_REGISTRY_URL})
PYPI_ARTIFACT_HOST = "files.pythonhosted.org"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ERROR_BYTES = 512
MAX_JSON_INTEGER_DIGITS = 1024
MAX_JSON_NESTING_DEPTH = 128
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 30.0
USER_AGENT = "seiche-distribution-receipt-auditor/1.0 (+https://seiche.info)"
BARE_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA256 = re.compile(r"[0-9a-f]{64}")
BLAKE2B_256 = re.compile(r"[0-9a-f]{64}")
RFC3339_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


class AuditError(RuntimeError):
    """A local contract or mandatory public receipt is invalid."""


FetchJson = Callable[[str, float], dict[str, Any]]


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the audited endpoint cannot silently move."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_https_opener() -> urllib.request.OpenerDirector:
    """Build a credential-free HTTPS-only opener that ignores proxy settings."""

    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler({}),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        _RejectRedirect(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


_HTTPS_OPENER = _build_https_opener()


def _strict_json(raw: bytes, *, source: str) -> dict[str, Any]:
    """Decode one bounded UTF-8 JSON object and reject duplicate object keys."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AuditError(f"{source} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    def bounded_integer(value: str) -> int:
        if len(value.removeprefix("-")) > MAX_JSON_INTEGER_DIGITS:
            raise AuditError(
                f"{source} contains an integer longer than "
                f"{MAX_JSON_INTEGER_DIGITS} digits"
            )
        return int(value)

    def reject_nonstandard_constant(value: str) -> None:
        raise AuditError(f"{source} contains non-standard JSON constant {value!r}")

    try:
        text = raw.decode("utf-8")
        depth = 0
        in_string = False
        escaped = False
        for character in text:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
            elif character == '"':
                in_string = True
            elif character in "[{":
                depth += 1
                if depth > MAX_JSON_NESTING_DEPTH:
                    raise AuditError(
                        f"{source} exceeds the safe JSON nesting depth of "
                        f"{MAX_JSON_NESTING_DEPTH}"
                    )
            elif character in "]}":
                depth -= 1
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_int=bounded_integer,
            parse_constant=reject_nonstandard_constant,
        )
    except UnicodeDecodeError as exc:
        raise AuditError(f"{source} is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise AuditError(f"{source} is not valid JSON: {exc.msg}") from exc
    except RecursionError as exc:
        raise AuditError(f"{source} exceeds the safe JSON nesting depth") from exc
    except ValueError as exc:
        raise AuditError(f"{source} could not be parsed safely: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"{source} must contain a JSON object")
    return value


def _validate_fetch_url(url: str) -> None:
    """Allow only the two literal mandatory receipt endpoints and origins."""

    if url not in ALLOWED_FETCH_URLS:
        raise AuditError(f"refusing non-allowlisted receipt URL {url!r}")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise AuditError(f"invalid allowlisted receipt URL {url!r}: {exc}") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.hostname not in {"pypi.org", "registry.modelcontextprotocol.io"}
        or parsed.query
        or parsed.fragment
    ):
        raise AuditError(f"refusing invalid receipt origin {url!r}")


def _read_http_error(exc: urllib.error.HTTPError, *, url: str) -> AuditError:
    """Turn a bounded HTTP error response into the auditor's stable error type."""

    if 300 <= exc.code < 400:
        exc.close()
        return AuditError(f"redirect response HTTP {exc.code} from {url} was refused")
    try:
        with exc:
            detail = exc.read(MAX_ERROR_BYTES).decode("utf-8", errors="replace").strip()
    except (http.client.HTTPException, OSError, TimeoutError) as read_exc:
        return AuditError(
            f"HTTP {exc.code} from {url}; could not read the error body: {read_exc}"
        )
    if exc.code == 404:
        return AuditError(f"mandatory public receipt is missing at {url}")
    if "```" in detail:
        detail = "[response body omitted because it contained a Markdown code fence]"
    suffix = f": {detail}" if detail else ""
    return AuditError(f"HTTP {exc.code} from {url}{suffix}")


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    """GET a JSON object through a bounded, direct anonymous request.

    ``timeout`` is urllib's per-blocking-socket-operation timeout. The scheduled
    workflow supplies the independent overall wall-clock bound.
    """

    _validate_fetch_url(url)

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with _HTTPS_OPENER.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status != 200:
                raise AuditError(f"{url} returned HTTP status {status!r}, expected 200")
            geturl = getattr(response, "geturl", None)
            final_url = geturl() if callable(geturl) else None
            if final_url != url:
                raise AuditError(
                    f"{url} returned final URL {final_url!r}; exact equality required"
                )
            headers = getattr(response, "headers", None)
            get_content_type = getattr(headers, "get_content_type", None)
            get_all = getattr(headers, "get_all", None)
            if not callable(get_content_type) or not callable(get_all):
                raise AuditError(f"{url} returned malformed response headers")
            try:
                content_types = get_all("Content-Type", [])
                content_encodings = get_all("Content-Encoding", [])
                media_type = get_content_type()
            except (TypeError, ValueError) as exc:
                raise AuditError(f"{url} returned malformed response headers") from exc
            if len(content_types) != 1 or len(content_encodings) > 1:
                raise AuditError(f"{url} returned ambiguous response headers")
            content_encoding = content_encodings[0] if content_encodings else None
            if media_type != "application/json":
                raise AuditError(f"{url} returned media type {media_type!r}, not JSON")
            if content_encoding is not None:
                if not isinstance(content_encoding, str):
                    raise AuditError(f"{url} returned malformed response headers")
                content_encoding = content_encoding.strip().lower()
            if content_encoding not in {None, "identity"}:
                raise AuditError(
                    f"{url} returned unsupported encoding {content_encoding!r}"
                )
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _read_http_error(exc, url=url) from exc
    except (
        http.client.HTTPException,
        urllib.error.URLError,
        OSError,
        TimeoutError,
    ) as exc:
        raise AuditError(f"could not read {url}: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AuditError(f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
    return _strict_json(raw, source=url)


def _read_local_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        if path.is_symlink() or not path.is_file():
            raise AuditError(f"{path} must be a regular repository file")
        raw = path.read_bytes()
    except OSError as exc:
        raise AuditError(f"could not read {path}: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AuditError(f"{path} exceeds {MAX_RESPONSE_BYTES} bytes")
    return _strict_json(raw, source=str(path)), raw


def load_local_contract(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Load and cross-check the repository's canonical distribution identity."""

    root = repo_root.resolve()
    server, server_raw = _read_local_json(root / "server.json")
    project_path = root / "backend" / "pyproject.toml"
    try:
        if project_path.is_symlink() or not project_path.is_file():
            raise AuditError(f"{project_path} must be a regular repository file")
        project_document = tomllib.loads(project_path.read_text(encoding="utf-8"))
        project = project_document["project"]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise AuditError(f"could not parse {project_path}: {exc}") from exc
    except (KeyError, TypeError) as exc:
        raise AuditError(f"{project_path} has no valid [project] table") from exc

    project_name = project.get("name")
    version = project.get("version")
    if project_name != PYPI_PROJECT:
        raise AuditError(
            f"backend project name is {project_name!r}; expected {PYPI_PROJECT!r}"
        )
    if not isinstance(version, str) or BARE_SEMVER.fullmatch(version) is None:
        raise AuditError(
            f"repository version is not bare semantic version: {version!r}"
        )
    if server.get("name") != MCP_SERVER_NAME:
        raise AuditError(
            f"server.json name is {server.get('name')!r}; expected {MCP_SERVER_NAME!r}"
        )
    if server.get("version") != version:
        raise AuditError("server.json version differs from backend/pyproject.toml")
    packages = server.get("packages")
    if not isinstance(packages, list) or len(packages) != 1:
        raise AuditError("server.json must declare one exact package")
    package = packages[0]
    if not isinstance(package, dict):
        raise AuditError("server.json package must be an object")
    expected_package = {
        "registryType": "pypi",
        "identifier": PYPI_PROJECT,
        "version": version,
    }
    for key, expected in expected_package.items():
        if package.get(key) != expected:
            raise AuditError(
                f"server.json package {key} is {package.get(key)!r}; expected {expected!r}"
            )
    return {
        "project": PYPI_PROJECT,
        "version": version,
        "mcp_server": MCP_SERVER_NAME,
        "server": server,
        "server_json_sha256": hashlib.sha256(server_raw).hexdigest(),
    }


def _normalized_project_name(value: Any) -> str:
    return re.sub(r"[-_.]+", "-", str(value)).lower()


def _validate_pypi_artifact_url(
    artifact_url: str, *, filename: str, blake2b_256: str
) -> None:
    """Require Warehouse's canonical, credential-free hashed artifact URL."""

    try:
        parsed = urllib.parse.urlsplit(artifact_url)
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except (UnicodeError, ValueError) as exc:
        raise AuditError(f"PyPI {filename} has an invalid artifact URL: {exc}") from exc

    canonical_path = re.fullmatch(
        rf"/packages/([0-9a-f]{{2}})/([0-9a-f]{{2}})/([0-9a-f]{{60}})/"
        rf"{re.escape(filename)}",
        parsed.path,
    )
    if (
        artifact_url != urllib.parse.urlunsplit(parsed)
        or parsed.scheme != "https"
        or parsed.netloc != PYPI_ARTIFACT_HOST
        or hostname != PYPI_ARTIFACT_HOST
        or port is not None
        or username is not None
        or password is not None
        or parsed.query
        or parsed.fragment
        or "?" in artifact_url
        or "#" in artifact_url
        or canonical_path is None
    ):
        raise AuditError(f"PyPI {filename} has a non-canonical artifact URL")
    path_digest = "".join(canonical_path.groups())
    if path_digest != blake2b_256:
        raise AuditError(
            f"PyPI {filename} URL path differs from its BLAKE2b-256 receipt"
        )


def _validate_pypi_inventory(
    entries: Any, *, version: str, label: str
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise AuditError(f"PyPI {label} is not a file array")
    expected_types = {
        f"seiche-{version}-py3-none-any.whl": "bdist_wheel",
        f"seiche-{version}.tar.gz": "sdist",
    }
    receipt: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise AuditError(f"PyPI {label} contains a malformed file entry")
        filename = entry.get("filename")
        if filename not in expected_types or filename in seen:
            raise AuditError(f"PyPI {label} has foreign or duplicate file {filename!r}")
        seen.add(filename)
        if entry.get("packagetype") != expected_types[filename]:
            raise AuditError(f"PyPI {filename} has the wrong package type")
        if entry.get("yanked") is not False:
            raise AuditError(f"PyPI {filename} is yanked or has no yanked status")
        digests = entry.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise AuditError(f"PyPI {filename} has no valid SHA-256 receipt")
        blake2b_256 = digests.get("blake2b_256") if isinstance(digests, dict) else None
        if (
            not isinstance(blake2b_256, str)
            or BLAKE2B_256.fullmatch(blake2b_256) is None
        ):
            raise AuditError(f"PyPI {filename} has no valid BLAKE2b-256 receipt")
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise AuditError(f"PyPI {filename} has no positive byte size")
        artifact_url = entry.get("url")
        if not isinstance(artifact_url, str):
            raise AuditError(f"PyPI {filename} has no artifact URL")
        _validate_pypi_artifact_url(
            artifact_url,
            filename=filename,
            blake2b_256=blake2b_256,
        )
        receipt.append(
            {
                "filename": filename,
                "packagetype": expected_types[filename],
                "sha256": digest,
                "blake2b_256": blake2b_256,
                "size": size,
                "url": artifact_url,
            }
        )
    if seen != set(expected_types):
        missing = sorted(set(expected_types) - seen)
        raise AuditError(f"PyPI {label} is missing canonical files: {missing}")
    return sorted(receipt, key=lambda item: item["filename"])


def audit_pypi(
    contract: dict[str, Any], *, timeout: float, fetch_json: FetchJson = _fetch_json
) -> dict[str, Any]:
    """Verify the latest PyPI identity and its immutable two-file inventory."""

    payload = fetch_json(PYPI_URL, timeout)
    info = payload.get("info")
    if not isinstance(info, dict):
        raise AuditError("PyPI latest project record has no info object")
    if _normalized_project_name(info.get("name")) != contract["project"]:
        raise AuditError(
            f"PyPI project name is {info.get('name')!r}; expected {contract['project']!r}"
        )
    if info.get("version") != contract["version"]:
        raise AuditError(
            "PyPI latest version is "
            f"{info.get('version')!r}; repository requires {contract['version']!r}"
        )
    latest = _validate_pypi_inventory(
        payload.get("urls"), version=contract["version"], label="latest inventory"
    )
    releases = payload.get("releases")
    if not isinstance(releases, dict):
        raise AuditError("PyPI latest project record has no releases object")
    release = _validate_pypi_inventory(
        releases.get(contract["version"]),
        version=contract["version"],
        label=f"release {contract['version']}",
    )
    latest_identity = [
        (
            item["filename"],
            item["sha256"],
            item["blake2b_256"],
            item["size"],
            item["url"],
        )
        for item in latest
    ]
    release_identity = [
        (
            item["filename"],
            item["sha256"],
            item["blake2b_256"],
            item["size"],
            item["url"],
        )
        for item in release
    ]
    if latest_identity != release_identity:
        raise AuditError("PyPI latest and version-indexed artifact receipts differ")
    return {
        "surface": "pypi",
        "status": "pass",
        "url": PYPI_URL,
        "project": contract["project"],
        "version": contract["version"],
        "artifacts": latest,
    }


_INPUT_FALSE_DEFAULTS = frozenset({"isRequired", "isSecret"})


def _normalize_mcp_input(
    value: Any, *, argument: bool = False, allow_variables: bool = False
) -> Any:
    """Normalize defaults only on schema-defined Input and Argument objects."""

    normalized = deepcopy(value)
    if not isinstance(normalized, dict):
        return normalized
    defaults = _INPUT_FALSE_DEFAULTS | ({"isRepeated"} if argument else set())
    for key in defaults:
        if normalized.get(key) is False:
            normalized.pop(key)
    if allow_variables:
        _normalize_mcp_input_mapping(normalized, "variables")
    return normalized


def _normalize_mcp_input_mapping(parent: dict[str, Any], key: str) -> None:
    inputs = parent.get(key)
    if isinstance(inputs, dict):
        parent[key] = {
            name: _normalize_mcp_input(value) for name, value in inputs.items()
        }


def _normalize_mcp_input_list(
    parent: dict[str, Any], key: str, *, argument: bool = False
) -> None:
    inputs = parent.get(key)
    if isinstance(inputs, list):
        parent[key] = [
            _normalize_mcp_input(
                value,
                argument=argument,
                allow_variables=True,
            )
            for value in inputs
        ]


def _normalize_mcp_transport(transport: Any) -> None:
    if isinstance(transport, dict):
        _normalize_mcp_input_list(transport, "headers")


def _normalize_mcp_manifest(value: Any) -> Any:
    """Mirror schema defaults omitted from exact Registry server cards.

    False defaults are ignored only on Input objects at package environment,
    transport-header, and remote-variable paths, plus ``isRepeated`` on the two
    package argument arrays. Identically named fields anywhere else remain part
    of the exact manifest identity.
    """

    normalized = deepcopy(value)
    if not isinstance(normalized, dict):
        return normalized
    packages = normalized.get("packages")
    if isinstance(packages, list):
        for package in packages:
            if not isinstance(package, dict):
                continue
            _normalize_mcp_input_list(package, "environmentVariables")
            _normalize_mcp_input_list(package, "runtimeArguments", argument=True)
            _normalize_mcp_input_list(package, "packageArguments", argument=True)
            _normalize_mcp_transport(package.get("transport"))
    remotes = normalized.get("remotes")
    if isinstance(remotes, list):
        for remote in remotes:
            if not isinstance(remote, dict):
                continue
            _normalize_mcp_input_list(remote, "headers")
            _normalize_mcp_input_mapping(remote, "variables")
    return normalized


def _parse_rfc3339_timestamp(value: Any, *, field: str) -> str:
    """Validate a timezone-aware RFC 3339 timestamp without normalizing it."""

    if not isinstance(value, str) or RFC3339_TIMESTAMP.fullmatch(value) is None:
        raise AuditError(
            f"official MCP Registry {field} is not a timezone-aware RFC 3339 timestamp"
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (OverflowError, ValueError) as exc:
        raise AuditError(
            f"official MCP Registry {field} is not a valid RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuditError(f"official MCP Registry {field} has no timezone offset")
    return value


def audit_mcp_registry(
    contract: dict[str, Any], *, timeout: float, fetch_json: FetchJson = _fetch_json
) -> dict[str, Any]:
    """Verify the latest active official Registry record and exact server card."""

    payload = fetch_json(MCP_REGISTRY_URL, timeout)
    server = payload.get("server")
    if not isinstance(server, dict):
        raise AuditError("official MCP Registry response has no server object")
    if server.get("name") != contract["mcp_server"]:
        raise AuditError(
            f"official MCP Registry name is {server.get('name')!r}; "
            f"expected {contract['mcp_server']!r}"
        )
    if server.get("version") != contract["version"]:
        raise AuditError(
            "official MCP Registry latest version is "
            f"{server.get('version')!r}; repository requires {contract['version']!r}"
        )
    metadata = payload.get("_meta")
    if not isinstance(metadata, dict):
        raise AuditError("official MCP Registry response has no metadata object")
    official = metadata.get("io.modelcontextprotocol.registry/official")
    if not isinstance(official, dict):
        raise AuditError("MCP Registry response has no official receipt metadata")
    if official.get("status") != "active":
        raise AuditError(
            f"official MCP Registry status is {official.get('status')!r}, not 'active'"
        )
    if official.get("isLatest") is not True:
        raise AuditError("official MCP Registry record is not marked latest")
    status_changed_at = _parse_rfc3339_timestamp(
        official.get("statusChangedAt"), field="statusChangedAt"
    )
    published_at = _parse_rfc3339_timestamp(
        official.get("publishedAt"), field="publishedAt"
    )
    updated_at = None
    if "updatedAt" in official:
        updated_at = _parse_rfc3339_timestamp(
            official.get("updatedAt"), field="updatedAt"
        )
    if _normalize_mcp_manifest(server) != _normalize_mcp_manifest(contract["server"]):
        raise AuditError("official MCP Registry server card differs from server.json")
    receipt = {
        "surface": "official_mcp_registry",
        "status": "pass",
        "url": MCP_REGISTRY_URL,
        "name": contract["mcp_server"],
        "version": contract["version"],
        "official_status": "active",
        "is_latest": True,
        "status_changed_at": status_changed_at,
        "published_at": published_at,
        "server_json_sha256": contract["server_json_sha256"],
    }
    if updated_at is not None:
        receipt["updated_at"] = updated_at
    return receipt


def audit_distribution_receipts(
    *,
    repo_root: Path = REPO_ROOT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    fetch_json: FetchJson = _fetch_json,
) -> dict[str, Any]:
    """Return a complete pass/fail report for both mandatory public receipts."""

    try:
        contract = load_local_contract(repo_root)
    except AuditError as exc:
        return {
            "schema": "seiche.distribution-receipts.audit.v1",
            "status": "fail",
            "expected": None,
            "receipts": [],
            "errors": [{"surface": "repository", "error": str(exc)}],
        }

    expected = {
        "project": contract["project"],
        "version": contract["version"],
        "mcp_server": contract["mcp_server"],
        "server_json_sha256": contract["server_json_sha256"],
    }
    checks = (
        ("pypi", audit_pypi),
        ("official_mcp_registry", audit_mcp_registry),
    )
    receipts: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for surface, check in checks:
        try:
            receipts.append(check(contract, timeout=timeout, fetch_json=fetch_json))
        except AuditError as exc:
            errors.append({"surface": surface, "error": str(exc)})
    return {
        "schema": "seiche.distribution-receipts.audit.v1",
        "status": "fail" if errors else "pass",
        "expected": expected,
        "receipts": receipts,
        "errors": errors,
    }


def _bounded_timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0 < parsed <= MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}"
        )
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="GET and validate Seiche's mandatory public release receipts."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository checkout containing server.json (default: script checkout)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_bounded_timeout,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"per-request timeout, bounded to {MAX_TIMEOUT_SECONDS:g} seconds",
    )
    args = parser.parse_args()
    report = audit_distribution_receipts(
        repo_root=args.repo_root,
        timeout=args.timeout_seconds,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
