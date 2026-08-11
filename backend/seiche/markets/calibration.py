"""Versioned local-gauge calibrations over universal semantic engines.

The kernel remains market-neutral.  This module is pack-side configuration:
it chooses which semantic roles anchor each market, how raw local distances
are oriented toward stress, and which components are mandatory to publish.
The first production generation is deliberately labelled ``forward-v1``;
it may accrue a paper record but cannot claim retrospective validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from seiche.domain.observation import SemanticRole


class EngineKind(StrEnum):
    POLICY_RELATIVE = "policy_relative"
    CORRIDOR = "corridor"
    SECURED_UNSECURED = "secured_unsecured"
    TERM_SLOPE = "term_slope"
    FUNDING_BILL = "funding_bill"
    LIQUIDITY_DRAIN = "liquidity_drain"
    FACILITY_USAGE = "facility_usage"
    TAIL_DISLOCATION = "tail_dislocation"
    VOLUME_DISLOCATION = "volume_dislocation"


@dataclass(frozen=True, slots=True)
class ComponentCalibration:
    component_id: str
    kind: EngineKind
    weight: float
    required: bool
    stress_direction: int
    center: float
    scale: float
    minimum_history: int = 60
    overnight_role: SemanticRole | None = None
    anchor_role: SemanticRole | None = None
    term_role: SemanticRole | None = None
    funding_role: SemanticRole | None = None
    buffer_role: SemanticRole | None = None

    def __post_init__(self) -> None:
        if not self.component_id.strip():
            raise ValueError("component_id is required")
        if self.weight <= 0 or self.scale <= 0:
            raise ValueError("calibration weight and scale must be positive")
        if self.stress_direction not in {-1, 1}:
            raise ValueError("stress_direction must be -1 or 1")
        if self.minimum_history < 1:
            raise ValueError("minimum_history must be positive")
        if self.kind is EngineKind.POLICY_RELATIVE and (
            self.overnight_role is None or self.anchor_role is None
        ):
            raise ValueError("policy-relative calibration needs overnight and anchor roles")
        if self.kind is EngineKind.CORRIDOR and self.overnight_role is None:
            raise ValueError("corridor calibration needs an overnight role")
        if self.kind is EngineKind.TERM_SLOPE and (
            self.term_role is None or self.overnight_role is None
        ):
            raise ValueError("term-slope calibration needs term and overnight roles")
        if self.kind is EngineKind.FUNDING_BILL and self.funding_role is None:
            raise ValueError("funding-bill calibration needs a funding role")
        if self.kind is EngineKind.LIQUIDITY_DRAIN and self.buffer_role is None:
            raise ValueError("liquidity-drain calibration needs a buffer role")


@dataclass(frozen=True, slots=True)
class LocalCalibration:
    calibration_id: str
    market_id: str
    components: tuple[ComponentCalibration, ...]
    maturity: str = "FORWARD_ONLY"

    def __post_init__(self) -> None:
        if not self.calibration_id.strip() or not self.market_id.strip():
            raise ValueError("calibration and market identifiers are required")
        ids = [item.component_id for item in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("component IDs must be unique within a calibration")
        if not any(item.required for item in self.components):
            raise ValueError("a local calibration needs at least one required component")

    @property
    def required_components(self) -> frozenset[str]:
        return frozenset(
            item.component_id for item in self.components if item.required
        )


def _component(
    component_id: str,
    kind: EngineKind,
    weight: float,
    *,
    required: bool = False,
    direction: int = 1,
    center: float = 0.0,
    scale: float = 20.0,
    history: int = 60,
    overnight: SemanticRole | None = None,
    anchor: SemanticRole | None = None,
    term: SemanticRole | None = None,
    funding: SemanticRole | None = None,
    buffer: SemanticRole | None = None,
) -> ComponentCalibration:
    return ComponentCalibration(
        component_id,
        kind,
        weight,
        required,
        direction,
        center,
        scale,
        history,
        overnight,
        anchor,
        term,
        funding,
        buffer,
    )


_POLICY_SECURED_TARGET = _component(
    "policy_relative_overnight",
    EngineKind.POLICY_RELATIVE,
    0.28,
    required=True,
    scale=20,
    overnight=SemanticRole.SECURED_OVERNIGHT,
    anchor=SemanticRole.POLICY_TARGET,
)
_SECURED_UNSECURED = _component(
    "secured_unsecured",
    EngineKind.SECURED_UNSECURED,
    0.16,
    scale=15,
)
_FUNDING_BILL = _component(
    "term_funding",
    EngineKind.FUNDING_BILL,
    0.16,
    center=40,
    scale=30,
    funding=SemanticRole.CP_3M,
)
_LIQUIDITY_RESERVES = _component(
    "liquidity_buffer_drain",
    EngineKind.LIQUIDITY_DRAIN,
    0.12,
    scale=2,
    history=20,
    buffer=SemanticRole.RESERVE_BALANCES,
)
_FACILITY = _component(
    "facility_usage",
    EngineKind.FACILITY_USAGE,
    0.10,
    scale=1,
    history=20,
)
_TAIL = _component(
    "tail_dispersion",
    EngineKind.TAIL_DISLOCATION,
    0.10,
    scale=10,
)
_VOLUME = _component(
    "volume_dislocation",
    EngineKind.VOLUME_DISLOCATION,
    0.08,
    direction=-1,
    scale=25,
    history=20,
)


_CALIBRATIONS = {
    "US-USD": LocalCalibration(
        "us-usd-legacy-parity-v1",
        "US-USD",
        (
            _POLICY_SECURED_TARGET,
            _SECURED_UNSECURED,
            _FUNDING_BILL,
            _LIQUIDITY_RESERVES,
            _FACILITY,
            _TAIL,
            _VOLUME,
        ),
    ),
    "EA-EUR": LocalCalibration(
        "ea-eur-local-forward-v1",
        "EA-EUR",
        (
            _component(
                "policy_relative_overnight",
                EngineKind.POLICY_RELATIVE,
                0.55,
                required=True,
                center=8,
                scale=8,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
                anchor=SemanticRole.POLICY_FLOOR,
            ),
            _component(
                "corridor_pressure",
                EngineKind.CORRIDOR,
                0.25,
                center=25,
                scale=20,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
            ),
            _component(
                "liquidity_buffer_drain",
                EngineKind.LIQUIDITY_DRAIN,
                0.20,
                scale=2,
                history=20,
                buffer=SemanticRole.SYSTEM_LIQUIDITY,
            ),
        ),
    ),
    "UK-GBP": LocalCalibration(
        "uk-gbp-local-forward-v1",
        "UK-GBP",
        (
            _component(
                "policy_relative_overnight",
                EngineKind.POLICY_RELATIVE,
                0.75,
                required=True,
                scale=12,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
                anchor=SemanticRole.POLICY_TARGET,
            ),
            _component(
                "secured_unsecured",
                EngineKind.SECURED_UNSECURED,
                0.25,
                scale=12,
            ),
        ),
    ),
    "JP-JPY": LocalCalibration(
        "jp-jpy-local-forward-v1",
        "JP-JPY",
        (
            _component(
                "policy_relative_overnight",
                EngineKind.POLICY_RELATIVE,
                0.75,
                required=True,
                center=-25,
                scale=15,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
                anchor=SemanticRole.POLICY_CEILING,
            ),
            _component(
                "liquidity_buffer_drain",
                EngineKind.LIQUIDITY_DRAIN,
                0.25,
                scale=2,
                history=20,
                buffer=SemanticRole.RESERVE_BALANCES,
            ),
        ),
    ),
    "CN-CNY": LocalCalibration(
        "cn-cny-local-forward-v1",
        "CN-CNY",
        (
            _component(
                "term_funding",
                EngineKind.TERM_SLOPE,
                0.70,
                required=True,
                scale=25,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
                term=SemanticRole.TERM_1W,
            ),
            _component(
                "liquidity_buffer_drain",
                EngineKind.LIQUIDITY_DRAIN,
                0.30,
                scale=3,
                history=20,
                buffer=SemanticRole.SYSTEM_LIQUIDITY,
            ),
        ),
    ),
    "HK-HKD": LocalCalibration(
        "hk-hkd-local-forward-v1",
        "HK-HKD",
        (
            _component(
                "liquidity_buffer_drain",
                EngineKind.LIQUIDITY_DRAIN,
                1.0,
                required=True,
                scale=5,
                history=20,
                buffer=SemanticRole.SYSTEM_LIQUIDITY,
            ),
        ),
    ),
    "IN-INR": LocalCalibration(
        "in-inr-local-forward-v1",
        "IN-INR",
        (
            _component(
                "corridor_pressure",
                EngineKind.CORRIDOR,
                0.40,
                required=True,
                center=50,
                scale=25,
                history=20,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
            ),
            _component(
                "policy_relative_overnight",
                EngineKind.POLICY_RELATIVE,
                0.20,
                scale=15,
                history=20,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
                anchor=SemanticRole.POLICY_TARGET,
            ),
            _component(
                "liquidity_buffer_drain",
                EngineKind.LIQUIDITY_DRAIN,
                0.15,
                scale=5,
                history=20,
                buffer=SemanticRole.SYSTEM_LIQUIDITY,
            ),
            _FACILITY,
            _VOLUME,
        ),
    ),
    "AU-AUD": LocalCalibration(
        "au-aud-local-forward-v1",
        "AU-AUD",
        (
            _component(
                "policy_relative_overnight",
                EngineKind.POLICY_RELATIVE,
                0.65,
                required=True,
                scale=12,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
                anchor=SemanticRole.POLICY_TARGET,
            ),
            _component(
                "corridor_pressure",
                EngineKind.CORRIDOR,
                0.35,
                center=50,
                scale=25,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
            ),
        ),
    ),
    "NZ-NZD": LocalCalibration(
        "nz-nzd-local-forward-v2",
        "NZ-NZD",
        (
            _component(
                "corridor_pressure",
                EngineKind.CORRIDOR,
                1.0,
                required=True,
                center=50,
                scale=25,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
            ),
        ),
    ),
    "SG-SGD": LocalCalibration(
        "sg-sgd-local-forward-v1",
        "SG-SGD",
        (
            _component(
                "corridor_pressure",
                EngineKind.CORRIDOR,
                0.75,
                required=True,
                center=50,
                scale=25,
                history=20,
                overnight=SemanticRole.UNSECURED_OVERNIGHT,
            ),
            _component(
                "term_funding",
                EngineKind.FUNDING_BILL,
                0.25,
                center=40,
                scale=30,
                funding=SemanticRole.CP_3M,
            ),
        ),
    ),
}


def get_local_calibration(market_id: str) -> LocalCalibration:
    try:
        return _CALIBRATIONS[market_id.upper()]
    except KeyError as exc:
        raise KeyError(f"no local calibration for {market_id!r}") from exc


def list_local_calibrations() -> tuple[LocalCalibration, ...]:
    return tuple(_CALIBRATIONS[key] for key in sorted(_CALIBRATIONS))
