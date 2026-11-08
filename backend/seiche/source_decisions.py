"""Owner source-use decisions that gate specific original-publisher series.

The registry is ``source_decisions.json`` beside this module. Adapters read it
when they decode a capture, and the public rights projection reads it before
it lets a value or a dependent reading through. A gated series stays withheld
when its decision is absent, malformed or not ``allowed``: removing the entry
is enough to close it again.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REGISTRY_PATH = Path(__file__).with_name("source_decisions.json")
SCHEMA = "seiche.source-use-decisions.v1"

FED_CP = "seiche.source-use.2026-11-08.fed-cp"
BLS_API = "seiche.source-use.2026-11-08.bls-api"
BOJ_TOTAL_ASSETS = "seiche.source-use.2026-11-08.boj-total-assets"
BOE_SONIA = "seiche.source-use.2026-11-08.boe-sonia"

_REQUIRED = ("id", "decided_on", "decided_by", "owner_words", "decision",
             "redistribution_status", "sources", "series", "terms_urls")


@lru_cache(maxsize=1)
def registry() -> dict:
    """The validated registry; an unreadable file yields no decisions."""
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "decisions": []}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "decisions": []}
    decisions = [
        row for row in data.get("decisions", [])
        if isinstance(row, dict) and all(row.get(key) for key in _REQUIRED)
    ]
    return {**data, "decisions": decisions}


def decision(decision_id: str | None) -> dict | None:
    if not decision_id:
        return None
    for row in registry()["decisions"]:
        if row["id"] == decision_id:
            return row
    return None


def allows(decision_id: str | None) -> bool:
    row = decision(decision_id)
    return row is not None and row.get("redistribution_status") == "allowed"


def summary(decision_id: str | None) -> dict | None:
    """The public citation for one decision, safe to embed in payloads."""
    row = decision(decision_id)
    if row is None:
        return None
    return {key: row[key] for key in (
        "id", "decided_on", "decided_by", "owner_words", "decision",
        "redistribution_status", "conditions", "terms_urls",
    ) if key in row}
