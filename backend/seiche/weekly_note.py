"""The Seiche weekly note: observed changes in US dollar funding, facts only.

Owner decision, 27 Nov 2026: a facts-only "Seiche weekly note", one per week,
due each Monday at 12:00 UTC and covering the previous Monday to Friday by
observation date. No regime, score or forecast, and no person reviews a note:
the gates below decide, and a note that fails any of them is not published.

Inputs, all already in the build:
  - the sealed snapshot, frontend/public/data/overview.json. Its public
    money-market desk carries 180 dated rows per chart with no forward fill,
    which hold both the week covered and the week before it. (The published
    data/book_history.json is a source-rights restriction notice, so it
    carries no values to compare against.)
  - the snapshot's rights projection and provenance, which decide which
    series may show a value;
  - the money-market catalog, for the map a reader is sent to;
  - the published archive, https://seiche.info/weekly/notes.json, which keeps
    every note. A build re-renders all of them and adds at most one. A read
    that fails is retried and then answered by the same deployment at
    seiche.pages.dev (see fetch_archive); only an archive that no hostname
    serves, or one that is broken, stops the build.

Gates (any failure publishes nothing, substitutes nothing and logs a
SEICHE_WEEKLY_NOTE line with the reasons):
  rights          every value comes from a series the current rights
                  projection publishes; a restricted series is named, never
                  given a value
  facts_only      no regime, score, forecast or advice vocabulary
  numbers_dated   every sentence that carries a number carries a date
  no_daily        nothing promises a daily letter, edition or 11:30 UTC
  changed_facts   at least one series changed during the week
  phrase_overlap  at most 20% of the note's 8-word phrases appear in any one
                  of the last four notes

The step is on from 0.16.18: ENABLED = True (owner decision of 27 Nov 2026,
after a test run over 14 published snapshots). It is the existing "Render
dispatch pages" step of the full publisher: dispatch_pages calls publish()
after it writes the sitemap. No schedule or workflow changes. ENABLED = False
turns it off again, and publish(enabled=False) skips it for one call.

The first note is the issue of Monday 30 November 2026 (FIRST_ISSUE, owner
decision of 27 Nov 2026), due from 12:00 UTC that day. While the due issue is
earlier, a build composes nothing, logs before_first_issue and still
re-renders any archive it reads.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import http.client
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from seiche import family_shell

ENABLED = True

SITE = "https://seiche.info"
ARCHIVE_PATH = "/weekly/"
ARCHIVE_FILE = "notes.json"
INDEX_FILE = "index.json"
SCHEMA = "seiche.weekly-notes.v1"
ISSUE_WEEKDAY = 0  # Monday
ISSUE_HOUR_UTC = 12
# Owner decision of 27 Nov 2026: the first note is the issue of Monday 30 Nov 2026.
FIRST_ISSUE = "2026-11-30"
CADENCE = "A new note is published each Monday after 12:00 UTC."
SHINGLE_WORDS = 8
OVERLAP_LIMIT = 0.20
OVERLAP_NOTES = 4
FORM_MEMORY = 2
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
USER_AGENT = "seiche-weekly-note/1.0 (+https://seiche.info/weekly/)"
# The same Cloudflare Pages deployment under its own hostname: it serves the
# bytes seiche.info serves, outside the seiche.info zone's security rules, so a
# challenge or a custom-domain fault there does not hide the archive.
ARCHIVE_MIRROR = "https://seiche.pages.dev"
READ_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 20

# key, money-market chart, column, label, unit, decimals, rights source,
# headline key in the rights projection (or None), provenance mnemonic
SERIES = (
    ("sofr", "policy", "sofr_pct", "SOFR", "pct", 2, "nyfed_original", "sofr_pct", "SOFR"),
    ("effr", "policy", "effr_pct", "EFFR", "pct", 2, "nyfed_original", "effr_pct", "EFFR"),
    ("iorb", "policy", "iorb_pct", "Interest on reserve balances", "pct", 2, "fed_prates", "iorb_pct", "IORB"),
    ("bill_4w", "bills", "bill_4w_pct", "Four-week Treasury bill", "pct", 2, "fed_h15", None, "TB4W"),
    ("bill_3m", "bills", "bill_3m_pct", "Three-month Treasury bill", "pct", 2, "fed_h15", None, "TB3M"),
    ("reserves", "liquidity", "reserves_b", "Reserve balances", "usd_b", 1, "fed_h41", "reserves_b", "WRESBAL"),
    ("tga", "liquidity", "tga_b", "Treasury General Account", "usd_b", 1, "tga", "tga_b", None),
    ("on_rrp", "liquidity", "on_rrp_b", "ON RRP take-up", "usd_b", 2, "nyfed_original", "rrp_b", "RRPONTSYD"),
    ("srf", "liquidity", "srf_b", "Standing Repo Facility accepted", "usd_b", 2, "nyfed_srf", "srf_accepted_b", None),
    ("discount_window", "liquidity", "discount_window_b", "Discount window lending", "usd_b", 1, "fed_h41", "dw_b", "DISCOUNT_WINDOW"),
)

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DIGIT = re.compile(r"\d")
_WORD = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
NOT_FACTS = re.compile(
    r"\b(?:calm|erosion|strain|stress(?:ed)?|crisis|regime|scores?|forecasts?|predict(?:s|ed|ion)?|"
    r"expect(?:s|ed)?|likely|unlikely|will|should|could|might|recommend(?:s|ed)?|signals?|"
    r"warnings?|outlook|probabilit(?:y|ies)|odds|advice|buy|sell)\b",
    re.IGNORECASE,
)
DAILY_PROMISE = re.compile(
    r"daily letter|daily edition|11:30 UTC|every day|daily (?:funding )?(?:analysis|articles?)",
    re.IGNORECASE,
)
_ISSUE_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _monday_issue(value: object) -> bool:
    """An issue is the ISO date of a Monday: it names the note's folder under weekly/."""
    if not isinstance(value, str) or not _ISSUE_DATE.fullmatch(value):
        return False
    try:
        day = dt.date.fromisoformat(value)
    except ValueError:
        return False
    return day.isoformat() == value and day.weekday() == ISSUE_WEEKDAY


