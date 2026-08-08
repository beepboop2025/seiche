"""Shared historical-evidence claim boundary for every Seiche surface.

Historical scores can be useful without being eligible for a validated
backtest claim.  REST, MCP, Telegram, and the web UI must therefore derive the
same status and eligibility flags from a snapshot instead of inventing copy
from the presence of a scoreboard.
"""

from __future__ import annotations

from typing import Any, Mapping


DEFAULT_STATUS = "FINAL_VINTAGE_CONSTRUCTION_PIT"


def historical_evidence(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return an explicit evidence boundary, failing closed for legacy data."""

    source = payload if isinstance(payload, Mapping) else {}
    candidates = [source.get("historical_evidence")]

    deep = source.get("deep")
    if isinstance(deep, Mapping):
        candidates.append(deep.get("historical_evidence"))
        history = deep.get("history")
        if isinstance(history, Mapping):
            candidates.append(history.get("vintage_evidence"))

    for candidate in candidates:
        if isinstance(candidate, Mapping):
            boundary = dict(candidate)
            boundary.setdefault("status", DEFAULT_STATUS)
            boundary.setdefault("validated_backtest_eligible", False)
            boundary.setdefault("real_money_eligible", False)
            return boundary

    return {
        "status": DEFAULT_STATUS,
        "validated_backtest_eligible": False,
        "real_money_eligible": False,
        "reason": (
            "historical reconstruction uses final/current-vintage public data; "
            "no complete as-published manifest accompanied this payload"
        ),
    }
