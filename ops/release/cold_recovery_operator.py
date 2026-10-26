#!/usr/bin/env python3
"""Explicit cold successor operators; normal upgrade operators remain unchanged.

Use a separately reviewed operator bundle with 0.16.9 bindings and fresh exact
source qualification. This module does not authorize a release on its own.
Every mutating command is explicit; an uncertain upload must be reconciled.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import subprocess
import sys
import urllib.request
import uuid

OPERATION = "application_cold_recovery"
TERMINAL = {"EXITED", "STOPPED", "REMOVED"}
PUBLIC = "https://api.seiche.info"


def require(value, message):
    if not value:
        raise ValueError(message)


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "clock must be timezone aware")
    return result.astimezone(timezone.utc)


def deadline(request, fence, generated_at, *, now=None, admission_expiry=None):
    now = now or datetime.now(timezone.utc)
    generated = utc(generated_at)
    require(timedelta(0) <= now - generated <= timedelta(seconds=900),
            "cold computation is stale or future dated")
    limits = [utc(request["expires_at"]), utc(fence["payload"]["expires_at"]),
              generated + timedelta(seconds=900)]
    if admission_expiry:
        limits.append(utc(admission_expiry))
    result = min(limits)
    require(result > now, "cold grant has no remaining lifetime")
    return result.isoformat().replace("+00:00", "Z")


def terminal_deployment(row, target, *, parent=False, reviewed_id=None):
    require(reviewed_id is not None and row["id"] == reviewed_id,
            "terminal deployment identity is not explicitly reviewed")
    require(row.get("deploymentStopped") is True, "deployment is not stopped")
    require(row["status"] == "REMOVED" if parent else
            row["status"] in {"REMOVED", "SUCCESS", "CRASHED"},
            "cold predecessor is not terminal")
    require({k: row[k] for k in ("projectId", "environmentId", "serviceId")} == {
        "projectId": target["project_id"], "environmentId": target["environment_id"],
        "serviceId": target["service_id"]}, "cold predecessor scope differs")
    rows = row["instances"]
    failed = not parent and row["status"] == "CRASHED"
    require(isinstance(rows, list) and len(rows) <= 8 and (bool(rows) or failed),
            "cold predecessor instance inventory is absent or oversized")
    allowed = TERMINAL | ({"CRASHED"} if failed else set())
    require(len({r["id"] for r in rows}) == len(rows)
            and all(r["status"] in allowed for r in rows),
            "cold predecessor has unsettled or duplicate instances")


def topology_admitted(topology, retired, *, candidate=None, crashed_retired=()):
    latest = topology.get("latestDeployment")
    require(latest and latest["id"] == candidate if candidate else
            latest and latest["id"] in retired, "latest deployment is unreviewed")
    require(set(crashed_retired) <= set(retired), "failed deployment is unreviewed")
    active = topology["activeDeployments"]
    require(len({row["id"] for row in active}) == len(active), "duplicate deployment inventory")
    for row in active:
        if candidate and row["id"] == candidate:
            require(row["deploymentStopped"] is False and len(row["instances"]) == 1
                    and row["instances"][0]["status"] == "RUNNING",
                    "candidate is not singly running")
        else:
            allowed = TERMINAL | ({"CRASHED"} if row["id"] in crashed_retired else set())
            require(row["id"] in retired and row["deploymentStopped"] is True
                    and all(item["status"] in allowed for item in row["instances"]),
                    "another deployment may own the volume")


def approval_available(root, request_id, operation):
    if operation == "observe":
        return
    suffix = "source-stopped" if operation == "source-proof" else "activate"
    for ending in (".json", ".proof.json"):
        path = root / (request_id + "." + suffix + ending)
        require(not path.exists() and not path.is_symlink(),
                "approval already recorded; reconcile without signing again")


def parent_pointer(parent):
    return {"request_id": parent["request_id"], "commit": parent["commit"],
            "deployment_id": parent["railway"]["deployment_id"],
            "grant_sha256": parent["grant_sha256"], "state": "active"}


def authority_admitted(actual, expected, parent):
    require(actual == expected, "current volume authority differs from reviewed readback")
    require(actual["active"] == parent_pointer(parent), "accepted parent pointer differs")
    require(actual["accepted_grants"].get(parent["request_id"] + ".json")
            == parent["grant_sha256"], "accepted parent grant is absent")


def capture_admitted(actual, parent, request, never_authorized):
    # Each envelope is separately signature-verified in the authenticated remote
    # reader. Unknown historical filenames are not assumed absent or fabricated.
    require(set(actual) == {"active", "accepted_grants", "activation_approvals"},
            "authority inventory fields differ")
    require(isinstance(actual["accepted_grants"], dict)
            and isinstance(actual["activation_approvals"], dict), "authority inventory is absent")
    authority_admitted(actual, actual, parent)
    for identifier in [request["request_id"], *never_authorized]:
        require(identifier != parent["request_id"], "parent cannot be a forbidden request")
        require(identifier + ".json" not in actual["accepted_grants"]
                and identifier + ".activate.json" not in actual["activation_approvals"],
                "candidate or never-authorized request already has a grant")


def health_admitted(origin, public, basis, *, version):
    fields = ("generated_at", "version", "provenance", "faults")
    require(basis["version"] == version, "cold snapshot version differs")
    for observed in (origin, public):
        require({key: observed["body"].get(key) for key in fields} == basis,
                "HTTP health differs from authenticated cold artifact")


def upload_identity(raw):
    require(0 < len(raw) <= 1024 * 1024, "upload response absent or oversized")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    ids = {row["deploymentId"] for row in rows if isinstance(row, dict) and "deploymentId" in row}
    require(len(ids) == 1, "upload identity uncertain; reconcile without repeating")
    identifier = ids.pop()
    require(str(uuid.UUID(identifier)) == identifier, "invalid deployment identifier")
    return identifier


def load_operators(path):
    require(sys.flags.optimize == 0, "optimized Python is forbidden")
    root = path.resolve(strict=True)
    sys.path.insert(0, str(root))
    import application_operator as op
    import publication_ready as publication
    require(Path(op.__file__).resolve().parent == root
            and Path(publication.__file__).resolve().parent == root,
            "operator import escaped reviewed bundle")
    require(op.RELEASE["version"] == "0.16.9"
            and op.RELEASE["version_label"] == "estuary",
            "cold release requires separately qualified 0.16.9 bindings")
    require(Path(op.RELEASE["worktree"]).resolve() == Path(__file__).resolve().parents[2],
            "cold operator is not from the qualified worktree")
    return op, publication


def cold_policy(op):
    policy = op.RELEASE["cold_recovery"]
    require(set(policy) == {"retired_deployments", "authority_readback"},
            "cold recovery policy fields differ")
    retired = policy["retired_deployments"]
    require(isinstance(retired, list) and 0 < len(retired) <= 8
            and len(set(retired)) == len(retired)
            and op.RELEASE["parent_deployment"] in retired,
            "cold terminal deployment inventory differs")
    for identifier in retired:
        require(str(uuid.UUID(identifier)) == identifier, "invalid retired deployment")
    readback = op.bindings.bound(policy["authority_readback"], "cold authority readback")
    require(readback["schema"] == "seiche.cold-parent-authority.v1"
            and readback["status"] == "PARENT_RETAINED"
            and readback["parent_activation_sha256"] == op.RELEASE["parent_file_sha256"]["activation.json"],
            "retained parent authority basis differs")
    parent = op.app.read_document(Path(op.RELEASE["parent_dir"]) / "activation.json")
    require(readback["parent_pointer"] == parent_pointer(parent)
            and readback["parent_grant_sha256"] == parent["grant_sha256"],
            "retained parent pointer or grant differs")
    forbidden = readback["never_authorized_requests"]
    require(isinstance(forbidden, list) and len(forbidden) == len(set(forbidden))
            and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                    and value != parent["request_id"] for value in forbidden),
            "never-authorized request inventory differs")
    return retired, readback, parent


def request_from(op, context):
    request = op.app.validate_request(op.app.read_document(context / "request.json"))
    op.bindings.validate_request_binding(request, op.RELEASE)
    require(request["operation"] == OPERATION, "not an explicit cold recovery request")
    previous = op.app.REQUEST_PATH
    try:
        op.app.REQUEST_PATH = context / "request.json"
        op.app.load_parent(request, current=True)
    finally:
        op.app.REQUEST_PATH = previous
    return request


def provider(op, p, deployment):
    """Read authenticated stopped state; the frozen helper omits that field."""
    query = ("query($id:String!){deployment(id:$id){id projectId environmentId serviceId "
             "status deploymentStopped meta instances{id status}}}")
    row = p.api(query, {"id": deployment})["deployment"]
    require({key: row[key] for key in ("id", "projectId", "environmentId", "serviceId")} == {
        "id": deployment, "projectId": op.PROJECT, "environmentId": op.ENVIRONMENT,
        "serviceId": op.SERVICE}, "deployment scope differs")
    require(type(row.get("deploymentStopped")) is bool, "provider stopped state is missing")
    image = (row.get("meta") or {}).get("imageDigest")
    require((image is None and row["status"] == "DEPLOYING") or
            (isinstance(image, str) and image.startswith("sha256:")
             and op.bindings.sha(image[7:], 64)), "provider image identity differs")
    row["meta"] = {"imageDigest": image} if image else {}
    return row


def current_topology(op, p):
    query = ("query($e:String!,$s:String!){serviceInstance(environmentId:$e,serviceId:$s){"
             "latestDeployment{id} activeDeployments{id deploymentStopped instances{id status}}}}")
    return p.api(query, {"e": op.ENVIRONMENT, "s": op.SERVICE})["serviceInstance"]


def preflight(op, p, *, candidate=None):
    retired, basis, parent = cold_policy(op)
    require(p.gh(f"repos/{p.REPO}/git/ref/heads/main")["object"]["sha"] == op.RELEASE["source"],
            "qualified main changed")
    runs = p.gh(f"repos/{p.REPO}/actions/workflows/railway-stateful-recovery.yml/runs?per_page=100")
    require(all(row["status"] == "completed" for row in runs["workflow_runs"]),
            "recovery workflow is running")
    target = op.bindings.target(op.RELEASE)
    predecessors = [provider(op, p, identifier) for identifier in retired]
    for identifier, row in zip(retired, predecessors):
        terminal_deployment(row, target, parent=row["id"] == parent["railway"]["deployment_id"],
                            reviewed_id=identifier)
    topology = current_topology(op, p)
    topology_admitted(topology, retired, candidate=candidate,
                      crashed_retired={row["id"] for row in predecessors if row["status"] == "CRASHED"})
    return {"observed_at": p.now(), "predecessors": predecessors, "topology": topology,
            "frozen_units": op.frozen_source(), "retained_parent_authority": basis,
            "parent_recovery": op.RELEASE["parent_readback"],
            "volume_authority_recheck_required": True}


def prepare(op, p, context):
    require(not context.exists(), "context already exists; never rebuild in place")
    pre = preflight(op, p)
    builder_path = Path(op.RELEASE["worktree"]) / "ops/railway/build_application_context.py"
    spec = importlib.util.spec_from_file_location("_cold_context_builder", builder_path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    request = builder.build_context(Path(op.RELEASE["worktree"]), op.RELEASE["source"],
                                    Path(op.RELEASE["parent_dir"]), op.ROOT / "target.json", context)
    request["operation"] = OPERATION
    request["request_id"] = op.app.digest({k: v for k, v in request.items() if k != "request_id"})
    op.app.validate_request(request)
    (context / "request.json").write_bytes(op.app.canonical(request))
    request_from(op, context)
    manifest = {"status": "CONTEXT_READY", "source": op.RELEASE["source"], "context": str(context),
                "request_id": request["request_id"], "operation": OPERATION,
                "files": {f.relative_to(context).as_posix(): p.ref(f)
                          for f in context.rglob("*") if f.is_file()},
                "preflight": pre}
    p.save(op.ROOT / "application-context.json", manifest)
    return {"status": "CONTEXT_READY", "request_id": request["request_id"]}


def submit(op, p, context):
    require(not (op.ROOT / "application-submission-intent.json").exists(),
            "submission already claimed; reconcile without re-uploading")
    request = request_from(op, context)
    manifest = json.loads((op.ROOT / "application-context.json").read_bytes())
    require(manifest["status"] == "CONTEXT_READY" and manifest["source"] == op.RELEASE["source"]
            and manifest["context"] == str(context) and manifest["request_id"] == request["request_id"]
            and manifest["operation"] == OPERATION, "context identity differs")
    require({f.relative_to(context).as_posix(): p.ref(f) for f in context.rglob("*") if f.is_file()}
            == manifest["files"], "context bytes changed")
    require(utc(request["expires_at"]) > datetime.now(timezone.utc) + timedelta(minutes=40),
            "context has insufficient submission lifetime")
    pre = preflight(op, p)
    p.held(op.ROOT / "cold-submission-held.json")
    command = ["railway", "up", str(context), "--path-as-root", "--project", op.PROJECT,
               "--environment", op.ENVIRONMENT, "--service", op.SERVICE, "--detach", "--json",
               "--message", "Seiche 0.16.9 explicit cold successor; preserve single-writer authority"]
    p.save(op.ROOT / "application-submission-intent.json",
           {"source": op.RELEASE["source"], "request_id": request["request_id"], "operation": OPERATION,
            "context": p.ref(op.ROOT / "application-context.json"), "preflight": pre,
            "command": command, "retry_policy": "Reconcile original upload; never repeat."})
    with (op.ROOT / "application-upload.jsonl").open("xb") as out, \
         (op.ROOT / "application-upload.stderr").open("xb") as err:
        result = subprocess.run(command, stdout=out, stderr=err, timeout=300)
    require(result.returncode == 0, "upload uncertain; reconcile provider without resubmitting")
    identifier = upload_identity((op.ROOT / "application-upload.jsonl").read_bytes())
    provider_row = provider(op, p, identifier)
    receipt = {"status": "SUBMITTED", "source": op.RELEASE["source"], "request_id": request["request_id"],
               "deployment": identifier, "context": str(context), "provider": provider_row, "observed_at": p.now()}
    p.save(op.ROOT / "application-submission.json", receipt)
    return receipt


def state_code(request, deployment, *, cold=False):
    code = """import json,os,sys
