#!/usr/bin/env python3
"""Fail closed before publishing versioned AI-catalog release pointers.

The repository can land a release candidate on ``main`` before the signed tag,
runtime cutover, and immutable PyPI files exist.  Static publication must not
turn that normal staging interval into a dangling public package reference.
This verifier binds the catalog to the signed release tag, then proves the
corresponding runtime and both PyPI distributions are already public.
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
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")

RELEASE_IDENTITY_PATHS = (
    "backend/README.md",
    "backend/pyproject.toml",
    "backend/seiche/assemble.py",
    "frontend/public/.well-known/ai-catalog.json",
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
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationGateError(f"release input is not UTF-8 JSON: {path}") from exc


def verify_local_identity(root: Path) -> tuple[str, dict[str, Any]]:
    server = _read_json(root / "server.json")
    catalog = _read_json(root / "frontend/public/.well-known/ai-catalog.json")
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

    entries = catalog.get("entries") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        raise PublicationGateError("AI catalog has no entry inventory")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("identifier") == MCP_ENTRY
    ]
    if len(matches) != 1:
        raise PublicationGateError(
            "AI catalog does not contain one canonical MCP entry"
        )
    entry = matches[0]
    if entry.get("data") != server or entry.get("version") != version:
        raise PublicationGateError(
            "AI catalog and server.json release identities differ"
        )

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
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PublicationGateError("backend/pyproject.toml is invalid") from exc
    if pyproject.get("project", {}).get("name") != PYPI_PROJECT or (
        pyproject.get("project", {}).get("version") != version
    ):
        raise PublicationGateError("package and server release identities differ")
    if (
        f"mcp-name: {MCP_NAME}"
        not in (root / "backend/README.md").read_text(encoding="utf-8").splitlines()
    ):
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

    tag = f"v{version}"
    if _run_git(root, "cat-file", "-t", tag).stdout.strip() != "tag":
        raise PublicationGateError("release identity is not an annotated tag")
    target = _run_git(root, "rev-parse", f"{tag}^{{commit}}").stdout.strip()
    if COMMIT_RE.fullmatch(target) is None:
        raise PublicationGateError("release tag target is malformed")
    if _run_git(
        root, "merge-base", "--is-ancestor", target, head, check=False
    ).returncode:
        raise PublicationGateError(
            "release tag is not an ancestor of the publication SHA"
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

    git_config = f"gpg.ssh.allowedSignersFile={allowed}"
    _run_git(root, "-c", git_config, "verify-commit", target)
    _run_git(root, "-c", git_config, "verify-tag", tag)
    return tag


def _open_bytes(url: str, *, max_bytes: int, expected_host: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "seiche-publication-gate/1",
        },
    )
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
        raise PublicationGateError(f"publication receipt fetch failed: {url}") from exc
    if len(body) > max_bytes:
        raise PublicationGateError("publication receipt exceeds its size limit")
    return body


def _fetch_json(url: str, *, expected_host: str) -> Any:
    body = _open_bytes(url, max_bytes=MAX_JSON_BYTES, expected_host=expected_host)
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationGateError(
            f"publication receipt is not UTF-8 JSON: {url}"
        ) from exc


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
        receipt = verify_public_receipts(version)
    except (PublicationGateError, OSError, subprocess.SubprocessError) as exc:
        print(f"catalog publication blocked: {exc}", file=sys.stderr)
        return 1
    receipt.update({"releaseTag": tag, "revision": args.expected_sha})
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
