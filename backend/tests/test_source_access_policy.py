"""CBUAE DONIA access refusals are stale evidence, not faults; nothing else changes.

The refusal is served by a real local HTTP server through the collector's own
urllib opener, so the 403 and 429 cases arrive as the HTTPError production sees.
"""
from __future__ import annotations

import importlib.util
import io
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.error import HTTPError
from urllib.request import Request

import httpx
import pytest
from fastapi import Response

from seiche import api, assemble, mcp_server, store
from seiche.public_faults import project_public_faults
from seiche.sources import base as source_base
from seiche.sources import cbuae_donia as source
from seiche.sources.base import SourceFault

FETCHED = "2026-10-06T11:00:00+00:00"
REFUSED = "2026-10-06T12:30:00+00:00"
NOW = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
CHALLENGE = "official source refuses automated access (HTTP 403 challenge); last good observation retained"
ROOT = Path(__file__).resolve().parents[2]


def html(days=("2026-10-05", "2026-10-06"), values=(4.2187, 4.047)):
    chart = {name: {"labels": list(days), "donia": list(values)} for name in source._PANELS}
    return ('<script type="application/json" id="ki-chart-data">' + json.dumps(chart) + "</script>").encode()


@contextmanager
def publisher(status: int, *, headers=None, body: bytes = b""):
    """A real local endpoint; the collector's own opener talks to it."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = f"http://127.0.0.1:{server.server_address[1]}/donia"
    real = source.build_opener

    def build(*handlers):
        # The collector's own opener and handlers; only the address is local.
        opener = real(*handlers)

        class Local:
            def open(self, request, timeout):
                assert request.full_url == source.SOURCE_URL
                return opener.open(Request(local, headers=dict(request.header_items())), timeout=timeout)

        return Local()

    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(source, "build_opener", build)
            yield
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def official(body: bytes):
    """The official 200 response, through the collector's bounded reader."""

    class Answer(io.BytesIO):
        status = 200
        headers = {"content-length": str(len(body))}

        def geturl(self):
            return source.SOURCE_URL

    class Opener:
        def open(self, request, timeout):
            return Answer(body)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(source, "build_opener", lambda *_: Opener())
        yield


class Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "donia.sqlite")
    monkeypatch.setattr(source, "utcnow_iso", lambda: FETCHED)
    # Provenance ages series on the wall clock; pin it just after the capture.
    monkeypatch.setattr(source_base, "datetime", Frozen)
    return tmp_path


async def seed():
    with official(html()):
        result = await source.fetch(httpx.AsyncClient(), force=True)
    assert result["DONIA"].fetched_at == FETCHED
    return result


def donia(provenance):
    return next(row for row in provenance if row["mnemonic"] == "DONIA")


@pytest.mark.asyncio
async def test_a_challenge_refusal_keeps_the_last_good_observation_stale_and_out_of_faults(isolated, monkeypatch):
    baseline = donia(assemble._provenance({"cbuae_donia": await seed()}))
    assert baseline["staleness"] == "fresh" and "stale_reason" not in baseline
    monkeypatch.setattr(source, "utcnow_iso", lambda: REFUSED)
    faults = []
    with publisher(403, headers={"cf-mitigated": "challenge"}):
        result = await source.fetch(httpx.AsyncClient(), faults, force=True)
    assert faults == []
    assert result["DONIA"].fetched_at == FETCHED and result["DONIA"].asof == "2026-10-06"
    row = donia(assemble._provenance({"cbuae_donia": result}))
    assert row["staleness"] == "stale"
    assert row["stale_reason"] == CHALLENGE and row["freshness_basis"] == CHALLENGE
    assert row["asof"] == "2026-10-06" and row["fetched_at"] == FETCHED
    assert row["access_refusal"] == {"status": 403, "challenge": True, "refused_at": REFUSED}
    reference = source.read_reference(now=NOW)
    assert reference["status"] == "STALE" and reference["reason"] == CHALLENGE
    assert reference["value"] == 4.047 and reference["as_of"] == "2026-10-06"
    assert reference["access_refusal"]["status"] == 403


