"""Retry unanswered health connections inside the original 15-second budget."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ORIGIN = "https://seiche-stateful-core-production.up.railway.app"
PUBLIC = "https://api.seiche.info"
BUDGET = 15.0
CONNECT_TIMEOUT = 3.0
MAX_ATTEMPTS = 3
RETRYABLE = frozenset({7, 28, 52, 56})


def validate_header(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077
                or not 1 <= metadata.st_size <= 4096):
            raise ValueError("health header is not one private regular file")
        body = os.read(descriptor, 4097)
        if re.fullmatch(rb"X-Seiche-Edge-Token: [!-~]+\n", body) is None:
            raise ValueError("health header differs from the private edge-token contract")
    finally:
        os.close(descriptor)


def probe(url, header=None, *, clock=time.monotonic, run=subprocess.run):
    if url not in {ORIGIN, PUBLIC} or (url == ORIGIN) != (header is not None):
        raise ValueError("health probe target or credential scope differs")
    if header is not None:
        validate_header(header)
    started = clock()
    deadline = started + BUDGET
    attempts = []
    status = "000"
    for _ in range(MAX_ATTEMPTS):
        remaining = deadline - clock()
        if remaining <= 0:
            break
        arguments = ["/usr/bin/curl", "--disable", "--silent", "--proto", "=https",
                     "--tlsv1.2", "--connect-timeout", str(min(CONNECT_TIMEOUT, remaining)),
                     "--max-time", str(remaining), "--output", "/dev/null", "--write-out", "%{http_code}"]
        if header is not None:
            arguments += ["--header", "@" + str(header)]
        arguments.append(url + "/api/health")
        try:
            result = run(arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env={"PATH": os.defpath, "LANG": "C"}, timeout=remaining,
                         close_fds=True, check=False)
            code, response = result.returncode, result.stdout.decode("ascii", errors="replace")
        except subprocess.TimeoutExpired:
            code, response = 28, "000"
        except OSError:
            code, response = -1, "000"
        observed = clock()
        attempts.append({"curl_code": code, "status": response if re.fullmatch(r"[0-9]{3}", response) else "invalid",
                         "elapsed_seconds": round(observed - started, 3)})
        if observed >= deadline:
            break
        if code == 0:
            if re.fullmatch(r"[1-5][0-9]{2}", response):
                status = response
            break  # HTTP errors and redirects are evidence, never retried.
        if code not in RETRYABLE:
            break
    return {"status": status, "endpoint": "origin" if url == ORIGIN else "public",
            "budget_seconds": BUDGET, "elapsed_seconds": round(clock() - started, 3),
            "attempts": attempts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--origin", choices=[ORIGIN])
    target.add_argument("--public", action="store_true")
    parser.add_argument("--header-file", type=Path)
    args = parser.parse_args()
    result = probe(args.origin or PUBLIC, args.header_file)
    if len(result["attempts"]) != 1 or result["status"] != "200":
        print(json.dumps({"event": "native_recovery_health_probe", "observed_at": datetime.now(timezone.utc).isoformat(),
                          **result}, sort_keys=True), file=sys.stderr, flush=True)
    print(result["status"], flush=True)
    return 0 if result["status"] == "200" else 1


if __name__ == "__main__":
    raise SystemExit(main())
