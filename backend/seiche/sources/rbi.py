"""Dated RBI tables. No FX clock is borrowed for policy, bills or bonds."""
from __future__ import annotations

import html
import calendar
import csv
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from seiche.domain.observation import QualityState
from seiche.markets.india_inr.pack import RBI_HOME_TENORS, CURVE_TENORS
from seiche.sources.canonical import FetchedDocument, ParsedPoint, PublicationTimePolicy, get_documents

MMO_URL = "https://www.rbi.org.in/Scripts/BS_ViewMMO.aspx/Statistics.aspx"
HOME_URL = "https://www.rbi.org.in/"
RATES_URL = "https://www.rbi.org.in/Scripts/BS_NSDPDisplay.aspx?param=4"
POLICY_URL = "https://www.rbi.org.in/Scripts/annualpolicy.aspx"
RSS_URL = "https://www.rbi.org.in/pressreleases_rss.xml"
AUCTION_REFERENCE_URL = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63517"


class _ReportHTML(HTMLParser):
    """Extract report content, ignoring comments and executable/style blocks.

    This is an extraction step, not a sanitizer for rendering untrusted HTML.
    Retained markup is used only to identify the source's table rows and IDs.
    """

    def __init__(self, *, keep_tags: bool):
        super().__init__(convert_charrefs=False)
        self.keep_tags = keep_tags
        self.ignored_tag: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.ignored_tag is not None:
            return
        if tag in {"script", "style"}:
            self.ignored_tag = tag
        elif self.keep_tags:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.ignored_tag is not None:
            if tag == self.ignored_tag:
                self.ignored_tag = None
        elif self.keep_tags:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.ignored_tag is None:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def _clean(text: str) -> str:
    parser = _ReportHTML(keep_tags=True)
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


def _text(text: str) -> str:
    parser = _ReportHTML(keep_tags=False)
    parser.feed(text)
    parser.close()
    return " ".join(html.unescape(" ".join(parser.parts)).split())


