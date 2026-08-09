"""Helpers shared by pre-support reference packs."""

from __future__ import annotations

from seiche.domain.observation import (
    CanonicalUnit,
    DayCountConvention,
    RateCompounding,
    SemanticRole,
)
from seiche.markets.base import Capability, CapabilityStatus, InstrumentSpec


_UNIVERSAL_CAPABILITIES = (
    "policy_relative_overnight",
    "corridor_pressure",
    "secured_unsecured",
    "term_funding",
    "liquidity_buffer_drain",
    "facility_usage",
    "volume_dislocation",
    "tail_dispersion",
    "calendar_amplification",
    "cross_basin_coupling",
)


def pre_support_capabilities(reason: str) -> tuple[Capability, ...]:
    return tuple(
        Capability(name, CapabilityStatus.UNAVAILABLE, reason=reason)
        for name in _UNIVERSAL_CAPABILITIES
    ) + (
        Capability(
            "historical_prediction",
            CapabilityStatus.FORWARD_ONLY,
            reason="point-in-time canonical history has not accrued",
        ),
    )


def rate_instrument(
    instrument_id: str,
    mnemonic: str,
    role: SemanticRole,
    adapter_id: str,
    day_count: DayCountConvention,
    *,
    compounding: RateCompounding = RateCompounding.SIMPLE,
) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=instrument_id,
        mnemonic=mnemonic,
        semantic_role=role,
        source_adapter_id=adapter_id,
        source_unit="percent",
        canonical_unit=CanonicalUnit.BASIS_POINTS,
        value_multiplier=100,
        rate_compounding=compounding,
        day_count=day_count,
    )
