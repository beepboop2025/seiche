"""Publication policy seam for partial local gauges.

Computation and publication are deliberately separate: engines can return a
mix of READY, STALE and UNAVAILABLE components without deciding whether the
product as a whole should be shown to users.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from seiche.kernel.engines import KernelResult


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
    """Decide whether a partially covered local gauge may publish.

    TODO(learning): implement the 5-10 line product policy here. The safe
    placeholder withholds the aggregate until a strict-vs-quorum policy is
    chosen; individual component results remain publishable either way.
    """

    del components, required_components
    return PublicationDecision(
        status=PublicationStatus.UNAVAILABLE,
        publish_value=False,
        reason="local-gauge publication policy has not been selected",
    )