def _iso_instant(value: object) -> bool:
    """A published_at clock: an ISO date and time whose first ten characters are the date."""
    if not isinstance(value, str) or not _ISSUE_DATE.fullmatch(value[:10]):
        return False
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _terms_url(value: object) -> bool:
    """A source's terms link: absent, or an http or https URL (never javascript: or a relative path)."""
    return value is None or (isinstance(value, str) and value.startswith(("https://", "http://")))


# --------------------------------------------------------------------- clock

def issue_for(now: dt.datetime) -> dt.date:
    """The Monday whose note is due at `now`: due from Monday 12:00 UTC."""
    now = now.astimezone(dt.timezone.utc)
    monday = now.date() - dt.timedelta(days=(now.weekday() - ISSUE_WEEKDAY) % 7)
    if now < dt.datetime.combine(monday, dt.time(ISSUE_HOUR_UTC), dt.timezone.utc):
        monday -= dt.timedelta(days=7)
    return monday


def window(issue: dt.date) -> tuple[dt.date, dt.date]:
    """The Monday to Friday a note covers, by observation date."""
    return issue - dt.timedelta(days=7), issue - dt.timedelta(days=3)


# -------------------------------------------------------------------- rights

def series_rights(snapshot: dict) -> dict[str, str | None]:
    """For each series: None when its value may be shown, else the reason it may not."""
    projection = snapshot.get("rights_projection") or {}
    engine = (projection.get("engines") or {}).get("money_market") or {}
    desk = (snapshot.get("engines") or {}).get("money_market") or {}
    desk_public = (desk.get("publication_projection") or {}).get("status") == "public"
    sources = set(engine.get("sources") or [])
    restricted = set(engine.get("restricted_inputs") or [])
    headline = projection.get("headline") or {}
    known = projection.get("sources") or {}
    provenance = {row.get("mnemonic"): row for row in snapshot.get("provenance") or []}
    out: dict[str, str | None] = {}
    for key, _chart, _column, _label, _unit, _dp, source, head, mnemonic in SERIES:
        if engine.get("status") != "public" or not desk_public:
            out[key] = "money-market desk not public in this snapshot"
        elif source not in sources or source in restricted or source not in known:
            out[key] = f"source {source} not published by the rights projection"
        elif head is not None and headline.get(head) != "public":
            out[key] = f"headline {head} is {headline.get(head, 'absent')}"
        elif head is None and (provenance.get(mnemonic) or {}).get("redistribution_status") != "allowed":
            out[key] = f"{mnemonic} redistribution not allowed"
        else:
            out[key] = None
    return out


