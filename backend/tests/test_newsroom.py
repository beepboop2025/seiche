"""The analytical-story contract keeps cross-product context in its lane."""

import copy

import pytest

from seiche.newsroom import build_story


def _dispatch():
    return {
        "slug": "2026-08-11-daily",
        "date": "2026-08-11",
        "title": "The board climbs 7 points on a fresh print",
        "summary": "A measured change, not a standing level.",
        "odds": [],
    }


def test_story_requires_change_for_full_story(fake_snap):
    snap = copy.deepcopy(fake_snap)
    story = build_story(_dispatch(), snap, previous_value=41.0)
    assert story["editorial_class"] != "full_story"
    assert story["original_contribution"]["kinds"] == [
        "dated_forward_test"
    ]


def test_fresh_delta_can_clear_gate_and_is_traceable(fake_snap):
    snap = copy.deepcopy(fake_snap)
    mover = {
        "label": "SOFR minus IORB",
        "value": 8.0,
        "unit": "bp",
        "max_abs_z": 3.1,
        "asof": "2026-07-10",
        "flag": True,
    }
    story = build_story(
        _dispatch(), snap, novel_movers=[mover], previous_value=34.0,
        letter_previous={"regime": "CALM", "tell": 2.0},
    )
    assert story["editorial_class"] == "full_story"
    assert any(c["evidence_status"] == "OBSERVED" for c in story["claims"])
    assert story["clocks"]["event_time"] <= story["clocks"]["knowledge_time"]


def test_palimpsest_reading_is_context_only_and_never_scored(fake_snap):
    snap = copy.deepcopy(fake_snap)
    snap["engines"]["farbasin"] = {
        "ok": True,
        "asof": "2026-07-09",
        "channels": {
            "fear": {"label": "Deletion threat", "last": 71.0,
                     "unit": "score", "asof": "2026-07-09", "n_obs": 42},
        },
        "status": {"backtestable": False, "note": "ACCRUING 42/250"},
    }
    story = build_story(_dispatch(), snap)
    relation = story["evidence_braid"]["relationships"][0]
    assert relation["product"] == "palimpsest"
    assert relation["relation"] == "topic-surface-only"
    assert relation["context_only"] is True
    assert relation["used_in_score"] is False
    assert story["evidence_braid"]["cross_product_score"] is None


@pytest.mark.parametrize("reading", [0.0, 3.78, 100.0])
def test_sonar_latest_reading_survives_in_the_structured_claim(fake_snap, reading):
    mover = {
        "label": "Retained Sonar observation", "last": reading,
        "unit": "%", "max_abs_z": 3.37, "asof": "2026-07-10",
        "flag": True,
    }
    story = build_story(_dispatch(), fake_snap, novel_movers=[mover])
    observed = next(c for c in story["claims"] if c["evidence_status"] == "OBSERVED")
    assert observed["metric"]["value"] == reading
    assert observed["metric"]["as_of"] == mover["asof"]


def test_explicit_zero_value_is_preserved_when_legacy_last_is_also_present(fake_snap):
    mover = {
        "label": "Observed zero", "value": 0.0, "last": 3.78,
        "unit": "%", "max_abs_z": 3.37, "asof": "2026-07-10",
        "flag": True,
    }
    story = build_story(_dispatch(), fake_snap, novel_movers=[mover])
    observed = next(c for c in story["claims"] if c["evidence_status"] == "OBSERVED")
    assert observed["metric"]["value"] == 0.0
