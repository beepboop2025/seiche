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
    },
    "deep": {
        "tell": {"ok": True, "tell": 12.0},
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
    "vintage_note": "reconstructed point-in-time",
}


@pytest.fixture()
def fake_snap():
    return _FAKE_SNAP


@pytest.fixture()
def asof_snap():
    return _ASOF_SNAP
