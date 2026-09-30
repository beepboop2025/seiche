#!/usr/bin/env python3
"""Publish only the public projection after wake.service's successful run."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from seiche import wake_public, wakeflows  # noqa: E402


def publish(source: Path, output: Path) -> None:
    body = json.dumps(wake_public.project(wakeflows.load(str(source))), allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode() + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".projection-", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(name, output)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/var/lib/wake/wake_seiche.json"))
    parser.add_argument("--output", type=Path, default=Path("/var/lib/seiche-wake-public/public-institutional-flows.json"))
    args = parser.parse_args()
    publish(args.source, args.output)
