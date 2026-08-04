#!/usr/bin/env python3
"""Rissaga, the Liquidity Lab news radar.

A rissaga is a seiche set off by a travelling atmospheric pressure jump:
weather arrives from outside and the basin swings. This service watches the
outside weather (news) and marks, every six hours, the few items that matter
to the lab's desks, from funding and institutions through market depth, risk
timing and information controls. Each item is paired with the relevant live
board or authority line.

WHAT IT IS NOT. It runs no generative model and changes no product score,
tier or regime. It emits linked facts, grounded board lines and bounded
fallback commentary for seven desk routes. The shared channel receives at
most two selected primary routes per sweep, ref tagged lab_rissaga.

SOURCES, all quota free: 23 live verified RSS feeds (official regulators
tier 1.0 down to market blogs 0.35) plus 11 Google News query feeds across
12 beats. Zero GDELT calls: attention context is read from the lab's own
already published packs. Board context comes from the product APIs, with an
explicit authority boundary where Riptide has no public board.

RANKING, deterministic and auditable (no LLM in the path, house style per
ml/mpc_gauge.py): weighted beat lexicon x source tier x recency x cross
outlet cluster bonus x board corroboration boost, then a seen ledger so a
story is marked once and resurfaces only on escalation. The lexicon below is
the tuning surface and it is versioned by content hash. Every relevant
external story is appended to a durable world readable JSONL subscriber
outbox before its seen suppression is committed. Stable story identifiers
group revisions, while dispatch identifiers separate escalations and later
re-entry.

Deploy (fleet convention): copy this file to /opt/rissaga/, env from
/etc/seiche-bot.env (token) plus /etc/rissaga.env, systemd rissaga.timer at
02,08,14,20:50 UTC. Modes: --run (fetch, route, append outbox, DM owner and
publish selected channel items), --print (fetch and compose to stdout without
mutating seen, board or outbox delivery state).

House prose rule holds in every user facing string here: commas, colons or
parentheses, never an em or en dash (offline test enforces it).
"""

from __future__ import annotations

import concurrent.futures
import email.utils
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# ------------------------------------------------------------------ env ----
TOKEN = os.environ.get("SEICHE_BOT_TOKEN", "")
OWNER_CHAT = os.environ.get("RISSAGA_OWNER_CHAT", "8727818928")
LAB_CHANNEL = os.environ.get("LAB_CHANNEL_ID", "")   # empty = channel off
# Who writes the channel post. "hermes": this radar only exports
# latest.json and the Hermes agent lane posts the desk reads (Mrinal's
# call 2026-08-03); "direct": this radar posts its deterministic reads
# itself (fallback lane); "off": no channel activity at all.
CHANNEL_MODE = os.environ.get("RISSAGA_CHANNEL_MODE", "direct").lower()
LATEST_EXPORT = "latest.json"    # world readable handoff for the Hermes lane
OUTBOX_EXPORT = "outbox.jsonl"   # durable multi-desk subscriber handoff
MAX_CHANNEL_POSTS = 2
OUTBOX_TTL_H = float(os.environ.get("RISSAGA_OUTBOX_TTL_H", "24"))
LAB_LINK = "https://t.me/LiquidityLabDesk"
STATE_DIR = os.environ.get("RISSAGA_STATE", "/var/lib/rissaga")
SEICHE_API = os.environ.get("SEICHE_API", "https://api.seiche.info").rstrip("/")
LL_API = os.environ.get("LIQUILENS_API", "https://api.liquilens.in/api").rstrip("/")
UT_BASE = os.environ.get("UNDERTOW_BASE", "https://api.seiche.info/undertow").rstrip("/")
PALIMPSEST_BOARD = os.environ.get(
    "PALIMPSEST_BOARD",
    "https://api.seiche.info/palimpsest/osint/osint-china.json",
)
PALIMPSEST_DDTI = os.environ.get(
    "PALIMPSEST_DDTI",
    "https://palimpsest.info/readings/ddti-latest.json",
)
TG = f"https://api.telegram.org/bot{TOKEN}"
UA = os.environ.get("RISSAGA_UA",
                    "Mozilla/5.0 (compatible; rissaga/1.0; +https://seiche.info)")

IST = timezone(timedelta(hours=5, minutes=30))

MARK_BAR = float(os.environ.get("RISSAGA_MARK_BAR", "3.0"))
CHANNEL_BAR = float(os.environ.get("RISSAGA_CHANNEL_BAR", "4.5"))
ROUTE_BAR = float(os.environ.get("RISSAGA_ROUTE_BAR", "3.0"))
MAX_MARKED = 5
MAX_DESK_COVERAGE = 2
MAX_AGE_H = 36.0      # external items older than this never rank
FEED_ITEM_CAP = 40
SEEN_TTL_H = 48.0     # a marked story stays suppressed this long
SEEN_ESCALATE = 1.3   # unless its score grows by this factor
FEED_CACHE_TTL_H = 24.0

