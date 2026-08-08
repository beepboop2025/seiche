"""Ballast — commodity futures cash pressure read through funding plumbing.

Ballast is deliberately *not* a commodity-price signal and never enters the
Seiche composite.  It combines official weekly CFTC positioning with public
spot benchmarks, EIA physical inventory, and dollar-funding spreads to answer
one narrower question: where can a commodity move create an unusually large
need for cash or balance-sheet capacity?

The central estimate is a settlement-sensitivity proxy, not an observed margin
call.  Aggregate open interest multiplied by the contract multiplier and the
absolute benchmark-price move is a useful gross cash-transfer scale, but spot
is not the futures settlement, portfolios net, and OTC positions are dark.
Every payload carries those boundaries next to the number.
"""

from __future__ import annotations

from collections.abc import Mapping
import math

import numpy as np
import pandas as pd

from seiche.config import (
    BALLAST_ACUTE_PCTL,
    BALLAST_CFTC_RELEASE_LAG_DAYS,
    BALLAST_CONTRACTS,
    BALLAST_EIA_RELEASE_LAG_DAYS,
    BALLAST_TIGHT_PCTL,
)


MIN_COT_HISTORY = 52
PCTL_WEEKS = 260
PCTL_DAYS = 756


def _clean(series: pd.Series | None) -> pd.Series:
    if series is None or not isinstance(series, pd.Series):
        return pd.Series(dtype=float)
    out = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    return out[~out.index.duplicated(keep="last")].astype(float)


def _number(value: object, digits: int = 3) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return round(parsed, digits)


def _latest(series: pd.Series) -> tuple[float | None, str | None]:
    values = _clean(series)
    if values.empty:
        return None, None
    return _number(values.iloc[-1], 4), values.index[-1].date().isoformat()


def _percentile(series: pd.Series, *, tail: int | None = None) -> tuple[float | None, int]:
    values = _clean(series)
    if tail is not None:
        values = values.tail(tail)
    if len(values) < 20:
        return None, int(len(values))
    current = values.iloc[-1]
    # Mid-rank ties keep a flat channel at p50.  A plain ``<=`` empirical rank
    # would label an unchanged spread p100 and manufacture stress from stasis.
    rank = (values < current).mean() + 0.5 * (values == current).mean()
    return round(float(rank * 100.0), 1), int(len(values))


def _spread_bp(rate: pd.Series, benchmark: pd.Series) -> pd.Series:
    """Rate minus benchmark on rate-print dates, with a five-day carry."""

    left, right = _clean(rate), _clean(benchmark)
    if left.empty or right.empty:
        return pd.Series(dtype=float)
    grid = left.index.union(right.index)
    frame = pd.concat(
        {"rate": left.reindex(grid), "benchmark": right.reindex(grid)},
        axis=1,
        sort=True,
    ).sort_index()
    frame["benchmark"] = frame["benchmark"].ffill(limit=5)
    frame = frame.reindex(left.index).dropna()
    return (frame["rate"] - frame["benchmark"]).mul(100.0)


