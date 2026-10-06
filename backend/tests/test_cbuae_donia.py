"""DONIA keeps its own dated evidence and never borrows USD/FX freshness."""
import asyncio
from contextlib import contextmanager
import io
from threading import Event
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from seiche import gift_city, store
from seiche.sources import cbuae_donia as source
from seiche.sources.base import SourceFault
from seiche.sources.publication import publication_freshness

FETCHED = "2026-10-06T11:00:00+00:00"
NOW = datetime.fromisoformat(FETCHED)


def chart(days=None, values=None):
    days = ["2026-10-05", "2026-10-06"] if days is None else days
    values = [4.2187, 4.047] if values is None else values
    return {name: {"labels": list(days), "donia": list(values)} for name in source._PANELS}


def html(value=None):
    body = json.dumps(chart() if value is None else value)
    return ('<script type="application/json" id="ki-chart-data">' + body + '</script>').encode()


@contextmanager
def public_response(handler):
    """Stub only the wire response; run the bounded reader and cache normally."""
    class Response(io.BytesIO):
        def __init__(self, response):
            super().__init__(response.content)
            self.status = response.status_code
            self.headers = response.headers
        def geturl(self):
            return source.SOURCE_URL
    class Opener:
        def open(self, request, timeout):
            assert request.get_header("User-agent") == source.SOURCE_USER_AGENT
            assert request.get_header("Accept-language") == "en-US,en;q=0.9"
            assert request.get_header("Authorization") is None
            assert request.get_header("Cookie") is None
            assert timeout == 10
            return Response(handler(httpx.Request("GET", request.full_url)))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(source, "build_opener", lambda *_: Opener())
        yield None


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "donia.sqlite")
    monkeypatch.setattr(source, "utcnow_iso", lambda: FETCHED)
    return tmp_path


def test_parser_uses_donia_array_and_native_chart_dates_only():
    value = chart()
    for panel in value.values():
        panel.update(base=[3.9, 3.9], eibor=[3.66293, 3.7063], cmf=[4.4, 4.4])
    row = source.parse_html(html(value), fetched_at=FETCHED)
    assert row.mnemonic == "DONIA" and row.source == "cbuae_donia" and row.unit == "%"
    assert row.points.tolist() == [4.2187, 4.047]
    assert row.asof == "2026-10-06" and row.fetched_at == FETCHED


@pytest.mark.parametrize("days,values", [
    ([], []), (["2026-10-06"], []), (["2026-10-06"] * 2, [4, 4]),
    (["2026-10-06", "2026-10-05"], [4, 4]), (["2026-10-07"], [4]),
    (["2026-1-06"], [4]), (["2026-02-30"], [4]), (["2020-10-06"], [4]),
    (["2026-10-06"], [None]), (["2026-10-06"], [True]), (["2026-10-06"], ["4.0"]),
    (["2026-10-06"], [float("nan")]), (["2026-10-06"], [float("inf")]),
    (["2026-10-06"], [101]),
])
def test_malformed_or_future_observation_never_becomes_a_rate(days, values):
    with pytest.raises(ValueError):
        source.parse_html(html(chart(days, values)), fetched_at=FETCHED)


def test_missing_history_is_not_filled_and_missing_latest_is_not_forward_filled():
    row = source.parse_html(html(chart(values=[None, 4.047])), fetched_at=FETCHED)
    assert row.points.tolist() == [4.047] and row.asof == "2026-10-06"
    with pytest.raises(ValueError):
        source.parse_html(html(chart(values=[4.2187, None])), fetched_at=FETCHED)


def test_misaligned_all_history_is_excluded_without_shifting_bounded_dates():
    value = chart()
    value["all"] = {"labels": ["2026-10-02", "2026-10-05", "2026-10-06"], "donia": [9.9, 8.8]}
    item = source.parse_html(html(value), fetched_at=FETCHED)
    assert item.points.tolist() == [4.2187, 4.047]
    assert item.asof == "2026-10-06"
    value["oneYear"]["donia"].pop(0)
    with pytest.raises(ValueError, match="incomplete"):
        source.parse_html(html(value), fetched_at=FETCHED)


