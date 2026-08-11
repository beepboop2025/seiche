"""Shared test fixtures.

The MCP tests exercise tools against a canned snapshot (no network). It lives
here as fixtures rather than a cross-imported module constant so collection
works regardless of the working directory pytest is invoked from (the box runs
`pytest backend/tests` from the repo root, where `tests` is not importable).
"""

import pytest


# A snapshot shaped like assemble.snapshot(), trimmed to the fields the tools read.
_FAKE_SNAP = {
    "generated_at": "2026-07-10T00:00:00Z",
    "version": "0.2.0-test",
    "faults": [],
    "provenance": {"WALCL": {"fresh": True, "age_h": 3}},
    "engines": {
        "composite": {
            "value": 41.0,
            "regime": "EROSION",
            "coverage_pct": 96,
            "decomposition": [
                {"component": "repo", "score": 55.0, "status": "OK"},
                {"component": "reserves", "score": 30.0, "status": "OK"},
            ],
        },
        "weather": {"crunch_windows": [{"date": "2026-07-31", "reason": "month-end + settlement"}]},
        "oilfunding": {
            "ok": True,
            "asof": "2026-07-09",
            "live": {
                "wti": {"price_usd_per_bbl": 81.5, "change_5d_usd": 2.4,
                        "change_20d_pct": 6.2, "asof": "2026-07-09"},
                "brent": {"price_usd_per_bbl": 85.1, "change_5d_usd": 2.0,
                          "change_20d_pct": 5.4, "asof": "2026-07-09"},
                "cp_nonfinancial": {"spread_bp": 24.0, "change_20d_bp": 3.0,
                                    "percentile_3y": 68.0, "asof": "2026-07-09"},
                "cp_financial": {"spread_bp": 31.0, "change_20d_bp": 2.0,
                                 "percentile_3y": 61.0, "asof": "2026-07-09"},
                "sofr_iorb": {"spread_bp": 2.0, "change_20d_bp": 1.0,
                              "percentile_3y": 55.0, "asof": "2026-07-09"},
                "inr": {"per_usd": 84.2, "change_20d_pct": 0.7,
                        "change_60d_pct": 1.8, "asof": "2026-07-09"},
                "inflation_policy": {"energy_cpi_yoy_pct": 3.2,
                                     "core_cpi_yoy_pct": 2.7, "iorb_pct": 4.4},
                "official_dollar_parking": {
                    "treasury_custody_change_52w_b": 82.0,
                    "foreign_rrp_change_52w_b": -18.0,
                },
                "cushing": {
                    "stocks_m_bbl": 21.0,
                    "change_1w_m_bbl": -0.7,
                    "change_8w_m_bbl": -4.2,
                    "fill_of_last_working_capacity_pct": 26.8,
                    "buffer_to_20m_reference_m_bbl": 1.0,
                    "asof": "2026-07-04",
                },
                "brent_wti_spread": {
                    "brent_minus_wti_usd_per_bbl": 3.6,
                    "average_5d_usd_per_bbl": 3.3,
                    "negative_days_last_60_observations": 0,
                    "asof": "2026-07-09",
                },
            },
            "charts": {"scatter": {
                "fit": {"n": 120, "correlation": 0.42,
                        "slope_bp_per_usd": 0.31, "r_squared": 0.176},
                "x_label": "5bd WTI change, USD per barrel",
                "y_label": "5bd nonfinancial CP−bill change, bp",
            }},
            "scenario": {"assumptions": {"tenor_days": 90.0},
                         "outputs": {"margin": {
                             "same_day_liquidity_demand_usd": 12_000_000}}},
            "market_structure": {
                "evidence_mode": "observed_where_available_reference_where_dated",
                "cushing": {
                    "working_capacity_m_bbl": 78.410,
                    "capacity_asof": "2024-03-31",
                    "capacity_status": "last official observation; publication discontinued",
                    "stress_reference_m_bbl": 20.0,
                    "stress_reference_status": "analytical reference, not a universal floor",
                    "delivery_role": "physical delivery hub for NYMEX WTI futures",
                },
                "benchmark_architecture": [
                    {"benchmark": "WTI", "settlement": "physical delivery at Cushing, Oklahoma"},
                    {"benchmark": "Brent", "settlement": "cargo-based benchmark complex"},
                ],
                "hub_taxonomy": [{"type": "inland delivery hub", "examples": ["Cushing"]}],
                "control_stack": [{"layer": "local deliverability", "nodes": "Cushing · WTI basis"}],
                "transmission_order": ["chokepoint risk", "insurance and freight", "benchmark spreads"],
                "chokepoints": {
                    "rows": [{"name": "Strait of Hormuz", "q1_2026_mbd": 14.6}],
                    "unit": "million barrels per day",
                    "latest_period": "1Q26",
                    "live_status": "not asserted by Seiche without a reliable live transit feed",
                },
                "india": {
                    "crude_import_dependence_pct": 88.5,
                    "non_hormuz_crude_routing_pct": 70.0,
                },
                "principles": ["WTI is a claim on a specified delivery hub."],
            },
            "channel_directions": {"margin": "oil_price_move_to_same_day_cash"},
            "sources": [{"series": "WTI spot", "source": "EIA via FRED"}],
            "caveats": ["scenario inputs are assumptions, not observations"],
        },
        "ballast": {
            "ok": True,
            "schema": "seiche.ballast.v1",
            "asof": "2026-07-09",
            "headline": {
                "state": "TIGHT",
                "worst_channel_percentile": 91.0,
                "dominant_channel": {
                    "channel": "mark_to_market",
                    "label": "WTI gross mark displacement",
                    "percentile": 91.0,
                },
                "coverage_pct": 100.0,
                "composite_status": "never_enters_seiche_composite",
            },
            "contracts": [{
                "key": "WTI",
                "label": "WTI physical crude",
                "report_asof": "2026-07-07",
                "available_asof": "2026-07-10",
                "report_lag": "Tuesday positions, normally published Friday (T+3)",
                "price_proxy": {"change_since_prior_report": 3.0},
                "open_interest": {"contracts": 1_800_000},
                "cash_transfer_scale": {
                    "gross_mark_displacement_usd": 5_400_000_000,
                    "status": "derived_gross_scale_not_observed_margin_call",
                },
                "positioning": {"top4_paying_side_pct": 23.0},
                "history": {"columns": ["date"], "rows": [["2026-07-07"]]},
            }],
            "inventory": {"stocks_million_bbl": 418.2},
            "funding": {"sofr_iorb": {"spread_bp": 2.0}},
            "pressure_ledger": [{"channel": "mark_to_market", "percentile": 91.0}],
            "coverage": {"available_pct": 100.0, "boundaries": []},
            "handoffs": {"undertow": {"boundary": "no live depth"}},
            "sources": [{"source": "CFTC Disaggregated COT futures-only"}],
            "caveats": ["gross scale is not an observed margin call"],
        },
        "estuary": {
            "ok": True,
            "asof": "2026-07-09",
            "headline": {
                "upstream_pressure": 72.0,
                "fx_pressure": 75.0,
                "materials_pressure": 68.0,
                "funding_priced": 43.0,
                "transmission_gap": 29.0,
                "regime": "PRESSURE HELD UPSTREAM",
                "verdict": "FX and physical cash pressure lead funding pricing.",
                "coverage_pct": 96.0,
                "context_only": True,
            },
            "fx": {
                "broad": {"index": 121.2, "change_20d_pct": 1.3,
                          "pressure_percentile": 78.0, "asof": "2026-07-09"},
                "advanced": {"change_20d_pct": 0.8, "pressure_percentile": 64.0},
                "emerging": {"change_20d_pct": 2.1, "pressure_percentile": 86.0},
                "median_pair_depreciation_percentile": 77.0,
                "median_pair_volatility_percentile": 71.0,
                "currencies": [{"key": "INR", "label": "Indian rupee",
                                "pressure": 89.0}],
            },
            "materials": {
                "categories": [{"category": "energy", "pressure": 82.0}],
                "instruments": [{"key": "WTI", "label": "WTI crude",
                                 "pressure": 88.0}],
                "breadth_higher_pct": 60.0,
            },
            "funding": {"markets": [{"key": "cp_nonfinancial",
                                     "label": "3m nonfinancial CP − Treasury",
                                     "pressure": 48.0}]},
            "passage": {
                "edges": [{"source": "EM dollar", "target": "SOFR−IORB",
                           "lag_bd": 3, "status": "earned", "corr_holdout": 0.31}],
                "earned": 1, "tentative": 1, "not_earned": 4,
                "doctrine": "selected on 60%; earned on untouched 40%",
            },
            "analogs": {"neighbors": []},
            "dollar_system": {"swap_lines": {"outstanding_usd_m": 500.0}},
            "settlement_structure": {"survey_asof": "2025-04"},
            "scenario": {"assumptions": {"adverse_fx_move_pct": 2.0},
                         "outputs": {"fx": {"replacement_cost_shock_usd": 10_000_000}}},
            "coverage_matrix": [{"aspect": "cross-currency basis",
                                 "status": "out_of_scope"}],
            "sources": [{"layer": "FX spot", "source": "Federal Reserve H.10"}],
            "caveats": ["The gap is context, not a probability."],
        },
    },
    "deep": {
        "tell": {
            "ok": True,
            "tell": 12.0,
            "plumbing_pctl": 58.0,
            "market_pctl": 46.0,
            "reading": "plumbing leads price",
        },
        "backtest": {
            "ok": True,
            "sample": {"start": "2018-01-01", "end": "2026-07-01", "n_events": 14},
            "event_capture": {"recall": 0.79, "precision_runs": 0.61, "base_rate": 0.06,
                              "median_lead_d": 42, "runs_hit": 8, "n_alert_runs": 13},
            "orthogonal": {"ok": True, "event_capture": {"recall": 0.69}},
            "episodes": [{"date": "2019-09-17", "episode": "repo spike", "in_sample": True,
                          "first_alert_lead_d": 5, "max_pctl_30d_before": 98}],
            "caveats": ["small event count; CIs are wide"],
        },
        "tidetables": {
            "ok": True,
            "event_odds": {"p": 0.4, "n": 25, "base_rate": 0.06, "lift": 6.7, "ci95": [0.22, 0.61]},
            "novelty": {"verdict": "charted", "pctl": 44},
            "skill": {"ok": True, "brier": 0.05, "brier_climatology": 0.06},
            "analogs": [{"end_date": "2019-09-10", "distance": 0.21, "max_move_5bd_bp": 30.0,
                         "event_within_5bd": True, "episode": "pre-repo-spike"}],
            "fan": [{"p25": 2, "median": 5, "p75": 12}],
            "horizon_bd": 21,
            "spread_now_bp": 4,
        },
        "swell": {"ok": True, "event_by_horizon": {"h5": 0.18, "h10": 0.25, "h21": 0.4},
                  "peak": {"date": "2026-07-31", "bucket": "month-end", "p10": 0.3},
                  "validation": {"ok": True, "auroc": 0.82, "brier": 0.04, "brier_climatology": 0.06}},
        "bathymetry": {"ok": True, "p_by_horizon": {"h1": 0.02, "h5": 0.15, "h10": 0.22},
                       "mfpt_bd": 38, "state_now": {"in_event_bin": False},
                       "validation": {"ok": True, "auroc": 0.8}},
        "ml": {"ok": True, "p_event_5bd": 0.17, "verdict": "elevated but not acute",
               "validation": {"auroc": 0.81, "brier": 0.04}},
        "book": {
            "ok": True,
            "today": {"stance": "risk_off", "rationale": "erosion + month-end",
                      "positions": [{"label": "front-end steepener", "weight": 0.3,
                                     "direction": "long", "vol_ann_pct": 8, "tcost_bp": 2}]},
            "backtest": {"sample": {"start": "2018", "end": "2026"}, "sharpe": 0.9, "verdict": "positive net of costs"},
            "live": {"n_days": 30, "since": "2026-06-10", "cum_return_pct": 1.2, "note": "early"},
            "caveats": [],
        },
        "stacker": {"ok": True, "p_now": 0.19, "published": "0.19", "dispersion_now": 0.03, "verdict": "consensus"},
        "markov": {"ok": True, "current_regime": "EROSION",
                   "p_reach_stress": {"h5": 0.0, "h10": 0.01, "h21": 0.03}, "expected_dwell_bd": 61.0},
        "oujump": {"ok": True, "level_now": 44.7, "fit": {"half_life_bd": 112.3},
                   "horizons": [{"h": 5, "p_above_stress": 0.0}, {"h": 21, "p_above_stress": 0.01}]},
        "montecarlo": {"ok": True, "level_now": 44.7,
                       "fan": [{"h": 21, "p10": 33.8, "median": 38.8, "p90": 45.4}],
                       "p_touch_stress": {"h5": 0.0, "h10": 0.0, "h21": 0.001},
                       "p_back_to_calm": {"h5": 0.0, "h10": 0.02, "h21": 0.08}},
    },
    "navigator": {"ok": True, "p_event_5bd": 0.2, "asof": "2026-07-10", "rationale": "test"},
}

_ASOF_SNAP = {
    "ok": True,
    "asof": "2019-09-17",
    "engines": {
        "composite": {"value": 88.0, "regime": "STRESS", "coverage_pct": 92,
                      "decomposition": [{"component": "repo", "score": 99.0, "status": "OK"}]},
        "weather": {"crunch_windows": []},
    },
    "vintage_note": "final-vintage construction-PIT reconstruction",
}


@pytest.fixture()
def fake_snap():
    return _FAKE_SNAP


@pytest.fixture()
def asof_snap():
    return _ASOF_SNAP
