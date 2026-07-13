"""BOJ stat-search CSV parser: fail-loud and format invariants."""

from __future__ import annotations

import pandas as pd
import pytest

from seiche.sources.boj import parse_csv

SAMPLE = """BOJ's Main Time-series Statistics
2026-07-13 AM 09:00
"","Call Rates (Daily)"
"Name of time-series","Call Rate, Uncollateralized Overnight, Average (Daily)"
"Series code",FM01'STRDCLUCON
"Unit",percent per annum
"Start of the time-series","1998/01/05"
"End of the time-series","2026/07/09"
"Last update","2026/07/13"
1998/01/10,NA
2026/07/08,0.978
2026/07/09,0.978
"""


def test_parse_csv_skips_metadata_and_na():
    s = parse_csv(SAMPLE)
    assert list(s.index) == [pd.Timestamp("2026-07-08"), pd.Timestamp("2026-07-09")]
    assert s.iloc[-1] == pytest.approx(0.978)


def test_parse_csv_error_page_fails_loud():
    with pytest.raises(ValueError):
        parse_csv("<html>Service temporarily unavailable</html>")
    with pytest.raises(ValueError):
        parse_csv("BOJ's Main Time-series Statistics\n1998/01/10,NA\n")  # only NA rows


def test_parse_csv_dedupes_and_sorts():
    s = parse_csv("2026/07/09,0.980\n2026/07/08,0.978\n2026/07/09,0.978\n")
    assert list(s.values) == [0.978, 0.978]  # sorted, duplicate keeps last