# ================================================================= BEATS ===
# THE TUNING SURFACE. Each beat: which desk owns it, weighted terms (word
# boundary regexes, weight 1..6, each term counts once per item, sum capped
# at 10), and the Google News query that gives the beat commercial breadth.
# Mrinal: edit weights and terms freely, the test suite only checks shape.
BEATS: dict[str, dict] = {
    "plumbing": {
        "desk": "SEICHE", "emoji": "\U0001f30a", "label": "plumbing",
        "terms": [
            (r"discount window", 5), (r"standing repo facility", 5),
            (r"\bSRF\b", 4), (r"repo market", 4), (r"reverse repo", 3),
            (r"\bRRP\b", 3), (r"\bSOFR\b", 4), (r"repo rate", 2),
            (r"reserve scarcity", 6), (r"ample reserves", 3),
            (r"bank reserves", 3), (r"money market fund", 3),
            (r"bill issuance", 3), (r"treasury bills", 2),
            (r"debt ceiling", 4), (r"treasury general account", 4),
            (r"\bTGA\b", 3), (r"balance sheet runoff", 4),
            (r"quantitative tightening", 4), (r"funding stress", 5),
            (r"funding squeeze", 5), (r"repo spike", 6),
            (r"collateral shortage", 5), (r"cross currency basis", 4),
            (r"swap line", 5), (r"lender of last resort", 5),
        ],
        "gnews": '"discount window" OR "repo market" OR "reverse repo" OR SOFR OR "money market"',
    },
    "bank_stress": {
        "desk": "LIQUILENS", "emoji": "\U0001f3e6", "label": "bank stress",
        "terms": [
            (r"bank failure", 6), (r"bank collapse", 6), (r"bank run", 6),
            (r"deposit run", 6), (r"receivership", 6), (r"\bFDIC\b", 3),
            (r"deposit (?:flight|outflows?)", 5), (r"deposit withdrawals?", 4),
            (r"uninsured deposits", 5), (r"brokered deposits", 4),
            (r"unrealized losses", 5), (r"held to maturity", 4),
            (r"commercial real estate", 3), (r"\bCRE\b", 3),
            (r"regional bank", 3), (r"capital shortfall", 5),
            (r"emergency capital", 5), (r"bank rescue", 5), (r"bailout", 4),
            (r"prompt corrective action", 5), (r"moratorium", 4),
            (r"credit rating (?:cut|downgrade)", 3), (r"downgrade", 2),
            (r"\bFHLB\b", 4), (r"liquidity support", 4), (r"bank seiz\w+", 5),
        ],
        "gnews": '"bank failure" OR "deposit outflows" OR "bank run" OR FDIC OR "regional bank"',
    },
    "private_credit": {
        "desk": "LIQUILENS", "emoji": "\U0001f3e6", "label": "private credit",
        "terms": [
            (r"private credit", 4), (r"shadow bank\w*", 4), (r"\bNBFC\b", 4),
            (r"\bNDFI\b", 5), (r"direct lending", 3), (r"\bBDC\b", 3),
            (r"redemption(?:s)? (?:freeze|frozen|halt\w*|gate\w*)", 6),
            (r"gated redemptions", 5), (r"side pocket", 5),
            (r"NAV markdown", 4), (r"credit fund", 3), (r"fund suspension", 5),
            (r"microfinance", 3), (r"leveraged loans?", 3), (r"\bCLO\b", 3),
        ],
        "gnews": '"private credit" OR "shadow banking" OR "redemptions" fund OR NBFC',
    },
    "market_liquidity": {
        "desk": "UNDERTOW", "emoji": "\U0001f300", "label": "market depth",
        "terms": [
            (r"basis trade", 5), (r"market depth", 4), (r"margin calls?", 5),
            (r"forced selling", 5), (r"fire sale", 5), (r"deleveraging", 4),
            (r"liquidity (?:vacuum|spiral)", 6), (r"liquidity crunch", 5),
            (r"dealer balance sheets?", 4), (r"swap spreads?", 4),
            (r"off the run", 4), (r"treasury market (?:dysfunction|functioning)", 6),
            (r"flash crash", 5), (r"circuit breakers?", 3), (r"unwinds?", 3),
            (r"\bCTA\b", 2), (r"bid ask", 3), (r"market makers?", 3),
        ],
        "gnews": '"basis trade" OR "margin calls" OR "forced selling" OR "market liquidity"',
    },
    "stablecoin_rails": {
        "desk": "LIQUILENS", "emoji": "\U0001f3e6", "label": "rails",
        "terms": [
            (r"stablecoins?", 3), (r"depeg\w*", 6), (r"\bUSDC\b", 3),
            (r"\bUSDT\b", 3), (r"tether", 3), (r"attestation", 4),
            (r"tokenized treasur\w+", 4), (r"money market token", 4),
            (r"stablecoin issuer", 4), (r"reserve (?:report|breakdown)", 3),
            (r"GENIUS Act", 3), (r"custodian", 3),
        ],
        "gnews": 'stablecoin depeg OR tether reserves OR USDC OR "tokenized treasury"',
    },
    "crypto_stress": {
        "desk": "LIQUILENS", "emoji": "\U0001f3e6", "label": "crypto leverage",
        "terms": [
            (r"liquidation cascade", 6), (r"liquidations?", 3),
            (r"withdrawals? (?:halted|paused|suspended)", 6),
            (r"exchange (?:insolven\w+|halts?)", 6), (r"crypto lender", 4),
            (r"perp\w* funding", 3), (r"defi exploit", 4),
            (r"bitcoin etf (?:outflows?|redemptions?)", 4),
            (r"leverage flush", 4), (r"crypto exchange", 2),
        ],
        "gnews": 'crypto liquidations OR "withdrawals halted" exchange OR "crypto lender"',
    },
    "policy_shock": {
        "desk": "SEICHE", "emoji": "\U0001f30a", "label": "policy shock",
        "terms": [
            (r"emergency (?:meeting|session)", 6), (r"intermeeting", 6),
            (r"unscheduled meeting", 5), (r"emergency (?:rate|cut|hike)", 6),
            (r"liquidity facilit\w+", 5), (r"swap lines? activated", 6),
            (r"central bank intervention", 5), (r"capital controls", 5),
            (r"bank holiday", 6), (r"systemic risk exception", 6),
            (r"emergency lending", 5),
        ],
        "gnews": '"emergency meeting" central bank OR "systemic risk" OR "liquidity facility"',
    },
    "india_watch": {
        "desk": "LIQUILENS", "emoji": "\U0001f3e6", "label": "india watch",
        "terms": [
            (r"RBI (?:action|restrictions?|penalty|supersede\w*)", 4),
            (r"PCA framework", 3), (r"co.?operative bank", 3),
            (r"\bDICGC\b", 4), (r"RBI moratorium", 5), (r"\bNPA\b", 2),
        ],
        "gnews": "",   # ET and Mint feeds carry this beat, no gnews query
    },
    "corporate_stress": {
        "desk": "CORPORATE", "emoji": "\U0001f3ed", "label": "corporate stress",
        "terms": [
            (r"commercial paper", 4), (r"credit lines?", 3),
            (r"revolver draw\w*", 5), (r"drawdown wave", 5), (r"\bSLOOS\b", 4),
            (r"chapter 11", 4), (r"bankruptc\w+", 4),
            (r"corporate defaults?", 5), (r"interest coverage", 4),
            (r"leveraged loan defaults?", 5), (r"downgrades?", 2),
            (r"mass layoffs", 3), (r"capex cuts?", 3),
            (r"missed (?:coupon|payment)", 5), (r"debt restructuring", 4),
        ],
        "gnews": '"commercial paper" OR "chapter 11" OR "corporate defaults" OR "credit line" drawdown',
    },
    "real_economy": {
        "desk": "REALECON", "emoji": "\U0001f6d2", "label": "real economy",
        "terms": [
            (r"jobless claims", 4), (r"nonfarm payrolls", 4),
            (r"unemployment rate", 3), (r"\bCPI\b", 3), (r"inflation", 2),
            (r"retail sales", 3), (r"consumer delinquenc\w+", 5),
            (r"credit card delinquenc\w+", 5), (r"household debt", 4),
            (r"\bGST\b collections?", 3), (r"\bIIP\b", 3), (r"\bPMI\b", 3),
            (r"monsoon", 2), (r"food (?:prices|inflation)", 3),
            (r"real wages", 3), (r"consumer confidence", 3),
        ],
        "gnews": '"jobless claims" OR "consumer delinquencies" OR CPI inflation OR payrolls',
    },
    "information_controls": {
        "desk": "PALIMPSEST", "emoji": "\U0001f9f1",
        "label": "information controls",
        "terms": [
            (r"internet censorship", 5), (r"state censorship", 5),
            (r"information controls?", 4), (r"digital repression", 4),
            (r"great firewall", 6), (r"\bGFW\b", 4), (r"\bOONI\b", 5),
            (r"censored planet", 5), (r"citizen lab", 4),
            (r"website block\w*", 4), (r"internet shutdown", 6),
            (r"network blackout", 5), (r"content remov\w+", 4),
            (r"deleted posts?", 4), (r"deletion threat", 5),
            (r"we(?:ibo|chat).{0,30}(?:censor|delet|remov|block)", 5),
            (r"(?:censor|delet|remov|block).{0,30}we(?:ibo|chat)", 5),
            (r"Chinese (?:AI|LLM).{0,35}(?:refus|censor|redirect)", 5),
            (r"generative firewall", 6), (r"VPN crackdown", 5),
        ],
        "gnews": ('China censorship OR "Great Firewall" OR "deleted posts" '
                  'OR "Chinese AI" refusal OR "internet shutdown"'),
    },
    "risk_timing": {
        "desk": "RIPTIDE", "emoji": "\U0001f9ed", "label": "risk timing",
        "terms": [
            (r"volatility (?:spike|surge|shock)", 5), (r"\bVIX\b", 3),
            (r"risk.?off", 4), (r"equity selloff", 4),
            (r"stock market selloff", 4), (r"market turmoil", 4),
            (r"drawdown", 3), (r"flight to quality", 5),
            (r"safe haven", 3), (r"credit spreads?", 3),
            (r"cross.?asset", 3), (r"systematic sell\w*", 5),
            (r"volatility control", 4), (r"portfolio deleveraging", 5),
            (r"liquidation cascade", 5), (r"margin calls?", 4),
            (r"bitcoin (?:slump|crash|selloff)", 4),
            (r"crypto market (?:slump|crash|selloff)", 4),
        ],
        "gnews": ('"volatility spike" OR VIX OR "risk off" OR '
                  '"equity selloff" OR "flight to quality"'),
    },
}

# Routine noise killed unless the item also carries a strong distress term
# (beat base score 6 or more survives the kill list).
KILL = [
    r"week ahead", r"what to watch", r"\bpreview\b", r"is set to speak",
    r"scheduled to speak", r"earnings calendar", r"market wrap",
    r"stocks (?:close|end|finish)", r"wall street (?:closes|ends)",
    r"\bpodcast\b", r"\bnewsletter\b", r"\bhoroscope\b", r"live updates",
    r"opinion:", r"5 things to know", r"morning bid",
]

ANGLES = {
    "plumbing": "pair it with the gauge line and the RRP path, plumbing before headlines",
    "bank_stress": "put the name against the radar, tiers and PD movement are citable",
    "private_credit": "NDFI concentration is the underwritten story, cite the watch ratio",
    "market_liquidity": "exit cost and depth, the backbone map says who carries it",
    "stablecoin_rails": "rails watch has issuer thresholds on record, cite the state",
    "crypto_stress": "the exposure register and regime lens are on record, cite them",
    "policy_shock": "what the facility does to the gauge inputs, mechanics over drama",
    "india_watch": "supervisory tape context, actions run ahead of ratings",
    "corporate_stress": "read it through the transmission board, channel by channel",
    "real_economy": "the household and India boards say if stress is arriving downstream",
    "information_controls": "separate network blocking, content deletion and model refusal before reading coincidence",
    "risk_timing": "test persistence across volatility, spreads and trend before treating one shock as a regime",
}

DESK_NICE = {"SEICHE": "Seiche", "LIQUILENS": "LiquiLens",
             "UNDERTOW": "Undertow", "CORPORATE": "Corporate",
             "REALECON": "Real economy", "PALIMPSEST": "Palimpsest",
             "RIPTIDE": "Riptide"}