@pytest.mark.asyncio
async def test_a_429_refusal_without_a_challenge_is_stale_too(isolated, monkeypatch):
    await seed()
    faults = []
    with publisher(429, headers={"Retry-After": "120"}):
        result = await source.fetch(httpx.AsyncClient(), faults, force=True)
    assert faults == []
    row = donia(assemble._provenance({"cbuae_donia": result}))
    assert row["stale_reason"] == ("official source refuses automated access (HTTP 429); "
                                   "last good observation retained")


@pytest.mark.asyncio
async def test_a_refusal_with_nothing_retained_is_unavailable_with_its_reason_not_a_fault(isolated):
    faults = []
    with publisher(403, headers={"cf-mitigated": "challenge"}):
        result = await source.fetch(httpx.AsyncClient(), faults, force=True)
    assert faults == [] and result == {}
    row = donia(assemble._provenance({"cbuae_donia": result}))
    assert row["availability"] == "unavailable" and row["asof"] is None
    assert row["unavailable_reason"] == ("official source refuses automated access (HTTP 403 challenge); "
                                         "no validated observation is retained")
    reference = source.read_reference(now=NOW)
    assert reference["status"] == "UNAVAILABLE" and reference["value"] is None
    assert reference["reason"].startswith("official source refuses automated access")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["parse", "changed_format", "server_error", "redirect", "unauthorized", "timeout"])
async def test_every_other_donia_failure_stays_a_fault(isolated, monkeypatch, failure):
    await seed()
    faults = []
    client = httpx.AsyncClient()
    if failure == "parse":
        with official(b"<html>maintenance</html>"):
            result = await source.fetch(client, faults, force=True)
    elif failure == "changed_format":
        with official(html(days=("2026-10-06", "2026-10-05"))):
            result = await source.fetch(client, faults, force=True)
    elif failure == "timeout":
        monkeypatch.setattr(source, "_download", AsyncMock(side_effect=TimeoutError("DONIA download deadline exceeded")))
        result = await source.fetch(client, faults, force=True)
    else:
        status, headers = {"server_error": (503, {}), "redirect": (302, {"Location": "https://example.org/"}),
                           "unauthorized": (401, {})}[failure]
        with publisher(status, headers=headers):
            result = await source.fetch(client, faults, force=True)
    assert [fault["source"] for fault in faults] == ["cbuae_donia"]
    assert project_public_faults(faults)
    # The cached observation is retained as before, without a refusal reason.
    row = donia(assemble._provenance({"cbuae_donia": result}))
    assert "stale_reason" not in row and "access_refusal" not in row
    assert source.read_reference(now=NOW)["reason"] is None


@pytest.mark.asyncio
async def test_a_later_good_capture_clears_the_refusal(isolated, monkeypatch):
    await seed()
    with publisher(403, headers={"cf-mitigated": "challenge"}):
        await source.fetch(httpx.AsyncClient(), [], force=True)
    assert source.read_reference(now=NOW)["status"] == "STALE"
    monkeypatch.setattr(source, "utcnow_iso", lambda: "2026-10-06T12:45:00+00:00")
    with official(html(values=(4.2187, 4.05))):
        result = await source.fetch(httpx.AsyncClient(), [], force=True)
    row = donia(assemble._provenance({"cbuae_donia": result}))
    assert "stale_reason" not in row and row["staleness"] == "fresh"
    later = source.read_reference(now=datetime(2026, 10, 6, 13, tzinfo=UTC))
    assert later["status"] == "FRESH" and later["reason"] is None and "access_refusal" not in later


