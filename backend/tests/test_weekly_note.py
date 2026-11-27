"""The Seiche weekly note (owner decision, 27 Nov 2026): facts only, gated,
and on from 0.16.18 (weekly_note.ENABLED); False still turns it off."""

import datetime as dt
import json
import re

import pytest

from seiche import family_shell, weekly_note
from seiche.weekly_note import compose, gates, headline, sentences, text

ISSUE = dt.date(2026, 11, 30)  # a Monday; the note covers 23 to 27 November
GENERATED = "2026-11-30T14:23:00+00:00"
SOURCES = ("fed_h15", "fed_h41", "fed_prates", "nyfed_original", "nyfed_srf", "tga")


def _days(first, last):
    day = dt.date.fromisoformat(first)
    while day <= dt.date.fromisoformat(last):
        if day.weekday() < 5:
            yield day.isoformat()
        day += dt.timedelta(days=1)


def _snapshot(*, generated=GENERATED, sofr_end=3.94, withheld=(), desk="public"):
    policy, bills, liquidity = [], [], []
    for day in _days("2026-11-16", "2026-11-27"):
        late = day >= "2026-11-23"
        policy.append([day, sofr_end if late else 3.90, 3.88, 3.90])
        bills.append([day, 4.04 if late else 3.95, 4.18])
        wednesday = dt.date.fromisoformat(day).weekday() == 2
        liquidity.append([day, (2979.7 if late else 2960.6) if wednesday else None,
                          891.2 if late else 933.1, 0.4 if late else 0.33, 0.0,
                          (9.1 if late else 9.3) if wednesday else None])
    headline_rights = {key: "public" for key in ("sofr_pct", "effr_pct", "iorb_pct", "reserves_b", "tga_b",
                                                  "rrp_b", "srf_accepted_b", "dw_b")}
    headline_rights.update({key: "withheld" for key in withheld})
    return {
        "generated_at": generated,
        "engines": {"money_market": {
            "publication_projection": {"status": desk},
            "charts": {
                "policy": {"columns": ["date", "sofr_pct", "effr_pct", "iorb_pct"], "rows": policy},
                "bills": {"columns": ["date", "bill_4w_pct", "bill_3m_pct"], "rows": bills},
                "liquidity": {"columns": ["date", "reserves_b", "tga_b", "on_rrp_b", "srf_b", "discount_window_b"],
                              "rows": liquidity},
            },
        }},
        "rights_projection": {
            "engines": {"money_market": {"status": desk, "sources": list(SOURCES), "restricted_inputs": []}},
            "headline": headline_rights,
            "sources": {key: {"publisher": f"Publisher {key}", "attribution": f"Source: {key}.",
                              "terms_url": f"https://example.org/{key}/terms"} for key in SOURCES},
        },
        "provenance": [{"mnemonic": "TB4W", "redistribution_status": "allowed"},
                       {"mnemonic": "TB3M", "redistribution_status": "allowed"}],
    }


def _published(note):
    note = dict(note, headline=headline(note), prev_sha256=None)
    note["sha256"] = weekly_note._digest(note)
    return note


# ---------------------------------------------------------------- the clock

def test_the_step_is_on_from_0_16_18_and_false_still_turns_it_off(tmp_path, monkeypatch):
    """Owner decision of 27 Nov 2026, after a test run over 14 published
    snapshots: 0.16.18 ships the note on. enabled=False for one call, or
    ENABLED = False, still means no fetch and no files."""
    assert weekly_note.ENABLED is True
    calls = []

    def fetch():
        calls.append("fetch")
        return []

    # Shipped on: the module flag alone runs the step.
    logs = []
    public = _public(tmp_path / "on", _snapshot())
    assert weekly_note.publish(public, fetch=fetch, log=logs.append)
    assert calls == ["fetch"] and [entry["status"] for entry in logs] == ["published"]
    # Off for one call, then off by the flag: nothing is fetched or written.
    off = tmp_path / "off"
    assert weekly_note.publish(off, enabled=False, fetch=fetch) == []
    monkeypatch.setattr(weekly_note, "ENABLED", False)
    assert weekly_note.publish(off, fetch=fetch) == []
    assert calls == ["fetch"] and not off.exists()


