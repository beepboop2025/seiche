#!/usr/bin/env python3
"""Retry bounded Railway reads; never repeat a provider mutation."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time


def is_read(arguments: list[str]) -> bool:
    if not arguments:
        return False
    if arguments[0] == "logs":
        return True
    if arguments[:2] in (
        ["deployment", "list"],
        ["domain", "list"],
        ["variable", "list"],
    ):
        return True
    if arguments[0] == "volume" and arguments[-2:] == ["list", "--json"]:
        return "files" not in arguments
    return (
        arguments[0] == "api"
        and len(arguments) > 1
        and re.match(r"^\s*(?:query\b|\{)", arguments[1]) is not None
    )


def execute(binary: str, arguments: list[str]) -> int:
    if not is_read(arguments):
        return subprocess.run([binary, *arguments], check=False).returncode
    for attempt in range(3):
        try:
            result = subprocess.run(
                [binary, *arguments], capture_output=True, timeout=90, check=False
            )
        except subprocess.TimeoutExpired:
            result = subprocess.CompletedProcess(
                [binary, *arguments], 124, b"", b"Railway read timed out\n"
            )
        transient = any(
            marker in result.stderr.lower()
            for marker in (
                b"timed out",
                b"error sending request",
                b"connection reset",
                b"connection closed",
                b"502 bad gateway",
                b"503 service unavailable",
                b"504 gateway timeout",
            )
        )
        if result.returncode == 0 or not transient or attempt == 2:
            sys.stdout.buffer.write(result.stdout)
            sys.stderr.buffer.write(result.stderr)
            return result.returncode
        print(
            f"Retrying Railway read after transport failure ({attempt + 1}/3)",
            file=sys.stderr,
        )
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(execute(os.environ["RAILWAY_REAL_BIN"], sys.argv[1:]))