def test_the_policy_is_one_reviewed_entry_and_no_other_source_is_admitted():
    from seiche import source_access

    assert set(source_access.ACCESS_REFUSAL_POLICIES) == {"cbuae_donia"}
    policy = source_access.ACCESS_REFUSAL_POLICIES["cbuae_donia"]
    assert policy.mnemonics == ("DONIA",) and policy.statuses == frozenset({403, 429})
    challenge = HTTPError("https://centralbank.ae/", 403, "Forbidden", {"cf-mitigated": "challenge"}, None)
    spec = importlib.util.spec_from_file_location("source_degradation", ROOT / "ops" / "release" / "source_degradation.py")
    degradation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(degradation)
    for name in degradation.KNOWN_SOURCES - {"cbuae_donia"}:
        assert source_access.refusal(name, challenge) is None, name
        assert source_access.stale_override(name, "DONIA", current_fetched_at=None) is None
    assert source_access.refusal("cbuae_donia", challenge) == {"source": "cbuae_donia", "status": 403, "challenge": True}
    for status in (401, 404, 500, 502, 503):
        assert source_access.refusal("cbuae_donia", HTTPError("u", status, "x", {}, None)) is None
    assert source_access.refusal("cbuae_donia", ValueError("DONIA requires a complete HTTP 200 response")) is None
    # The owner-signed degradation admission for faulted sources is untouched.
    assert degradation.HTTP_DETAIL == "official source returned an HTTP error"


@pytest.mark.asyncio
async def test_the_board_health_gates_and_mcp_see_a_stale_source_not_a_fault(isolated, monkeypatch):
    calls = {
        "fred": ("fetch_many",), "eia_petroleum": ("fetch_many",), "ofr": ("fetch_many",),
        "llamahacks": ("fetch_all",), "windfetch": ("fetch_all",), "ecb": ("fetch_many",),
        "ecb_fx": ("fetch",), "boj": ("fetch_many",), "bis": ("fetch_many",), "crypto": ("fetch_all",),
        "nyfed": ("fetch_secured_rates", "fetch_srf_ops", "fetch_pd_positions", "fetch_fx_swaps"),
        "nyfed_rde": ("fetch_rde",), "fiscaldata": ("fetch_tga_daily", "fetch_auctions", "fetch_upcoming_auctions"),
        "td_auctions": ("fetch_mspd_maturities",), "cftc": ("fetch_tff_ust", "fetch_disaggregated_commodities"),
        "palimpsest": ("fetch_all",), "fedtext": ("fetch_all",), "gdelt": ("fetch_all",),
    }
    for module, names in calls.items():
        for name in names:
            monkeypatch.setattr(getattr(assemble, module), name, AsyncMock(return_value={}))
    await seed()
    # The other CBUAE source keeps failing loudly on the same refusal.
    other = {"source": "cbuae_fx", "detail": "HTTPError: HTTP Error 403: Forbidden"}

    async def cbuae_fx_refused(client, faults):
        raise SourceFault(other["source"], other["detail"])

    monkeypatch.setattr(assemble.cbuae_fx, "fetch", cbuae_fx_refused)
    with publisher(403, headers={"cf-mitigated": "challenge"}):
        sources, faults = await assemble._gather_sources()
    assert faults == [other]
    assert sources["cbuae_donia"]["DONIA"].fetched_at == FETCHED

    # Without the other source's fault the board is clean and DONIA reads stale.
    monkeypatch.setattr(assemble.cbuae_fx, "fetch", AsyncMock(return_value={}))
    with publisher(403, headers={"cf-mitigated": "challenge"}):
        sources, faults = await assemble._gather_sources()
    assert faults == []
    board = {"generated_at": NOW.isoformat(), "version": "0.16.12 estuary", "faults": faults,
             "provenance": assemble._provenance(sources)}
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: board)
    health = api._health_response(Response(), require_rebuilt=False, include_release_candidate=False)
    assert health["faults"] == []
    assert donia(health["provenance"])["stale_reason"] == CHALLENGE
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: board)
    tool = mcp_server.tool_health({}, True)
    assert tool["faults"] == [] and donia(tool["provenance"])["staleness"] == "stale"
    # The recovery monitor's strict readiness predicate passes on this body.
    spec = importlib.util.spec_from_file_location(
        "health_wait", ROOT / "deploy" / "railway-ci" / "recovery-monitor" / "health_wait.py")
    health_wait = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health_wait)
    sha = "a" * 40
    headers = (f"x-seiche-railway-authority: production\r\nx-seiche-release-sha: {sha}\r\n"
               "x-seiche-railway-deployment: 12345678-1234-4234-8234-123456789abc\r\n").encode()
    result, _ = health_wait.assess("production", json.dumps(health).encode(), headers, sha, NOW)
    assert result["reason"] == "fresh" and result["fault_count"] == 0
