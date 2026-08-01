"""The Rubric: completeness, the ordering rule, the honest-PARTIAL locks,
and the house copy rules. Pure repo facts, no network.

The regression locks are the point of this file: every grade in both
matrices is pinned, so a PARTIAL cannot drift to PASS (and a FAIL cannot
soften) without the missing evidence shipping in the same commit as the
test change. The cited repo facts are pinned too: if the no-look-ahead test
or the TUNING POINT banner is renamed, the rubric's evidence goes stale and
this file says so.
"""

from __future__ import annotations

import json
from pathlib import Path

from seiche import rubric
from seiche.config import COMPOSITE_WEIGHTS

BACKEND = Path(__file__).resolve().parents[1]


def _gli_blk() -> dict:
    """The slice of a lit refereegli block the rubric reads."""
    return {
        "ok": True,
        "window": ["2003-01-31", "2026-06-30"],
        "n_months": 282,
        "claim1": {
            "walkforward_oos": {
                "eval_window": ["2011-01-31", "2026-01-31"],
                "spread_6m_logret": 0.0171,
                "spread_ci95": [-0.0318, 0.065],
            },
        },
        "claim2": {
            "peak_lead_months": 22,
            "corr_at_claimed_13m": {"corr": 0.15, "ci95": [-0.139, 0.459], "n": 250},
        },
        "claim3": {"spectral_resolution_at_65m_pm_months": 15.6},
    }


def _rows(case: dict) -> dict[str, dict]:
    return {r["field"]: r for r in case["rows"]}


# ---------------------------------------------------------------------------
# 1. Completeness: every field graded, in order, in both cases
# ---------------------------------------------------------------------------

def test_every_field_graded_in_both_cases():
    for blk in (None, _gli_blk()):
        out = rubric.build(blk)
        assert out["ok"]
        assert len(out["cases"]) == 2
        for case in out["cases"]:
            assert [r["field"] for r in case["rows"]] == list(rubric.FIELD_KEYS)
            for r in case["rows"]:
                assert r["grade"] in rubric.GRADES
                assert r["evidence"].strip()
            assert case["tally"] == {
                g: sum(1 for r in case["rows"] if r["grade"] == g)
                for g in rubric.GRADES
            }


def test_the_two_recoded_fields_are_declared():
    out = rubric.build(None)
    recoded = {f["field"]: f.get("recoded_from") for f in out["fields"]
               if "recoded_from" in f}
    assert recoded == {
        "vintage_handling": "cost and turnover treatment",
        "threshold_provenance": "execution semantics",
    }


# ---------------------------------------------------------------------------
# 2. The ordering rule: the self grade publishes first, and the validator
#    refuses a block where it does not
# ---------------------------------------------------------------------------

def test_self_grade_publishes_first():
    out = rubric.build(_gli_blk())
    assert out["cases"][0]["case"] == "self"
    assert out["cases"][1]["case"] == "external"


def test_validator_refuses_a_reordered_block():
    out = rubric.build(None)
    out["cases"].reverse()
    issues = rubric.validate_block(out)
    assert any("ordering rule" in i for i in issues)


def test_validator_refuses_na_without_reason():
    out = rubric.build(None)
    row = out["cases"][0]["rows"][0]
    row["grade"] = rubric.NOT_APPLICABLE
    out["cases"][0]["tally"] = {
        g: sum(1 for r in out["cases"][0]["rows"] if r["grade"] == g)
        for g in rubric.GRADES
    }
    issues = rubric.validate_block(out)
    assert any("without a stated reason" in i for i in issues)
    row["na_reason"] = "stated"
    assert not any("without a stated reason" in i
                   for i in rubric.validate_block(out))


# ---------------------------------------------------------------------------
# 3. The honest-PARTIAL locks: every grade pinned, both cases. A dishonest
#    clean sweep of the self grade is a build failure by test.
# ---------------------------------------------------------------------------

_EXPECTED_SELF = {
    "point_in_time_controls": rubric.PASS,
    "split_transparency": rubric.PARTIAL,
    "held_out_evaluation": rubric.PARTIAL,
    "universe_definition": rubric.PASS,
    "artifact_release": rubric.PASS,
    "vintage_handling": rubric.PARTIAL,
    "threshold_provenance": rubric.PARTIAL,
    "verdict_revision_policy": rubric.PASS,
}

