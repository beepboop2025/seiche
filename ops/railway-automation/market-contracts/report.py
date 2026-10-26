"""Verify the built source and prove kernel isolation in the actual runtime."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


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


def validate_cold_result(raw):
    root = ET.fromstring(raw)
    suites = list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))
    if not suites or not cases or any(
        int(suite.get(field, "0")) != 0
        for suite in suites for field in ("skipped", "failures", "errors")
    ):
        raise ValueError("Cold runtime isolation did not pass without skips")
    if any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")):
        raise ValueError("Cold runtime isolation contains an unsuccessful case")
    if "test_real_linux_kernel_denies_native_and_descendant_production_writes" not in {
        case.get("name") for case in cases
    }:
        raise ValueError("Real kernel isolation proof is missing")
    return len(cases)


def checkout_head():
    prefix = ["git", "-c", "safe.directory=/app"]
    for args in (["diff", "--exit-code"], ["diff", "--cached", "--exit-code"]):
        subprocess.run(prefix + args, cwd="/app", check=True, timeout=10,
                       stdout=subprocess.DEVNULL)
    return subprocess.check_output(prefix + ["rev-parse", "HEAD"],
                                   cwd="/app", text=True, timeout=10).strip()


def prove_runtime_isolation():
    if os.getuid() != 0 or os.getgid() != 0:
        raise ValueError("Native isolation proof requires the actual root supervisor")
    # BuildKit and the running container expose different kernel restrictions.
    # Keep the real kernel test mandatory in the environment it qualifies.
    with tempfile.TemporaryDirectory(prefix="seiche-cold-runtime-") as folder:
        result = Path(folder) / "results.xml"
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": "/root", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        }
        subprocess.run([
            "timeout", "--signal=TERM", "--kill-after=10", "180",
            sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "backend/tests/test_stateful_cold_snapshot.py",
            "backend/tests/test_application_cold_readiness.py",
            "--junitxml=" + str(result),
        ], cwd="/app", env=environment, check=True, timeout=210)
        return validate_cold_result(result.read_bytes())


def main():
    source = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    deployment = os.environ.get("RAILWAY_DEPLOYMENT_ID", "")
    admission = Path("/ci-source-admission").read_text()
    build_log = Path("/ci-market.log").read_text()
    marker = validate_proof(source, deployment, checkout_head(), admission, build_log)
    count = prove_runtime_isolation()
    validate_proof(source, deployment, checkout_head(), admission, build_log)
    # Emit success only after the real runtime check and unchanged source proof.
    time.sleep(2)
    print(admission, end="", flush=True)
    print(marker, flush=True)
    print(f"RAILWAY_COLD_RECOVERY_ISOLATION_PASS source={source} tests={count} skipped=0", flush=True)
    print(f"RAILWAY_MARKET_CONTRACTS_PASS source={source} deployment={deployment}", flush=True)
    time.sleep(2)


if __name__ == "__main__":
    main()