# These are editorial boundaries, not ornamental names. Consumers may write
# their own commentary, but each route's deterministic fallback must remain in
# the desk's register and must not turn a news match into a prediction.
DESK_PERSONAS = {
    "SEICHE": "funding mechanics, facilities and reserve plumbing",
    "LIQUILENS": "institution balance sheets, thresholds and failure paths",
    "UNDERTOW": "quoted depth, exit cost and market carrying capacity",
    "CORPORATE": "transmission from funding access into company cash flows",
    "REALECON": "households, employment, prices and downstream demand",
    "PALIMPSEST": "network blocking, content deletion and model refusal as distinct measurements",
    "RIPTIDE": "risk persistence across volatility, spreads and trend",
}

FALLBACK_COMMENTARY = {
    "plumbing": "The mechanical question is whether funding data confirm the headline.",
    "bank_stress": "The institution belongs against the failure radar before the event is treated as systemic.",
    "private_credit": "The transmission question is whether nonbank concentration is reaching regulated balance sheets.",
    "market_liquidity": "Quoted depth and exit cost decide whether the headline has become an execution problem.",
    "stablecoin_rails": "Reserve quality and redemption rails separate plumbing stress from token noise.",
    "crypto_stress": "Leverage and withdrawal conditions separate a positioning flush from balance sheet stress.",
    "policy_shock": "Facility mechanics matter before the policy label.",
    "india_watch": "Supervisory action is the signal, ratings and market reaction arrive later.",
    "corporate_stress": "Trace the shock from funding access into investment, payrolls and supplier terms.",
    "real_economy": "Household and India channels show whether financial stress is reaching demand.",
    "information_controls": "Treat blocking, deletion and refusal as separate layers, then ask whether independent measurements agree.",
    "risk_timing": "The relevant read is whether volatility, spreads and trend confirm persistence beyond one session.",
}

# ================================================================= FEEDS ===
# (key, url, tier). Live verified 2026-08-03; a feed that rots reports
# unavailable in the dispatch footer instead of failing silently.
FEEDS: list[tuple[str, str, float]] = [
    ("fed_press", "https://www.federalreserve.gov/feeds/press_all.xml", 1.0),
    ("fdic", "https://public.govdelivery.com/topics/USFDIC_26/feed.rss", 1.0),
    ("occ", "https://www.occ.gov/rss/occ_news.xml", 1.0),
    ("rbi_press", "https://rbi.org.in/pressreleases_rss.xml", 1.0),
    ("ecb", "https://www.ecb.europa.eu/rss/press.html", 1.0),
    ("sec", "https://www.sec.gov/news/pressreleases.rss", 0.95),
    ("fsb", "https://www.fsb.org/feed/", 0.9),
    ("bbg_markets", "https://feeds.bloomberg.com/markets/news.rss", 0.8),
    ("bbg_econ", "https://feeds.bloomberg.com/economics/news.rss", 0.8),
    ("wsj_markets", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", 0.8),
    ("ft_home", "https://www.ft.com/rss/home", 0.8),
    ("ft_markets", "https://www.ft.com/markets?format=rss", 0.8),
    ("cnbc_markets",
     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
     0.75),
    ("yahoo_fin", "https://finance.yahoo.com/news/rssindex", 0.55),
    ("mw_top", "https://feeds.content.dowjones.io/public/rss/mw_topstories", 0.5),
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", 0.6),
    ("theblock", "https://www.theblock.co/rss.xml", 0.6),
    ("ooni", "https://ooni.org/index.xml", 0.9),
    ("citizen_lab", "https://citizenlab.ca/feed/", 0.9),
    ("china_digital_times", "https://chinadigitaltimes.net/feed/", 0.85),
    ("et_markets",
     "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", 0.5),
    ("mint_markets", "https://www.livemint.com/rss/markets", 0.5),
    ("zerohedge", "https://feeds.feedburner.com/zerohedge/feed", 0.35),
]

GNEWS_TIER = 0.65

SOURCE_NICE = {
    "fed_press": "Federal Reserve", "fdic": "FDIC", "occ": "OCC",
    "rbi_press": "Reserve Bank of India", "ecb": "ECB", "sec": "SEC",
    "fsb": "FSB", "bbg_markets": "Bloomberg",
    "bbg_econ": "Bloomberg Econ", "wsj_markets": "WSJ", "ft_home": "FT",
    "ft_markets": "FT Markets", "cnbc_markets": "CNBC",
    "yahoo_fin": "Yahoo Finance", "mw_top": "MarketWatch",
    "coindesk": "CoinDesk", "theblock": "The Block", "ooni": "OONI",
    "citizen_lab": "Citizen Lab", "china_digital_times": "China Digital Times",
    "et_markets": "Economic Times", "mint_markets": "Mint",
    "zerohedge": "ZeroHedge",
}


def gnews_feeds() -> list[tuple[str, str, float]]:
    out = []
    for beat, spec in BEATS.items():
        q = spec.get("gnews")
        if not q:
            continue
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(q)
               + "&hl=en-US&gl=US&ceid=US:en")
        out.append((f"gnews_{beat}", url, GNEWS_TIER))
    return out


def all_feeds() -> list[tuple[str, str, float]]:
    return FEEDS + gnews_feeds()


