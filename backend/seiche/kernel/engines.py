"""Universal engines over semantic roles.

This module deliberately contains no source mnemonics, jurisdiction names, or
local policy terminology. A monetary-area pack chooses instruments and maps
them into roles; these functions only operate on those roles and canonical
units.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from statistics import median

import numpy as np
import pandas as pd

from seiche.domain.observation import (
    CanonicalUnit,
    Observation,
    SemanticRole,
    StalenessState,
)


class KernelStatus(StrEnum):
    READY = "READY"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


@dataclass(frozen=True, slots=True)
class RoleSeries:
    role: SemanticRole
    instrument_id: str
    unit: CanonicalUnit
    points: pd.Series
    observations: tuple[Observation, ...]

    @property
    def latest(self) -> Observation:
        return max(self.observations, key=lambda item: (item.event_time, item.knowledge_time))

    @property
    def stale(self) -> bool:
        return self.latest.staleness in {StalenessState.STALE, StalenessState.DEAD}


@dataclass(frozen=True, slots=True)
class RoleLookup:
    series: RoleSeries | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class MarketPanel:
    market_id: str
    currency: str
    series: tuple[RoleSeries, ...]

    @classmethod
    def from_observations(cls, observations: Iterable[Observation]) -> MarketPanel:
        captured = tuple(observations)
        if not captured:
            raise ValueError("a market panel needs at least one observation")
        markets = {item.market_id for item in captured}
        currencies = {item.currency for item in captured}
        if len(markets) != 1 or len(currencies) != 1:
            raise ValueError("a market panel cannot mix markets or currencies")
        # Preserve the market identity even when every row explicitly says
        # UNAVAILABLE. Engines can then return null-state results instead of a
        # panel-construction exception or, worse, a fabricated zero.
        usable = tuple(item for item in captured if item.usable)

        grouped: dict[tuple[SemanticRole, str], list[Observation]] = {}
        for observation in usable:
            grouped.setdefault(
                (observation.semantic_role, observation.instrument_id), []
            ).append(observation)
        role_series: list[RoleSeries] = []
        for (role, instrument_id), group in grouped.items():
            latest_by_event: dict[object, Observation] = {}
            for observation in group:
                current = latest_by_event.get(observation.event_time)
                if current is None or observation.knowledge_time > current.knowledge_time:
                    latest_by_event[observation.event_time] = observation
            ordered = tuple(
                sorted(latest_by_event.values(), key=lambda item: item.event_time)
            )
            units = {item.canonical_unit for item in ordered}
            if len(units) != 1:
                raise ValueError(f"instrument {instrument_id!r} mixes canonical units")
            points = pd.Series(
                [float(item.value) for item in ordered],
                index=pd.DatetimeIndex([item.event_time for item in ordered]),
                dtype=float,
            )
            role_series.append(
                RoleSeries(role, instrument_id, next(iter(units)), points, ordered)
            )
        return cls(
            market_id=next(iter(markets)),
            currency=next(iter(currencies)),
            series=tuple(
                sorted(role_series, key=lambda item: (item.role.value, item.instrument_id))
            ),
        )

    def lookup(
        self,
        role: SemanticRole,
        *,
        instrument_id: str | None = None,
    ) -> RoleLookup:
        candidates = [item for item in self.series if item.role is role]
        if instrument_id is not None:
            candidates = [item for item in candidates if item.instrument_id == instrument_id]
        if not candidates:
            detail = f"instrument {instrument_id!r}" if instrument_id else "mapped instrument"
            return RoleLookup(None, f"{role.value}: no {detail}")
        if len(candidates) > 1:
            names = ", ".join(item.instrument_id for item in candidates)
            return RoleLookup(
                None,
                f"{role.value}: ambiguous instruments ({names}); pack must select one",
            )
        return RoleLookup(candidates[0], None)


@dataclass(frozen=True, slots=True)
class KernelResult:
    engine_id: str
    status: KernelStatus
    value: float | None
    unit: str | None
    event_cutoff: str | None
    knowledge_cutoff: str | None
    input_roles: tuple[str, ...]
    method: str
    reason: str | None = None
    stale_inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "engine_id": self.engine_id,
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "event_cutoff": self.event_cutoff,
            "knowledge_cutoff": self.knowledge_cutoff,
            "input_roles": list(self.input_roles),
            "method": self.method,
            "reason": self.reason,
            "stale_inputs": list(self.stale_inputs),
        }


def _missing(engine_id: str, roles: tuple[SemanticRole, ...], reason: str) -> KernelResult:
    return KernelResult(
        engine_id=engine_id,
        status=KernelStatus.UNAVAILABLE,
        value=None,
        unit=None,
        event_cutoff=None,
        knowledge_cutoff=None,
        input_roles=tuple(role.value for role in roles),
        method="not computed",
        reason=reason,
    )


def _insufficient(
    engine_id: str,
    roles: tuple[SemanticRole, ...],
    method: str,
    count: int,
    required: int,
) -> KernelResult:
    return KernelResult(
        engine_id=engine_id,
        status=KernelStatus.INSUFFICIENT_HISTORY,
        value=None,
        unit=None,
        event_cutoff=None,
        knowledge_cutoff=None,
        input_roles=tuple(role.value for role in roles),
        method=method,
        reason=f"{count} aligned observations; {required} required",
    )


def _ready(
    engine_id: str,
    value: float,
    unit: str,
    roles: tuple[SemanticRole, ...],
    inputs: tuple[RoleSeries, ...],
    method: str,
) -> KernelResult:
    if not np.isfinite(value):
        return _missing(engine_id, roles, "calculation produced a non-finite value")
    event_sets = [set(item.points.index) for item in inputs]
    common_events = set.intersection(*event_sets) if event_sets else set()
    cutoff = max(common_events) if common_events else min(
        item.latest.event_time for item in inputs
    )
    relevant = [
        observation
        for item in inputs
        for observation in item.observations
        if observation.event_time <= cutoff
    ]
    knowledge = max(item.knowledge_time for item in relevant)
    stale = tuple(item.role.value for item in inputs if item.stale)
    return KernelResult(
        engine_id=engine_id,
        status=KernelStatus.STALE if stale else KernelStatus.READY,
        value=round(float(value), 6),
        unit=unit,
        event_cutoff=cutoff.isoformat(),
        knowledge_cutoff=knowledge.isoformat(),
        input_roles=tuple(role.value for role in roles),
        method=method,
        stale_inputs=stale,
    )


def _resolve(panel: MarketPanel, roles: tuple[SemanticRole, ...]) -> tuple[RoleSeries, ...] | str:
    resolved: list[RoleSeries] = []
    for role in roles:
        lookup = panel.lookup(role)
        if lookup.series is None:
            return lookup.reason or f"{role.value} unavailable"
        resolved.append(lookup.series)
    return tuple(resolved)


def _aligned(inputs: tuple[RoleSeries, ...]) -> pd.DataFrame:
    return pd.concat(
        [item.points.rename(item.role.value) for item in inputs],
        axis=1,
        join="inner",
    ).dropna()


def policy_relative_overnight_pressure(
    panel: MarketPanel,
    *,
    overnight_role: SemanticRole,
    anchor_role: SemanticRole,
) -> KernelResult:
    engine_id = "policy_relative_overnight"
    roles = (overnight_role, anchor_role)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    aligned = _aligned(inputs)
    if aligned.empty:
        return _insufficient(engine_id, roles, "overnight minus policy anchor", 0, 1)
    value = aligned.iloc[-1, 0] - aligned.iloc[-1, 1]
    return _ready(
        engine_id,
        value,
        CanonicalUnit.BASIS_POINTS.value,
        roles,
        inputs,
        "overnight rate minus pack-selected local policy anchor",
    )


def corridor_position(
    panel: MarketPanel,
    *,
    overnight_role: SemanticRole,
) -> KernelResult:
    engine_id = "corridor_pressure"
    roles = (
        overnight_role,
        SemanticRole.POLICY_FLOOR,
        SemanticRole.POLICY_CEILING,
    )
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    aligned = _aligned(inputs)
    if aligned.empty:
        return _insufficient(engine_id, roles, "position within local corridor", 0, 1)
    overnight, floor, ceiling = aligned.iloc[-1]
    width = ceiling - floor
    if width <= 0:
        return _missing(engine_id, roles, "policy corridor width is non-positive")
    value = (overnight - floor) / width * 100.0
    return _ready(
        engine_id,
        value,
        "percent_of_corridor",
        roles,
        inputs,
        "100 × (overnight − floor) / (ceiling − floor); breaches remain outside 0..100",
    )


def secured_unsecured_wedge(panel: MarketPanel) -> KernelResult:
    engine_id = "secured_unsecured"
    roles = (SemanticRole.SECURED_OVERNIGHT, SemanticRole.UNSECURED_OVERNIGHT)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    aligned = _aligned(inputs)
    if aligned.empty:
        return _insufficient(engine_id, roles, "secured minus unsecured", 0, 1)
    value = aligned.iloc[-1, 0] - aligned.iloc[-1, 1]
    return _ready(
        engine_id,
        value,
        CanonicalUnit.BASIS_POINTS.value,
        roles,
        inputs,
        "secured overnight minus unsecured overnight",
    )


def term_funding_slope(
    panel: MarketPanel,
    *,
    term_role: SemanticRole,
    overnight_role: SemanticRole,
) -> KernelResult:
    if term_role not in {
        SemanticRole.TERM_1W,
        SemanticRole.TERM_1M,
        SemanticRole.TERM_3M,
    }:
        raise ValueError("term_role must be a term-money role")
    engine_id = "term_funding_slope"
    roles = (term_role, overnight_role)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    aligned = _aligned(inputs)
    if aligned.empty:
        return _insufficient(engine_id, roles, "term rate minus overnight rate", 0, 1)
    return _ready(
        engine_id,
        aligned.iloc[-1, 0] - aligned.iloc[-1, 1],
        CanonicalUnit.BASIS_POINTS.value,
        roles,
        inputs,
        "pack-selected term funding rate minus pack-selected overnight rate",
    )


def funding_bill_wedge(
    panel: MarketPanel,
    *,
    funding_role: SemanticRole,
) -> KernelResult:
    if funding_role not in {SemanticRole.CP_3M, SemanticRole.CD_3M}:
        raise ValueError("funding_role must be CP_3M or CD_3M")
    engine_id = "funding_bill_wedge"
    roles = (funding_role, SemanticRole.TBILL_3M)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    aligned = _aligned(inputs)
    if aligned.empty:
        return _insufficient(engine_id, roles, "funding rate minus bill rate", 0, 1)
    return _ready(
        engine_id,
        aligned.iloc[-1, 0] - aligned.iloc[-1, 1],
        CanonicalUnit.BASIS_POINTS.value,
        roles,
        inputs,
        "three-month funding rate minus three-month risk-free bill rate",
    )


def liquidity_buffer_drain(
    panel: MarketPanel,
    *,
    buffer_role: SemanticRole,
    lookback_observations: int = 4,
) -> KernelResult:
    if buffer_role not in {
        SemanticRole.RESERVE_BALANCES,
        SemanticRole.SYSTEM_LIQUIDITY,
    }:
        raise ValueError("buffer_role must be RESERVE_BALANCES or SYSTEM_LIQUIDITY")
    if lookback_observations < 1:
        raise ValueError("lookback_observations must be positive")
    engine_id = "liquidity_buffer_drain"
    roles = (buffer_role,)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    points = inputs[0].points.dropna()
    required = lookback_observations + 1
    if len(points) < required:
        return _insufficient(
            engine_id,
            roles,
            "negative percent change in the pack-selected liquidity buffer",
            len(points),
            required,
        )
    prior = float(points.iloc[-required])
    if prior == 0:
        return _missing(engine_id, roles, "liquidity buffer baseline is zero")
    drain_pct = -(float(points.iloc[-1]) / prior - 1.0) * 100.0
    return _ready(
        engine_id,
        drain_pct,
        "percent",
        roles,
        inputs,
        f"negative change over {lookback_observations} native observations",
    )


def facility_usage_pressure(panel: MarketPanel) -> KernelResult:
    engine_id = "facility_usage"
    roles = (
        SemanticRole.CENTRAL_BANK_FACILITY_TAKEUP,
        SemanticRole.RESERVE_BALANCES,
    )
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    takeup, reserves = inputs
    if takeup.unit is not CanonicalUnit.LOCAL_CURRENCY_MILLIONS or reserves.unit is not CanonicalUnit.LOCAL_CURRENCY_MILLIONS:
        return _missing(engine_id, roles, "facility take-up and reserve balances need matching canonical units")
    aligned = _aligned(inputs)
    if aligned.empty:
        return _insufficient(engine_id, roles, "facility take-up divided by reserves", 0, 1)
    denominator = float(aligned.iloc[-1, 1])
    if denominator <= 0:
        return _missing(engine_id, roles, "reserve-balance denominator is non-positive")
    return _ready(
        engine_id,
        float(aligned.iloc[-1, 0]) / denominator * 100.0,
        "percent_of_reserves",
        roles,
        inputs,
        "facility take-up divided by local reserve balances",
    )


def tail_dislocation(panel: MarketPanel) -> KernelResult:
    engine_id = "tail_dispersion"
    roles = (SemanticRole.RATE_P99, SemanticRole.RATE_MEDIAN)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    aligned = _aligned(inputs)
    if aligned.empty:
        return _insufficient(engine_id, roles, "rate p99 minus median", 0, 1)
    return _ready(
        engine_id,
        aligned.iloc[-1, 0] - aligned.iloc[-1, 1],
        CanonicalUnit.BASIS_POINTS.value,
        roles,
        inputs,
        "same-market rate p99 minus rate median",
    )


def volume_dislocation(
    panel: MarketPanel,
    *,
    minimum_seasonal_observations: int = 8,
) -> KernelResult:
    """Compare repo volume with its own prior same-weekday median."""

    if minimum_seasonal_observations < 1:
        raise ValueError("minimum_seasonal_observations must be positive")
    engine_id = "volume_dislocation"
    roles = (SemanticRole.REPO_VOLUME,)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    points = inputs[0].points.dropna().sort_index()
    if points.empty:
        return _insufficient(
            engine_id,
            roles,
            "repo volume versus its prior same-weekday median",
            0,
            minimum_seasonal_observations,
        )
    latest_time = points.index[-1]
    prior = points.iloc[:-1]
    seasonal = prior[prior.index.weekday == latest_time.weekday()]
    if len(seasonal) < minimum_seasonal_observations:
        return _insufficient(
            engine_id,
            roles,
            "repo volume versus its prior same-weekday median",
            len(seasonal),
            minimum_seasonal_observations,
        )
    baseline = float(seasonal.median())
    if baseline <= 0:
        return _missing(engine_id, roles, "same-weekday volume baseline is non-positive")
    dislocation = (float(points.iloc[-1]) / baseline - 1.0) * 100.0
    return _ready(
        engine_id,
        dislocation,
        "percent_vs_seasonal_median",
        roles,
        inputs,
        "latest repo volume versus its prior same-weekday median",
    )


def own_history_percentile(
    panel: MarketPanel,
    *,
    role: SemanticRole,
    minimum_observations: int = 60,
) -> KernelResult:
    engine_id = "own_history_percentile"
    roles = (role,)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    points = inputs[0].points.dropna()
    if len(points) < minimum_observations:
        return _insufficient(
            engine_id,
            roles,
            "point-in-time rank against the market's own history",
            len(points),
            minimum_observations,
        )
    latest = float(points.iloc[-1])
    percentile = float((points <= latest).sum()) / len(points) * 100.0
    return _ready(
        engine_id,
        percentile,
        "percentile",
        roles,
        inputs,
        "latest value ranked only against same-market observations known through the cutoff",
    )


def change_point_detection(
    panel: MarketPanel,
    *,
    role: SemanticRole,
    window: int = 20,
) -> KernelResult:
    if window < 5:
        raise ValueError("change-point window must be at least five observations")
    engine_id = "change_point_detection"
    roles = (role,)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    points = inputs[0].points.dropna()
    required = window * 2
    if len(points) < required:
        return _insufficient(
            engine_id,
            roles,
            "two-window median shift divided by prior-window MAD",
            len(points),
            required,
        )
    previous = points.iloc[-required:-window]
    recent = points.iloc[-window:]
    previous_median = float(previous.median())
    mad = float((previous - previous_median).abs().median())
    if mad == 0:
        return _missing(engine_id, roles, "prior-window median absolute deviation is zero")
    score = (float(recent.median()) - previous_median) / (1.4826 * mad)
    return _ready(
        engine_id,
        score,
        "robust_z",
        roles,
        inputs,
        f"{window}-observation median shift divided by scaled prior-window MAD",
    )


def calendar_amplification(
    panel: MarketPanel,
    *,
    role: SemanticRole,
    event_dates: Iterable[date],
    minimum_event_observations: int = 8,
) -> KernelResult:
    engine_id = "calendar_amplification"
    roles = (role,)
    inputs = _resolve(panel, roles)
    if isinstance(inputs, str):
        return _missing(engine_id, roles, inputs)
    dates = frozenset(event_dates)
    points = inputs[0].points.dropna()
    event_values = [float(value) for idx, value in points.items() if idx.date() in dates]
    baseline = [float(value) for idx, value in points.items() if idx.date() not in dates]
    if len(event_values) < minimum_event_observations or not baseline:
        return _insufficient(
            engine_id,
            roles,
            "event-date median minus non-event median",
            len(event_values),
            minimum_event_observations,
        )
    return _ready(
        engine_id,
        median(event_values) - median(baseline),
        inputs[0].unit.value,
        roles,
        inputs,
        "same-market event-date median minus same-market non-event median",
    )


def cross_basin_coupling(
    series_by_market: Mapping[str, RoleSeries],
    *,
    minimum_aligned_changes: int = 60,
) -> KernelResult:
    engine_id = "cross_basin_coupling"
    role = SemanticRole.FX_SWAP_BASIS
    if len(series_by_market) < 2:
        return _missing(engine_id, (role,), "at least two monetary areas are required")
    panel = pd.concat(
        {
            market_id: item.points.sort_index().diff()
            for market_id, item in series_by_market.items()
        },
        axis=1,
        join="inner",
    ).dropna()
    if len(panel) < minimum_aligned_changes:
        return _insufficient(
            engine_id,
            (role,),
            "mean absolute pairwise correlation of same-role changes",
            len(panel),
            minimum_aligned_changes,
        )
    corr = panel.corr().to_numpy(dtype=float)
    upper = np.abs(corr[np.triu_indices_from(corr, k=1)])
    value = float(np.nanmean(upper)) * 100.0
    inputs = tuple(series_by_market.values())
    return _ready(
        engine_id,
        value,
        "mean_absolute_correlation_percent",
        (role,),
        inputs,
        "mean absolute pairwise correlation of aligned FX-swap-basis changes",
    )
