"""Export Seiche's static board files under the current source-rights policy.

Usage: python backend/scripts/export_public.py [--snapshot FILE] [--private-snapshot FILE] <public-path> [<overview-path>]

<public-path> and <overview-path> are published as /data/public.json and
/data/overview.json. Each carries exactly what its API route may serve
(public_rights.STATIC_SURFACES): while /api/public and /api/overview are
withheld, both files carry the same source-rights restriction payload and no
board values. Static readers (the prerendered home, social cards and the Book)
render that state; they never fall back to a copy of the full board.

The full board is written only to --private-snapshot, for internal steps that
are allowed to read it. That path is refused inside the public web roots
(frontend/public, frontend/dist) and refused if it names a public output.
When a route's policy is open again, its file carries the board as before.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seiche import assemble, public_rights, public_view  # noqa: E402
from seiche.publish_snapshot import PublishSnapshotError, load_publish_snapshot  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_WEB_ROOTS = (REPO_ROOT / "frontend" / "public", REPO_ROOT / "frontend" / "dist")


def _json_safe(o):
    """NaN/Inf → null: strict JSON parsers (every browser) reject them."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    return o


def _private_path_problem(path: Path, public_outputs: list[Path]) -> str | None:
    resolved = path.resolve()
    for root in PUBLIC_WEB_ROOTS:
        base = root.resolve()
        if resolved == base or base in resolved.parents:
            return f"private snapshot {path} is inside the public web root {root}"
    if any(resolved == output.resolve() for output in public_outputs):
        return f"private snapshot {path} names a public output"
    return None


def _write_private(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(body)
    path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", help="reuse this already-built full-board JSON")
    parser.add_argument("--private-snapshot",
                        help="also write the full board here, outside any public web root")
    parser.add_argument("public_path", nargs="?", default="public.json")
    parser.add_argument("overview_path", nargs="?")
    args = parser.parse_args(argv)

    out = Path(args.public_path)
    overview_out = Path(args.overview_path) if args.overview_path else None
    private_out = Path(args.private_snapshot) if args.private_snapshot else None
    if private_out is not None:
        problem = _private_path_problem(private_out, [p for p in (out, overview_out) if p])
        if problem:
            print(f"FATAL: {problem}; refusing to publish", file=sys.stderr)
            return 1
    if args.snapshot:
        try:
            snap = load_publish_snapshot(args.snapshot)
        except PublishSnapshotError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            return 1
        print(f"board read from {args.snapshot} (generated {snap.get('generated_at')})")
    else:
        snap = asyncio.run(assemble.snapshot(force=True))
    engines = snap.get("engines", {})
    if sum(1 for v in engines.values() if isinstance(v, dict) and v.get("ok")) == 0:
        print("FATAL: zero engines produced output; refusing to publish", file=sys.stderr)
        return 1

    withheld = public_rights.static_restriction("/data/public.json")
    payload = withheld if withheld is not None else public_view.public_payload(snap)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    if withheld is not None:
        print(f"wrote public surface -> {out} (withheld under {withheld['policy']})")
    else:
        print(f"wrote public surface -> {out} (regime {payload['conclusion']['regime']})")
    if overview_out is not None:
        withheld = public_rights.static_restriction("/data/overview.json")
        board = withheld if withheld is not None else _json_safe(snap)
        overview_out.parent.mkdir(parents=True, exist_ok=True)
        overview_out.write_text(json.dumps(board, separators=(",", ":"), allow_nan=False))
        state = f"withheld under {withheld['policy']}" if withheld is not None else "full board"
        print(f"wrote static board -> {overview_out} ({state})")
    if private_out is not None:
        _write_private(private_out, json.dumps(_json_safe(snap), separators=(",", ":"), allow_nan=False))
        print(f"wrote private full board -> {private_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