def test_a_note_is_due_from_monday_noon_utc_and_covers_monday_to_friday():
    utc = dt.timezone.utc
    assert weekly_note.issue_for(dt.datetime(2026, 11, 30, 11, 59, tzinfo=utc)) == dt.date(2026, 11, 23)
    assert weekly_note.issue_for(dt.datetime(2026, 11, 30, 12, 0, tzinfo=utc)) == ISSUE
    assert weekly_note.issue_for(dt.datetime(2026, 12, 6, 23, 0, tzinfo=utc)) == ISSUE
    assert weekly_note.window(ISSUE) == (dt.date(2026, 11, 23), dt.date(2026, 11, 27))


# ---------------------------------------------------------------- the facts

def test_the_note_states_changed_facts_each_with_its_dates():
    snapshot = _snapshot()
    note = compose(snapshot, ISSUE, [])
    assert gates(note, snapshot, []) == []
    assert headline(note) == "Four-week Treasury bill up 9 basis points to 4.04% in the week to 2026-11-27"
    body = sentences(note)
    changed = [line for line in body if "basis point" in line or "billion" in line]
    assert any("SOFR" in line and "3.90% on 2026-11-20" in line and "3.94% on 2026-11-27" in line for line in changed)
    assert any("Reserve balances" in line and "2026-11-18" in line and "2026-11-25" in line for line in changed)
    # Unchanged series are named with their dates and no value.
    unchanged = next(line for line in body if line.startswith("No change: "))
    assert "EFFR (2026-11-20 to 2026-11-27)" in unchanged
    assert not re.search(r"\d\.\d", unchanged)
    assert "regime" not in text(note).lower()


def test_a_restricted_series_is_named_and_never_given_a_value():
    snapshot = _snapshot(withheld=("sofr_pct",))
    note = compose(snapshot, ISSUE, [])
    assert gates(note, snapshot, []) == []
    sofr = next(fact for fact in note["facts"] if fact["series"] == "sofr")
    assert sofr["kind"] == "restricted" and "start" not in sofr and "end" not in sofr
    body = text(note)
    assert "Restricted, no value published: SOFR." in body
    assert not re.search(r"SOFR (moved|was|stood)", body)


def test_the_rights_gate_refuses_a_value_the_projection_does_not_publish():
    public, withheld = _snapshot(), _snapshot(withheld=("sofr_pct",))
    note = compose(public, ISSUE, [])  # composed while SOFR was public
    failures = gates(note, withheld, [])
    assert any(failure.startswith("rights: sofr carries a value") for failure in failures)


def test_a_desk_that_is_not_public_publishes_nothing():
    snapshot = _snapshot(desk="withheld")
    note = compose(snapshot, ISSUE, [])
    assert all(fact["kind"] == "restricted" for fact in note["facts"])
    assert "changed_facts: no series changed during the week" in gates(note, snapshot, [])


# ---------------------------------------------------------------- the gates

def test_the_overlap_gate_refuses_a_repeat_of_any_of_the_last_four_notes():
    snapshot = _snapshot()
    note = compose(snapshot, ISSUE, [])
    repeat = _published(dict(note, issue="2026-11-23"))
    # Four earlier notes about other weeks, sharing no 8-word phrase with this one.
    different = [_published(compose(_snapshot(), dt.date(2026, 10, 5) + dt.timedelta(weeks=i), []))
                 for i in range(4)]
    assert all(weekly_note.overlap(text(note), text(earlier)) <= 0.20 for earlier in different)
    assert weekly_note.overlap(text(note), text(repeat)) == 1.0
    assert any(failure.startswith("phrase_overlap: 100%") for failure in gates(note, snapshot, [repeat]))
    # Only the last four notes count: a repeat five notes back does not block.
    assert not any(failure.startswith("phrase_overlap") for failure in gates(note, snapshot, [repeat, *different]))
    assert weekly_note.OVERLAP_LIMIT == 0.20 and weekly_note.OVERLAP_NOTES == 4 and weekly_note.SHINGLE_WORDS == 8


def test_the_numbers_dated_gate_refuses_a_number_without_a_date(monkeypatch):
    monkeypatch.setattr(weekly_note, "FORMS", ("{label} moved to {v1}, {direction} {delta}.",) * 3)
    snapshot = _snapshot()
    note = compose(snapshot, ISSUE, [])
    assert any(failure.startswith("numbers_dated") for failure in gates(note, snapshot, []))