def _rows(text: str):
    for block in re.findall(r"<tr\b[^>]*>.*?</tr>", _clean(text), re.I | re.S):
        cells = [_text(v) for v in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", block, re.I | re.S)]
        ids = re.findall(r"\bid=[\"']([^\"']+)", block, re.I)
        yield ids, cells, block


def _number(text: str) -> Decimal | None:
    cleaned = text.strip().replace(",", "").replace("−", "-")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return None
    try:
        number = Decimal(cleaned)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _day(text: str) -> date | None:
    text = text.strip().replace("Sept.", "Sep.").replace(".", "")
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _point(instrument: str, day: date, value: Decimal, evidence, **kwargs) -> ParsedPoint:
    row = json.dumps({"instrument": instrument, "date": day.isoformat(), "value": str(value),
                      "source_row": evidence}, sort_keys=True, separators=(",", ":")).encode()
    # Weekly tables and policy statements here provide dates, not publication
    # instants. Knowledge time is supplied by the canonical capture itself.
    if (instrument.endswith("_WEEKLY") or instrument.endswith("_DECISION") or instrument == "IN.RBI.CRR"
            or instrument.startswith(("IN.RBI.WSS_", "IN.RBI.OMO_", "IN.RBI.CD_", "IN.RBI.CP_"))):
        kwargs.setdefault("publication_time_policy", PublicationTimePolicy.UNKNOWN)
    return ParsedPoint(instrument, day, value, row, **kwargs)


def parse_mmo(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    text = document.payload.decode("utf-8-sig", errors="replace")
    match = re.search(r"Money Market Operations as on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", _text(text), re.I)
    day = _day(match[1]) if match else None
    if day is None:
        raise ValueError("RBI page has no money-market event date")
    rows = list(_rows(text))
    out: list[ParsedPoint] = []
    singles = {
        "OSCallMoney": ((2, "IN.MARKET.CALL_WAR"),),
        "OSTriparty": ((1, "IN.RBI.TRIPARTY_REPO_VOLUME"), (2, "IN.RBI.TREPS_WAR")),
        "OSMarket": ((2, "IN.RBI.MARKET_REPO_WAR"),),
        "Netliquidityinjected": ((-2, "IN.RBI.NET_INJECTION_TODAY"),),
        "Netliquidityinjectedoutstanding": ((-2, "IN.RBI.NET_INJECTION_OUTSTANDING"),),
        "Netliquidityinjectedoutstandingtoday": ((-2, "IN.RBI.SYSTEM_LIQUIDITY"),),
        "SLFAvailedfromRBI": ((-2, "IN.RBI.SLF"),),
    }
    dated = {"CashBalRBI": "IN.RBI.CASH_BALANCES",
             "GovernmentIndiaSurplusCashBalance": "IN.GOVERNMENT.CASH_BALANCE",
             "Netdurableliquidity": "IN.RBI.DURABLE_LIQUIDITY"}
    facilities = {"MSF3": "MSF_TODAY", "SDF2": "SDF_TODAY", "MORepo": "VRR_TODAY", "MORevRepo": "VRRR_TODAY",
                  "OOMSF2": "MSF_OUTSTANDING", "OOSDF2": "SDF_OUTSTANDING", "MORepo2": "VRR_OUTSTANDING", "OORevRepo": "VRRR_OUTSTANDING"}
    for index, (ids, cells, _) in enumerate(rows):
        for row_id in ids:
            for column, instrument in singles.get(row_id, ()):
                if len(cells) > abs(column) and (value := _number(cells[column])) is not None:
                    out.append(_point(instrument, day, value, cells))
            if row_id in dated and len(cells) >= 3:
                event = _day(cells[1])
                value = _number(cells[2])
                if event is not None and value is not None and event <= day:
                    out.append(_point(dated[row_id], event, value, cells))
            if row_id == "AvgDailyCashRes" and len(cells) >= 3:
                # The future fortnight end is a period label, not an observation.
                value = _number(cells[2])
                if value is not None:
                    out.append(_point("IN.RBI.RESERVE_REQUIREMENT", day, value, cells))
            if row_id not in facilities:
                continue
            group = [cells]
            for next_ids, next_cells, _ in rows[index + 1:]:
                if next_ids or len(next_cells) < 5:
                    break
                group.append(next_cells)
            amounts = [_number(row[-2]) for row in group]
            # Blank operation rows are unknown/not-applicable, never numeric zero.
            if amounts and all(value is not None for value in amounts):
                out.append(_point("IN.RBI." + facilities[row_id], day, sum(amounts, Decimal(0)), group))
            rates = {_number(row[-1]) for row in group}
            if row_id in {"MSF3", "SDF2"} and len(rates) == 1 and None not in rates:
                out.append(_point("IN.RBI." + ("MSF" if row_id == "MSF3" else "SDF"), day, rates.pop(), group))
    # Legacy aggregate remains available, now including every maturity.
    takeup = [p for p in out if p.instrument_id in {"IN.RBI.MSF_TODAY", "IN.RBI.SDF_TODAY"}]
    if len(takeup) == 2:
        out.append(_point("IN.RBI.FACILITY_TAKEUP", day, sum((p.raw_value for p in takeup), Decimal(0)),
                          [p.row_evidence.decode() for p in takeup]))
    if not out:
        raise ValueError("RBI page contains no mapped money-market rows")
    # A Sunday report can be published on Monday before its inferred end-of-
    # business-day clock. The explicit release date establishes the document
    # identity but not an exact publication instant; capture bounds knowledge.
    if re.search(r"Date:\s*\d{1,2}\s+[A-Za-z]+\s+\d{4}", _text(text)):
        out = [replace(point, publication_time_policy=PublicationTimePolicy.UNKNOWN) for point in out]
    return tuple(out)


def parse_sovereign(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    text = _clean(document.payload.decode("utf-8-sig", errors="replace"))
    section = re.search(r"Government Securities Market</h\d>(.*?)(?:<h\d|$)", text, re.I | re.S)
    if section is None:
        raise ValueError("RBI government-securities section missing")
    plain = _text(section[1])
    stamp = re.search(r"#\s*as on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", plain, re.I)
    day = _day(stamp[1]) if stamp else None
    if day is None:
        raise ValueError("RBI government-securities observation date missing")
    bonds = []
    for _, cells, _ in _rows(section[1]):
        if len(cells) != 2:
            continue
        bond = re.fullmatch(r"(\d+(?:\.\d+)?)%\s+GS\s+(\d{4})", cells[0], re.I)
        quote = re.fullmatch(r":?\s*(\d+(?:\.\d+)?)%\s*#", cells[1])
        if bond is None or quote is None:
            continue
        bonds.append((bond, quote, cells))
    if len(bonds) != len(RBI_HOME_TENORS):
        raise ValueError("RBI benchmark table shape changed")
    points = []
    for tenor, (bond, quote, cells) in zip(RBI_HOME_TENORS, bonds):
        years = int(bond[2]) - day.year
        if abs(tenor - years) > 1:
            raise ValueError("RBI benchmark maturity no longer matches its approximate tenor slot")
        points.append(_point(f"IN.RBI.GSEC_BENCHMARK_{tenor}Y", day, Decimal(quote[1]), cells,
                             revision_id=f"bond:{bond[1]}-GS-{bond[2]}"))
    if not points:
        raise ValueError("RBI government-securities section contains no dated benchmark yields")
    return tuple(points)


def parse_weekly_rates(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    text = document.payload.decode("utf-8-sig", errors="replace")
    rows = list(_rows(text))
    mapping = {"Cash Reserve Ratio": "IN.RBI.CRR", "Policy Repo Rate": "IN.RBI.POLICY_REPO_WEEKLY",
               **{f"{tenor}-Day Treasury Bill (Primary) Yield": f"IN.RBI.TBILL_{tenor}D_WEEKLY" for tenor in (91, 182, 364)}}
    dates = []
    out = []
    for idx, (_, cells, block) in enumerate(rows):
        if cells and cells[0] == "Item/Week Ended":
            years = []
            for attrs, value in re.findall(r"<td\b([^>]*)>(.*?)</td>", block, re.I | re.S):
                if re.fullmatch(r"20\d{2}", _text(value)):
                    span = re.search(r"colspan=[\"'](\d+)", attrs)
                    years.extend([int(_text(value))] * (int(span[1]) if span else 1))
            date_cells = rows[idx + 1][1] if idx + 1 < len(rows) else []
            if len(years) != len(date_cells):
                raise ValueError("RBI weekly date columns do not align")
            dates = [_day(f"{cell.replace('.', '')}, {year}") for cell, year in zip(date_cells, years)]
        if cells and cells[0] in mapping:
            if not dates or len(cells) != len(dates) + 1 or any(d is None for d in dates):
                raise ValueError("RBI weekly rate has no aligned date headers")
            for day, value in zip(dates, cells[1:]):
                number = _number(value)
                if number is not None:
                    out.append(_point(mapping[cells[0]], day, number, [cells[0], day.isoformat(), value]))
    if not out:
        raise ValueError("RBI weekly rates have no mapped observations")
    return tuple(out)


def parse_policy(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    plain = _text(document.payload.decode("utf-8-sig", errors="replace"))
    stamp = re.search(r"Date\s*:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})", plain)
    day = _day(stamp[1]) if stamp else None
    if day is None or "Resolution of the Monetary Policy Committee" not in plain:
        return ()
    patterns = {
        "POLICY_REPO_DECISION": r"policy repo rate[^.]{0,150}?(?:at|to)\s+(\d+\.\d+)\s+per cent",
        "SDF_DECISION": r"standing deposit facility\s*\(SDF\)\s*rate[^.]{0,60}?(\d+\.\d+)\s+per cent",
        "MSF_DECISION": r"marginal standing facility\s*\(MSF\)\s*rate[^.]{0,90}?(\d+\.\d+)\s+per cent",
    }
    out = []
    for name, pattern in patterns.items():
        match = re.search(pattern, plain, re.I)
        if match:
            out.append(_point("IN.RBI." + name, day, Decimal(match[1]), [document.source_uri, match[0]],
                              revision_id="decision:" + document.source_uri.rsplit("=", 1)[-1]))
    if not out:
        raise ValueError("RBI policy resolution contains no recognized policy rates")
    return tuple(out)


async def fetch_policy(client):
    index, = await get_documents(client, (("rbi_policy_index", POLICY_URL, None),))
    text = index.payload.decode("utf-8-sig", errors="replace")
    urls = []
    # The official policy index labels resolution links 'Full Document'.
    # Examine at most four current documents, preserving the discovery capture.
    for href, label in re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", text, re.I | re.S):
        url = urljoin(POLICY_URL, html.unescape(href))
        parsed = urlsplit(url)
        if (_text(label) == "Full Document" and parsed.scheme == "https"
                and parsed.hostname in {"www.rbi.org.in", "rbi.org.in"}
                and parsed.path.lower() == "/scripts/bs_pressreleasedisplay.aspx"
                and re.fullmatch(r"prid=\d+", parsed.query) and url not in urls):
            urls.append(url)
    if not urls:
        raise ValueError("RBI policy index has no resolution links")
    documents = await get_documents(client, tuple(("rbi_policy", url, None) for url in urls[:4]))
    return (index, *documents)


def parse_policy_document(document):
    return () if document.label == "rbi_policy_index" else parse_policy(document)


def parse_wss(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    if document.label == "rbi_wss_index":
        return ()
    text = document.payload.decode("utf-8-sig", errors="replace")
    out = []
    if document.label == "rbi_liquidity_weekly":
        plain = _text(text)
        if not all(label in plain for label in ("Liquidity Operations by RBI", "OMO (Outright)", "Sale Purchase")):
            raise ValueError("RBI liquidity table headers changed")
        instruments = ("WSS_FIXED_REPO", "WSS_FIXED_REVERSE_REPO", "WSS_VRR", "WSS_VRRR",
                       "WSS_MSF", "WSS_SDF", "WSS_SLF", "OMO_SALES", "OMO_PURCHASES", "WSS_NET_INJECTION")
        for _, cells, _ in _rows(text):
            if len(cells) != 11 or (day := _day(cells[0])) is None:
                continue
            for instrument, value in zip(instruments, cells[1:]):
                number = _number(value)
                if number is not None:
                    out.append(_point("IN.RBI." + instrument, day, number, [instrument, *cells]))
    elif document.label in {"rbi_cd", "rbi_cp"}:
        kind = "CD" if document.label == "rbi_cd" else "CP"
        required = "Certificates of Deposit" if kind == "CD" else "Commercial Paper"
        if required not in _text(text) or "Rate of Interest" not in _text(text):
            raise ValueError("RBI credit table headers changed")
        for _, cells, _ in _rows(text):
            if len(cells) != 4 or (day := _day(cells[0])) is None:
                continue
            for suffix, raw in zip(("OUTSTANDING", "ISSUED"), cells[1:3]):
                if (value := _number(raw)) is not None:
                    out.append(_point(f"IN.RBI.{kind}_{suffix}", day, value, cells))
            band = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", cells[3])
            if band:
                if Decimal(band[1]) > Decimal(band[2]):
                    raise ValueError("RBI credit rate range is reversed")
                for suffix, value in zip(("LOW", "HIGH"), band.groups()):
                    out.append(_point(f"IN.RBI.{kind}_RATE_{suffix}", day, Decimal(value), cells))
    if not out:
        raise ValueError("RBI WSS contains no recognized dated observations")
    return tuple(out)


async def fetch_wss(client, *, credit: bool):
    today = datetime.now(UTC).date()
    friday = today - timedelta(days=(today.weekday() - 4) % 7)
    desired = ({"Certificates of Deposit": "rbi_cd", "Commercial Paper": "rbi_cp"} if credit
               else {"Liquidity Operations by RBI": "rbi_liquidity_weekly"})
    for week in range(3):
        day = friday - timedelta(days=week * 7)
        url = f"https://www.rbi.org.in/scripts/WSSViewDetail.aspx?PARAM1={day.month}%2F{day.day}%2F{day.year}&TYPE=Basic"
        try:
            index, = await get_documents(client, (("rbi_wss_index", url, None),))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                continue
            raise
        text = index.payload.decode("utf-8-sig", errors="replace")
        requests = []
        # RBI uses unquoted href attributes for these table links.
        for href, label in re.findall(r"<a\b[^>]*href=[\"']?(WSSView\.aspx\?Id=\d+)[\"']?[^>]*>(.*?)</a>", text, re.I | re.S):
            name = desired.get(_text(label))
            if name:
                requests.append((name, urljoin(url, href), None))
        if len(requests) == len(desired):
            return (index, *await get_documents(client, requests))
    raise ValueError("RBI WSS has no recent recognized tables")


def _rss_items(document):
    root = ET.fromstring(document.payload)
    for item in root.findall("./channel/item")[:100]:
        title, url = item.findtext("title", ""), item.findtext("link", "")
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname == "www.rbi.org.in"
                and parsed.path.lower() == "/scripts/bs_pressreleasedisplay.aspx"
                and re.fullmatch(r"prid=\d+", parsed.query)):
            yield title, url, item.findtext("description", ""), item.findtext("pubDate", "")


async def fetch_money_market(client):
    # The release feed retains non-empty sessions when the rolling MMO page
    # has advanced to a weekend/holiday report. No extra archive crawl.
    try:
        feed, = await get_documents(client, (("rbi_mmo_rss", RSS_URL, None),))
        if any(title.startswith("Money Market Operations as on ") for title, *_ in _rss_items(feed)):
            return (feed,)
    except (httpx.HTTPError, ET.ParseError):
        pass
    return await get_documents(client, (("rbi_mmo", MMO_URL, None),))


def parse_money_market_document(document):
    if document.label != "rbi_mmo_rss":
        return parse_mmo(document)
    out = []
    for title, url, body, timestamp in _rss_items(document):
        if not title.startswith("Money Market Operations as on "):
            continue
        points = parse_mmo(FetchedDocument(url, "text/html", (html.escape(title) + body).encode(), "rbi_mmo"))
        publication = parsedate_to_datetime(timestamp)
        # RBI RSS omits a zone; its native market timezone is Asia/Kolkata.
        # Retain estimated quality rather than claiming an exact UTC receipt.
        if publication.tzinfo is None:
            publication = publication.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        publication = publication.astimezone(UTC)
        for point in points:
            out.append(replace(point, source_publication_time=publication,
                               publication_time_policy=PublicationTimePolicy.INFER,
                               quality=QualityState.ESTIMATED))
    if not out:
        raise ValueError("RBI feed contains no dated money-market releases")
    # Several releases can repeat the same older reserve/govt balance date.
    # Select the newest release for that event before canonical persistence.
    by_event = {}
    for point in sorted(out, key=lambda point: point.source_publication_time):
        by_event[(point.instrument_id, point.event_time)] = point
    return tuple(by_event.values())


async def fetch_auctions(client):
    # A named, dated bootstrap reference preserves the observable 40Y auction
    # until newer full results enter the rolling official feed. It never
    # becomes a current daily quote merely because it is fetched again.
    return await get_documents(client, (("rbi_auction_rss", RSS_URL, None),
                                        ("rbi_auction_reference", AUCTION_REFERENCE_URL, None)))


def _auction_points(text, source_url):
    plain = _text(text)
    if "Government Stock - Full Auction Results" not in plain:
        return ()
    stamp = re.search(r"Date\s*:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})", plain)
    day = _day(stamp[1]) if stamp else None
    if day is None:
        raise ValueError("RBI government auction date is missing")
    securities = []
    out = []
    cut_off = False
    for _, cells, _ in _rows(text):
        if cells and cells[0] == "Auction Results":
            securities = cells[1:]
        if any("Cut-off" in cell for cell in cells):
            cut_off = True
        quotes = [re.search(r"\(YTM:\s*(\d+(?:\.\d+)?)%\)", cell) for cell in cells]
        if cut_off and securities and len(quotes) == len(securities) and all(quotes):
            for security, quote in zip(securities, quotes):
                maturity = re.fullmatch(r"(?:New|\d+(?:\.\d+)?%)\s+GS\s+(\d{4})", security)
                if maturity is None:
                    raise ValueError("RBI auction security identity is unrecognized")
                tenor = int(maturity[1]) - day.year
                # Separate primary-auction series: approximate year slot,
                # never used to fill a daily or monthly secondary-market node.
                if tenor in CURVE_TENORS:
                    out.append(_point(f"IN.RBI.GSEC_AUCTION_{tenor}Y", day, Decimal(quote[1]),
                                      [source_url, security, quote[0]], revision_id=f"auction:{security}:prid:{source_url.rsplit('=', 1)[-1]}",
                                      publication_time_policy=PublicationTimePolicy.UNKNOWN))
            break
    if not out:
        raise ValueError("RBI full auction results contain no recognized cut-off yields")
    return tuple(out)


def parse_auctions(document):
    if document.label != "rbi_auction_rss":
        return _auction_points(document.payload.decode("utf-8-sig"), document.source_uri)
    out = []
    for title, url, body, stamp in _rss_items(document):
        if title != "Government Stock - Full Auction Results":
            continue
        published = parsedate_to_datetime(stamp)
        prefix = f"Date : {published:%b %d, %Y} {title}"
        out.extend(_auction_points(prefix + body, url))
    return tuple(out)


RBIH_ARCHIVE = "https://dbie-common-scrapes.s3.ap-south-1.amazonaws.com/scrapes"
MONTHLY_REPORT = ("data/reports/publication/time-series-publications/handbook-of-statistics-on-the-indian-economy/"
                  "217--month-end-yield-of-sgl-transactions-in-government-dated-securities-for-various-maturities--new-format.csv")


async def fetch_monthly_curve(client):
    """RBI Innovation Hub's public, hash-checked export of DBIE report 217.

    The SDMX export drops maturity labels for this dataset. Only the labelled
    original report is suitable. Discovery paths are fixed, never URL inputs.
    """
    latest, = await get_documents(client, (("rbih_latest", f"{RBIH_ARCHIVE}/latest.json", None),))
    version = json.loads(latest.payload)["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
        raise ValueError("RBI archive version is invalid")
    manifest, = await get_documents(client, (("rbih_manifest", f"{RBIH_ARCHIVE}/{version}/MANIFEST.json", None),))
    entries = [entry for entry in json.loads(manifest.payload)["entries"] if entry.get("path") == MONTHLY_REPORT]
    if len(entries) != 1 or not 0 < entries[0]["bytes"] <= 2_000_000:
        raise ValueError("RBI archive has no unique bounded maturity report")
    report, = await get_documents(client, (("rbi_monthly_curve", f"{RBIH_ARCHIVE}/{version}/{MONTHLY_REPORT}", None),))
    entry = entries[0]
    if len(report.payload) != entry["bytes"] or hashlib.sha256(report.payload).hexdigest() != entry["sha256"]:
        raise ValueError("RBI maturity report does not match its archive manifest")
    return latest, manifest, report


def parse_monthly_curve(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    if document.label in {"rbih_latest", "rbih_manifest"}:
        return ()
    text = document.payload.decode("utf-8-sig")
    if ("Month-end Yield of SGL Transactions in Government Dated Securities" not in text
            or "Source : Reserve Bank of India." not in text or "Per cent per annum" not in text):
        raise ValueError("RBI maturity report identity or unit is missing")
    tenors = None
    out = []
    seen = set()
    for cells in csv.reader(io.StringIO(text), delimiter=";"):
        # The source repeats its attribution beside Apr-2018, after all 30
        # values. Strip only that exact extra cell, never a yield column.
        if len(cells) == 32 and cells[-1] == "Source : Reserve Bank of India.":
            cells = cells[:-1]
        if len(cells) > 1 and cells[1] == "1 Year":
            labels = [re.fullmatch(r"(\d+) Years?", cell) for cell in cells[1:]]
            if not all(labels):
                raise ValueError("RBI maturity labels changed")
            tenors = [int(label[1]) for label in labels]
            if tenors != list(range(1, 31)):
                raise ValueError("RBI monthly tenor columns do not align")
        if not cells or not re.fullmatch(r"[A-Z][a-z]{2}-\d{4}", cells[0]):
            continue
        if tenors is None or len(cells) != len(tenors) + 1:
            raise ValueError("RBI monthly row has no aligned maturity labels")
        first = datetime.strptime(cells[0], "%b-%Y").date()
        day = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        if day in seen:
            raise ValueError("RBI monthly report contains duplicate periods")
        seen.add(day)
        if day.year < 2001:
            # The INR pack's reviewed calendar starts in 2001.
            continue
        for tenor, raw in zip(tenors, cells[1:]):
            if tenor in CURVE_TENORS and (value := _number(raw)) is not None:
                out.append(_point(f"IN.RBI.SGL_MONTHLY_{tenor}Y", day, value,
                                  ["DBIE report 217", cells[0], f"{tenor}Y", raw],
                                  publication_time_policy=PublicationTimePolicy.UNKNOWN,
                                  revision_id=f"rbi-sgl-monthly:{tenor}Y"))
    if not out:
        raise ValueError("RBI monthly report contains no usable dated maturities")
    return tuple(out)
