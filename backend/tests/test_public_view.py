"""The free public payload is copy too, and it obeys the same house rules."""

from seiche.dispatch_daily import lint_letter
from seiche.public_view import _regime_line, public_payload


def _line(**kw):
    return _regime_line({"regime": kw.get("regime", "STRAIN"),
                         "value": kw.get("value", 46.1)},
                        {"tell": kw.get("tell", 31.9),
                         "reading": kw.get("reading", "plumbing leads price")})


def test_regime_line_carries_no_dashes():
    """conclusion.line ships to anyone consuming public.json, so an em dash
    here is a house-style violation on the free public API surface."""
    line = _line()
    assert "—" not in line and "–" not in line, line
    assert lint_letter(line) == [], (line, lint_letter(line))


def test_regime_line_sentences_start_capitalised():
    """The line is built by joining on ". ", so a lower-case fragment reads as
    a broken sentence mid-string."""
    line = _line()
    for sentence in [s.strip() for s in line.split(". ") if s.strip()]:
        assert sentence[0].isupper(), (sentence, line)


def test_regime_line_survives_a_missing_tell():
    assert _line(tell=None).endswith(".")


def test_public_payload_carries_editorial_and_quality_contracts(fake_snap):
    from copy import deepcopy

    snap = deepcopy(fake_snap)
    snap["editorial"] = {"schema": "seiche.editorial.v1", "thesis": "A testable claim."}
    snap["data_quality"] = {"schema": "seiche.data_quality.v1", "source_count": 73}
    payload = public_payload(snap)
    assert payload["schema"] == "seiche.public.v2"
    assert payload["editorial"]["thesis"] == "A testable claim."
    assert payload["data_quality"]["source_count"] == 73
    assert "engines" not in payload and "deep" not in payload


def test_public_proof_carries_fail_closed_historical_evidence(fake_snap):
    evidence = public_payload(fake_snap)["proof"]["historical_evidence"]
    assert evidence["status"] == "FINAL_VINTAGE_CONSTRUCTION_PIT"
    assert evidence["validated_backtest_eligible"] is False
    assert evidence["real_money_eligible"] is False


def test_public_proof_preserves_a_served_verified_boundary(fake_snap):
    from copy import deepcopy

    snap = deepcopy(fake_snap)
    snap["historical_evidence"] = {
        "status": "VERIFIED_AS_PUBLISHED_DATA_CUT",
        "validated_backtest_eligible": True,
        "real_money_eligible": False,
        "cut_id": "vintagecut_test",
    }
    evidence = public_payload(snap)["proof"]["historical_evidence"]
    assert evidence["status"] == "VERIFIED_AS_PUBLISHED_DATA_CUT"
    assert evidence["validated_backtest_eligible"] is True
    assert evidence["real_money_eligible"] is False