# --------------------------------------------------------------------- facts

def _rows(snapshot: dict, chart: str, column: str) -> list[tuple[str, float]]:
    block = (((snapshot.get("engines") or {}).get("money_market") or {}).get("charts") or {}).get(chart) or {}
    columns = block.get("columns") or []
    if column not in columns or "date" not in columns:
        return []
    at, date_at = columns.index(column), columns.index("date")
    rows = []
    for row in block.get("rows") or []:
        if isinstance(row, list) and len(row) > max(at, date_at):
            value, day = row[at], row[date_at]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and isinstance(day, str) and _ISO_DATE.fullmatch(day):
                rows.append((day, float(value)))
    return sorted(rows)


def _fmt(value: float, unit: str, dp: int) -> str:
    return f"{value:.{dp}f}%" if unit == "pct" else f"${value:,.{dp}f} billion"


def _delta(v0: float, v1: float, unit: str, dp: int) -> tuple[str, str]:
    if unit == "pct":
        bp = round((v1 - v0) * 100)
        return ("up" if v1 > v0 else "down"), f"{abs(bp)} basis point{'' if abs(bp) == 1 else 's'}"
    return ("up" if v1 > v0 else "down"), f"${abs(v1 - v0):,.{dp}f} billion"


FORMS = (
    "{label} moved from {v0} on {d0} to {v1} on {d1}, {direction} {delta}.",
    "{label} was {v1} on {d1}, {direction} {delta} from {v0} on {d0}.",
    "On {d1} {label} stood at {v1}, against {v0} on {d0}: {direction} {delta}.",
)


def _form(issue: str, key: str, previous: list[dict]) -> int:
    recent = {note.get("forms", {}).get(key) for note in previous[-FORM_MEMORY:]}
    order = sorted(range(len(FORMS)), key=lambda i: hashlib.sha256(f"{issue}:{key}:{i}".encode()).hexdigest())
    return next((i for i in order if i not in recent), order[0])


def compose(snapshot: dict, issue: dt.date, previous: list[dict]) -> dict:
    """Build one note from the sealed snapshot. Gates run separately."""
    start, end = window(issue)
    rights = series_rights(snapshot)
    issue_s = issue.isoformat()
    facts, forms = [], {}
    for key, chart, column, label, unit, dp, source, _head, _mnemonic in SERIES:
        if rights[key] is not None:
            facts.append({"series": key, "label": label, "kind": "restricted", "reason": rights[key]})
            continue
        rows = _rows(snapshot, chart, column)
        inside = [row for row in rows if start.isoformat() <= row[0] <= end.isoformat()]
        before = [row for row in rows if row[0] < start.isoformat()]
        last = (inside or before or [None])[-1]
        if not inside:
            facts.append({"series": key, "label": label, "kind": "no_new", "source": source,
                          "last": {"date": last[0]} if last else None})
            continue
        if not before:
            facts.append({"series": key, "label": label, "kind": "no_baseline", "source": source})
            continue
        (d0, v0), (d1, v1) = before[-1], inside[-1]
        fact = {"series": key, "label": label, "unit": unit, "dp": dp, "source": source,
                "start": {"date": d0, "value": v0}, "end": {"date": d1, "value": v1}}
        if _fmt(v0, unit, dp) == _fmt(v1, unit, dp):
            fact["kind"] = "unchanged"
        else:
            fact["kind"] = "changed"
            forms[key] = _form(issue_s, key, previous)
        facts.append(fact)
    return {
        "issue": issue_s,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "published_at": snapshot.get("generated_at"),
        "facts": facts,
        "forms": forms,
        "sources": _sources(snapshot, facts),
    }


