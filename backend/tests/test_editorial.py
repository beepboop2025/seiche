"""The editorial surface is data, not decorative copy."""

from seiche.editorial import build_data_quality, build_editorial


def _inputs():
    engines = {
        "composite": {
            "value": 45.4,
            "regime": "STRAIN",
            "coverage_pct": 100.0,
            "decomposition": [
                {"component": "weather", "score": 100.0, "contribution": 11.0, "saturated": True},
                {"component": "tails", "score": 13.7, "contribution": 2.3},
            ],
        },
        "kink": {
            "ok": True,
            "asof": "2026-08-04",
            "distance_b": -719,
            "observed_spread_now_bp": -3.7,
            "r2": 0.62,
        },
        "ledger": {
            "ok": True,
            "asof": "2026-07-29",
            "letter_line": "Reserves fell $77.6B and the TGA absorbed $81.2B.",
        },
    }
    deep = {
        "tell": {"ok": True, "asof": "2026-08-04", "tell": 34, "market_pctl": 34},
        "stacker": {"ok": True, "p_now": 0.066},
    }
    headline = {
        "sofr_pct": {"value": 3.66, "asof": "2026-08-04"},
        "reserves_b": {"value": 2984.6, "asof": "2026-07-29"},
        "srf_accepted_b": {"value": 0.0, "asof": "2026-08-05"},
    }
    calendar = {
        "crunch_windows": [{
            "date": "2026-08-11",
            "reason": "$210B auction settlement while reserves sit below the estimated kink",
            "worst_case_b": 2949.1,
            "settlement_b": 210,
        }],
    }
    return engines, deep, headline, calendar


def test_editorial_leads_with_argument_and_countercase():
    engines, deep, headline, calendar = _inputs()
    brief = build_editorial(
        generated_at="2026-08-06T07:24:42+00:00",
        engines=engines,
        deep=deep,
        headline=headline,
        calendar=calendar,
        faults=[],
    )
    assert brief["schema"] == "seiche.editorial.v1"
    assert "calendar" in brief["thesis"].lower()
    assert "abundance" in brief["thesis"].lower()
    assert brief["confidence"] == "guarded"
    assert brief["dominant_driver"]["engine"] == "weather"
    assert brief["evidence"][0]["engine"] == "ledger"
    assert "SOFR" in brief["countercase"][0]["claim"]
    assert brief["watch"][0]["date"] == "2026-08-11"


def test_fault_cuts_editorial_confidence_before_interpretation():
    engines, deep, headline, calendar = _inputs()
    brief = build_editorial(
        generated_at="2026-08-06T07:24:42+00:00",
        engines=engines,
        deep=deep,
        headline=headline,
        calendar=calendar,
        faults=[{"source": "fred", "detail": "timeout"}],
    )
    assert brief["confidence"] == "low"
    assert "fault" in brief["confidence_note"].lower()


def test_data_quality_keeps_realtime_scope_separate():
    _, _, headline, _ = _inputs()
    quality = build_data_quality(
        generated_at="2026-08-06T07:24:42+00:00",
        provenance=[
            {"staleness": "fresh", "fetched_at": "2026-08-06T07:20:00+00:00"},
            {"staleness": "aging", "fetched_at": "2026-08-05T20:00:00+00:00"},
            {"staleness": "dead", "fetched_at": "2026-07-01T00:00:00+00:00"},
            {"staleness": "unknown", "fetched_at": "2026-08-06T07:22:00+00:00"},
        ],
        headline=headline,
    )
    assert quality["status_counts"] == {"aging": 1, "dead": 1, "fresh": 1, "unknown": 1}
    assert quality["fresh_share_pct"] == 50.0
    assert quality["classified_source_count"] == 3
    assert quality["unclassified_source_count"] == 1
    assert quality["classification_coverage_pct"] == 75.0
    assert quality["realtime"]["scope"] == "crypto venue microstructure only"
    reserves = next(row for row in quality["headline_ages"] if row["series"] == "reserves_b")
    assert reserves["age_days"] == 8


def test_fetch_only_table_is_not_mislabelled_as_observation_fresh(monkeypatch):
    from seiche import assemble

    monkeypatch.setattr(assemble.store, "load_series", lambda _name: None)
    rows = assemble._provenance({
        "nyfed_srf": {"fetched_at": "2026-08-06T07:20:00+00:00"},
    })
    assert rows[0]["staleness"] == "unknown"
    assert rows[0]["asof"] is None
    assert "fetch clock only" in rows[0]["freshness_basis"]


def test_dispatch_uses_the_same_editorial_object(fake_snap):
    from copy import deepcopy

    from seiche.dispatch_daily import build_dispatch

    snap = deepcopy(fake_snap)
    snap["editorial"] = {
        "thesis": "The balance sheet is tightening, but the tape has not confirmed it.",
        "standfirst": "The board reads 41 out of 100, EROSION; the five-day event read is 19.0%.",
        "confidence": "guarded",
        "confidence_note": "One structural signal is doing most of the work.",
        "evidence": [{
            "label": "Balance-sheet identity",
            "claim": "The TGA absorbed $80B.",
            "asof": "2026-07-09",
            "source": "Federal Reserve H.4.1",
        }],
        "countercase": [{"claim": "SOFR remains below IORB."}],
    }
    letter = build_dispatch(snap)
    assert letter["title"] == "The balance sheet is tightening, but the tape has not confirmed it"
    assert "## The argument" in letter["free_md"]
    assert "The countercase" in letter["free_md"]
    assert "Conviction: GUARDED" in letter["free_md"]
    assert "five-day event read" in letter["summary"]