def _price_on_dates(price: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    values = _clean(price)
    if values.empty or dates.empty:
        return pd.Series(index=dates, dtype=float)
    return values.reindex(dates, method="ffill", tolerance=pd.Timedelta(days=7))


def _pressure_metric(
    *, channel: str, label: str, percentile: float | None, asof: str | None,
    value: float | None, unit: str,
) -> dict | None:
    if percentile is None:
        return None
    return {
        "channel": channel,
        "label": label,
        "percentile": percentile,
        "asof": asof,
        "value": value,
        "unit": unit,
    }


def _relative_state(percentile: float | None) -> str:
    """Describe a within-channel rank without turning it into a stress claim."""

    if percentile is None:
        return "UNAVAILABLE"
    if percentile >= BALLAST_ACUTE_PCTL:
        return "TAIL_RELATIVE"
    if percentile >= BALLAST_TIGHT_PCTL:
        return "ELEVATED_RELATIVE"
    return "NORMAL_RELATIVE"


def _contract_block(
    contract: str,
    positions: pd.DataFrame,
    price: pd.Series,
) -> tuple[dict | None, list[dict]]:
    spec = BALLAST_CONTRACTS[contract]
    if "contract" not in positions.columns:
        return None, []
    frame = positions.loc[positions["contract"] == contract].copy()
    required = {
        "date",
        "open_interest_all",
        "prod_merc_positions_long",
        "prod_merc_positions_short",
        "swap_positions_long_all",
        "swap__positions_short_all",
        "m_money_positions_long_all",
        "m_money_positions_short_all",
        "other_rept_positions_long",
        "other_rept_positions_short",
        "conc_gross_le_4_tdr_long",
        "conc_gross_le_4_tdr_short",
        "conc_gross_le_8_tdr_long",
        "conc_gross_le_8_tdr_short",
    }
    if frame.empty or not required.issubset(frame.columns):
        return None, []
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    if "available_date" in frame.columns:
        frame["available_date"] = pd.to_datetime(
            frame["available_date"], errors="coerce"
        )
    else:
        frame["available_date"] = pd.NaT
    frame["available_date"] = frame["available_date"].fillna(
        frame["date"] + pd.Timedelta(days=BALLAST_CFTC_RELEASE_LAG_DAYS)
    )
    if len(frame) < MIN_COT_HISTORY:
        return None, []
    for column in required - {"date"}:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.set_index("date", drop=False)

    price_values = _clean(price)
    frame["price_proxy"] = _price_on_dates(price_values, frame.index)
    frame["price_move"] = frame["price_proxy"].diff()
    frame["gross_displacement"] = (
        frame["price_move"].abs()
        * frame["open_interest_all"]
        * float(spec["multiplier"])
    )
    rising = frame["price_move"] > 0
    falling = frame["price_move"] < 0

    category_columns = {
        "producer_merchant": (
            "prod_merc_positions_long", "prod_merc_positions_short"
        ),
        "swap_dealer": ("swap_positions_long_all", "swap__positions_short_all"),
        "managed_money": (
            "m_money_positions_long_all", "m_money_positions_short_all"
        ),
        "other_reportable": (
            "other_rept_positions_long", "other_rept_positions_short"
        ),
    }
    for name, (long_col, short_col) in category_columns.items():
        at_risk = pd.Series(np.nan, index=frame.index, dtype=float)
        at_risk.loc[rising] = frame.loc[rising, short_col]
        at_risk.loc[falling] = frame.loc[falling, long_col]
        frame[f"{name}_at_risk"] = at_risk
        frame[f"{name}_displacement"] = (
            at_risk * frame["price_move"].abs() * float(spec["multiplier"])
        )

    frame["top4_paying"] = np.where(
        rising,
        frame["conc_gross_le_4_tdr_short"],
        np.where(falling, frame["conc_gross_le_4_tdr_long"], np.nan),
    )
    frame["top8_paying"] = np.where(
        rising,
        frame["conc_gross_le_8_tdr_short"],
        np.where(falling, frame["conc_gross_le_8_tdr_long"], np.nan),
    )
    usable = frame.dropna(
        subset=["price_proxy", "price_move", "open_interest_all", "gross_displacement"]
    )
    if len(usable) < MIN_COT_HISTORY - 1:
        return None, []
    current = usable.iloc[-1]
    report_asof = current.name.date().isoformat()
    available_asof = current["available_date"].date().isoformat()
    latest_price, latest_price_asof = _latest(price_values)

    gross_pctl, gross_n = _percentile(usable["gross_displacement"], tail=PCTL_WEEKS)
    concentration_pctl, concentration_n = _percentile(
        usable["top4_paying"], tail=PCTL_WEEKS
    )
    oi_pctl, oi_n = _percentile(usable["open_interest_all"], tail=PCTL_WEEKS)
    paying_positions = sum(
        float(current.get(f"{name}_at_risk") or 0.0)
        if pd.notna(current.get(f"{name}_at_risk"))
        else 0.0
        for name in category_columns
    )
    open_interest = float(current["open_interest_all"])
    paying_coverage = (
        min(100.0, paying_positions / open_interest * 100.0)
        if open_interest > 0
        else 0.0
    )
    move = float(current["price_move"])
    if move > 0:
        direction = "shorts_pay_if_proxy_tracks_settlement"
    elif move < 0:
        direction = "longs_pay_if_proxy_tracks_settlement"
    else:
        direction = "no_direction_at_unchanged_proxy"

    pressure: list[dict] = []
    for metric in (
        _pressure_metric(
            channel="mark_to_market",
            label=f"{contract} gross mark displacement",
            percentile=gross_pctl,
            asof=report_asof,
            value=_number(current["gross_displacement"] / 1e9, 2),
            unit="USD billions, spot-proxy scale",
        ),
        _pressure_metric(
            channel="position_concentration",
            label=f"{contract} top-four paying-side concentration",
            percentile=concentration_pctl,
            asof=report_asof,
            value=_number(current["top4_paying"], 1),
            unit="percent of open interest",
        ),
    ):
        if metric is not None:
            pressure.append(metric)

    history = []
    for date, row in usable.tail(PCTL_WEEKS).iterrows():
        history.append(
            [
                date.date().isoformat(),
                _number(row["gross_displacement"] / 1e9, 3),
                _number(row["producer_merchant_displacement"] / 1e9, 3),
                _number(row["top4_paying"], 2),
            ]
        )

    return {
        "key": contract,
        "label": spec["label"],
        "report_asof": report_asof,
        "available_asof": available_asof,
        "report_lag": (
            "normal T+3 availability assumption: Tuesday positions become "
            "public Friday; holiday exceptions may differ"
        ),
        "price_proxy": {
            "report_date_value": _number(current["price_proxy"], 4),
            "change_since_prior_report": _number(current["price_move"], 4),
            "latest_value": latest_price,
            "latest_asof": latest_price_asof,
            "status": "spot_proxy_not_futures_settlement",
        },
        "open_interest": {
            "contracts": _number(open_interest, 0),
            "notional_usd_at_proxy_price": _number(
                open_interest * float(spec["multiplier"]) * abs(float(current["price_proxy"])),
                0,
            ),
            "percentile_5y": oi_pctl,
            "percentile_n": oi_n,
            "contract_multiplier": spec["multiplier"],
            "multiplier_unit": spec["multiplier_unit"],
        },
        "cash_transfer_scale": {
            "gross_mark_displacement_usd": _number(current["gross_displacement"], 0),
            "gross_displacement_percentile_5y": gross_pctl,
            "percentile_n": gross_n,
            "direction": direction,
            "category_proxies_usd": {
                name: _number(current[f"{name}_displacement"], 0)
                for name in category_columns
            },
            "reported_paying_side_coverage_pct": _number(paying_coverage, 1),
            "status": "derived_gross_scale_not_observed_margin_call",
        },
        "positioning": {
            "top4_paying_side_pct": _number(current["top4_paying"], 1),
            "top8_paying_side_pct": _number(current["top8_paying"], 1),
            "top4_paying_side_percentile_5y": concentration_pctl,
            "percentile_n": concentration_n,
            "producer_merchant_paying_side_pct_oi": _number(
                current["producer_merchant_at_risk"] / open_interest * 100.0, 1
            ) if open_interest > 0 else None,
            "managed_money_paying_side_pct_oi": _number(
                current["managed_money_at_risk"] / open_interest * 100.0, 1
            ) if open_interest > 0 else None,
        },
        "history": {
            "columns": [
                "date", "gross_displacement_usd_b", "producer_merchant_proxy_usd_b",
                "top4_paying_side_pct",
            ],
            "rows": history,
        },
    }, pressure


def _inventory_block(
    stocks: pd.Series,
    wti: pd.Series,
    sofr: pd.Series,
) -> tuple[dict | None, dict | None]:
    stock = _clean(stocks)
    price = _clean(wti)
    funding = _clean(sofr)
    if len(stock) < 52 or price.empty:
        return None, None
    latest_date = stock.index[-1]
    price_at = _price_on_dates(price, pd.DatetimeIndex([latest_date])).iloc[0]
    if pd.isna(price_at):
        return None, None
    funding_at = _price_on_dates(funding, pd.DatetimeIndex([latest_date])).iloc[0]
    latest = float(stock.iloc[-1])
    change_1w = float(stock.diff().iloc[-1])
    change_4w = float(stock.diff(4).iloc[-1]) if len(stock) > 4 else np.nan
    level_pctl, level_n = _percentile(stock, tail=PCTL_WEEKS)
    change_pctl, change_n = _percentile(stock.diff().abs(), tail=PCTL_WEEKS)
    market_value = latest * 1000.0 * float(price_at)
    annual_carry = (
        market_value * float(funding_at) / 100.0 if pd.notna(funding_at) else None
    )
    asof = latest_date.date().isoformat()
    available_asof = (
        latest_date + pd.Timedelta(days=BALLAST_EIA_RELEASE_LAG_DAYS)
    ).date().isoformat()
    block = {
        "asof": asof,
        "available_asof": available_asof,
        "stocks_million_bbl": _number(latest / 1000.0, 3),
        "change_1w_million_bbl": _number(change_1w / 1000.0, 3),
        "change_4w_million_bbl": _number(change_4w / 1000.0, 3),
        "level_percentile_5y": level_pctl,
        "level_percentile_n": level_n,
        "absolute_weekly_change_percentile_5y": change_pctl,
        "change_percentile_n": change_n,
        "market_value_at_wti_proxy_usd": _number(market_value, 0),
        "annual_sofr_carry_benchmark_usd": _number(annual_carry, 0),
        "sofr_benchmark_pct": _number(funding_at, 3),
        "status": "observed_quantity_times_public_benchmark_not_financed_book",
    }
    return block, _pressure_metric(
        channel="physical_inventory",
        label="US crude inventory absolute weekly change",
        percentile=change_pctl,
        asof=asof,
        value=_number(abs(change_1w) / 1000.0, 3),
        unit="million barrels",
    )


def _funding_block(
    sofr: pd.Series,
    iorb: pd.Series,
    cp_nonfinancial_3m: pd.Series,
    treasury_3m: pd.Series,
) -> tuple[dict, list[dict]]:
    sofr_iorb = _spread_bp(sofr, iorb)
    cp_bill = _spread_bp(cp_nonfinancial_3m, treasury_3m)
    rows: dict[str, dict] = {}
    pressure: list[dict] = []
    for key, label, series in (
        ("sofr_iorb", "SOFR minus IORB", sofr_iorb),
        ("cp_nonfinancial", "3m nonfinancial CP minus Treasury", cp_bill),
    ):
        value, asof = _latest(series)
        pctl, n = _percentile(series, tail=PCTL_DAYS)
        rows[key] = {
            "spread_bp": value,
            "percentile_3y": pctl,
            "percentile_n": n,
            "asof": asof,
        }
        metric = _pressure_metric(
            channel="dollar_funding",
            label=label,
            percentile=pctl,
            asof=asof,
            value=value,
            unit="basis points",
        )
        if metric is not None:
            pressure.append(metric)
    return rows, pressure


def analyze(
    *,
    commodity_positions: pd.DataFrame,
    prices: Mapping[str, pd.Series],
    crude_stocks_ex_spr: pd.Series,
    sofr: pd.Series,
    iorb: pd.Series,
    cp_nonfinancial_3m: pd.Series,
    treasury_3m: pd.Series,
) -> dict:
    """Build the energy-first Ballast context contract."""

    positions = commodity_positions.copy() if isinstance(commodity_positions, pd.DataFrame) else pd.DataFrame()
    contracts: list[dict] = []
    metrics: list[dict] = []
    for key, spec in BALLAST_CONTRACTS.items():
        block, contract_metrics = _contract_block(
            key,
            positions,
            prices.get(str(spec["price_mnemonic"]), pd.Series(dtype=float)),
        )
        if block is not None:
            contracts.append(block)
            metrics.extend(contract_metrics)
    if not contracts:
        return {
            "ok": False,
            "reason": "insufficient aligned CFTC positioning and public benchmark history",
        }

    inventory, inventory_metric = _inventory_block(
        crude_stocks_ex_spr,
        prices.get("WTI_SPOT", pd.Series(dtype=float)),
        sofr,
    )
    if inventory_metric is not None:
        metrics.append(inventory_metric)
    funding, funding_metrics = _funding_block(
        sofr, iorb, cp_nonfinancial_3m, treasury_3m
    )
    metrics.extend(funding_metrics)

    availability = {
        "wti_positioning": any(row["key"] == "WTI" for row in contracts),
        "henry_hub_positioning": any(row["key"] == "HENRY_HUB" for row in contracts),
        "wti_price_proxy": not _clean(prices.get("WTI_SPOT")).empty,
        "henry_hub_price_proxy": not _clean(prices.get("HENRY_HUB_SPOT")).empty,
        "physical_inventory": inventory is not None,
        "sofr_iorb": funding["sofr_iorb"]["spread_bp"] is not None,
        "commercial_paper": funding["cp_nonfinancial"]["spread_bp"] is not None,
    }
    coverage_pct = round(sum(availability.values()) / len(availability) * 100.0, 1)
    ranked = sorted(metrics, key=lambda row: row["percentile"], reverse=True)
    commodity_ranked = [
        row for row in ranked if row["channel"] != "dollar_funding"
    ]
    funding_ranked = [
        row for row in ranked if row["channel"] == "dollar_funding"
    ]
    dominant = commodity_ranked[0] if commodity_ranked else None
    funding_dominant = funding_ranked[0] if funding_ranked else None
    worst = dominant["percentile"] if dominant is not None else None
    if coverage_pct < 50.0 or worst is None:
        state = "CANNOT_ASSESS"
    elif worst >= BALLAST_ACUTE_PCTL:
        state = "ACUTE"
    elif worst >= BALLAST_TIGHT_PCTL:
        state = "TIGHT"
    else:
        state = "CALM"

    asof_candidates = [
        value
        for value in (
            *(row.get("available_asof") for row in contracts),
            inventory.get("available_asof") if inventory else None,
            funding["sofr_iorb"].get("asof"),
            funding["cp_nonfinancial"].get("asof"),
        )
        if value
    ]
    return {
        "ok": True,
        "schema": "seiche.ballast.v1",
        "asof": max(asof_candidates) if asof_candidates else None,
        "context_only": True,
        "headline": {
            "state": state,
            "worst_channel_percentile": worst,
            "dominant_channel": dominant,
            "funding_overlay": {
                "status": _relative_state(
                    funding_dominant["percentile"] if funding_dominant else None
                ),
                "dominant_channel": funding_dominant,
                "role": "amplifier_not_commodity_state_trigger",
            },
            "coverage_pct": coverage_pct,
            "rule": (
                f"worst observed commodity or physical channel; TIGHT >= "
                f"p{BALLAST_TIGHT_PCTL:.0f}, ACUTE >= p{BALLAST_ACUTE_PCTL:.0f}; "
                "funding is a separate amplifier overlay; no weighted blend"
            ),
            "composite_status": "never_enters_seiche_composite",
        },
        "contracts": contracts,
        "inventory": inventory or {
            "status": "unavailable",
            "reason": "official EIA weekly inventory history is unavailable",
        },
        "funding": funding,
        "pressure_ledger": ranked,
        "coverage": {
            "required_inputs": availability,
            "available_pct": coverage_pct,
            "boundaries": [
                {"layer": "CFTC open interest and trader classes", "status": "observed_weekly_normal_t_plus_3_availability"},
                {"layer": "commodity benchmark prices", "status": "observed_spot_proxy_not_settlement"},
                {"layer": "EIA commercial crude inventory", "status": "observed_weekly_normal_wednesday_availability"},
                {"layer": "exchange initial / maintenance margin", "status": "scenario_only_until_stable_contract_feed"},
                {"layer": "portfolio netting and OTC positions", "status": "dark"},
                {"layer": "live order-book depth and exit cost", "status": "undertow_handoff_requires_licensed_depth"},
            ],
        },
        "handoffs": {
            "undertow": {
                "question": "What does exiting this futures risk cost at position size?",
                "keys": [
                    "contract", "report_asof", "available_asof",
                    "open_interest_contracts",
                    "gross_mark_displacement_usd", "paying_side_concentration_pct",
                ],
                "boundary": "Ballast supplies stress context, never live depth or executable slippage",
            },
            "liquilens": {
                "question": (
                    "Which institution has qualifying exposure evidence, and how "
                    "could the cash demand reach its funding channels?"
                ),
                "keys": [
                    "contract", "price_move_proxy", "commercial_side_proxy_usd",
                    "funding_spreads", "inventory_carry_benchmark",
                ],
                "boundary": "Ballast attributes no aggregate market position to a named institution",
            },
        },
        "sources": [
            {"layer": "physical-commodity futures positioning", "source": "CFTC Disaggregated COT futures-only", "id": "72hh-3qpy", "cadence": "weekly, T+3"},
            {"layer": "WTI / Henry Hub benchmark", "source": "EIA via FRED", "id": "DCOILWTICO / DHHNGSP", "cadence": "daily"},
            {"layer": "commercial crude stocks ex SPR", "source": "EIA dnav", "id": "WCESTUS1", "cadence": "weekly"},
            {"layer": "dollar funding", "source": "Federal Reserve / NY Fed via FRED", "id": "SOFR / IORB / DCPN3M / DGS3MO", "cadence": "daily"},
        ],
        "caveats": [
            "gross mark displacement uses a public spot benchmark in place of the exact futures settlement and is a scale proxy, not an observed variation-margin call",
            "open interest is aggregated across contract months; calendar-spread netting, portfolio offsets, client add-ons and intraday calls are not public",
            "CFTC categories are weekly Tuesday positions normally published Friday; EIA stock periods normally publish the following Wednesday; replay uses those standard lags and may conservatively differ in holiday weeks",
            "CFTC trader classes cannot identify a named participant or an institution's bilateral OTC book",
            "inventory market value and SOFR carry are benchmark arithmetic; they are not the financed share, borrowing rate or hedge book of any owner",
            "the headline is the worst commodity or physical percentile; funding ranks are a separate amplifier overlay and cannot trigger the commodity state by themselves",
            "all percentile labels are descriptive ranks, not probabilities, forecasts, trade signals or Seiche composite components",
        ],
        "method": (
            "per contract, align the official Tuesday CFTC row to the latest public spot benchmark no more than seven days old and treat it as available on the normal Friday release; "
            "gross mark-displacement scale = abs(change since prior CFTC report) x open interest x contract multiplier; "
            "category scales apply the same move to the reported losing-side positions; concentration chooses the long or short CFTC field according to the move; "
            "inventory = EIA weekly commercial crude stocks excluding SPR, treated as available on the normal following Wednesday, with market value and annual SOFR carry shown only as benchmark arithmetic; "
            "each observed channel is ranked against its own trailing history; the headline takes the worst commodity or physical percentile without blending, while funding remains a separate amplifier overlay"
        ),
    }
