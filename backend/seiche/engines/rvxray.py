"""RV X-Ray — leveraged-positioning size and fragility for the Treasury RV
complex (basis trade + swap-spread trade funding leg).

Published estimates of the basis trade disagree by $500B (MS $1.5T vs IMF
$1T) partly because methods are opaque. Ours is deliberately transparent:

  pair proxy  = sum over contracts of min(lev-fund shorts, asset-mgr longs)
                x face value        -> classic cash-futures basis footprint
  gross short = lev-fund shorts x face                     -> whole RV complex
  DV01        = lev-fund shorts x per-contract DV01        -> shock arithmetic

Fragility couples positioning to funding: size x repo dependence (DVP volume)
vs a dealer-capacity proxy. The margin-shock simulator answers: for an X bp
adverse move, what's the mark-to-market hit, and how many days of DVP volume
would an unwind of Y% of the trade absorb?
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.config import UST_CONTRACTS


_PAIR_POSITION_FIELDS = (
    "lev_money_positions_short_all",
    "asset_mgr_positions_long_all",
)
_GROSS_POSITION_FIELDS = ("lev_money_positions_short_all",)
_NET_POSITION_FIELDS = (
    "lev_money_positions_short_all",
    "lev_money_positions_long_all",
)
_RV_POSITION_FIELDS = tuple(
    dict.fromkeys(
        (*_PAIR_POSITION_FIELDS, *_GROSS_POSITION_FIELDS, *_NET_POSITION_FIELDS)
    )
)
_RV_METRIC_FIELDS = {
    "pair_proxy_b": _PAIR_POSITION_FIELDS,
    "gross_short_b": _GROSS_POSITION_FIELDS,
    "net_b": _NET_POSITION_FIELDS,
    "dv01_m_per_bp": _GROSS_POSITION_FIELDS,
}
_CROWDING_POSITION_FIELDS = (
    "open_interest_all",
    "lev_money_positions_long_all",
    "lev_money_positions_short_all",
)


def _position_rows(
    frame: pd.DataFrame, numeric_fields: tuple[str, ...]
) -> pd.DataFrame:
    """Normalize identities and numeric fields without coupling metric coverage."""

    if frame.empty or not {"date", "contract"}.issubset(frame.columns):
        return frame.iloc[0:0].copy()

    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    mask = out["date"].notna() & out["contract"].notna()
    mask &= out["contract"].astype(str).str.strip().ne("")
    for field in numeric_fields:
        values = (
            out[field] if field in out.columns else pd.Series(np.nan, index=out.index)
        )
        out[field] = pd.to_numeric(values, errors="coerce").astype(float)
    return out.loc[mask].copy()


def _finite_mask(frame: pd.DataFrame, numeric_fields: tuple[str, ...]) -> np.ndarray:
    mask = np.ones(len(frame), dtype=bool)
    for field in numeric_fields:
        if field not in frame.columns:
            return np.zeros(len(frame), dtype=bool)
        mask &= np.isfinite(frame[field].to_numpy(dtype=float))
    return mask


def _finite_rows(frame: pd.DataFrame, numeric_fields: tuple[str, ...]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    mask = _finite_mask(frame, numeric_fields)
    return frame.loc[mask].copy()


def _finite_unique_contract_rows(
    frame: pd.DataFrame, numeric_fields: tuple[str, ...]
) -> pd.DataFrame:
    """Return one finite row per contract; duplicate identities fail closed."""

    if frame.empty:
        return frame.copy()
    unique_identity = frame.groupby("contract")["contract"].transform("size").eq(1)
    return frame.loc[unique_identity & _finite_mask(frame, numeric_fields)].copy()


def _complete_position_rows(
    frame: pd.DataFrame, numeric_fields: tuple[str, ...]
) -> pd.DataFrame:
    """Return dated contract rows whose requested numeric fields are finite."""

    return _finite_rows(_position_rows(frame, numeric_fields), numeric_fields)


def _ust_rows(tff: pd.DataFrame) -> pd.DataFrame:
    if "contract" not in tff.columns:
        return tff.iloc[0:0].copy()
    return tff[tff["contract"].isin(UST_CONTRACTS)].copy()


def _empty_position_history() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["pair_b", "gross_short_b", "net_b", "dv01_m"],
        index=pd.DatetimeIndex([], name="date"),
    )


def position_history(tff: pd.DataFrame) -> pd.DataFrame:
    """Weekly metric-specific RV history; unavailable metrics remain missing."""
    prepared = _position_rows(_ust_rows(tff), _RV_POSITION_FIELDS)
    if prepared.empty:
        return _empty_position_history()

    rows = []
    for date, grp in prepared.groupby("date"):
        pair_rows = _finite_unique_contract_rows(grp, _PAIR_POSITION_FIELDS)
        gross_rows = _finite_unique_contract_rows(grp, _GROSS_POSITION_FIELDS)
        net_rows = _finite_unique_contract_rows(grp, _NET_POSITION_FIELDS)
        pair_notional = (
            sum(
                min(
                    float(r["lev_money_positions_short_all"]),
                    float(r["asset_mgr_positions_long_all"]),
                )
                * UST_CONTRACTS[r["contract"]]["face"]
                for _, r in pair_rows.iterrows()
            )
            if not pair_rows.empty
            else np.nan
        )
        gross_short = (
            sum(
                float(r["lev_money_positions_short_all"])
                * UST_CONTRACTS[r["contract"]]["face"]
                for _, r in gross_rows.iterrows()
            )
            if not gross_rows.empty
            else np.nan
        )
        net = (
            sum(
                (
                    float(r["lev_money_positions_long_all"])
                    - float(r["lev_money_positions_short_all"])
                )
                * UST_CONTRACTS[r["contract"]]["face"]
                for _, r in net_rows.iterrows()
            )
            if not net_rows.empty
            else np.nan
        )
        dv01 = (
            sum(
                float(r["lev_money_positions_short_all"])
                * UST_CONTRACTS[r["contract"]]["dv01"]
                for _, r in gross_rows.iterrows()
            )
            if not gross_rows.empty
            else np.nan
        )
        rows.append(
            {
                "date": date,
                "pair_b": pair_notional / 1e9,
                "gross_short_b": gross_short / 1e9,
                "net_b": net / 1e9,
                "dv01_m": dv01 / 1e6,
            }
        )
    return pd.DataFrame(rows).set_index("date").sort_index().dropna(how="all")


def _metric_coverage(
    frame: pd.DataFrame, expected_contracts: set[str]
) -> dict[str, dict]:
    """Measure usable contracts against the cumulative observed universe.

    Looking only at received rows cannot detect a contract that disappeared
    from the report.  The caller therefore carries forward every UST contract
    observed on or before this report date.
    """

    expected = sorted(expected_contracts)
    total = len(expected)
    contract_counts = (
        frame["contract"].value_counts() if not frame.empty else pd.Series(dtype=int)
    )
    coverage = {}
    for metric, fields in _RV_METRIC_FIELDS.items():
        usable_contracts = []
        duplicate_contracts = []
        for contract in expected:
            rows = frame[frame["contract"] == contract]
            if len(rows) > 1:
                duplicate_contracts.append(contract)
            if len(rows) == 1 and bool(_finite_mask(rows, fields)[0]):
                usable_contracts.append(contract)
        present_contracts = sorted(
            contract
            for contract in expected
            if int(contract_counts.get(contract, 0)) > 0
        )
        missing_contracts = sorted(set(expected) - set(present_contracts))
        unusable_contracts = sorted(set(present_contracts) - set(usable_contracts))
        usable = len(usable_contracts)
        status = "unavailable"
        if total > 0 and usable == total:
            status = "complete"
        elif usable > 0:
            status = "partial"
        coverage[metric] = {
            "status": status,
            "usable_rows": usable,
            "total_rows": total,
            "coverage_pct": round(usable / total * 100.0, 1) if total else 0.0,
            "coverage_unit": "expected_contracts",
            "expected_contracts": expected,
            "usable_contracts": usable_contracts,
            "missing_contracts": missing_contracts,
            "unusable_contracts": unusable_contracts,
            "duplicate_contracts": duplicate_contracts,
            "required_fields": list(fields),
        }
    return coverage


def _finite_or_none(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _rounded_or_none(value: object, digits: int = 1) -> float | None:
    numeric = _finite_or_none(value)
    return round(numeric, digits) if numeric is not None else None


def _by_contract_latest(
    tff: pd.DataFrame, asof: pd.Timestamp | None = None
) -> list[dict]:
    """Per-contract gross short and net (long minus short) for the as-of week, $B.

    The as-of date is the caller's, so the table cannot date itself differently
    from the headline when the last TFF row is partial.
    """
    ust = _position_rows(_ust_rows(tff), _RV_POSITION_FIELDS)
    if ust.empty:
        return []
    latest = ust[ust["date"] == (ust["date"].max() if asof is None else asof)]
    out = []
    for _, r in latest.iterrows():
        c = UST_CONTRACTS[r["contract"]]
        short_ok = np.isfinite(float(r["lev_money_positions_short_all"]))
        long_ok = np.isfinite(float(r["lev_money_positions_long_all"]))
        asset_long_ok = np.isfinite(float(r["asset_mgr_positions_long_all"]))
        ls = float(r["lev_money_positions_short_all"]) if short_ok else None
        ll = float(r["lev_money_positions_long_all"]) if long_ok else None
        al = float(r["asset_mgr_positions_long_all"]) if asset_long_ok else None
        missing_fields = [
            field for field in _RV_POSITION_FIELDS if not np.isfinite(float(r[field]))
        ]
        out.append(
            {
                "contract": r["contract"],
                "pair_proxy_b": (
                    round(min(ls, al) * c["face"] / 1e9, 1)
                    if ls is not None and al is not None
                    else None
                ),
                "gross_short_b": (
                    round(ls * c["face"] / 1e9, 1) if ls is not None else None
                ),
                "net_b": (
                    round((ll - ls) * c["face"] / 1e9, 1)
                    if ls is not None and ll is not None
                    else None
                ),
                "missing_fields": missing_fields,
            }
        )
    out.sort(
        key=lambda item: (
            item["gross_short_b"] is None,
            -(item["gross_short_b"] or 0.0),
        )
    )
    return out


def analyze(tff: pd.DataFrame, dvp_vol: pd.Series) -> dict:
    if tff.empty:
        return {"ok": False, "reason": "no TFF data"}

    # Headline and per-contract table read ONE week. History is built from the
    # UST rows alone: a report date carrying only crowding-panel contracts is
    # not an RV observation, and grouping it in dated the totals a week later
    # than the table.
    ust_candidates = _ust_rows(tff)
    if ust_candidates.empty:
        return {"ok": False, "reason": "no UST contracts in TFF data"}
    ust = _position_rows(ust_candidates, _RV_POSITION_FIELDS)
    if ust.empty:
        return {"ok": False, "reason": "no valid dated UST position rows in TFF data"}
    hist = position_history(ust)
    if hist.empty:
        return {"ok": False, "reason": "no usable UST position metrics in TFF data"}
    asof = pd.Timestamp(ust["date"].max())
    latest = hist.loc[asof] if asof in hist.index else pd.Series(dtype=float)
    coverage_by_date = {}
    expected_contracts: set[str] = set()
    for date, group in ust.groupby("date", sort=True):
        expected_contracts.update(group["contract"].astype(str))
        coverage_by_date[pd.Timestamp(date)] = _metric_coverage(
            group, expected_contracts
        )
    metric_coverage = coverage_by_date[asof]
    current_expected_contracts = tuple(
        metric_coverage["pair_proxy_b"]["expected_contracts"]
    )
    comparable_pair_dates = [
        date
        for date, coverage in coverage_by_date.items()
        if coverage["pair_proxy_b"]["status"] == "complete"
        and tuple(coverage["pair_proxy_b"]["expected_contracts"])
        == current_expected_contracts
    ]
    pair_history = hist.loc[
        hist.index.intersection(pd.DatetimeIndex(comparable_pair_dates)), "pair_b"
    ].dropna()
    pair_now_raw = _finite_or_none(latest.get("pair_b"))
    pair_now = _rounded_or_none(pair_now_raw)
    pair_current_complete = metric_coverage["pair_proxy_b"]["status"] == "complete"

    required_prior_asof = asof - pd.Timedelta(weeks=13)
    prior_pair_coverage = coverage_by_date.get(required_prior_asof, {}).get(
        "pair_proxy_b"
    )
    prior_expected_contracts = (
        tuple(prior_pair_coverage["expected_contracts"])
        if prior_pair_coverage is not None
        else None
    )
    exact_prior_available = (
        pair_now_raw is not None
        and pair_current_complete
        and required_prior_asof in pair_history.index
    )
    chg_13w = (
        float(pair_now_raw - pair_history.loc[required_prior_asof])
        if exact_prior_available
        else None
    )
    if not pair_current_complete:
        change_reason = "current_pair_coverage_incomplete"
    elif pair_now is None:
        change_reason = "current_pair_unavailable"
    elif (
        prior_pair_coverage is not None
        and prior_pair_coverage["status"] == "complete"
        and prior_expected_contracts != current_expected_contracts
    ):
        change_reason = "contract_universe_changed"
    elif not exact_prior_available:
        change_reason = "exact_13_week_report_unavailable"
    else:
        change_reason = None
    pair_change_quality = {
        "status": "complete" if exact_prior_available else "unavailable",
        "current_asof": asof.date().isoformat(),
        "required_prior_asof": required_prior_asof.date().isoformat(),
        "prior_observation_asof": (
            required_prior_asof.date().isoformat() if exact_prior_available else None
        ),
        "current_expected_contracts": list(current_expected_contracts),
        "prior_expected_contracts": (
            list(prior_expected_contracts)
            if prior_expected_contracts is not None
            else None
        ),
        "reason": change_reason,
    }
    size_z = None
    pair_sd = float(pair_history.std()) if len(pair_history) > 1 else np.nan
    if (
        pair_now is not None
        and pair_current_complete
        and np.isfinite(pair_sd)
        and pair_sd > 0
    ):
        size_z = float((pair_history.iloc[-1] - pair_history.mean()) / pair_sd)
    score_eligible = exact_prior_available and size_z is not None

    dvp_values = pd.to_numeric(dvp_vol, errors="coerce").dropna()
    dvp_now = _finite_or_none(dvp_values.iloc[-1]) if not dvp_values.empty else None
    if dvp_now is not None and dvp_now <= 0:
        dvp_now = None
    elif dvp_now is not None and dvp_now > 1e6:
        dvp_now /= 1e9  # OFR volume mnemonics are raw dollars, not $B

    # Margin-shock scenarios: adverse basis moves of 5/15/30 bp.
    gross_short_raw = _finite_or_none(latest.get("gross_short_b"))
    net_raw = _finite_or_none(latest.get("net_b"))
    dv01_raw = _finite_or_none(latest.get("dv01_m"))
    gross_short_now = _rounded_or_none(gross_short_raw)
    net_now = _rounded_or_none(net_raw)
    dv01_now = _rounded_or_none(dv01_raw)
    dv01_scenario_available = (
        metric_coverage["dv01_m_per_bp"]["status"] == "complete"
        and dv01_raw is not None
    )
    unwind_scenario_available = (
        metric_coverage["gross_short_b"]["status"] == "complete"
        and gross_short_raw is not None
    )
    days_scenario_available = unwind_scenario_available and dvp_now is not None
    scenarios = []
    for shock_bp in (5, 15, 30):
        mtm_b = dv01_raw * shock_bp / 1000.0 if dv01_scenario_available else None
        unwind_b = 0.10 * gross_short_raw if unwind_scenario_available else None
        days_of_dvp = (
            unwind_b / dvp_now
            if days_scenario_available and unwind_b is not None and dvp_now is not None
            else None
        )
        scenarios.append(
            {
                "shock_bp": shock_bp,
                "mtm_loss_b": _rounded_or_none(mtm_b),
                "assumed_unwind_b": _rounded_or_none(unwind_b),
                "unwind_days_of_dvp": _rounded_or_none(days_of_dvp, 2),
            }
        )

    scenario_quality = {
        "mtm_loss_b": {
            "status": "complete" if dv01_scenario_available else "unavailable",
            "required_inputs": ["dv01_m_per_bp"],
            "reason": (
                None if dv01_scenario_available else "current_dv01_coverage_incomplete"
            ),
        },
        "assumed_unwind_b": {
            "status": "complete" if unwind_scenario_available else "unavailable",
            "required_inputs": ["gross_short_b"],
            "reason": (
                None
                if unwind_scenario_available
                else "current_gross_short_coverage_incomplete"
            ),
        },
        "unwind_days_of_dvp": {
            "status": "complete" if days_scenario_available else "unavailable",
            "required_inputs": ["gross_short_b", "dvp_volume_b"],
            "reason": (
                None
                if days_scenario_available
                else (
                    "current_gross_short_coverage_incomplete"
                    if not unwind_scenario_available
                    else "dvp_volume_unavailable"
                )
            ),
        },
    }

    fully_complete_mask = _finite_mask(ust, _RV_POSITION_FIELDS)
    metric_masks = [_finite_mask(ust, fields) for fields in _RV_METRIC_FIELDS.values()]
    any_metric = np.logical_or.reduce(metric_masks)
    partial_rows_used = any_metric & ~fully_complete_mask
    coverage_labels = {
        "pair_proxy_b": "pair proxy",
        "gross_short_b": "gross short",
        "net_b": "net",
        "dv01_m_per_bp": "DV01",
    }
    coverage_summary = ", ".join(
        f"{coverage_labels[metric]} "
        f"{record['usable_rows']}/{record['total_rows']} contracts {record['status']}"
        for metric, record in metric_coverage.items()
    )
    published_history = hist.tail(200)
    series_metric_map = {
        "pair_proxy_b": "pair_b",
        "gross_short_b": "gross_short_b",
        "net_b": "net_b",
    }
    series = []
    complete_series_points = {metric: 0 for metric in series_metric_map}
    incomplete_series_points = {metric: 0 for metric in series_metric_map}
    incomparable_series_points = {metric: 0 for metric in series_metric_map}
    for date, row in published_history.iterrows():
        coverage = coverage_by_date[pd.Timestamp(date)]
        values = []
        for metric, history_column in series_metric_map.items():
            comparable_universe = (
                tuple(coverage[metric]["expected_contracts"])
                == current_expected_contracts
            )
            if coverage[metric]["status"] == "complete" and comparable_universe:
                values.append(_rounded_or_none(row[history_column]))
                complete_series_points[metric] += 1
            else:
                values.append(None)
                if coverage[metric]["status"] == "complete":
                    incomparable_series_points[metric] += 1
                else:
                    incomplete_series_points[metric] += 1
        series.append([date.date().isoformat(), *values])

    headline_values = (pair_now, gross_short_now, net_now, dv01_now)
    current_available = any(value is not None for value in headline_values)

    return {
        "_pair_full": pair_history,  # pd.Series for the history layer; stripped from payloads
        "ok": True,
        "asof": asof.date().isoformat(),
        "pair_proxy_b": pair_now,
        "gross_short_b": gross_short_now,
        "net_b": net_now,
        "by_contract": _by_contract_latest(ust, asof),
        "by_contract_asof": asof.date().isoformat(),
        "current_available": current_available,
        "current_reason": (
            None if current_available else "no usable current UST position metrics"
        ),
        "score_eligible": score_eligible,
        "metric_coverage": metric_coverage,
        "input_quality": {
            "ust_rows_received": int(len(ust_candidates)),
            "valid_identity_rows": int(len(ust)),
            "invalid_identity_rows_excluded": int(len(ust_candidates) - len(ust)),
            "complete_ust_rows": int(fully_complete_mask.sum()),
            "partial_ust_rows_used": int(partial_rows_used.sum()),
            "incomplete_ust_rows_excluded": int((~any_metric).sum()),
            "complete_pair_history_dates": int(len(pair_history)),
            "headline_asof": asof.date().isoformat(),
            "headline_metrics": metric_coverage,
        },
        "dv01_m_per_bp": dv01_now,
        "pair_change_13w_b": _rounded_or_none(chg_13w),
        "pair_change_13w_quality": pair_change_quality,
        "size_z": _rounded_or_none(size_z, 2),
        "dvp_volume_b": _rounded_or_none(dvp_now),
        "scenarios": scenarios,
        "scenario_quality": scenario_quality,
        "series": series,
        "series_quality": {
            "policy": "non_complete_aggregates_are_null",
            "coverage_unit": "cumulative expected UST contracts per report date",
            "complete_points": complete_series_points,
            "incomplete_points_nulled": incomplete_series_points,
            "incomparable_universe_points_nulled": incomparable_series_points,
        },
        "method": (
            "TFF futures-only; each metric uses only its own required finite fields, "
            "never zero-fills and never discards valid leveraged-short exposure because "
            "an unrelated asset-manager field is missing; pair=min(levShort,amLong)xface; "
            "DV01 per-contract constants in config; scenarios assume 10% forced unwind "
            "vs DVP daily volume and are withheld unless their required current aggregate "
            "has complete expected-contract coverage; net=(levLong minus levShort)xface. "
            "Current contract coverage: "
            f"{coverage_summary}. The 13-week change requires the exact report date "
            "13 calendar weeks earlier with the same expected-contract universe; "
            "incomplete or universe-incomparable historical aggregates are null chart "
            "gaps. The RV composite score is withheld unless current pair coverage is "
            "complete, exact comparable 13-week growth evidence exists, and its "
            "same-universe own-history z-score is finite"
        ),
    }


def rvxray_score(result: dict) -> float | None:
    """0-100: size percentile vs own history + growth impulse."""
    if not result.get("ok"):
        return 0.0
    if result.get("score_eligible") is not True:
        return None
    sz = result.get("size_z")
    if not isinstance(sz, (int, float)) or not np.isfinite(float(sz)):
        return None
    grow = _finite_or_none(result.get("pair_change_13w_b"))
    if grow is None:
        return None
    base = 100.0 / (1.0 + np.exp(-(sz - 0.8) * 1.3))
    if grow > 50:  # +$50B in 13 weeks = rapid build
        base = min(base + 15.0, 100.0)
    return float(np.clip(base, 0.0, 100.0))


def crowding(tff: pd.DataFrame, lookback_weeks: int = 156) -> dict:
    """Positioning crowding per contract: leveraged-fund NET position as a
    share of open interest, z-scored and percentiled vs its own trailing
    history. Crowded shorts in duration + crowded longs in equities is the
    classic pre-unwind constellation (Apr 2025). T+3 provenance as always."""
    if tff.empty:
        return {"ok": False, "reason": "no TFF data"}
    complete = _complete_position_rows(tff, _CROWDING_POSITION_FIELDS)
    if complete.empty:
        return {"ok": False, "reason": "no complete CFTC positioning rows"}
    out = []
    for contract, grp in complete.groupby("contract"):
        g = grp.sort_values("date")
        oi = g["open_interest_all"]
        net = g["lev_money_positions_long_all"] - g["lev_money_positions_short_all"]
        share = (net / oi.replace(0, np.nan)).dropna()
        if len(share) < 60:
            continue
        hist = share.tail(lookback_weeks)
        cur = float(hist.iloc[-1])
        sd = float(hist.std()) or np.nan
        z = (cur - float(hist.mean())) / sd if np.isfinite(sd) else 0.0
        pctl = float((hist <= cur).mean() * 100.0)
        out.append(
            {
                "contract": contract,
                "lev_net_share_oi": round(cur, 3),
                "z": round(float(z), 2),
                "pctl": round(pctl, 0),
                "asof": g["date"].iloc[-1].date().isoformat(),
            }
        )
    if not out:
        return {"ok": False, "reason": "no contracts with enough history"}
    out.sort(key=lambda r: -abs(r["z"]))
    return {
        "ok": True,
        "rows": out,
        "method": (
            "leveraged-fund net position / open interest per contract; z and percentile "
            f"vs trailing {lookback_weeks}w; incomplete/non-finite CFTC rows are excluded, "
            "never zero-filled; |z| ranks the board (extremes = crowding)"
        ),
    }
