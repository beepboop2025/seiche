"""CP Sentinel: planted narrowing must be detected and beaten against the
placebo, pure noise must NOT print a channel, closed-event rows must be
identical under truncation (no look-ahead), and thin catalogs must stay ok
with 'insufficient events' instead of refusing.

The two tests that matter most:
  - the planted flight-to-quality world: every exploit is followed by a dip
    in the CP spread -> hit_rate 1.0 and a placebo percentile above 95;
  - the pure-noise world: identical exploit calendar, no planted response ->
    the placebo eats the min-over-10bd selection bias and the verdict stays
    'no evidence yet'.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

# Leak canary (Bloomberg pytest-memray): inert unless pytest runs with
# --memray (CI does; local dev may not).
pytestmark = pytest.mark.limit_memory("256 MB")

from seiche.engines import cpsentinel as cps

N = 1500
# last exploit sits 5bd before the sample edge: its +10bd window is still
# open at asof (censored, excluded from the hit rate, drives live state)
LOCS = (300, 500, 700, 900, 1100, N - 6)


def _grid(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2020-01-01", periods=n)


def _hacks(idx: pd.DatetimeIndex, locs=LOCS) -> pd.Series:
    """Daily exploit-loss series (calendar-day grid, zeros on quiet days)."""
    hacks = pd.Series(0.0, index=pd.date_range(idx[0], idx[-1], freq="D"))
    for loc in locs:
        hacks.loc[idx[loc]] += 60_000_000.0
    return hacks


def _planted_world(seed: int = 42):
    """CP spread of mean 20bp, sigma 1bp, with a planted 6bp narrowing for
    8bd after every exploit — the paper's flight-to-quality made synthetic."""
    idx = _grid(N)
    rng = np.random.default_rng(seed)
    cp = pd.Series(rng.normal(20.0, 1.0, N), index=idx)
    for loc in LOCS:
        cp.iloc[loc + 1 : loc + 9] -= 6.0
    return _hacks(idx), cp


def _noise_world(seed: int = 7):
    """Identical exploit calendar on pure noise — nothing to find."""
    idx = _grid(N)
    rng = np.random.default_rng(seed)
    cp = pd.Series(rng.normal(20.0, 1.0, N), index=idx)
    return _hacks(idx), cp


# ---------------------------------------------------------------------------
# 1. Planted narrowing -> full detection, placebo beaten
# ---------------------------------------------------------------------------

def test_planted_narrowing_detected_and_beats_placebo():
    hacks, cp = _planted_world()
    r = cps.analyze(hacks, cp)
    assert r["ok"]
    assert r["n_events"] == len(LOCS)
    assert r["n_events_scored"] == len(LOCS) - 1  # the last window is still open
    assert r["hit_rate"] == 1.0
    scored = [e for e in r["events"] if e["hit"] is not None]
    assert all(e["hit"] is True for e in scored)
    assert all(e["change_bp"] < -3.0 for e in scored), \
        "a planted 6bp dip must read as a large negative signed change"
    assert r["events"][-1]["window_open"] is True and r["events"][-1]["hit"] is None
    assert r["placebo_percentile"] is not None and r["placebo_percentile"] > 95.0
    assert r["placebo"]["hit_rate_mean"] < 0.85, \
        "even the biased placebo calendar must stay well short of the planted 1.0"
    assert r["verdict"] == "channel active"
    # the per-event table carries the signed path vs the trailing median
    e0 = r["events"][0]
    assert e0["path_offsets_bd"] == list(range(-5, 11))
    assert len(e0["path_bp_vs_trail_med"]) == 16
    assert min(e0["path_bp_vs_trail_med"][6:]) < -3.0  # post window dips


# ---------------------------------------------------------------------------
# 2. Pure noise -> the placebo eats the selection bias, no channel printed
# ---------------------------------------------------------------------------

def test_pure_noise_prints_no_channel():
    hacks, cp = _noise_world()
    r = cps.analyze(hacks, cp)
    assert r["ok"]
    assert r["n_events"] == len(LOCS)
    assert r["hit_rate"] is not None and r["hit_rate"] <= 0.6
    assert r["placebo_percentile"] is not None and r["placebo_percentile"] < 95.0
    # the placebo floor sits high (~0.6-0.8): min-over-10bd hands the random
    # calendar frequent false "narrowing" — the percentile, not the raw hit
    # rate, is the honest statistic
    assert r["placebo"]["hit_rate_mean"] >= 0.4
    assert r["verdict"] == "no evidence yet"


