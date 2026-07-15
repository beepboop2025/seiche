"""Diebold-Yilmaz spillover engine. The connectedness table has a testable
ground truth: build a system with a KNOWN transmitter and assert the directional
decomposition names it; check the math invariants (rows sum to 1, diagonal is
own-share) and the honest-degradation guards."""
import numpy as np
import pandas as pd
import pytest

from seiche.engines import spillover


def _system(seed, n=800, coupling=0.85):
    """SOURCE drives FOLLOWER with a one-day lag; INDEP is independent."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-01", periods=n)
    a = np.cumsum(rng.standard_normal(n))
    da = np.diff(a, prepend=a[0])
    b = np.zeros(n)
    for t in range(1, n):
        b[t] = b[t - 1] + coupling * da[t - 1] + 0.2 * rng.standard_normal()
    c = np.cumsum(rng.standard_normal(n))
    return {"SOURCE": pd.Series(a, idx), "FOLLOWER": pd.Series(b, idx),
            "INDEP": pd.Series(c, idx)}


# ── the ground-truth recovery ───────────────────────────────────────────────────

def test_recovers_the_net_transmitter():
    r = spillover.analyze(_system(0))
    assert r["ok"]
    assert r["source"] == "SOURCE"        # the driver is the net transmitter
    assert r["sink"] == "FOLLOWER"        # the follower is the net receiver
    net = {d["node"]: d["net"] for d in r["directional"]}
    assert net["SOURCE"] > 0 > net["FOLLOWER"]


def test_directional_is_stable_across_seeds():
    for seed in (1, 2, 3):
        r = spillover.analyze(_system(seed))
        assert r["source"] == "SOURCE"


def test_stronger_coupling_raises_total_connectedness():
    weak = spillover.analyze(_system(0, coupling=0.2))["total_connectedness"]
    strong = spillover.analyze(_system(0, coupling=0.9))["total_connectedness"]
    assert strong > weak


# ── FEVD math invariants ────────────────────────────────────────────────────────

def test_gfevd_rows_sum_to_one():
    Y = np.random.default_rng(4).standard_normal((500, 3))
    A, Sigma = spillover._var_ols(Y, 2)
    table = spillover.gfevd(A, Sigma, 10)
    assert np.allclose(table.sum(axis=1), 1.0)
    assert (table >= -1e-9).all()


def test_independent_system_has_low_connectedness():
    """Three independent random walks should share almost no forecast-error
    variance — total connectedness near its floor."""
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2022-01-01", periods=700)
    sm = {n: pd.Series(np.cumsum(rng.standard_normal(700)), idx)
          for n in ("X", "Y", "Z")}
    r = spillover.analyze(sm)
    assert r["ok"]
    assert r["total_connectedness"] < 20  # mostly own-variance


def test_vma_first_term_is_identity():
    A = [np.array([[0.5, 0.0], [0.3, 0.4]])]
    theta = spillover._vma(A, 5)
    assert np.allclose(theta[0], np.eye(2))
    assert np.allclose(theta[1], A[0])  # Theta_1 = A_1


# ── honest degradation ──────────────────────────────────────────────────────────

def test_refuses_with_one_node():
    idx = pd.bdate_range("2022-01-01", periods=400)
    r = spillover.analyze({"ONLY": pd.Series(range(400), idx)})
    assert not r["ok"] and "2 daily nodes" in r["reason"]


def test_refuses_on_thin_history():
    idx = pd.bdate_range("2022-01-01", periods=30)
    rng = np.random.default_rng(1)
    sm = {n: pd.Series(np.cumsum(rng.standard_normal(30)), idx) for n in ("A", "B", "C")}
    r = spillover.analyze(sm)
    assert not r["ok"] and "insufficient" in r["reason"]


def test_caps_node_count():
    idx = pd.bdate_range("2020-01-01", periods=1500)
    rng = np.random.default_rng(2)
    sm = {f"N{i}": pd.Series(np.cumsum(rng.standard_normal(1500 - i * 10)),
                             idx[: 1500 - i * 10]) for i in range(12)}
    r = spillover.analyze(sm)
    assert r["ok"]
    assert len(r["nodes"]) <= spillover.MAX_NODES