def _sources(snapshot: dict, facts: list[dict]) -> list[dict]:
    known = (snapshot.get("rights_projection") or {}).get("sources") or {}
    used = sorted({fact["source"] for fact in facts if fact["kind"] in {"changed", "unchanged"}})
    return [{"key": key, "publisher": known[key].get("publisher"), "attribution": known[key].get("attribution"),
             "terms_url": known[key].get("terms_url") if _terms_url(known[key].get("terms_url")) else None}
            for key in used if key in known]


def headline(note: dict) -> str | None:
    changed = [fact for fact in note["facts"] if fact["kind"] == "changed"]
    if not changed:
        return None
    rates = [fact for fact in changed if fact["unit"] == "pct"]
    pool = rates if any(abs(f["end"]["value"] - f["start"]["value"]) >= 0.005 for f in rates) else changed

    def size(fact):
        v0, v1 = fact["start"]["value"], fact["end"]["value"]
        return abs(v1 - v0) if fact["unit"] == "pct" else abs(v1 - v0) / max(abs(v0), 1.0)

    top = max(pool, key=lambda fact: (size(fact), fact["series"]))
    direction, delta = _delta(top["start"]["value"], top["end"]["value"], top["unit"], top["dp"])
    value = _fmt(top["end"]["value"], top["unit"], top["dp"])
    return f"{top['label']} {direction} {delta} to {value} in the week to {note['window']['end']}"


def sentences(note: dict) -> list[str]:
    """The note's body, in order: each change, then the grouped lines."""
    out = []
    for fact in note["facts"]:
        if fact["kind"] != "changed":
            continue
        unit, dp = fact["unit"], fact["dp"]
        v0, v1 = fact["start"]["value"], fact["end"]["value"]
        direction, delta = _delta(v0, v1, unit, dp)
        out.append(FORMS[note["forms"][fact["series"]]].format(
            label=fact["label"], v0=_fmt(v0, unit, dp), v1=_fmt(v1, unit, dp),
            d0=fact["start"]["date"], d1=fact["end"]["date"], direction=direction, delta=delta))
    unchanged = [f"{fact['label']} ({fact['start']['date']} to {fact['end']['date']})"
                 for fact in note["facts"] if fact["kind"] == "unchanged"]
    if unchanged:
        out.append("No change: " + "; ".join(unchanged) + ".")
    quiet = [f"{fact['label']} (last observed {fact['last']['date']})" if fact.get("last") else fact["label"]
             for fact in note["facts"] if fact["kind"] == "no_new"]
    if quiet:
        out.append(f"No observation dated {note['window']['start']} to {note['window']['end']}: " + "; ".join(quiet) + ".")
    thin = [fact["label"] for fact in note["facts"] if fact["kind"] == "no_baseline"]
    if thin:
        out.append(f"No earlier observation to compare with before {note['window']['start']}: " + "; ".join(thin) + ".")
    restricted = [fact["label"] for fact in note["facts"] if fact["kind"] == "restricted"]
    if restricted:
        out.append("Restricted, no value published: " + "; ".join(restricted) + ".")
    return out


def text(note: dict) -> str:
    """Everything a reader sees that changes from week to week."""
    return "\n".join([headline(note) or "", *sentences(note)])


# --------------------------------------------------------------------- gates