def test_the_facts_only_gate_refuses_forecast_or_regime_words(monkeypatch):
    monkeypatch.setattr(weekly_note, "FORMS", ("{label} is likely to rise from {v1} on {d1}.",) * 3)
    snapshot = _snapshot()
    note = compose(snapshot, ISSUE, [])
    assert any(failure.startswith("facts_only") for failure in gates(note, snapshot, []))


def test_the_no_daily_gate_refuses_any_daily_promise(monkeypatch):
    rows = [row if row[0] != "sofr" else ("sofr", *row[1:3], "SOFR in the daily letter", *row[4:])
            for row in weekly_note.SERIES]
    monkeypatch.setattr(weekly_note, "SERIES", tuple(rows))
    snapshot = _snapshot()
    note = compose(snapshot, ISSUE, [])
    assert any(failure.startswith("no_daily") for failure in gates(note, snapshot, []))


def test_a_week_with_no_change_publishes_nothing():
    snapshot = _snapshot(sofr_end=3.90)
    flat = json.loads(json.dumps(snapshot))
    for chart in flat["engines"]["money_market"]["charts"].values():
        last = {}
        for row in chart["rows"]:
            for at, value in enumerate(row[1:], start=1):
                if value is not None:
                    last.setdefault(at, value)
                    row[at] = last[at]
    note = compose(flat, ISSUE, [])
    assert "changed_facts: no series changed during the week" in gates(note, flat, [])


# -------------------------------------------------------------- the archive

def test_the_archive_is_absent_only_when_file_and_page_are_both_missing():
    replies = {"https://seiche.info/weekly/notes.json": (404, b""), "https://seiche.info/weekly/": (404, b"")}
    assert weekly_note.fetch_archive(lambda url: replies[url]) == []
    replies["https://seiche.info/weekly/"] = (200, b"<html></html>")
    with pytest.raises(SystemExit):
        weekly_note.fetch_archive(lambda url: replies[url])
    with pytest.raises(SystemExit):
        weekly_note.fetch_archive(lambda url: (503, b""), sleep=lambda seconds: None)


def test_a_broken_or_altered_archive_stops_the_build():
    first = _published(compose(_snapshot(), ISSUE, []))
    good = {"schema": weekly_note.SCHEMA, "notes": [first]}
    assert weekly_note.verify(json.loads(json.dumps(good))) == [first]
    altered = json.loads(json.dumps(good))
    altered["notes"][0]["facts"][0]["end"]["value"] = 9.99
    with pytest.raises(SystemExit):
        weekly_note.verify(altered)
    second = dict(first, issue="2026-12-07", prev_sha256="0" * 64)
    second["sha256"] = weekly_note._digest(second)
    with pytest.raises(SystemExit):
        weekly_note.verify({"schema": weekly_note.SCHEMA, "notes": [first, second]})


def _one_note_archive(change):
    """A valid one-note archive whose note is altered by `change` and then
    re-digested, so only a field check, never the hash chain, can refuse it."""
    note = _published(compose(_snapshot(), ISSUE, []))
    change(note)
    note["sha256"] = weekly_note._digest(note)
    return {"schema": weekly_note.SCHEMA, "notes": [note]}


@pytest.mark.parametrize("issue", ["../outside", "../../frontend", "/tmp/seiche-escape", "2026-11-30/..",
                                   "2026-11-24", "2026-02-30", "２０２６-11-30", 20261130, None])
def test_the_archive_refuses_an_issue_that_is_not_the_iso_date_of_a_monday(issue):
    """The archive is read back from the site and its chain describes only
    itself; an issue names a folder under weekly/, so it must be a Monday's
    ISO date. The refusal does not echo the value it refused."""
    with pytest.raises(SystemExit, match="an issue is not the ISO date of a Monday") as refused:
        weekly_note.verify(_one_note_archive(lambda note: note.update(issue=issue)))
    assert str(issue) not in str(refused.value)


@pytest.mark.parametrize("terms_url", ["javascript:alert(1)", "/terms"])
def test_the_archive_refuses_a_terms_link_that_is_not_http_or_https(terms_url):
    with pytest.raises(SystemExit, match="a terms link is not http or https"):
        weekly_note.verify(_one_note_archive(lambda note: note["sources"][0].update(terms_url=terms_url)))


