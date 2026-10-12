"""Free self-service identities with bounded, replay-resistant admission.

This grants workspace access only. It never enrolls a participant in learning,
data licensing, commerce attribution or institution verification.
"""
import hashlib
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import Column, Float, MetaData, String, Table, delete, func, insert, select

from .models import NetworkError
from .store import _id, _label, agents, orgs

POLICY = "free-agent-network-2026-10-12"
DIFFICULTY = 16
meta = MetaData()
claims = Table("network_enrollment_claims", meta,
               Column("id", String(36), primary_key=True),
               Column("org_id", String(36)), Column("created", Float, index=True))


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: dict
    signature: str = Field(max_length=128)
    proof: StrictInt = Field(ge=0, le=2**32 - 1)
    organization: str = Field(min_length=1, max_length=120)
    agent: str = Field(min_length=1, max_length=120)
    policy_version: str = Field(max_length=80)
    data_notice_acknowledged: StrictBool


def challenge(store):
    value = {"id": str(uuid.uuid4()), "issued_at": int(store.clock()),
             "difficulty_bits": DIFFICULTY, "purpose": POLICY}
    return {"challenge": value, "signature": store.crypto.sign(value),
            "expires_in_seconds": 300,
            "proof_algorithm": "SHA256(UTF8(challenge.id + ':' + decimal_nonce)); first 16 bits zero",
            "policy_version": POLICY, "data_notice": "/privacy",
            "free_access_requires_enrollment": False,
            "training_enrollment": False, "data_resale_permission": False}


def enroll(store, request, *, daily_limit=100):
    value = request.challenge
    now = store.clock()
    if (set(value) != {"id", "issued_at", "difficulty_bits", "purpose"}
            or not isinstance(value.get("id"), str)
            or not re.fullmatch(r"[a-f0-9-]{36}", value["id"])
            or type(value.get("issued_at")) is not int
            or not 0 <= now - value["issued_at"] <= 300
            or type(value.get("difficulty_bits")) is not int
            or value["difficulty_bits"] != DIFFICULTY or value["purpose"] != POLICY
            or not store.crypto.verify(value, request.signature)):
        raise NetworkError("invalid_challenge", "Enrollment challenge is invalid or expired", 400)
    work = hashlib.sha256(f"{value['id']}:{request.proof}".encode()).digest()
    if work[:2] != b"\0\0":
        raise NetworkError("invalid_proof", "Enrollment proof is invalid", 400)
    if request.policy_version != POLICY or request.data_notice_acknowledged is not True:
        raise NetworkError("notice_required", "Read and acknowledge the current data notice", 400)
    organization = _label(request.organization)
    agent_label = _label(request.agent)
    with store._transaction() as db:
        db.execute(delete(claims).where(claims.c.created < now - 86400))
        if db.scalar(select(claims.c.id).where(claims.c.id == value["id"])):
            raise NetworkError("challenge_used", "This enrollment was already accepted; use its saved credential", 409)
        if db.scalar(select(func.count()).select_from(claims)) >= daily_limit:
            raise NetworkError("enrollment_capacity", "Free workspace enrollment is at capacity; anonymous tools remain available", 429)
        org = dict(id=_id(), label=organization, verification="self_declared", status="active", created=now)
        agent = dict(id=_id(), org_id=org["id"], label=agent_label, capabilities="[]", public=False,
                     status="active", created=now)
        db.execute(insert(orgs).values(**org))
        db.execute(insert(agents).values(**agent))
        db.execute(insert(claims).values(id=value["id"], org_id=org["id"], created=now))
        return {"organization": org, "agent": store._agent_view(db, agent),
                "owner_token": store._credential(db, org["id"]),
                "agent_token": store._credential(db, org["id"], agent["id"]),
                "policy_version": POLICY, "directory_visibility": "private",
                "shared_training": False, "data_resale_permission": False,
                "credential_recovery": "Save now; plaintext credentials cannot be retrieved again."}


def mount_enrollment(app, store):
    import os
    meta.create_all(store.engine)

    @app.get("/v1/enrollment/challenge")
    def get_challenge():
        if os.environ.get("SEICHE_SELF_SERVICE", "1") != "1":
            raise NetworkError("enrollment_paused", "Workspace enrollment is paused; anonymous tools remain free", 503)
        return challenge(store)

    @app.post("/v1/enrollment", status_code=201)
    def create_workspace(body: EnrollmentRequest):
        if os.environ.get("SEICHE_SELF_SERVICE", "1") != "1":
            raise NetworkError("enrollment_paused", "Workspace enrollment is paused; anonymous tools remain free", 503)
        return enroll(store, body)
