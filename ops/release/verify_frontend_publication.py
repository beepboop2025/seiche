#!/usr/bin/env python3
"""Authenticate a frontend-only publication without claiming runtime activation.

An immutable SSH-signed annotated tag authorizes the exact source tree. Backend
and corpus receipts retain their original signed subjects and live checks. This
contract is deliberately separate from the application's descendant fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request


def _load_gate():
    source = Path(__file__).with_name("verify_catalog_publication.py")
    spec = importlib.util.spec_from_file_location("catalog_publication_gate", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()
Error = gate.PublicationGateError
SCHEMA = "seiche.frontend-publication.v1"
PURPOSE = "frontend_only_no_runtime_activation"
TAG_PREFIX = "frontend-publication-"

# These files cannot be imported by the frontend build, or by postprocessing:
# the latter runs from the immutable backend release archive, never this tree.
# Isolated CI and controller files are not copied into the frontend-only archive
# or the immutable backend postprocessor archive. Controllers are deployed from
# separately reviewed signed images; this receipt does not activate them.
# Keep exact paths: this is not a general operations/runtime exception.
EXCLUDED_MONITOR_PATHS = frozenset(
    {
        ".github/workflows/market-platform-ci.yml",
        "deploy/railway-ci/editorial-controller/Dockerfile",
        "deploy/railway-ci/editorial-controller/README.md",
        "deploy/railway-ci/editorial-controller/editorial.py",
        "deploy/railway-ci/editorial-controller/isolation.py",
        "deploy/railway-ci/editorial-controller/prepare.py",
        "deploy/railway-ci/editorial-controller/requirements.lock",
        "deploy/railway-ci/editorial-controller/test_editorial.py",
        "backend/scripts/ard_coverage.py",
        ".github/workflows/distribution-contracts.yml",
        "ops/railway-automation/Dockerfile",
        "ops/railway-automation/Dockerfile.dockerignore",
        "ops/railway-automation/README.md",
        "ops/railway-automation/run.sh",
        "ops/railway-automation/Dockerfile.distribution",
        "ops/railway-automation/Dockerfile.distribution.dockerignore",
        "ops/railway-automation/distribution.py",
        "ops/railway-automation/publisher/Dockerfile",
        "ops/railway-automation/publisher/README.md",
        "ops/railway-automation/publisher/assemble.py",
        "ops/railway-automation/publisher/github-known-hosts",
        "ops/railway-automation/publisher/publish.py",
        "ops/railway-automation/publisher/test_publish.py",
        "ops/railway-automation/market-contracts/Dockerfile",
        "ops/railway-automation/market-contracts/Dockerfile.dockerignore",
        "ops/railway-automation/market-contracts/run.sh",
        "ops/railway-automation/market-contracts/README.md",
        "ops/railway-automation/full-publisher/Dockerfile",
        "ops/railway-automation/full-publisher/publish.py",
        "ops/railway-automation/full-publisher/test_publish.py",
        "ops/railway-automation/full-publisher/README.md",
        "ops/railway/fetch_recovery_logs.py",
        "ops/railway/test_fetch_recovery_logs.py",
        "backend/tests/test_railway_stateful_recovery.py",
        "backend/tests/test_tide_session_alignment.py",
        "deploy/railway-ci/recovery-controller/Dockerfile",
        "deploy/railway-ci/recovery-controller/README.md",
        "deploy/railway-ci/recovery-controller/native_docker.py",
        "deploy/railway-ci/recovery-controller/prepare.py",
        "deploy/railway-ci/recovery-controller/requirements.lock",
        "deploy/railway-ci/recovery-controller/restore.sh",
        "deploy/railway-ci/recovery-controller/test_verify.py",
        "deploy/railway-ci/recovery-controller/verify.py",
        "deploy/railway-ci/recovery-controller/recurring.py",
        "deploy/railway-ci/recovery-controller/attest.py",
        "deploy/railway-ci/recovery-controller/test_attest.py",
        "deploy/railway-ci/recovery-controller/test_recurring.py",
        "deploy/railway-ci/recovery-monitor/Dockerfile",
        "deploy/railway-ci/recovery-monitor/README.md",
        "deploy/railway-ci/recovery-monitor/monitor.py",
        "deploy/railway-ci/recovery-monitor/prepare.py",
        "deploy/railway-ci/recovery-monitor/requirements.in",
        "deploy/railway-ci/recovery-monitor/requirements.lock",
        "deploy/railway-ci/recovery-monitor/test_monitor.py",
        ".github/workflows/railway-stateful-recovery.yml",
    }
)
RETIRED_HANDOFF_PATH = ".github/workflows/recovery-monitor-handoff.yml"
CONTROLLER_PATHS = frozenset(
    {
        "ops/release/verify_frontend_publication.py",
        "ops/release/frontend_site_proof.py",
        ".github/workflows/publish-static.yml",
        "backend/tests/test_catalog_publication_gate.py",
    }
)
FRONTEND_PATH = re.compile(
    r"frontend/src/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:ts|tsx|css)"
)
TEST_PATH = re.compile(r"frontend/tests/[A-Za-z0-9_.-]+\.(?:mjs|json)")
DOC_PATH = re.compile(r"docs/[A-Za-z0-9_.-]+\.md")
DESK_PATH = re.compile(
    r"(?:frontend/public/(?:dispatches|articles)/[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:md|json)"
    r"|backend/seiche/dispatches/(?:[A-Za-z0-9][A-Za-z0-9_.-]*\.desk\.md"
    r"|state\.json|weekly_state\.json|odds_ledger\.jsonl))"
)
REGULAR_CHANGE = re.compile(
    rb":(?:100644 100644 [0-9a-f]{40} [0-9a-f]{40} M"
    rb"|000000 100644 0{40} [0-9a-f]{40} A"
    rb"|100644 000000 [0-9a-f]{40} 0{40} D)"
)


def _git(root: Path, *args: str) -> str:
    return gate._run_git(root, *args).stdout.strip()


def _blob(root: Path, revision: str, relative: str) -> bytes:
    return gate._run_git_bytes(root, "show", f"{revision}:{relative}").stdout


def _canonical(payload: dict) -> bytes:
    return gate._json_identity(payload) + b"\n"


def _assert_clean(root: Path, expected_sha: str) -> None:
    if gate.COMMIT_RE.fullmatch(expected_sha) is None:
        raise Error("frontend source SHA is malformed")
    if _git(root, "rev-parse", "HEAD") != expected_sha:
        raise Error("frontend checkout differs from the expected source SHA")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise Error("frontend tracked working tree is dirty")
    # The verifier must also come from that exact source, including clean files
    # hidden with Git assume-unchanged/skip-worktree flags.
    for relative in (
        "ops/release/verify_catalog_publication.py",
        "ops/release/verify_frontend_publication.py",
        "ops/release/frontend_site_proof.py",
        "ops/deploy/release-allowed-signers",
    ):
        target = root / relative
        if (
            target.is_symlink()
            or not target.is_file()
            or target.read_bytes() != _blob(root, expected_sha, relative)
        ):
            raise Error(f"frontend verification input differs from source: {relative}")
    flags = gate._run_git_bytes(root, "ls-files", "-v", "-z").stdout.split(b"\0")
    if any(row and not row.startswith(b"H ") for row in flags):
        raise Error("frontend checkout hides tracked inputs with index flags")
    untracked = gate._run_git_bytes(
        root, "ls-files", "--others", "--exclude-standard", "-z"
    ).stdout.split(b"\0")
    if any(
        row.startswith((b"frontend/", b"backend/", b"ops/release/", b".github/"))
        for row in untracked
    ):
        raise Error("frontend checkout contains untracked build or verification inputs")


def _desk_tree(root: Path, revision: str) -> tuple[bytes, ...]:
    """Identity of the complete generated desk snapshot, including file modes."""
    entries = gate._run_git_bytes(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        revision,
        "--",
        "frontend/public/dispatches",
        "frontend/public/articles",
        "backend/seiche/dispatches",
    ).stdout.split(b"\0")
    selected = []
    for entry in entries:
        if not entry:
            continue
        try:
            relative = entry.split(b"\t", 1)[1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise Error("frontend history contains an unsupported path") from exc
        if DESK_PATH.fullmatch(relative):
            selected.append(entry)
    return tuple(selected)


def compatibility_changes(root: Path, release: str, source: str) -> list[dict]:
    """Check every commit, including reverted edits and both sides of merges."""
    if any(gate.COMMIT_RE.fullmatch(value) is None for value in (release, source)):
        raise Error("frontend compatibility SHA is malformed")
    if gate._run_git(
        root, "merge-base", "--is-ancestor", release, source, check=False
    ).returncode:
        raise Error("frontend source is not a descendant of its backend release")
    changes = []
    desk_origins: dict[str, frozenset[str]] = {release: frozenset()}
    for commit in _git(
        root, "rev-list", "--reverse", "--topo-order", f"{release}..{source}"
    ).splitlines():
        parents, author, subject = _git(
            root,
            "show",
            "-s",
            "--no-show-signature",
            "--format=%P%x00%ae%x00%s",
            commit,
        ).split("\0")
        parent_list = parents.split()
        if not parent_list or any(
            gate._run_git(
                root, "merge-base", "--is-ancestor", release, parent, check=False
            ).returncode
            for parent in parent_list
        ):
            raise Error("frontend history merges an unrelated release ancestry")
        # Every side-branch commit is also visited by rev-list. Comparing each
        # parent additionally checks merge conflict resolutions and tree modes.
        origins = frozenset().union(*(desk_origins[parent] for parent in parent_list))
        # A merge may inherit one complete, already validated desk snapshot.
        # The chosen parent must contain every desk-origin commit from all
        # parents: choosing an older snapshot or combining divergent histories
        # is not a frontend publication authorization.
        inherited_desk = None
        if len(parent_list) > 1:
            snapshot = _desk_tree(root, commit)
            inherited_desk = next(
                (
                    parent
                    for parent in parent_list
                    if desk_origins[parent] == origins
                    and _desk_tree(root, parent) == snapshot
                ),
                None,
            )
        commit_changes = 0
        has_desk_change = False
        for parent in parent_list:
            raw = gate._run_git_bytes(
                root,
                "diff-tree",
                "--raw",
                "-r",
                "-z",
                "--no-renames",
                "--no-commit-id",
                "--no-abbrev",
                parent,
                commit,
                "--",
            ).stdout.split(b"\0")
            if raw == [b""]:
                continue
            if len(raw) % 2 != 1 or raw[-1] != b"":
                raise Error("frontend history has an invalid change list")
            for metadata, path_bytes in zip(raw[:-1:2], raw[1:-1:2]):
                if REGULAR_CHANGE.fullmatch(metadata) is None:
                    raise Error(
                        "frontend history changes a symlink, executable or nonregular file"
                    )
                try:
                    path = path_bytes.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise Error(
                        "frontend history contains an unsupported path"
                    ) from exc
                if any(part in {"", ".", ".."} for part in path.split("/")):
                    raise Error("frontend history contains an unsafe path")
                if path == "frontend/index.html" or FRONTEND_PATH.fullmatch(path):
                    kind = "frontend"
                elif TEST_PATH.fullmatch(path) or DOC_PATH.fullmatch(path):
                    kind = "review_only"
                elif path in CONTROLLER_PATHS:
                    kind = "publication_controller"
                elif path == RETIRED_HANDOFF_PATH:
                    # The owner reviews its signed add/remove history in this receipt;
                    # the one-time workflow must no longer exist in the published source.
                    if (
                        gate._run_git(
                            root, "cat-file", "-e", f"{source}:{path}", check=False
                        ).returncode
                        == 0
                    ):
                        raise Error(
                            "frontend history retains a forbidden one-time handoff workflow"
                        )
                    kind = "retired_handoff"
                elif path in EXCLUDED_MONITOR_PATHS:
                    kind = "excluded_monitor"
                elif DESK_PATH.fullmatch(path):
                    if len(parent_list) == 1:
                        authorized = (
                            author == "desk@seiche.info"
                            and subject.startswith(("dispatch: ", "week ahead: "))
                        )
                    else:
                        authorized = inherited_desk is not None
                    if not authorized:
                        raise Error(
                            "frontend history contains unauthorized generated evidence"
                        )
                    kind = "excluded_desk_content"
                    has_desk_change = True
                else:
                    raise Error(
                        f"frontend history changes a forbidden runtime, build, catalog or data path: {path}"
                    )
                changes.append(
                    {
                        "commit": commit,
                        "parent": parent,
                        "path": path,
                        "kind": kind,
                        "change": metadata.decode("ascii"),
                    }
                )
                commit_changes += 1
        if not commit_changes:
            raise Error("frontend history contains an empty release commit")
        desk_origins[commit] = origins | (
            {commit} if len(parent_list) == 1 and has_desk_change else set()
        )
    if not any(change["kind"] == "frontend" for change in changes):
        raise Error("frontend publication contains no frontend changes")
    return changes


def prepare_receipt(
    root: Path, *, expected_sha: str, signer_fingerprint: str
) -> tuple[dict, list[dict]]:
    """Return unsigned review inputs; no tags, signatures or runtime assertions."""
    root = root.resolve()
    _assert_clean(root, expected_sha)
    version, _ = gate.verify_local_identity(root)
    backend_tag = gate.verify_signed_release(
        root,
        version=version,
        expected_sha=expected_sha,
        signer_fingerprint=signer_fingerprint,
    )
    backend_sha = _git(root, "rev-parse", f"{backend_tag}^{{commit}}")
    changes = compatibility_changes(root, backend_sha, expected_sha)
    # The original gate and trust file must remain the actual backend subject's
    # bytes, not a newly permissive implementation supplied by the UI release.
    for relative in (
        gate.AI_CATALOG_PATH,
        "ops/release/verify_catalog_publication.py",
        "ops/deploy/release-allowed-signers",
    ):
        if _blob(root, backend_sha, relative) != _blob(root, expected_sha, relative):
            raise Error(f"frontend release changed its backend contract: {relative}")
    catalog = gate._read_json(root / gate.AI_CATALOG_PATH)
    entry = gate._market_corpus_catalog_entry(catalog)
    corpus_version = gate._validate_market_corpus_entry(entry)
    corpus_version_tag = f"{gate.MARKET_CORPUS_TAG_PREFIX}{corpus_version}"
    config = gate._signing_git_config(root, signer_fingerprint)
    gate._verify_annotated_signed_tag(
        root, tag=corpus_version_tag, head=backend_sha, git_config=config
    )
    gate._verify_market_corpus_version_tagged_identity(
        catalog, gate._read_tagged_json(root, corpus_version_tag, gate.AI_CATALOG_PATH)
    )
    corpus = gate._market_corpus_publication_receipt(entry)
    corpus_sha = gate._verify_annotated_signed_tag(
        root, tag=corpus["tag"], head=backend_sha, git_config=config
    )
    if corpus_sha != backend_sha:
        raise Error(
            "frontend corpus receipt is not bound to the actual backend release"
        )
    gate._verify_market_corpus_tagged_identity(
        catalog, gate._read_tagged_json(root, corpus["tag"], gate.AI_CATALOG_PATH)
    )
    payload = {
        "schema": SCHEMA,
        "purpose": PURPOSE,
        "sourceSha": expected_sha,
        "backendReleaseTag": backend_tag,
        "backendReleaseSha": backend_sha,
        "corpusReceiptTag": corpus["tag"],
        "corpusReceiptSha": corpus_sha,
        "catalogSha256": hashlib.sha256(
            _blob(root, expected_sha, gate.AI_CATALOG_PATH)
        ).hexdigest(),
        "compatibilityChangesSha256": hashlib.sha256(
            _canonical({"changes": changes})
        ).hexdigest(),
    }
    return payload, changes


def verify_frontend_receipt(
    root: Path, *, expected_sha: str, signer_fingerprint: str, receipt_tag: str
) -> dict:
    expected_tag = TAG_PREFIX + expected_sha
    if receipt_tag != expected_tag or gate.COMMIT_RE.fullmatch(expected_sha) is None:
        raise Error("frontend receipt tag must name the exact source SHA")
    payload, _ = prepare_receipt(
        root, expected_sha=expected_sha, signer_fingerprint=signer_fingerprint
    )
    if _git(root, "cat-file", "-t", receipt_tag) != "tag":
        raise Error("frontend receipt must be an annotated SSH-signed tag")
    raw = gate._run_git_bytes(root, "cat-file", "tag", receipt_tag).stdout
    header, separator, body = raw.partition(b"\n\n")
    fields = header.splitlines()
    if (
        not separator
        or len(fields) != 4
        or fields[:3]
        != [
            f"object {expected_sha}".encode(),
            b"type commit",
            f"tag {expected_tag}".encode(),
        ]
        or not fields[3].startswith(b"tagger ")
    ):
        raise Error("frontend receipt tag target or subject is inconsistent")
    marker = b"-----BEGIN SSH SIGNATURE-----\n"
    if body.count(marker) != 1 or b"-----BEGIN PGP SIGNATURE-----" in body:
        raise Error("frontend receipt requires the pinned SSH signature algorithm")
    annotation, signature = body.split(marker)
    if annotation != _canonical(payload) or not signature.endswith(
        b"-----END SSH SIGNATURE-----\n"
    ):
        raise Error("frontend receipt payload differs from the exact reviewed inputs")
    config = gate._signing_git_config(root, signer_fingerprint)
    checked = gate._run_git(
        root, "-c", config, "verify-tag", "--raw", receipt_tag, check=False
    )
    if (
        checked.returncode
        or f"key {signer_fingerprint}" not in checked.stdout + checked.stderr
    ):
        raise Error("frontend receipt signature differs from the pinned signer")
    return payload


def verify_desk_only_descendant(
    root: Path, *, receipt_source_sha: str, current_source_sha: str
) -> dict:
    """Check desk ancestry; the exact ancestor receipt must be authenticated separately.

    This does not reinterpret a signed receipt or authorize current desk bytes
    for publication. It proves that publishing the unchanged signed frontend
    remains compatible with the separately identified current main.
    """
    _assert_clean(root, current_source_sha)
    if receipt_source_sha == current_source_sha:
        raise Error("desk descendant admission requires a distinct receipt ancestor")
    # Omitting the optional signer deliberately rejects even signed controller
    # repairs. Only the existing single-parent daily/weekly desk lane is allowed.
    gate._verify_generated_content_descendants(
        root, release=receipt_source_sha, head=current_source_sha
    )
    return {
        "schema": "seiche.frontend-desk-descendant.v1",
        "receiptSourceSha": receipt_source_sha,
        "currentSourceSha": current_source_sha,
        "purpose": "unchanged_frontend_only_no_desk_publication",
    }


def require_unused_tag(root: Path, tag: str) -> None:
    if re.fullmatch(r"frontend-publication-[0-9a-f]{40}", tag) is None:
        raise Error("frontend tag name is malformed")
    if (
        gate._run_git(
            root, "show-ref", "--verify", "--quiet", f"refs/tags/{tag}", check=False
        ).returncode
        == 0
    ):
        raise Error(
            "frontend receipt tag already exists locally; never reuse or move it"
        )
    remote = gate._run_git(
        root,
        "ls-remote",
        "--exit-code",
        "--tags",
        "origin",
        f"refs/tags/{tag}",
        check=False,
    )
    if remote.returncode == 0:
        raise Error(
            "frontend receipt tag already exists remotely; never reuse or move it"
        )
    if remote.returncode != 2:
        raise Error("frontend receipt remote absence could not be established")


def verify_runtime_subject(
    backend_sha: str, headers, *, previous: dict | None = None
) -> dict:
    """Every runtime response must identify the old backend's same deployment."""
    values = {}
    for name in (
        "x-seiche-release-sha",
        "x-seiche-railway-deployment",
        "x-seiche-railway-authority",
    ):
        entries = [value for key, value in headers.items() if key.lower() == name]
        if len(entries) != 1:
            raise Error(
                "frontend backend production subject has missing or duplicate identity headers"
            )
        values[name] = entries[0]
    subject = {
        "releaseSha": values.get("x-seiche-release-sha"),
        "deploymentId": values.get("x-seiche-railway-deployment"),
        "authority": values.get("x-seiche-railway-authority"),
    }
    deployment = subject["deploymentId"]
    if (
        gate.COMMIT_RE.fullmatch(backend_sha) is None
        or subject["releaseSha"] != backend_sha
        or subject["authority"] != "production"
        or not isinstance(deployment, str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            deployment,
        )
        is None
    ):
        raise Error(
            "frontend runtime does not identify the receipted backend production subject"
        )
    if previous is not None and previous != subject:
        raise Error(
            "frontend runtime deployment identity changed across receipt endpoints"
        )
    return subject


