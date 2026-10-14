"""Small, purpose-bounded contribution contract. No free-form prompts or portfolios."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Label = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Amount = Annotated[str, Field(max_length=60, pattern=r"^-?[0-9]{1,36}(\.[0-9]{1,18})?$")]


class Contribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    event_id: Label
    agent_id: Label
    agent_version: Label
    journey_id: Label
    kind: Literal["decision", "tool_call", "order", "fill", "position_snapshot", "cash_flow",
                  "payment_authorized", "payment_settled", "fulfilled", "refunded", "failed"]
    occurred_at: AwareDatetime
    mode: Literal["live", "paper", "backtest", "test", "unknown"]
    venue: Label
    economic_domain: Literal["trading", "digital_service", "physical_goods", "unknown"] = "unknown"
    account_ref: Label | None = None
    evidence_sha256: Digest
    external_ref: Label | None = None
    decision_ref: Label | None = None
    instrument: Label | None = None
    side: Literal["buy", "sell", "in", "out"] | None = None
    price: Amount | None = None
    quantity: Amount | None = None
    amount: Amount | None = None
    fee: Amount | None = None
    currency: Label | None = None
    tool: Label | None = None
    outcome: Literal["unknown", "success", "error", "refused"] = "unknown"
    model_version: Label | None = None
    config_version: Label | None = None
    trace_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")] | None = None

    @model_validator(mode="after")
    def transaction_fields(self):
        if self.kind in {"payment_authorized", "payment_settled", "fulfilled", "refunded"} and self.economic_domain not in {"digital_service", "physical_goods"}:
            raise ValueError("Commerce events require a separate digital-service or physical-goods domain")
        if self.kind in {"fill", "payment_settled", "refunded"} and not self.external_ref:
            raise ValueError("An independently reconcilable external reference is required")
        if self.amount is not None and not self.currency:
            raise ValueError("An amount requires its currency or native asset identifier")
        if self.kind == "fill" and not all([self.instrument, self.side in {"buy", "sell"}, self.quantity]):
            raise ValueError("A fill needs instrument, buy/sell side and quantity")
        if self.price is not None and Decimal(self.price) < 0:
            raise ValueError("Price must be nonnegative")
        if self.quantity is not None and Decimal(self.quantity) <= 0:
            raise ValueError("Quantity must be positive")
        return self


class IdentityClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: Label
    agent_version: Label
    venue: Literal["hyperliquid", "base", "alpaca", "merchant"]
    account: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
    evidence_sha256: Digest
    valid_from: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def period(self):
        if self.valid_until <= self.valid_from:
            raise ValueError("Identity validity must have a positive duration")
        if self.venue in {"hyperliquid", "base"}:
            import re
            if not re.fullmatch(r"0x[a-fA-F0-9]{40}", self.account):
                raise ValueError("An EVM account address is required")
        return self


class PilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization: Annotated[str, Field(min_length=2, max_length=100)]
    contact_email: Annotated[str, Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]
    role: Literal["agent_operator", "platform", "broker", "merchant", "research_buyer"]
    use_case: Literal["performance_history", "due_diligence", "commerce_demand", "research_dataset"]
    notice_accepted: Literal[True]


CONTRACT = {
    "schema": "agent-activity.contract.v1",
    "version": "2026-10-14",
    "public_collection": "Bounded public registry, service and protocol observations; global coverage unknown.",
    "private_ingestion": "A separately reviewed contributor credential is required. Network and learning tokens are not accepted.",
    "identity": "Self-declared, reviewer-attested and wallet-controlled are different claims. A signature alone does not prove AI control.",
    "outcomes": "Submitted outcomes remain source-reported until separately reconciled. No trading return is inferred from transfers.",
    "purposes": ["private_evidence", "aggregate_resale"],
    "shared_training": False,
    "record_level_resale": False,
    "retention_days": 90,
    "pilot_request_retention_days": 30,
    "maximum_request_bytes": 65536,
    "maximum_contributor_events_per_day": 10000,
    "revocation": "Withdrawal removes private events, claims and reviews. Exports require live validation before delivery.",
    "prohibited_fields": ["prompts", "raw tool arguments", "credentials", "personal payment details"],
    "contribution_schema": Contribution.model_json_schema(),
    "identity_schema": IdentityClaim.model_json_schema(),
}
