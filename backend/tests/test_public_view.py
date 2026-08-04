"""The free public payload is copy too, and it obeys the same house rules."""

from seiche.dispatch_daily import lint_letter
from seiche.public_view import _regime_line


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