def lexicon_version() -> str:
    """Content hash of the ranking surface, stamped into every dispatch
    history row so a tuning change is visible in the record."""
    blob = json.dumps({"beats": {k: v["terms"] for k, v in BEATS.items()},
                       "kill": KILL,
                       "bars": [MARK_BAR, CHANNEL_BAR, ROUTE_BAR]},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


_COMPILED: dict[str, list[tuple[re.Pattern, float]]] = {
    beat: [(re.compile(pat, re.I), float(w)) for pat, w in spec["terms"]]
    for beat, spec in BEATS.items()
}
_KILL = [re.compile(p, re.I) for p in KILL]


# ------------------------------------------------------------- plumbing ----
def tg_call(method: str, payload: dict) -> dict | None:
    req = urllib.request.Request(
        f"{TG}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
        except Exception:
            body = {"ok": False, "error_code": exc.code}
        print(f"tg {method} failed: {exc.code} {body.get('description', '')}",
              file=sys.stderr)
        return body
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"tg {method} failed: {exc}", file=sys.stderr)
        return None


def send(chat_id: int, text: str, keyboard: list | None = None) -> dict | None:
    """Chunk at line seams under Telegram's cap, HTML mode, honor 429 once,
    fall back to plain text on a parse 400. Same lineage as seiche_bot."""
    res = None
    while text:
        cut = len(text)
        if cut > 4000:
            nl = text.rfind("\n", 1, 4000)
            cut = nl if nl > 0 else 4000
        chunk, text = text[:cut], text[cut:].lstrip("\n")
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if keyboard and not text:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        res = tg_call("sendMessage", payload)
        if isinstance(res, dict) and not res.get("ok") and res.get("error_code") == 429:
            wait = (res.get("parameters") or {}).get("retry_after") or 3
            time.sleep(min(float(wait), 30.0))
            res = tg_call("sendMessage", payload)
        if isinstance(res, dict) and not res.get("ok") and res.get("error_code") == 400:
            res = tg_call("sendMessage",
                          {k: v for k, v in payload.items() if k != "parse_mode"})
    return res


def _state_path(name: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, name)


def load_state(name: str, default):
    try:
        with open(_state_path(name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def save_state(name: str, value) -> None:
    tmp = _state_path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    os.replace(tmp, _state_path(name))


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _http_get(url: str, headers: dict | None = None, timeout: int = 20):
    """One raw GET: (status, headers, body bytes). 304 returns empty body.
    The single seam every offline test stubs."""
    hdrs = {"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, "
                                        "application/json, application/xml, */*"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, dict(exc.headers or {}), b""
        raise


def get_json(url: str, timeout: int = 20):
    try:
        status, _, body = _http_get(url, timeout=timeout)
    except Exception as exc:
        print(f"GET {url} failed: {exc}", file=sys.stderr)
        return None
    if status != 200:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None


# ----------------------------------------------------------- feed parse ----
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAGSTRIP = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_DTD = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.I)


def _lenient_root(data: bytes):
    # Stdlib ElementTree never resolves external entities, but entity
    # expansion (billion laughs) depends on the system expat. No legitimate
    # RSS or Atom feed ships a DTD, so any document that declares one is
    # rejected outright: cheaper and stricter than defusedxml, and it keeps
    # the fleet's single file stdlib deploy contract.
    if _DTD.search(data[:4096]):
        raise ValueError("DTD or entity declaration rejected")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        txt = data.decode("utf-8", "replace")
        txt = txt[txt.find("<"):]
        txt = re.sub(r"&(?!#?[A-Za-z0-9]{1,10};)", "&amp;", txt)
        txt = "".join(ch for ch in txt if ch >= " " or ch in "\t\n\r")
        return ET.fromstring(txt.encode())


def _parse_ts(raw: str, fallback: float) -> float:
    raw = (raw or "").strip()
    if not raw:
        return fallback
    try:
        return email.utils.parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return fallback


def _clean_text(s: str) -> str:
    return _WS.sub(" ", html.unescape(_TAGSTRIP.sub(" ", s or ""))).strip()


def parse_feed(data: bytes, key: str, tier: float, now_ts: float) -> list[dict]:
    """RSS2 or Atom bytes to item dicts. An undated item is treated as 12h
    old rather than fresh, so a feed without dates cannot dominate."""
    root = _lenient_root(data)
    fallback_ts = now_ts - 12 * 3600
    items = []
    for it in root.iter("item"):
        title = _clean_text(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source_name = (_clean_text(src_el.text if src_el is not None else "")
                       or SOURCE_NICE.get(key, key))
        if key.startswith("gnews_") and " - " in title:
            title, _, tail = title.rpartition(" - ")
            source_name = _clean_text(tail) or source_name
        items.append({
            "key": key, "tier": tier, "title": title, "link": link,
            "snippet": _clean_text(it.findtext("description") or "")[:300],
            "source_name": source_name,
            "ts": _parse_ts(it.findtext("pubDate") or "", fallback_ts),
        })
    if not items:
        for e in root.iter(f"{_ATOM}entry"):
            link_el = e.find(f"{_ATOM}link")
            items.append({
                "key": key, "tier": tier,
                "title": _clean_text(e.findtext(f"{_ATOM}title") or ""),
                "link": (link_el.get("href") if link_el is not None else "") or "",
                "snippet": _clean_text(e.findtext(f"{_ATOM}summary") or "")[:300],
                "source_name": SOURCE_NICE.get(key, key),
                "ts": _parse_ts(e.findtext(f"{_ATOM}updated")
                                or e.findtext(f"{_ATOM}published") or "", fallback_ts),
            })
    return [it for it in items if it["title"]][:FEED_ITEM_CAP]


def fetch_feeds(now_ts: float) -> tuple[list[dict], dict]:
    """All feeds in parallel with conditional GET. Per feed failure is a
    reported absence, never a crash; a fresh cache stands in when it can."""
    cache = load_state("feeds_http.json", {})
    health: dict[str, str] = {}
    items: list[dict] = []

    def one(key: str, url: str, tier: float):
        entry = cache.get(key) or {}
        hdrs = {}
        if entry.get("etag"):
            hdrs["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            hdrs["If-Modified-Since"] = entry["last_modified"]
        try:
            status, rh, body = _http_get(url, headers=hdrs)
        except Exception as exc:
            return key, f"unavailable: {type(exc).__name__}", entry, None
        if status == 304 and entry.get("items"):
            return key, "ok (304)", entry, entry["items"]
        if status != 200:
            return key, f"unavailable: http {status}", entry, None
        try:
            parsed = parse_feed(body, key, tier, now_ts)
        except Exception as exc:
            return key, f"unavailable: parse {type(exc).__name__}", entry, None
        fresh = {"etag": rh.get("ETag") or rh.get("Etag"),
                 "last_modified": rh.get("Last-Modified"),
                 "fetched_ts": now_ts, "items": parsed}
        return key, "ok", fresh, parsed

    feeds = all_feeds()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, k, u, t) for k, u, t in feeds]
        for fut in concurrent.futures.as_completed(futs):
            key, verdict, entry, parsed = fut.result()
            health[key] = verdict
            if parsed is None:
                cached = (cache.get(key) or {})
                if cached.get("items") and \
                        now_ts - cached.get("fetched_ts", 0) < FEED_CACHE_TTL_H * 3600:
                    parsed = cached["items"]
                    health[key] = verdict + ", using cache"
                else:
                    parsed = []
            else:
                cache[key] = entry
            items.extend(parsed)
    save_state("feeds_http.json", cache)
    return items, health


# ----------------------------------------------------------- board reads ---
def read_boards() -> dict:
    """The lab's own surfaces, each independent, absence declared."""
    def grab(name, url, dig):
        data = get_json(url)
        try:
            return name, (dig(data) if data is not None else None)
        except Exception as exc:
            print(f"board {name} dig failed: {exc}", file=sys.stderr)
            return name, None

    def d_gauge(d):
        return {"regime": d.get("regime"), "index": d.get("index")}

    def d_overview(d):
        sc = ((d.get("engines") or {}).get("scuttlebutt") or {})
        latest = sc.get("latest") or {}
        return {"loudest": latest.get("loudest"),
                "loudest_attention": latest.get("loudest_attention")}

    def d_ll_board(d):
        # live shape 2026-08-03: top level tiers counts, rows carry grade
        tiers = d.get("tiers") if isinstance(d.get("tiers"), dict) else {}
        counts = {str(k).lower(): int(v) for k, v in tiers.items()
                  if isinstance(v, (int, float))}
        rows = d.get("rows") or []
        top = None
        for r in rows if isinstance(rows, list) else []:
            g = str(r.get("grade") or r.get("tier") or "").lower()
            if g in ("red", "orange"):
                top = r.get("name") or r.get("slug")
                break
        total = sum(counts.values()) or (len(rows) if isinstance(rows, list) else 0)
        return {"red": counts.get("red", 0), "orange": counts.get("orange", 0),
                "yellow": counts.get("yellow", 0), "total": total, "top": top}

    def d_news(d):
        # live shape: rows[].name + evidence.results[].url
        hot = []
        for r in (d.get("rows") or []):
            stress = r.get("news_stress")
            if isinstance(stress, (int, float)) and stress >= 40:
                ev = (r.get("evidence") or {}).get("results") or []
                receipt = ev[0].get("url") if ev and isinstance(ev[0], dict) else None
                hot.append({"name": r.get("name") or r.get("display_name")
                            or r.get("slug"), "stress": stress,
                            "receipt": receipt})
        hot.sort(key=lambda h: -h["stress"])
        return hot[:3]

    def d_rails(d):
        # live shape: regime + regime_reasons
        return {"state": d.get("regime") or d.get("state"),
                "why": (d.get("regime_reasons") or [None])[0]}

    def d_crypto(d):
        # live shape: keys["BTC-USD"].regime
        keys = d.get("keys") or {}
        btc = str((keys.get("BTC-USD") or {}).get("regime") or "")
        eth = str((keys.get("ETH-USD") or {}).get("regime") or "")
        vals = [v for v in (btc, eth) if v]
        if not vals:
            return None
        worst = ("ALARM" if "ALARM" in vals else
                 "WATCH" if "WATCH" in vals else vals[0])
        return {"state": worst, "btc": btc or None, "eth": eth or None}

    def d_corp(d):
        tr = d.get("transmission")
        out = {"verdict": None, "funding": None, "real": None}
        if isinstance(tr, dict):
            out["verdict"] = tr.get("verdict") or tr.get("state")
            out["funding"] = tr.get("funding_state")
            out["real"] = tr.get("real_state")
        elif tr:
            out["verdict"] = tr
        return out

    def d_india(d):
        ch = d.get("channels")
        if not isinstance(ch, dict) or not ch:
            return None
        off = [name for name, c in ch.items()
               if str((c or {}).get("state") or "CALM").upper() != "CALM"]
        return {"regime": d.get("regime"), "off": off, "n": len(ch)}

    def d_household(d):
        ch = d.get("channels") if isinstance(d.get("channels"), dict) else {}
        dq = ((ch.get("delinquencies") or {}).get("state"))
        return {"regime": d.get("regime"), "dq": dq}

    def d_ut_board(d):
        # live shape: segments{name: {tier}}. PARTIAL means partial
        # measure coverage, not stress, and must never read as stress.
        calm_tiers = {"NORMAL", "PARTIAL", "NO_DATA", "NODATA", "NA", "N/A",
                      "UNKNOWN", ""}
        segs = d.get("segments")
        if not isinstance(segs, dict) or not segs:
            return None
        off, partial = [], 0
        for name, seg in segs.items():
            tier = str((seg or {}).get("tier") or "").upper()
            if tier == "PARTIAL":
                partial += 1
            elif tier not in calm_tiers:
                off.append(f"{name} {tier}")
        return {"off": len(off), "total": len(segs), "partial": partial,
                "detail": ", ".join(off[:3]),
                "state": "WATCH" if off else "NORMAL"}

    def d_ndfi(d):
        # live shape: ndfi_watch list, rows carry bank
        rows = d.get("ndfi_watch") or d.get("rows") or []
        if isinstance(rows, list) and rows:
            r0 = rows[0]
            ratio = None
            for k in ("ndfi_ratio", "ndfi_to_tier1", "ndfi_tier1_ratio", "ratio"):
                if isinstance(r0.get(k), (int, float)):
                    ratio = r0[k]
                    break
            return {"top": r0.get("bank") or r0.get("name"), "ratio": ratio}
        return None

    def d_tape(d):
        rows = d.get("data") or d.get("items") or d.get("actions") or []
        if isinstance(rows, list) and rows:
            r0 = rows[0]
            return {"top": r0.get("title") or r0.get("headline")}
        return None

    def d_palimpsest(d):
        """Published OSINT board, with shape tolerance during its rollout.

        Only the upstream headline and health state are carried through. An
        absent value stays absent, because silence is not a healthy reading.
        """
        headline = d.get("headline") or d.get("title")
        summary = d.get("summary")
        if not headline and isinstance(summary, str):
            headline = summary
        elif not headline and isinstance(summary, dict):
            headline = summary.get("headline") or summary.get("title")
        health = d.get("health_status") or d.get("health") or d.get("status")
        if isinstance(health, dict):
            health = (health.get("status") or health.get("state")
                      or health.get("verdict"))
        if not headline:
            ddti = d.get("ddti") or {}
            ranked = ddti.get("ranked") if isinstance(ddti, dict) else []
            top = (ranked[0] if isinstance(ranked, list) and ranked
                   and isinstance(ranked[0], dict) else {})
            term, threat = top.get("term"), top.get("threat")
            if isinstance(term, str) and term.strip():
                headline = f"top observed term {term.strip()[:120]}"
                if (isinstance(threat, (int, float))
                        and not isinstance(threat, bool)
                        and math.isfinite(float(threat))):
                    headline += f", threat {threat}"
        coverage = d.get("coverage") or {}
        health_bits = [str(health)[:60]] if health else []
        if isinstance(coverage, dict):
            sources = coverage.get("observed_source_count")
            if (isinstance(sources, int) and not isinstance(sources, bool)
                    and sources >= 0):
                noun = "source" if sources == 1 else "sources"
                health_bits.append(f"{sources} observed {noun}")
            completeness = coverage.get("completeness")
            if isinstance(completeness, str) and completeness:
                health_bits.append(
                    "coverage not measured" if completeness == "not_measured"
                    else f"coverage {completeness[:40]}"
                )
        health = ", ".join(health_bits) or None
        if not headline and not health:
            return None
        return {"headline": str(headline)[:180] if headline else None,
                "health": str(health)[:180] if health else None}

    def d_palimpsest_ddti(d):
        ranked = d.get("ranked") or []
        top = ranked[0] if ranked and isinstance(ranked[0], dict) else {}
        term, threat = top.get("term"), top.get("threat")
        headline = None
        if term:
            headline = f"top ranked term {term}"
            if isinstance(threat, (int, float)):
                headline += f", threat {threat}"
        feed = d.get("feed_health") or {}
        health = None
        if isinstance(feed, dict):
            covered = feed.get("history_window_covered")
            if covered is True:
                health = "history window covered"
            elif covered is False:
                health = "history window incomplete"
            missing = feed.get("roles_missing")
            if isinstance(missing, list) and missing:
                tail = "roles missing " + ", ".join(str(x) for x in missing[:4])
                health = f"{health}, {tail}" if health else tail
        if not headline and not health:
            return None
        return {"headline": headline, "health": health}

    jobs = [
        ("seiche", f"{SEICHE_API}/api/gauge", d_gauge),
        ("scuttlebutt", f"{SEICHE_API}/api/overview", d_overview),
        ("ll", f"{LL_API}/failure-radar/board", d_ll_board),
        ("ll_news", f"{LL_API}/public-signals/news", d_news),
        ("rails", f"{LL_API}/public-signals/rails", d_rails),
        ("crypto", f"{LL_API}/public-signals/crypto-regime", d_crypto),
        ("corp", f"{LL_API}/public-signals/corporate-transmission", d_corp),
        ("ut", f"{UT_BASE}/board.json", d_ut_board),
        ("ndfi", f"{LL_API}/us-radar/ndfi", d_ndfi),
        ("rbi_tape", f"{LL_API}/economic/rbi-actions", d_tape),
        ("india", f"{LL_API}/public-signals/india-macro", d_india),
        ("household", f"{LL_API}/public-signals/household-credit", d_household),
        ("palimpsest", PALIMPSEST_BOARD, d_palimpsest),
        ("palimpsest_ddti", PALIMPSEST_DDTI, d_palimpsest_ddti),
    ]
    boards: dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(grab, n, u, f) for n, u, f in jobs]
        for fut in concurrent.futures.as_completed(futs):
            name, val = fut.result()
            boards[name] = val
    if not boards.get("palimpsest"):
        boards["palimpsest"] = boards.get("palimpsest_ddti")
    boards.pop("palimpsest_ddti", None)
    return boards


def board_line(beat: str, boards: dict) -> str:
    """One citable line of what the lab's own board says for this beat."""
    if beat in ("plumbing", "policy_shock"):
        g = boards.get("seiche")
        if g and g.get("regime"):
            line = f"Seiche gauge {g.get('index', '?')} {g['regime']}"
            sc = boards.get("scuttlebutt") or {}
            if sc.get("loudest"):
                line += f", loudest topic {sc['loudest']}"
            return line
    if beat in ("bank_stress", "india_watch"):
        b = boards.get("ll")
        if b:
            line = (f"LiquiLens board {b['red']} red, {b['orange']} orange "
                    f"of {b['total']}")
            if b.get("top"):
                line += f", watch {b['top']}"
            return line
    if beat == "private_credit":
        n = boards.get("ndfi")
        if n and n.get("top"):
            r = n.get("ratio")
            rtxt = f" {float(r):.2f}x" if isinstance(r, (int, float)) else ""
            return f"NDFI watch top {n['top']}{rtxt}"
        b = boards.get("corp")
        if b and b.get("verdict"):
            return f"corporate transmission {b['verdict']}"
    if beat == "market_liquidity":
        u = boards.get("ut")
        if u and u.get("total"):
            if u.get("off"):
                return (f"Undertow {u['off']} of {u['total']} segments "
                        f"stressed: {u['detail']}")
            line = f"Undertow segments NORMAL ({u['total']} tracked"
            if u.get("partial"):
                line += f", {u['partial']} partial coverage"
            return line + ")"
    if beat == "stablecoin_rails":
        r = boards.get("rails")
        if r and r.get("state"):
            line = f"Rails watch {r['state']}"
            if r.get("why"):
                line += f", {str(r['why'])[:70]}"
            return line
    if beat == "crypto_stress":
        c = boards.get("crypto")
        if c and c.get("state"):
            if c.get("btc") and c.get("eth"):
                return f"Crypto regime BTC {c['btc']}, ETH {c['eth']}"
            return f"Crypto regime {c['state']}"
    if beat == "corporate_stress":
        co = boards.get("corp")
        if co and co.get("verdict"):
            line = f"Corporate transmission {co['verdict']}"
            if co.get("funding"):
                line += f", funding {co['funding']}"
            if co.get("real"):
                line += f", real economy {co['real']}"
            return line
    if beat == "real_economy":
        bits = []
        ind = boards.get("india")
        if ind and ind.get("regime"):
            off = ind.get("off") or []
            offtxt = ", ".join(off[:3]) if off else "all channels calm"
            bits.append(f"India macro {ind['regime']} ({offtxt})")
        hh = boards.get("household")
        if hh and hh.get("regime"):
            line = f"US household {hh['regime']}"
            if hh.get("dq"):
                line += f", delinquencies {hh['dq']}"
            bits.append(line)
        if bits:
            return "; ".join(bits)
    if beat == "information_controls":
        pal = boards.get("palimpsest")
        if not pal:
            return "Palimpsest OSINT board UNAVAILABLE this run"
        bits = []
        if pal.get("health"):
            bits.append(f"health {pal['health']}")
        if pal.get("headline"):
            bits.append(f"headline {pal['headline']}")
        return "Palimpsest OSINT board: " + ", ".join(bits)
    if beat == "risk_timing":
        return ("Riptide authority: news is advisory only, paper sizing "
                "changes only from permitted cues")
    return "board read unavailable this run"


_BEAT_STRESSED = {
    "plumbing": lambda b: (b.get("seiche") or {}).get("regime") in ("EROSION", "STRAIN"),
    "policy_shock": lambda b: (b.get("seiche") or {}).get("regime") == "STRAIN",
    "bank_stress": lambda b: ((b.get("ll") or {}).get("red", 0)
                              + (b.get("ll") or {}).get("orange", 0)) > 0,
    "india_watch": lambda b: ((b.get("ll") or {}).get("red", 0)) > 0,
    "private_credit": lambda b: str((b.get("corp") or {}).get("verdict")) == "TRANSMITTING",
    "market_liquidity": lambda b: ((b.get("ut") or {}).get("off") or 0) > 0,
    "stablecoin_rails": lambda b: str((b.get("rails") or {}).get("state") or "")
                                  .upper() in ("WATCH", "ALARM"),
    "crypto_stress": lambda b: str((b.get("crypto") or {}).get("state") or "")
                               .upper() in ("WATCH", "ALARM"),
    "corporate_stress": lambda b: str((b.get("corp") or {}).get("verdict") or "")
                                  == "TRANSMITTING",
    "real_economy": lambda b: (str((b.get("india") or {}).get("regime") or "")
                               .upper() == "ALARM"
                               or str((b.get("household") or {}).get("regime")
                                      or "").upper() in ("WATCH", "ALARM")),
    "information_controls": lambda b: False,
    "risk_timing": lambda b: ((b.get("seiche") or {}).get("regime") == "STRAIN"
                              or ((b.get("ut") or {}).get("off") or 0) > 0),
}


def board_events(boards: dict, now_ts: float,
                 persist: bool = True) -> list[dict]:
    """The lab's own state changes, synthesized as first class items. The
    channel never gets these (the desks announce their own flips), the DM
    does."""
    prev = load_state("last_boards.json", {})
    events = []

    def ev(beat, title, link):
        events.append({"key": "board", "tier": 1.0, "title": title,
                       "link": link, "snippet": "", "source_name": "lab board",
                       "ts": now_ts, "board_event": True, "beat": beat})

    cur_regime = (boards.get("seiche") or {}).get("regime")
    old_regime = (prev.get("seiche") or {}).get("regime")
    if cur_regime and old_regime and cur_regime != old_regime:
        ev("plumbing", f"Seiche regime moved {old_regime} to {cur_regime}",
           "https://seiche.info")
    cur_ll = boards.get("ll") or {}
    old_ll = prev.get("ll") or {}
    if cur_ll and old_ll:
        cur_hot = cur_ll.get("red", 0) + cur_ll.get("orange", 0)
        old_hot = old_ll.get("red", 0) + old_ll.get("orange", 0)
        if cur_hot > old_hot:
            ev("bank_stress",
               f"LiquiLens board escalation, red plus orange {old_hot} to {cur_hot}",
               "https://liquilens.in")
    for name, beat, label, site in (
            ("rails", "stablecoin_rails", "Rails watch", "https://liquilens.in"),
            ("crypto", "crypto_stress", "Crypto regime", "https://liquilens.in"),
            ("corp", "private_credit", "Corporate transmission", "https://liquilens.in")):
        cur = ((boards.get(name) or {}).get("state")
               or (boards.get(name) or {}).get("verdict"))
        old = ((prev.get(name) or {}).get("state")
               or (prev.get(name) or {}).get("verdict"))
        if cur and old and cur != old:
            ev(beat, f"{label} moved {old} to {cur}", site)
    if persist:
        commit_boards(boards)
    return events


def commit_boards(boards: dict) -> None:
    """Persist only boards that answered, so absence cannot fake a flip."""
    prev = load_state("last_boards.json", {})
    keep = {k: v for k, v in boards.items() if v is not None}
    merged = {**prev, **keep}
    save_state("last_boards.json", merged)


# --------------------------------------------------------------- ranking ---
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "as",
         "at", "by", "with", "after", "amid", "over", "is", "are", "its",
         "his", "her", "their", "this", "that", "from", "into", "up", "down",
         "says", "say", "said", "new", "us", "will"}
_TOKEN = re.compile(r"[a-z0-9]{2,}")


def title_tokens(title: str) -> frozenset:
    return frozenset(t for t in _TOKEN.findall(title.lower()) if t not in _STOP)


def fingerprint(tokens: frozenset) -> str:
    core = sorted(t for t in tokens if len(t) > 3)[:10]
    return hashlib.sha1(" ".join(core).encode()).hexdigest()[:16]


def beat_scores(text: str) -> dict[str, float]:
    """Every positive lexical beat score, before source and age weights."""
    scores = {}
    for beat, pats in _COMPILED.items():
        s = sum(w for rx, w in pats if rx.search(text))
        if s > 0:
            scores[beat] = min(s, 10.0)
    return scores


def beat_score(text: str) -> tuple[str | None, float]:
    """The original primary beat contract, now derived from all matches."""
    scores = beat_scores(text)
    if not scores:
        return None, 0.0
    beat = max(scores, key=scores.get)
    return beat, scores[beat]


def _best_route_beats(score_maps, primary_beat: str | None = None) -> list[dict]:
    """Choose one highest relevance beat per desk across a story cluster."""
    best_by_desk: dict[str, dict] = {}
    for scores in score_maps:
        for beat, relevance in scores.items():
            desk = BEATS[beat]["desk"]
            old = best_by_desk.get(desk)
            if old is None or relevance > old["relevance"]:
                best_by_desk[desk] = {
                    "desk": desk, "beat": beat, "relevance": relevance,
                }
    primary_desk = BEATS[primary_beat]["desk"] if primary_beat else None
    routes = [route for route in best_by_desk.values()
              if route["desk"] == primary_desk
              or route["relevance"] >= ROUTE_BAR]
    routes.sort(key=lambda r: (r["desk"] != primary_desk,
                               -r["relevance"], r["desk"]))
    return routes


def route_beats(text: str) -> list[dict]:
    """Public scoring seam: zero or one best route for every matching desk."""
    primary, _ = beat_score(text)
    return _best_route_beats([beat_scores(text)], primary)


def recency_factor(age_h: float) -> float:
    if age_h <= 3:
        return 1.0
    if age_h <= 6:
        return 0.9
    if age_h <= 12:
        return 0.75
    if age_h <= 24:
        return 0.55
    if age_h <= MAX_AGE_H:
        return 0.35
    return 0.0


def rank(items: list[dict], boards: dict, now_ts: float,
         persist_seen: bool = True) -> list[dict]:
    """Score, cluster, boost, dedup against the seen ledger. Returns marked
    clusters, best first, each carrying its display fields."""
    scored = []
    for it in items:
        if it.get("board_event"):
            it["base"] = 6.0
            it["score"] = 6.0 * (1.25 if _BEAT_STRESSED.get(it["beat"],
                                                            lambda b: False)(boards) else 1.0)
            it["beat_scores"] = {it["beat"]: 6.0}
            it["tokens"] = title_tokens(it["title"])
            scored.append(it)
            continue
        text = f"{it['title']} {it['snippet']}"
        scores = beat_scores(text)
        if not scores:
            continue
        beat = max(scores, key=scores.get)
        base = scores[beat]
        if not beat or base <= 0:
            continue
        if base < 6 and any(rx.search(text) for rx in _KILL):
            continue
        age_h = max(0.0, (now_ts - it["ts"]) / 3600.0)
        rec = recency_factor(age_h)
        if rec == 0.0:
            continue
        it["beat"] = beat
        it["base"] = base
        it["beat_scores"] = scores
        it["age_h"] = age_h
        it["tokens"] = title_tokens(it["title"])
        it["score"] = base * it["tier"] * rec
        scored.append(it)

    scored.sort(key=lambda x: -x["score"])
    clusters: list[dict] = []
    for it in scored:
        placed = False
        for cl in clusters:
            inter = len(it["tokens"] & cl["tokens"])
            union = len(it["tokens"] | cl["tokens"]) or 1
            if inter / union >= 0.5:
                cl["members"].append(it)
                cl["sources"].add(it["source_name"])
                placed = True
                break
        if not placed:
            clusters.append({"rep": it, "members": [it], "tokens": it["tokens"],
                             "sources": {it["source_name"]}})

    seen = load_state("seen.json", {})
    marked = []
    for cl in clusters:
        rep = cl["rep"]
        n_extra = len(cl["sources"]) - 1
        score = rep["score"] * (1 + 0.12 * min(n_extra, 5))
        beat = rep["beat"]
        if _BEAT_STRESSED.get(beat, lambda b: False)(boards) and not rep.get("board_event"):
            score *= 1.25
        fp = fingerprint(cl["tokens"])
        old = seen.get(fp)
        if old and not rep.get("board_event"):
            age = now_ts - old.get("ts", 0)
            if age < SEEN_TTL_H * 3600 and score < old.get("score", 0) * SEEN_ESCALATE:
                continue
        cl["final"] = score
        cl["fp"] = fp
        cl["story_id"] = f"rissaga-{fp}"
        cl["n_sources"] = len(cl["sources"])
        cl["route_beats"] = _best_route_beats(
            (member.get("beat_scores") or {} for member in cl["members"]),
            rep["beat"],
        )
        marked.append(cl)

    marked.sort(key=lambda c: -c["final"])
    # The owner digest stays globally ranked, while the delivery handoff also
    # retains a small above-bar slice for every matched desk. Without this
    # union, a busy banking cycle can consume all five slots and make a valid
    # Palimpsest or Real Economy story unreachable by its own bot.
    selected_ids = {cl["story_id"] for cl in marked[:MAX_MARKED]}
    desks = sorted({route["desk"] for cl in marked
                    for route in cl.get("route_beats") or []})
    for desk in desks:
        coverage = [cl for cl in marked
                    if cl["final"] >= MARK_BAR
                    and not cl["rep"].get("board_event")
                    and any(route["desk"] == desk
                            for route in cl.get("route_beats") or [])]
        selected_ids.update(
            cl["story_id"] for cl in coverage[:MAX_DESK_COVERAGE])
    top = [cl for cl in marked if cl["story_id"] in selected_ids]
    if persist_seen:
        commit_seen(top, now_ts)
    return top


def commit_seen(marked: list[dict], now_ts: float) -> None:
    """Commit suppression only after every required durable export exists."""
    seen = load_state("seen.json", {})
    now_dt = datetime.fromtimestamp(now_ts, timezone.utc)
    now_iso = now_dt.isoformat(timespec="seconds")
    # If a prior process appended successfully and died before this commit,
    # retain that durable score. A lower retry score must not lower the
    # escalation baseline and make later seen/outbox thresholds disagree.
    outbox_scores = _outbox_active(_state_path(OUTBOX_EXPORT), now_dt)
    for cl in marked:
        if cl["final"] >= MARK_BAR:
            score = max(float(cl["final"]),
                        outbox_scores.get(cl.get("story_id"), 0.0))
            seen[cl["fp"]] = {"ts": now_ts, "score": score, "at": now_iso}
    seen = {fp: rec for fp, rec in seen.items()
            if now_ts - rec.get("ts", 0) < 7 * 24 * 3600}
    save_state("seen.json", seen)


# --------------------------------------------------------------- compose ---
def _age_txt(it: dict) -> str:
    h = it.get("age_h")
    if h is None:
        return "board event"
    if h < 1:
        return "under 1h old"
    if h < 24:
        return f"{int(h)}h old"
    return f"{h / 24:.1f}d old"


def _hot_names_block(boards: dict) -> str:
    hot = boards.get("ll_news") or []
    if not hot:
        return ""
    lines = ["", "<b>News Watch hot names</b> (LiquiLens, display only)"]
    for h in hot:
        line = f"  {esc(h['name'])}, news stress {h['stress']:.0f}"
        if h.get("receipt"):
            line += f' <a href="{esc(h["receipt"])}">receipt</a>'
        lines.append(line)
    return "\n".join(lines)


def compose(marked: list[dict], boards: dict, health: dict,
            now: datetime) -> str:
    n_ok = sum(1 for v in health.values() if v.startswith("ok"))
    stamp = (f"{now.strftime('%a %d %b, %H:%M UTC')} "
             f"({now.astimezone(IST).strftime('%H:%M IST')})")
    head = [f"\U0001f30a <b>Rissaga</b>, lab news radar, {stamp}"]
    body: list[str] = []
    # `rank` can retain extra desk-coverage items for private fanout. Keep the
    # human owner digest at its established global cap.
    above = [c for c in marked if c["final"] >= MARK_BAR][:MAX_MARKED]
    below = [c for c in marked if c["final"] < MARK_BAR]
    if not above:
        body.append("\nNothing cleared the bar this run. Closest to it:")
        show = below[:2]
    else:
        show = above
    for i, cl in enumerate(show, 1):
        rep = cl["rep"]
        spec = BEATS[rep["beat"]]
        tag = f"{spec['emoji']} <b>[{spec['desk']} · {spec['label']}]</b>"
        title = esc(rep["title"])
        link = rep.get("link") or ""
        head_line = (f'{i}. {tag} <a href="{esc(link)}">{title}</a>'
                     if link else f"{i}. {tag} {title}")
        src = esc(rep["source_name"])
        extra = cl["n_sources"] - 1
        src_line = f"{src} plus {extra} more" if extra > 0 else src
        desk = DESK_NICE.get(spec["desk"], spec["desk"])
        lines = [head_line,
                 f"   {src_line}, {_age_txt(rep)}, score {cl['final']:.1f}",
                 f"   {desk} desk: {esc(board_line(rep['beat'], boards))}"]
        if not rep.get("board_event"):
            lines.append(f"   Angle: {esc(ANGLES.get(rep['beat'], ''))}")
        body.append("\n" + "\n".join(lines))
    body.append(_hot_names_block(boards))
    foot = [""]
    if n_ok < len(health):
        bad = [k for k, v in health.items() if not v.startswith("ok")]
        foot.append(f"<i>{n_ok} of {len(health)} feeds answered, "
                    f"quiet: {esc(', '.join(sorted(bad)[:5]))}</i>")
    foot.append("<i>Facts only, links included. You write the prose. "
                "Pangram gate applies before anything ships.</i>")
    return "\n".join(head + [b for b in body if b] + foot)


def compose_channel(cl: dict, boards: dict) -> str:
    """Desk voice, one item, no first person, no advice."""
    rep = cl["rep"]
    spec = BEATS[rep["beat"]]
    title = esc(rep["title"])
    link = rep.get("link") or ""
    desk = DESK_NICE.get(spec["desk"], spec["desk"])
    line1 = (f"\U0001f30a <b>Rissaga marked this</b> "
             f"[{spec['desk']} · {spec['label']}]")
    line2 = f'<a href="{esc(link)}">{title}</a>' if link else title
    src = esc(rep["source_name"])
    extra = cl["n_sources"] - 1
    line3 = (f"{src} plus {extra} more outlets, {_age_txt(rep)}"
             if extra > 0 else f"{src}, {_age_txt(rep)}")
    line4 = f"{desk} desk: {esc(board_line(rep['beat'], boards))}"
    return "\n".join([line1, line2, line3, line4])


def _route_payloads(cl: dict, boards: dict) -> list[dict]:
    routes = []
    for matched in cl.get("route_beats") or []:
        beat = matched["beat"]
        spec = BEATS[beat]
        routes.append({
            "desk": spec["desk"],
            "desk_nice": DESK_NICE.get(spec["desk"], spec["desk"]),
            "beat": beat,
            "label": spec["label"],
            "relevance": round(float(matched["relevance"]), 2),
            "desk_line": board_line(beat, boards),
            "angle": ANGLES.get(beat, ""),
            "fallback_commentary": FALLBACK_COMMENTARY.get(beat, ""),
            "channel_candidate": False,
        })
    return routes


def dispatch_id(story_id: str, generated: str, score: float,
                n_sources: int) -> str:
    """Stable identifier for one story delivery revision."""
    blob = json.dumps({
        "story_id": story_id,
        "generated": generated,
        "score": float(score),
        "n_sources": int(n_sources),
    }, sort_keys=True, separators=(",", ":"))
    return ("rissaga-dispatch-"
            + hashlib.sha256(blob.encode()).hexdigest()[:20])


def latest_payload(marked: list[dict], boards: dict, now: datetime) -> dict:
    """Build the v2 handoff while preserving every v1 field and index."""
    generated = now.isoformat(timespec="seconds")
    items = []
    for cl in marked:
        rep = cl["rep"]
        spec = BEATS[rep["beat"]]
        score = round(cl["final"], 2)
        items.append({
            "story_id": cl["story_id"],
            "dispatch_id": dispatch_id(cl["story_id"], generated, score,
                                       cl["n_sources"]),
            "title": rep["title"], "link": rep.get("link") or "",
            "source": rep["source_name"], "n_sources": cl["n_sources"],
            "age": _age_txt(rep), "score": score,
            "desk": spec["desk"], "desk_nice": DESK_NICE.get(spec["desk"]),
            "beat": rep["beat"], "label": spec["label"],
            "board_event": bool(rep.get("board_event")),
            "desk_line": board_line(rep["beat"], boards),
            "angle": ANGLES.get(rep["beat"], ""),
            "routes": _route_payloads(cl, boards),
        })
    candidates = [i for i, cl in enumerate(marked)
                  if not cl["rep"].get("board_event")
                  and cl["final"] >= CHANNEL_BAR][:MAX_CHANNEL_POSTS]
    for index in candidates:
        routes = items[index]["routes"]
        if routes:
            # _best_route_beats places the primary desk first. Only that route
            # owns the shared-channel slot; every route still reaches its bot.
            routes[0]["channel_candidate"] = True
    return {"schema": "rissaga.news.v2",
            "generated": generated,
            "lexicon": lexicon_version(), "channel_mode": CHANNEL_MODE,
            "items": items, "channel_candidates": candidates}


def export_latest(marked: list[dict], boards: dict, now: datetime,
                  payload: dict | None = None) -> None:
    """Atomically publish the world readable current-run handoff."""
    payload = payload or latest_payload(marked, boards, now)
    path = _state_path(LATEST_EXPORT)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o644)