def test_ambiguous_or_conflicting_chart_is_rejected():
    bad = chart()
    bad["oneMonth"]["donia"][-1] = 9.0
    for body in (html(bad), html() + html(), html().replace(b'</script>', b''),
                 html().replace(b'"donia":', b'"donia": [], "donia":', 1),
                 html().replace(b'application/json', b'text/javascript'), b'<p>DONIA 4.047</p>'):
        with pytest.raises(ValueError):
            source.parse_html(body, fetched_at=FETCHED)


def test_source_identity_and_timezone_are_required():
    with pytest.raises(ValueError):
        source.parse_html(html(), fetched_at=FETCHED, source_url="https://example.org")
    with pytest.raises(ValueError):
        source.parse_html(html(), fetched_at="2026-10-06T11:00:00")


@pytest.mark.asyncio
async def test_atomic_capture_has_raw_hash_rights_and_unknown_publication_time(isolated_store):
    body = html()
    with public_response(lambda _: httpx.Response(200, content=body)) as client:
        await source.fetch(client, force=True)
    metadata = store.load_blob(source.LATEST_CAPTURE_KEY)
    assert Path(metadata["raw_path"]).read_bytes() == body
    assert metadata["evidence_sha256"] == hashlib.sha256(body).hexdigest()
    assert metadata["source_publication_time"] is None
    assert metadata["observation_date_basis"] == "publisher_chart_date"
    assert metadata["history_range"] == "oneYear" and metadata["excluded_chart_ranges"] == ["all"]
    assert metadata["day_count"] == "ACT/360"
    assert metadata["benchmark_scope"] == "mixed secured and unsecured overnight funding"
    row = source.read_reference(now=NOW)
    assert row["value"] == 4.047 and row["as_of"] == "2026-10-06" and row["status"] == "FRESH"
    assert row["reference_only"] is True and row["missed_publication_opportunities"] is None
    assert "raw_path" not in row["evidence"] and row["evidence"]["terms_url"] == source.TERMS_URL


@pytest.mark.asyncio
async def test_network_failure_does_not_renew_observation_or_capture_clock(isolated_store, monkeypatch):
    responses = iter([httpx.Response(200, content=html()), httpx.Response(503)])
    with public_response(lambda _: next(responses)) as client:
        await source.fetch(client, force=True)
        prior = store.load_blob(source.LATEST_CAPTURE_KEY)
        monkeypatch.setattr(source, "utcnow_iso", lambda: "2026-10-08T11:00:00+00:00")
        faults = []
        result = await source.fetch(client, faults, force=True)
    assert faults and result["DONIA"].fetched_at == FETCHED
    assert store.load_blob(source.LATEST_CAPTURE_KEY) == prior
    row = source.read_reference(now=datetime(2026, 10, 8, 11, tzinfo=UTC))
    assert row["status"] == "STALE" and row["as_of"] == "2026-10-06"


@pytest.mark.asyncio
async def test_regression_rejected_but_same_day_revision_retained(isolated_store, monkeypatch):
    bodies = iter([html(), html(chart(["2026-10-05"], [4.2187])), html(chart(values=[4.2187, 4.05]))])
    with public_response(lambda _: httpx.Response(200, content=next(bodies))) as client:
        await source.fetch(client, force=True)
        first = store.load_blob(source.LATEST_CAPTURE_KEY)
        with pytest.raises(SourceFault, match="regressed"):
            await source.fetch(client, force=True)
        assert store.load_blob(source.LATEST_CAPTURE_KEY) == first
        monkeypatch.setattr(source, "utcnow_iso", lambda: "2026-10-06T12:00:00+00:00")
        revised = await source.fetch(client, force=True)
    assert revised["DONIA"].asof == "2026-10-06" and revised["DONIA"].points.iloc[-1] == 4.05
    assert store.load_blob(source.CAPTURE_PREFIX + first["evidence_sha256"] + ':' + FETCHED) == first


