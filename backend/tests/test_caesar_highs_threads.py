"""CAESar's small LPs must not multiply native pools across API workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import warnings

import numpy as np
import pytest
from scipy.optimize import OptimizeWarning

from seiche.engines import caesar


@pytest.mark.parametrize("theta", [0.05, 0.5, 0.99])
def test_quantile_lp_preserves_exact_fit(theta):
    X = np.column_stack([np.ones(7), np.arange(7)])
    y = X @ np.array([-3.0, 0.25])
    beta = caesar._qr_lp(X, y, theta)
    np.testing.assert_allclose(beta, [-3.0, 0.25], atol=1e-10)
    assert caesar._pinball(y, X @ beta, theta) == pytest.approx(0.0, abs=1e-10)


@pytest.mark.parametrize("slope, expected", [(-2.0, 0.0), (2.0, 0.999)])
def test_quantile_lp_preserves_persistence_bounds(slope, expected):
    X = np.column_stack([np.ones(5), np.arange(-2, 3)])
    beta = caesar._qr_lp(X, slope * X[:, 1], 0.5)
    np.testing.assert_allclose(beta, [0.0, expected], atol=1e-10)


@pytest.mark.parametrize("status", [2, 3, 4])
def test_quantile_lp_keeps_solver_failures_explicit(monkeypatch, status):
    monkeypatch.setattr(caesar, "linprog", lambda *a, **kw: SimpleNamespace(status=status))
    with pytest.raises(RuntimeError, match=rf"quantile LP failed \(status {status}\)"):
        caesar._qr_lp(np.ones((3, 2)), np.arange(3.0), 0.5)


def test_caviar_keeps_constant_quantile_fallback_when_solver_fails(monkeypatch):
    monkeypatch.setattr(caesar, "linprog", lambda *a, **kw: SimpleNamespace(status=4))
    y = np.arange(-4.0, 5.0)
    beta, q = caesar._fit_caviar(y, 0.5, q0=0.0)
    np.testing.assert_array_equal(beta, [0.0, 0.0, 0.0, 0.0])
    np.testing.assert_array_equal(q, np.zeros_like(y))


def test_quantile_lp_preserves_solver_warnings_and_filters(monkeypatch):
    notice = (
        "Unrecognized options detected: {'threads': 1}. "
        "These will be passed to HiGHS verbatim."
    )
    expected = [
        (notice, OptimizeWarning),
        ("solver accuracy warning", OptimizeWarning),
        (notice.replace("'threads': 1", "'threads': 2"), OptimizeWarning),
        (notice, RuntimeWarning),
    ]

    def solver(*args, **kwargs):
        for message, category in expected:
            warnings.warn(message, category)
        return SimpleNamespace(status=0, x=np.zeros(2))

    monkeypatch.setattr(caesar, "linprog", solver)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        filters = warnings.filters[:]
        caesar._qr_lp(np.ones((3, 2)), np.arange(3.0), 0.5)
        assert warnings.filters == filters
    assert [(str(item.message), item.category) for item in caught] == expected


@pytest.mark.skipif(sys.platform != "linux", reason="native thread count requires Linux /proc")
def test_quantile_lp_does_not_grow_native_pools_across_persistent_workers():
    # A fresh interpreter isolates HiGHS scheduler initialization from other tests.
    # Keep three distinct Python workers alive while rotating the actual LP call.
    script = """
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import threading
import numpy as np
from seiche.engines import caesar

def count():
    return len(list(Path('/proc/self/task').iterdir()))

X = np.column_stack([np.ones(7), np.arange(7)])
y = X @ np.array([-3.0, 0.25])
with ExitStack() as stack:
    pools = [stack.enter_context(ThreadPoolExecutor(max_workers=1)) for _ in range(3)]
    workers = [pool.submit(threading.get_native_id).result(timeout=10) for pool in pools]
    baseline = count()
    counts = []
    for _ in range(3):
        for pool in pools:
            beta = pool.submit(caesar._qr_lp, X, y, 0.5).result(timeout=10)
            np.testing.assert_allclose(beta, [-3.0, 0.25], atol=1e-10)
            counts.append(count())
print(json.dumps({'workers': workers, 'baseline': baseline, 'counts': counts}))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(caesar.__file__).resolve().parents[2])
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
        env[name] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], env=env, capture_output=True, text=True,
        timeout=60, check=True,
    )
    observed = json.loads(result.stdout)
    assert len(set(observed["workers"])) == 3, observed
    assert observed["counts"] == [observed["baseline"]] * 9, observed
