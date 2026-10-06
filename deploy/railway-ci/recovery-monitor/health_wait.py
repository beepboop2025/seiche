"""Wait briefly for an old healthy snapshot; preserve the final workflow predicate."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time


MAX_SECONDS = 120
MAX_ATTEMPTS = 13
INTERVAL_SECONDS = 10
MAX_BODY_BYTES = 8 * 1024 * 1024
SIDES = ("origin", "public")
SHA = re.compile(r"[0-9a-f]{40}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


class HealthError(RuntimeError):
    """Only fixed, non-sensitive reason codes may cross this boundary."""


def diagnostic(side, body=b"", reason="malformed_body"):
    return {"endpoint": side, "reason": reason, "generated_at": None,
            "age_seconds": None, "version_present": False, "fault_count": None,
            "provenance_count": None, "body_sha256": hashlib.sha256(body).hexdigest()}


def assess(side, body, headers, expected_source, now):
    """Do not return input strings, faults, provenance values or HTTP headers."""
    result = diagnostic(side, body)
    if not body or len(body) > MAX_BODY_BYTES:
        return result, None
    try:
        value = json.loads(body)
    except (UnicodeError, ValueError, RecursionError):
        return result, None
    if not isinstance(value, dict):
        return result, None
    version = value.get("version")
    faults = value.get("faults")
    provenance = value.get("provenance")
    result.update(version_present=isinstance(version, str) and bool(version),
                  fault_count=len(faults) if isinstance(faults, list) else None,
                  provenance_count=len(provenance) if isinstance(provenance, list) else None)
    generated = value.get("generated_at")
    try:
        if not isinstance(generated, str):
            raise ValueError
        generated = datetime.fromisoformat(generated.replace("Z", "+00:00"))
        if generated.tzinfo is None or generated.utcoffset() is None:
            raise ValueError
        generated = generated.astimezone(timezone.utc)
        age = (now - generated).total_seconds()
        if not math.isfinite(age):
            raise ValueError
        result.update(generated_at=generated.isoformat(), age_seconds=age)
    except (ValueError, OverflowError, TypeError):
        result["reason"] = "invalid_generated_at"
        return result, None
    if not result["version_present"]:
        result["reason"] = "missing_version"
    elif not isinstance(faults, list):
        result["reason"] = "invalid_faults"
    elif faults:
        result["reason"] = "faults_present"
    elif not isinstance(provenance, list) or not provenance:
        result["reason"] = "missing_provenance"
    elif age < 0:
        result["reason"] = "future_generated_at"
    else:
        result["reason"] = "old_snapshot" if age > 900 else "fresh"
    # The original proof independently repeats its full pair/source predicate.
    parsed = {}
    for line in headers.decode("iso-8859-1").splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            parsed[name.lower().strip()] = value.strip()
    deployment = parsed.get("x-seiche-railway-deployment", "")
    if (not SHA.fullmatch(expected_source) or
            parsed.get("x-seiche-railway-authority") != "production" or
            parsed.get("x-seiche-release-sha") != expected_source or
            not UUID.fullmatch(deployment)):
        result["reason"] = "source_identity_mismatch"
    return result, deployment


def emit(value):
    print("RAILWAY_RECOVERY_HEALTH_OBSERVATION " + json.dumps(value, sort_keys=True), flush=True)


def fetch(side, remaining):
    """Use the original curl HTTPS endpoints and private origin header file."""
    body, headers = Path(side + ".json"), Path(side + ".headers")
    body.unlink(missing_ok=True)
    headers.unlink(missing_ok=True)
    timeout = min(30, remaining)
    arguments = ["curl", "--silent", "--show-error", "--proto", "=https", "--tlsv1.2",
                 "--connect-timeout", str(min(10, timeout)), "--max-time", str(timeout),
                 "--max-filesize", str(MAX_BODY_BYTES)]
    if side == "origin":
        arguments += ["--header", "@" + str(Path(os.environ["RUNNER_TEMP"]) / "edge-header")]
        url = os.environ["RAILWAY_ORIGIN"] + "/api/health"
    else:
        url = "https://api.seiche.info/api/health"
    arguments += ["--dump-header", str(headers), "--output", str(body), "--write-out", "%{http_code}", url]
    try:
        process = subprocess.run(arguments, capture_output=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        raise HealthError("transport_failure") from None
    if process.returncode:
        raise HealthError("transport_failure")
    if process.stdout != b"200":
        raise HealthError("http_status_not_200")
    try:
        if body.stat().st_size > MAX_BODY_BYTES or headers.stat().st_size > 65536:
            raise HealthError("response_exceeds_bound")
        return body.read_bytes(), headers.read_bytes()
    except OSError:
        raise HealthError("response_unavailable") from None


def wait_for_health(expected_source, *, read=fetch, monotonic=time.monotonic,
                    sleep=time.sleep, utcnow=lambda: datetime.now(timezone.utc), report=emit):
    deadline = monotonic() + MAX_SECONDS
    for attempt in range(1, MAX_ATTEMPTS + 1):
        samples = {}
        for side in SIDES:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise HealthError("freshness_wait_expired")
            try:
                samples[side] = read(side, remaining)
            except HealthError as error:
                report({**diagnostic(side, reason=str(error)), "attempt": attempt})
                raise
            value, _ = assess(side, *samples[side], expected_source, utcnow())
            if value["reason"] not in ("fresh", "old_snapshot"):
                report({**value, "attempt": attempt})
                raise HealthError(value["reason"])
        # Evaluate both retained bodies against the same real final clock.
        now = utcnow()
        results = [assess(side, *samples[side], expected_source, now) for side in SIDES]
        if results[0][1] != results[1][1]:
            for value, _ in results:
                value["reason"] = "split_deployment_identity"
        for value, _ in results:
            report({**value, "attempt": attempt})
        reasons = {value["reason"] for value, _ in results}
        if not reasons <= {"fresh", "old_snapshot"}:
            raise HealthError("health_pair_invalid")
        if monotonic() >= deadline:
            raise HealthError("freshness_wait_expired")
        if reasons == {"fresh"}:
            return
        if attempt == MAX_ATTEMPTS:
            break
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(INTERVAL_SECONDS, remaining))
    raise HealthError("freshness_wait_expired")


if __name__ == "__main__":
    try:
        wait_for_health(os.environ["RECOVERY_SOURCE_SHA"])
    except HealthError as error:
        raise SystemExit("production API health: " + str(error)) from None