from pathlib import Path
sys.path.insert(0,'/workspace/backend')
from seiche import stateful_application as app, stateful_application_runtime as runtime
from seiche import stateful_migration as migration
request=app.load_request()
"""
    code += f"assert request['request_id']=={request['request_id']!r}\n"
    code += f"assert os.environ['RAILWAY_DEPLOYMENT_ID']=={deployment!r}\n"
    code += """parent=app.load_parent(request,current=True)
platform=Path('/var/lib/seiche-platform')
authority=platform/'authority'
receipts=platform/'cutover-receipts'
candidate_path=receipts/(request['request_id']+'.application-candidate.json')
activation_path=receipts/(request['request_id']+'.activation.json')
def inventory(directory, pattern):
    assert directory.is_dir() and not directory.is_symlink()
    result={}
    for path in directory.glob(pattern):
        assert path.is_file() and not path.is_symlink()
        envelope=app.read_document(path)
        payload=app.validate_approval(envelope,'activate')
        assert {key:payload['railway'][key] for key in request['railway']}==request['railway']
        result[path.name]=app.digest(envelope)
    return result
state={'request':request,'candidate':app.read_document(candidate_path) if candidate_path.exists() else None,
       'activation':app.read_document(activation_path) if activation_path.exists() else None,
       'authority':{'active':app.read_document(authority/'application-active.json'),
                    'accepted_grants':inventory(authority/'application-grants','*.json'),
                    'activation_approvals':inventory(platform/'application-approvals','*.activate.json')}}
