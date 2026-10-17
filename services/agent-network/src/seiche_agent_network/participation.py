"""Owner-authorized, bounded task signals with no prompt or transaction capture.

Agents report their own coarse task state. Reports are not verified behavior,
settlement evidence, a grant of training rights, or permission for data resale.
"""
from collections import Counter
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, delete, func, insert, select, update

from .crypto import canonical, digest
from .models import NetworkError, Principal

POLICY = "task-signals-2026-10-12"
RETENTION_DAYS = 30
AGENT_DAILY_LIMIT = 100
WORKSPACE_DAILY_LIMIT = 1000
NOTICE = (
    "Optional task sharing records only the selected goal, topic, stage and outcome, "
    "linked to your workspace and agent for up to 30 days, for product improvement "
    "and your workspace summary. Do not send private prompts, "
    "personal data, account details or transaction details. Signals are self-reported, "
    "not verified purchases or trades. An owner can withdraw and erase active signals "
    "at any time. Encrypted backup copies expire within 7 days. This does not authorize "
    "shared model training or data resale. Public tools remain free without sharing."
)
Goal = Literal["research", "compare", "monitor", "buy", "arbitrage"]
Topic = Literal["dollar_funding", "company_evidence", "market_liquidity", "china_economy",
                "science", "public_datasets", "ai_economy"]
Stage = Literal["exploring", "comparing", "decided", "completed", "abandoned"]
Outcome = Literal["unknown", "useful", "not_useful", "blocked"]
TrafficClass = Literal["real", "synthetic"]


class ParticipationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: StrictBool
    policy_version: Literal["task-signals-2026-10-12"]
    data_notice_acknowledged: StrictBool = False


class TaskSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(min_length=36, max_length=36, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    grant_id: str = Field(min_length=36, max_length=36, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    goal: Goal
    topic: Topic
    stage: Stage
    outcome: Outcome = "unknown"
    traffic_class: TrafficClass = "real"

    @field_validator("event_id", "grant_id")
    @classmethod
    def canonical_uuid(cls, value):
        if len(value) != 36 or str(UUID(value)) != value:
            raise ValueError("A canonical UUID is required")
        return value


meta = MetaData()
grants = Table("network_participation_grants", meta,
    Column("org_id", String(36), primary_key=True),
    Column("grant_id", String(36), nullable=False),
    Column("policy_version", String(40), nullable=False),
    Column("accepted_at", Float, nullable=False))
signals = Table("network_participation_signals", meta,
    Column("agent_id", String(36), primary_key=True),
    Column("event_id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False, index=True),
    Column("grant_id", String(36), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("received_at", Float, nullable=False, index=True),
    Column("goal", String(16), nullable=False),
    Column("topic", String(24), nullable=False),
    Column("stage", String(16), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("traffic_class", String(16), nullable=False))
# Minimal current-day counters survive participation withdrawal, so toggling
# consent cannot bypass admission limits. They contain no task classifications.
limits = Table("network_participation_limits", meta,
    Column("org_id", String(36), primary_key=True),
    Column("agent_id", String(36), primary_key=True),
    Column("day", String(10), primary_key=True),
    Column("count", Integer, nullable=False))


def _day(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


class Participation:
    def __init__(self, store):
        self.store = store

    def _status(self, db, principal):
        row = db.execute(select(grants).where(grants.c.org_id == principal.org_id)).mappings().first()
        enabled = row is not None and row["policy_version"] == POLICY
        return {"enabled": enabled, "grant_id": row["grant_id"] if enabled else None,
                "policy_version": POLICY, "evidence_kind": "self_reported",
                "retention_days": RETENTION_DAYS, "shared_training": False,
                "data_resale_permission": False, "role": "agent" if principal.agent_id else "owner",
                "notice": NOTICE}

    def status(self, principal):
        with self.store._transaction(shared=True) as db:
            self.store._principal(db, principal)
            return self._status(db, principal)

    def configure(self, principal, request):
        # Revalidate even for direct Python callers using constructed models.
        request = ParticipationRequest.model_validate(request.model_dump())
        if request.enabled and not request.data_notice_acknowledged:
            raise NetworkError("notice_required", "An owner must acknowledge the task-sharing notice", 400)
        with self.store._transaction() as db:
            self.store._principal(db, principal, owner=True)
            current = self._status(db, principal)
            if request.enabled:
                if not current["enabled"]:
                    db.execute(delete(signals).where(signals.c.org_id == principal.org_id))
                    db.execute(delete(grants).where(grants.c.org_id == principal.org_id))
                    db.execute(insert(grants).values(org_id=principal.org_id, grant_id=str(uuid4()),
                                                    policy_version=POLICY, accepted_at=self.store.clock()))
            else:
                db.execute(delete(signals).where(signals.c.org_id == principal.org_id))
                db.execute(delete(grants).where(grants.c.org_id == principal.org_id))
            return self._status(db, principal)

    def withdraw(self, principal):
        return self.configure(principal, ParticipationRequest(enabled=False, policy_version=POLICY))

    def record(self, principal, signal):
        signal = TaskSignal.model_validate(signal.model_dump())
        body = signal.model_dump()
        request_hash = digest(canonical(body))
        with self.store._transaction(shared=True, keys=[f"participation:{principal.org_id}"]) as db:
            self.store._principal(db, principal, agent=True)
            current = self._status(db, principal)
            if not current["enabled"] or current["grant_id"] != signal.grant_id:
                raise NetworkError("participation_disabled", "Task sharing requires the current owner-authorized grant", 403)
            now = self.store.clock()
            # Expired rows cannot affect summary or idempotency while maintenance
            # is delayed. Idempotency receipts are retained for the same 30 days.
            db.execute(delete(signals).where(signals.c.org_id == principal.org_id,
                                            signals.c.received_at <= now - RETENTION_DAYS * 86400))
            previous = db.execute(select(signals).where(signals.c.agent_id == principal.agent_id,
                                                       signals.c.event_id == signal.event_id)).mappings().first()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise NetworkError("idempotency_conflict", "This event identifier was already used for another signal", 409)
                return self._receipt(previous, "duplicate")
            day = _day(now)
            db.execute(delete(limits).where(limits.c.org_id == principal.org_id, limits.c.day < day))
            conditions = (limits.c.org_id == principal.org_id, limits.c.agent_id == principal.agent_id,
                          limits.c.day == day)
            used = db.scalar(select(limits.c.count).where(*conditions)) or 0
            workspace_used = db.scalar(select(func.sum(limits.c.count)).where(
                limits.c.org_id == principal.org_id, limits.c.day == day)) or 0
            if used >= AGENT_DAILY_LIMIT or workspace_used >= WORKSPACE_DAILY_LIMIT:
                raise NetworkError("participation_capacity", "Daily task-sharing limit reached; public tools remain free", 429)
            row = body | {"org_id": principal.org_id, "agent_id": principal.agent_id,
                          "request_hash": request_hash, "received_at": now}
            db.execute(insert(signals).values(**row))
            if used:
                db.execute(update(limits).where(*conditions).values(count=used + 1))
            else:
                db.execute(insert(limits).values(org_id=principal.org_id, agent_id=principal.agent_id,
                                                day=day, count=1))
            return self._receipt(row, "accepted")

    @staticmethod
    def _receipt(row, status):
        return {"status": status, "event_id": row["event_id"], "received_at": row["received_at"],
                "evidence_kind": "self_reported"}

    def summary(self, principal):
        with self.store._transaction(shared=True) as db:
            self.store._principal(db, principal)
            conditions = [signals.c.org_id == principal.org_id,
                          signals.c.received_at > self.store.clock() - RETENTION_DAYS * 86400]
            if principal.agent_id:
                conditions.append(signals.c.agent_id == principal.agent_id)
            dimensions = (signals.c.traffic_class, signals.c.goal, signals.c.topic,
                          signals.c.stage, signals.c.outcome)
            rows = db.execute(select(*dimensions, func.count().label("count")).where(*conditions)
                              .group_by(*dimensions)).mappings().all()
        groups = {traffic: {"total_events": 0, "goals": Counter(), "topics": Counter(),
                            "stages": Counter(), "outcomes": Counter()}
                  for traffic in ("real", "synthetic")}
        for row in rows:
            group = groups[row["traffic_class"]]
            group["total_events"] += row["count"]
            for singular in ("goal", "topic", "stage", "outcome"):
                group[singular + "s"][row[singular]] += row["count"]
        return {"scope": "agent" if principal.agent_id else "workspace", "window_days": RETENTION_DAYS,
                "evidence_kind": "self_reported", "total_events": sum(g["total_events"] for g in groups.values()),
                "traffic_classes": groups, "shared_training": False, "data_resale_permission": False,
                "interpretation": "Counts describe submitted reports, not unique people, completed purchases, "
                                  "verified trades or global agent coverage. Traffic class is self-declared."}


def contract():
    schema = TaskSignal.model_json_schema()
    return {"policy_version": POLICY, "notice": NOTICE, "evidence_kind": "self_reported",
            "taxonomy": {key: schema["properties"][key]["enum"]
                         for key in ("goal", "topic", "stage", "outcome", "traffic_class")},
            "schemas": {"event": schema, "participation": ParticipationRequest.model_json_schema()},
            "endpoints": {"status": "/v1/participation", "configure": "/v1/participation",
                          "withdraw": "/v1/participation", "events": "/v1/participation/events",
                          "summary": "/v1/participation/summary"},
            "limits": {"events_per_agent_per_utc_day": AGENT_DAILY_LIMIT,
                       "events_per_workspace_per_utc_day": WORKSPACE_DAILY_LIMIT,
                       "retention_days": RETENTION_DAYS, "idempotency_days": RETENTION_DAYS,
                       "withdrawal_quota_counters": "Current UTC day only; contain no task classifications",
                       "backup_retention_days": 7},
            "coverage_scope": "Only explicit reports from agents in owner-authorized workspaces; "
                              "no tracking of unrelated agents or estimate of global coverage.",
            "authentication": {"configure_and_withdraw": "owner bearer credential",
                               "events": "delegated agent bearer credential and current grant_id",
                               "status_and_summary": "owner or delegated agent bearer credential"},
            "shared_training": False, "data_resale_permission": False,
            "free_tools_require_sharing": False}


def mount_participation(app, store, principal_dependency):
    participation = Participation(store)
    app.state.participation = participation
    Identity = Annotated[Principal, Depends(principal_dependency)]

    @app.get("/v1/participation/contract")
    def public_contract():
        return contract()

    @app.get("/v1/participation")
    def get_status(principal: Identity):
        return participation.status(principal)

    @app.patch("/v1/participation")
    def set_status(body: ParticipationRequest, principal: Identity):
        return participation.configure(principal, body)

    @app.delete("/v1/participation")
    def withdraw(principal: Identity):
        return participation.withdraw(principal)

    @app.post("/v1/participation/events")
    def report(body: TaskSignal, principal: Identity):
        receipt = participation.record(principal, body)
        return JSONResponse(receipt, status_code=201 if receipt["status"] == "accepted" else 200)

    @app.get("/v1/participation/summary")
    def summary(principal: Identity):
        return participation.summary(principal)
