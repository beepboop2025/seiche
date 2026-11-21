import json

import pytest

from seiche import sdl_publication as publication
from seiche.sources import rbi_sdl as rbi

INDEX = b'<a href="BS_PressReleaseDisplay.aspx?prid=123">State Government Securities - Full Auction Result</a>'
CLOCK = "2026-11-18T09:00:00+00:00"


def sample(total=150):
    return f"""State Government Securities - Full Auction Result. Date : Nov 17, 2026.
        Auction held on November 17, 2026. <table><tr><td><table>
        <tr><th></th><th>RAJASTHAN SGS 2036</th><th>GOA SGS 2037</th><th>Total</th></tr>
        <tr><td>Notified Amount</td><td>100</td><td>50</td><td>{total}</td></tr>
        <tr><td>Tenor</td><td>10 Years</td><td>11 Years</td><td></td></tr>
        <tr><td>Cut-off Yield (%)</td><td>7.95</td><td>-</td><td></td></tr>
        <tr><td>Total Allotment Amount</td><td>100</td><td>0</td><td>100</td></tr>
        </table></td></tr></table>""".encode()


def test_index_and_sum_checks():
    assert rbi.discover(INDEX + INDEX) == [123]
    with pytest.raises(ValueError):
        rbi.discover(b"<html>Access unavailable</html>")
    item = rbi.packet(123, sample(), CLOCK)
    assert len(item["rows"]) == 2 and len(item["release"]["sha256"]) == 64
    assert item["rows"][1][10] is None and item["rows"][1][13] == 0
    with pytest.raises(ValueError, match="totals"):
        rbi.packet(123, sample(200), CLOCK)


def test_a_retry_or_a_visible_older_vintage_cannot_duplicate_or_roll_back_rows():
    item = rbi.packet(123, sample(), CLOCK)
    base = json.loads(publication.PACK.read_text())
    merged = publication.merge(base, {"123":item})
    assert publication.merge(merged, {"123":item}) == merged
    revised = {**item, "release":{**item["release"], "retrieved_utc":"2026-11-19T08:00:00+00:00"}}
    latest = publication.merge(merged, {"123":revised})
    assert latest["releases"]["123"] == revised["release"]
    assert publication.merge(latest, {"123":item}) == latest


def test_bundled_17_november_release_contains_all_rbi_columns():
    base = json.loads(publication.PACK.read_text())
    release = base["releases"]["63989"]
    assert release["auction_date"] == "2026-11-17" and release["total_check"] == "matched"
    rows = [r for r in base["rows"] if r[1] == 63989]
    assert len(rows) == 22
    assert sorted((r[4],r[10]) for r in rows if r[2] == "RAJASTHAN") == [
        ("RAJASTHAN SGS 2036",7.95),("RAJASTHAN SGS 2042",8.1496),("RAJASTHAN SGS 2052",8.12)]
