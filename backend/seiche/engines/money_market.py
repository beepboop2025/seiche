"""Institutional USD money-market desk, built only from observed public data.

The engine is deliberately pure: callers hand it already-collected pandas
objects and it returns a deterministic, JSON-safe context contract.  There is
no network access, wall-clock dependency, forward fill, model forecast, or
trade recommendation here.  Every cross-series calculation is performed on
an exact observation-date intersection and publishes that alignment record.

The desk is broader than Seiche's funding-stress composite.  It keeps policy
anchors, secured-rate distributions, repo segments, commercial paper, bills,
Federal Reserve liquidity quantities, and money-fund plumbing in their own
sections and their own native clocks.  Its regime label is descriptive only:
the worst Bonferroni-adjusted empirical stress percentile among a small,
published anomaly set.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import pandas as pd

SCHEMA = "seiche.money-market-desk.v1"
DAILY_CHART_ROWS = 180
MONTHLY_CHART_ROWS = 36

LEGAL_NOTICES = [
    {
        "source": "Federal Reserve Bank of New York reference rates",
        "terms_url": "https://www.newyorkfed.org/privacy/termsofuse.html",
        "attribution": (
            "© 2026 Federal Reserve Bank of New York. New York Fed content "
            "is subject to its Terms of Use."
        ),
        "non_affiliation": (
            "Seiche is independent of and not endorsed by the New York Fed; "
            "Seiche alone is responsible for this republication, analysis, "
            "and any modifications."
        ),
        "third_party_data": (
            "SOFR and BGCR incorporate transaction data licensed by the New "
            "York Fed from DTCC Solutions; those providers accept no liability "
            "for this Seiche presentation."
        ),
    },
    {
        "source": "Other official United States data",
        "notice": (
            "Federal Reserve, Treasury, and OFR source labels identify origin. "
            "Seiche-derived metrics and explanations are marked modifications, "
            "not official agency analysis or endorsement."
        ),
    },
]

_RATE_COLUMN = "percentRate"
_PERCENTILE_COLUMNS = {
    "p01": "percentPercentile1",
    "p25": "percentPercentile25",
    "p75": "percentPercentile75",
    "p99": "percentPercentile99",
}
_VOLUME_COLUMN = "volumeInBillions"


# ---------------------------------------------------------------------------
# Cleaning, alignment, and descriptive statistics
# ---------------------------------------------------------------------------


def _dates(index: pd.Index) -> pd.DatetimeIndex:
    """Canonical daily observation dates, timezone-free and normalized."""

    try:
        parsed = pd.to_datetime(index, errors="coerce", utc=True)
    except (TypeError, ValueError):
        return pd.DatetimeIndex([pd.NaT] * len(index))
    return pd.DatetimeIndex(parsed).tz_convert(None).normalize()


def _clean(series: pd.Series | None) -> pd.Series:
    if not isinstance(series, pd.Series) or series.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    index = _dates(series.index)
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    valid_dates = ~index.isna()
    out = pd.Series(values[valid_dates], index=index[valid_dates], dtype=float)
    out = out.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    return out[~out.index.duplicated(keep="last")]


def _clean_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(index=pd.DatetimeIndex([]))
    index = _dates(frame.index)
    valid_dates = ~index.isna()
    out = frame.iloc[np.asarray(valid_dates)].copy()
    out.index = index[valid_dates]
    for column in out.columns:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.replace([np.inf, -np.inf], np.nan).sort_index()
    return out[~out.index.duplicated(keep="last")]


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    if frame.empty or name not in frame.columns:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return _clean(frame[name])


def _asof(series: pd.Series) -> str | None:
    values = _clean(series)
    return values.index[-1].date().isoformat() if not values.empty else None


def _number(value: Any, digits: int = 3) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, digits)
    # Avoid the visually noisy and semantically meaningless -0.0.
    if rounded == 0:
        rounded = 0.0
    return rounded


def _minimum(cadence: str, statistic: str) -> int:
    if cadence == "monthly":
        return 6 if statistic == "z" else 12
    if cadence == "weekly":
        return 20 if statistic == "z" else 26
    return 40 if statistic == "z" else 60


def _robust_z_1y(series: pd.Series, cadence: str) -> float | None:
    values = _clean(series)
    if values.empty:
        return None
    trailing = values[values.index >= values.index[-1] - pd.DateOffset(years=1)]
    if len(trailing) < _minimum(cadence, "z"):
        return None
    median = float(trailing.median())
    mad = float((trailing - median).abs().median())
    scale = 1.4826 * mad
    latest = float(trailing.iloc[-1])
    if scale <= 1e-12:
        return 0.0 if math.isclose(latest, median, abs_tol=1e-12) else None
    return _number((latest - median) / scale, 2)


def _percentile_3y(series: pd.Series, cadence: str) -> float | None:
    """Trailing three-year midrank; ties sit at their midpoint (flat -> 50)."""

    values = _clean(series)
    if values.empty:
        return None
    trailing = values[values.index >= values.index[-1] - pd.DateOffset(years=3)]
    if len(trailing) < _minimum(cadence, "percentile"):
        return None
    latest = float(trailing.iloc[-1])
    raw = trailing.to_numpy(dtype=float)
    tied = np.isclose(raw, latest, rtol=1e-10, atol=1e-12)
    # Rounded economic ties can differ after floating-point subtraction.
    # Keep the below/tied groups disjoint so their midrank stays in [0, 100].
    below = int(np.sum((raw < latest) & ~tied))
    equal = int(np.sum(tied))
    return _number(100.0 * (below + 0.5 * equal) / len(raw), 1)


def _change(series: pd.Series, lag: int, scale: float) -> float | None:
    values = _clean(series)
    if len(values) <= lag:
        return None
    return _number((float(values.iloc[-1]) - float(values.iloc[-lag - 1])) * scale, 3)


def _window_count(series: pd.Series, years: int) -> int:
    values = _clean(series)
    if values.empty:
        return 0
    return len(values[values.index >= values.index[-1] - pd.DateOffset(years=years)])


def _exact(
    inputs: Mapping[str, pd.Series],
    calculation: Callable[[pd.DataFrame], pd.Series],
    formula: str,
) -> tuple[pd.Series, dict]:
    """Calculate only where every input printed on exactly the same date."""

    cleaned = {name: _clean(series) for name, series in inputs.items()}
    input_asofs = {name: _asof(series) for name, series in cleaned.items()}
    input_counts = {name: len(series) for name, series in cleaned.items()}
    if not cleaned or any(series.empty for series in cleaned.values()):
        aligned = pd.DataFrame()
    else:
        aligned = pd.concat(cleaned, axis=1, join="inner").dropna(how="any")
    if aligned.empty:
        result = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    else:
        try:
            result = _clean(calculation(aligned))
        except (ArithmeticError, KeyError, TypeError, ValueError):
            result = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    denominator = min(input_counts.values()) if input_counts else 0
    return result, {
        "method": "exact_date_inner_join",
        "no_forward_fill": True,
        "formula": formula,
        "input_asof": input_asofs,
        "input_observations": input_counts,
        "overlap_observations": len(aligned),
        "overlap_pct_of_shortest_input": (
            _number(100.0 * len(aligned) / denominator, 1) if denominator else None
        ),
        "latest_common_asof": _asof(result),
    }


def _composition_safe_component_sum(
    inputs: Mapping[str, pd.Series], *, minimum_components: int = 2
) -> tuple[pd.Series, dict]:
    """Build a same-date sum whose instrument composition never changes.

    The comparison mask is the component set on the latest date with at least
    ``minimum_components`` prints.  Earlier dates enter the result only when
    their observed component mask is identical.  This prevents a change in a
    reported aggregate from being driven mechanically by (for example) GCF
    disappearing from, or reappearing in, the sum.
    """

    cleaned = {name: _clean(series) for name, series in inputs.items()}
    live = {name: series for name, series in cleaned.items() if not series.empty}
    if not live:
        frame = pd.DataFrame()
        result = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        counts = pd.Series(dtype=float)
        eligible = pd.Series(dtype=bool)
        comparison_components: list[str] = []
        same_composition = pd.Series(dtype=bool)
    else:
        frame = pd.concat(live, axis=1, join="outer").sort_index()
        counts = frame.notna().sum(axis=1)
        eligible = counts >= minimum_components
        if not eligible.any():
            comparison_components = []
            same_composition = pd.Series(False, index=frame.index, dtype=bool)
            result = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        else:
            latest_eligible = eligible[eligible].index[-1]
            comparison_components = [
                name
                for name in inputs
                if name in frame and pd.notna(frame.at[latest_eligible, name])
            ]
            comparison_set = set(comparison_components)
            same_composition = pd.Series(True, index=frame.index, dtype=bool)
            for name in inputs:
                present = (
                    frame[name].notna()
                    if name in frame
                    else pd.Series(False, index=frame.index, dtype=bool)
                )
                same_composition &= present if name in comparison_set else ~present
            result = _clean(
                frame.loc[same_composition, comparison_components].sum(
                    axis=1,
                    min_count=len(comparison_components),
                )
            )
    latest = result.index[-1] if not result.empty else None
    latest_components = (
        [name for name in inputs if name in frame and pd.notna(frame.at[latest, name])]
        if latest is not None
        else []
    )
    observed_masks: dict[str, int] = {}
    if not frame.empty:
        for _, row in frame.iterrows():
            mask = "+".join(
                name for name in inputs if name in frame and pd.notna(row[name])
            )
            if mask:
                observed_masks[mask] = observed_masks.get(mask, 0) + 1
    return result, {
        "method": "same_date_fixed_composition_sum",
        "no_forward_fill": True,
        "missing_component_treatment": "a date with a different component mask is excluded, never imputed as zero",
        "minimum_components": minimum_components,
        "input_asof": {name: _asof(series) for name, series in cleaned.items()},
        "input_observations": {name: len(series) for name, series in cleaned.items()},
        "output_observations": len(result),
        "latest_asof": _asof(result),
        "latest_available_components": latest_components,
        "comparison_components": comparison_components,
        "comparison_component_mask": "+".join(comparison_components) or None,
        "observed_component_masks": observed_masks,
        "eligible_observations_before_composition_mask": int(eligible.sum()),
        "excluded_mixed_composition_observations": (
            int((eligible & ~same_composition).sum()) if not frame.empty else 0
        ),
        "composition_rule": "only dates with the latest eligible date's exact reported-component mask are comparable",
        "latest_component_coverage_pct": (
            _number(100.0 * len(latest_components) / len(inputs), 1) if inputs else None
        ),
    }


def _bonferroni_stress_percentile(
    raw_stress_percentile: float | int | None,
    family_size: int,
) -> float | None:
    """Convert a raw upper-tail rank into a dependence-robust headline rank."""

    if raw_stress_percentile is None or family_size < 1:
        return None
    try:
        raw = float(raw_stress_percentile)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(raw):
        return None
    raw = min(100.0, max(0.0, raw))
    raw_tail_probability = 1.0 - raw / 100.0
    adjusted_tail_probability = min(1.0, family_size * raw_tail_probability)
    adjusted = 100.0 * (1.0 - adjusted_tail_probability)
    # Floor rather than round the public one-decimal headline so serialization
    # cannot make the adjusted rank microscopically exceed its raw input.
    return math.floor((adjusted + 1e-12) * 10.0) / 10.0


def _familywise_adjust_indicators(indicators: list[dict]) -> list[dict]:
    """Attach raw and Bonferroni-adjusted ranks to every eligible channel."""

    family_size = len(indicators)
    adjusted: list[dict] = []
    for indicator in indicators:
        row = dict(indicator)
        raw = row.get("raw_stress_percentile", row.get("stress_percentile"))
        headline = _bonferroni_stress_percentile(raw, family_size)
        raw_number = _number(raw, 1)
        raw_tail = (
            _number(1.0 - float(raw_number) / 100.0, 6)
            if raw_number is not None
            else None
        )
        adjusted_tail = (
            _number(1.0 - float(headline) / 100.0, 6) if headline is not None else None
        )
        row.update(
            {
                "raw_stress_percentile": raw_number,
                "bonferroni_adjusted_stress_percentile": headline,
                # Preserve the original public key, but make it truthful for
                # headline/state consumers by assigning the adjusted value.
                "stress_percentile": headline,
                "raw_empirical_tail_probability": raw_tail,
                "familywise_adjusted_tail_probability": adjusted_tail,
                "familywise_hypotheses": family_size,
            }
        )
        adjusted.append(row)
    return adjusted


def _card(
    *,
    metric_id: str,
    label: str,
    series: pd.Series,
    unit: str,
    cadence: str,
    source: str,
    explanation: str,
    change_scale: float = 1.0,
    change_unit: str | None = None,
    digits: int = 3,
    alignment: dict | None = None,
    formula: str | None = None,
) -> dict:
    values = _clean(series)
    live = not values.empty
    card: dict[str, Any] = {
        "id": metric_id,
        "label": label,
        "value": _number(values.iloc[-1], digits) if live else None,
        "unit": unit,
        "asof": _asof(values),
        "cadence": cadence,
        "source": source,
        "explanation": explanation,
        "status": "available" if live else "unavailable",
        "robust_z_1y": _robust_z_1y(values, cadence) if live else None,
        "robust_z_1y_n": _window_count(values, 1),
        "percentile_3y": _percentile_3y(values, cadence) if live else None,
        "percentile_3y_n": _window_count(values, 3),
    }
    if cadence == "daily":
        card.update(
            {
                "change_1d": _change(values, 1, change_scale),
                "change_5d": _change(values, 5, change_scale),
                "change_20d": _change(values, 20, change_scale),
                "change_basis": "own observation lags; weekends and holidays are not fabricated",
                "change_unit": change_unit or unit,
            }
        )
    elif cadence == "weekly":
        card.update(
            {
                "change_1w": _change(values, 1, change_scale),
                "change_4w": _change(values, 4, change_scale),
                "change_13w": _change(values, 13, change_scale),
                "change_basis": "own weekly observation lags",
                "change_unit": change_unit or unit,
            }
        )
    elif cadence == "monthly":
        card.update(
            {
                "change_1m": _change(values, 1, change_scale),
                "change_3m": _change(values, 3, change_scale),
                "change_12m": _change(values, 12, change_scale),
                "change_basis": "own monthly observation lags",
                "change_unit": change_unit or unit,
            }
        )
    if alignment is not None:
        card["alignment"] = alignment
    if formula is not None:
        card["formula"] = formula
    if not live:
        card["unavailable_reason"] = (
            "required observations or exact-date overlap unavailable"
        )
    return card


def _section(
    section_id: str, label: str, explanation: str, metrics: list[dict]
) -> dict:
    available = sum(metric["status"] == "available" for metric in metrics)
    status = (
        "available"
        if available == len(metrics)
        else "partial"
        if available
        else "unavailable"
    )
    return {
        "id": section_id,
        "label": label,
        "plain_language": explanation,
        "status": status,
        "available_metrics": available,
        "total_metrics": len(metrics),
        "metrics": metrics,
    }


def _refresh_section_clocks(sections: list[dict]) -> None:
    """Project section availability at the current evaluation clock in place."""

    for section in sections:
        metrics = [
            metric
            for metric in section.get("metrics") or []
            if isinstance(metric, dict)
        ]
        historical = sum(metric.get("status") == "available" for metric in metrics)
        current = sum(
            metric.get("status") == "available"
            and metric.get("freshness") in {"fresh", "aging"}
            for metric in metrics
        )
        stale = sum(
            metric.get("status") == "available" and metric.get("freshness") == "stale"
            for metric in metrics
        )
        section["historical_available_metrics"] = historical
        section["available_metrics"] = current
        section["stale_metrics"] = stale
        section["status"] = (
            "available"
            if metrics and current == len(metrics)
            else "partial"
            if current
            else "stale"
            if historical
            else "unavailable"
        )


def _chart(
    chart_id: str,
    label: str,
    series: Mapping[str, pd.Series],
    *,
    cadence: str,
    limit: int,
    digits: int = 3,
) -> dict:
    columns = ["date", *series.keys()]
    live = {
        name: _clean(values)
        for name, values in series.items()
        if not _clean(values).empty
    }
    if not live:
        rows: list[list[str | float | None]] = []
    else:
        frame = pd.concat(live, axis=1, sort=True).sort_index().tail(limit)
        frame = frame.reindex(columns=list(series.keys()))
        rows = [
            [date.date().isoformat()]
            + [None if pd.isna(value) else _number(value, digits) for value in row]
            for date, row in frame.iterrows()
        ]
    return {
        "id": chart_id,
        "label": label,
        "cadence": cadence,
        "columns": columns,
        "rows": rows,
        "row_limit": limit,
        "sampling": "latest native observations; outer display grid only; no fill",
        "no_forward_fill": True,
    }


def _freshness_status(age_days: int, cadence: str) -> str:
    fresh, aging = {
        "daily": (3, 7),
        "weekly": (10, 21),
        "monthly": (45, 75),
    }.get(cadence, (7, 21))
    if age_days <= fresh:
        return "fresh"
    if age_days <= aging:
        return "aging"
    return "stale"


def _regime_state(score: float | None) -> str:
    if score is None:
        return "CANNOT_ASSESS"
    if score >= 97.5:
        return "STRESS"
    if score >= 90.0:
        return "STRAIN"
    if score >= 75.0:
        return "WATCH"
    return "NORMAL"


def _source_row(
    *,
    source_id: str,
    label: str,
    publisher: str,
    series_id: str,
    cadence: str,
    values: pd.Series | pd.DataFrame,
    desk_asof: pd.Timestamp,
    evaluation_asof: pd.Timestamp,
) -> dict:
    if isinstance(values, pd.DataFrame):
        frame = _clean_frame(values)
        populated = frame.dropna(how="all")
        count = len(populated)
        first = populated.index[0] if count else None
        latest = populated.index[-1] if count else None
    else:
        cleaned = _clean(values)
        count = len(cleaned)
        first = cleaned.index[0] if count else None
        latest = cleaned.index[-1] if count else None
    desk_age_days = (
        max(0, int((desk_asof - latest).days)) if latest is not None else None
    )
    evaluation_age_days = (
        max(0, int((evaluation_asof - latest).days)) if latest is not None else None
    )
    return {
        "id": source_id,
        "label": label,
        "publisher": publisher,
        "series": series_id,
        "cadence": cadence,
        "available": bool(count),
        "observations": count,
        "coverage_start": first.date().isoformat() if first is not None else None,
        "asof": latest.date().isoformat() if latest is not None else None,
        "age_days_vs_desk_asof": desk_age_days,
        "age_days_vs_evaluation_asof": evaluation_age_days,
        "freshness": (
            _freshness_status(evaluation_age_days, cadence)
            if evaluation_age_days is not None
            else "unavailable"
        ),
    }


def _evaluation_date(
    value: pd.Timestamp | str | None,
    *,
    desk_asof: pd.Timestamp,
) -> pd.Timestamp:
    """Return a normalized, timezone-free evaluation date.

    ``None`` preserves the pure engine's historical behavior for direct callers
    by evaluating at its evidence horizon. Production and replay assembly pass
    an explicit date so a frozen snapshot cannot describe itself as fresh.
    """

    if value is None:
        return desk_asof
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError("evaluation_asof must be a valid date")
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    parsed = parsed.normalize()
    if parsed < desk_asof:
        raise ValueError("evaluation_asof cannot precede the desk evidence horizon")
    return parsed


def _unavailable_evaluation_asof(
    value: pd.Timestamp | str | None,
) -> str | None:
    """Normalize the requested clock when no evidence horizon can be formed."""

    if value is None:
        return None
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError("evaluation_asof must be a valid date")
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return parsed.normalize().date().isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------


def _funding_diagnostics(
    sofr: pd.Series,
    iorb: pd.Series,
    effr: pd.Series,
    *,
    evaluated_at: pd.Timestamp,
    sofr_source_id: str = "fred_sofr",
) -> dict:
    """Observed funding persistence and calendar context, never a new score."""
    spread, alignment = _exact(
        {"SOFR": sofr, "IORB": iorb},
        lambda frame: ((frame["SOFR"] - frame["IORB"]) * 100.0).round(6),
        "100 x (SOFR - IORB), in basis points",
    )
    out: dict[str, Any] = {
        "schema": "seiche.money-market-diagnostics.v1",
        "status": "unavailable",
        "context_only": True,
        "used_in_regime": False,
        "asof": _asof(spread),
        "alignment": alignment,
        "source_ids": [sofr_source_id, "fred_iorb", "fred_effr"],
        "caveats": [
            "IORB is an administered rate available to eligible institutions; a spread is not a universally executable arbitrage.",
            "Persistence counts observed prints. Calendar weekdays without prints are disclosed, not filled or automatically called collection failures.",
            "Calendar cohorts describe association, not a causal month-end effect or a policy-regime adjustment.",
            "These diagnostics do not alter the desk regime, composite score or trading authority.",
        ],
    }
    if spread.empty:
        out["reason"] = "SOFR and IORB have no exact common date"
        _refresh_diagnostic_clocks(out, evaluated_at)
        return out

    latest = spread.index[-1]
    history = spread[spread.index >= latest - pd.DateOffset(years=3)]
    windows = []
    for size in (5, 20, 60):
        window = history.tail(size)
        count = len(window)
        weekdays = pd.bdate_range(window.index[0], window.index[-1])
        windows.append(
            {
                "requested_observations": size,
                "observed_n": count,
                "status": "available" if count == size else "partial",
                "from": window.index[0].date().isoformat(),
                "to": window.index[-1].date().isoformat(),
                "above_iorb_n": int((window > 0).sum()),
                "above_iorb_share_pct": _number(100.0 * (window > 0).mean(), 1),
                "median_bp": _number(window.median()),
                "p95_bp": _number(window.quantile(0.95)) if count >= 20 else None,
                "p95_min_observations": 20,
                "maximum_bp": _number(window.max()),
                "unobserved_calendar_weekdays": int(
                    len(weekdays.difference(window.index))
                ),
            }
        )
    run = 0
    for value in reversed(history.tolist()):
        if value <= 0:
            break
        run += 1
    persistence = {
        "status": "available",
        "asof": latest.date().isoformat(),
        "lookback": "at most three calendar years of exact-date observations",
        "windows": windows,
        "current_above_iorb_run": {
            "observed_prints": run,
            "first_date": history.index[-run].date().isoformat() if run else None,
            "last_date": latest.date().isoformat() if run else None,
            "calendar_span_days": int((latest - history.index[-run]).days) + 1
            if run
            else 0,
            "left_censored": bool(run and run == len(history)),
        },
    }

    common = pd.concat({"SOFR": sofr, "IORB": iorb, "EFFR": effr}, axis=1).dropna()
    transmission: dict[str, Any] = {
        "status": "unavailable",
        "asof": None,
        "reason": "SOFR, IORB and EFFR require one exact common date",
    }
    if not common.empty:
        observation = common.iloc[-1]
        secured = round(100.0 * float(observation["SOFR"] - observation["IORB"]), 6)
        unsecured = round(100.0 * float(observation["EFFR"] - observation["IORB"]), 6)
        patterns = {
            (True, True): "both_benchmarks_above_iorb",
            (True, False): "secured_benchmark_above_iorb_only",
            (False, True): "unsecured_benchmark_above_iorb_only",
            (False, False): "neither_benchmark_above_iorb",
        }
        transmission = {
            "status": "available",
            "asof": common.index[-1].date().isoformat(),
            "pattern": patterns[(secured > 0, unsecured > 0)],
            "sofr_minus_iorb_bp": _number(secured),
            "effr_minus_iorb_bp": _number(unsecured),
            "sofr_minus_effr_bp": _number(secured - unsecured),
            "common_observation_n": len(common),
            "interpretation": "Different counterparty sets and collateral explain why these benchmarks need not coincide; the pattern alone does not identify a cause.",
        }

    month_end = (history.index.days_in_month - history.index.day) < 3
    quarter_end = month_end & (history.index.month % 3 == 0)
    cohorts = []
    for name, mask in (
        ("quarter_end", quarter_end),
        ("other_month_end", month_end & ~quarter_end),
        ("other_dates", ~month_end),
    ):
        values = history[mask]
        enough = len(values) >= 20
        cohorts.append(
            {
                "cohort": name,
                "observed_n": len(values),
                "status": "available" if enough else "insufficient_history",
                "median_bp": _number(values.median()) if enough else None,
                "minimum_observations": 20,
            }
        )
    medians = {row["cohort"]: row["median_bp"] for row in cohorts}
    baseline = medians["other_dates"]
    calendar = {
        "status": "available"
        if all(row["status"] == "available" for row in cohorts)
        else "partial",
        "asof": latest.date().isoformat(),
        "from": history.index[0].date().isoformat(),
        "cohort_definition": "last three calendar dates of a month; quarter ends are March, June, September and December",
        "cohorts": cohorts,
        "quarter_end_minus_other_dates_median_bp": (
            _number(medians["quarter_end"] - baseline)
            if medians["quarter_end"] is not None and baseline is not None
            else None
        ),
        "inference": "descriptive cohort medians; not a forecast or causal estimate",
    }
    out.update(
        status="available",
        persistence=persistence,
        overnight_transmission=transmission,
        calendar_context=calendar,
    )
    _refresh_diagnostic_clocks(out, evaluated_at)
    return out


def _refresh_diagnostic_clocks(diagnostics: dict, evaluated_at: pd.Timestamp) -> None:
    """Retain observed facts but age every diagnostic at response time."""
    parts = [diagnostics] + [
        value
        for key in ("persistence", "overnight_transmission", "calendar_context")
        if isinstance(value := diagnostics.get(key), dict)
    ]
    for part in parts:
        observed = part.get("asof")
        try:
            clock = pd.Timestamp(observed) if isinstance(observed, str) else pd.NaT
            age = int((evaluated_at - clock).days) if not pd.isna(clock) else None
        except (TypeError, ValueError, OverflowError):
            age = None
        part["evaluation_asof"] = evaluated_at.date().isoformat()
        part["age_days"] = age if age is not None and age >= 0 else None
        part["freshness"] = (
            _freshness_status(age, "daily")
            if age is not None and age >= 0
            else "unavailable"
        )
        part["use"] = (
            "historical_context"
            if part["freshness"] == "stale"
            else "descriptive_context"
            if part["freshness"] in {"fresh", "aging"}
            else "no_inference"
        )


def analyze(
    *,
    sofr: pd.Series | None = None,
    effr: pd.Series | None = None,
    iorb: pd.Series | None = None,
    nyfed_sofr: pd.DataFrame | None = None,
    nyfed_tgcr: pd.DataFrame | None = None,
    nyfed_bgcr: pd.DataFrame | None = None,
    bgcr: pd.Series | None = None,
    tgcr: pd.Series | None = None,
    dvp_rate: pd.Series | None = None,
    dvp_volume: pd.Series | None = None,
    tri_rate: pd.Series | None = None,
    tri_volume: pd.Series | None = None,
    gcf_rate: pd.Series | None = None,
    gcf_volume: pd.Series | None = None,
    mmf_total: pd.Series | None = None,
    mmf_repo_ficc: pd.Series | None = None,
    mmf_repo_fed: pd.Series | None = None,
    mmf_repo_total: pd.Series | None = None,
    cp_nonfinancial_3m: pd.Series | None = None,
    cp_financial_3m: pd.Series | None = None,
    treasury_3m: pd.Series | None = None,
    bill_4w: pd.Series | None = None,
    bill_3m: pd.Series | None = None,
    reserves: pd.Series | None = None,
    tga: pd.Series | None = None,
    on_rrp: pd.Series | None = None,
    srf: pd.Series | None = None,
    discount_window: pd.Series | None = None,
    evaluation_asof: pd.Timestamp | str | None = None,
) -> dict:
    """Build a point-in-time USD money-market context desk.

    Rates are percentage points, rate spreads are emitted in basis points, and
    every quantity input is expected in USD billions.  The assembler performs
    the two source-specific unit conversions (H.4.1 $M and raw OFR dollars)
    before calling this function.
    """

    frames = {
        "SOFR": _clean_frame(nyfed_sofr),
        "TGCR": _clean_frame(nyfed_tgcr),
        "BGCR": _clean_frame(nyfed_bgcr),
    }
    raw: dict[str, pd.Series] = {
        "sofr": _clean(sofr),
        "effr": _clean(effr),
        "iorb": _clean(iorb),
        "bgcr": _clean(bgcr),
        "tgcr": _clean(tgcr),
        "dvp_rate": _clean(dvp_rate),
        "dvp_volume": _clean(dvp_volume),
        "tri_rate": _clean(tri_rate),
        "tri_volume": _clean(tri_volume),
        "gcf_rate": _clean(gcf_rate),
        "gcf_volume": _clean(gcf_volume),
        "mmf_total": _clean(mmf_total),
        "mmf_repo_ficc": _clean(mmf_repo_ficc),
        "mmf_repo_fed": _clean(mmf_repo_fed),
        "mmf_repo_total": _clean(mmf_repo_total),
        "cp_nonfinancial_3m": _clean(cp_nonfinancial_3m),
        "cp_financial_3m": _clean(cp_financial_3m),
        "treasury_3m": _clean(treasury_3m),
        "bill_4w": _clean(bill_4w),
        "bill_3m": _clean(bill_3m),
        "reserves": _clean(reserves),
        "tga": _clean(tga),
        "on_rrp": _clean(on_rrp),
        "srf": _clean(srf),
        "discount_window": _clean(discount_window),
    }

    # The official NY Fed frame is an acceptable SOFR fallback if the FRED
    # mirror is absent.  It is the same benchmark, not a proxy for it.
    direct_nyfed_sofr_fallback = raw["sofr"].empty
    core_sofr = (
        _column(frames["SOFR"], _RATE_COLUMN)
        if direct_nyfed_sofr_fallback
        else raw["sofr"]
    )
    if core_sofr.empty or raw["iorb"].empty:
        unavailable_evaluation_asof = _unavailable_evaluation_asof(evaluation_asof)
        return _json_safe(
            {
                "ok": False,
                "schema": SCHEMA,
                "asof": None,
                "context_only": True,
                "reason": "core SOFR and IORB observations are required",
                "regime": {
                    "state": "CANNOT_ASSESS",
                    "raw_worst_stress_percentile": None,
                    "worst_stress_percentile": None,
                    "bonferroni_adjusted_worst_stress_percentile": None,
                    "worst_indicator": None,
                    "indicators": [],
                    "familywise_adjustment": {
                        "method": "bonferroni_empirical_upper_tail",
                        "eligible_hypotheses": 0,
                        "dependence_assumption": "valid under arbitrary cross-channel dependence",
                    },
                    "status": "descriptive_context_only_not_forecast_probability_or_trade_signal",
                },
                "plain_language": "The desk cannot form its core overnight comparison because SOFR or IORB is missing.",
                "quant_read": "No regime or derived market interpretation is produced without both core rate series.",
                "strongest_signal": {
                    "metric_id": None,
                    "label": "Core observations unavailable",
                    "value": None,
                    "unit": None,
                    "asof": None,
                    "raw_stress_percentile": None,
                    "bonferroni_adjusted_stress_percentile": None,
                    "stress_percentile": None,
                    "use": "no inference",
                },
                "countercase": {
                    "metric_id": None,
                    "reading": "No independent countercase can be formed without the core desk.",
                    "limit": "Missing data are not treated as calm.",
                },
                "coverage": {
                    "core": {
                        "sofr": not core_sofr.empty,
                        "iorb": not raw["iorb"].empty,
                    },
                    "status": "core_unavailable",
                },
                "freshness": {
                    "desk_asof": None,
                    "evaluation_asof": unavailable_evaluation_asof,
                    "basis": "unavailable because the core evidence horizon cannot be established",
                    "status_counts": {
                        "fresh": 0,
                        "aging": 0,
                        "stale": 0,
                        "unavailable": 2,
                    },
                    "by_source": [],
                },
                "sections": [],
                "charts": {},
                "methodology": {
                    "alignment": "exact observation-date intersections; no forward fill",
                    "failure_rule": "only missing core SOFR or IORB makes the desk unavailable",
                },
                "formulas": [],
                "caveats": [
                    "No regime or market interpretation is produced without the core SOFR-IORB pair."
                ],
                "source_metadata": [],
                "sources": [],
                "legal_notices": LEGAL_NOTICES,
            }
        )

    core_spread, _ = _exact(
        {"SOFR": core_sofr, "IORB": raw["iorb"]},
        lambda frame: (frame["SOFR"] - frame["IORB"]) * 100.0,
        "100 x (SOFR percent - IORB percent)",
    )
    core_aligned = not core_spread.empty
    desk_asof = (
        core_spread.index[-1]
        if core_aligned
        else max(core_sofr.index[-1], raw["iorb"].index[-1])
    )
    horizon_basis = (
        "latest exact-date SOFR-IORB observation"
        if core_aligned
        else "latest raw SOFR or IORB observation; the core spread remains explicitly unavailable"
    )
    # A single conservative evidence horizon prevents a later-dated input from
    # entering a desk whose core funding print has not reached that date.
    raw = {name: values[values.index <= desk_asof] for name, values in raw.items()}
    frames = {name: frame[frame.index <= desk_asof] for name, frame in frames.items()}
    core_sofr = (
        _column(frames["SOFR"], _RATE_COLUMN)
        if direct_nyfed_sofr_fallback
        else raw["sofr"]
    )
    evaluated_at = _evaluation_date(evaluation_asof, desk_asof=desk_asof)

    # ---- policy corridor and overnight spreads ---------------------------
    sofr_iorb, a_sofr_iorb = _exact(
        {"SOFR": core_sofr, "IORB": raw["iorb"]},
        lambda f: (f["SOFR"] - f["IORB"]) * 100.0,
        "100 x (SOFR - IORB)",
    )
    effr_iorb, a_effr_iorb = _exact(
        {"EFFR": raw["effr"], "IORB": raw["iorb"]},
        lambda f: (f["EFFR"] - f["IORB"]) * 100.0,
        "100 x (EFFR - IORB)",
    )
    sofr_effr, a_sofr_effr = _exact(
        {"SOFR": core_sofr, "EFFR": raw["effr"]},
        lambda f: (f["SOFR"] - f["EFFR"]) * 100.0,
        "100 x (SOFR - EFFR)",
    )
    policy_metrics = [
        _card(
            metric_id="policy.sofr",
            label="SOFR",
            series=core_sofr,
            unit="%",
            cadence="daily",
            source=(
                "Federal Reserve Bank of New York direct reference-rates feed"
                if direct_nyfed_sofr_fallback
                else "New York Fed SOFR via FRED"
            ),
            explanation="The median cost of borrowing cash overnight against Treasury collateral.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="policy.effr",
            label="Effective federal funds rate",
            series=raw["effr"],
            unit="%",
            cadence="daily",
            source="Federal Reserve via FRED EFFR",
            explanation="The observed overnight unsecured rate paid in the federal-funds market.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="policy.iorb",
            label="Interest on reserve balances",
            series=raw["iorb"],
            unit="%",
            cadence="daily",
            source="Federal Reserve via FRED IORB/IOER splice",
            explanation="The administered rate banks earn on reserve balances; it is the desk's policy anchor.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="policy.sofr_minus_iorb",
            label="SOFR minus IORB",
            series=sofr_iorb,
            unit="bp",
            cadence="daily",
            source="NY Fed SOFR + Federal Reserve IORB",
            explanation="Positive values mean secured overnight cash traded above the reserve-remuneration anchor on the same date.",
            alignment=a_sofr_iorb,
            formula="100 x (SOFR - IORB)",
        ),
        _card(
            metric_id="policy.effr_minus_iorb",
            label="EFFR minus IORB",
            series=effr_iorb,
            unit="bp",
            cadence="daily",
            source="Federal Reserve EFFR + IORB",
            explanation="Shows where unsecured federal-funds trades cleared relative to the reserve-remuneration anchor.",
            alignment=a_effr_iorb,
            formula="100 x (EFFR - IORB)",
        ),
        _card(
            metric_id="policy.sofr_minus_effr",
            label="SOFR minus EFFR",
            series=sofr_effr,
            unit="bp",
            cadence="daily",
            source="NY Fed SOFR + Federal Reserve EFFR",
            explanation="Compares secured and unsecured overnight benchmarks without claiming the difference is a pure credit premium.",
            alignment=a_sofr_effr,
            formula="100 x (SOFR - EFFR)",
        ),
    ]

    # ---- secured-rate distributions --------------------------------------
    distribution_metrics: list[dict] = []
    distribution_series: dict[str, dict[str, pd.Series]] = {}
    for benchmark in ("SOFR", "TGCR", "BGCR"):
        frame = frames[benchmark]
        prefix = benchmark.lower()
        rate = _column(frame, _RATE_COLUMN)
        volume = _column(frame, _VOLUME_COLUMN)
        p = {
            name: _column(frame, column) for name, column in _PERCENTILE_COLUMNS.items()
        }
        p99_tail, p99_alignment = _exact(
            {"P99": p["p99"], "RATE": rate},
            lambda f: (f["P99"] - f["RATE"]) * 100.0,
            f"100 x ({benchmark} P99 - {benchmark} rate)",
        )
        iqr, iqr_alignment = _exact(
            {"P75": p["p75"], "P25": p["p25"]},
            lambda f: (f["P75"] - f["P25"]) * 100.0,
            f"100 x ({benchmark} P75 - {benchmark} P25)",
        )
        full_range, range_alignment = _exact(
            {"P99": p["p99"], "P01": p["p01"]},
            lambda f: (f["P99"] - f["P01"]) * 100.0,
            f"100 x ({benchmark} P99 - {benchmark} P01)",
        )
        tail_skew, skew_alignment = _exact(
            {"P99": p["p99"], "RATE": rate, "P01": p["p01"]},
            lambda f: (
                (f["P99"] - f["RATE"])
                / ((f["RATE"] - f["P01"]).where((f["RATE"] - f["P01"]).abs() > 1e-12))
            ),
            f"({benchmark} P99 - rate) / ({benchmark} rate - P01)",
        )
        distribution_series[benchmark] = {
            "rate": rate,
            **p,
            "volume": volume,
            "p99_tail": p99_tail,
            "iqr": iqr,
            "p99_p01": full_range,
            "tail_skew": tail_skew,
        }
        source = f"NY Fed Markets API {benchmark} distribution"
        base_explanation = {
            "SOFR": "secured overnight Treasury financing across tri-party, GCF, and cleared bilateral activity",
            "TGCR": "overnight Treasury collateral financing in the tri-party market, excluding GCF",
            "BGCR": "TGCR activity plus GCF Treasury repo",
        }[benchmark]
        distribution_metrics.extend(
            [
                _card(
                    metric_id=f"distribution.{prefix}.rate",
                    label=f"{benchmark} published rate",
                    series=rate,
                    unit="%",
                    cadence="daily",
                    source=source,
                    explanation=f"The published rate for {base_explanation}.",
                    change_scale=100.0,
                    change_unit="bp",
                ),
                _card(
                    metric_id=f"distribution.{prefix}.p01",
                    label=f"{benchmark} 1st percentile",
                    series=p["p01"],
                    unit="%",
                    cadence="daily",
                    source=source,
                    explanation="The low-rate edge of the transaction distribution; it describes dispersion, not an executable quote.",
                    change_scale=100.0,
                    change_unit="bp",
                ),
                _card(
                    metric_id=f"distribution.{prefix}.p25",
                    label=f"{benchmark} 25th percentile",
                    series=p["p25"],
                    unit="%",
                    cadence="daily",
                    source=source,
                    explanation="One quarter of reported transaction volume occurred at or below this rate.",
                    change_scale=100.0,
                    change_unit="bp",
                ),
                _card(
                    metric_id=f"distribution.{prefix}.p75",
                    label=f"{benchmark} 75th percentile",
                    series=p["p75"],
                    unit="%",
                    cadence="daily",
                    source=source,
                    explanation="Three quarters of reported transaction volume occurred at or below this rate.",
                    change_scale=100.0,
                    change_unit="bp",
                ),
                _card(
                    metric_id=f"distribution.{prefix}.p99",
                    label=f"{benchmark} 99th percentile",
                    series=p["p99"],
                    unit="%",
                    cadence="daily",
                    source=source,
                    explanation="The expensive tail of observed transactions; it can widen even when the headline rate is steady.",
                    change_scale=100.0,
                    change_unit="bp",
                ),
                _card(
                    metric_id=f"distribution.{prefix}.volume",
                    label=f"{benchmark} transaction volume",
                    series=volume,
                    unit="$B",
                    cadence="daily",
                    source=source,
                    explanation="The reported transaction volume underlying the benchmark on that observation date.",
                    change_unit="$B",
                    digits=2,
                ),
                _card(
                    metric_id=f"distribution.{prefix}.p99_minus_rate",
                    label=f"{benchmark} P99 minus rate",
                    series=p99_tail,
                    unit="bp",
                    cadence="daily",
                    source=source,
                    explanation="How far the expensive transaction tail sits above the published rate on the same date.",
                    alignment=p99_alignment,
                    formula=f"100 x ({benchmark} P99 - rate)",
                ),
                _card(
                    metric_id=f"distribution.{prefix}.iqr",
                    label=f"{benchmark} interquartile width",
                    series=iqr,
                    unit="bp",
                    cadence="daily",
                    source=source,
                    explanation="The middle-half width of the transaction distribution; wider means less uniform funding outcomes.",
                    alignment=iqr_alignment,
                    formula=f"100 x ({benchmark} P75 - P25)",
                ),
                _card(
                    metric_id=f"distribution.{prefix}.p99_p01_width",
                    label=f"{benchmark} P99-P01 width",
                    series=full_range,
                    unit="bp",
                    cadence="daily",
                    source=source,
                    explanation="A broad tail-to-tail dispersion measure using observed percentiles, not assumed normality.",
                    alignment=range_alignment,
                    formula=f"100 x ({benchmark} P99 - P01)",
                ),
                _card(
                    metric_id=f"distribution.{prefix}.tail_skew",
                    label=f"{benchmark} upper-to-lower tail ratio",
                    series=tail_skew,
                    unit="ratio",
                    cadence="daily",
                    source=source,
                    explanation="Compares expensive-tail distance with cheap-tail distance around the published rate; above one means the upper tail is longer.",
                    alignment=skew_alignment,
                    formula=f"({benchmark} P99 - rate) / ({benchmark} rate - P01)",
                ),
            ]
        )

    # ---- repo segment rates and volumes ----------------------------------
    repo_rates = {
        "bgcr": raw["bgcr"],
        "tgcr": raw["tgcr"],
        "dvp": raw["dvp_rate"],
        "tri": raw["tri_rate"],
        "gcf": raw["gcf_rate"],
    }
    repo_rate_labels = {
        "bgcr": "BGCR",
        "tgcr": "TGCR",
        "dvp": "DVP repo overnight/open average rate",
        "tri": "Tri-party repo overnight/open average rate",
        "gcf": "GCF repo overnight/open average rate",
    }
    repo_metrics = [
        _card(
            metric_id=f"repo.{name}_rate",
            label=repo_rate_labels[name],
            series=values,
            unit="%",
            cadence="daily",
            source="OFR Short-Term Funding Monitor",
            explanation={
                "bgcr": "Broad general collateral repo benchmark mirrored by OFR.",
                "tgcr": "Tri-party general collateral repo benchmark mirrored by OFR.",
                "dvp": "Average overnight/open rate in delivery-versus-payment repo, an important dealer and relative-value funding segment.",
                "tri": "Average overnight/open rate in the tri-party repo segment.",
                "gcf": "Average overnight/open rate in FICC's interdealer GCF repo segment; no print can mean no qualifying activity.",
            }[name],
            change_scale=100.0,
            change_unit="bp",
        )
        for name, values in repo_rates.items()
    ]
    repo_volumes = {
        "dvp": raw["dvp_volume"],
        "tri": raw["tri_volume"],
        "gcf": raw["gcf_volume"],
    }
    for name, values in repo_volumes.items():
        repo_metrics.append(
            _card(
                metric_id=f"repo.{name}_volume",
                label=f"{name.upper()} repo volume",
                series=values,
                unit="$B",
                cadence="daily",
                source="OFR Short-Term Funding Monitor",
                explanation=f"Reported {name.upper()} repo transaction volume; it is market activity, not net balance-sheet exposure.",
                change_unit="$B",
                digits=2,
            )
        )
    repo_spreads: dict[str, pd.Series] = {}
    for name, values in repo_rates.items():
        leg = name.upper()
        spread, alignment = _exact(
            {leg: values, "SOFR": core_sofr},
            lambda f, leg=leg: (f[leg] - f["SOFR"]) * 100.0,
            f"100 x ({leg} rate - SOFR)",
        )
        repo_spreads[name] = spread
        repo_metrics.append(
            _card(
                metric_id=f"repo.{name}_minus_sofr",
                label=f"{name.upper()} minus SOFR",
                series=spread,
                unit="bp",
                cadence="daily",
                source="OFR Short-Term Funding Monitor + NY Fed SOFR",
                explanation=f"The {name.upper()} segment rate relative to SOFR on dates when both printed; it is segmentation context, not an arbitrage quote.",
                alignment=alignment,
                formula=f"100 x ({name.upper()} - SOFR)",
            )
        )
    dvp_tri, a_dvp_tri = _exact(
        {"DVP": raw["dvp_rate"], "TRI": raw["tri_rate"]},
        lambda f: (f["DVP"] - f["TRI"]) * 100.0,
        "100 x (DVP rate - tri-party rate)",
    )
    repo_metrics.append(
        _card(
            metric_id="repo.dvp_minus_tri",
            label="DVP minus tri-party repo",
            series=dvp_tri,
            unit="bp",
            cadence="daily",
            source="OFR Short-Term Funding Monitor",
            explanation="A same-date measure of rate separation between DVP and tri-party repo segments.",
            alignment=a_dvp_tri,
            formula="100 x (DVP - tri-party)",
        )
    )
    total_repo_volume, a_total_repo_volume = _composition_safe_component_sum(
        {"DVP": raw["dvp_volume"], "TRI": raw["tri_volume"], "GCF": raw["gcf_volume"]},
        minimum_components=2,
    )
    repo_comparison_components = a_total_repo_volume["comparison_components"]
    repo_comparison_formula = " + ".join(repo_comparison_components)
    repo_metrics.append(
        _card(
            metric_id="repo.reported_segment_volume",
            label="Comparable reported repo-segment volume",
            series=total_repo_volume,
            unit="$B",
            cadence="daily",
            source="OFR Short-Term Funding Monitor",
            explanation="A same-date repo-segment sum whose component mask is fixed to the latest comparable print. Dates with a different DVP/tri-party/GCF composition are excluded rather than creating a mechanical change.",
            change_unit="$B",
            digits=2,
            alignment=a_total_repo_volume,
            formula=(
                f"same-date sum of fixed comparison components: {repo_comparison_formula}"
                if repo_comparison_formula
                else "unavailable: fewer than two same-date repo segments"
            ),
        )
    )
    repo_shares: dict[str, pd.Series] = {}
    for name in ("dvp", "tri", "gcf"):
        leg = name.upper()
        share, alignment = _exact(
            {leg: raw[f"{name}_volume"], "TOTAL": total_repo_volume},
            lambda f, leg=leg: (f[leg] / f["TOTAL"].replace(0.0, np.nan)) * 100.0,
            f"100 x {leg} volume / displayed segment volume",
        )
        repo_shares[name] = share
        repo_metrics.append(
            _card(
                metric_id=f"repo.{name}_volume_share",
                label=f"{name.upper()} share of displayed segment volume",
                series=share,
                unit="%",
                cadence="daily",
                source="OFR Short-Term Funding Monitor",
                explanation="The segment's share of the displayed exact-date, fixed-composition repo aggregate. A segment outside that aggregate's comparison mask is unavailable, and this is not a share of every US repo trade.",
                change_unit="percentage points",
                alignment=alignment,
                formula=f"100 x {name.upper()} / displayed total",
            )
        )

    # ---- unsecured commercial paper --------------------------------------
    cp_nf_bill, a_cp_nf_bill = _exact(
        {"CP": raw["cp_nonfinancial_3m"], "TREASURY": raw["treasury_3m"]},
        lambda f: (f["CP"] - f["TREASURY"]) * 100.0,
        "100 x (3m nonfinancial CP - 3m Treasury constant maturity)",
    )
    cp_fin_bill, a_cp_fin_bill = _exact(
        {"CP": raw["cp_financial_3m"], "TREASURY": raw["treasury_3m"]},
        lambda f: (f["CP"] - f["TREASURY"]) * 100.0,
        "100 x (3m financial CP - 3m Treasury constant maturity)",
    )
    cp_fin_nf, a_cp_fin_nf = _exact(
        {
            "FINANCIAL": raw["cp_financial_3m"],
            "NONFINANCIAL": raw["cp_nonfinancial_3m"],
        },
        lambda f: (f["FINANCIAL"] - f["NONFINANCIAL"]) * 100.0,
        "100 x (3m financial CP - 3m nonfinancial CP)",
    )
    unsecured_metrics = [
        _card(
            metric_id="unsecured.cp_nonfinancial_3m",
            label="3m AA nonfinancial commercial paper",
            series=raw["cp_nonfinancial_3m"],
            unit="%",
            cadence="daily",
            source="Federal Reserve commercial paper release via FRED DCPN3M",
            explanation="Indicative three-month unsecured funding cost for top-tier nonfinancial issuers.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="unsecured.cp_financial_3m",
            label="3m AA financial commercial paper",
            series=raw["cp_financial_3m"],
            unit="%",
            cadence="daily",
            source="Federal Reserve commercial paper release via FRED DCPF3M",
            explanation="Indicative three-month unsecured funding cost for top-tier financial issuers.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="unsecured.treasury_3m",
            label="3m Treasury constant maturity",
            series=raw["treasury_3m"],
            unit="%",
            cadence="daily",
            source="US Treasury via FRED DGS3MO",
            explanation="A par-yield government benchmark used here as the same-date public reference for CP spreads.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="unsecured.nonfinancial_cp_minus_treasury",
            label="Nonfinancial CP minus 3m Treasury",
            series=cp_nf_bill,
            unit="bp",
            cadence="daily",
            source="Federal Reserve CP + US Treasury via FRED",
            explanation="The extra same-date yield on top-tier nonfinancial CP versus the public Treasury benchmark; it mixes credit, liquidity, and convention effects.",
            alignment=a_cp_nf_bill,
            formula="100 x (nonfinancial CP - 3m Treasury)",
        ),
        _card(
            metric_id="unsecured.financial_cp_minus_treasury",
            label="Financial CP minus 3m Treasury",
            series=cp_fin_bill,
            unit="bp",
            cadence="daily",
            source="Federal Reserve CP + US Treasury via FRED",
            explanation="The extra same-date yield on top-tier financial CP versus the public Treasury benchmark; wider is descriptive funding pressure, not default probability.",
            alignment=a_cp_fin_bill,
            formula="100 x (financial CP - 3m Treasury)",
        ),
        _card(
            metric_id="unsecured.financial_minus_nonfinancial_cp",
            label="Financial minus nonfinancial CP",
            series=cp_fin_nf,
            unit="bp",
            cadence="daily",
            source="Federal Reserve commercial paper release via FRED",
            explanation="Same-date sector separation inside top-tier three-month commercial paper.",
            alignment=a_cp_fin_nf,
            formula="100 x (financial CP - nonfinancial CP)",
        ),
    ]

    # ---- bills and the cash curve ----------------------------------------
    bill_curve, a_bill_curve = _exact(
        {"THREE_MONTH": raw["bill_3m"], "FOUR_WEEK": raw["bill_4w"]},
        lambda f: (f["THREE_MONTH"] - f["FOUR_WEEK"]) * 100.0,
        "100 x (3m bill discount rate - 4w bill discount rate)",
    )
    dgs_bill_basis, a_dgs_bill_basis = _exact(
        {"CONSTANT_MATURITY": raw["treasury_3m"], "DISCOUNT_BILL": raw["bill_3m"]},
        lambda f: (f["CONSTANT_MATURITY"] - f["DISCOUNT_BILL"]) * 100.0,
        "100 x (3m constant-maturity par yield - 3m bill discount rate)",
    )
    sofr_bill4w, a_sofr_bill4w = _exact(
        {"SOFR": core_sofr, "BILL": raw["bill_4w"]},
        lambda f: (f["SOFR"] - f["BILL"]) * 100.0,
        "100 x (SOFR - 4w bill discount rate)",
    )
    sofr_bill3m, a_sofr_bill3m = _exact(
        {"SOFR": core_sofr, "BILL": raw["bill_3m"]},
        lambda f: (f["SOFR"] - f["BILL"]) * 100.0,
        "100 x (SOFR - 3m bill discount rate)",
    )
    bills_metrics = [
        _card(
            metric_id="bills.bill_4w",
            label="4-week Treasury bill discount rate",
            series=raw["bill_4w"],
            unit="%",
            cadence="daily",
            source="US Treasury via FRED DTB4WK",
            explanation="Secondary-market four-week bill discount rate; its quote convention differs from SOFR and par yields.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="bills.bill_3m",
            label="3-month Treasury bill discount rate",
            series=raw["bill_3m"],
            unit="%",
            cadence="daily",
            source="US Treasury via FRED DTB3",
            explanation="Secondary-market three-month bill discount rate, shown on its published bank-discount basis.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="bills.treasury_3m_constant_maturity",
            label="3-month Treasury constant maturity",
            series=raw["treasury_3m"],
            unit="%",
            cadence="daily",
            source="US Treasury via FRED DGS3MO",
            explanation="A par-yield interpolation included beside discount-rate bills so the convention basis is visible.",
            change_scale=100.0,
            change_unit="bp",
        ),
        _card(
            metric_id="bills.three_month_minus_four_week",
            label="3m minus 4w bill curve",
            series=bill_curve,
            unit="bp",
            cadence="daily",
            source="US Treasury via FRED DTB3 + DTB4WK",
            explanation="The short bill-curve slope using two same-convention discount rates on the same observation date.",
            alignment=a_bill_curve,
            formula="100 x (3m bill - 4w bill)",
        ),
        _card(
            metric_id="bills.constant_maturity_minus_discount_basis",
            label="3m constant-maturity minus bill basis",
            series=dgs_bill_basis,
            unit="bp",
            cadence="daily",
            source="US Treasury via FRED DGS3MO + DTB3",
            explanation="Makes the par-yield versus bank-discount quote-convention gap explicit instead of treating the two series as interchangeable.",
            alignment=a_dgs_bill_basis,
            formula="100 x (3m constant maturity - 3m bill discount rate)",
        ),
        _card(
            metric_id="bills.sofr_minus_four_week",
            label="SOFR minus 4w bill",
            series=sofr_bill4w,
            unit="bp",
            cadence="daily",
            source="NY Fed SOFR + US Treasury DTB4WK",
            explanation="An exact-date cash-versus-collateral reference; quote conventions differ, so this is context rather than executable basis.",
            alignment=a_sofr_bill4w,
            formula="100 x (SOFR - 4w bill discount rate)",
        ),
        _card(
            metric_id="bills.sofr_minus_three_month",
            label="SOFR minus 3m bill",
            series=sofr_bill3m,
            unit="bp",
            cadence="daily",
            source="NY Fed SOFR + US Treasury DTB3",
            explanation="Overnight secured funding relative to the three-month bill discount rate on the same date, without term-premium attribution.",
            alignment=a_sofr_bill3m,
            formula="100 x (SOFR - 3m bill discount rate)",
        ),
    ]

    # ---- liquidity buffers and official facilities -----------------------
    reserves_rrp, a_reserves_rrp = _exact(
        {"RESERVES": raw["reserves"], "ON_RRP": raw["on_rrp"]},
        lambda f: f["RESERVES"] + f["ON_RRP"],
        "reserve balances + ON RRP take-up",
    )
    rrp_share, a_rrp_share = _exact(
        {"ON_RRP": raw["on_rrp"], "BUFFER": reserves_rrp},
        lambda f: (f["ON_RRP"] / f["BUFFER"].replace(0.0, np.nan)) * 100.0,
        "100 x ON RRP / (reserve balances + ON RRP)",
    )
    backstop_use, a_backstop_use = _exact(
        {"SRF": raw["srf"], "DISCOUNT_WINDOW": raw["discount_window"]},
        lambda f: f["SRF"] + f["DISCOUNT_WINDOW"],
        "SRF accepted + discount-window primary credit",
    )
    liquidity_metrics = [
        _card(
            metric_id="liquidity.reserves",
            label="Reserve balances",
            series=raw["reserves"],
            unit="$B",
            cadence="weekly",
            source="Federal Reserve H.4.1 via FRED WRESBAL",
            explanation="Bank reserve balances held at Federal Reserve Banks; this is a system aggregate, not the distribution across banks.",
            change_unit="$B",
            digits=2,
        ),
        _card(
            metric_id="liquidity.tga",
            label="Treasury General Account",
            series=raw["tga"],
            unit="$B",
            cadence="daily",
            source="US Treasury Daily Treasury Statement",
            explanation="The Treasury's cash balance at the Fed; changes are shown as observations and are not asserted to map one-for-one into reserves.",
            change_unit="$B",
            digits=2,
        ),
        _card(
            metric_id="liquidity.on_rrp",
            label="ON RRP take-up",
            series=raw["on_rrp"],
            unit="$B",
            cadence="daily",
            source="NY Fed ON RRP operations via FRED RRPONTSYD",
            explanation="Cash placed overnight at the Fed's reverse-repo facility, historically an alternative liquidity pool outside bank reserves.",
            change_unit="$B",
            digits=2,
        ),
        _card(
            metric_id="liquidity.srf",
            label="Standing Repo Facility accepted",
            series=raw["srf"],
            unit="$B",
            cadence="daily",
            source="NY Fed repo operation results",
            explanation="Accepted borrowing against eligible collateral at the Fed's repo backstop; zero is a real no-use observation when reported.",
            change_unit="$B",
            digits=3,
        ),
        _card(
            metric_id="liquidity.discount_window",
            label="Discount-window primary credit",
            series=raw["discount_window"],
            unit="$B",
            cadence="weekly",
            source="Federal Reserve H.4.1 via FRED WLCFLPCL",
            explanation="Primary-credit loans outstanding at the weekly H.4.1 observation; use can reflect liquidity demand but not its cause.",
            change_unit="$B",
            digits=3,
        ),
        _card(
            metric_id="liquidity.reserves_plus_on_rrp",
            label="Reserves plus ON RRP",
            series=reserves_rrp,
            unit="$B",
            cadence="weekly",
            source="Federal Reserve H.4.1 + NY Fed ON RRP",
            explanation="A same-date broad Fed-liability liquidity total. The holders and economic uses differ, so it is not one fungible cash bucket.",
            change_unit="$B",
            digits=2,
            alignment=a_reserves_rrp,
            formula="reserves + ON RRP",
        ),
        _card(
            metric_id="liquidity.on_rrp_share",
            label="ON RRP share of reserves-plus-RRP",
            series=rrp_share,
            unit="%",
            cadence="weekly",
            source="Federal Reserve H.4.1 + NY Fed ON RRP",
            explanation="How the displayed Fed-liability liquidity total is split toward ON RRP on exact common dates; it says nothing about holder-level access.",
            change_unit="percentage points",
            alignment=a_rrp_share,
            formula="100 x ON RRP / (reserves + ON RRP)",
        ),
        _card(
            metric_id="liquidity.reported_backstop_use",
            label="SRF plus discount-window use",
            series=backstop_use,
            unit="$B",
            cadence="weekly",
            source="NY Fed SRF + Federal Reserve H.4.1",
            explanation="A same-date scale sum of two different official backstops. It is displayed for magnitude, not treated as one homogeneous facility.",
            change_unit="$B",
            digits=3,
            alignment=a_backstop_use,
            formula="SRF accepted + discount-window primary credit",
        ),
    ]

    # ---- money-market-fund plumbing --------------------------------------
    mmf_total_repo_share, a_mmf_total_repo_share = _exact(
        {"REPO": raw["mmf_repo_total"], "ASSETS": raw["mmf_total"]},
        lambda f: (f["REPO"] / f["ASSETS"].replace(0.0, np.nan)) * 100.0,
        "100 x total MMF repo / total MMF assets",
    )
    mmf_ficc_share, a_mmf_ficc_share = _exact(
        {"FICC": raw["mmf_repo_ficc"], "REPO": raw["mmf_repo_total"]},
        lambda f: (f["FICC"] / f["REPO"].replace(0.0, np.nan)) * 100.0,
        "100 x MMF repo with FICC / total MMF repo",
    )
    mmf_fed_share, a_mmf_fed_share = _exact(
        {"FED": raw["mmf_repo_fed"], "REPO": raw["mmf_repo_total"]},
        lambda f: (f["FED"] / f["REPO"].replace(0.0, np.nan)) * 100.0,
        "100 x MMF repo with Federal Reserve / total MMF repo",
    )
    mmf_other_repo, a_mmf_other_repo = _exact(
        {
            "TOTAL": raw["mmf_repo_total"],
            "FICC": raw["mmf_repo_ficc"],
            "FED": raw["mmf_repo_fed"],
        },
        lambda f: f["TOTAL"] - f["FICC"] - f["FED"],
        "total MMF repo - FICC repo - Federal Reserve repo",
    )
    mmf_metrics = [
        _card(
            metric_id="mmf.total_assets",
            label="Money-market-fund total assets",
            series=raw["mmf_total"],
            unit="$B",
            cadence="monthly",
            source="OFR Short-Term Funding Monitor",
            explanation="Aggregate assets of reporting money-market funds; this is a stock, not monthly investor flow.",
            change_unit="$B",
            digits=2,
        ),
        _card(
            metric_id="mmf.total_repo",
            label="MMF total repo lending",
            series=raw["mmf_repo_total"],
            unit="$B",
            cadence="monthly",
            source="OFR Short-Term Funding Monitor",
            explanation="Aggregate repo claims held by money-market funds across reported counterparties.",
            change_unit="$B",
            digits=2,
        ),
        _card(
            metric_id="mmf.repo_ficc",
            label="MMF repo with FICC",
            series=raw["mmf_repo_ficc"],
            unit="$B",
            cadence="monthly",
            source="OFR Short-Term Funding Monitor",
            explanation="Money funds' repo exposure routed through FICC, including sponsored-clearing plumbing.",
            change_unit="$B",
            digits=2,
        ),
        _card(
            metric_id="mmf.repo_fed",
            label="MMF repo with the Federal Reserve",
            series=raw["mmf_repo_fed"],
            unit="$B",
            cadence="monthly",
            source="OFR Short-Term Funding Monitor",
            explanation="Money funds' repo placement with the Fed, corresponding to the ON RRP channel at a slower reporting cadence.",
            change_unit="$B",
            digits=2,
        ),
        _card(
            metric_id="mmf.repo_share_of_assets",
            label="Repo share of MMF assets",
            series=mmf_total_repo_share,
            unit="%",
            cadence="monthly",
            source="OFR Short-Term Funding Monitor",
            explanation="The share of aggregate MMF assets allocated to reported repo on the same monthly observation date.",
            change_unit="percentage points",
            alignment=a_mmf_total_repo_share,
            formula="100 x total repo / total assets",
        ),
        _card(
            metric_id="mmf.ficc_share_of_repo",
            label="FICC share of MMF repo",
            series=mmf_ficc_share,
            unit="%",
            cadence="monthly",
            source="OFR Short-Term Funding Monitor",
            explanation="The portion of reported MMF repo routed to FICC on the same monthly date.",
            change_unit="percentage points",
            alignment=a_mmf_ficc_share,
            formula="100 x FICC repo / total repo",
        ),
        _card(
            metric_id="mmf.fed_share_of_repo",
            label="Federal Reserve share of MMF repo",
            series=mmf_fed_share,
            unit="%",
            cadence="monthly",
            source="OFR Short-Term Funding Monitor",
            explanation="The portion of reported MMF repo placed with the Fed on the same monthly date.",
            change_unit="percentage points",
            alignment=a_mmf_fed_share,
            formula="100 x Fed repo / total repo",
        ),
        _card(
            metric_id="mmf.other_repo",
            label="Other reported MMF repo",
            series=mmf_other_repo,
            unit="$B",
            cadence="monthly",
            source="OFR Short-Term Funding Monitor",
            explanation="The arithmetic remainder after subtracting FICC and Fed repo from total MMF repo; it is not a named counterparty bucket.",
            change_unit="$B",
            digits=2,
            alignment=a_mmf_other_repo,
            formula="total repo - FICC repo - Fed repo",
        ),
    ]

    sections = [
        _section(
            "policy_corridor",
            "Policy anchors and overnight spreads",
            "This section shows where secured and unsecured overnight cash traded relative to the rate paid on reserves.",
            policy_metrics,
        ),
        _section(
            "secured_distributions",
            "SOFR, TGCR, and BGCR distributions",
            "Headline rates can look calm while expensive transactions fan out; percentiles and widths expose that tail.",
            distribution_metrics,
        ),
        _section(
            "repo_segments",
            "Repo segments",
            "DVP, tri-party, and GCF are different pieces of the repo market, so their rates and volumes are kept separate.",
            repo_metrics,
        ),
        _section(
            "unsecured_funding",
            "Unsecured commercial paper",
            "Commercial-paper spreads show the extra public-market cost of unsecured three-month cash versus Treasury references.",
            unsecured_metrics,
        ),
        _section(
            "bills_cash_curve",
            "Bills and the cash curve",
            "The short Treasury curve is shown with its quote-convention basis so discount rates are not mistaken for par yields.",
            bills_metrics,
        ),
        _section(
            "liquidity_buffers",
            "Liquidity buffers and facilities",
            "Reserve balances, Treasury cash, ON RRP, SRF, and discount-window use describe the official-dollar plumbing without assigning a cause.",
            liquidity_metrics,
        ),
        _section(
            "mmf_plumbing",
            "Money-market-fund plumbing",
            "Monthly holdings show how the cash-provider side divides repo between FICC, the Fed, and the arithmetic remainder.",
            mmf_metrics,
        ),
    ]

    # ---- source coverage and native-clock freshness ----------------------
    sofr_source_spec = (
        (
            "nyfed_sofr_rate",
            "SOFR rate",
            "Federal Reserve Bank of New York",
            "SOFR percentRate (direct reference-rates feed)",
            "daily",
            core_sofr,
        )
        if direct_nyfed_sofr_fallback
        else ("fred_sofr", "SOFR", "New York Fed via FRED", "SOFR", "daily", core_sofr)
    )
    source_specs: list[tuple[str, str, str, str, str, pd.Series | pd.DataFrame]] = [
        sofr_source_spec,
        (
            "fred_effr",
            "Effective federal funds rate",
            "Federal Reserve via FRED",
            "EFFR",
            "daily",
            raw["effr"],
        ),
        (
            "fred_iorb",
            "Interest on reserve balances",
            "Federal Reserve via FRED",
            "IORB/IOER",
            "daily",
            raw["iorb"],
        ),
        (
            "nyfed_sofr_distribution",
            "SOFR distribution",
            "Federal Reserve Bank of New York",
            "SOFR percentiles and volume",
            "daily",
            frames["SOFR"],
        ),
        (
            "nyfed_tgcr_distribution",
            "TGCR distribution",
            "Federal Reserve Bank of New York",
            "TGCR percentiles and volume",
            "daily",
            frames["TGCR"],
        ),
        (
            "nyfed_bgcr_distribution",
            "BGCR distribution",
            "Federal Reserve Bank of New York",
            "BGCR percentiles and volume",
            "daily",
            frames["BGCR"],
        ),
        (
            "ofr_bgcr",
            "BGCR",
            "Office of Financial Research",
            "FNYR-BGCR-A",
            "daily",
            raw["bgcr"],
        ),
        (
            "ofr_tgcr",
            "TGCR",
            "Office of Financial Research",
            "FNYR-TGCR-A",
            "daily",
            raw["tgcr"],
        ),
        (
            "ofr_dvp_rate",
            "DVP repo rate",
            "Office of Financial Research",
            "REPO-DVP_AR_OO-P",
            "daily",
            raw["dvp_rate"],
        ),
        (
            "ofr_dvp_volume",
            "DVP repo volume",
            "Office of Financial Research",
            "REPO-DVP_TV_TOT-P",
            "daily",
            raw["dvp_volume"],
        ),
        (
            "ofr_tri_rate",
            "Tri-party repo rate",
            "Office of Financial Research",
            "REPO-TRI_AR_OO-P",
            "daily",
            raw["tri_rate"],
        ),
        (
            "ofr_tri_volume",
            "Tri-party repo volume",
            "Office of Financial Research",
            "REPO-TRI_TV_TOT-P",
            "daily",
            raw["tri_volume"],
        ),
        (
            "ofr_gcf_rate",
            "GCF repo rate",
            "Office of Financial Research",
            "REPO-GCF_AR_OO-P",
            "daily",
            raw["gcf_rate"],
        ),
        (
            "ofr_gcf_volume",
            "GCF repo volume",
            "Office of Financial Research",
            "REPO-GCF_TV_OO-P",
            "daily",
            raw["gcf_volume"],
        ),
        (
            "fred_cp_nonfinancial",
            "3m nonfinancial CP",
            "Federal Reserve via FRED",
            "DCPN3M",
            "daily",
            raw["cp_nonfinancial_3m"],
        ),
        (
            "fred_cp_financial",
            "3m financial CP",
            "Federal Reserve via FRED",
            "DCPF3M",
            "daily",
            raw["cp_financial_3m"],
        ),
        (
            "fred_treasury_3m",
            "3m Treasury constant maturity",
            "US Treasury via FRED",
            "DGS3MO",
            "daily",
            raw["treasury_3m"],
        ),
        (
            "fred_bill_4w",
            "4w Treasury bill",
            "US Treasury via FRED",
            "DTB4WK",
            "daily",
            raw["bill_4w"],
        ),
        (
            "fred_bill_3m",
            "3m Treasury bill",
            "US Treasury via FRED",
            "DTB3",
            "daily",
            raw["bill_3m"],
        ),
        (
            "fred_reserves",
            "Reserve balances",
            "Federal Reserve via FRED",
            "WRESBAL",
            "weekly",
            raw["reserves"],
        ),
        (
            "fiscal_tga",
            "Treasury General Account",
            "US Treasury FiscalData",
            "DTS operating cash balance",
            "daily",
            raw["tga"],
        ),
        (
            "fred_on_rrp",
            "ON RRP take-up",
            "Federal Reserve Bank of New York via FRED",
            "RRPONTSYD",
            "daily",
            raw["on_rrp"],
        ),
        (
            "nyfed_srf",
            "Standing Repo Facility",
            "Federal Reserve Bank of New York",
            "repo operation results",
            "daily",
            raw["srf"],
        ),
        (
            "fred_discount_window",
            "Discount-window primary credit",
            "Federal Reserve via FRED",
            "WLCFLPCL",
            "weekly",
            raw["discount_window"],
        ),
        (
            "ofr_mmf_total",
            "MMF total assets",
            "Office of Financial Research",
            "MMF-MMF_TOT-M",
            "monthly",
            raw["mmf_total"],
        ),
        (
            "ofr_mmf_repo_ficc",
            "MMF repo with FICC",
            "Office of Financial Research",
            "MMF-MMF_RP_wFICC-M",
            "monthly",
            raw["mmf_repo_ficc"],
        ),
        (
            "ofr_mmf_repo_fed",
            "MMF repo with the Fed",
            "Office of Financial Research",
            "MMF-MMF_RP_wFR-M",
            "monthly",
            raw["mmf_repo_fed"],
        ),
        (
            "ofr_mmf_repo_total",
            "MMF total repo",
            "Office of Financial Research",
            "MMF-MMF_RP_TOT-M",
            "monthly",
            raw["mmf_repo_total"],
        ),
    ]
    source_metadata = [
        _source_row(
            source_id=source_id,
            label=label,
            publisher=publisher,
            series_id=series_id,
            cadence=cadence,
            values=values,
            desk_asof=desk_asof,
            evaluation_asof=evaluated_at,
        )
        for source_id, label, publisher, series_id, cadence, values in source_specs
    ]
    status_counts = {
        status: sum(row["freshness"] == status for row in source_metadata)
        for status in ("fresh", "aging", "stale", "unavailable")
    }

    all_metrics = [metric for section in sections for metric in section["metrics"]]
    for metric in all_metrics:
        metric_asof = metric.get("asof")
        if metric.get("status") != "available" or not metric_asof:
            metric["age_days_vs_evaluation_asof"] = None
            metric["freshness"] = "unavailable"
            continue
        metric_age_days = max(
            0,
            int((evaluated_at - pd.Timestamp(metric_asof)).days),
        )
        metric["age_days_vs_evaluation_asof"] = metric_age_days
        metric["freshness"] = _freshness_status(metric_age_days, metric["cadence"])
    _refresh_section_clocks(sections)
    historical_available_metrics = sum(
        metric["status"] == "available" for metric in all_metrics
    )
    available_metrics = sum(
        metric.get("freshness") in {"fresh", "aging"} for metric in all_metrics
    )
    coverage = {
        "available_metrics": available_metrics,
        "historical_available_metrics": historical_available_metrics,
        "total_metrics": len(all_metrics),
        "coverage_pct": _number(100.0 * available_metrics / len(all_metrics), 1),
        "available_sources": sum(
            row["freshness"] in {"fresh", "aging"} for row in source_metadata
        ),
        "historical_available_sources": sum(
            row["available"] for row in source_metadata
        ),
        "total_sources": len(source_metadata),
        "non_stale_metrics": sum(
            metric.get("freshness") in {"fresh", "aging"} for metric in all_metrics
        ),
        "sections": [
            {
                "id": section["id"],
                "status": section["status"],
                "available_metrics": section["available_metrics"],
                "historical_available_metrics": section["historical_available_metrics"],
                "stale_metrics": section["stale_metrics"],
                "total_metrics": section["total_metrics"],
            }
            for section in sections
        ],
        "unavailable_metrics": [
            metric["id"] for metric in all_metrics if metric["status"] != "available"
        ],
        "stale_metrics": [
            metric["id"] for metric in all_metrics if metric.get("freshness") == "stale"
        ],
        "rule": "available counts include only fresh or aging cards at the explicit evaluation date; historical values remain separately counted and visible",
    }
    freshness = {
        "desk_asof": desk_asof.date().isoformat(),
        "evaluation_asof": evaluated_at.date().isoformat(),
        "basis": "age of each source's latest used observation versus the explicit response or replay evaluation date; cadence-specific thresholds",
        "thresholds_days": {
            "daily": {"fresh_through": 3, "aging_through": 7},
            "weekly": {"fresh_through": 10, "aging_through": 21},
            "monthly": {"fresh_through": 45, "aging_through": 75},
        },
        "status_counts": status_counts,
        "by_source": [
            {
                key: row[key]
                for key in (
                    "id",
                    "asof",
                    "cadence",
                    "age_days_vs_desk_asof",
                    "age_days_vs_evaluation_asof",
                    "freshness",
                )
            }
            for row in source_metadata
        ],
    }

    # ---- descriptive worst-channel regime --------------------------------
    metric_by_id = {metric["id"]: metric for metric in all_metrics}
    regime_specs = [
        (
            "policy.sofr_minus_iorb",
            "high",
            "secured cash above the reserve-rate anchor",
        ),
        ("distribution.sofr.p99_minus_rate", "high", "SOFR's expensive tail"),
        ("distribution.sofr.iqr", "high", "SOFR transaction dispersion"),
        ("repo.dvp_minus_sofr", "high", "DVP repo paying above SOFR"),
        ("repo.gcf_minus_sofr", "high", "GCF repo paying above SOFR"),
        (
            "unsecured.nonfinancial_cp_minus_treasury",
            "high",
            "nonfinancial unsecured funding spread",
        ),
        (
            "unsecured.financial_cp_minus_treasury",
            "high",
            "financial unsecured funding spread",
        ),
        ("liquidity.srf", "high", "Standing Repo Facility use"),
        ("liquidity.discount_window", "high", "discount-window use"),
    ]
    indicators: list[dict] = []
    excluded_indicators: list[dict] = [
        {
            "metric_id": metric_id,
            "asof": metric_by_id[metric_id].get("asof"),
            "freshness": metric_by_id[metric_id].get("freshness", "unavailable"),
            "reason": "contextual_stock_level_not_headline_anomaly",
            "interpretation": explanation,
        }
        for metric_id, explanation in (
            (
                "liquidity.reserves",
                "Reserve balances remain a visible system-liquidity stock, but a low level alone is not a calibrated stress event.",
            ),
            (
                "liquidity.on_rrp",
                "ON RRP remains visible as a facility stock, but depletion can reflect normal policy transmission and is not a standalone anomaly headline.",
            ),
        )
    ]
    for metric_id, direction, interpretation in regime_specs:
        metric = metric_by_id[metric_id]
        percentile = metric.get("percentile_3y")
        if percentile is None or metric.get("value") is None:
            excluded_indicators.append(
                {
                    "metric_id": metric_id,
                    "asof": metric.get("asof"),
                    "freshness": metric.get("freshness", "unavailable"),
                    "reason": "unavailable_or_insufficient_history",
                }
            )
            continue
        if metric.get("freshness") == "stale":
            excluded_indicators.append(
                {
                    "metric_id": metric_id,
                    "asof": metric.get("asof"),
                    "freshness": "stale",
                    "reason": "stale_at_evaluation_asof",
                }
            )
            continue
        raw_stress_percentile = (
            float(percentile) if direction == "high" else 100.0 - float(percentile)
        )
        indicators.append(
            {
                "metric_id": metric_id,
                "label": metric["label"],
                "value": metric["value"],
                "unit": metric["unit"],
                "asof": metric["asof"],
                "freshness": metric["freshness"],
                "age_days_vs_evaluation_asof": metric["age_days_vs_evaluation_asof"],
                "direction": direction,
                "empirical_percentile_3y": percentile,
                "empirical_sample_n": metric["percentile_3y_n"],
                "raw_stress_percentile": _number(raw_stress_percentile, 1),
                "interpretation": interpretation,
            }
        )
    indicators = _familywise_adjust_indicators(indicators)
    ranked = sorted(
        indicators, key=lambda row: (-float(row["stress_percentile"]), row["metric_id"])
    )
    worst = ranked[0] if ranked else None
    score = float(worst["stress_percentile"]) if worst is not None else None
    raw_score = float(worst["raw_stress_percentile"]) if worst is not None else None
    state = _regime_state(score)
    regime = {
        "state": state,
        "raw_worst_stress_percentile": _number(raw_score, 1),
        "worst_stress_percentile": _number(score, 1),
        "bonferroni_adjusted_worst_stress_percentile": _number(score, 1),
        "worst_indicator": worst,
        "indicators": ranked,
        "excluded_indicators": excluded_indicators,
        "familywise_adjustment": {
            "method": "bonferroni_empirical_upper_tail",
            "eligible_hypotheses": len(ranked),
            "formula": "adjusted tail probability = min(1, m x (1 - raw stress percentile / 100)); adjusted stress percentile = 100 x (1 - adjusted tail probability)",
            "dependence_assumption": "valid under arbitrary cross-channel dependence",
            "headline_uses": "bonferroni_adjusted_stress_percentile",
        },
        "thresholds": {
            "NORMAL": "Bonferroni-adjusted p < 75",
            "WATCH": "75 <= Bonferroni-adjusted p < 90",
            "STRAIN": "90 <= Bonferroni-adjusted p < 97.5",
            "STRESS": "Bonferroni-adjusted p >= 97.5",
        },
        "rule": "worst Bonferroni-adjusted non-stale available three-year empirical anomaly percentile; raw ranks remain visible; reserve and ON-RRP stocks are context only; no averaging",
        "minimum_history": "60 daily, 26 weekly, or 12 monthly observations inside the trailing three-year window",
        "status": "descriptive_context_only_not_forecast_probability_or_trade_signal",
    }
    strongest_signal = (
        {
            **worst,
            "why_selected": "highest Bonferroni-adjusted empirical stress percentile among eligible indicators",
            "use": "context only; not causal, predictive, or directly tradable",
        }
        if worst is not None
        else {
            "metric_id": None,
            "label": "No empirically scaled indicator available",
            "value": None,
            "unit": None,
            "asof": None,
            "raw_stress_percentile": None,
            "bonferroni_adjusted_stress_percentile": None,
            "stress_percentile": None,
            "why_selected": (
                "all empirically scaled configured indicators are stale"
                if any(
                    row["reason"] == "stale_at_evaluation_asof"
                    for row in excluded_indicators
                )
                else "insufficient trailing history"
            ),
            "use": "no inference",
        }
    )
    # A countercase must be independent of the headline.  With only one
    # scaled channel it would be misleading to present that same observation
    # as both thesis and counterweight.
    counter = (
        min(
            ranked[1:],
            key=lambda row: (float(row["stress_percentile"]), row["metric_id"]),
        )
        if len(ranked) > 1
        else None
    )
    countercase = (
        {
            **counter,
            "reading": "This observed channel is the least stressed counterweight among the configured indicators.",
            "limit": "A calm counterweight does not invalidate stress elsewhere, and a stressed reading does not establish cause.",
        }
        if counter is not None
        else {
            "metric_id": None,
            "reading": "There is not a second empirically scaled channel to serve as an independent counterweight.",
            "limit": "Missing or insufficiently long data are not treated as calm.",
        }
    )
    if worst is None:
        if any(
            row["reason"] == "stale_at_evaluation_asof" for row in excluded_indicators
        ):
            plain_language = "The desk has historical observations, but none of its empirically scaled indicators is current enough to describe today's regime. Stale evidence is shown for context and is not treated as calm."
            quant_read = "CANNOT_ASSESS: every otherwise rankable configured indicator is stale at the explicit evaluation date and is excluded from regime selection."
        else:
            plain_language = "The core SOFR-IORB relationship is available, but the desk does not have enough trailing history to rank stress reliably. Missing sections remain visibly unavailable."
            quant_read = "No non-stale configured indicator met its cadence-specific minimum history for a three-year empirical rank."
    else:
        plain_language = {
            "NORMAL": "The most stretched observed channel is still below the desk's watch threshold relative to its own recent history.",
            "WATCH": "At least one money-market channel is unusually stretched versus its own recent history, but this is an early context flag rather than evidence of system-wide stress.",
            "STRAIN": "At least one observed channel is in the top tenth of its own recent stress distribution; check the other sections before generalizing it to the whole market.",
            "STRESS": "At least one observed channel is near the extreme of its own three-year history. This describes an exceptional print, not its cause or the next market move.",
            "CANNOT_ASSESS": "The desk cannot scale the current observations against enough history.",
        }[state]
        quant_read = (
            f"Family-wise headline selects {worst['label']} at adjusted stress p{worst['stress_percentile']:.1f} "
            f"(raw p{worst['raw_stress_percentile']:.1f}): "
            f"{worst['value']} {worst['unit']} as of {worst['asof']}. "
            f"Bonferroni m={len(ranked)} controls the empirical upper-tail family under arbitrary dependence. "
            f"Metric coverage is {coverage['coverage_pct']:.1f}%; no cross-channel averaging or causal claim is applied."
        )

    charts = {
        "policy": _chart(
            "policy",
            "Overnight policy anchors and spreads",
            {
                "sofr_pct": core_sofr,
                "effr_pct": raw["effr"],
                "iorb_pct": raw["iorb"],
                "sofr_minus_iorb_bp": sofr_iorb,
                "effr_minus_iorb_bp": effr_iorb,
            },
            cadence="daily",
            limit=DAILY_CHART_ROWS,
        ),
        "sofr_distribution": _chart(
            "sofr_distribution",
            "SOFR distribution and volume",
            {
                "p01_pct": distribution_series["SOFR"]["p01"],
                "rate_pct": distribution_series["SOFR"]["rate"],
                "p99_pct": distribution_series["SOFR"]["p99"],
                "p99_minus_rate_bp": distribution_series["SOFR"]["p99_tail"],
                "volume_b": distribution_series["SOFR"]["volume"],
            },
            cadence="daily",
            limit=DAILY_CHART_ROWS,
        ),
        "repo_rates": _chart(
            "repo_rates",
            "Repo segment rates",
            {
                "sofr_pct": core_sofr,
                "bgcr_pct": raw["bgcr"],
                "tgcr_pct": raw["tgcr"],
                "dvp_pct": raw["dvp_rate"],
                "tri_pct": raw["tri_rate"],
                "gcf_pct": raw["gcf_rate"],
            },
            cadence="daily",
            limit=DAILY_CHART_ROWS,
        ),
        "repo_volumes": _chart(
            "repo_volumes",
            "Repo segment volumes",
            {
                "dvp_b": raw["dvp_volume"],
                "tri_b": raw["tri_volume"],
                "gcf_b": raw["gcf_volume"],
                "displayed_total_b": total_repo_volume,
            },
            cadence="daily",
            limit=DAILY_CHART_ROWS,
            digits=2,
        ),
        "unsecured": _chart(
            "unsecured",
            "Commercial-paper spreads",
            {
                "nonfinancial_cp_minus_treasury_bp": cp_nf_bill,
                "financial_cp_minus_treasury_bp": cp_fin_bill,
                "financial_minus_nonfinancial_bp": cp_fin_nf,
            },
            cadence="daily",
            limit=DAILY_CHART_ROWS,
        ),
        "bills": _chart(
            "bills",
            "Bills and overnight cash",
            {
                "bill_4w_pct": raw["bill_4w"],
                "bill_3m_pct": raw["bill_3m"],
                "treasury_3m_pct": raw["treasury_3m"],
                "sofr_pct": core_sofr,
                "curve_3m_minus_4w_bp": bill_curve,
            },
            cadence="daily",
            limit=DAILY_CHART_ROWS,
        ),
        "liquidity": _chart(
            "liquidity",
            "Liquidity buffers and facility use",
            {
                "reserves_b": raw["reserves"],
                "tga_b": raw["tga"],
                "on_rrp_b": raw["on_rrp"],
                "srf_b": raw["srf"],
                "discount_window_b": raw["discount_window"],
            },
            cadence="mixed daily/weekly",
            limit=DAILY_CHART_ROWS,
            digits=2,
        ),
        "mmf": _chart(
            "mmf",
            "Money-market-fund repo plumbing",
            {
                "total_assets_b": raw["mmf_total"],
                "total_repo_b": raw["mmf_repo_total"],
                "ficc_repo_b": raw["mmf_repo_ficc"],
                "fed_repo_b": raw["mmf_repo_fed"],
                "other_repo_b": mmf_other_repo,
            },
            cadence="monthly",
            limit=MONTHLY_CHART_ROWS,
            digits=2,
        ),
    }

    formulas = [
        {
            "id": "basis_point_conversion",
            "expression": "100 x difference between rates quoted in percent",
            "unit": "bp",
        },
        {
            "id": "sofr_iorb",
            "expression": "100 x (SOFR - IORB)",
            "alignment": "exact observation date",
        },
        {
            "id": "sofr_tail",
            "expression": "100 x (SOFR P99 - published SOFR rate)",
            "alignment": "same NY Fed row",
        },
        {
            "id": "secured_iqr",
            "expression": "100 x (P75 - P25)",
            "alignment": "same NY Fed row",
        },
        {
            "id": "secured_tail_skew",
            "expression": "(P99 - published rate) / (published rate - P01)",
            "alignment": "same NY Fed row",
        },
        {
            "id": "repo_segment_spread",
            "expression": "100 x (segment rate - SOFR)",
            "alignment": "exact observation date",
        },
        {
            "id": "displayed_repo_volume",
            "expression": "sum of a fixed DVP/tri-party/GCF component mask selected on the latest date with at least two prints",
            "alignment": "exact observation date and exact component mask; different compositions are excluded, never set to zero",
        },
        {
            "id": "cp_treasury",
            "expression": "100 x (3m CP - 3m Treasury constant maturity)",
            "alignment": "exact observation date",
        },
        {
            "id": "bill_curve",
            "expression": "100 x (3m bill discount rate - 4w bill discount rate)",
            "alignment": "exact observation date",
        },
        {
            "id": "broad_liquidity_buffer",
            "expression": "reserve balances + ON RRP take-up",
            "alignment": "exact observation date",
        },
        {
            "id": "mmf_repo_share",
            "expression": "100 x MMF repo bucket / denominator",
            "alignment": "exact monthly observation date",
        },
        {
            "id": "robust_z_1y",
            "expression": "(latest - trailing-1y median) / (1.4826 x trailing-1y MAD)",
        },
        {
            "id": "percentile_3y",
            "expression": "100 x (count below latest + 0.5 x count tied) / trailing-3y count",
        },
        {
            "id": "bonferroni_headline",
            "expression": "100 x (1 - min(1, m x (1 - raw stress percentile / 100)))",
            "alignment": "m is the number of non-stale eligible anomaly channels evaluated for that headline",
        },
    ]
    methodology = {
        "purpose": "institutional-grade descriptive context for the public USD money market",
        "evidence_horizon": f"all inputs are clipped to the {horizon_basis}",
        "alignment": "all cross-source arithmetic uses exact-date inner joins; unrelated source clocks are never forward-filled",
        "changes": "1d/5d/20d means one/five/twenty observations for native-daily series; weekly and monthly cards use explicitly named native lags",
        "statistics": "one-year median/MAD robust z and three-year tie-aware empirical percentile, with cadence-specific minimum samples",
        "regime": "worst Bonferroni-adjusted non-stale anomaly percentile with published thresholds; the raw percentile is retained, reserve/ON-RRP stock levels are context only, stale inputs are excluded, and there is no weighted blend",
        "charts": f"at most {DAILY_CHART_ROWS} rows for daily or mixed charts and {MONTHLY_CHART_ROWS} rows for monthly charts; null means no print on that display date",
        "availability": "SOFR and IORB are the only hard requirements; every other missing leg becomes an explicit partial section",
    }
    caveats = [
        "This engine is descriptive context only. It is not a forecast, causal model, probability, investment recommendation, or executable trading signal.",
        "Exact-date joins avoid invented observations but reduce overlap when sources publish on different calendars; each derived card reports the overlap used.",
        "SOFR, EFFR, IORB, Treasury par yields, and bill discount rates do not all share the same instrument, tenor, counterparty set, or quote convention.",
        "NY Fed percentiles describe the distribution of reported benchmark transactions, not prices guaranteed to any individual borrower or lender.",
        "OFR DVP, tri-party, and GCF series describe published segments; their displayed fixed-composition sum is not represented as total US repo market size, and dates with a different component mask are excluded from aggregate comparisons.",
        "GCF overnight/open nulls may mean no qualifying print rather than a failed collector; the engine preserves the null.",
        "Commercial-paper spreads combine credit, liquidity, term, tax, and quote-convention effects; they are not standalone default-risk measures.",
        "Reserve balances, ON RRP, TGA, SRF, and discount-window balances are system aggregates. Their changes do not identify holder-level access or establish a one-for-one causal flow.",
        "MMF holdings are monthly stocks and can lag daily repo conditions. The 'other repo' bucket is an arithmetic remainder, not a separately reported counterparty class.",
        "Historical percentile thresholds adapt to the available trailing sample and can change as old observations leave the window; they are ranks, not calibrated event probabilities. The headline applies a conservative Bonferroni tail adjustment across eligible channels.",
        "Freshness is measured against an explicit live-response or replay evaluation date. Stale metrics remain visible as historical context but cannot set the descriptive regime.",
    ]
    if not core_aligned:
        caveats.insert(
            1,
            "SOFR and IORB are both present but have no exact common date; their raw cards remain visible and SOFR-IORB remains unavailable rather than being forward-filled.",
        )

    return _json_safe(
        {
            "ok": True,
            "schema": SCHEMA,
            "asof": desk_asof.date().isoformat(),
            "context_only": True,
            "regime": regime,
            "plain_language": plain_language,
            "quant_read": quant_read,
            "strongest_signal": strongest_signal,
            "countercase": countercase,
            "coverage": coverage,
            "freshness": freshness,
            "sections": sections,
            "charts": charts,
            "methodology": methodology,
            "formulas": formulas,
            "caveats": caveats,
            "source_metadata": source_metadata,
            # Existing Seiche engines conventionally expose this shorter key.
            "sources": source_metadata,
            "legal_notices": LEGAL_NOTICES,
            "diagnostics": _funding_diagnostics(
                core_sofr,
                raw["iorb"],
                raw["effr"],
                evaluated_at=evaluated_at,
                sofr_source_id=sofr_source_spec[0],
            ),
        }
    )


def refresh_for_evaluation(
    payload: Mapping[str, Any],
    *,
    evaluation_asof: pd.Timestamp | str,
) -> dict:
    """Age a completed desk at read time without changing its observations.

    Seiche deliberately serves a last-known-good snapshot while a background
    rebuild runs or fails. Values and histories in that snapshot are immutable,
    but their freshness is not: a once-current daily print can become stale.
    This projection advances source/metric clocks and rebuilds the descriptive
    family-wise-adjusted worst-channel regime using only indicators that remain
    fresh or aging.
    """

    out = copy.deepcopy(dict(payload))
    if out.get("schema") != SCHEMA or out.get("ok") is not True:
        return _json_safe(out)
    desk_asof_raw = out.get("asof")
    if not isinstance(desk_asof_raw, str):
        return _json_safe(out)
    evaluated_at = _evaluation_date(
        evaluation_asof,
        desk_asof=pd.Timestamp(desk_asof_raw),
    )

    diagnostics = out.get("diagnostics")
    if isinstance(diagnostics, dict):
        _refresh_diagnostic_clocks(diagnostics, evaluated_at)

    def age_days(asof: Any) -> int | None:
        if not isinstance(asof, str) or not asof:
            return None
        try:
            observed = pd.Timestamp(asof)
        except (TypeError, ValueError):
            return None
        if pd.isna(observed):
            return None
        if observed.tzinfo is not None:
            observed = observed.tz_convert("UTC").tz_localize(None)
        return max(0, int((evaluated_at - observed.normalize()).days))

    metrics: list[dict] = []
    sections = [
        section for section in out.get("sections") or [] if isinstance(section, dict)
    ]
    for section in sections:
        for metric in section.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            metrics.append(metric)
            age = age_days(metric.get("asof"))
            if metric.get("status") != "available" or age is None:
                metric["age_days_vs_evaluation_asof"] = None
                metric["freshness"] = "unavailable"
            else:
                metric["age_days_vs_evaluation_asof"] = age
                metric["freshness"] = _freshness_status(
                    age,
                    str(metric.get("cadence") or "daily"),
                )
    _refresh_section_clocks(sections)

    source_metadata = out.get("source_metadata") or out.get("sources") or []
    source_metadata = [row for row in source_metadata if isinstance(row, dict)]
    for row in source_metadata:
        age = age_days(row.get("asof"))
        row["age_days_vs_evaluation_asof"] = age
        row["freshness"] = (
            _freshness_status(age, str(row.get("cadence") or "daily"))
            if age is not None
            else "unavailable"
        )
    out["source_metadata"] = source_metadata
    out["sources"] = copy.deepcopy(source_metadata)

    freshness = out.get("freshness")
    freshness = freshness if isinstance(freshness, dict) else {}
    freshness["evaluation_asof"] = evaluated_at.date().isoformat()
    freshness["basis"] = (
        "age of each source's latest used observation versus the explicit "
        "response or replay evaluation date; cadence-specific thresholds"
    )
    freshness["status_counts"] = {
        status: sum(row.get("freshness") == status for row in source_metadata)
        for status in ("fresh", "aging", "stale", "unavailable")
    }
    freshness["by_source"] = [
        {
            key: row.get(key)
            for key in (
                "id",
                "asof",
                "cadence",
                "age_days_vs_desk_asof",
                "age_days_vs_evaluation_asof",
                "freshness",
            )
        }
        for row in source_metadata
    ]
    out["freshness"] = freshness
    coverage = out.get("coverage")
    if isinstance(coverage, dict):
        historical_available_metrics = sum(
            metric.get("status") == "available" for metric in metrics
        )
        current_metrics = sum(
            metric.get("freshness") in {"fresh", "aging"} for metric in metrics
        )
        coverage["available_metrics"] = current_metrics
        coverage["historical_available_metrics"] = historical_available_metrics
        coverage["non_stale_metrics"] = current_metrics
        coverage["coverage_pct"] = _number(
            100.0 * current_metrics / len(metrics) if metrics else 0.0,
            1,
        )
        coverage["available_sources"] = sum(
            row.get("freshness") in {"fresh", "aging"} for row in source_metadata
        )
        coverage["historical_available_sources"] = sum(
            row.get("asof") is not None for row in source_metadata
        )
        coverage["sections"] = [
            {
                key: section.get(key)
                for key in (
                    "id",
                    "status",
                    "available_metrics",
                    "historical_available_metrics",
                    "stale_metrics",
                    "total_metrics",
                )
            }
            for section in sections
        ]
        coverage["stale_metrics"] = [
            metric.get("id") for metric in metrics if metric.get("freshness") == "stale"
        ]

    metric_by_id = {metric.get("id"): metric for metric in metrics if metric.get("id")}
    regime = out.get("regime")
    regime = regime if isinstance(regime, dict) else {}
    original_indicators = regime.get("indicators")
    original_indicators = (
        original_indicators if isinstance(original_indicators, list) else []
    )
    excluded = regime.get("excluded_indicators")
    excluded = excluded if isinstance(excluded, list) else []
    excluded_by_id = {
        row.get("metric_id"): dict(row)
        for row in excluded
        if isinstance(row, dict) and row.get("metric_id")
    }
    for metric_id, row in excluded_by_id.items():
        if row.get("reason") != "contextual_stock_level_not_headline_anomaly":
            continue
        metric = metric_by_id.get(metric_id)
        row["asof"] = metric.get("asof") if metric is not None else None
        row["freshness"] = (
            metric.get("freshness") if metric is not None else "unavailable"
        )
    eligible: list[dict] = []
    for indicator in original_indicators:
        if not isinstance(indicator, dict):
            continue
        metric_id = indicator.get("metric_id")
        metric = metric_by_id.get(metric_id)
        if metric is not None and metric.get("freshness") in {"fresh", "aging"}:
            current = dict(indicator)
            current["freshness"] = metric["freshness"]
            current["age_days_vs_evaluation_asof"] = metric.get(
                "age_days_vs_evaluation_asof"
            )
            eligible.append(current)
            continue
        excluded_by_id[metric_id] = {
            "metric_id": metric_id,
            "asof": metric.get("asof") if metric is not None else None,
            "freshness": (
                metric.get("freshness") if metric is not None else "unavailable"
            ),
            "reason": (
                "stale_at_evaluation_asof"
                if metric is not None and metric.get("freshness") == "stale"
                else "unavailable_at_evaluation_asof"
            ),
        }
    eligible = _familywise_adjust_indicators(eligible)
    ranked = sorted(
        eligible,
        key=lambda row: (-float(row["stress_percentile"]), str(row["metric_id"])),
    )
    worst = ranked[0] if ranked else None
    score = float(worst["stress_percentile"]) if worst is not None else None
    raw_score = float(worst["raw_stress_percentile"]) if worst is not None else None
    state = _regime_state(score)
    regime["state"] = state
    regime["raw_worst_stress_percentile"] = _number(raw_score, 1)
    regime["worst_stress_percentile"] = _number(score, 1)
    regime["bonferroni_adjusted_worst_stress_percentile"] = _number(score, 1)
    regime["worst_indicator"] = worst
    regime["indicators"] = ranked
    regime["familywise_adjustment"] = {
        "method": "bonferroni_empirical_upper_tail",
        "eligible_hypotheses": len(ranked),
        "formula": "adjusted tail probability = min(1, m x (1 - raw stress percentile / 100)); adjusted stress percentile = 100 x (1 - adjusted tail probability)",
        "dependence_assumption": "valid under arbitrary cross-channel dependence",
        "headline_uses": "bonferroni_adjusted_stress_percentile",
    }
    regime["excluded_indicators"] = [
        excluded_by_id[key] for key in sorted(excluded_by_id, key=str)
    ]
    out["regime"] = regime

    if worst is None:
        stale = any(
            row.get("reason") == "stale_at_evaluation_asof"
            for row in excluded_by_id.values()
        )
        out["strongest_signal"] = {
            "metric_id": None,
            "label": "No current empirically scaled indicator available",
            "value": None,
            "unit": None,
            "asof": None,
            "raw_stress_percentile": None,
            "bonferroni_adjusted_stress_percentile": None,
            "stress_percentile": None,
            "why_selected": (
                "all empirically scaled configured indicators are stale"
                if stale
                else "insufficient current trailing history"
            ),
            "use": "no inference",
        }
        out["countercase"] = {
            "metric_id": None,
            "reading": "There is not a second current empirically scaled channel to serve as an independent counterweight.",
            "limit": "Missing, stale, or insufficiently long data are not treated as calm.",
        }
        out["plain_language"] = (
            "The desk has historical observations, but none of its empirically "
            "scaled indicators is current enough to describe today's regime. "
            "Stale evidence is shown for context and is not treated as calm."
            if stale
            else "The desk does not have enough current trailing history to rank stress reliably."
        )
        out["quant_read"] = (
            "CANNOT_ASSESS: every otherwise rankable configured indicator is "
            "stale at the explicit evaluation date and is excluded from regime selection."
            if stale
            else "No non-stale configured indicator meets its cadence-specific empirical-history threshold."
        )
        return _json_safe(out)

    out["strongest_signal"] = {
        **worst,
        "why_selected": "highest Bonferroni-adjusted empirical stress percentile among non-stale eligible indicators",
        "use": "context only; not causal, predictive, or directly tradable",
    }
    counter = (
        min(
            ranked[1:],
            key=lambda row: (
                float(row["stress_percentile"]),
                str(row["metric_id"]),
            ),
        )
        if len(ranked) > 1
        else None
    )
    out["countercase"] = (
        {
            **counter,
            "reading": "This observed channel is the least stressed current counterweight among the configured indicators.",
            "limit": "A calm counterweight does not invalidate stress elsewhere, and a stressed reading does not establish cause.",
        }
        if counter is not None
        else {
            "metric_id": None,
            "reading": "There is not a second current empirically scaled channel to serve as an independent counterweight.",
            "limit": "Missing, stale, or insufficiently long data are not treated as calm.",
        }
    )
    out["plain_language"] = {
        "NORMAL": "The most stretched current observed channel is still below the desk's watch threshold relative to its own recent history.",
        "WATCH": "At least one current money-market channel is unusually stretched versus its own recent history, but this is context rather than evidence of system-wide stress.",
        "STRAIN": "At least one current observed channel is in the top tenth of its own recent stress distribution; check other sections before generalizing.",
        "STRESS": "At least one current observed channel is near the extreme of its own three-year history. This describes an exceptional print, not its cause or the next move.",
    }[state]
    coverage_pct = (
        float(coverage.get("coverage_pct") or 0.0)
        if isinstance(coverage, dict)
        else 0.0
    )
    out["quant_read"] = (
        f"Family-wise headline selects {worst['label']} at adjusted stress "
        f"p{worst['stress_percentile']:.1f} (raw p{worst['raw_stress_percentile']:.1f}): "
        f"{worst['value']} {worst['unit']} as of {worst['asof']}. "
        f"Bonferroni m={len(ranked)} controls the empirical upper-tail family under arbitrary dependence. "
        f"Metric coverage is {coverage_pct:.1f}%; "
        "stale indicators are excluded and no cross-channel averaging or causal claim is applied."
    )
    return _json_safe(out)
