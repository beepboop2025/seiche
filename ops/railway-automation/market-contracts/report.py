"""Verify the completed image's source and retain readable runtime CI evidence."""
import os
from pathlib import Path
import re
import subprocess
import time


def validate_proof(source, deployment, head, admission, build_log):
    if re.fullmatch(r"[0-9a-f]{40}", source) is None or source != head:
        raise ValueError("Runtime source differs from the tested checkout")
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", deployment) is None:
        raise ValueError("Runtime deployment identity is missing or malformed")
    if re.fullmatch(
        rf"RAILWAY_MARKET_SOURCE_ADMITTED source={source} base=[0-9a-f]{{40}}\n",
        admission,
    ) is None:
        raise ValueError("Retained source admission differs from the tested checkout")
    marker = f"RAILWAY_MARKET_CONTRACTS_BUILD_PASS source={source}"
    if build_log.splitlines().count(marker) != 1:
        raise ValueError("Expected one successful original build proof")
    return marker


def main():
    source = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    deployment = os.environ.get("RAILWAY_DEPLOYMENT_ID", "")
    head = subprocess.check_output(
        ["git", "-c", "safe.directory=/app", "rev-parse", "HEAD"],
        cwd="/app", text=True, timeout=10,
    ).strip()
    admission = Path("/ci-source-admission").read_text()
    build_log = Path("/ci-market.log").read_text()
    marker = validate_proof(source, deployment, head, admission, build_log)
    # Keep this short-lived job observable while the provider attaches logging.
    time.sleep(2)
    print(admission, end="", flush=True)
    print(marker, flush=True)
    print(f"RAILWAY_MARKET_CONTRACTS_PASS source={source} deployment={deployment}", flush=True)
    time.sleep(2)


if __name__ == "__main__":
    main()
