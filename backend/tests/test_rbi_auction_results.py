"""RBI full auction results: mixed auctions, calendar tenors and discovery."""
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from seiche import store
from seiche.domain.observation import QualityState
from seiche.markets.india_inr.pack import AUCTION_TENORS, PACK
from seiche.repository import SQLiteMarketRepository
from seiche.sources import rbi
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import build_official_adapters

FIXTURES = Path(__file__).parent / "fixtures" / "rbi"
RELEASE = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={}"


def release(name, prid, label="rbi_auction_release"):
    return FetchedDocument(RELEASE.format(prid), "text/html", (FIXTURES / name).read_bytes(), label)


def results(day, securities, yields):
    header = "".join(f"<td>{name}</td>" for name in securities)
    ytm = "".join(f"<td>(YTM: {value}%)</td>" for value in yields)
    return (f"<table><tr><td>Date : {day}</td></tr><tr><td>Government Stock - Full Auction Results</td></tr>"
            f"<tr><td colspan=2>Auction Results</td>{header}</tr>"
            f"<tr><td>III.</td><td>Cut-off price / Yield</td>{'<td>99.00</td>' * len(securities)}</tr>"
            f"<tr>{ytm}</tr></table>")


def page(text, prid=64000, label="rbi_auction_release"):
    return FetchedDocument(RELEASE.format(prid), "text/html", text.encode(), label)


def test_mixed_auction_with_a_green_bond_parses_its_conventional_yields():
    # The 30 October 2026 results failed validation on "7.50% GOI SGrB 2056".
    points = rbi.parse_auctions(release("full-auction-results-2026-10-30.html", 63889))
    assert [(p.instrument_id, p.raw_value) for p in points] == [
        ("IN.RBI.GSEC_AUCTION_3Y", Decimal("7.1218")),
        ("IN.RBI.GSEC_AUCTION_7Y", Decimal("7.3818")),
        ("IN.RBI.GSEC_AUCTION_30Y", Decimal("7.8459")),
    ]
    assert [p.revision_id for p in points] == [
        "auction:6.20% GS 2029:prid:63889",
        "auction:6.57% GS 2033:prid:63889",
        "auction:7.63% GS 2056:prid:63889",
    ]
    # The green bond is identified but never fills a conventional slot.
    assert not any(b"SGrB" in p.row_evidence or b"7.8292" in p.row_evidence for p in points)
    assert {p.event_time for p in points} == {date(2026, 10, 30)}
    # The release is dated; its canonical clock is inferred, not invented.
    assert all(p.publication_time_policy is PublicationTimePolicy.INFER for p in points)
    assert all(p.source_publication_time is None for p in points)


def test_fifteen_and_fifty_year_auctions_fill_issuance_calendar_slots():
    points = rbi.parse_auctions(release("full-auction-results-2026-10-09.html", 63775))
    assert [(p.instrument_id, p.raw_value) for p in points] == [
        ("IN.RBI.GSEC_AUCTION_15Y", Decimal("7.4898")),
        ("IN.RBI.GSEC_AUCTION_50Y", Decimal("7.8015")),
    ]
    assert AUCTION_TENORS == (3, 5, 7, 10, 15, 30, 40, 50)
    declared = {i.instrument_id for i in PACK.instruments if i.source_adapter_id == "rbi_auctions"}
    assert declared == {f"IN.RBI.GSEC_AUCTION_{tenor}Y" for tenor in AUCTION_TENORS}
    assert all(PACK.instrument_cadence(PACK.instrument_map[i]) == "P4W" for i in declared)


def test_reissue_after_year_turn_uses_its_bucket_and_exact_matches_win():
    january = results("Jan 22, 2027", ["6.20% GS 2029", "6.57% GS 2033", "7.63% GS 2056"],
                      ["7.10", "7.40", "7.80"])
    assert [p.instrument_id for p in rbi.parse_auctions(page(january))] == [
        "IN.RBI.GSEC_AUCTION_3Y", "IN.RBI.GSEC_AUCTION_7Y", "IN.RBI.GSEC_AUCTION_30Y"]
    contested = results("Oct 30, 2026", ["6.57% GS 2033", "6.40% GS 2032"], ["7.38", "7.30"])
    point, = rbi.parse_auctions(page(contested))
    assert (point.instrument_id, point.raw_value) == ("IN.RBI.GSEC_AUCTION_7Y", Decimal("7.38"))


def test_recognized_securities_outside_the_slots_are_not_a_fault():
    off_calendar = results("Oct 30, 2026", ["7.10% GS 2045", "GOI FRB 2034"], ["7.55", "6.90"])
    assert rbi.parse_auctions(page(off_calendar)) == ()


@pytest.mark.parametrize("text,message", [
    (results("Oct 30, 2026", ["7.10% GS 2036", "7.25% XYZ 2036"], ["7.40", "7.45"]), "identity"),
    (results("Oct 30, 2026", ["7.10% GS 2036"], ["7.40"]).replace("(YTM: 7.40%)", "(WAY: 7.40%)"), "cut-off"),
])
def test_unknown_identities_and_missing_cut_off_rows_still_fail_loudly(text, message):
    with pytest.raises(ValueError, match=message):
        rbi.parse_auctions(page(text))


