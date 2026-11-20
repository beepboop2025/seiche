"""Live boards can reuse a fit while each result keeps its own evidence."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from seiche.engines import caesar


@pytest.fixture
def walks(monkeypatch):
    calls = []

    def walk(y, theta):
        calls.append(theta)
        return {key: np.full(len(y), -theta) for key in ("q", "e", "qc", "ec")}

    monkeypatch.setattr(caesar, "_latest_walks", None)
    monkeypatch.setattr(caesar, "_walk_level", walk)
    return calls


def test_live_fit_is_private_exact_and_separate_from_replay(walks):
    index = pd.bdate_range("2020-01-01", periods=560)
    series = pd.Series(np.sin(np.arange(len(index))), index=index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(caesar.analyze, series, reuse_live_fit=True) for _ in range(2)]
        first, second = (future.result() for future in futures)
    assert len(walks) == 2
    first["_wf"]["q95"].iloc[0, 1] = 500
    assert second["_wf"]["q95"].iloc[0, 1] != 500
    assert caesar.analyze(series, reuse_live_fit=True)["_wf"]["q95"].iloc[0, 1] != 500
    assert len(walks) == 2

    # Ordinary analyses and Time Machine replays run their own fit.
    caesar.analyze(series[:-1])
    caesar.analyze(series, reuse_live_fit=True)
    assert len(walks) == 4

    revised = series.copy()
    revised.iloc[16] += 0.5
    caesar.analyze(revised, reuse_live_fit=True)
    assert len(walks) == 6
    revised.index = index + pd.Timedelta(days=1)
    shifted = caesar.analyze(revised, reuse_live_fit=True)
    assert len(walks) == 8
    assert shifted["asof"] == revised.index[-1].date().isoformat()
    caesar.analyze(series, reuse_live_fit=True)
    assert len(walks) == 10


def test_bad_or_partial_fit_retried(walks, monkeypatch):
    original = caesar._walk_level
    series = pd.Series(np.arange(560.0), index=pd.bdate_range("2022-01-01", periods=560))
    assert not caesar.analyze(series.iloc[:10], reuse_live_fit=True)["ok"]
    assert not walks

    def fail_second(y, theta):
        paths = original(y, theta)
        if len(walks) == 2:
            raise RuntimeError("second level failed")
        return paths

    monkeypatch.setattr(caesar, "_walk_level", fail_second)
    with pytest.raises(RuntimeError, match="second level failed"):
        caesar.analyze(series, reuse_live_fit=True)
    assert caesar._latest_walks is None

    def invalid(y, theta):
        paths = original(y, theta)
        paths["q"][-1] = np.nan
        return paths

    monkeypatch.setattr(caesar, "_walk_level", invalid)
    caesar.analyze(series, reuse_live_fit=True)
    assert caesar._latest_walks is None
    monkeypatch.setattr(caesar, "_walk_level", original)
    caesar.analyze(series, reuse_live_fit=True)
    assert len(walks) == 6


def test_finite_fallback_after_lp_refusal_is_retried(walks, monkeypatch):
    original = caesar._walk_level

    def refuse(*args):
        raise RuntimeError("quantile LP failed")

    def fallback(y, theta):
        # Exercise the real refusal path that retains finite model outputs.
        caesar._fit_caviar(y[:12], theta, q0=-1.0)
        return original(y, theta)

    monkeypatch.setattr(caesar, "_qr_lp", refuse)
    monkeypatch.setattr(caesar, "_walk_level", fallback)
    series = pd.Series(np.arange(560.0), index=pd.bdate_range("2022-01-01", periods=560))
    assert caesar.analyze(series, reuse_live_fit=True)["ok"]
    monkeypatch.setattr(caesar, "_walk_level", original)
    assert caesar.analyze(series, reuse_live_fit=True)["ok"]
    assert len(walks) == 4


@pytest.mark.parametrize("stage", ["gap", "joint"])
@pytest.mark.parametrize("invalid", ["parameters", "objective", None])
def test_optimizer_fallback_cache_admission(stage, invalid, monkeypatch):
    def minimize(fn, start, *args, **kwargs):
        values = np.full_like(start, np.nan) if invalid == "parameters" else start
        loss = np.nan if invalid == "objective" else 1e12
        return SimpleNamespace(x=values, fun=loss)

    monkeypatch.setattr(caesar, "minimize", minimize)
    y = np.linspace(-3.0, 0.0, 12)
    token = caesar._cacheable_fit.set(True)
    try:
        if stage == "gap":
            fit, _ = caesar._fit_es_gap(y, y - 1, 0.05, -1)
        else:
            fit = caesar._fit_joint(np.zeros(10), y, 0.05, -1, -2)
        assert np.isfinite(fit).all()
        assert caesar._cacheable_fit.get() == (invalid is None)
    finally:
        caesar._cacheable_fit.reset(token)


@pytest.mark.parametrize("asof", [None, pd.Timestamp("2024-11-15")])
def test_board_enables_reuse_only_for_live_inputs(asof, monkeypatch):
    from seiche import assemble

    seen = []

    def record(*, spread_bp, reuse_live_fit):
        seen.append(reuse_live_fit)
        return {"ok": False, "reason": "empty fixture"}

    monkeypatch.setattr(caesar, "analyze", record)
    assemble._run_engines({}, assemble._derived({}), [], asof=asof)
    assert seen == [asof is None]
