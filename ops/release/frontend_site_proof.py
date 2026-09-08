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
import urllib.parse
import urllib.request

SCHEMA = "seiche.frontend-site-proof.v1"
ASSET = re.compile(r"assets/[A-Za-z0-9_.-]+\.(?:js|css|woff2?|ttf|svg|png|webp|jpg)")
ROOT_CARD = re.compile(r"share/cards/board/home\.([0-9a-f]{16})\.png")
SAFE_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+")


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


def seal(root: Path, before: dict, *, source_sha: str) -> dict:
    if (
        before.get("schema") != SCHEMA
        or not isinstance(before.get("files"), dict)
        or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None
    ):
        raise ProofError("invalid frontend recovery manifest or source")
    after = files(root)
    changed = []
    for relative in sorted(set(before["files"]) | set(after)):
        old = before["files"].get(relative)
        new = after.get(relative)
        if old == new:
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
            *changed,
        }
    )
    return {
        "schema": SCHEMA,
        "sourceSha": source_sha,
        "previousMirrorSha": before["mirrorSha"],
        "recoveryArchiveSha256": before["archiveSha256"],
        "changed": changed,
        "publicFiles": {relative: after[relative] for relative in public},
    }


def verify_public(root: Path, manifest: dict, *, cache_key: str, fetch=None) -> dict:
    if manifest.get("schema") != SCHEMA or not isinstance(
        manifest.get("publicFiles"), dict
    ):
        raise ProofError("invalid frontend public manifest")
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
            with urllib.request.urlopen(request, timeout=20) as response:
                if urllib.parse.urlsplit(response.url).hostname != "seiche.info":
                    raise ProofError("frontend public probe left its canonical host")
                body = response.read(16 * 1024 * 1024 + 1)
            if len(body) > 16 * 1024 * 1024:
                raise ProofError("frontend public response exceeded its bound")
            return body

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
        if hashlib.sha256(fetch(url)).hexdigest() != digest:
            raise ProofError(f"frontend public bytes differ: {relative}")
        checked.append(relative)
    return {
        "schema": SCHEMA,
        "sourceSha": manifest["sourceSha"],
        "status": "verified",
        "paths": checked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("snapshot", "seal", "public"))
    parser.add_argument("--site-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--cache-key")
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            if args.archive is None:
                raise ProofError("snapshot requires --archive")
            result = snapshot(args.site_root, args.archive)
        else:
            if args.manifest is None or args.manifest.is_symlink():
                raise ProofError("seal/public requires a regular --manifest")
            manifest = json.loads(args.manifest.read_text())
            if args.command == "seal":
                result = seal(
                    args.site_root, manifest, source_sha=args.source_sha or ""
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
