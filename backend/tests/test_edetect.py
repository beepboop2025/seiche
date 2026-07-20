"""E-Detector tests — the warranty must hold, the tripwire must bite.

Same philosophy as the sibling engine tests: not "does it run" but "does it
refuse to cheat". The e-detector's product IS the false-alarm warranty
(arXiv:2203.03532, Thm. 2.4: threshold 1/alpha => ARL >= 1/alpha days,
nonasymptotic), so the tests pin both sides of the contract:

  (a) on iid noise the mixture must NOT reach 1/alpha — one long stream plus
      a Monte-Carlo panel, with the theoretical allowance E[#alarms] <= n*alpha
      stated beside the (much tighter) observed count;
  (b) a planted mean shift at a known day must be caught fast (< 60bd) with
      the SR change-date estimate landing near the planted day, in both
      directions (spread up, tail down);
  (c) no look-ahead: analyze(s[:k]) reproduces the full run restricted to k
      exactly — detections and the log-mixture path (house invariant);
  (d) thin history refuses with a reason instead of guessing;
  (e) the payload is JSON-safe, carries the house keys, and publishes no
      "score" — a detection is testimony with a warranty, not a gauge input.

Synthetic construction: iid Gaussian noise (sigma = 1.5bp); the shifted
world adds +2*sigma at day 1000 (up) or -2*sigma at day 1500 on a
tail-like |N(4,1)| stream (down). Seeds fixed; ground truth known.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

# Leak canary (Bloomberg pytest-memray): the engine is a vectorized O(K*n)
# recursion — kilobytes. Inert unless pytest runs with --memray (CI does).
pytestmark = pytest.mark.limit_memory("256 MB")

from seiche.engines import edetect


def _bdays(n: int, start: str = "2015-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


@pytest.fixture()
def rng():
    return np.random.default_rng(7)


def _null_stream(rng, n: int = 2000, sigma: float = 1.5) -> pd.Series:
    return pd.Series(rng.normal(0.0, sigma, n), index=_bdays(n))


def _shifted_stream(rng, n: int = 2000, sigma: float = 1.5,
                    at: int = 1000, delta: float = 3.0) -> pd.Series:
    v = rng.normal(0.0, sigma, n)
    v[at:] += delta
    return pd.Series(v, index=_bdays(n))


_LOG10_THRESHOLD = 3.0  # log10(1/alpha) = log10(1000)


# ---------------------------------------------------------------------------
# (a) Null world: the warranty must hold — no false alarms, e-value contained
# ---------------------------------------------------------------------------

def test_edetect_null_stream_no_false_alarm(rng):
    r = edetect.analyze(spread_bp=_null_stream(rng, 2000))
    assert r["ok"], r.get("reason")
    sp = r["streams"]["spread"]
    assert not sp["detected"], "iid noise tripped the tripwire — warranty broken"
    assert sp["n_detections"] == 0
    assert sp["days_since_last_detection"] is None
    assert sp["change_date"] is None
    assert not sp["alarm_now"]
    # the mixture must stay under 1/alpha with margin on this planted world
    assert sp["max_log10_e_value"] < _LOG10_THRESHOLD, \
        f"e-value reached 10^{sp['max_log10_e_value']} >= 1/alpha under the null"
    # the warranty arithmetic itself: threshold IS 1/alpha, stated in days
    assert sp["threshold"] == 1.0 / edetect.EDETECT_ALPHA == 1000.0
    assert "1000 days in expectation" in sp["arl_warranty"]


def test_edetect_false_alarm_rate_matches_warranty():
    """Monte-Carlo panel: 8 independent null streams x 1500 days. The ARL
    warranty permits E[#alarms] <= n*alpha = 1.5 per stream (12 panel-wide);
    a valid e-detector is far tighter in practice — pinned at the observed
    zero with the theoretical allowance stated."""
    total_alarms = 0
    for seed in range(101, 109):
        rr = np.random.default_rng(seed)
        s = pd.Series(rr.normal(0.0, 1.5, 1500), index=_bdays(1500))
        blk = edetect.analyze(spread_bp=s)["streams"]["spread"]
        assert blk["max_log10_e_value"] < _LOG10_THRESHOLD
        total_alarms += blk["n_detections"]
    assert total_alarms == 0, \
        f"{total_alarms} false alarms in 8x1500d of noise (theory allows up to 12 in expectation)"


# ---------------------------------------------------------------------------
# (b) Planted shifts must be caught — fast, dated, and in the right direction
# ---------------------------------------------------------------------------

def test_edetect_detects_planted_shift_fast(rng):
    rng = np.random.default_rng(11)
    s = _shifted_stream(rng, 2000, at=1000, delta=3.0)  # +2 sigma at day 1000
    r = edetect.analyze(spread_bp=s)
    assert r["ok"], r.get("reason")
    sp = r["streams"]["spread"]
    assert sp["detected"], "a 2-sigma regime break walked past the tripwire"
    first = sp["detections"][0]
    delay = first["pos"] - 1000
    assert 0 <= delay < 60, f"detection delay {delay}bd >= 60bd"
    # the SR change-date estimate must land near the planted day
    assert abs(first["change_pos"] - 1000) <= 50, \
        f"change-date estimate off by {abs(first['change_pos'] - 1000)}bd"
    assert first["direction"] == "up"
    assert first["lambda_star"] > 0.0
    assert first["e_value"] >= 1000.0
    assert sp["days_since_last_detection"] is not None
    assert sp["change_date"] == sp["detections"][-1]["change_date"]


def test_edetect_detects_downshift_on_tail_stream(rng):
    spread = _null_stream(rng, 2000)
    rr = np.random.default_rng(13)
    t = np.abs(rr.normal(4.0, 1.0, 2000))
    t[1500:] -= 2.0  # -2 sigma detachment collapse at day 1500
    tail = pd.Series(t, index=_bdays(2000))
    r = edetect.analyze(spread_bp=spread, tail_bp=tail)
    assert r["ok"], r.get("reason")
    tb = r["streams"]["tail"]
    assert tb["detected"], "a -2 sigma tail collapse went undetected"
    first = tb["detections"][0]
    assert 0 <= first["pos"] - 1500 < 60
    assert abs(first["change_pos"] - 1500) <= 60
    assert first["direction"] == "down"
    # and the untouched spread stream must stay quiet: per-stream independence
    assert not r["streams"]["spread"]["detected"]


# ---------------------------------------------------------------------------
# (c) No look-ahead: truncation equality on detections and the e-value path
# ---------------------------------------------------------------------------

def test_edetect_no_look_ahead(rng):
    rng = np.random.default_rng(11)
    s = _shifted_stream(rng, 2000, at=1000, delta=3.0)
    full = edetect.analyze(spread_bp=s)
    trunc = edetect.analyze(spread_bp=s.iloc[:1300])
    assert full["ok"] and trunc["ok"]

    f_det = full["streams"]["spread"]["detections"]
    t_det = trunc["streams"]["spread"]["detections"]
    assert t_det == [d for d in f_det if d["pos"] < 1300], \
        "detections changed when future data was appended — look-ahead leak"

    ser_f = full["_log_m"]["spread"]
    ser_t = trunc["_log_m"]["spread"]
    assert len(ser_t) > 100
    pref = ser_f.loc[: ser_t.index[-1]]
    assert len(pref) == len(ser_t)
    assert np.allclose(pref.to_numpy(), ser_t.to_numpy(), atol=1e-12), \
        "log-mixture path at T changed when future data was appended"

    # a truncation ending BEFORE the shift must show the same (empty) record
    pre = edetect.analyze(spread_bp=s.iloc[:800])
    assert pre["ok"] and not pre["streams"]["spread"]["detected"]
    assert [d for d in f_det if d["pos"] < 800] == []


# ---------------------------------------------------------------------------
# (d) Refusals: thin history, thin tail — never a guessed calibration
# ---------------------------------------------------------------------------

def test_edetect_refuses_thin_history(rng):
    short = pd.Series(rng.normal(0.0, 1.5, 59), index=_bdays(59))
    r = edetect.analyze(spread_bp=short)
    assert not r["ok"]
    assert "59" in r["reason"] and str(edetect.EDETECT_MIN_HISTORY_D) in r["reason"]


def test_edetect_thin_tail_degrades_honestly(rng):
    spread = _null_stream(rng, 2000)
    tail = pd.Series(rng.normal(4.0, 1.0, 40), index=_bdays(40))
    r = edetect.analyze(spread_bp=spread, tail_bp=tail)
    assert r["ok"], "a thin tail must not sink the spread detector"
    assert r["streams"]["tail"]["ok"] is False
    assert "reason" in r["streams"]["tail"]
    assert r["streams"]["spread"]["ok"]
    # and a missing tail is declared, not silently dropped
    r2 = edetect.analyze(spread_bp=spread)
    assert r2["ok"] and r2["streams"]["tail"] is None
    assert any("tail" in c for c in r2["caveats"])


# ---------------------------------------------------------------------------
# (e) Payload: JSON-safe, house keys, warranty note, no score
# ---------------------------------------------------------------------------

def test_edetect_payload_json_safe(rng):
    rng = np.random.default_rng(11)
    s = _shifted_stream(rng, 2000, at=1000, delta=3.0)
    r = edetect.analyze(spread_bp=s, tail_bp=_null_stream(np.random.default_rng(5), 2000) + 4.0)
    assert r["ok"], r.get("reason")
    payload = {k: v for k, v in r.items() if not str(k).startswith("_")}
    for key in (
        "ok", "asof", "alpha", "threshold", "lambda_grid", "baseline_bd",
        "arl_warranty", "warranty_note", "streams", "caveats", "method",
    ):
        assert key in payload, f"missing output key {key}"
    for stream_key in ("spread", "tail"):
        blk = payload["streams"][stream_key]
        assert blk is not None and blk["ok"]
        for key in (
            "e_value", "log10_e_value", "detected", "alarm_now", "n_detections",
            "days_since_last_detection", "change_date", "detections",
            "lambda_grid", "arl_warranty", "threshold", "baseline",
            "max_log10_e_value",
        ):
            assert key in blk, f"stream {stream_key} missing key {key}"
    json.dumps(payload)  # must not raise — no numpy/pandas leakage
    assert isinstance(r["_log_m"]["spread"], pd.Series)
    assert "arXiv:2203.03532" in payload["method"]
    assert "nonasymptotic" in payload["warranty_note"]
    assert payload["lambda_grid"] == list(edetect.EDETECT_LAMBDAS)
    assert "score" not in payload, "testimony with a warranty is context, not a gauge input"
