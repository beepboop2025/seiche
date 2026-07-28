"""Two published claims under test.

Sea Room: ACI buys a LONG-RUN AVERAGE coverage rate, so nothing the engine
prints (docstring, method, caveats, verdict) may sell it as an assumption-free
or per-day guarantee. The methodology page renders the docstring, so the page
is checked, not just the module.

Composite: saturation is disclosed for EVERY pinned component, not only the
largest, and the flag is purely additive to the decomposition row.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche import methodology
from seiche.engines import composite, searoom

DASHES = ("—", "–")

# The live board on 2026-07-28: weather is pinned at the ceiling and buffers
# sits at 99.7 on (1 - ON RRP / $400B) * 100 while ON RRP is near empty.
LIVE_SUBSCORES = {
    "tails": 11.9, "kink": 52.2, "weather": 100.0, "confession": 21.4,
    "rvxray": 55.5, "resonance": 71.3, "hydrophone": 0.8, "undertow": 47.4,
    "auctions": 22.6, "warehouse": 81.6, "buffers": 99.7,
}


# ---------------------------------------------------------------------------
# Sea Room: the coverage claim
# ---------------------------------------------------------------------------

def _searoom_out():
    rng = np.random.default_rng(11)
    n = 1200
    idx = pd.bdate_range("2018-01-01", periods=n)
    y = pd.Series((rng.random(n) < 0.15).astype(float), index=idx)
    p = pd.Series((0.5 * y + 0.1 + 0.15 * rng.random(n)).clip(0, 1), index=idx)
    return searoom.analyze(p, y)


def test_searoom_docstring_does_not_sell_an_assumption_free_guarantee():
    doc = searoom.__doc__
    low = doc.lower()
    assert "assumption-free" not in low
    assert "guaranteed coverage" not in low
    assert "long-run average" in low
    assert "not per-day" in low or "not a per-day" in low
    assert "Gibbs & Candès 2021" in doc      # the citation stays


def test_methodology_page_carries_the_corrected_claim():
    """The page renders the first docstring paragraphs, so the fix has to
    survive that render, not just sit lower down in the module."""
    text = next(s["text"] for s in methodology.engine_sections()
                if s["module"] == "searoom")
    assert "LONG-RUN AVERAGE" in text
    assert "assumption-free" not in text.lower()
    assert "guaranteed coverage" not in text.lower()
    assert "Gibbs & Candès 2021" in text


def test_searoom_method_and_caveats_state_the_average_not_a_guarantee():
    out = _searoom_out()
    assert out["ok"]
    method = out["method"]
    assert "LONG-RUN AVERAGE" in method
    assert "not a finite-sample or per-day guarantee" in method
    assert "Gibbs and Candès 2021" in method
    assert not any(d in method for d in DASHES)      # house copy rule
    caveats = " ".join(out["caveats"])
    assert "LONG-RUN AVERAGE coverage rate" in caveats
    assert "not a finite-sample and not a per-day guarantee" in caveats
    assert "the realized coverage printed" in caveats  # aggregation is evidence
    assert "guarantee tolerates" not in caveats


def test_searoom_verdict_and_reading_claim_no_per_day_guarantee():
    out = _searoom_out()
    assert "guarantee holding" not in out["verdict"]
    assert not any(d in out["verdict"] for d in DASHES)
    assert "guaranteed" not in out["today"]["reading"]
    # the engine is not weakened: the long-run rate still tracks its target
    assert abs(out["coverage"]["realized"] - out["coverage"]["target"]) <= 0.05
    assert out["today"]["set"] in ("no_event", "both", "event", "empty")


# ---------------------------------------------------------------------------
# Composite: saturation disclosure
# ---------------------------------------------------------------------------

def test_every_pinned_component_is_flagged_not_only_the_largest():
    """Buffers is structurally pinned near 100 and contributes 3.0 points, so
    a consumer that discloses only the top row misses it."""
    out = composite.compose(LIVE_SUBSCORES)
    assert out["ok"]
    flagged = [d["component"] for d in out["decomposition"] if d["saturated"]]
    assert flagged == ["weather", "buffers"] or set(flagged) == {"weather", "buffers"}
    top = out["decomposition"][0]["component"]
    assert top == "weather" and "buffers" in flagged   # the second one, named
    by = {d["component"]: d for d in out["decomposition"]}
    assert by["warehouse"]["score"] == 81.6 and not by["warehouse"]["saturated"]
    assert not by["hydrophone"]["saturated"]           # a floor is not a ceiling


def test_saturated_is_additive_and_moves_no_number():
    """Existing keys and the composite value are untouched by the flag."""
    out = composite.compose(LIVE_SUBSCORES)
    assert out["value"] == 45.3 and out["regime"] == "STRAIN"
    assert out["coverage_pct"] == 100.0 and out["dead_inputs"] == []
    for d in out["decomposition"]:
        assert set(d) == {"component", "score", "weight", "contribution",
                          "status", "saturated"}
        assert isinstance(d["saturated"], bool)
        assert d["status"] == "live" and d["score"] == LIVE_SUBSCORES[d["component"]]
    # weights renormalize as before, so the contributions still sum to the value
    assert round(sum(d["contribution"] for d in out["decomposition"]), 0) == 45.0


def test_saturation_boundary_is_the_published_score():
    subs = dict.fromkeys(composite.COMPOSITE_WEIGHTS, 10.0)
    subs["tails"] = composite.SATURATION_SCORE
    subs["kink"] = composite.SATURATION_SCORE - 0.1
    by = {d["component"]: d for d in composite.compose(subs)["decomposition"]}
    assert by["tails"]["saturated"] is True       # at the threshold counts
    assert by["kink"]["saturated"] is False


def test_dead_component_is_not_saturated():
    subs = dict.fromkeys(composite.COMPOSITE_WEIGHTS, 50.0)
    subs["buffers"] = None
    by = {d["component"]: d for d in composite.compose(subs)["decomposition"]}
    assert by["buffers"]["status"] == "DEAD" and by["buffers"]["score"] is None
    assert by["buffers"]["saturated"] is False    # a bool, never None


def test_composite_method_states_the_saturation_rule():
    method = composite.compose(LIVE_SUBSCORES)["method"]
    assert "saturated" in method
    assert f"{composite.SATURATION_SCORE:g}" in method
    assert "0-100" in method
    assert not any(d in method for d in DASHES)   # house copy rule
