"""Publication policy seam for partial local gauges.

Computation and publication are deliberately separate: engines can return a
mix of READY, STALE and UNAVAILABLE components without deciding whether the
product as a whole should be shown to users.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from seiche.kernel.engines import KernelResult, KernelStatus


class PublicationStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    status: PublicationStatus
    publish_value: bool
    reason: str


def decide_local_gauge_publication(
    components: Mapping[str, KernelResult],
    required_components: frozenset[str],
) -> PublicationDecision:
    """Apply a strict required-component policy to a partial local gauge."""

    missing = required_components - components.keys()
    blocked = {
        name
        for name in required_components & components.keys()
        if components[name].status
        in {KernelStatus.UNAVAILABLE, KernelStatus.INSUFFICIENT_HISTORY}
    }
    if missing or blocked:
        unavailable = ", ".join(sorted(missing | blocked))
        return PublicationDecision(
            PublicationStatus.UNAVAILABLE,
            False,
            f"required components unavailable: {unavailable}",
        )
    stale = {
        name
        for name in required_components
        if components[name].status is KernelStatus.STALE
    }
    if stale:
        return PublicationDecision(
            PublicationStatus.DEGRADED,
            True,
            f"required components stale: {', '.join(sorted(stale))}",
        )
    return PublicationDecision(
        PublicationStatus.READY,
        True,
        "all required components are ready",
    )