# ---------------------------------------------------------------------------
# 3. Declustering: a burst of exploit days inside 5bd is ONE event (max kept)
# ---------------------------------------------------------------------------

def test_declustering_keeps_cluster_max():
    idx = _grid(400)
    rng = np.random.default_rng(3)
    cp = pd.Series(rng.normal(20.0, 1.0, 400), index=idx)
    hacks = pd.Series(0.0, index=pd.date_range(idx[0], idx[-1], freq="D"))
    hacks.loc[idx[200]] = 30_000_000.0
    hacks.loc[idx[202]] = 90_000_000.0  # same cluster (2bd later), the max
    hacks.loc[idx[204]] = 40_000_000.0  # same cluster
    hacks.loc[idx[350]] = 55_000_000.0  # far away: its own event
    r = cps.analyze(hacks, cp)
    assert r["ok"]
    assert r["n_events"] == 2, "5bd-clustered exploit days must collapse to one event"
    big = r["events"][0]
    assert big["date"] == idx[202].date().isoformat()
    assert big["exploit_usd"] == 90_000_000.0
    assert r["verdict"] == "insufficient events"  # 2 < CPS_MIN_EVENTS


# ---------------------------------------------------------------------------
# 4. Truncation equality: closed-event rows never change when future arrives
# ---------------------------------------------------------------------------

def test_truncation_no_look_ahead():
    hacks, cp = _planted_world()
    k = 1200  # keeps events at 300..1100 closed; the N-6 event has no coverage yet
    full = cps.analyze(hacks, cp)
    trunc = cps.analyze(hacks[hacks.index <= cp.index[k - 1]], cp.iloc[:k])
    assert full["ok"] and trunc["ok"]
    assert trunc["n_events"] == 5
    assert trunc["events"] == full["events"][:5], \
        "a closed event-study row changed when future data arrived — look-ahead leak"
    assert trunc["hit_rate_expanding"] == full["hit_rate_expanding"][:5]
    assert trunc["live"]["cp_spread_bp"] == round(float(cp.iloc[k - 1]), 2)


# ---------------------------------------------------------------------------
# 5. Thin catalogs stay ok with 'insufficient events' (NOT a refusal)
# ---------------------------------------------------------------------------

def test_fewer_than_three_events_is_not_a_refusal():
    idx = _grid(N)
    rng = np.random.default_rng(11)
    cp = pd.Series(rng.normal(20.0, 1.0, N), index=idx)
    hacks = _hacks(idx, locs=(400, 900))
    r = cps.analyze(hacks, cp)
    assert r["ok"]
    assert r["verdict"] == "insufficient events"
    assert r["hit_rate"] is None and r["placebo_percentile"] is None
    assert len(r["events"]) == 2  # the case table still publishes
    assert r["live"]["cp_spread_bp"] is not None


def test_refuses_thin_spread_history():
    idx = _grid(40)
    rng = np.random.default_rng(1)
    cp = pd.Series(rng.normal(20.0, 1.0, 40), index=idx)
    hacks = _hacks(idx, locs=(20,))
    r = cps.analyze(hacks, cp)
    assert not r["ok"]
    assert "insufficient" in r["reason"]


# ---------------------------------------------------------------------------
# 6. Payload contract: json-safe, house keys, context engine (no score)
# ---------------------------------------------------------------------------

def test_payload_json_safe():
    hacks, cp = _planted_world()
    out = cps.analyze(hacks, cp)
    blob = json.dumps(out)
    assert len(blob) > 0
    required = {"ok", "asof", "method", "caveats", "hit_rate",
                "placebo_percentile", "events", "live", "verdict"}
    assert required <= set(out), f"missing house keys: {required - set(out)}"
    assert "score" not in out, "context engines emit no score key (doctrine)"
    assert "arXiv:2601.08263" in out["method"]
    assert "associational event-study, not causal" in out["method"]
    assert out["asof"] == cp.index[-1].date().isoformat()
    live = out["live"]
    assert set(live) >= {"cp_spread_bp", "level_pctl", "days_since_big_exploit",
                         "window_active", "last_event"}
    assert live["window_active"] is True  # the 1300 event's +10bd window is open
    assert out["caveats"] and all(isinstance(c, str) for c in out["caveats"])
    # determinism: the seeded placebo recomputes identically
    assert json.dumps(cps.analyze(hacks, cp), sort_keys=True) == json.dumps(out, sort_keys=True)