def _repair_outbox_tail(path: str) -> None:
    """Remove only an interrupted final JSONL fragment before appending.

    A complete final object without its newline is preserved and terminated.
    Corruption in earlier lines remains visible and is ignored by readers.
    """
    try:
        with open(path, "r+b") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            if end == 0:
                return
            fh.seek(end - 1)
            if fh.read(1) == b"\n":
                return
            cursor = end
            start = 0
            while cursor > 0:
                size = min(4096, cursor)
                cursor -= size
                fh.seek(cursor)
                block = fh.read(size)
                offset = block.rfind(b"\n")
                if offset >= 0:
                    start = cursor + offset + 1
                    break
            fh.seek(start)
            tail = fh.read(end - start)
            try:
                json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                fh.truncate(start)
            else:
                fh.seek(0, os.SEEK_END)
                fh.write(b"\n")
            fh.flush()
            os.fsync(fh.fileno())
    except FileNotFoundError:
        pass


def _outbox_active_records(path: str, now: datetime) -> dict[str, dict]:
    """Highest active dispatch per story, ignoring expired history."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    active: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                expires = record.get("expires_at")
                if expires:
                    try:
                        expiry = datetime.fromisoformat(
                            str(expires).replace("Z", "+00:00"))
                        if expiry.tzinfo is None:
                            expiry = expiry.replace(tzinfo=timezone.utc)
                        if expiry <= now:
                            continue
                    except ValueError:
                        pass
                story_id = record.get("story_id")
                if isinstance(story_id, str):
                    try:
                        score = float(record.get("score") or 0)
                    except (TypeError, ValueError):
                        score = 0.0
                    old = active.get(story_id)
                    if old is None or score >= old["score"]:
                        active[story_id] = {
                            "score": score,
                            "dispatch_id": record.get("dispatch_id")
                                           or story_id,
                        }
    except OSError:
        pass
    return active


def _outbox_active(path: str, now: datetime) -> dict[str, float]:
    """Compatibility score view of active outbox revisions."""
    return {story_id: record["score"]
            for story_id, record in _outbox_active_records(path, now).items()}


def _outbox_story_ids(path: str, now: datetime | None = None) -> set[str]:
    """Compatibility seam returning only unexpired story identifiers."""
    return set(_outbox_active(path, now or datetime.now(timezone.utc)))


def append_outbox(payload: dict, now: datetime) -> int:
    """Durably append each new routed story exactly once.

    The outbox itself is the retry idempotency ledger. This closes the crash
    window where the append succeeded but the seen ledger did not.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    path = _state_path(OUTBOX_EXPORT)
    _repair_outbox_tail(path)
    active = _outbox_active_records(path, now)
    generated = payload["generated"]
    expires_at = (now + timedelta(hours=OUTBOX_TTL_H)).isoformat(
        timespec="seconds")
    records = []
    for item in payload.get("items") or []:
        story_id = item.get("story_id")
        routes = item.get("routes") or []
        if (item.get("board_event") or item.get("score", 0) < MARK_BAR
                or not story_id or not routes):
            continue
        score = float(item["score"])
        prior = active.get(story_id)
        if prior is not None and score < prior["score"] * SEEN_ESCALATE:
            # A retry after append but before seen commit gets the already
            # durable revision key in latest.json, so Hermes cannot replay it.
            item["dispatch_id"] = prior["dispatch_id"]
            continue
        item_dispatch_id = item.get("dispatch_id") or dispatch_id(
            story_id, generated, score, item["n_sources"])
        records.append({
            "schema": "rissaga.news.v2",
            "story_id": story_id,
            "dispatch_id": item_dispatch_id,
            "generated": generated,
            "expires_at": expires_at,
            "title": item["title"],
            "link": item.get("link") or "",
            "source": item["source"],
            "n_sources": item["n_sources"],
            "age": str(item["age"])[:40],
            "score": item["score"],
            "routes": routes,
            "shared_candidate": any(r.get("channel_candidate")
                                    for r in routes),
        })
        active[story_id] = {"score": score,
                            "dispatch_id": item_dispatch_id}
    if records:
        with open(path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    if os.path.exists(path):
        os.chmod(path, 0o644)
    return len(records)


def post_channel(text: str, ref: str) -> bool:
    """Publish the top marked item to the free lab channel. Never raises,
    never blocks the owner DM. Same contract as the desk bots."""
    if not LAB_CHANNEL:
        return False
    body = text + (
        f"\n\n<i>Rissaga is the Liquidity Lab news radar. Open the matching "
        f"desk for its own grounded read: {LAB_LINK}</i>"
    )
    keyboard = [
        [{"text": "\U0001f321 Plumbing desk",
          "url": f"https://t.me/seiche_desk_bot?start={ref}"}],
        [{"text": "\U0001f3e6 Failure radar",
          "url": f"https://t.me/LiquiLens_bot?start={ref}"},
         {"text": "\U0001f300 Market depth",
          "url": f"https://t.me/undertow_LiquiLens_bot?start={ref}"}],
        [{"text": "\U0001f9ed Riptide risk desk",
          "url": f"https://t.me/riptide_anake_bot?start={ref}"},
         {"text": "\U0001f9f1 Palimpsest watch",
          "url": f"https://t.me/palimpsest_watch_bot?start={ref}"}],
        [{"text": "\U0001f3ed Corporate stress",
          "url": f"https://t.me/corporate_stress_bot?start={ref}"},
         {"text": "\U0001f6d2 Real economy",
          "url": f"https://t.me/real_economy_desk_bot?start={ref}"}],
    ]
    try:
        res = send(int(LAB_CHANNEL), body, keyboard)
    except Exception as exc:                       # noqa: BLE001 - see docstring
        print(f"channel post failed: {exc}", file=sys.stderr)
        return False
    ok = isinstance(res, dict) and res.get("ok")
    if not ok:
        print(f"channel post rejected: {res}", file=sys.stderr)
    return bool(ok)


# ------------------------------------------------------------------ runs ---
def gather(now: datetime, mutate: bool = True):
    now_ts = now.timestamp()
    items, health = fetch_feeds(now_ts)
    boards = read_boards()
    items.extend(board_events(boards, now_ts, persist=mutate))
    marked = rank(items, boards, now_ts, persist_seen=mutate)
    return marked, boards, health


def run(dry: bool = False) -> int:
    now = datetime.now(timezone.utc)
    # Production and print both calculate without committing delivery state.
    # A real run commits only after the durable multi-desk outbox append.
    marked, boards, health = gather(now, mutate=False)
    text = compose(marked, boards, health, now)
    if dry:
        print(text)
        print("\n----- feed health -----", file=sys.stderr)
        for k in sorted(health):
            print(f"{k:22s} {health[k]}", file=sys.stderr)
        return 0
    payload = latest_payload(marked, boards, now)
    try:
        append_outbox(payload, now)
    except OSError as exc:
        print(f"outbox append failed, seen not committed: {exc}", file=sys.stderr)
        return 1
    # Both delivery handoffs must exist before suppression is committed. If
    # latest.json fails after the JSONL append, the next run reuses the durable
    # dispatch_id and retries this export without replaying bot recipients.
    try:
        export_latest(marked, boards, now, payload=payload)
    except OSError as exc:
        print(f"latest export failed, seen not committed: {exc}",
              file=sys.stderr)
        return 1
    commit_seen(marked, now.timestamp())
    commit_boards(boards)
    res = send(int(OWNER_CHAT), text)
    delivered = isinstance(res, dict) and res.get("ok")
    if not delivered:
        print(f"owner DM failed: {res}", file=sys.stderr)
    channel_posted = 0
    if CHANNEL_MODE == "direct":
        posts = [c for c in marked if not c["rep"].get("board_event")
                 and c["final"] >= CHANNEL_BAR][:MAX_CHANNEL_POSTS]
        for cl in posts:
            if post_channel(compose_channel(cl, boards), "lab_rissaga"):
                channel_posted += 1
            time.sleep(0.5)
    hist = {"ts": now.isoformat(timespec="seconds"),
            "lexicon": lexicon_version(),
            "marked": [{"title": c["rep"]["title"], "beat": c["rep"]["beat"],
                        "score": round(c["final"], 2),
                        "sources": c["n_sources"]} for c in marked],
            "feeds_ok": sum(1 for v in health.values() if v.startswith("ok")),
            "feeds_total": len(health),
            "channel_mode": CHANNEL_MODE,
            "channel_posted": channel_posted}
    with open(_state_path("history.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(hist, sort_keys=True) + "\n")
    return 0 if delivered else 1


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "--run"
    if mode == "--print":
        return run(dry=True)
    if mode == "--run":
        if not TOKEN:
            print("SEICHE_BOT_TOKEN missing", file=sys.stderr)
            return 2
        return run(dry=False)
    print(f"unknown mode {mode}, use --run or --print", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
