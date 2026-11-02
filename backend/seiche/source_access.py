"""Reviewed source-access exceptions: a refused collection is stale evidence, not a fault.

Each entry is one owner decision for one source, kept narrow on purpose. An
HTTP access refusal from that source (status 403 or 429, including a Cloudflare
challenge) keeps the last good observation with its own date, shows it as
stale with a named reason, and does not enter the board's faults. Every other
failure of the same source (a changed format, an unexpected URL or redirect, a
5xx, a timeout, a store error) stays a fault, and every other source is
unchanged. Adding a source here is a code change that needs review; the test
pins this table.

This does not admit a faulted source into a gate. The owner-signed
source-degradation admission (``ops/release/source_degradation.py``) keeps its
own contract for sources that do fault.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError

from seiche import store

SCHEMA = "seiche.source-access-refusal.v1"


@dataclass(frozen=True)
class AccessRefusalPolicy:
    source: str
    mnemonics: tuple[str, ...]
    statuses: frozenset[int]
    decision: str


ACCESS_REFUSAL_POLICIES: dict[str, AccessRefusalPolicy] = {
    # centralbank.ae answers the collector with HTTP 403 and
    # "cf-mitigated: challenge". Until automated access is granted, DONIA is
    # stale, not blocking.
    "cbuae_donia": AccessRefusalPolicy(
        source="cbuae_donia",
        mnemonics=("DONIA",),
        statuses=frozenset({403, 429}),
        decision="Owner decision 2026-11-02: stale, not blocking, until automated access is granted.",
    ),
}


def refusal(source: str, exc: BaseException) -> dict | None:
    """The admitted access refusal behind ``exc``, or None when it stays a fault."""

    policy = ACCESS_REFUSAL_POLICIES.get(source)
    if policy is None or not isinstance(exc, HTTPError) or exc.code not in policy.statuses:
        return None
    headers = exc.headers
    challenge = headers is not None and str(headers.get("cf-mitigated", "")).strip().lower() == "challenge"
    return {"source": source, "status": exc.code, "challenge": challenge}


def reason(record: dict, *, retained: bool) -> str:
    kind = f"HTTP {record['status']}" + (" challenge" if record.get("challenge") else "")
    held = "last good observation retained" if retained else "no validated observation is retained"
    return f"official source refuses automated access ({kind}); {held}"


def _key(source: str) -> str:
    return f"source_access_refusal:{source}"


def record_refusal(record: dict, *, refused_at: str, last_good_fetched_at: str | None,
                   last_good_observation_date: str | None) -> dict:
    """Persist the refusal against the capture it leaves in place."""

    policy = ACCESS_REFUSAL_POLICIES[record["source"]]
    marker = {
        "schema": SCHEMA, "source": policy.source, "status": record["status"],
        "challenge": record["challenge"], "refused_at": refused_at,
        "last_good_fetched_at": last_good_fetched_at,
        "last_good_observation_date": last_good_observation_date,
        "decision": policy.decision,
    }
    store.save_blob(_key(policy.source), marker)
    return marker


def active_refusal(source: str, *, current_fetched_at: str | None) -> dict | None:
    """The refusal still in force: no validated capture has replaced its last good one."""

    if source not in ACCESS_REFUSAL_POLICIES:
        return None
    marker = store.load_blob(_key(source))
    if (not isinstance(marker, dict) or marker.get("schema") != SCHEMA
            or marker.get("source") != source
            or marker.get("status") not in ACCESS_REFUSAL_POLICIES[source].statuses
            or marker.get("last_good_fetched_at") != current_fetched_at):
        return None
    return marker


def stale_override(source: str, mnemonic: str, *, current_fetched_at: str | None) -> dict | None:
    """Fields that show a refused source's series as stale, or None.

    A retained observation reads stale with the refusal reason; a series with
    no validated observation keeps its unavailability and gains the reason.
    """

    policy = ACCESS_REFUSAL_POLICIES.get(source)
    if policy is None or mnemonic not in policy.mnemonics:
        return None
    marker = active_refusal(source, current_fetched_at=current_fetched_at)
    if marker is None:
        return None
    retained = current_fetched_at is not None
    text = reason(marker, retained=retained)
    refusal_view = {"status": marker["status"], "challenge": marker["challenge"],
                    "refused_at": marker["refused_at"]}
    if retained:
        return {"staleness": "stale", "stale_reason": text, "freshness_basis": text,
                "access_refusal": refusal_view}
    return {"unavailable_reason": text, "access_refusal": refusal_view}