def test_the_archive_accepts_a_source_without_a_terms_link():
    archive = _one_note_archive(lambda note: note["sources"][0].update(terms_url=None))
    assert weekly_note.verify(archive)[0]["sources"][0]["terms_url"] is None


def test_the_archive_refuses_a_published_at_that_is_not_an_iso_clock():
    with pytest.raises(SystemExit, match="published_at is not an ISO clock"):
        weekly_note.verify(_one_note_archive(lambda note: note.update(published_at="yesterday")))


def _public(tmp_path, snapshot):
    public = tmp_path / "public"
    (public / "data").mkdir(parents=True)
    (public / "data" / "overview.json").write_text(json.dumps(snapshot))
    (public / "money-markets").mkdir()
    (public / "money-markets" / "catalog.json").write_text(
        json.dumps({"canonical_url": "https://seiche.info/money-markets/"}))
    (public / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n    <loc>https://seiche.info/</loc>\n  </url>\n</urlset>\n")
    return public


def test_publish_adds_the_due_note_pages_index_and_sitemap_rows_then_holds(tmp_path):
    public = _public(tmp_path, _snapshot())
    logs = []
    written = weekly_note.publish(public, enabled=True, fetch=lambda: [], log=logs.append)
    assert logs == [{"status": "published", "issue": "2026-11-30", "sha256": logs[0]["sha256"]}]
    page = (public / "weekly" / "2026-11-30" / "index.html").read_text()
    archive = json.loads((public / "weekly" / "notes.json").read_text())
    assert str(public / "weekly" / "2026-11-30" / "index.html") in written
    assert weekly_note.verify(archive)[0]["issue"] == "2026-11-30"
    jsonld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
    assert jsonld["datePublished"] == jsonld["dateModified"] == GENERATED
    assert '<a href="/money-markets/">Money-market evidence map</a>' in page
    assert json.loads((public / "weekly" / "index.json").read_text())[0]["slug"] == "2026-11-30"
    sitemap = (public / "sitemap.xml").read_text()
    assert "<loc>https://seiche.info/weekly/</loc>\n    <lastmod>2026-11-30</lastmod>" in sitemap
    assert "<loc>https://seiche.info/weekly/2026-11-30/</loc>" in sitemap
    # A later build in the same week keeps the note as published and adds none.
    again = _public(tmp_path / "next", _snapshot(generated="2026-11-30T18:23:00+00:00"))
    later = []
    weekly_note.publish(again, enabled=True, fetch=lambda: archive["notes"], log=later.append)
    assert later == [{"status": "already_published", "issue": "2026-11-30"}]
    assert (again / "weekly" / "2026-11-30" / "index.html").read_text() == page


def test_no_note_is_composed_before_the_first_issue(tmp_path):
    """Owner decision of 27 Nov 2026: the first note is the issue of Monday
    30 Nov 2026, due from 12:00 UTC. Before then the due issue is 23 Nov:
    nothing is composed or written, and the build log says why."""
    assert weekly_note.FIRST_ISSUE == "2026-11-30"
    assert dt.date.fromisoformat(weekly_note.FIRST_ISSUE).weekday() == weekly_note.ISSUE_WEEKDAY
    for clock in ("2026-11-27T02:04:05+00:00", "2026-11-30T11:59:59+00:00"):
        public = _public(tmp_path / clock[:13], _snapshot(generated=clock))
        logs = []
        assert weekly_note.publish(public, enabled=True, fetch=lambda: [], log=logs.append) == []
        assert logs == [{"status": "before_first_issue", "issue": "2026-11-23", "first_issue": "2026-11-30"}]
        assert not (public / "weekly").exists()
        assert "/weekly/" not in (public / "sitemap.xml").read_text()


def test_the_first_issue_is_composed_from_monday_30_november_noon(tmp_path):
    for clock in ("2026-11-30T12:00:00+00:00", "2026-12-01T09:00:00+00:00"):
        public = _public(tmp_path / clock[:13], _snapshot(generated=clock))
        logs = []
        written = weekly_note.publish(public, enabled=True, fetch=lambda: [], log=logs.append)
        assert [entry["status"] for entry in logs] == ["published"] and logs[0]["issue"] == "2026-11-30"
        assert str(public / "weekly" / "2026-11-30" / "index.html") in written


def test_before_the_first_issue_an_existing_archive_is_still_re_rendered(tmp_path):
    """The guard composes nothing new and keeps everything else: an archive
    that holds a note is re-rendered as on any build (first_issue injected)."""
    archived = _published(compose(_snapshot(), ISSUE, []))
    public = _public(tmp_path, _snapshot(generated="2026-12-07T13:00:00+00:00"))
    logs = []
    written = weekly_note.publish(public, enabled=True, fetch=lambda: [archived], log=logs.append,
                                  first_issue="2026-12-14")
    assert logs == [{"status": "before_first_issue", "issue": "2026-12-07", "first_issue": "2026-12-14"}]
    assert str(public / "weekly" / "2026-11-30" / "index.html") in written
    notes = weekly_note.verify(json.loads((public / "weekly" / "notes.json").read_text()))
    assert [note["issue"] for note in notes] == ["2026-11-30"]
    assert "<loc>https://seiche.info/weekly/2026-11-30/</loc>" in (public / "sitemap.xml").read_text()


def test_a_refused_note_publishes_nothing_and_logs_why(tmp_path):
    previous = _published(dict(compose(_snapshot(), ISSUE, []), issue="2026-11-23"))
    public = _public(tmp_path, _snapshot())
    logs = []
    weekly_note.publish(public, enabled=True, fetch=lambda: [previous], log=logs.append)
    assert logs[0]["status"] == "refused" and logs[0]["issue"] == "2026-11-30"
    assert any(gate.startswith("phrase_overlap") for gate in logs[0]["gates"])
    assert not (public / "weekly" / "2026-11-30").exists()
    notes = json.loads((public / "weekly" / "notes.json").read_text())["notes"]
    assert [note["issue"] for note in notes] == ["2026-11-23"]


def test_a_note_whose_source_is_later_restricted_is_withdrawn(tmp_path):
    published = _published(dict(compose(_snapshot(), ISSUE, []), issue="2026-11-23"))
    public = _public(tmp_path, _snapshot(withheld=("sofr_pct",)))
    logs = []
    weekly_note.publish(public, enabled=True, fetch=lambda: [published], log=logs.append)
    assert logs[0] == {"status": "withdrawn", "issue": "2026-11-23",
                       "reason": "a source of this note is no longer published"}
    stub = json.loads((public / "weekly" / "notes.json").read_text())["notes"][0]
    assert stub["status"] == "withdrawn" and "facts" not in stub and stub["sha256"] == published["sha256"]
    assert not (public / "weekly" / "2026-11-23").exists()
    assert "2026-11-23" not in (public / "weekly" / "index.html").read_text()


def test_publish_refuses_an_issue_that_would_leave_weekly_and_writes_nothing(tmp_path):
    """An injected fetch bypasses verify(), so publish() checks every issue
    itself before it writes anything."""
    escaping = _one_note_archive(lambda note: note.update(issue="../../outside"))["notes"]
    public = _public(tmp_path, _snapshot())
    sitemap = (public / "sitemap.xml").read_text()
    with pytest.raises(SystemExit, match="nothing written"):
        weekly_note.publish(public, enabled=True, fetch=lambda: escaping, log=lambda record: None)
    assert not (tmp_path / "outside").exists()
    assert not (public / "weekly").exists()
    assert (public / "sitemap.xml").read_text() == sitemap


def test_publish_refuses_a_note_folder_that_resolves_outside_weekly(tmp_path):
    """The symlink defence: a note page is written only inside weekly/."""
    public = _public(tmp_path, _snapshot())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (public / "weekly").mkdir()
    (public / "weekly" / "2026-11-30").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(SystemExit, match="outside weekly/"):
        weekly_note.publish(public, enabled=True, fetch=lambda: [], log=lambda record: None)
    assert list(elsewhere.iterdir()) == []


def test_a_terms_link_that_is_not_http_or_https_is_composed_as_none_and_never_linked():
    snapshot = _snapshot()
    snapshot["rights_projection"]["sources"]["nyfed_original"]["terms_url"] = "javascript:x"
    note = _published(compose(snapshot, ISSUE, []))
    source = next(source for source in note["sources"] if source["key"] == "nyfed_original")
    assert source["terms_url"] is None
    page = weekly_note.render_note(note)
    assert "javascript:" not in page
    assert f"<li>{source['attribution']}</li>" in page
    assert '<a href="https://example.org/fed_h15/terms">Terms</a>' in page
    # The composer never writes a note that the next build's verify() refuses.
    assert weekly_note.verify({"schema": weekly_note.SCHEMA, "notes": [note]}) == [note]


# ----------------------------------------------------------- the family shell

def _pages():
    note = _published(compose(_snapshot(), ISSUE, []))
    return {"weekly/index.html": weekly_note.render_archive([note], "/money-markets/"),
            "weekly/2026-11-30/index.html": weekly_note.render_note(note, "/money-markets/")}


@pytest.mark.parametrize("name", ["weekly/index.html", "weekly/2026-11-30/index.html"])
def test_weekly_pages_meet_the_frontend_family_contract(name):
    """The assertions of familyInterface.test.mjs: the frontend tests scan these
    pages when the publisher writes them into frontend/public."""
    page = _pages()[name]
    assert re.search(r'<html\b[^>]*class="[^"]*research-interface[^"]*"[^>]*data-product="seiche"', page)
    for href in ("/research-ui/research.css", "/research-ui/family-tokens.css", "/research-ui/seiche-pages.css"):
        assert f'<link rel="stylesheet" href="{href}">' in page
    assert page.count('<header class="research-header">') == 1
    mast = family_shell.HEADER[: family_shell.HEADER.index('<nav class="research-tabs"')]
    assert mast.replace('<a class="research-skip" href="#main">Skip to content</a>', "") in page
    assert family_shell.CONTEXT in page
    assert len(re.findall(r"<h1\b", page)) == 1 and f'<h1 id="{family_shell.SKIP_TARGET}">' in page
    assert page.index(family_shell.STYLE) < page.index("<style>")
    assert "--accent:var(--research-accent" in page
    assert not re.search(r"fonts\.googleapis\.com|fonts\.gstatic\.com|[\u2013\u2014]", page)
    assert not weekly_note.DAILY_PROMISE.search(page)


def _served_archive():
    first = _published(compose(_snapshot(), ISSUE, []))
    return first, json.dumps({"schema": weekly_note.SCHEMA, "notes": [first]}).encode()


def test_a_failed_read_is_retried_then_served_by_the_pages_hostname():
    first, body = _served_archive()
    calls, pauses, logged = [], [], []

    def get(url):
        calls.append(url)
        return (0, b"") if url.startswith("https://seiche.info/") else (200, body)

    assert weekly_note.fetch_archive(get, sleep=pauses.append, log=logged.append) == [first]
    assert calls == ["https://seiche.info/weekly/notes.json"] * 3 + ["https://seiche.pages.dev/weekly/notes.json"]
    assert pauses == [weekly_note.RETRY_PAUSE_SECONDS] * 2
    assert logged == [{"status": "archive_fallback", "source": "https://seiche.pages.dev",
                       "site_failures": ["seiche.info HTTP 0"] * 3}]


def test_a_transient_failure_recovers_on_seiche_info_itself():
    first, body = _served_archive()
    replies = iter([(503, b""), (200, body)])
    assert weekly_note.fetch_archive(lambda url: next(replies), sleep=lambda seconds: None) == [first]


def test_a_404_on_the_pages_hostname_never_counts_as_an_empty_archive():
    def get(url):
        return (403, b"") if url.startswith("https://seiche.info/") else (404, b"")

    with pytest.raises(SystemExit, match="unreadable"):
        weekly_note.fetch_archive(get, sleep=lambda seconds: None)


def test_a_broken_archive_from_the_pages_hostname_still_stops_the_build():
    first, _ = _served_archive()
    altered = json.dumps({"schema": weekly_note.SCHEMA, "notes": [dict(first, sha256="0" * 64)]}).encode()

    def get(url):
        return (503, b"") if url.startswith("https://seiche.info/") else (200, altered)

    with pytest.raises(SystemExit):
        weekly_note.fetch_archive(get, sleep=lambda seconds: None, log=lambda record: None)


def test_no_answer_at_all_reads_as_status_zero(monkeypatch):
    def refuse(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(weekly_note.urllib.request, "urlopen", refuse)
    assert weekly_note._get("https://seiche.info/weekly/notes.json") == (0, b"")