_EXPECTED_GLI = {
    "point_in_time_controls": rubric.FAIL,
    "split_transparency": rubric.FAIL,
    "held_out_evaluation": rubric.FAIL,
    "universe_definition": rubric.PARTIAL,
    "artifact_release": rubric.FAIL,
    "vintage_handling": rubric.FAIL,
    "threshold_provenance": rubric.FAIL,
    "verdict_revision_policy": rubric.FAIL,
}


def test_self_grade_is_not_a_clean_sweep():
    """Flipping any PARTIAL to PASS requires shipping the named evidence
    (an ALFRED as-published replay, a walk-forward threshold derivation, a
    design/evaluation separation, a long attested live record) and changing
    this pin in the same commit."""
    case = rubric.self_grade()
    assert {r["field"]: r["grade"] for r in case["rows"]} == _EXPECTED_SELF
    assert case["tally"][rubric.PARTIAL] == 4


def test_partial_rows_name_their_gaps():
    rows = _rows(rubric.self_grade())
    assert "fredgraph.csv" in rows["vintage_handling"]["evidence"]
    assert "revised values" in rows["vintage_handling"]["evidence"]
    assert "judgment" in rows["threshold_provenance"]["evidence"]
    assert "not held out" in rows["held_out_evaluation"]["evidence"]
    assert "still short" in rows["held_out_evaluation"]["evidence"]
    assert "not separated" in rows["split_transparency"]["evidence"]


def test_gli_grades_are_pinned_lit_or_dark():
    """The grades are facts about the published record, so the dark path
    must not soften them; only the evidence degrades."""
    for blk in (_gli_blk(), {"ok": False, "reason": "fed series unavailable"}, None):
        case = rubric.grade_gli(blk)
        assert {r["field"]: r["grade"] for r in case["rows"]} == _EXPECTED_GLI


# ---------------------------------------------------------------------------
# 4. Live numbers ride the external evidence when the block is lit
# ---------------------------------------------------------------------------

def test_gli_evidence_rides_live_numbers():
    rows = _rows(rubric.grade_gli(_gli_blk()))
    assert "+0.0171" in rows["point_in_time_controls"]["evidence"]
    assert "[-0.0318, +0.0650]" in rows["point_in_time_controls"]["evidence"]
    assert "+0.15 in [-0.14, +0.46], n 250" in rows["held_out_evaluation"]["evidence"]
    assert "282 months" in rows["universe_definition"]["evidence"]
    assert "22 month lead" in rows["threshold_provenance"]["evidence"]
    assert "15.6 months" in rows["threshold_provenance"]["evidence"]


def test_gli_evidence_degrades_honest_when_dark():
    rows = _rows(rubric.grade_gli(None))
    assert "dark on today's inputs" in rows["point_in_time_controls"]["evidence"]
    assert "monthly from 2003" in rows["universe_definition"]["evidence"]


# ---------------------------------------------------------------------------
# 5. The cited repo facts exist (evidence must never go stale silently)
# ---------------------------------------------------------------------------

def test_cited_no_look_ahead_test_exists():
    src = (BACKEND / "tests" / "test_engines.py").read_text()
    assert "def test_history_has_no_look_ahead" in src


def test_cited_tuning_point_banner_exists():
    src = (BACKEND / "seiche" / "config.py").read_text()
    assert "TUNING POINT" in src


def test_cited_event_definition_exists():
    from seiche.engines.backtest import pop_bp  # noqa: F401 -- the citation
    src = (BACKEND / "seiche" / "sources" / "fred.py").read_text()
    assert "fredgraph.csv" in src
    assert "alfred" not in src.lower()  # the day ALFRED ships, regrade the row


# ---------------------------------------------------------------------------
# 6. Determinism, house copy rule, display-only doctrine
# ---------------------------------------------------------------------------

def test_deterministic():
    a, b = rubric.build(_gli_blk()), rubric.build(_gli_blk())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_no_dashes_anywhere_in_the_payload():
    for blk in (None, _gli_blk()):
        blob = json.dumps(rubric.build(blk))
        assert "—" not in blob and "–" not in blob


def test_display_only_doctrine():
    """No composite weight: a FAIL on someone's matrix (or our own) changes
    the board only through a pre-registered methodology change, never
    automatically."""
    assert "rubric" not in COMPOSITE_WEIGHTS
