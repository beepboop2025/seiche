"""Explicit, contributor-side adapters. No credentials are uploaded or trading calls made."""

from datetime import datetime
import json

import httpx

from .contracts import Contribution
from .store import Rejected, digest


class AlpacaReadOnly:
    """Use a separately obtained read-only OAuth token, never a trading API secret."""

    def __init__(self, oauth_token, *, paper):
        if not oauth_token or not isinstance(paper, bool):
            raise ValueError("Explicit OAuth token and paper/live selection are required")
        self.token = oauth_token
        self.paper = paper
        self.origin = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"

    def fills(self, after, until, *, max_pages=10):
        # Fixed GET endpoint only. An interrupted read can be replayed with immutable IDs.
        start, end = datetime.fromisoformat(after.replace("Z", "+00:00")), datetime.fromisoformat(until.replace("Z", "+00:00"))
        if not start.tzinfo or not end.tzinfo or start >= end or not 1 <= max_pages <= 100:
            raise ValueError("A bounded UTC interval and page budget are required")
        params = {"activity_types": "FILL", "after": after, "until": until, "direction": "asc", "page_size": 100}
        seen = set()
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            for _ in range(max_pages):
                with client.stream("GET", self.origin+"/v2/account/activities", params=params,
                                   headers={"Authorization": "Bearer "+self.token}) as response:
                    if response.status_code != 200:
                        raise Rejected("alpaca_http_"+str(response.status_code))
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise Rejected("alpaca_response_too_large")
                    rows = json.loads(raw)
                if not isinstance(rows, list) or len(rows) > 100:
                    raise Rejected("alpaca_schema_changed")
                for row in rows:
                    if row.get("activity_type") != "FILL" or not isinstance(row.get("id"), str):
                        raise Rejected("alpaca_fill_schema_changed")
                    if row["id"] in seen:
                        raise Rejected("alpaca_repeated_page")
                    seen.add(row["id"])
                    yield row
                if len(rows) < 100:
                    return
                params["page_token"] = rows[-1]["id"]
        raise Rejected("alpaca_page_budget_exhausted_replay_required")

    def event(self, row, *, agent_id, agent_version, account_ref, journey_id):
        # Attribution and outcome remain source-reported; OAuth alone proves neither AI control nor profit.
        return Contribution.model_validate({
            "event_id": "alpaca:"+digest((account_ref+":"+row["id"]).encode()),
            "agent_id": agent_id, "agent_version": agent_version, "journey_id": journey_id,
            "account_ref": account_ref, "kind": "fill", "occurred_at": row["transaction_time"],
            "mode": "paper" if self.paper else "live", "venue": "alpaca", "economic_domain": "trading", "evidence_sha256": digest(row),
            "external_ref": row["id"], "instrument": row["symbol"], "side": row["side"],
            "quantity": row["qty"], "price": row["price"],
        }).model_dump(mode="json")


def merchant_event(*, event_id, agent_id, agent_version, journey_id, kind, economic_domain, occurred_at, external_ref, evidence_sha256, amount=None, currency=None):
    """Normalize a merchant/payment system's own verified receipt after its webhook authentication.

    This is deliberately not a generic webhook verifier: each provider has its own signature
    contract. The caller must verify that contract locally before producing this bounded record.
    """
    if kind not in {"payment_authorized", "payment_settled", "fulfilled", "refunded", "failed"}:
        raise ValueError("Unsupported commerce lifecycle event")
    return Contribution.model_validate(dict(event_id=event_id, agent_id=agent_id, agent_version=agent_version,
        journey_id=journey_id, kind=kind, economic_domain=economic_domain, occurred_at=occurred_at, mode="live", venue="merchant",
        external_ref=external_ref, evidence_sha256=evidence_sha256, amount=amount, currency=currency)).model_dump(mode="json")
