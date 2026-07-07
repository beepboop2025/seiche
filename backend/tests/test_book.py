"""Signal-book tests — the invariants that make the P&L believable.

Same philosophy as test_engines.py: the tests that matter are not "does it
run" but "does it refuse to cheat" — no look-ahead in the stacker or the
book, the execution lag enforced, costs actually charged, the verdict
confessing on noise, and the hash chain breaking loudly when a published
record is edited.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche import publisher
from seiche.config import (
    BACKTEST_EVENT_FWD_D,
    BACKTEST_SPIKE_BP,
    BOOK_DISPERSION_GATE,
    BOOK_SLEEVES,
)
from seiche.engines import book, farbasin, stacker


def _bdays(n: int, start: str = "2019-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


@pytest.fixture()
def rng():
    return np.random.default_rng(11)


def _synthetic_returns(rng, n=1300) -> pd.DataFrame:
    idx = _bdays(n)
    y2 = pd.Series(2.0 + np.cumsum(rng.normal(0, 0.02, n)), index=idx).clip(0.1)
    y10 = pd.Series(3.0 + np.cumsum(rng.normal(0, 0.03, n)), index=idx).clip(0.1)
    spx = pd.Series(3000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n))), index=idx)
    btc = pd.Series(30000 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, n))), index=idx)
    tb = pd.Series(4.0, index=idx)
    return book.build_returns(y2, y10, spx, btc, tb)


# --------------------------------------------------------------------------
# Book: returns construction
# --------------------------------------------------------------------------

def test_duration_proxy_signs():
    idx = _bdays(300)
    rising = pd.Series(np.linspace(2.0, 4.0, len(idx)), index=idx)   # yields up
    flat = pd.Series(3.0, index=idx)
    spx = pd.Series(100.0, index=idx)
    btc = pd.Series(100.0, index=idx)
    tb = pd.Series(4.0, index=idx)
    r = book.build_returns(rising, rising, spx, btc, tb)
    assert r["ust10y"].dropna().mean() < 0, "rising yields must hurt duration"
    r2 = book.build_returns(flat, flat, spx, btc, tb)
    carry = r2["ust10y"].dropna().mean()
    assert abs(carry - 0.03 / 252.0) < 1e-5, "flat yields must earn ~y/252 carry"


# --------------------------------------------------------------------------
# Book: THE lag invariant — signal at t earns returns at t+1
# --------------------------------------------------------------------------

def test_signal_execution_lag(rng):
    rets = _synthetic_returns(rng, 400)
    idx = rets.index
    rets.loc[:, "spx"] = 0.0
    rets.loc[:, "cash"] = 0.0
    k = 200
    rets.iloc[k, rets.columns.get_loc("spx")] = 0.10  # one huge day

    w_late = pd.DataFrame(0.0, index=idx, columns=book.SLEEVES)
    w_late.loc[idx[k]:, "spx"] = 1.0            # signal turns on AT the move
    late = book.pnl(w_late, rets, {s: 0.0 for s in book.SLEEVES})
    assert abs(late["net"].iloc[k]) < 1e-12, "a signal formed on the move's day must NOT earn it"

    w_early = pd.DataFrame(0.0, index=idx, columns=book.SLEEVES)
    w_early.loc[idx[k - 1]:, "spx"] = 1.0       # signal on the day before
    early = book.pnl(w_early, rets, {s: 0.0 for s in book.SLEEVES})
    assert abs(early["net"].iloc[k] - 0.10) < 1e-12


def test_costs_and_turnover(rng):
    rets = _synthetic_returns(rng, 500)
    w = pd.DataFrame(0.0, index=rets.index, columns=book.SLEEVES)
    w.iloc[100:300, w.columns.get_loc("spx")] = 1.0  # one round trip
    free = book.pnl(w, rets, {s: 0.0 for s in book.SLEEVES})
    paid = book.pnl(w, rets)
    double = book.pnl(w, rets, {s: 2 * BOOK_SLEEVES[s]["tcost_bp"] for s in book.SLEEVES})
    assert free["costs"].sum() == 0.0
    exp = 2.0 * BOOK_SLEEVES["spx"]["tcost_bp"] / 1e4  # in + out
    assert abs(paid["costs"].sum() - exp) < 1e-12
    assert abs(double["costs"].sum() - 2 * exp) < 1e-12
    assert float(free["net"].sum() - paid["net"].sum()) == pytest.approx(exp)


def test_dispersion_gate_forces_neutral():
    idx = _bdays(400)
    p = pd.Series(0.9, index=idx)                    # screaming risk-off
    disp = pd.Series(0.0, index=idx)
    disp.iloc[300:310] = BOOK_DISPERSION_GATE + 0.2  # fleet splits
    tell = pd.Series(0.0, index=idx)
    st = book.stance_series(p, disp, tell)
    assert (st.iloc[250:300] == "risk_off").all()
    assert (st.iloc[300:310] == "neutral").all(), "disagreement must gate to neutral"
    assert (st.iloc[310:] == "risk_off").all()


# --------------------------------------------------------------------------
# Book: no look-ahead (truncation invariance of weights and P&L)
# --------------------------------------------------------------------------

def test_book_no_look_ahead(rng):
    rets = _synthetic_returns(rng, 1000)
    idx = rets.index
    p = pd.Series(np.clip(rng.uniform(0, 0.6, len(idx)), 0, 1), index=idx)
    disp = pd.Series(rng.uniform(0, 0.2, len(idx)), index=idx)
    tell = pd.Series(rng.normal(0, 30, len(idx)), index=idx)

    st = book.stance_series(p, disp, tell)
    w = book.size_positions(st, rets)
    net = book.pnl(w, rets)["net"]

    cut = idx[-120]
    st_t = book.stance_series(p[p.index <= cut], disp[disp.index <= cut], tell[tell.index <= cut])
    w_t = book.size_positions(st_t, rets[rets.index <= cut])
    net_t = book.pnl(w_t, rets[rets.index <= cut])["net"]

    overlap = w_t.index
    assert np.allclose(w.loc[overlap].to_numpy(), w_t.to_numpy(), atol=1e-12), \
        "weights at T changed when future data was appended — look-ahead leak"
    assert np.allclose(net.loc[overlap].fillna(0).to_numpy(), net_t.fillna(0).to_numpy(), atol=1e-12)


# --------------------------------------------------------------------------
# Book: the verdict must confess on noise
# --------------------------------------------------------------------------

def test_verdict_confesses_on_noise(rng):
    rets = _synthetic_returns(rng, 1300)
    idx = rets.index
    p = pd.Series(rng.uniform(0, 0.6, len(idx)), index=idx)  # coin-flip signal
    members = pd.DataFrame({"m1": p}, index=idx)
    disp = pd.Series(0.0, index=idx)
    tell = pd.Series(rng.normal(0, 30, len(idx)), index=idx)
    r = book.run(p, members, disp, tell, rets, pit_records=[])
    assert r["ok"]
    assert "does NOT beat" in r["backtest"]["verdict"], \
        f"a coin-flip book must confess: {r['backtest']['verdict']}"


def test_sharpe_ci_brackets_point_estimate(rng):
    x = pd.Series(rng.normal(0.0004, 0.006, 1200), index=_bdays(1200))
    sc = book.sharpe_ci(x, n=500)
    assert sc["ci95"][0] <= sc["sharpe"] <= sc["ci95"][1]
    assert sc["nw_tstat"] is not None


# --------------------------------------------------------------------------
# Stacker
# --------------------------------------------------------------------------

def _planted_members(rng, n=1300):
    idx = _bdays(n)
    y = pd.Series(0.0, index=idx)
    for s in range(300, n - 10, 60):
        y.iloc[s : s + 3] = 1.0
    good = (y * 0.7 + 0.1 + rng.normal(0, 0.05, n)).clip(0, 1)
    noise1 = pd.Series(rng.uniform(0, 1, n), index=idx)
    noise2 = pd.Series(rng.uniform(0, 1, n), index=idx)
    M = pd.DataFrame({"rule": good, "ml": noise1, "tide": noise2}, index=idx)
    return M, y


def test_stacker_uses_informative_member(rng):
    M, y = _planted_members(rng)
    r = stacker.walk_forward_stack(M, y)
    assert r["ok"]
    v = r["validation"]
    assert v[f"auroc_{r['published']}"] >= 0.75, \
        f"an ensemble holding a near-perfect member must rank well: {v}"
    # publish-naive consistency: the published stream is the OOS-Brier winner
    if r["published"] == "stack":
        assert v["brier_stack"] < v["brier_mean"]
    else:
        assert v["brier_mean"] <= v["brier_stack"]


def test_stacker_no_look_ahead(rng):
    M, y = _planted_members(rng, 1200)
    full = stacker.walk_forward_stack(M, y)
    assert full["ok"]
    p_full = full["_p"]
    cut = M.index[-60]
    Mt, yt = M[M.index <= cut], y[y.index <= cut]
    # labels near the cut have open windows in the truncated world — NaN them
    yt = yt.copy()
    yt.iloc[-BACKTEST_EVENT_FWD_D:] = np.nan
    part = stacker.walk_forward_stack(Mt, yt)
    assert part["ok"]
    p_part = part["_p"]
    t = p_part.index[-1]
    assert t in p_full.index
    assert abs(float(p_full.loc[t]) - float(p_part.loc[t])) < 1e-9, \
        "stacked probability at T changed when the future was appended"


def test_stacker_label_matches_proof_event_definition(rng):
    idx = _bdays(600)
    spread = pd.Series(rng.normal(0, 2, len(idx)), index=idx)
    spread.iloc[400] += 25.0
    y = stacker.event_labels(spread, idx)
    # independent first-principles reconstruction of the PROOF definition:
    # pop = spread − trailing 5bd median SHIFTED BY 1 (yesterday's yardstick —
    # the event day's own print must not contaminate its baseline; see
    # backtest._funding_events / backtest.pop_bp)
    pop = spread - spread.rolling(5, min_periods=3).median().shift(1)
    fwd = pd.concat([pop.shift(-k) for k in range(1, BACKTEST_EVENT_FWD_D + 1)], axis=1).max(axis=1)
    expect = (fwd >= BACKTEST_SPIKE_BP).astype(float)
    expect[fwd.isna()] = np.nan
    pd.testing.assert_series_equal(y, expect, check_names=False)
    assert y.iloc[395:400].sum() >= 1  # the spike is visible in the run-up labels


# --------------------------------------------------------------------------
# Hash chain
# --------------------------------------------------------------------------

def test_hash_chain_append_and_tamper():
    pub = publisher.HashChainPublisher()
    history: list[dict] = []
    for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
        rec = pub.publish({"date": day, "stance": "neutral", "positions": []}, history)
        history.append(rec)
    ok, msg = publisher.verify_chain(history)
    assert ok, msg
    history[1]["stance"] = "risk_on"  # rewrite history
    ok, msg = publisher.verify_chain(history)
    assert not ok, "editing a published record must break the chain"


# --------------------------------------------------------------------------
# Far Basin (Palimpsest)
# --------------------------------------------------------------------------

def test_farbasin_quarantines_young_channel():
    idx = pd.bdate_range("2026-07-01", periods=5)
    fear = pd.Series([1.7, 2.1, 1.9, 2.4, 2.9], index=idx)
    r = farbasin.analyze(fear, None, None, {"top": []})
    assert r["ok"]
    assert not r["status"]["backtestable"], "a 5-day-old channel must be quarantined"
    assert "ACCRUING" in r["status"]["note"]
    assert r["channels"]["fear"]["last"] == 2.9
