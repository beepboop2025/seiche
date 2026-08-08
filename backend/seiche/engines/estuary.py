"""The Estuary — where FX and physical-market cash demands meet funding.

Raw quote screens answer what moved.  This context engine asks whether the
cash consequences of those moves are already visible in SOFR and commercial
paper.  Its signature output, The Passage, keeps direction and timing honest:
candidate lead/lag links are selected on an early discovery sample and only
called ``earned`` when the same sign survives the untouched later sample.

This engine never enters the Seiche composite.  Daily H.10 / EIA series drive
the live passage and analog ledger.  Monthly IMF commodity benchmarks add
category breadth, but are never forward-filled into daily evidence.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from statistics import NormalDist

import numpy as np
import pandas as pd

from seiche.config import (
    ESTUARY_BIS_FX_STRUCTURE,
    ESTUARY_FX_WEIGHTS,
    ESTUARY_GAP_BUILDING,
    ESTUARY_GAP_OPEN,
    ESTUARY_MATERIAL_WEIGHTS,
    ESTUARY_SCENARIO_DEFAULTS,
    ESTUARY_UPSTREAM_WEIGHTS,
)


DAILY_MIN = 120
MONTHLY_MIN = 24
SCORE_WINDOW_D = 756
SCORE_WINDOW_M = 120
PASSAGE_LAGS = (0, 1, 3, 5, 10)
PASSAGE_DISCOVERY_SHARE = 0.60
PASSAGE_EARNED_R = 0.10
PASSAGE_TENTATIVE_R = 0.05
ANALOG_K = 8
ANALOG_SEPARATION_D = 28
ANALOG_HORIZON = 10
FUNDING_EVENT_BP = 5.0
_NORMAL = NormalDist()


def _clean(series: pd.Series | None) -> pd.Series:
    if series is None or not isinstance(series, pd.Series):
        return pd.Series(dtype=float)
    out = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    return out[~out.index.duplicated(keep="last")].astype(float)


def _spread_bp(rate: pd.Series, bill: pd.Series) -> pd.Series:
    """Rate minus reference yield on actual rate-print dates, in bp."""

    left, right = _clean(rate), _clean(bill)
    if left.empty or right.empty:
        return pd.Series(dtype=float)
    grid = left.index.union(right.index)
    frame = pd.concat(
        {"rate": left.reindex(grid), "bill": right.reindex(grid)}, axis=1
    ).sort_index()
    frame["bill"] = frame["bill"].ffill(limit=5)
    frame = frame.reindex(left.index).dropna()
    return ((frame["rate"] - frame["bill"]) * 100.0).dropna()


def _normalise_fx(series: pd.Series, quote: str) -> pd.Series:
    """Return local-currency units per USD for one consistent direction."""

    values = _clean(series)
    if quote == "usd_per_local":
        return (1.0 / values.replace(0.0, np.nan)).dropna()
    return values


def _last(series: pd.Series) -> tuple[float | None, str | None]:
    values = _clean(series)
    if values.empty:
        return None, None
    return float(values.iloc[-1]), values.index[-1].date().isoformat()


def _change(series: pd.Series, periods: int, *, percent: bool = True) -> float | None:
    values = _clean(series)
    if len(values) <= periods:
        return None
    now, before = float(values.iloc[-1]), float(values.iloc[-periods - 1])
    if percent:
        if before == 0.0:
            return None
        return (now / before - 1.0) * 100.0
    return now - before


def _midrank_last(
    series: pd.Series, *, window: int, minimum: int
) -> float | None:
    """Current percentile with ties at their midpoint (constant -> 50)."""

    values = _clean(series).tail(window)
    if len(values) < minimum:
        return None
    now = float(values.iloc[-1])
    below = int((values < now).sum())
    equal = int(np.isclose(values.to_numpy(dtype=float), now).sum())
    return 100.0 * (below + 0.5 * equal) / len(values)


def _weighted(values: Mapping[str, float | None], weights: Mapping[str, float]) -> float | None:
    live = [(float(values[k]), float(w)) for k, w in weights.items() if values.get(k) is not None]
    denom = sum(weight for _, weight in live)
    if not live or denom <= 0.0:
        return None
    return sum(value * weight for value, weight in live) / denom


def _median(values: list[float | None]) -> float | None:
    live = [float(value) for value in values if value is not None and np.isfinite(value)]
    return float(np.median(live)) if live else None


def _rolling_z(series: pd.Series, *, window: int = SCORE_WINDOW_D, minimum: int = DAILY_MIN) -> pd.Series:
    values = _clean(series)
    mean = values.rolling(window, min_periods=minimum).mean()
    std = values.rolling(window, min_periods=minimum).std(ddof=0)
    z = (values - mean) / std.replace(0.0, np.nan)
    return z.where(std > 1e-12, 0.0).replace([np.inf, -np.inf], np.nan)


def _z_score(series: pd.Series) -> pd.Series:
    z = series.clip(-4.0, 4.0)
    return z.map(lambda value: 100.0 * _NORMAL.cdf(float(value)) if pd.notna(value) else np.nan)


def _adaptive_rows(
    series: Mapping[str, pd.Series], *, years: int = 5, recent_days: int = 180, digits: int = 2
) -> list[list[str | float | None]]:
    live = {name: _clean(values) for name, values in series.items() if not _clean(values).empty}
    if not live:
        return []
    frame = pd.concat(live, axis=1, sort=True).sort_index()
    end = frame.index.max()
    frame = frame[frame.index >= end - pd.DateOffset(years=years)]
    recent_cut = end - pd.Timedelta(days=recent_days)
    old = frame[frame.index < recent_cut]
    recent = frame[frame.index >= recent_cut]
    if not old.empty:
        old = old.groupby(pd.Grouper(freq="W-FRI")).tail(1)
    sampled = pd.concat([old, recent], sort=True).sort_index()
    sampled = sampled[~sampled.index.duplicated(keep="last")]
    return [
        [date.date().isoformat()]
        + [None if pd.isna(value) else round(float(value), digits) for value in row]
        for date, row in sampled.iterrows()
    ]


def _indexed_monthly_rows(series: Mapping[str, pd.Series], years: int = 5) -> list[list[str | float | None]]:
    """Mixed-cadence prices -> month-end, each indexed to 100 in-window."""

    monthly: dict[str, pd.Series] = {}
    for name, raw in series.items():
        values = _clean(raw)
        if values.empty:
            continue
        month = values.resample("ME").last().dropna()
        if not month.empty:
            monthly[name] = month
    if not monthly:
        return []
    frame = pd.concat(monthly, axis=1, sort=True).sort_index()
    end = frame.index.max()
    frame = frame[frame.index >= end - pd.DateOffset(years=years)]
    for column in frame:
        first = frame[column].dropna()
        if not first.empty and float(first.iloc[0]) != 0.0:
            frame[column] = frame[column] / float(first.iloc[0]) * 100.0
    return [
        [date.date().isoformat()]
        + [None if pd.isna(value) else round(float(value), 2) for value in row]
        for date, row in frame.iterrows()
    ]


def _rate_differential(local: pd.Series, effr: pd.Series) -> tuple[float | None, str | None]:
    rate = _clean(local)
    usd = _clean(effr)
    if rate.empty or usd.empty:
        return None, None
    asof = rate.index[-1]
    usd_known = usd[usd.index <= asof]
    if usd_known.empty:
        return None, None
    return (float(rate.iloc[-1]) - float(usd_known.iloc[-1])) * 100.0, asof.date().isoformat()


def _fx_metrics(
    key: str,
    spec: Mapping[str, object],
    effr: pd.Series,
) -> tuple[dict | None, pd.Series]:
    values = _normalise_fx(spec.get("series"), str(spec.get("quote", "local_per_usd")))  # type: ignore[arg-type]
    if len(values) < DAILY_MIN:
        return None, values
    returns = values.pct_change(fill_method=None).mul(100.0).dropna()
    dep20 = values.pct_change(20, fill_method=None).mul(100.0).dropna()
    vol20 = returns.rolling(20, min_periods=15).std(ddof=0).mul(math.sqrt(252.0)).dropna()
    dep_pctl = _midrank_last(dep20, window=SCORE_WINDOW_D, minimum=DAILY_MIN)
    vol_pctl = _midrank_last(vol20, window=SCORE_WINDOW_D, minimum=DAILY_MIN)
    pressure = _weighted(
        {"depreciation": dep_pctl, "volatility": vol_pctl},
        {"depreciation": 0.60, "volatility": 0.40},
    )
    last, asof = _last(values)
    ch20 = _change(values, 20)
    policy_diff, policy_asof = _rate_differential(
        spec.get("rate", pd.Series(dtype=float)), effr  # type: ignore[arg-type]
    )
    direction = "flat"
    if ch20 is not None and ch20 > 0.25:
        direction = "weaker vs USD"
    elif ch20 is not None and ch20 < -0.25:
        direction = "stronger vs USD"
    return (
        {
            "key": key,
            "label": str(spec.get("label", key)),
            "bucket": str(spec.get("bucket", "OTHER")),
            "last_local_per_usd": round(last, 5) if last is not None else None,
            "unit": f"{key} per USD",
            "change_5d_pct": round(_change(values, 5) or 0.0, 2) if _change(values, 5) is not None else None,
            "change_20d_pct": round(ch20, 2) if ch20 is not None else None,
            "change_60d_pct": round(_change(values, 60), 2) if _change(values, 60) is not None else None,
            "realized_vol_20d_pct": round(float(vol20.iloc[-1]), 2) if not vol20.empty else None,
            "depreciation_percentile": round(dep_pctl, 1) if dep_pctl is not None else None,
            "volatility_percentile": round(vol_pctl, 1) if vol_pctl is not None else None,
            "pressure": round(pressure, 1) if pressure is not None else None,
            "direction": direction,
            "policy_diff_vs_effr_bp": round(policy_diff, 1) if policy_diff is not None else None,
            "policy_rate_label": spec.get("rate_label"),
            "policy_rate_cadence": spec.get("rate_cadence"),
            "policy_asof": policy_asof,
            "asof": asof,
            "source_id": spec.get("source_id"),
        },
        values,
    )


def _commodity_metrics(key: str, spec: Mapping[str, object]) -> dict | None:
    values = _clean(spec.get("series"))  # type: ignore[arg-type]
    cadence = str(spec.get("cadence", "M"))
    daily = cadence == "D"
    minimum = DAILY_MIN if daily else MONTHLY_MIN
    if len(values) < minimum:
        return None
    horizon = 20 if daily else 3
    short = 5 if daily else 1
    vol_window = 20 if daily else 6
    annualizer = math.sqrt(252.0 if daily else 12.0)
    change_kind = str(spec.get("change_kind", "pct"))
    if change_kind == "diff":
        horizon_moves = values.diff(horizon).dropna()
        short_move = _change(values, short, percent=False)
        horizon_move = _change(values, horizon, percent=False)
        change_unit = str(spec.get("unit", ""))
    else:
        horizon_moves = values.pct_change(horizon, fill_method=None).mul(100.0).dropna()
        short_move = _change(values, short)
        horizon_move = _change(values, horizon)
        change_unit = "%"
    one_step = values.pct_change(fill_method=None).mul(100.0).replace([np.inf, -np.inf], np.nan).dropna()
    vol = one_step.rolling(vol_window, min_periods=max(4, vol_window // 2)).std(ddof=0).mul(annualizer).dropna()
    window = SCORE_WINDOW_D if daily else SCORE_WINDOW_M
    shock_pctl = _midrank_last(horizon_moves.abs(), window=window, minimum=minimum)
    vol_pctl = _midrank_last(vol, window=window, minimum=minimum)
    pressure = _weighted(
        {"shock": shock_pctl, "volatility": vol_pctl},
        {"shock": 0.65, "volatility": 0.35},
    )
    last, asof = _last(values)
    if horizon_move is None or abs(horizon_move) < 0.10:
        direction = "flat"
        channel = "no material cash impulse"
    elif horizon_move > 0:
        direction = "higher"
        channel = "working capital + inflation"
    else:
        direction = "lower"
        channel = "collateral + margin"
    return {
        "key": key,
        "label": str(spec.get("label", key)),
        "category": str(spec.get("category", "other")),
        "last": round(last, 4) if last is not None else None,
        "unit": spec.get("unit"),
        "cadence": "daily" if daily else "monthly",
        "short_change": round(short_move, 2) if short_move is not None else None,
        "horizon_change": round(horizon_move, 2) if horizon_move is not None else None,
        "horizon": "20 observations" if daily else "3 months",
        "change_unit": change_unit,
        "realized_vol_pct": round(float(vol.iloc[-1]), 2) if not vol.empty else None,
        "shock_percentile": round(shock_pctl, 1) if shock_pctl is not None else None,
        "volatility_percentile": round(vol_pctl, 1) if vol_pctl is not None else None,
        "pressure": round(pressure, 1) if pressure is not None else None,
        "direction": direction,
        "channel": channel,
        "asof": asof,
        "source_id": spec.get("source_id"),
    }


def _funding_metric(key: str, label: str, series: pd.Series) -> dict | None:
    values = _clean(series)
    if len(values) < DAILY_MIN:
        return None
    widening = values.diff(20).dropna()
    level_pctl = _midrank_last(values, window=SCORE_WINDOW_D, minimum=DAILY_MIN)
    widening_pctl = _midrank_last(widening, window=SCORE_WINDOW_D, minimum=DAILY_MIN)
    score = _weighted(
        {"level": level_pctl, "widening": widening_pctl},
        {"level": 0.65, "widening": 0.35},
    )
    last, asof = _last(values)
    return {
        "key": key,
        "label": label,
        "spread_bp": round(last, 2) if last is not None else None,
        "change_20d_bp": round(_change(values, 20, percent=False), 2)
        if _change(values, 20, percent=False) is not None
        else None,
        "level_percentile": round(level_pctl, 1) if level_pctl is not None else None,
        "widening_percentile": round(widening_pctl, 1) if widening_pctl is not None else None,
        "pressure": round(score, 1) if score is not None else None,
        "asof": asof,
    }


def _corr(pair: pd.DataFrame) -> float | None:
    if len(pair) < 30 or float(pair.iloc[:, 0].std()) <= 1e-12 or float(pair.iloc[:, 1].std()) <= 1e-12:
        return None
    value = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
    return value if np.isfinite(value) else None


def _passage_edge(
    source: str,
    x: pd.Series,
    targets: Mapping[str, pd.Series],
) -> dict | None:
    """Select target/lag on discovery; grade only on the untouched holdout."""

    candidates: list[dict] = []
    x = _clean(x)
    for target, raw_y in targets.items():
        y = _clean(raw_y)
        for lag in PASSAGE_LAGS:
            pair = pd.concat({"x": x, "y": y.shift(-lag)}, axis=1, sort=True).dropna()
            if len(pair) < DAILY_MIN * 2:
                continue
            split = int(len(pair) * PASSAGE_DISCOVERY_SHARE)
            discovery = pair.iloc[:split]
            holdout = pair.iloc[split:]
            r_discovery = _corr(discovery)
            if r_discovery is None:
                continue
            candidates.append(
                {
                    "target": target,
                    "lag_bd": lag,
                    "pair": pair,
                    "split": split,
                    "r_discovery": r_discovery,
                    "n_discovery": len(discovery),
                    "n_holdout": len(holdout),
                }
            )
    if not candidates:
        return None
    chosen = max(candidates, key=lambda item: abs(item["r_discovery"]))
    pair = chosen["pair"]
    holdout = pair.iloc[chosen["split"] :]
    r_holdout = _corr(holdout)
    r_full = _corr(pair)
    same_sign = (
        r_holdout is not None
        and r_full is not None
        and np.sign(chosen["r_discovery"]) == np.sign(r_holdout) == np.sign(r_full)
    )
    if same_sign and abs(r_holdout) >= PASSAGE_EARNED_R:
        status = "earned"
    elif same_sign and abs(r_holdout) >= PASSAGE_TENTATIVE_R:
        status = "tentative"
    else:
        status = "not_earned"
    return {
        "source": source,
        "target": chosen["target"],
        "lag_bd": int(chosen["lag_bd"]),
        "corr_discovery": round(float(chosen["r_discovery"]), 3),
        "corr_holdout": round(float(r_holdout), 3) if r_holdout is not None else None,
        "corr_full": round(float(r_full), 3) if r_full is not None else None,
        "n_discovery": int(chosen["n_discovery"]),
        "n_holdout": int(chosen["n_holdout"]),
        "status": status,
        "direction": (
            "wider funding" if status != "not_earned" and r_holdout is not None and r_holdout > 0
            else "narrower funding" if status != "not_earned" and r_holdout is not None
            else "unresolved"
        ),
        "search": f"target + lag chosen from discovery only; lags {list(PASSAGE_LAGS)}bd",
    }


def _forward_max(series: pd.Series, date: pd.Timestamp, horizon: int) -> float | None:
    values = _clean(series)
    if values.empty:
        return None
    pos = int(values.index.searchsorted(date, side="right")) - 1
    if pos < 0 or pos + 1 >= len(values):
        return None
    future = values.iloc[pos + 1 : pos + 1 + horizon]
    if future.empty:
        return None
    return float((future - float(values.iloc[pos])).max())


def _wilson(k: int, n: int) -> list[float] | None:
    if n <= 0:
        return None
    z = 1.96
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return [round(max(0.0, centre - half) * 100.0, 1), round(min(1.0, centre + half) * 100.0, 1)]


def _analogs(state: pd.DataFrame, funding: Mapping[str, pd.Series]) -> dict:
    if state.empty or len(state) < 300:
        return {"ok": False, "reason": "daily cross-market state needs at least 300 complete observations"}
    z = pd.DataFrame(index=state.index)
    for column in state:
        z[column] = _rolling_z(state[column])
    z = z.dropna()
    if len(z) < 160:
        return {"ok": False, "reason": "cross-market state has insufficient rolling-standardized history"}
    current_date = z.index[-1]
    current = z.iloc[-1].to_numpy(dtype=float)
    eligible = z.iloc[:-ANALOG_HORIZON]
    distances = np.sqrt(((eligible.to_numpy(dtype=float) - current) ** 2).mean(axis=1))
    ranked = sorted(zip(eligible.index, distances, strict=False), key=lambda item: item[1])
    selected: list[tuple[pd.Timestamp, float]] = []
    for date, distance in ranked:
        if (current_date - date).days < ANALOG_SEPARATION_D:
            continue
        if any(abs((date - prior).days) < ANALOG_SEPARATION_D for prior, _ in selected):
            continue
        selected.append((date, float(distance)))
        if len(selected) >= ANALOG_K:
            break

    def outcome(date: pd.Timestamp) -> tuple[dict[str, float | None], bool]:
        moves = {name: _forward_max(series, date, ANALOG_HORIZON) for name, series in funding.items()}
        live = [value for value in moves.values() if value is not None]
        return moves, bool(live and max(live) >= FUNDING_EVENT_BP)

    rows = []
    hits = 0
    for date, distance in selected:
        moves, event = outcome(date)
        hits += int(event)
        rows.append(
            {
                "date": date.date().isoformat(),
                "distance": round(distance, 3),
                "funding_event_10bd": event,
                "max_widening_10bd_bp": round(max((v for v in moves.values() if v is not None), default=0.0), 2),
                "by_market_bp": {key: round(value, 2) if value is not None else None for key, value in moves.items()},
            }
        )

    base_trials = 0
    base_hits = 0
    for date in eligible.index[::ANALOG_HORIZON]:
        _, event = outcome(date)
        base_trials += 1
        base_hits += int(event)
    event_rate = 100.0 * hits / len(rows) if rows else None
    base_rate = 100.0 * base_hits / base_trials if base_trials else None
    return {
        "ok": bool(rows),
        "asof": current_date.date().isoformat(),
        "k": len(rows),
        "horizon_bd": ANALOG_HORIZON,
        "event_threshold_bp": FUNDING_EVENT_BP,
        "event_rate_pct": round(event_rate, 1) if event_rate is not None else None,
        "event_rate_ci95_pct": _wilson(hits, len(rows)),
        "base_rate_pct": round(base_rate, 1) if base_rate is not None else None,
        "lift": round(event_rate / base_rate, 2) if event_rate is not None and base_rate else None,
        "base_trials": base_trials,
        "analogs": rows,
        "method": (
            "nearest prior daily states over broad/EM USD, pair depreciation/volatility, WTI and gas; "
            "each feature uses a trailing 3y z-score, candidates are 28 calendar days apart, and outcomes "
            "are the next 10 observed funding prints. Selection sees no forward funding outcome."
        ),
    }


def _scenario(assumptions: Mapping[str, float]) -> dict:
    fx_gross = assumptions["daily_fx_obligations_usd_b"] * 1e9
    fx_unmitigated = fx_gross * assumptions["gross_bilateral_share_pct"] / 100.0
    fx_replacement = fx_unmitigated * abs(assumptions["adverse_fx_move_pct"]) / 100.0
    inventory = assumptions["commodity_inventory_usd_b"] * 1e9
    price_move = abs(assumptions["commodity_price_move_pct"]) / 100.0
    hedge = assumptions["commodity_hedge_ratio_pct"] / 100.0
    inventory_cash = inventory * price_move
    hedge_margin = inventory * hedge * price_move
    haircut_cash = inventory * assumptions["haircut_increase_pct"] / 100.0
    receivable_carry = (
        inventory
        * assumptions["funding_rate_pct"]
        / 100.0
        * assumptions["receivable_days"]
        / 365.0
    )
    return {
        "fx": {
            "gross_obligations_usd": round(fx_gross, 2),
            "principal_without_pvp_usd": round(fx_unmitigated, 2),
            "replacement_cost_shock_usd": round(fx_replacement, 2),
        },
        "materials": {
            "inventory_value_change_usd": round(inventory_cash, 2),
            "hedge_margin_call_usd": round(hedge_margin, 2),
            "haircut_cash_call_usd": round(haircut_cash, 2),
            "receivable_carry_usd": round(receivable_carry, 2),
            "same_day_margin_and_haircut_usd": round(hedge_margin + haircut_cash, 2),
        },
    }


def analyze(
    *,
    fx: Mapping[str, Mapping[str, object]],
    broad_dollar: pd.Series,
    afe_dollar: pd.Series,
    eme_dollar: pd.Series,
    commodities: Mapping[str, Mapping[str, object]],
    sofr: pd.Series,
    iorb: pd.Series,
    effr: pd.Series,
    cp_nonfinancial_3m: pd.Series,
    cp_financial_3m: pd.Series,
    treasury_3m: pd.Series,
    swap_lines_m: pd.Series,
    foreign_rrp_m: pd.Series,
    fima_repo_m: pd.Series,
    offshore_usd_credit_m: pd.Series,
    assumptions: Mapping[str, float] | None = None,
) -> dict:
    broad_dollar = _clean(broad_dollar)
    afe_dollar = _clean(afe_dollar)
    eme_dollar = _clean(eme_dollar)
    effr = _clean(effr)
    if len(broad_dollar) < DAILY_MIN:
        return {"ok": False, "reason": f"insufficient broad-dollar history ({len(broad_dollar)} observations)"}

    fx_rows: list[dict] = []
    fx_series: dict[str, pd.Series] = {}
    for key, spec in fx.items():
        row, values = _fx_metrics(key, spec, effr)
        if row is not None:
            fx_rows.append(row)
            fx_series[key] = values
    if len(fx_rows) < 4:
        return {"ok": False, "reason": f"only {len(fx_rows)} qualifying H.10 currency histories"}

    broad_ret20 = broad_dollar.pct_change(20, fill_method=None).mul(100.0).dropna()
    afe_ret20 = afe_dollar.pct_change(20, fill_method=None).mul(100.0).dropna()
    eme_ret20 = eme_dollar.pct_change(20, fill_method=None).mul(100.0).dropna()
    broad_pctl = _midrank_last(broad_ret20, window=SCORE_WINDOW_D, minimum=DAILY_MIN)
    afe_pctl = _midrank_last(afe_ret20, window=SCORE_WINDOW_D, minimum=DAILY_MIN)
    eme_pctl = _midrank_last(eme_ret20, window=SCORE_WINDOW_D, minimum=DAILY_MIN)
    pair_dep = _median([row.get("depreciation_percentile") for row in fx_rows])
    pair_vol = _median([row.get("volatility_percentile") for row in fx_rows])
    fx_score = _weighted(
        {
            "broad_dollar": broad_pctl,
            "eme_dollar": eme_pctl,
            "pair_depreciation": pair_dep,
            "pair_volatility": pair_vol,
        },
        ESTUARY_FX_WEIGHTS,
    )

    commodity_rows = [
        row for key, spec in commodities.items() if (row := _commodity_metrics(key, spec)) is not None
    ]
    if len(commodity_rows) < 4:
        return {"ok": False, "reason": f"only {len(commodity_rows)} qualifying commodity histories"}
    category_rows = []
    category_scores: dict[str, float | None] = {}
    for category in ESTUARY_MATERIAL_WEIGHTS:
        members = [row for row in commodity_rows if row["category"] == category]
        value = _median([row.get("pressure") for row in members])
        category_scores[category] = value
        category_rows.append(
            {
                "category": category,
                "pressure": round(value, 1) if value is not None else None,
                "n": len(members),
                "leaders": [row["key"] for row in sorted(members, key=lambda item: item.get("pressure") or -1, reverse=True)[:2]],
            }
        )
    materials_score = _weighted(category_scores, ESTUARY_MATERIAL_WEIGHTS)

    sofr_iorb = _spread_bp(sofr, iorb)
    cp_nonfinancial = _spread_bp(cp_nonfinancial_3m, treasury_3m)
    cp_financial = _spread_bp(cp_financial_3m, treasury_3m)
    funding_series = {
        "SOFR−IORB": sofr_iorb,
        "Nonfinancial CP−bill": cp_nonfinancial,
        "Financial CP−bill": cp_financial,
    }
    funding_rows = [
        row
        for key, label, series in (
            ("sofr_iorb", "SOFR − IORB", sofr_iorb),
            ("cp_nonfinancial", "3m nonfinancial CP − Treasury", cp_nonfinancial),
            ("cp_financial", "3m financial CP − Treasury", cp_financial),
        )
        if (row := _funding_metric(key, label, series)) is not None
    ]
    if not funding_rows:
        return {"ok": False, "reason": "no qualifying downstream funding-spread history"}
    funding_score = _median([row.get("pressure") for row in funding_rows])
    upstream_score = _weighted(
        {"fx": fx_score, "materials": materials_score}, ESTUARY_UPSTREAM_WEIGHTS
    )
    gap = upstream_score - funding_score if upstream_score is not None and funding_score is not None else None

    if gap is None:
        regime = "UNRESOLVED"
    elif upstream_score is not None and upstream_score < 40.0 and funding_score is not None and funding_score < 40.0:
        regime = "QUIET CONFLUENCE"
    elif gap >= ESTUARY_GAP_OPEN:
        regime = "PRESSURE HELD UPSTREAM"
    elif gap >= ESTUARY_GAP_BUILDING:
        regime = "TRANSMISSION BUILDING"
    elif gap <= -ESTUARY_GAP_OPEN:
        regime = "FUNDING LEADS"
    else:
        regime = "IN SYNC"

    fx_leaders = sorted(fx_rows, key=lambda row: row.get("pressure") or -1, reverse=True)
    material_leaders = sorted(commodity_rows, key=lambda row: row.get("pressure") or -1, reverse=True)
    funding_leaders = sorted(funding_rows, key=lambda row: row.get("pressure") or -1, reverse=True)
    leader_fx = fx_leaders[0]
    leader_material = material_leaders[0]
    leader_funding = funding_leaders[0]
    if regime == "PRESSURE HELD UPSTREAM":
        verdict = (
            f"{leader_fx['label']} and {leader_material['label']} carry the strongest upstream cash pressure, "
            f"while {leader_funding['label']} remains materially less repriced. The gap is open; transmission is a risk, not a forecast."
        )
    elif regime == "TRANSMISSION BUILDING":
        verdict = (
            f"Upstream pressure is running ahead of cash markets by {gap:.0f} points. "
            f"Watch {leader_funding['label']} for confirmation rather than treating the cross-market move as funding stress already."
        )
    elif regime == "FUNDING LEADS":
        verdict = (
            f"Funding is tighter than the FX/material tape explains. {leader_funding['label']} is leading; "
            "the current squeeze is more likely inside the financial plumbing than imported from trade flows."
        )
    else:
        verdict = (
            f"FX/material pressure and funding pricing are broadly aligned. The loudest upstream row is "
            f"{leader_fx['label']}; the loudest physical row is {leader_material['label']}."
        )

    # Daily state: monthly commodity observations never enter this block.
    pair_frame = pd.concat(fx_series, axis=1, sort=True).sort_index().ffill(limit=3)
    pair_dep20_series = pair_frame.pct_change(20, fill_method=None).mul(100.0).median(axis=1).dropna()
    pair_vol_series = (
        pair_frame.pct_change(fill_method=None).mul(100.0).rolling(20, min_periods=15).std(ddof=0).median(axis=1).dropna()
    )
    daily_commodities = {
        key: _clean(spec.get("series"))  # type: ignore[arg-type]
        for key, spec in commodities.items()
        if str(spec.get("cadence", "M")) == "D"
    }
    wti = daily_commodities.get("WTI", pd.Series(dtype=float))
    brent = daily_commodities.get("BRENT", pd.Series(dtype=float))
    natgas = daily_commodities.get("NATGAS", pd.Series(dtype=float))
    state = pd.concat(
        {
            "broad_usd_20d": broad_ret20,
            "eme_usd_20d": eme_ret20,
            "pair_depreciation_20d": pair_dep20_series,
            "pair_volatility_20d": pair_vol_series,
            "wti_20d_dollar": wti.diff(20).abs(),
            "natgas_20d_abs_pct": natgas.pct_change(20, fill_method=None).mul(100.0).abs(),
        },
        axis=1,
        sort=True,
    ).dropna()

    # Discovery/holdout Passage.  The source moves use five-observation
    # changes; target moves are future five-observation spread changes.
    em_keys = [key for key, spec in fx.items() if str(spec.get("bucket")) == "EM" and key in fx_series]
    em_frame = pair_frame[em_keys] if em_keys else pd.DataFrame()
    source_moves: dict[str, pd.Series] = {
        "Broad USD": broad_dollar.pct_change(5, fill_method=None).mul(100.0),
        "EM dollar": eme_dollar.pct_change(5, fill_method=None).mul(100.0),
        "EM FX breadth": em_frame.pct_change(5, fill_method=None).mul(100.0).median(axis=1)
        if not em_frame.empty
        else pd.Series(dtype=float),
        "JPY": fx_series.get("JPY", pd.Series(dtype=float)).pct_change(5, fill_method=None).mul(100.0),
        "WTI": wti.diff(5),
        "Natural gas": natgas.pct_change(5, fill_method=None).mul(100.0),
    }
    target_moves = {name: values.diff(5) for name, values in funding_series.items()}
    passage_edges = [
        edge for name, source in source_moves.items() if (edge := _passage_edge(name, source, target_moves)) is not None
    ]
    passage_edges.sort(
        key=lambda edge: (
            {"earned": 2, "tentative": 1, "not_earned": 0}[edge["status"]],
            abs(edge.get("corr_holdout") or 0.0),
        ),
        reverse=True,
    )

    # A daily-only history for the chart.  It is deliberately labelled a
    # proxy because industrial/agricultural IMF rows are monthly.
    fx_daily_scores = pd.concat(
        {
            "broad": _z_score(_rolling_z(broad_ret20)),
            "eme": _z_score(_rolling_z(eme_ret20)),
            "depreciation": _z_score(_rolling_z(pair_dep20_series)),
            "volatility": _z_score(_rolling_z(pair_vol_series)),
        },
        axis=1,
        sort=True,
    ).mean(axis=1, skipna=True)
    material_daily_scores = pd.concat(
        {
            "wti": _z_score(_rolling_z(wti.diff(20).abs())),
            "brent": _z_score(_rolling_z(brent.diff(20).abs())),
            "gas": _z_score(_rolling_z(natgas.pct_change(20, fill_method=None).mul(100.0).abs())),
        },
        axis=1,
        sort=True,
    ).mean(axis=1, skipna=True)
    upstream_daily = pd.concat(
        {"fx": fx_daily_scores, "energy": material_daily_scores}, axis=1, sort=True
    ).mean(axis=1, skipna=True)
    funding_score_series = []
    for values in funding_series.values():
        level = _z_score(_rolling_z(values))
        widening = _z_score(_rolling_z(values.diff(20)))
        funding_score_series.append(pd.concat({"l": level, "w": widening}, axis=1).mean(axis=1))
    downstream_daily = pd.concat(funding_score_series, axis=1, sort=True).median(axis=1)
    daily_gap = upstream_daily - downstream_daily

    analogs = _analogs(state, funding_series)

    scenario_assumptions = dict(ESTUARY_SCENARIO_DEFAULTS)
    sofr_last, _ = _last(_clean(sofr))
    if sofr_last is not None:
        scenario_assumptions["funding_rate_pct"] = sofr_last
    if assumptions:
        for key, value in assumptions.items():
            if key in scenario_assumptions and np.isfinite(float(value)):
                scenario_assumptions[key] = float(value)

    all_asof = [
        value
        for value in (
            [row.get("asof") for row in fx_rows]
            + [row.get("asof") for row in commodity_rows]
            + [row.get("asof") for row in funding_rows]
        )
        if value
    ]
    expected = len(fx) + len(commodities) + 3
    present = len(fx_rows) + len(commodity_rows) + len(funding_rows)
    coverage = 100.0 * present / expected if expected else 0.0

    swap_last, swap_asof = _last(swap_lines_m)
    rrp_last, rrp_asof = _last(foreign_rrp_m)
    fima_last, fima_asof = _last(fima_repo_m)
    offshore_last, offshore_asof = _last(offshore_usd_credit_m)

    return {
        "ok": True,
        "asof": max(all_asof) if all_asof else None,
        "headline": {
            "upstream_pressure": round(upstream_score, 1) if upstream_score is not None else None,
            "fx_pressure": round(fx_score, 1) if fx_score is not None else None,
            "materials_pressure": round(materials_score, 1) if materials_score is not None else None,
            "funding_priced": round(funding_score, 1) if funding_score is not None else None,
            "transmission_gap": round(gap, 1) if gap is not None else None,
            "regime": regime,
            "verdict": verdict,
            "coverage_pct": round(coverage, 1),
            "present_series": present,
            "expected_series": expected,
            "context_only": True,
        },
        "fx": {
            "broad": {
                "index": round(float(broad_dollar.iloc[-1]), 3),
                "change_20d_pct": round(_change(broad_dollar, 20), 2) if _change(broad_dollar, 20) is not None else None,
                "pressure_percentile": round(broad_pctl, 1) if broad_pctl is not None else None,
                "asof": broad_dollar.index[-1].date().isoformat(),
            },
            "advanced": {
                "change_20d_pct": round(_change(afe_dollar, 20), 2) if _change(afe_dollar, 20) is not None else None,
                "pressure_percentile": round(afe_pctl, 1) if afe_pctl is not None else None,
                "asof": afe_dollar.index[-1].date().isoformat() if not afe_dollar.empty else None,
            },
            "emerging": {
                "change_20d_pct": round(_change(eme_dollar, 20), 2) if _change(eme_dollar, 20) is not None else None,
                "pressure_percentile": round(eme_pctl, 1) if eme_pctl is not None else None,
                "asof": eme_dollar.index[-1].date().isoformat() if not eme_dollar.empty else None,
            },
            "median_pair_depreciation_percentile": round(pair_dep, 1) if pair_dep is not None else None,
            "median_pair_volatility_percentile": round(pair_vol, 1) if pair_vol is not None else None,
            "currencies": fx_leaders,
        },
        "materials": {
            "categories": category_rows,
            "instruments": material_leaders,
            "breadth_higher_pct": round(
                100.0 * sum(row["direction"] == "higher" for row in commodity_rows) / len(commodity_rows), 1
            ),
        },
        "funding": {"markets": funding_leaders},
        "passage": {
            "edges": passage_edges,
            "earned": sum(edge["status"] == "earned" for edge in passage_edges),
            "tentative": sum(edge["status"] == "tentative" for edge in passage_edges),
            "not_earned": sum(edge["status"] == "not_earned" for edge in passage_edges),
            "doctrine": (
                "target and lag are selected on the first 60% of each aligned history; earned requires the same sign "
                f"and |r| ≥ {PASSAGE_EARNED_R:.2f} in the untouched final 40%. Correlation is not causation."
            ),
        },
        "analogs": analogs,
        "dollar_system": {
            "swap_lines": {
                "outstanding_usd_m": round(swap_last, 1) if swap_last is not None else None,
                "change_13w_usd_m": round(_change(swap_lines_m, 13, percent=False), 1)
                if _change(swap_lines_m, 13, percent=False) is not None
                else None,
                "asof": swap_asof,
            },
            "foreign_official_rrp": {
                "outstanding_usd_b": round(rrp_last / 1000.0, 2) if rrp_last is not None else None,
                "change_13w_usd_b": round((_change(foreign_rrp_m, 13, percent=False) or 0.0) / 1000.0, 2)
                if _change(foreign_rrp_m, 13, percent=False) is not None
                else None,
                "asof": rrp_asof,
            },
            "fima_repo": {
                "outstanding_usd_m": round(fima_last, 1) if fima_last is not None else None,
                "asof": fima_asof,
            },
            "offshore_dollar_credit": {
                "outstanding_usd_t": round(offshore_last / 1e6, 2) if offshore_last is not None else None,
                "asof": offshore_asof,
                "cadence": "quarterly, published with a long native lag",
            },
        },
        "settlement_structure": dict(ESTUARY_BIS_FX_STRUCTURE),
        "scenario": {
            "assumptions": scenario_assumptions,
            "outputs": _scenario(scenario_assumptions),
        },
        "charts": {
            "dollar": {
                "rows": _adaptive_rows(
                    {"BROAD": broad_dollar, "AFE": afe_dollar, "EME": eme_dollar}, years=3
                ),
                "labels": ["broad USD", "advanced-economy USD", "emerging-market USD"],
                "unit": "Jan 2006 = 100",
            },
            "materials": {
                "rows": _indexed_monthly_rows(
                    {
                        "WTI": wti,
                        "NATGAS": natgas,
                        "COPPER": _clean(commodities.get("COPPER", {}).get("series")),  # type: ignore[arg-type]
                        "ALL": _clean(commodities.get("ALL", {}).get("series")),  # type: ignore[arg-type]
                    }
                ),
                "labels": ["WTI", "Henry Hub", "copper", "all commodities"],
                "unit": "each indexed to 100 at first in-window observation",
            },
            "funding": {
                "rows": _adaptive_rows(funding_series),
                "labels": list(funding_series),
                "unit": "basis points",
            },
            "daily_gap": {
                "rows": _adaptive_rows(
                    {"UPSTREAM": upstream_daily, "FUNDING": downstream_daily, "GAP": daily_gap},
                    years=3,
                ),
                "labels": ["daily upstream proxy", "funding priced", "daily passage gap"],
                "unit": "0–100 pressure / point gap",
                "note": "Daily H.10 + EIA proxy only; monthly IMF materials are excluded from this history.",
            },
        },
        "sources": [
            {"layer": "FX spot + dollar indexes", "source": "Federal Reserve H.10 via FRED", "cadence": "daily"},
            {"layer": "crude + natural gas", "source": "EIA via FRED", "cadence": "daily"},
            {"layer": "broad / metals / coal / grains", "source": "IMF Primary Commodity Prices via FRED", "cadence": "monthly"},
            {"layer": "SOFR / IORB / EFFR / CP / Treasury", "source": "NY Fed + Federal Reserve via FRED", "cadence": "daily"},
            {"layer": "swap lines / FIMA / foreign RRP", "source": "Federal Reserve H.4.1 via FRED", "cadence": "weekly"},
            {"layer": "offshore USD credit", "source": "BIS Data Portal", "cadence": "quarterly, lagged"},
            {"layer": "FX settlement structure", "source": ESTUARY_BIS_FX_STRUCTURE["source"], "cadence": "triennial structural benchmark"},
        ],
        "coverage_matrix": [
            {"aspect": "spot direction", "fx": "daily · observed", "materials": "daily energy / monthly breadth", "status": "covered"},
            {"aspect": "realized volatility", "fx": "20d · observed", "materials": "cadence-matched · observed", "status": "covered"},
            {"aspect": "policy carry", "fx": "local anchor − EFFR where available", "materials": "funding carry in Oil × Funding", "status": "partial"},
            {"aspect": "working capital", "fx": "settlement scenario", "materials": "inventory / receivable scenario", "status": "scenario"},
            {"aspect": "margin + collateral", "fx": "replacement-cost scenario", "materials": "hedge / haircut scenario", "status": "scenario"},
            {"aspect": "official backstops", "fx": "swap lines + FIMA + foreign RRP", "materials": "oil recycling proxy on Oil × Funding", "status": "covered"},
            {"aspect": "cross-currency basis / forwards", "fx": "no qualifying free live feed", "materials": "current futures curves unavailable", "status": "out_of_scope"},
            {"aspect": "precious-metal spot", "fx": "—", "materials": "old public LBMA/FRED series ended in 2022", "status": "out_of_scope"},
        ],
        "caveats": [
            "The Estuary gap is a transparent context score, not a probability, forecast, trade signal, or Seiche composite input.",
            "H.10 pairs are normalized to local currency per USD; positive change always means local-currency depreciation. H.10 fixes are reference rates, not executable dealer quotes.",
            "Monthly IMF prices are period averages with publication lag. They inform category breadth only and never enter the daily Passage or analog state.",
            "Commodity pressure is deliberately two-sided: higher prices can consume working capital; lower prices can impair collateral and trigger margin. The score identifies an unusually large move, not its net macro effect.",
            "The Passage is a split-sample correlation audit. A stable lead/lag association can still be driven by a third variable; 'earned' never means causal.",
            "Analog event rates use a small, de-clustered neighbor set; the Wilson interval and unconditional base rate must travel with the point estimate.",
            "Policy-rate differentials are not forward points, hedged carry, or cross-currency basis. Those require licensed live curves that Seiche does not have.",
            "BIS settlement figures are an April 2025 structural survey, not today's flow. Scenario outputs are identities under editable assumptions, not measured exposures.",
        ],
        "method": (
            "FX pressure = weighted current percentiles of 20-observation broad/EM dollar changes, median pair depreciation, and 20d realized volatility; "
            "materials pressure = category-weighted median of absolute cadence-matched move and realized-volatility percentiles; funding priced = median of spread-level and 20d-widening percentiles for SOFR−IORB and 3m CP−Treasury. "
            "Transmission gap = 55% FX + 45% materials upstream pressure minus funding priced. All percentiles are against each series' own trailing history with midrank ties. Context only, never composite."
        ),
    }
