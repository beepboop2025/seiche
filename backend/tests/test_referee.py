"""Referee page tests: verdict rules, lint-clean rendering on a canned
snapshot, the dark path, and the file write. tmp_path only, no network."""

from __future__ import annotations

import pytest

from seiche import referee


def _corr(r: float, lo: float, hi: float, n: int = 250) -> dict:
    return {"corr": r, "ci95": [lo, hi], "n": n}


def _blk() -> dict:
    return {
        "ok": True,
        "asof": "2026-06-30",
        "window": ["2003-01-31", "2026-06-30"],
        "n_months": 282,
        "historical_evidence": {
            "status": "FINAL_VINTAGE_CONSTRUCTION_PIT",
            "validated_backtest_eligible": False,
            "real_money_eligible": False,
            "reason": "current-vintage public series do not reconstruct old releases",
        },
        "latest": {
            "g3_usd_tn": 17.76,
            "components_usd_tn": {"fed": 6.74, "ecb": 7.04, "boj": 3.98},
            "yoy_pct": -5.78,
        },
        "claim1": {
            "stated": "liquidity leads asset prices by 3 to 6 months",
            "forward_return_corr": {
                f"fwd_{h}m": _corr(0.0, -0.2, 0.2) for h in (1, 3, 6, 9, 12, 18)
            },
            "monthly_correlogram": {"0": 0.0},
            "peak_lead_months": 5,
            "walkforward_oos": {
                "eval_window": ["2011-01-31", "2026-01-31"],
                "months_high_liq": 75,
                "months_low_liq": 106,
                "mean_fwd6m_logret_high_liq": 0.0819,
                "mean_fwd6m_logret_low_liq": 0.0648,
                "spread_6m_logret": 0.0171,
                "spread_ci95": [-0.0318, 0.065],
            },
            "granger": {
                "liq_to_returns": {"F": 0.6, "p": 0.7291, "n": 263, "lags": 6},
                "returns_to_liq": {"F": 1.19, "p": 0.3105, "n": 263, "lags": 6},
            },
        },
        "claim2": {
            "stated": "liquidity leads the business cycle by 12 to 15 months",
            "correlogram": {"0": -0.407},
            "peak_lead_months": 22,
            "peak_detail": _corr(0.231, 0.051, 0.415),
            "corr_at_claimed_13m": _corr(0.15, -0.139, 0.459),
            "contemporaneous": -0.407,
        },
        "claim3": {
            "stated": "global liquidity follows a cycle of roughly 65 months",
            "n_months": 270,
            "dominant_period_months": 54.0,
            "spectral_resolution_at_65m_pm_months": 15.6,
            "n_complete_cycles_in_sample_if_65m": 4.2,
            "peak_to_peak_spacings_months": [25, 27, 35, 29, 28, 51, 34, 27],
        },
        "subsample_stability_fwd6m": {
            "full": _corr(0.004, -0.257, 0.254),
            "first_half": _corr(-0.013, -0.456, 0.378),
            "second_half": _corr(0.074, -0.305, 0.339),
        },
        "robustness_liq_6m": {
            "fwd_3m": _corr(0.011, -0.188, 0.197),
            "fwd_6m": _corr(0.04, -0.262, 0.266),
            "fwd_12m": _corr(0.004, -0.349, 0.309),
        },
        "robustness_net_liquidity": {
            "ok": True,
            "window": ["2003-02-28", "2026-07-31"],
            "latest_usd_tn": 5.92,
            "forward_return_corr": {
                "fwd_3m": _corr(-0.042, -0.245, 0.249, n=165),
                "fwd_6m": _corr(0.027, -0.155, 0.234, n=162),
                "fwd_12m": _corr(0.019, -0.253, 0.207, n=156),
            },
            "fwd6_since_2015": _corr(0.096, -0.421, 0.413, n=133),
            "walkforward_oos": {
                "eval_window": ["2019-08-31", "2026-01-31"],
                "months_high_liq": 31,
                "months_low_liq": 47,
                "mean_fwd6m_logret_high_liq": 0.0835,
                "mean_fwd6m_logret_low_liq": 0.0796,
                "spread_6m_logret": 0.0039,
                "spread_ci95": [-0.1341, 0.1171],
            },
        },
        "method": "G3 central bank assets converted at monthly average spot.",
    }


# ---------------------------------------------------------------------------
# Verdict rules
# ---------------------------------------------------------------------------

def test_claim1_flat_zeros_are_not_reproducible():
    assert referee.claim1_verdict(_blk()["claim1"]) == referee.NOT_REPRODUCIBLE


def test_claim1_positive_interval_is_wrong_window_not_fail():
    c1 = _blk()["claim1"]
    c1["forward_return_corr"]["fwd_6m"] = _corr(0.4, 0.15, 0.6)
    assert referee.claim1_verdict(c1) == referee.WRONG_WINDOW


def test_claim2_significant_peak_outside_window_is_wrong_window():
    assert referee.claim2_verdict(_blk()["claim2"]) == referee.WRONG_WINDOW