def _shingles(value: str) -> set[tuple[str, ...]]:
    words = _WORD.findall(value.lower())
    return {tuple(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def overlap(candidate: str, earlier: str) -> float:
    mine = _shingles(candidate)
    return len(mine & _shingles(earlier)) / len(mine) if mine else 1.0


def gates(note: dict, snapshot: dict, previous: list[dict]) -> list[str]:
    """Every gate a note must pass. An empty list means it may be published."""
    failures = []
    rights = series_rights(snapshot)
    for fact in note["facts"]:
        carries_value = "start" in fact or "end" in fact
        reason = rights.get(fact["series"], "not a tracked series")
        if carries_value and reason is not None:
            failures.append(f"rights: {fact['series']} carries a value but is {reason}")
        if fact["kind"] == "restricted" and carries_value:
            failures.append(f"rights: restricted {fact['series']} carries a value")
    body = text(note)
    if not any(fact["kind"] == "changed" for fact in note["facts"]):
        failures.append("changed_facts: no series changed during the week")
    if NOT_FACTS.search(body):
        failures.append(f"facts_only: {NOT_FACTS.search(body).group(0)!r}")
    for line in body.split("\n"):
        for sentence in re.split(r"(?<=\.)\s+", line):
            if _DIGIT.search(_ISO_DATE.sub("", sentence)) and not _ISO_DATE.search(sentence):
                failures.append(f"numbers_dated: {sentence!r}")
    if DAILY_PROMISE.search(body):
        failures.append(f"no_daily: {DAILY_PROMISE.search(body).group(0)!r}")
    for earlier in [n for n in previous if n.get("status") != "withdrawn"][-OVERLAP_NOTES:]:
        share = overlap(body, text(earlier))
        if share > OVERLAP_LIMIT:
            failures.append(f"phrase_overlap: {share:.0%} of 8-word phrases repeat the note of {earlier['issue']}")
    return failures


# ------------------------------------------------------------------- archive

def _digest(note: dict) -> str:
    body = {key: value for key, value in note.items() if key != "sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify(document: dict) -> list[dict]:
    """Return the archive's notes, or fail loud on any break in the chain.

    The archive arrives over the network and its hash chain describes only itself, so
    each note's issue, clock and terms links are checked as untrusted input too."""
    if document.get("schema") != SCHEMA or not isinstance(document.get("notes"), list):
        raise SystemExit("weekly note archive: unexpected schema")
    previous = None
    for note in document["notes"]:
        if not isinstance(note, dict) or not _monday_issue(note.get("issue")):
            raise SystemExit("weekly note archive: an issue is not the ISO date of a Monday")
        if note.get("prev_sha256") != previous:
            raise SystemExit(f"weekly note archive: chain broken at {note.get('issue')}")
        if note.get("status") != "withdrawn":
            if _digest(note) != note.get("sha256"):
                raise SystemExit(f"weekly note archive: digest mismatch at {note.get('issue')}")
            if not _iso_instant(note.get("published_at")):
                raise SystemExit(f"weekly note archive: published_at is not an ISO clock at {note['issue']}")
            sources = note.get("sources")
            if not isinstance(sources, list) or not all(
                    isinstance(source, dict) and _terms_url(source.get("terms_url")) for source in sources):
                raise SystemExit(f"weekly note archive: a terms link is not http or https at {note['issue']}")
        previous = note.get("sha256")
    return document["notes"]


def _get(url: str) -> tuple[int, bytes]:
    """(status, body). Status 0 means no HTTP answer: DNS, TLS, a timeout or a reset."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (OSError, http.client.HTTPException):
        return 0, b""


def fetch_archive(get: Callable[[str], tuple[int, bytes]] = _get, *, mirror: str = ARCHIVE_MIRROR,
                  attempts: int = READ_ATTEMPTS, pause: float = RETRY_PAUSE_SECONDS,
                  sleep: Callable[[float], None] = time.sleep,
                  log: Callable[[dict], None] | None = None) -> list[dict]:
    """The published archive. Absent only when seiche.info answers 404 for both the file and its page.

    The site is published with delete, so a build that lost the archive would
    remove every published note: a build never proceeds without it. A failed
    read of seiche.info (no answer, a challenge or a server error) is retried,
    then answered by the same deployment at seiche.pages.dev, where a 404 never
    counts as absence. The build stops only when neither hostname serves the
    archive, or when the archive it serves is broken.
    """
    failures = []
    for attempt in range(attempts):
        status, body = get(SITE + ARCHIVE_PATH + ARCHIVE_FILE)
        if status == 404:
            page_status, _ = get(SITE + ARCHIVE_PATH)
            if page_status == 404:
                return []
            raise SystemExit("weekly note archive: notes.json is missing while the archive page exists")
        if status == 200 and len(body) <= MAX_ARCHIVE_BYTES:
            return verify(json.loads(body))
        failures.append(f"seiche.info HTTP {status}")
        if attempt + 1 < attempts:
            sleep(pause)
    for attempt in range(attempts):
        status, body = get(mirror + ARCHIVE_PATH + ARCHIVE_FILE)
        if status == 200 and len(body) <= MAX_ARCHIVE_BYTES:
            notes = verify(json.loads(body))
            (log or _log)({"status": "archive_fallback", "source": mirror, "site_failures": failures})
            return notes
        failures.append(f"{mirror.removeprefix('https://')} HTTP {status}")
        if attempt + 1 < attempts:
            sleep(pause)
    raise SystemExit("weekly note archive: unreadable (" + "; ".join(failures) + ")")


def _withdraw(note: dict, when: str | None) -> dict:
    return {"issue": note["issue"], "status": "withdrawn", "withdrawn_at": when,
            "prev_sha256": note.get("prev_sha256"), "sha256": note.get("sha256")}


# ------------------------------------------------------------------- render

_PAGE_CSS = (
    "\n" + family_shell.ROOT_TOKENS + """
    body { margin:0; background:var(--bg); color:var(--text); font-family:var(--mono); line-height:1.7; }
    main.weekly { max-width:760px; margin:0 auto; padding:28px 20px 72px; }
    main.weekly h1 { font-size:28px; line-height:1.3; margin:18px 0 12px; }
    main.weekly h2 { font-size:18px; margin:34px 0 10px; }
    main.weekly p, main.weekly li { color:var(--text); }
    main.weekly .faint { color:var(--dim); font-size:14px; }
    main.weekly a { color:var(--accent); }
"""
)


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _shell(title: str, canonical: str, description: str, jsonld: dict, body: str) -> str:
    data = json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
{family_shell.HTML_OPEN}
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} | Seiche</title>
<meta name="description" content="{_e(description)}">
<link rel="canonical" href="{_e(canonical)}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Seiche">
<meta property="og:title" content="{_e(title)}">
<meta property="og:url" content="{_e(canonical)}">
<script type="application/ld+json">{data}</script>
{family_shell.STYLE}<style>{_PAGE_CSS}</style>
</head>
<body>{family_shell.HEADER}
<main class="weekly">
{body}
</main>
{family_shell.CONTEXT}</body>
</html>
"""


def note_url(note: dict) -> str:
    return f"{SITE}{ARCHIVE_PATH}{note['issue']}/"


def map_url(public_dir: Path) -> str | None:
    """The money-market map a note sends readers to, as the catalog names it."""
    catalog = Path(public_dir) / "money-markets" / "catalog.json"
    try:
        url = json.loads(catalog.read_text(encoding="utf-8")).get("canonical_url")
    except (OSError, ValueError, AttributeError):
        return None
    return url.removeprefix(SITE) if isinstance(url, str) and url.startswith(SITE + "/") else None


def render_note(note: dict, money_map: str | None = None) -> str:
    title = f"Seiche weekly note, {note['issue']}"
    lead = headline(note)
    items = "".join(f"<li>{_e(line)}</li>" for line in sentences(note))
    sources = "".join(
        f'<li>{_e(source["attribution"])} <a href="{_e(source["terms_url"])}">Terms</a></li>' if source.get("terms_url")
        else f'<li>{_e(source["attribution"])}</li>' for source in note["sources"])
    body = (
        family_shell.heading(_e(title))
        + f"<p><strong>{_e(lead)}.</strong></p>"
        + f'<p class="faint">Observations dated {_e(note["window"]["start"])} to {_e(note["window"]["end"])}, '
        + f'from the sealed Seiche snapshot of {_e(note["published_at"])}. Facts only: no regime, score or forecast.</p>'
        + f"<h2>What the observations show</h2><ul>{items}</ul>"
        + f"<h2>Sources</h2><ul>{sources}</ul>"
        + f'<p class="faint"><a href="{ARCHIVE_PATH}">All weekly notes</a>'
        + (f' · <a href="{_e(money_map)}">Money-market evidence map</a>' if money_map else "") + "</p>"
    )
    jsonld = {
        "@context": "https://schema.org", "@type": "Article", "headline": lead, "name": title,
        "url": note_url(note), "mainEntityOfPage": note_url(note),
        "datePublished": note["published_at"], "dateModified": note["published_at"],
        "isAccessibleForFree": True, "inLanguage": "en",
        "author": {"@type": "Organization", "name": "Seiche", "url": SITE + "/"},
        "publisher": {"@type": "Organization", "name": "LIQUILENS PRIVATE LIMITED", "url": "https://liquilens.in/about/"},
        "isPartOf": {"@type": "CollectionPage", "url": SITE + ARCHIVE_PATH},
    }
    return _shell(title, note_url(note), lead or title, jsonld, body)


def render_archive(notes: list[dict], money_map: str | None = None) -> str:
    shown = sorted((note for note in notes if note.get("status") != "withdrawn"), key=lambda n: n["issue"], reverse=True)
    items = "".join(
        f'<li><a href="{ARCHIVE_PATH}{_e(note["issue"])}/">{_e(note["issue"])}: {_e(headline(note))}</a></li>' for note in shown)
    body = (
        family_shell.heading("Seiche weekly note")
        + "<p>Observed changes in US dollar funding rates and balances, each with its observation date. "
        + "Facts only: no regime, score or forecast. " + _e(CADENCE) + "</p>"
        + f"<ul>{items}</ul>"
        + '<p class="faint">' + (f'<a href="{_e(money_map)}">Money-market evidence map</a> · ' if money_map else "")
        + '<a href="/articles/">Articles</a></p>'
    )
    dates = sorted(note["published_at"] for note in shown)
    jsonld = {"@context": "https://schema.org", "@type": "CollectionPage", "name": "Seiche weekly note",
              "url": SITE + ARCHIVE_PATH, "inLanguage": "en"}
    if dates:
        jsonld.update({"datePublished": dates[0], "dateModified": dates[-1]})
    return _shell("Seiche weekly note", SITE + ARCHIVE_PATH, "Observed changes in US dollar funding, facts only.", jsonld, body)


def index_rows(notes: list[dict]) -> list[dict]:
    """The rows the homepage reads: the same shape as the article index."""
    return [{"slug": note["issue"], "date": note["issue"], "headline": headline(note)}
            for note in sorted(notes, key=lambda n: n["issue"], reverse=True) if note.get("status") != "withdrawn"]


def _sitemap_rows(notes: list[dict]) -> str:
    shown = [note for note in notes if note.get("status") != "withdrawn"]
    if not shown:
        return ""
    newest = max(note["published_at"][:10] for note in shown)
    rows = [f"  <url>\n    <loc>{SITE}{ARCHIVE_PATH}</loc>\n    <lastmod>{newest}</lastmod>\n"
            "    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>"]
    rows += [f"  <url>\n    <loc>{note_url(note)}</loc>\n    <lastmod>{note['published_at'][:10]}</lastmod>\n"
             "    <changefreq>yearly</changefreq>\n    <priority>0.7</priority>\n  </url>"
             for note in sorted(shown, key=lambda n: n["issue"], reverse=True)]
    return "\n".join(rows) + "\n"


# ------------------------------------------------------------------ publish

def _log(record: dict) -> None:
    print("SEICHE_WEEKLY_NOTE " + json.dumps(record, sort_keys=True), flush=True)


def publish(public_dir: Path, *, enabled: bool | None = None, fetch=fetch_archive, log=_log,
            first_issue: str = FIRST_ISSUE) -> list[str]:
    """Re-render the archive, add this week's note if it is due, is not earlier
    than first_issue and passes every gate."""
    if not (ENABLED if enabled is None else enabled):
        return []
    first = dt.date.fromisoformat(first_issue)
    public_dir = Path(public_dir)
    snapshot = json.loads((public_dir / "data" / "overview.json").read_text(encoding="utf-8"))
    generated = dt.datetime.fromisoformat(str(snapshot["generated_at"]).replace("Z", "+00:00"))
    notes = fetch()
    rights = series_rights(snapshot)
    kept = []
    for note in notes:
        if note.get("status") != "withdrawn" and any(
                rights.get(fact["series"], "not a tracked series") is not None for fact in note["facts"] if "end" in fact):
            log({"status": "withdrawn", "issue": note["issue"], "reason": "a source of this note is no longer published"})
            note = _withdraw(note, snapshot.get("generated_at"))
        kept.append(note)
    issue = issue_for(generated).isoformat()
    if dt.date.fromisoformat(issue) < first:
        # Before the first issue nothing new is composed; the archive read above is still re-rendered.
        log({"status": "before_first_issue", "issue": issue, "first_issue": first.isoformat()})
    elif any(note["issue"] == issue for note in kept):
        log({"status": "already_published", "issue": issue})
    else:
        candidate = compose(snapshot, dt.date.fromisoformat(issue), kept)
        failures = gates(candidate, snapshot, kept)
        if failures:
            log({"status": "refused", "issue": issue, "gates": failures})
        else:
            candidate["headline"] = headline(candidate)
            candidate["prev_sha256"] = kept[-1]["sha256"] if kept else None
            candidate["sha256"] = _digest(candidate)
            kept.append(candidate)
            log({"status": "published", "issue": issue, "sha256": candidate["sha256"]})
    if not kept:
        return []
    if not all(_monday_issue(note.get("issue")) for note in kept):
        raise SystemExit("weekly note: an issue is not the ISO date of a Monday; nothing written")
    money_map = map_url(public_dir)
    out = public_dir / ARCHIVE_PATH.strip("/")
    out.mkdir(parents=True, exist_ok=True)
    root = out.resolve()
    written = []
    for name, content in (
        (ARCHIVE_FILE, json.dumps({"schema": SCHEMA, "notes": kept}, indent=1, ensure_ascii=False) + "\n"),
        (INDEX_FILE, json.dumps(index_rows(kept), indent=1, ensure_ascii=False) + "\n"),
        ("index.html", render_archive(kept, money_map)),
    ):
        (out / name).write_text(content, encoding="utf-8")
        written.append(str(out / name))
    for note in kept:
        if note.get("status") != "withdrawn":
            page = out / note["issue"] / "index.html"
            if page.resolve().parent.parent != root:
                raise SystemExit("weekly note: a note page resolves outside weekly/; stopped before writing it")
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(render_note(note, money_map), encoding="utf-8")
            written.append(str(page))
    sitemap = public_dir / "sitemap.xml"
    if sitemap.is_file():
        document = sitemap.read_text(encoding="utf-8")
        if "</urlset>" in document and SITE + ARCHIVE_PATH + "<" not in document:
            sitemap.write_text(document.replace("</urlset>", _sitemap_rows(kept) + "</urlset>", 1), encoding="utf-8")
    return written
