"""Oil × Funding — the physical barrel read through money-market plumbing.

This is a context engine, never a Seiche composite component.  It joins public
spot-oil benchmarks to unsecured and secured dollar-funding series, publishes
trailing (not causal) coupling diagnostics, and exposes transparent scenario
arithmetic for carry, cargo finance, margin calls, and India's INR/OMC chain.

The current futures strip is intentionally NOT inferred.  EIA's public NYMEX
contract table stops in April 2024, so a forward spread remains an explicit
scenario input instead of masquerading as live market data.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from seiche.config import OIL_FUNDING_SCENARIO_DEFAULTS


MIN_HISTORY = 60
CORR_WINDOW = 63
CORR_MIN = 40
CHART_YEARS = 5
CHART_RECENT_DAYS = 180

# Last official EIA capacity observation (March 31, 2024). EIA discontinued
# the capacity report after this release, so these are reference denominators,
# never labelled current capacity in the payload or interface.
CUSHING_WORKING_CAPACITY_MBBL = 78.410
CUSHING_SHELL_CAPACITY_MBBL = 97.742
CUSHING_CAPACITY_ASOF = "2024-03-31"
CUSHING_STRESS_REFERENCE_MBBL = 20.0

# EIA Global Energy Security Data, released 2026-05-13. These are observed
# quarterly flow estimates, not a live vessel counter. The distinction is
# important because EIA says Hormuz AIS data became especially unreliable
# after February 2026.
CHOKEPOINT_FLOWS = [
    {"name": "Strait of Malacca", "q4_2025_mbd": 24.0, "q1_2026_mbd": 20.9, "kind": "chokepoint"},
    {"name": "Strait of Hormuz", "q4_2025_mbd": 20.7, "q1_2026_mbd": 14.6, "kind": "chokepoint"},
    {"name": "Cape of Good Hope", "q4_2025_mbd": 9.6, "q1_2026_mbd": 8.0, "kind": "alternate route"},
    {"name": "Bab el-Mandeb", "q4_2025_mbd": 5.2, "q1_2026_mbd": 5.4, "kind": "chokepoint"},
    {"name": "Suez + SUMED", "q4_2025_mbd": 5.1, "q1_2026_mbd": 4.9, "kind": "route + pipeline"},
    {"name": "Danish Straits", "q4_2025_mbd": 4.9, "q1_2026_mbd": 4.8, "kind": "chokepoint"},
    {"name": "Turkish Straits", "q4_2025_mbd": 3.6, "q1_2026_mbd": 3.3, "kind": "chokepoint"},
    {"name": "Panama Canal", "q4_2025_mbd": 2.8, "q1_2026_mbd": 2.9, "kind": "canal"},
]


def _clean(series: pd.Series | None) -> pd.Series:
    if series is None or not isinstance(series, pd.Series):
        return pd.Series(dtype=float)
    out = pd.to_numeric(series, errors="coerce").dropna()
    if out.empty or not isinstance(out.index, pd.DatetimeIndex):
        return pd.Series(dtype=float)
    out = out[~out.index.isna()]
    out = out[np.isfinite(out.to_numpy(dtype=float))].sort_index()
    return out[~out.index.duplicated(keep="last")].astype(float)


def _spread_bp(rate: pd.Series, bill: pd.Series) -> pd.Series:
    """Rate minus bill on actual rate-print dates; bill may carry <=5 rows."""

    left, right = _clean(rate), _clean(bill)
    if left.empty or right.empty:
        return pd.Series(dtype=float)
    grid = left.index.union(right.index)
    aligned = pd.concat(
        {"rate": left.reindex(grid), "bill": right.reindex(grid)}, axis=1, sort=True
    ).sort_index()
    aligned["bill"] = aligned["bill"].ffill(limit=5)
    on_print = aligned.reindex(left.index).dropna()
    return ((on_print["rate"] - on_print["bill"]) * 100.0).dropna()


def _rolling_corr(left: pd.Series, right: pd.Series) -> pd.Series:
    pair = pd.concat(
        {"left": _clean(left), "right": _clean(right)}, axis=1, sort=True
    ).dropna()
    if len(pair) < CORR_MIN:
        return pd.Series(dtype=float)
    return (
        pair["left"]
        .rolling(CORR_WINDOW, min_periods=CORR_MIN)
        .corr(pair["right"])
        .dropna()
    )


def _pctl_3y(series: pd.Series) -> float | None:
    values = _clean(series).tail(756)
    if len(values) < MIN_HISTORY:
        return None
    return round(float((values <= values.iloc[-1]).mean() * 100.0), 1)


def _change(series: pd.Series, periods: int, *, percent: bool = False) -> float | None:
    values = _clean(series)
    if len(values) <= periods:
        return None
    current, prior = float(values.iloc[-1]), float(values.iloc[-(periods + 1)])
    if percent:
        if prior == 0.0:
            return None
        return round((current / prior - 1.0) * 100.0, 2)
    return round(current - prior, 3)


def _latest(series: pd.Series) -> tuple[float | None, str | None]:
    values = _clean(series)
    if values.empty:
        return None, None
    return round(float(values.iloc[-1]), 4), values.index[-1].date().isoformat()


def _monthly_last(series: pd.Series) -> pd.Series:
    values = _clean(series)
    if values.empty:
        return values
    # Preserve the date of the actual final observation in each month. A
    # month-end resample label can otherwise put an August 10 policy print at
    # August 31, which looks like future data in the chart.
    return values.groupby(values.index.to_period("M")).tail(1)


def _monthly_yoy(series: pd.Series) -> pd.Series:
    monthly = _monthly_last(series)
    if monthly.empty:
        return monthly
    return monthly.pct_change(12, fill_method=None).mul(100.0).dropna()


def _official_52w_change_b(series_m: pd.Series) -> pd.Series:
    """Weekly foreign-official balance in $M → 52-week change in $B."""

    values = _clean(series_m)
    if values.empty:
        return values
    weekly = values.resample("W-FRI").last().dropna()
    return weekly.diff(52).div(1000.0).dropna()


def _adaptive_rows(
    series: Mapping[str, pd.Series], *, digits: int = 3
) -> list[list[str | float | None]]:
    """Weekly history plus daily recent data, retaining real observation dates."""

    live: dict[str, pd.Series] = {}
    for name, values in series.items():
        cleaned = _clean(values)
        if not cleaned.empty:
            live[name] = cleaned
    if not live:
        return []
    frame = pd.concat(live, axis=1, sort=True).sort_index()
    end = frame.index.max()
    frame = frame[frame.index >= end - pd.DateOffset(years=CHART_YEARS)]
    recent_cut = end - pd.Timedelta(days=CHART_RECENT_DAYS)
    older = frame[frame.index < recent_cut]
    recent = frame[frame.index >= recent_cut]
    if not older.empty:
        older = older.groupby(pd.Grouper(freq="W-FRI")).tail(1)
    sampled = pd.concat([older, recent], sort=True).sort_index()
    sampled = sampled[~sampled.index.duplicated(keep="last")]
    rows: list[list[str | float | None]] = []
    for date, row in sampled.iterrows():
        rows.append(
            [date.date().isoformat()]
            + [None if pd.isna(value) else round(float(value), digits) for value in row]
        )
    return rows


def _scenario(
    *,
    oil_price_usd_per_bbl: float,
    funding_rate_pct: float | None,
    usd_inr: float | None,
    assumptions: Mapping[str, float],
) -> dict:
    tenor = assumptions["tenor_days"]
    year_fraction = tenor / 365.0
    insurance_rate = assumptions["insurance_rate_pct"] / 100.0
    funding_rate = (
        funding_rate_pct / 100.0 if funding_rate_pct is not None else None
    )
    storage_cost = assumptions["storage_usd_per_bbl_day"] * tenor
    financing_cost = (
        oil_price_usd_per_bbl * funding_rate * year_fraction
        if funding_rate is not None
        else None
    )
    insurance_cost = oil_price_usd_per_bbl * insurance_rate * year_fraction
    required_contango = (
        storage_cost + financing_cost + insurance_cost
        if financing_cost is not None
        else None
    )
    carry_headroom = (
        assumptions["forward_spread_usd_per_bbl"] - required_contango
        if required_contango is not None
        else None
    )

    cargo_barrels = assumptions["barrels_per_cargo_m"] * 1_000_000.0
    daily_flow = assumptions["daily_throughput_mbd"] * 1_000_000.0
    voyage_days = assumptions["voyage_days"]
    baseline_days = assumptions["baseline_voyage_days"]
    cargo_credit = oil_price_usd_per_bbl * cargo_barrels
    in_transit = oil_price_usd_per_bbl * daily_flow * voyage_days
    baseline_in_transit = oil_price_usd_per_bbl * daily_flow * baseline_days

    hedge_barrels = assumptions["net_short_hedge_m_bbl"] * 1_000_000.0
    price_change = assumptions["oil_price_change_usd_per_bbl"]
    variation_call = max(0.0, hedge_barrels * price_change)
    initial_margin_call = (
        abs(hedge_barrels)
        * oil_price_usd_per_bbl
        * assumptions["initial_margin_rate_change_pct"]
        / 100.0
    )

    annual_import_bill_usd = (
        assumptions["india_import_mbd"]
        * 1_000_000.0
        * assumptions["india_oil_shock_usd_per_bbl"]
        * 365.0
    )
    intervention_inr = (
        assumptions["rbi_usd_sales_b"] * 1_000_000_000.0 * usd_inr
        if usd_inr is not None
        else None
    )
    unreplenished_inr = (
        intervention_inr
        * (1.0 - assumptions["liquidity_replenishment_pct"] / 100.0)
        if intervention_inr is not None
        else None
    )
    omc_stock_inr = (
        assumptions["under_recovery_inr_crore_day"]
        * 10_000_000.0
        * assumptions["compensation_lag_days"]
    )

    def rounded(value: float | None, digits: int = 2) -> float | None:
        return None if value is None else round(float(value), digits)

    return {
        "carry": {
            "storage_cost_usd_per_bbl": rounded(storage_cost, 3),
            "financing_cost_usd_per_bbl": rounded(financing_cost, 3),
            "insurance_cost_usd_per_bbl": rounded(insurance_cost, 3),
            "required_contango_usd_per_bbl": rounded(required_contango, 3),
            "mechanical_headroom_usd_per_bbl": rounded(carry_headroom, 3),
        },
        "trade_finance": {
            "cargo_credit_usd": rounded(cargo_credit),
            "cargo_financing_cost_usd": rounded(
                cargo_credit * funding_rate * voyage_days / 365.0
                if funding_rate is not None
                else None
            ),
            "in_transit_working_capital_usd": rounded(in_transit),
            "incremental_voyage_working_capital_usd": rounded(
                in_transit - baseline_in_transit
            ),
            "voyage_working_capital_multiple": rounded(
                voyage_days / baseline_days, 3
            )
            if baseline_days > 0
            else None,
        },
        "margin": {
            "variation_margin_call_usd": rounded(variation_call),
            "initial_margin_call_usd": rounded(initial_margin_call),
            "same_day_liquidity_demand_usd": rounded(
                variation_call + initial_margin_call
            ),
        },
        "india": {
            "annual_import_bill_change_usd": rounded(annual_import_bill_usd),
            "annual_import_bill_change_inr": rounded(
                annual_import_bill_usd * usd_inr if usd_inr is not None else None
            ),
            "rbi_gross_liquidity_absorption_inr": rounded(intervention_inr),
            "rbi_unreplenished_liquidity_absorption_inr": rounded(
                unreplenished_inr
            ),
            "omc_under_recovery_funding_stock_inr": rounded(omc_stock_inr),
            "omc_cp_funding_demand_inr": rounded(
                omc_stock_inr * assumptions["cp_funding_share_pct"] / 100.0
            ),
        },
    }


def analyze(
    *,
    wti: pd.Series,
    brent: pd.Series,
    sofr: pd.Series,
    iorb: pd.Series,
    cp_nonfinancial_3m: pd.Series,
    cp_financial_3m: pd.Series,
    treasury_3m: pd.Series,
    inr_per_usd: pd.Series,
    energy_cpi: pd.Series,
    core_cpi: pd.Series,
    foreign_treasury_custody: pd.Series,
    foreign_official_rrp: pd.Series,
    cushing_stocks: pd.Series,
    assumptions: Mapping[str, float] | None = None,
) -> dict:
    wti = _clean(wti)
    brent = _clean(brent)
    sofr = _clean(sofr)
    iorb = _clean(iorb)
    cp_nonfinancial_3m = _clean(cp_nonfinancial_3m)
    cp_financial_3m = _clean(cp_financial_3m)
    treasury_3m = _clean(treasury_3m)
    inr_per_usd = _clean(inr_per_usd)
    energy_cpi = _clean(energy_cpi)
    core_cpi = _clean(core_cpi)
    foreign_treasury_custody = _clean(foreign_treasury_custody)
    foreign_official_rrp = _clean(foreign_official_rrp)
    # EIA publishes Cushing stocks in thousand barrels; every public market-
    # structure field uses million barrels so capacity arithmetic is explicit.
    cushing_m_bbl = _clean(cushing_stocks).div(1000.0)

    if len(wti) < MIN_HISTORY:
        return {"ok": False, "reason": f"insufficient WTI history ({len(wti)} observations)"}

    cp_nonfinancial = _spread_bp(cp_nonfinancial_3m, treasury_3m)
    cp_financial = _spread_bp(cp_financial_3m, treasury_3m)
    sofr_iorb = _spread_bp(sofr, iorb)
    if max(len(cp_nonfinancial), len(cp_financial), len(sofr_iorb)) < MIN_HISTORY:
        return {"ok": False, "reason": "insufficient overlap with a dollar-funding spread"}

    wti_last, wti_asof = _latest(wti)
    brent_last, brent_asof = _latest(brent)
    sofr_last, sofr_asof = _latest(sofr)
    inr_last, inr_asof = _latest(inr_per_usd)
    cp_nf_last, cp_nf_asof = _latest(cp_nonfinancial)
    cp_fin_last, cp_fin_asof = _latest(cp_financial)
    sofr_spread_last, sofr_spread_asof = _latest(sofr_iorb)
    energy_yoy = _monthly_yoy(energy_cpi)
    core_yoy = _monthly_yoy(core_cpi)
    custody_change_b = _official_52w_change_b(foreign_treasury_custody)
    foreign_rrp_change_b = _official_52w_change_b(foreign_official_rrp)
    energy_last, energy_asof = _latest(energy_yoy)
    core_last, core_asof = _latest(core_yoy)
    custody_last, custody_asof = _latest(custody_change_b)
    foreign_rrp_last, foreign_rrp_asof = _latest(foreign_rrp_change_b)
    cushing_last, cushing_asof = _latest(cushing_m_bbl)

    benchmark_pair = pd.concat(
        {"WTI": wti, "BRENT": brent}, axis=1, sort=True
    ).dropna()
    brent_wti_spread = (
        benchmark_pair["BRENT"] - benchmark_pair["WTI"]
        if not benchmark_pair.empty
        else pd.Series(dtype=float)
    )
    brent_wti_average_5d = brent_wti_spread.rolling(5, min_periods=3).mean().dropna()
    spread_last, spread_asof = _latest(brent_wti_spread)
    spread_average_last, spread_average_asof = _latest(brent_wti_average_5d)

    # Some administered-rate series are pre-filled over weekends or a known
    # next business day. Keep this context engine bounded by the latest date
    # actually observed anywhere else in its evidence set.
    asof_values = [
        value
        for value in (
            wti_asof,
            brent_asof,
            cp_nf_asof,
            cp_fin_asof,
            sofr_spread_asof,
            inr_asof,
            energy_asof,
            core_asof,
            custody_asof,
            foreign_rrp_asof,
            cushing_asof,
            spread_asof,
        )
        if value is not None
    ]
    visible_cutoff = pd.Timestamp(max(asof_values))
    iorb_visible = iorb[iorb.index <= visible_cutoff]
    iorb_monthly = _monthly_last(iorb_visible)
    iorb_last, iorb_asof = _latest(iorb_visible)

    assert wti_last is not None
    scenario_assumptions: dict[str, float | None] = dict(
        OIL_FUNDING_SCENARIO_DEFAULTS
    )
    explicit_funding_rate: float | None = None
    if assumptions:
        for key, value in assumptions.items():
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(numeric_value):
                continue
            if key == "funding_rate_pct":
                explicit_funding_rate = numeric_value
            elif key in scenario_assumptions:
                scenario_assumptions[key] = numeric_value

    if explicit_funding_rate is not None:
        scenario_funding_rate = explicit_funding_rate
        scenario_funding_basis = "explicit_scenario_assumption"
        scenario_funding_asof = None
    else:
        scenario_funding_rate = sofr_last
        scenario_funding_basis = (
            "observed_sofr" if sofr_last is not None else "unavailable"
        )
        scenario_funding_asof = sofr_asof
    scenario_assumptions.update(
        {
            "oil_price_usd_per_bbl": wti_last,
            "funding_rate_pct": scenario_funding_rate,
            "usd_inr": inr_last,
        }
    )

    # Coupling is computed on changes, never levels. WTI uses dollar changes
    # rather than percentage/log returns so April 2020's negative print remains
    # a real observation instead of breaking the transform.
    wti_change = wti.diff().dropna()
    cp_nf_change = cp_nonfinancial.diff().dropna()
    sofr_change = sofr_iorb.diff().dropna()
    inr_return = inr_per_usd.pct_change(fill_method=None).mul(100.0).dropna()
    corr_cp = _rolling_corr(wti_change, cp_nf_change)
    corr_sofr = _rolling_corr(wti_change, sofr_change)
    corr_inr = _rolling_corr(wti_change, inr_return)

    # Five-business-day, non-overlapping scatter: enough smoothing to read a
    # funding impulse without pretending thousands of overlapping windows are
    # independent observations.
    scatter_pair = pd.concat(
        {
            "oil_change_usd": wti.diff(5),
            "cp_change_bp": cp_nonfinancial.diff(5),
        },
        axis=1,
        sort=True,
    ).dropna()
    scatter_pair = scatter_pair.iloc[::5].tail(500)
    scatter_points: list[list[str | float]] = []
    scatter_fit: dict[str, float | int | None] = {
        "n": int(len(scatter_pair)),
        "correlation": None,
        "slope_bp_per_usd": None,
        "intercept_bp": None,
        "r_squared": None,
    }
    if len(scatter_pair) >= 20 and float(scatter_pair["oil_change_usd"].var()) > 0:
        x = scatter_pair["oil_change_usd"].to_numpy(dtype=float)
        y = scatter_pair["cp_change_bp"].to_numpy(dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        correlation = float(np.corrcoef(x, y)[0, 1])
        scatter_fit.update(
            {
                "correlation": round(correlation, 3),
                "slope_bp_per_usd": round(float(slope), 3),
                "intercept_bp": round(float(intercept), 3),
                "r_squared": round(correlation * correlation, 3),
            }
        )
        scatter_points = [
            [date.date().isoformat(), round(float(row.iloc[0]), 3), round(float(row.iloc[1]), 3)]
            for date, row in scatter_pair.iterrows()
        ]

    defaults = OIL_FUNDING_SCENARIO_DEFAULTS
    carry_grid = pd.concat(
        {"WTI": wti, "SOFR": sofr}, axis=1, sort=True
    ).dropna()
    tenor_fraction = defaults["tenor_days"] / 365.0
    carry_funding = (
        carry_grid["WTI"] * carry_grid["SOFR"] / 100.0 * tenor_fraction
    )
    carry_storage_insurance = (
        defaults["storage_usd_per_bbl_day"] * defaults["tenor_days"]
        + carry_grid["WTI"]
        * defaults["insurance_rate_pct"]
        / 100.0
        * tenor_fraction
    )
    carry_total = carry_funding + carry_storage_insurance

    # Keep partially available series visible. Requiring every series to print
    # on the same date would quietly erase valid observations around holidays
    # and during a temporary upstream outage.
    spot_frame = pd.concat({"WTI": wti, "BRENT": brent}, axis=1, sort=True)
    funding_frame = pd.concat(
        {
            "CP_NONFIN": cp_nonfinancial,
            "CP_FIN": cp_financial,
            "SOFR_IORB": sofr_iorb,
        },
        axis=1,
        sort=True,
    )
    coupling_frame = pd.concat(
        {"CP": corr_cp, "SOFR_IORB": corr_sofr, "INR": corr_inr},
        axis=1,
        sort=True,
    ).sort_index().ffill(limit=5)

    cushing_fill_pct = (
        round(cushing_last / CUSHING_WORKING_CAPACITY_MBBL * 100.0, 1)
        if cushing_last is not None
        else None
    )
    cushing_buffer = (
        round(cushing_last - CUSHING_STRESS_REFERENCE_MBBL, 3)
        if cushing_last is not None
        else None
    )
    recent_spread = brent_wti_spread.tail(60)

    return {
        "ok": True,
        "asof": max(asof_values),
        "live": {
            "wti": {
                "price_usd_per_bbl": wti_last,
                "change_5d_usd": _change(wti, 5),
                "change_20d_pct": _change(wti, 20, percent=True),
                "asof": wti_asof,
            },
            "brent": {
                "price_usd_per_bbl": brent_last,
                "change_5d_usd": _change(brent, 5),
                "change_20d_pct": _change(brent, 20, percent=True),
                "asof": brent_asof,
            },
            "cp_nonfinancial": {
                "spread_bp": cp_nf_last,
                "change_20d_bp": _change(cp_nonfinancial, 20),
                "percentile_3y": _pctl_3y(cp_nonfinancial),
                "asof": cp_nf_asof,
            },
            "cp_financial": {
                "spread_bp": cp_fin_last,
                "change_20d_bp": _change(cp_financial, 20),
                "percentile_3y": _pctl_3y(cp_financial),
                "asof": cp_fin_asof,
            },
            "sofr_iorb": {
                "spread_bp": sofr_spread_last,
                "change_20d_bp": _change(sofr_iorb, 20),
                "percentile_3y": _pctl_3y(sofr_iorb),
                "asof": sofr_spread_asof,
            },
            "inr": {
                "per_usd": inr_last,
                "change_20d_pct": _change(inr_per_usd, 20, percent=True),
                "change_60d_pct": _change(inr_per_usd, 60, percent=True),
                "asof": inr_asof,
            },
            "inflation_policy": {
                "energy_cpi_yoy_pct": energy_last,
                "core_cpi_yoy_pct": core_last,
                "iorb_pct": iorb_last,
                "energy_asof": energy_asof,
                "core_asof": core_asof,
                "iorb_asof": iorb_asof,
            },
            "official_dollar_parking": {
                "treasury_custody_change_52w_b": custody_last,
                "foreign_rrp_change_52w_b": foreign_rrp_last,
                "custody_asof": custody_asof,
                "foreign_rrp_asof": foreign_rrp_asof,
            },
            "cushing": {
                "stocks_m_bbl": cushing_last,
                "change_1w_m_bbl": _change(cushing_m_bbl, 1),
                "change_8w_m_bbl": _change(cushing_m_bbl, 8),
                "fill_of_last_working_capacity_pct": cushing_fill_pct,
                "buffer_to_20m_reference_m_bbl": cushing_buffer,
                "asof": cushing_asof,
            },
            "brent_wti_spread": {
                "brent_minus_wti_usd_per_bbl": spread_last,
                "average_5d_usd_per_bbl": spread_average_last,
                "negative_days_last_60_observations": int((recent_spread < 0).sum()),
                "asof": spread_asof,
                "average_asof": spread_average_asof,
            },
        },
        "charts": {
            "spot": {
                "rows": _adaptive_rows(
                    {"WTI": spot_frame["WTI"], "BRENT": spot_frame["BRENT"]}
                ),
                "labels": ["WTI spot", "Brent spot"],
                "unit": "USD per barrel",
            },
            "funding": {
                "rows": _adaptive_rows(
                    {
                        "CP_NONFIN": funding_frame["CP_NONFIN"],
                        "CP_FIN": funding_frame["CP_FIN"],
                        "SOFR_IORB": funding_frame["SOFR_IORB"],
                    }
                ),
                "labels": ["3m nonfinancial CP − bill", "3m financial CP − bill", "SOFR − IORB"],
                "unit": "basis points",
            },
            "coupling": {
                "rows": _adaptive_rows(
                    {
                        "CP": coupling_frame.get("CP", pd.Series(dtype=float)),
                        "SOFR_IORB": coupling_frame.get(
                            "SOFR_IORB", pd.Series(dtype=float)
                        ),
                        "INR": coupling_frame.get("INR", pd.Series(dtype=float)),
                    },
                    digits=3,
                ),
                "labels": ["WTI Δ vs CP-spread Δ", "WTI Δ vs SOFR−IORB Δ", "WTI Δ vs INR return"],
                "unit": "63-observation rolling correlation",
            },
            "carry_hurdle": {
                "rows": _adaptive_rows(
                    {
                        "FUNDING": carry_funding,
                        "STORAGE_INSURANCE": carry_storage_insurance,
                        "TOTAL": carry_total,
                    }
                ),
                "labels": ["funding component", "storage + insurance", "required contango"],
                "unit": "USD per barrel",
                "assumption": (
                    f"{defaults['tenor_days']:.0f}d ACT/365 simple carry; storage "
                    f"${defaults['storage_usd_per_bbl_day']:.2f}/bbl/day; insurance "
                    f"{defaults['insurance_rate_pct']:.2f}% annual"
                ),
            },
            "inflation_policy": {
                "rows": _adaptive_rows(
                    {
                        "ENERGY_CPI": energy_yoy,
                        "CORE_CPI": core_yoy,
                        "IORB": iorb_monthly,
                    }
                ),
                "labels": ["energy CPI, YoY", "core CPI, YoY", "IORB"],
                "unit": "percent",
            },
            "official_dollar_parking": {
                "rows": _adaptive_rows(
                    {
                        "CUSTODY": custody_change_b,
                        "FOREIGN_RRP": foreign_rrp_change_b,
                    }
                ),
                "labels": [
                    "foreign-official Treasury custody, 52w change",
                    "foreign-official RRP, 52w change",
                ],
                "unit": "USD billions",
            },
            "scatter": {
                "points": scatter_points,
                "fit": scatter_fit,
                "x_label": "5bd WTI change, USD per barrel",
                "y_label": "5bd nonfinancial CP−bill change, bp",
            },
            "cushing_inventory": {
                "rows": _adaptive_rows({"STOCKS": cushing_m_bbl}),
                "labels": ["Cushing commercial crude stocks"],
                "unit": "million barrels",
                "stress_reference_m_bbl": CUSHING_STRESS_REFERENCE_MBBL,
            },
            "brent_wti_spread": {
                "rows": _adaptive_rows(
                    {
                        "SPREAD": brent_wti_spread,
                        "AVERAGE_5D": brent_wti_average_5d,
                    }
                ),
                "labels": ["Brent − WTI", "5-observation average"],
                "unit": "USD per barrel",
            },
        },
        "market_structure": {
            "evidence_mode": "observed_where_available_reference_where_dated",
            "cushing": {
                "working_capacity_m_bbl": CUSHING_WORKING_CAPACITY_MBBL,
                "net_available_shell_capacity_m_bbl": CUSHING_SHELL_CAPACITY_MBBL,
                "capacity_asof": CUSHING_CAPACITY_ASOF,
                "capacity_status": "last official observation; publication discontinued",
                "stress_reference_m_bbl": CUSHING_STRESS_REFERENCE_MBBL,
                "stress_reference_status": (
                    "analytical reference, not a universal tank-bottom floor; "
                    "operability varies by facility and pipeline system"
                ),
                "delivery_role": "physical delivery hub for NYMEX WTI futures",
            },
            "benchmark_architecture": [
                {
                    "benchmark": "WTI",
                    "claim": "a deliverable contract tied to a specified inland hub",
                    "settlement": "physical delivery at Cushing, Oklahoma",
                    "release_valve": "pipeline flows and finite local tank capacity",
                    "inventory_print": "weekly EIA Cushing stocks",
                },
                {
                    "benchmark": "Brent",
                    "claim": "a cargo-based benchmark complex tied to a seaborne market",
                    "settlement": (
                        "ICE EFP delivery with an option to cash settle against the ICE Brent Index"
                    ),
                    "release_valve": "a broader waterborne cargo pool and rerouting",
                    "inventory_print": "no single equivalent weekly hub-stock print",
                    "basket": "BFOET grades plus WTI Midland cargoes since 2023",
                },
            ],
            "hub_taxonomy": [
                {
                    "type": "inland deliverability",
                    "examples": ["Cushing", "Hardisty", "Midland"],
                    "mechanism": "local inventory, pipeline constraints and delivery obligations can dominate basis",
                },
                {
                    "type": "storage and blending",
                    "examples": ["ARA / Rotterdam", "Singapore", "Fujairah", "Zhoushan"],
                    "mechanism": "tank availability, grade blending and regional flows shape differentials",
                },
                {
                    "type": "waterborne price formation",
                    "examples": ["North Sea / Brent", "Dubai / Oman"],
                    "mechanism": "cargo programmes, quality differentials and freight connect a wider deliverable pool",
                },
                {
                    "type": "export gateways",
                    "examples": ["Houston", "Corpus Christi"],
                    "mechanism": "dock, channel and freight economics transmit inland basis into the seaborne market",
                },
                {
                    "type": "cross-commodity analogues",
                    "examples": ["Henry Hub", "LME warehouses", "Illinois River"],
                    "mechanism": "delivery geography turns local inventory and transport constraints into benchmark basis",
                },
            ],
            "control_stack": [
                {"layer": "physical flow", "nodes": "Hormuz · Malacca", "status": "observed structural exposure"},
                {"layer": "freight and insurance", "nodes": "voyage availability · war-risk premium", "status": "transmission layer"},
                {"layer": "global price formation", "nodes": "Brent complex · Dubai/Oman", "status": "benchmark layer"},
                {"layer": "supply response", "nodes": "OPEC+ capacity · Permian", "status": "months, not hours"},
                {"layer": "local deliverability", "nodes": "Cushing · WTI basis", "status": "weekly physical constraint"},
            ],
            "transmission_order": [
                "chokepoint risk",
                "insurance and freight",
                "export economics",
                "physical differentials",
                "benchmark spreads",
                "flat price",
            ],
            "chokepoints": {
                "rows": CHOKEPOINT_FLOWS,
                "unit": "million barrels per day",
                "release_date": "2026-05-13",
                "latest_period": "1Q26",
                "world_supply_q1_2026_mbd": 95.4,
                "quality_note": (
                    "quarterly EIA estimates, not live traffic; Hormuz AIS data since late "
                    "February 2026 are especially unreliable and revised frequently"
                ),
                "live_status": "not asserted by Seiche without a reliable live transit feed",
            },
            "india": {
                "crude_import_dependence_pct": 88.5,
                "non_hormuz_crude_routing_pct": 70.0,
                "prior_non_hormuz_crude_routing_pct": 55.0,
                "lpg_imports_via_hormuz_pct": 90.0,
                "strategic_inventory_m_bbl": 21.0,
                "strategic_inventory_period": "1Q26 or latest available",
                "excise_cut_inr_per_litre": 10.0,
                "mangaluru_expansion_mmt": 1.75,
                "mangaluru_expansion_status": "planned Phase II capacity in ISPRL annual report",
                "verdict": (
                    "diversification reduced immediate route concentration; import dependence "
                    "and the small strategic barrel buffer remain structural exposures"
                ),
            },
            "principles": [
                "Places that publish data get attention; places that control physical flow get the outcome.",
                "WTI is a claim on a specified delivery hub; Brent is a claim on a broader cargo-based benchmark complex.",
                "If inventory matters to the model, the financing rate is a driver—not a background assumption.",
            ],
        },
        "scenario": {
            "assumptions": scenario_assumptions,
            "funding_rate_evidence": {
                "value_pct": scenario_funding_rate,
                "basis": scenario_funding_basis,
                "asof": scenario_funding_asof,
            },
            "outputs": _scenario(
                oil_price_usd_per_bbl=wti_last,
                funding_rate_pct=scenario_funding_rate,
                usd_inr=inr_last,
                assumptions=scenario_assumptions,
            ),
        },
        "channel_directions": {
            "cost_of_carry": "money_market_rates_to_oil_curve",
            "trade_finance": "oil_price_and_voyage_to_bank_funding",
            "margin": "oil_price_move_to_same_day_cash",
            "india_external": "oil_price_to_import_bill",
            "india_fx_liquidity": "rbi_dollar_sales_to_rupee_liquidity",
            "india_omc": "under_recovery_to_commercial_paper",
            "petrodollar_recycling": "oil_receipts_to_foreign_official_dollar_assets",
            "inflation_policy": "oil_to_inflation_to_policy_rate",
        },
        "sources": [
            {"series": "WTI spot", "source": "EIA via FRED", "id": "DCOILWTICO"},
            {"series": "Brent spot", "source": "EIA via FRED", "id": "DCOILBRENTEU"},
            {"series": "3m AA CP", "source": "Federal Reserve via FRED", "id": "DCPN3M / DCPF3M"},
            {"series": "3m Treasury", "source": "Federal Reserve via FRED", "id": "DGS3MO"},
            {"series": "SOFR / IORB", "source": "New York Fed / Federal Reserve via FRED", "id": "SOFR / IORB"},
            {"series": "USD/INR", "source": "Federal Reserve H.10 via FRED", "id": "DEXINUS"},
            {"series": "Energy / core CPI", "source": "BLS via FRED", "id": "CPIENGSL / CPILFESL"},
            {"series": "Foreign-official dollar parking", "source": "Federal Reserve H.4.1 via FRED", "id": "WMTSECL1 / WLRRAFOIAL"},
            {"series": "Cushing commercial crude stocks", "source": "U.S. EIA", "id": "W_EPC0_SAX_YCUOK_MBBL", "url": "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=W_EPC0_SAX_YCUOK_MBBL&f=W"},
            {"series": "Cushing storage capacity", "source": "U.S. EIA, March 2024 (discontinued)", "id": "crudeoilstorage.xlsx", "url": "https://www.eia.gov/petroleum/storagecapacity/"},
            {"series": "Global chokepoint flows / strategic inventories", "source": "U.S. EIA Global Energy Security Data", "id": "May 2026", "url": "https://www.eia.gov/outlooks/steo/report/energysecurity/article.php"},
            {"series": "WTI delivery mechanics", "source": "CME Group", "id": "WTI delivery at Cushing", "url": "https://www.cmegroup.com/education/courses/introduction-to-crude-oil/crude-oil-fundamentals/delivery-of-wti-futures"},
            {"series": "Brent contract mechanics", "source": "ICE", "id": "Brent Crude Futures", "url": "https://www.ice.com/products/219"},
            {"series": "India route diversification", "source": "Government of India PIB", "id": "2026-03-11", "url": "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2238525&lang=1&reg=3"},
            {"series": "India crude import dependence", "source": "Government of India PIB", "id": "July 2026", "url": "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202675912001.pdf"},
            {"series": "Mangaluru strategic-storage expansion", "source": "Indian Strategic Petroleum Reserves Limited", "id": "FY2024-25 annual report", "url": "https://www.isprlindia.com/downloads/annual-reports/Annual_Report_Final_2025_Revised_English.pdf"},
        ],
        "caveats": [
            "WTI and Brent are spot benchmarks, not a current futures strip; forward spread is scenario-only because EIA's public NYMEX table stops after 2024-04-05",
            "rolling correlations and the scatter are associational diagnostics, not causal estimates; sign and strength can change by regime",
            "the scatter uses non-overlapping 5-business-day changes; no lead-lag or policy reaction is claimed",
            "India import volume, RBI sales, liquidity replenishment, OMC under-recovery, compensation lag, and CP share are editable scenario assumptions, not live observations",
            "foreign-official Treasury custody and RRP are broad dollar-parking proxies; they do not identify oil exporters or prove petrodollar recycling",
            "energy CPI includes more than crude oil and the inflation-policy chart is descriptive; no central-bank reaction coefficient is estimated",
            "positive carry headroom ignores capacity, quality/location basis, transaction costs, and convenience yield; it is not an executable arbitrage signal",
            "20m barrels is a visible stress reference, not a universal Cushing tank-bottom floor; EIA says operability differs across facilities and pipeline systems",
            "Cushing fill uses the last official March 2024 working-capacity denominator because EIA discontinued the capacity report; it is not a current-capacity claim",
            "chokepoint bars are EIA quarterly estimates through 1Q26, not a current closure or transit-count claim; Hormuz AIS observations are unusually uncertain",
            "India structural values combine official observations published on different dates; they are an exposure ledger, not a synchronized live snapshot",
        ],
        "method": (
            "spot and funding charts retain actual observation dates (weekly samples before the trailing 180 days, daily thereafter); "
            "CP spreads = 3m AA CP minus 3m Treasury on CP print dates; SOFR−IORB in bp; coupling = trailing 63-observation correlation of daily WTI dollar changes with daily funding-spread changes or INR returns; "
            "official-dollar parking = 52-week change in Fed custody / foreign-official RRP balances, converted from $M to $B; energy/core CPI = 12-month percent change; "
            "carry = spot × (funding + insurance) × ACT/365 tenor + daily storage × tenor; Cushing stocks are EIA weekly thousand barrels converted to million barrels; Brent−WTI uses same-date spot observations; scenario arithmetic is deterministic context and never enters the Seiche composite"
        ),
    }