def test_claim2_peak_inside_window_reproduces():
    c2 = _blk()["claim2"]
    c2["peak_lead_months"] = 13
    assert referee.claim2_verdict(c2) == referee.REPRODUCED


def test_claim2_interval_through_zero_is_not_reproducible():
    c2 = _blk()["claim2"]
    c2["peak_detail"] = _corr(0.1, -0.05, 0.25)
    assert referee.claim2_verdict(c2) == referee.NOT_REPRODUCIBLE


def test_claim3_coarse_resolution_is_untestable():
    assert referee.claim3_verdict(_blk()["claim3"]) == referee.UNTESTABLE


def test_claim3_fine_resolution_rules_either_way():
    c3 = _blk()["claim3"]
    c3["spectral_resolution_at_65m_pm_months"] = 5.0
    c3["dominant_period_months"] = 63.0
    assert referee.claim3_verdict(c3) == referee.REPRODUCED
    c3["dominant_period_months"] = 40.0
    assert referee.claim3_verdict(c3) == referee.NOT_REPRODUCIBLE


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_page_renders_lint_clean_with_scope_and_verdicts():
    page = referee.render_referee_html({"deep": {"refereegli": _blk()}})
    assert "NOT REPRODUCIBLE" in page
    assert "WRONG WINDOW" in page
    assert "UNTESTABLE HERE" in page
    # the scope disclosure is above the fold, not buried
    assert "not a test of the proprietary index" in " ".join(page.split()).lower()
    assert "17.76" in page
    # honesty about the moat stays in
    assert "irreplaceable" in page
    assert "CONSTRUCTION PIT ONLY" in page
    assert "not validated-backtest evidence" in page
    for ch in ("—", "–"):
        assert ch not in page


def test_net_liquidity_variant_renders_with_its_window():
    page = " ".join(referee.render_referee_html(
        {"deep": {"refereegli": _blk()}}).split())
    assert "net liquidity" in page
    assert "5.92 trillion" in page
    assert "2019-08-31" in page  # the walk forward lands on the famous era
    assert "gross or net" in page


def test_absent_net_liquidity_renders_the_reason():
    blk = _blk()
    blk["robustness_net_liquidity"] = {"ok": False,
                                       "reason": "tga or rrp series unavailable"}
    page = referee.render_referee_html({"deep": {"refereegli": blk}})
    assert "not testable on today's inputs" in page
    assert "tga or rrp series unavailable" in page


def test_dark_snapshot_renders_the_notice():
    page = referee.render_referee_html(
        {"deep": {"refereegli": {"ok": False, "reason": "fed series unavailable"}}})
    assert "not available today" in page
    assert "fed series unavailable" in page


def test_missing_block_renders_the_notice():
    page = referee.render_referee_html({"deep": {}})
    assert "not available today" in page


def test_missing_historical_evidence_fails_closed_on_the_page():
    blk = _blk()
    del blk["historical_evidence"]
    page = referee.render_referee_html({"deep": {"refereegli": blk}})
    assert "CONSTRUCTION PIT ONLY" in page
    assert "no machine-verifiable historical evidence record" in page


def test_write_referee(tmp_path):
    out = referee.write_referee({"deep": {"refereegli": _blk()}},
                                tmp_path / "referee.html")
    assert out.exists()
    assert out.read_text().startswith("<!doctype html>")


def test_absent_yoy_prints_prose_not_a_leak():
    blk = _blk()
    blk["latest"]["yoy_pct"] = None
    page = referee.render_referee_html({"deep": {"refereegli": blk}})
    assert "not available" in page


# ---------------------------------------------------------------------------
# The rubric section
# ---------------------------------------------------------------------------

def test_rubric_section_renders_self_first_and_lint_clean():
    from seiche import rubric
    blk = _blk()
    blk["rubric"] = rubric.build(blk)
    # render_referee_html raises on any lint issue, so success IS the lint test
    page = referee.render_referee_html({"deep": {"refereegli": blk}})
    assert "The rubric" in page
    i_self = page.index("Seiche itself: the PROOF scoreboard and the composite")
    i_ext = page.index("three headline claims")
    assert i_self < i_ext  # the ordering rule survives rendering
    assert "PARTIAL" in page and "FAIL" in page
    # the live walk-forward numbers ride into the external case's evidence
    assert "+0.0171" in page
    for ch in ("—", "–"):
        assert ch not in page


def test_snapshot_without_rubric_renders_without_the_section():
    """Snapshots baked before the rubric shipped carry no block; the page
    must render them unchanged, not crash or fake a matrix."""
    page = referee.render_referee_html({"deep": {"refereegli": _blk()}})
    assert "The rubric" not in page


def test_failed_rubric_block_is_omitted_not_rendered():
    blk = _blk()
    blk["rubric"] = {"ok": False, "reason": "RuntimeError: rubric failed validation"}
    page = referee.render_referee_html({"deep": {"refereegli": blk}})
    assert "The rubric" not in page