def _runtime_request(request):
    with urllib.request.urlopen(request, timeout=30) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if (
            response.geturl() != request.full_url
            or final.scheme != "https"
            or final.hostname != "api.seiche.info"
            or final.port not in {None, 443}
        ):
            raise Error("frontend runtime receipt left its exact canonical route")
        declared = response.headers.get("Content-Length")
        if declared is not None and int(declared) > gate.MAX_JSON_BYTES:
            raise Error("frontend runtime receipt exceeds its size limit")
        body = response.read(gate.MAX_JSON_BYTES + 1)
        headers = response.headers
    if len(body) > gate.MAX_JSON_BYTES:
        raise Error("frontend runtime receipt exceeds its size limit")
    return body, headers


class RuntimeReceipts:
    """Bind each exact receipt route to its independently signed runtime owner."""

    def __init__(
        self,
        backend_sha: str,
        *,
        version: str,
        corpus_release_id: str,
        request=_runtime_request,
    ):
        if (
            gate.VERSION_RE.fullmatch(version) is None
            or gate.CORPUS_RELEASE_RE.fullmatch(corpus_release_id) is None
        ):
            raise Error("frontend runtime subject configuration is malformed")
        self.backend_sha = backend_sha
        self.corpus_release_id = corpus_release_id
        self.request = request
        self.subject = None
        self.corpus_subject = None
        self.endpoints = []
        self.seiche_get_urls = frozenset(
            {
                f"https://api.seiche.info/api/health?release={version}",
                f"https://api.seiche.info/.well-known/mcp.json?release={version}",
                gate.MARKET_CORPUS_DISCOVERY_URL,
            }
        )
        # ops/Caddyfile routes only these public corpus receipts to the separate
        # gateway. Its native X-Corpus-Release is not a Railway deployment claim.
        self.corpus_methods = {
            gate.MARKET_CORPUS_HEALTH_URL: "GET",
            gate.MARKET_CORPUS_CATALOG_URL: "GET",
            gate.MARKET_CORPUS_MCP_URL: "POST",
        }

    def _json(self, url: str, *, payload=None):
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.seiche.info"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
        ):
            raise Error("frontend runtime request is not canonical")
        method = "GET" if payload is None else "POST"
        corpus_route = url in self.corpus_methods
        if corpus_route:
            if method != self.corpus_methods[url]:
                raise Error("frontend corpus receipt method is not registered")
            if method == "POST" and payload != {
                "jsonrpc": "2.0",
                "id": "market-corpus-publication-proof",
                "method": "tools/list",
                "params": {},
            }:
                raise Error(
                    "frontend corpus receipt permits only its exact tools/list request"
                )
        elif url not in self.seiche_get_urls or method != "GET":
            raise Error("frontend runtime receipt URL or method is not registered")
        request = urllib.request.Request(
            url,
            data=None if payload is None else gate._json_identity(payload),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
                "User-Agent": "seiche-frontend-subject-proof/1",
            },
        )
        body, headers = self.request(request)
        if corpus_route:
            values = [
                value
                for key, value in headers.items()
                if key.lower() == "x-corpus-release"
            ]
            if len(values) != 1 or values[0] != self.corpus_release_id:
                raise Error(
                    "frontend corpus subject has missing, duplicate or conflicting native release headers"
                )
            if any(
                key.lower()
                in {
                    "x-seiche-release-sha",
                    "x-seiche-railway-authority",
                    "x-seiche-railway-deployment",
                }
                for key, _ in headers.items()
            ):
                raise Error(
                    "frontend corpus response contains conflicting runtime-owner headers"
                )
            self.corpus_subject = {
                "releaseId": values[0],
                "identityHeader": "X-Corpus-Release",
            }
        else:
            if any(key.lower() == "x-corpus-release" for key, _ in headers.items()):
                raise Error(
                    "frontend Seiche response contains a conflicting corpus-owner header"
                )
            self.subject = verify_runtime_subject(
                self.backend_sha, headers, previous=self.subject
            )
        self.endpoints.append(url)
        return gate._load_json_bytes(body, label="frontend runtime receipt")

    def fetch_json(self, url: str, *, expected_host: str):
        if expected_host == "api.seiche.info":
            return self._json(url)
        return gate._fetch_json(url, expected_host=expected_host)

    def post_json(self, url: str, payload, *, expected_host: str):
        if expected_host != "api.seiche.info":
            raise Error("frontend runtime POST is not on the canonical host")
        if payload is None:
            raise Error("frontend runtime POST payload is missing")
        return self._json(url, payload=payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--signer-fingerprint", required=True)
    parser.add_argument("--receipt-tag")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.prepare:
            if args.receipt_tag or args.output is None:
                raise Error(
                    "prepare requires a new --output directory and no receipt tag"
                )
            payload, changes = prepare_receipt(
                args.root,
                expected_sha=args.expected_sha,
                signer_fingerprint=args.signer_fingerprint,
            )
            require_unused_tag(args.root, TAG_PREFIX + args.expected_sha)
            args.output.mkdir(parents=True, exist_ok=False)
            (args.output / "receipt.json").write_bytes(_canonical(payload))
            (args.output / "compatibility.json").write_bytes(
                _canonical({"changes": changes})
            )
            print(
                json.dumps(
                    {
                        "status": "unsigned_review_inputs",
                        "tag": TAG_PREFIX + args.expected_sha,
                        "output": str(args.output),
                    }
                )
            )
        else:
            if not args.receipt_tag or args.output is not None:
                raise Error(
                    "verification requires --receipt-tag and no output directory"
                )
            payload = verify_frontend_receipt(
                args.root,
                expected_sha=args.expected_sha,
                signer_fingerprint=args.signer_fingerprint,
                receipt_tag=args.receipt_tag,
            )
            # These are the unchanged package/runtime/corpus receipt gates. The
            # publication source is explicitly separate from the backend subject.
            version, _ = gate.verify_local_identity(args.root)
            corpus_entry = gate._market_corpus_catalog_entry(
                gate._read_json(args.root / gate.AI_CATALOG_PATH)
            )
            signed_corpus = gate._market_corpus_publication_receipt(corpus_entry)
            observed = RuntimeReceipts(
                payload["backendReleaseSha"],
                version=version,
                corpus_release_id=signed_corpus["releaseId"],
            )
            runtime = gate.verify_public_receipts(
                version, fetch_json=observed.fetch_json
            )
            corpus = gate.verify_market_corpus_receipts(
                corpus_entry,
                fetch_json=observed.fetch_json,
                post_json=observed.post_json,
            )
            print(
                json.dumps(
                    {
                        "frontendPublication": payload,
                        "backendPublicReceipts": runtime,
                        "backendRuntimeSubject": observed.subject,
                        "corpusRuntimeSubject": observed.corpus_subject,
                        "runtimeEndpoints": observed.endpoints,
                        "corpusPublicReceipts": corpus,
                    },
                    sort_keys=True,
                )
            )
    except (Error, OSError, ValueError) as exc:
        print(f"frontend publication blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