def feed(items):
    body = "".join(
        f"<item><title>{title}</title><link>https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid={prid}</link>"
        f"<pubDate>Fri, 30 Oct 2026 18:10:00</pubDate><description><![CDATA[{text}]]></description></item>"
        for title, prid, text in items)
    return f"<rss><channel>{body}</channel></rss>"


def listing(items):
    # RBI's listing uses unquoted relative hrefs.
    rows = "".join(f"<tr><td><a class='link2' href=BS_PressReleaseDisplay.aspx?prid={prid}>{title}</a></td></tr>"
                   for title, prid in items)
    return f"<table>{rows}</table>"


@pytest.mark.asyncio
async def test_discovery_reads_the_dated_listing_once_per_release():
    table = results("Oct 30, 2026", ["6.20% GS 2029"], ["7.1218"])
    rss = feed([(rbi.AUCTION_TITLE, 64001, table), ("Money Market Operations as on October 31, 2026", 64002, "")])
    index = listing([(rbi.AUCTION_TITLE, 64001), (rbi.AUCTION_TITLE, 63889), ("Auction of State Government Securities", 64003),
                     (rbi.AUCTION_TITLE, 64004), (rbi.AUCTION_TITLE, 64004)])
    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path.endswith(".xml"):
            return httpx.Response(200, text=rss, headers={"content-type": "application/xml"})
        if "prid" not in request.url.query.decode():
            return httpx.Response(200, text=index, headers={"content-type": "text/html"})
        return httpx.Response(200, text=results("Oct 30, 2026", ["6.20% GS 2029"], ["7.1218"]),
                              headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        documents = await rbi.fetch_auctions(client)

    labels = [(d.label, d.source_uri.rsplit("=", 1)[-1]) for d in documents]
    # The feed copy and the named references are not fetched a second time.
    assert labels == [("rbi_auction_rss", rbi.RSS_URL), ("rbi_auction_index", rbi.RELEASES_URL),
                      ("rbi_auction_release", "64004"),
                      *(("rbi_auction_reference", prid) for prid in rbi.AUCTION_REFERENCE_PRIDS)]
    assert len(requested) == 2 + 1 + len(rbi.AUCTION_REFERENCE_PRIDS)


def test_feed_and_listing_captures_of_one_release_share_row_evidence():
    table = results("Oct 30, 2026", ["6.20% GS 2029"], ["7.1218"])
    from_feed, = rbi.parse_auctions(FetchedDocument(
        rbi.RSS_URL, "application/xml", feed([(rbi.AUCTION_TITLE, 64001, table)]).encode(), "rbi_auction_rss"))
    from_listing, = rbi.parse_auctions(page(table, prid=64001))
    assert from_feed.row_evidence == from_listing.row_evidence
    assert from_feed.revision_id == from_listing.revision_id == "auction:6.20% GS 2029:prid:64001"
    # Only the feed carries a native publication clock.
    assert from_feed.source_publication_time == datetime(2026, 10, 30, 12, 40, tzinfo=UTC)
    assert from_feed.quality is QualityState.ESTIMATED
    # A named reference in the feed is captured only through its own request.
    assert rbi.parse_auctions(FetchedDocument(
        rbi.RSS_URL, "application/xml", feed([(rbi.AUCTION_TITLE, 63889, table)]).encode(), "rbi_auction_rss")) == ()


@pytest.mark.asyncio
async def test_the_failing_auction_now_collects_through_the_canonical_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "rbi-auctions.sqlite")
    capture = datetime(2026, 11, 2, 12, tzinfo=UTC)
    adapter = next(item for item in build_official_adapters(repository=SQLiteMarketRepository(), clock=lambda: capture)
                   if item.adapter_id == "rbi_auctions")

    async def fetcher(_client):
        return (FetchedDocument(rbi.RSS_URL, "application/xml", b"<rss><channel></channel></rss>", "rbi_auction_rss"),
                FetchedDocument(rbi.RELEASES_URL, "text/html", b"<table></table>", "rbi_auction_index"),
                release("full-auction-results-2026-10-30.html", 63889, "rbi_auction_reference"),
                release("full-auction-results-2026-10-09.html", 63775, "rbi_auction_reference"))

    adapter.fetcher = fetcher
    batch = await adapter.collect()
    by_id = {row.instrument_id: row for row in batch.observations}
    assert sorted(by_id) == sorted(f"IN.RBI.GSEC_AUCTION_{t}Y" for t in (3, 7, 15, 30, 50))
    assert by_id["IN.RBI.GSEC_AUCTION_30Y"].value == Decimal("784.5900")
    # End of the 30 October release day in Mumbai, never the capture time.
    assert by_id["IN.RBI.GSEC_AUCTION_3Y"].source_publication_time == datetime(2026, 10, 30, 18, 29, 59, tzinfo=UTC)
    assert all(row.knowledge_time == capture for row in batch.observations)