"""
    if cold:
        code += """from seiche import stateful_cold_snapshot as cold
from seiche.public_faults import project_public_faults
artifact,artifact_sha=cold._read_artifact(cold._artifact_path(request),migration.RUNTIME_GID)
checked=cold.validate_artifact(artifact,request,state['candidate'])
state['cold_evidence']={**checked['evidence'],'artifact_sha256':artifact_sha}
payload=checked['payload']
state['health_basis']={key:payload[key] for key in ('generated_at','version','provenance')}
state['health_basis']['faults']=project_public_faults(payload['faults'])
"""
    return code


def capture_path(op, request):
    return op.ROOT / (request["request_id"] + ".cold-authority.json")


def validate_capture(receipt, request, deployment, parent):
    require(receipt["schema"] == "seiche.cold-authority-capture.v1"
            and receipt["status"] == "CAPTURED"
            and receipt["request_id"] == request["request_id"]
            and receipt["source"] == request["commit"]
            and receipt["deployment"] == deployment
            and receipt["parent_activation_sha256"] == request["parent"]["activation_sha256"],
            "authority capture belongs to another request or deployment")
    authority_admitted(receipt["authority"], receipt["authority"], parent)
    return receipt["authority"]


def observe_state(op, request, deployment, replica, *, cold=False, capture=False):
    state = op.remote(replica, state_code(request, deployment, cold=cold)
                      + "print(json.dumps(state,sort_keys=True))\n")
    require(state["request"] == request and state["activation"] is None,
            "candidate request differs or activation already exists")
    _, basis, parent = cold_policy(op)
    capture_admitted(state["authority"], parent, request, basis["never_authorized_requests"])
    if not capture:
        receipt = op.app.read_document(capture_path(op, request))
        expected = validate_capture(receipt, request, deployment, parent)
        authority_admitted(state["authority"], expected, parent)
    return state


def health(op, base, request, deployment, *, token=None):
    headers = {"User-Agent": "Seiche-Cold-Recovery/1", "Cache-Control": "no-cache"}
    if token:
        headers["X-Seiche-Edge-Token"] = token
    opener = urllib.request.build_opener(op.NoRedirect)
    with opener.open(urllib.request.Request(base + "/api/health", headers=headers), timeout=30) as response:
        require(response.status == 200, "candidate health unavailable")
        actual = {k: response.headers.get(k) for k in
                  ("x-seiche-release-sha", "x-seiche-railway-deployment", "x-seiche-railway-authority")}
        require(actual == {"x-seiche-release-sha": request["commit"],
                "x-seiche-railway-deployment": deployment, "x-seiche-railway-authority": "candidate"},
                "candidate HTTP identity differs")
        raw = response.read(1048577)
        require(len(raw) <= 1048576, "health exceeds bound")
    return {"url": base + "/api/health", "headers": actual, "body": json.loads(raw)}


def approve(op, p, operation, context, deployment):
    request = request_from(op, context)
    if operation != "capture-authority":
        approval_available(op.ROOT, request["request_id"], operation)
    else:
        path = capture_path(op, request)
        require(not path.exists() and not path.is_symlink(),
                "authority capture already exists; reconcile original receipt")
    pre = preflight(op, p, candidate=deployment)
    current = provider(op, p, deployment)
    require(current["status"] in {"DEPLOYING", "SUCCESS"} and len(current["instances"]) == 1
            and current["instances"][0]["status"] == "RUNNING", "candidate is not singly running")
    replica = current["instances"][0]["id"]
    state = observe_state(op, request, deployment, replica, cold=operation == "grant",
                          capture=operation == "capture-authority")
    if operation == "capture-authority":
        receipt = {"schema": "seiche.cold-authority-capture.v1", "status": "CAPTURED",
                   "request_id": request["request_id"], "source": request["commit"],
                   "deployment": deployment, "observed_at": p.now(),
                   "parent_activation_sha256": request["parent"]["activation_sha256"],
                   "basis": op.RELEASE["cold_recovery"]["authority_readback"],
                   "authority": state["authority"], "preflight": pre}
        p.save(capture_path(op, request), receipt)
        return receipt
    if operation == "observe":
        return {"provider": current, **state}
    previous = next(row for row in pre["predecessors"] if row["id"] == request["parent"]["deployment_id"])
    proof = {"observed_at": p.now(), "preflight": pre, "authority": state["authority"]}
    candidate = fence = None
    if operation == "source-proof":
        op.validate_predecessor(previous, operation="source-proof", request=request)
        payload = {"request_id": request["request_id"], "parent_activation_sha256": request["parent"]["activation_sha256"],
                   "requested_at": p.now(), "expires_at": request["expires_at"],
                   "deployment": {k: previous[k] for k in ("id", "projectId", "environmentId", "serviceId", "instances")},
                   "hetzner_writers_frozen": True, "api_stopped": True, "writers_stopped": True}
        purpose, suffix = "source_stopped", "source-stopped"
    else:
        candidate = op.app.validate_candidate(state["candidate"], request=request, current=True)
        fence = op.app.read_document(op.ROOT / (request["request_id"] + ".source-stopped.json"))
        op.validate_predecessor(previous, operation="grant", request=request, candidate=candidate, source_fence=fence)
        token = op.bindings.external_path(op.RELEASE, "edge_token_path").read_text().strip()
        proof.update(origin=health(op, op.ORIGIN, request, deployment, token=token),
                     public=health(op, PUBLIC, request, deployment), cold_evidence=state["cold_evidence"])
        health_admitted(proof["origin"], proof["public"], state["health_basis"],
                        version=op.bindings.expected_version(op.RELEASE))
        assessment = op.source_health.assess_pair(
            op.RELEASE, role="candidate_activation", source=request["commit"], deployment=deployment,
            request=request, expected_version=op.RELEASE["version"], authority="candidate",
            origin=op.source_health.observe(proof["origin"]["body"], proof["origin"]["headers"]),
            public=op.source_health.observe(proof["public"]["body"], proof["public"]["headers"]), consume=True)
        proof["source_health"] = assessment
        expiry = deadline(request, fence, state["cold_evidence"]["generated_at"],
                          admission_expiry=assessment.get("expires_at"))
        payload = {"request_sha256": op.app.digest(request), "candidate_sha256": op.app.digest(candidate),
                   "parent_activation_sha256": request["parent"]["activation_sha256"], "railway": candidate["railway"],
                   "edge_token_sha256": hashlib.sha256(token.encode()).hexdigest(), "public_base_url": PUBLIC,
                   "public_probe_sha256": op.app.digest(proof), "requested_at": p.now(), "expires_at": expiry,
                   "confirmation": "STOPPED_PARENT_CURRENT_DATA_NEW_APPLICATION"}
        purpose, suffix = "activate", "activate"
    envelope = op.sign(purpose, payload)
    if purpose == "source_stopped":
        op.app.validate_source_fence(envelope, request=request)
    else:
        op.app.validate_grant(envelope, request=request, candidate=candidate)
    p.save(op.ROOT / (request["request_id"] + "." + suffix + ".json"), op.app.canonical(envelope))
    p.save(op.ROOT / (request["request_id"] + "." + suffix + ".proof.json"), proof)
    # Re-read actual root-owned volume/artifact immediately before publication.
    publication = state_code(request, deployment, cold=operation == "grant")
    publication += f"assert state['authority']=={state['authority']!r}\n"
    publication += f"assert state['activation'] is None\n"
    publication += f"envelope=json.loads(__import__('base64').b64decode({base64.b64encode(op.app.canonical(envelope)).decode()!r}))\n"
    if purpose == "source_stopped":
        publication += "app.validate_source_fence(envelope,request=request)\n"
    else:
        publication += f"assert state['cold_evidence']=={state['cold_evidence']!r}\n"
        publication += "app.validate_grant(envelope,request=request,candidate=state['candidate'])\n"
        sh = op.source_health
        context_value, body, signature = sh.material(
            op.RELEASE, role="candidate_activation", source=request["commit"], deployment=deployment,
            request=request, expected_version=op.RELEASE["version"], authority="candidate",
            healthy=proof["source_health"]["evidence_status"] == "healthy")
        publication += sh.remote_verifier(op.RELEASE, context=context_value, body=body, signature=signature)
        publication += "from datetime import datetime,timezone\n"
        publication += "_checked=_sh_core.assess(_sh_body,_sh_signature,expected_context=_sh_context,origin=" + repr(sh.observe(proof["origin"]["body"], proof["origin"]["headers"])) + ",public=" + repr(sh.observe(proof["public"]["body"], proof["public"]["headers"])) + ",now=datetime.now(timezone.utc))\n"
        publication += "assert _checked==" + repr({k: v for k, v in proof["source_health"].items() if k != "local_claim"}) + "\n"
    publication += f"path=platform/'application-approvals'/({request['request_id']!r}+{('.' + suffix + '.json')!r})\nruntime._seal(path,envelope)\nprint(json.dumps({{'path':str(path),'sha256':app.digest(app.read_document(path))}}))\n"
    receipt = op.remote(replica, publication)
    require(receipt["sha256"] == op.app.digest(envelope), "published approval readback differs")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operators", type=Path, required=True)
    parser.add_argument("operation", choices=("prepare", "preflight", "submit", "capture-authority", "observe", "source-proof", "grant"))
    parser.add_argument("--context", type=Path)
    parser.add_argument("--deployment")
    args = parser.parse_args()
    op, p = load_operators(args.operators)
    if args.operation == "preflight":
        result = preflight(op, p)
    else:
        require(args.context is not None, "--context required")
        context = args.context.resolve()
        if args.operation == "prepare":
            result = prepare(op, p, context)
        elif args.operation == "submit":
            result = submit(op, p, context)
        else:
            require(args.deployment is not None, "--deployment required")
            result = approve(op, p, args.operation, context, args.deployment)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