@pytest.mark.asyncio
async def test_ttl_avoids_network_and_generation_conflicts_fail_closed(isolated_store, monkeypatch):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=html())
    with public_response(handler) as client:
        await source.fetch(client)
        monkeypatch.setattr(store, "is_fresh", lambda *_: True)
        await source.fetch(client)
    assert calls == [source.SOURCE_URL]
    original = store.load_series
    monkeypatch.setattr(store, "load_series", lambda name: replace(original(name), source="fred"))
    assert source.read_reference(now=NOW)["status"] == "UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(302, headers={"location": "https://example.org"}),
    httpx.Response(403), httpx.Response(206, content=html()), httpx.Response(200, content=b'x' * 65),
    httpx.Response(200, content=b'x', headers={"content-length": "2"}),
    httpx.Response(200, content=b'x', headers={"content-length": "100000"})])
async def test_redirect_partial_and_oversized_responses_never_persist(isolated_store, monkeypatch, response):
    monkeypatch.setattr(source, "MAX_BODY_BYTES", 64)
    with public_response(lambda _: response) as client:
        with pytest.raises(SourceFault):
            await source.fetch(client, force=True)
    assert store.load_blob(source.LATEST_CAPTURE_KEY) is None


@pytest.mark.asyncio
async def test_failed_commit_never_publishes_a_capture(isolated_store, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("fixture commit failure")
    monkeypatch.setattr(store, "save_series_batch", fail)
    with public_response(lambda _: httpx.Response(200, content=html())) as client:
        with pytest.raises(SourceFault):
            await source.fetch(client, force=True)
    assert source.read_reference(now=NOW)["value"] is None


@pytest.mark.asyncio
async def test_gift_city_uses_cache_when_other_funding_sources_fail(isolated_store, monkeypatch):
    with public_response(lambda _: httpx.Response(200, content=html())) as client:
        await source.fetch(client, force=True)
    def failed_repo():
        raise OSError("fixture repository unavailable")
    monkeypatch.setattr(gift_city, "get_repository", failed_repo)
    rows = gift_city._funding(NOW)
    assert next(r for r in rows if r["currency"] == "AED")["value"] == 4.047
    assert all(r["value"] is None for r in rows if r["currency"] != "AED")


def test_freshness_never_invents_release_counts_or_actual_publication_time():
    observed = "2026-10-06"
    assert publication_freshness("fred", source.REMOTE_ID, "D", observed, now=NOW) is None
    policy = publication_freshness(source.SOURCE, source.REMOTE_ID, "D", observed, now=NOW)
    assert policy["staleness"] == "fresh"
    assert policy["publication_schedule"]["actual_published_at"] is None
    assert policy["publication_schedule"]["missed_publication_opportunities"] is None
    assert source.cbuae_donia_freshness("2026-10-07", now=NOW)["staleness"] == "unknown"


def test_donia_csv_carries_dataset_specific_attribution_and_convention():
    from seiche import methodology
    item = source.parse_html(html(), fetched_at=FETCHED)
    assert methodology.csv_restriction("DONIA") is None
    body = methodology.render_series_csv(item)
    assert "Central Bank of the UAE" in body and source.DATASET_URL in body and source.TERMS_URL in body
    assert "ACT/360" in body and "actual publication times unknown" in body
    assert "2026-10-06,4.047" in body


def test_download_deadline_does_not_commit_partial_bytes(monkeypatch):
    ticks = iter([0, 0, source.DOWNLOAD_DEADLINE_SECONDS])
    monkeypatch.setattr(source.time, "monotonic", lambda: next(ticks))
    with public_response(lambda _: httpx.Response(200, content=html())):
        with pytest.raises(TimeoutError, match="deadline"):
            source._download_sync(Event())


def test_cancelled_download_does_not_start_a_request():
    cancelled = Event()
    cancelled.set()
    with public_response(lambda _: pytest.fail("cancelled download started")):
        with pytest.raises(TimeoutError, match="cancelled"):
            source._download_sync(cancelled)


def test_redirect_handler_never_creates_a_redirected_request():
    assert source._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.org") is None
