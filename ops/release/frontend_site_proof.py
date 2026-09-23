#!/usr/bin/env python3
"""Preserve a recoverable sealed site and prove frontend publication bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = "seiche.frontend-site-proof.v1"
ASSET = re.compile(r"assets/[A-Za-z0-9_.-]+\.(?:js|css|woff2?|ttf|svg|png|webp|jpg)")
ROOT_CARD = re.compile(r"share/cards/board/home\.([0-9a-f]{16})\.png")
SAFE_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+")
PUBLIC_VERIFY_SECONDS = 60
PUBLIC_MAX_RETRIES = 5
TEMPORARY_HTTP_STATUS = frozenset({404, 429, 502, 503, 504})
EDITORIAL_ORIGIN = "https://myquantdoesntspeakenglish.com"
EDITORIAL_CONNECT_BEFORE = (
    "connect-src 'self' https://api.seiche.info https://cloudflareinsights.com;"
)
EDITORIAL_CONNECT_AFTER = EDITORIAL_CONNECT_BEFORE[:-1] + " " + EDITORIAL_ORIGIN + ";"


class ProofError(RuntimeError):
    pass


def _safe_path(relative: str) -> bool:
    return bool(SAFE_PATH.fullmatch(relative)) and all(
        part not in {".", ".."} for part in relative.split("/")
    )


def files(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise ProofError("site root is not a regular directory")
    result = {}
    for item in sorted(root.rglob("*")):
        relative = item.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if item.is_symlink() or not _safe_path(relative):
            raise ProofError(f"unsafe site path: {relative}")
        if item.is_dir():
            continue
        if not item.is_file():
            raise ProofError(f"nonregular site entry: {relative}")
        result[relative] = hashlib.sha256(item.read_bytes()).hexdigest()
    for required in ("index.html", "data/overview.json", ".well-known/ai-catalog.json"):
        if required not in result:
            raise ProofError(f"sealed site input is absent: {required}")
    return result


def snapshot(root: Path, archive: Path) -> dict:
    if archive.exists() or archive.is_symlink():
        raise ProofError("recovery archive already exists")
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root
    ):
        raise ProofError("site mirror is dirty before recovery capture")
    before = files(root)
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    with archive.open("xb") as output:
        subprocess.run(
            ["git", "archive", "--format=tar", revision],
            cwd=root,
            stdout=output,
            check=True,
        )
    # Prove the rollback tar actually contains the captured public file bytes.
    import tarfile

    archived = {}
    with tarfile.open(archive, "r:") as bundle:
        for item in bundle.getmembers():
            relative = item.name.rstrip("/")
            if not _safe_path(relative) or not (item.isfile() or item.isdir()):
                raise ProofError("recovery archive contains an unsafe entry")
            if item.isfile():
                body = bundle.extractfile(item)
                assert body is not None
                archived[relative] = hashlib.sha256(body.read()).hexdigest()
    if archived != before:
        raise ProofError("recovery archive differs from the captured mirror")
    return {
        "schema": SCHEMA,
        "mirrorSha": revision,
        "archiveSha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": before,
    }


def declared_retirements(proof: Path | None, source_sha: str) -> list[str]:
    if proof is None:
        return []
    if proof.is_symlink() or not proof.is_file():
        raise ProofError("retirement requires a regular verified source proof")
    publication = json.loads(proof.read_text()).get("frontendPublication", {})
    if publication.get("sourceSha") != source_sha:
        raise ProofError("retirement source differs from verified frontend receipt")
    paths = publication.get("retiredPublicPaths", [])
    if paths not in ([], ["funding.json"]):
        raise ProofError("frontend receipt declares an unsupported retirement")
    return paths


def retire(root: Path, paths: list[str]) -> dict:
    if paths not in ([], ["funding.json"]):
        raise ProofError("unsupported public retirement")
    files(root)
    for relative in paths:
        target = root / relative
        if target.exists():
            target.unlink()
    return {"retiredPublicPaths": paths}


def declared_editorial_origin(proof: Path | None, source_sha: str) -> str | None:
    if proof is None:
        return None
    # Reuse the same verified-source/regular-file checks as retirement.
    declared_retirements(proof, source_sha)
    origin = json.loads(proof.read_text())["frontendPublication"].get(
        "editorialConnectOrigin"
    )
    if origin not in (None, EDITORIAL_ORIGIN):
        raise ProofError("unsupported editorial connect origin")
    return origin


def editorial_policy(root: Path) -> tuple[bytes, str]:
    target = root / "_headers"
    if target.is_symlink() or not target.is_file():
        raise ProofError("editorial CSP requires a regular existing headers file")
    body = target.read_bytes()
    text = body.decode("utf-8")
    policies = re.findall(r"^  Content-Security-Policy: (.+)$", text, re.MULTILINE)
    if (
        len(policies) != 1
        or text.count(EDITORIAL_ORIGIN) != 1
        or policies[0].count(EDITORIAL_CONNECT_AFTER) != 1
    ):
        raise ProofError(
            "editorial CSP differs from the exact connect origin allowance"
        )
    return body, policies[0]


def allow_editorial(root: Path, origin: str | None) -> dict:
    if origin is None:
        return {}
    if origin != EDITORIAL_ORIGIN:
        raise ProofError("unsupported editorial connect origin")
    files(root)
    target = root / "_headers"
    if target.is_symlink() or not target.is_file():
        raise ProofError("editorial CSP requires a regular existing headers file")
    text = target.read_text()
    if EDITORIAL_ORIGIN not in text:
        if text.count(EDITORIAL_CONNECT_BEFORE) != 1:
            raise ProofError(
                "editorial CSP baseline differs from the expected connect directive"
            )
        target.write_text(
            text.replace(EDITORIAL_CONNECT_BEFORE, EDITORIAL_CONNECT_AFTER)
        )
    _, policy = editorial_policy(root)
    return {"contentSecurityPolicy": policy}


def seal(
    root: Path,
    before: dict,
    *,
    source_sha: str,
    retired_paths=(),
    editorial_origin=None,
) -> dict:
    if (
        before.get("schema") != SCHEMA
        or not isinstance(before.get("files"), dict)
        or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None
    ):
        raise ProofError("invalid frontend recovery manifest or source")
    retired_paths = list(retired_paths)
    if retired_paths not in ([], ["funding.json"]):
        raise ProofError("unsupported public retirement")
    after = files(root)
    policy_proof = {}
    if editorial_origin is not None:
        if editorial_origin != EDITORIAL_ORIGIN:
            raise ProofError("unsupported editorial connect origin")
        header_bytes, policy = editorial_policy(root)
        # The privileged seal independently proves every other header byte remains
        # identical to the recoverable mirror. The builder cannot widen this grant.
        predecessor = header_bytes.replace(
            EDITORIAL_CONNECT_AFTER.encode(), EDITORIAL_CONNECT_BEFORE.encode()
        )
        if before["files"].get("_headers") not in {
            hashlib.sha256(predecessor).hexdigest(),
            after["_headers"],
        }:
            raise ProofError("editorial CSP mutated another sealed header")
        policy_proof = {
            "contentSecurityPolicy": policy,
            "headersSha256": after["_headers"],
        }
    if any(relative in after for relative in retired_paths):
        raise ProofError("retired public file is still present")
    changed = []
    for relative in sorted(set(before["files"]) | set(after)):
        old = before["files"].get(relative)
        new = after.get(relative)
        if old == new:
            continue
        if relative == "_headers" and policy_proof:
            changed.append(relative)
            continue
        if relative in retired_paths and new is None:
            changed.append(relative)
            continue
        if relative != "index.html":
            if old is not None or new is None:
                raise ProofError(
                    f"frontend publication mutated an existing sealed file: {relative}"
                )
            card = ROOT_CARD.fullmatch(relative)
            if not ASSET.fullmatch(relative) and not (
                card and new.startswith(card.group(1))
            ):
                raise ProofError(
                    f"frontend publication added an unauthorized file: {relative}"
                )
        changed.append(relative)
    # The fresh shell may refer only to retained or newly staged local assets.
    shell = (root / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)=["\']\./(assets/[^"\']+)["\']', shell)
    if not refs or any(
        not ASSET.fullmatch(relative) or relative not in after for relative in refs
    ):
        raise ProofError("frontend shell has invalid or missing local asset references")
    public = sorted(
        {
            "index.html",
            "data/overview.json",
            ".well-known/ai-catalog.json",
            *refs,
            *(
                relative
                for relative in changed
                if relative not in retired_paths and relative != "_headers"
            ),
        }
    )
    return {
        "schema": SCHEMA,
        "sourceSha": source_sha,
        "previousMirrorSha": before["mirrorSha"],
        "recoveryArchiveSha256": before["archiveSha256"],
        "changed": changed,
        "publicFiles": {relative: after[relative] for relative in public},
        "absentPublicFiles": retired_paths,
        **policy_proof,
    }


def verify_public(root: Path, manifest: dict, *, cache_key: str, fetch=None) -> dict:
    if manifest.get("schema") != SCHEMA or not isinstance(
        manifest.get("publicFiles"), dict
    ):
        raise ProofError("invalid frontend public manifest")
    absent = manifest.get("absentPublicFiles", [])
    if absent not in ([], ["funding.json"]):
        raise ProofError("unsupported public absence proof")
    policy = manifest.get("contentSecurityPolicy")
    if policy is not None:
        header_bytes, staged_policy = editorial_policy(root)
        if staged_policy != policy or hashlib.sha256(
            header_bytes
        ).hexdigest() != manifest.get("headersSha256"):
            raise ProofError("frontend staged CSP changed")
    # One budget covers the entire file inventory, including backoff. A fresh
    # Pages upload may briefly return 404 for a new asset, but a large manifest
    # must not multiply the retry window or permit a mismatching 200 response.
    deadline = time.monotonic() + PUBLIC_VERIFY_SECONDS
    retries = 0
    if fetch is None:

        def fetch(url):
            request = urllib.request.Request(
                url,
                headers={
                    "Accept-Encoding": "identity",
                    "Cache-Control": "no-cache",
                    "User-Agent": "seiche-frontend-publication-proof/1",
                },
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProofError("frontend public verification deadline exceeded")
            with urllib.request.urlopen(
                request, timeout=min(20, remaining)
            ) as response:
                if urllib.parse.urlsplit(response.url).hostname != "seiche.info":
                    raise ProofError("frontend public probe left its canonical host")
                body = response.read(16 * 1024 * 1024 + 1)
                header_items = list(response.headers.items()) if policy else []
                headers = dict(header_items)
                if policy and urllib.parse.urlsplit(url).path == "/":
                    if (
                        sum(
                            key.lower() == "content-security-policy"
                            for key, _ in header_items
                        )
                        != 1
                    ):
                        raise ProofError(
                            "frontend public CSP must have exactly one policy header"
                        )
                    final = urllib.parse.urlsplit(response.url)
                    if (
                        final.scheme != "https"
                        or final.netloc != "seiche.info"
                        or final.path != "/"
                    ):
                        raise ProofError(
                            "frontend CSP probe left its exact canonical route"
                        )
            if len(body) > 16 * 1024 * 1024:
                raise ProofError("frontend public response exceeded its bound")
            return (body, headers) if policy else body

    checked = []
    for relative, digest in manifest["publicFiles"].items():
        if not _safe_path(relative):
            raise ProofError("frontend public proof contains an unsafe path")
        local = root / relative
        if (
            local.is_symlink()
            or not local.is_file()
            or hashlib.sha256(local.read_bytes()).hexdigest() != digest
        ):
            raise ProofError(f"frontend staged bytes changed: {relative}")
        suffix = "" if relative == "index.html" else relative
        url = (
            "https://seiche.info/"
            + suffix
            + "?"
            + urllib.parse.urlencode({"deployment": cache_key})
        )
        while True:
            if time.monotonic() >= deadline:
                raise ProofError("frontend public verification deadline exceeded")
            try:
                body = fetch(url)
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if isinstance(error, urllib.error.HTTPError):
                    temporary = error.code in TEMPORARY_HTTP_STATUS
                    if temporary:
                        error.close()
                elif isinstance(error, urllib.error.URLError):
                    temporary = isinstance(
                        error.reason, (TimeoutError, ConnectionError)
                    )
                else:
                    temporary = True
                if not temporary:
                    raise
                if retries >= PUBLIC_MAX_RETRIES:
                    raise ProofError(
                        "frontend public temporary retry limit exceeded"
                    ) from error
                delay = min(2**retries, 8)
                if time.monotonic() + delay >= deadline:
                    raise ProofError(
                        "frontend public verification deadline exceeded"
                    ) from error
                time.sleep(delay)
                retries += 1
        response_headers = {}
        if isinstance(body, tuple):
            body, response_headers = body
        if relative == "index.html" and policy is not None:
            actual_policy = {
                key.lower(): value for key, value in response_headers.items()
            }.get("content-security-policy")
            if actual_policy != policy:
                raise ProofError("frontend public CSP differs from the sealed policy")
        if hashlib.sha256(body).hexdigest() != digest:
            raise ProofError(f"frontend public bytes differ: {relative}")
        if time.monotonic() >= deadline:
            raise ProofError("frontend public verification deadline exceeded")
        checked.append(relative)
    for relative in absent:
        if (root / relative).exists() or (root / relative).is_symlink():
            raise ProofError("retired public file is still staged")
        url = (
            "https://seiche.info/"
            + relative
            + "?"
            + urllib.parse.urlencode({"deployment": cache_key})
        )
        try:
            fetch(url)
        except urllib.error.HTTPError as error:
            try:
                final = urllib.parse.urlsplit(error.url)
                if (
                    error.code not in {404, 410}
                    or final.scheme != "https"
                    or final.netloc != "seiche.info"
                    or final.path != "/funding.json"
                ):
                    raise ProofError(
                        "retired public file absence is unproven"
                    ) from error
            finally:
                error.close()
        else:
            raise ProofError("retired public file remains publicly accessible")
        if time.monotonic() >= deadline:
            raise ProofError("frontend public verification deadline exceeded")
    return {
        "schema": SCHEMA,
        "sourceSha": manifest["sourceSha"],
        "status": "verified",
        "paths": checked,
        "absentPaths": absent,
        **({"contentSecurityPolicy": policy} if policy else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("snapshot", "retire", "editorial-csp", "seal", "public")
    )
    parser.add_argument("--site-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--cache-key")
    parser.add_argument("--source-proof", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            if args.archive is None:
                raise ProofError("snapshot requires --archive")
            result = snapshot(args.site_root, args.archive)
        elif args.command == "editorial-csp":
            result = allow_editorial(
                args.site_root,
                declared_editorial_origin(args.source_proof, args.source_sha or ""),
            )
        elif args.command == "retire":
            result = retire(
                args.site_root,
                declared_retirements(args.source_proof, args.source_sha or ""),
            )
        else:
            if args.manifest is None or args.manifest.is_symlink():
                raise ProofError("seal/public requires a regular --manifest")
            manifest = json.loads(args.manifest.read_text())
            if args.command == "seal":
                result = seal(
                    args.site_root,
                    manifest,
                    source_sha=args.source_sha or "",
                    editorial_origin=declared_editorial_origin(
                        args.source_proof, args.source_sha or ""
                    ),
                    retired_paths=declared_retirements(
                        args.source_proof, args.source_sha or ""
                    ),
                )
            else:
                result = verify_public(
                    args.site_root, manifest, cache_key=args.cache_key or ""
                )
        print(json.dumps(result, sort_keys=True))
    except (ProofError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"frontend site proof blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
