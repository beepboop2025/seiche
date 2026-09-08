#!/usr/bin/env python3
"""Read exact recovery logs, allowing a bounded explicit activation bootstrap."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timedelta, timezone


MARKER = "SEICHE_RAILWAY_STATEFUL_RESULT_V1="
ROW_LIMIT = 5000
BYTE_LIMIT = 8 * 1024 * 1024
STRICT_AGE = timedelta(hours=26)
# Three days is within the shortest documented Railway deployment-log retention.
# Older activation history requires separate durable-receipt recovery review.
BOOTSTRAP_AGE = timedelta(days=3)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def moment(value):
    if not isinstance(value, str):
        raise ValueError("Log or deployment timestamp is absent")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Log or deployment timestamp lacks timezone")
    return result.astimezone(timezone.utc)


def api(query, variables):
    allowed = {name: os.environ[name] for name in
               ("PATH", "HOME", "RAILWAY_TOKEN", "RAILWAY_REAL_BIN") if name in os.environ}
    result = subprocess.run(["railway", "api", query, "--variables", json.dumps(variables)],
                            env=allowed, capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise RuntimeError("Bounded Railway log read failed")
    if len(result.stdout) > BYTE_LIMIT:
        raise ValueError("Railway response exceeds the recovery evidence byte limit")
    payload = json.loads(result.stdout)
    if payload.get("errors") or not isinstance(payload.get("data"), dict):
        raise ValueError("Railway recovery query returned errors")
    return payload["data"]


def deployment_identity(call, expected, now):
    query = "query($id:String!){deployment(id:$id){id projectId environmentId serviceId status createdAt instances{id status}}}"
    value = call(query, {"id": expected["id"]})["deployment"]
    if not isinstance(value, dict) or any(value.get(name) != data for name, data in expected.items()):
        raise ValueError("Recovery deployment identity differs from accepted monitor")
    if value.get("status") != "SUCCESS":
        raise ValueError("Recovery deployment is not successful")
    instances = value.get("instances")
    if (not isinstance(instances, list) or len(instances) != 1 or
            instances[0].get("status") != "RUNNING" or
            UUID.fullmatch(str(instances[0].get("id", ""))) is None):
        raise ValueError("Recovery deployment is not singly RUNNING")
    created = moment(value.get("createdAt"))
    if created > now:
        raise ValueError("Deployment creation timestamp is in the future")
    return value


def read_window(call, deployment, release, start, end):
    query = ("query($id:String!){deploymentLogs(deploymentId:$id,limit:5000,startDate:"
             + json.dumps(start.isoformat()) + ",endDate:" + json.dumps(end.isoformat())
             + ',filter:"SEICHE_RAILWAY_STATEFUL_RESULT_V1="){message timestamp attributes{key value}}}')
    rows = call(query, {"id": deployment})["deploymentLogs"]
    if not isinstance(rows, list) or len(rows) >= ROW_LIMIT:
        raise ValueError("Recovery history is truncated or exceeds the row limit")
    output = []
    envelopes = []
    for record in rows:
        if not isinstance(record, dict):
            raise ValueError("Recovery log record is invalid")
        message = record.get("message")
        if not message:
            attributes = {item["key"]: item["value"] for item in record.get("attributes", [])}
            value = attributes.get("message", attributes.get("msg"))
            if value is not None:
                try:
                    message = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    message = value
        if not isinstance(message, str) or not message.startswith(MARKER):
            raise ValueError("Recovery marker framing is invalid")
        timestamp = record.get("timestamp")
        if not start <= moment(timestamp) <= end:
            raise ValueError("Recovery log falls outside the requested time window")
        encoded = message.removeprefix(MARKER)
        if len(encoded) > 128 * 1024:
            raise ValueError("Recovery envelope exceeds the bounded log size")
        envelope = json.loads(base64.b64decode(encoded, validate=True))
        if not isinstance(envelope, dict):
            raise ValueError("Recovery envelope is not an object")
        # Other source revisions are never bootstrap candidates.
        if envelope.get("deployment_id") == deployment and envelope.get("commit") == release:
            envelopes.append(envelope)
        output.append({"message": message, "timestamp": timestamp})
    body = b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in output)
    if len(body) > BYTE_LIMIT:
        raise ValueError("Recovery evidence exceeds the byte limit")
    return body, envelopes


def fetch(expected, release, *, bootstrap, now, call=api):
    if any(UUID.fullmatch(str(value)) is None for value in expected.values()):
        raise ValueError("Expected Railway identity is invalid")
    if re.fullmatch(r"[0-9a-f]{40}", release) is None:
        raise ValueError("Expected release is invalid")
    before = deployment_identity(call, expected, now)
    strict_start = max(moment(before["createdAt"]), now - STRICT_AGE)
    body, recent = read_window(call, expected["id"], release, strict_start, now)
    mode = "strict-26h"
    if bootstrap and not any(item.get("kind") == "recovery_offsite_paired" for item in recent):
        created = moment(before["createdAt"])
        if now - created > BOOTSTRAP_AGE:
            raise ValueError("Activation bootstrap is outside bounded provider retention")
        body, history = read_window(call, expected["id"], release, created, now)
        # An old recovery pair must never masquerade as a fresh recurring pair.
        # Bootstrap consumes only immutable candidate/activation records; the
        # existing caller verifies full canonical envelopes and activation digest.
        keep = {"candidate", "activation"}
        selected = []
        for line in body.splitlines():
            row = json.loads(line)
            envelope = json.loads(base64.b64decode(row["message"].removeprefix(MARKER), validate=True))
            if (envelope.get("kind") in keep and envelope.get("deployment_id") == expected["id"]
                    and envelope.get("commit") == release):
                selected.append(line + b"\n")
        if not any(item.get("kind") == "activation" for item in history):
            raise ValueError("Exact deployment activation bootstrap is absent")
        body = b"".join(selected)
        mode = "explicit-activation-bootstrap"
    after = deployment_identity(call, expected, now)
    if after != before:
        raise ValueError("Recovery runtime changed during history retrieval")
    return body, {"mode": mode, "deployment_id": expected["id"], "release_sha": release,
                  "created_at": before["createdAt"], "instance": before["instances"][0]["id"],
                  "rows": len(body.splitlines()), "bytes": len(body),
                  "observed_at": now.isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    if args.bootstrap and (os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or
                           os.environ.get("CONFIRMATION") != "EXPORT_WITHOUT_AUTHORITY_CHANGE"):
        raise ValueError("Activation bootstrap requires the explicit governed export operation")
    expected = {"id": args.deployment, "projectId": os.environ["RAILWAY_PROJECT_ID"],
                "environmentId": os.environ["RAILWAY_ENVIRONMENT_ID"],
                "serviceId": os.environ["RAILWAY_SERVICE_ID"]}
    body, proof = fetch(expected, args.release, bootstrap=args.bootstrap,
                        now=datetime.now(timezone.utc))
    args.output.write_bytes(body)
    args.output.with_suffix(args.output.suffix + ".query-proof.json").write_text(json.dumps(proof, sort_keys=True) + "\n")
    print("Recovery log history verified: " + json.dumps(proof, sort_keys=True))


if __name__ == "__main__":
    main()
