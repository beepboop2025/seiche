import datetime as dt
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

from seiche import wake_public as public, wakeflows

NOW = dt.datetime(2026, 9, 30, 7, tzinfo=dt.timezone.utc)


def pack():
    provenance = []
    for key, (source, url, _) in public.SOURCES.items():
        ref, released = {"cftc": ("2026-09-22", "2026-09-25"), "custody": ("2026-09-23", "2026-09-24"), "spread": ("2026-09-28", "2026-09-29")}[key]
        provenance.append(dict(source=source, url=url, reference_date=ref, release_date=released, retrieved_at=NOW.isoformat()))
    return dict(product="seiche", schema_version="1.0", generated_at=NOW.isoformat(), as_of="2026-09-30",
                method_versions={"private": "secret"}, provenance=provenance,
                payload={"positioning_flows": {
                    "basis_nowcast": dict(ref_date="2026-09-22", basis_size_usd_bn=10, gross_short_usd_bn=20, pension_long_usd_bn=11, fragile=False),
                    "sovereign_custody": dict(ref_date="2026-09-23", level_usd_bn=100, private_model="secret"),
                    "fusion_index": dict(date="2026-09-28", index=1, sigma=.2, band68=[.8, 1.2], private_model="secret"),
                }, "stress_endogeneity": dict(branching_ratio=.5, endogeneity="moderate", n_events=85, p_event_next={"5d": .1, "21d": .2, "private": "secret"})})


def test_projection_contains_only_public_readings_and_clocks():
    envelope = public.project(pack(), now=NOW)
    assert "secret" not in json.dumps(envelope)
    assert envelope["sources"]["cftc"]["reference_date"] == "2026-09-22"
    out = public.validate(envelope, now=NOW)
    assert out["status"] == "fresh"
    assert out["sections"]["basis_trade"]["oldest_input_date"] == "2026-09-22"
    assert out["sections"]["basis_trade"]["source_ids"] == ["cftc", "spread"]


def test_new_generation_does_not_refresh_old_observations():
    raw = pack()
    raw["provenance"][0].update(reference_date="2026-08-01", release_date="2026-08-04")
    raw["payload"]["positioning_flows"]["basis_nowcast"]["ref_date"] = "2026-08-01"
    out = public.validate(public.project(raw, now=NOW), now=NOW)
    assert out["status"] == "partial"
    assert out["sections"]["basis_trade"]["status"] == "stale"
    assert "basis_trade" not in out and "pension_duration" not in out and "fusion_index" not in out
    assert "sovereign_custody" in out


def test_missing_sources_are_unavailable_not_empty_fresh_readings():
    raw = pack()
    raw["provenance"] = raw["provenance"][1:]
    out = public.validate(public.project(raw, now=NOW), now=NOW)
    assert out["sections"]["basis_trade"]["status"] == "unavailable"
    assert "basis_trade" not in out


@pytest.mark.parametrize("value", [{"private": "secret"}, {}, {"size_usd_bn": 10}, {"size_usd_bn": None, "gross_short_usd_bn": None, "ref_date": "2026-09-22"}])
def test_incomplete_readings_are_unavailable(value):
    raw = public.project(pack(), now=NOW)
    raw["values"]["basis_trade"] = value
    out = public.validate(raw, now=NOW)
    assert out["sections"]["basis_trade"]["status"] == "unavailable"
    assert "basis_trade" not in out


@pytest.mark.parametrize("date", ["20260922", "2026-W39-2", "2026-09-22T00:00:00", "2026-9-22"])
def test_non_normalized_dates_rejected(date):
    raw = public.project(pack(), now=NOW)
    raw["sources"]["cftc"]["reference_date"] = date
    with pytest.raises(wakeflows.WakePackError):
        public.validate(raw, now=NOW)


@pytest.mark.parametrize("field,value", [("generated_at", "2026-09-26T00:00:00+00:00"), ("generated_at", "2026-10-01T00:00:00+00:00"), ("generated_at", "2026-09-30T07:00:00"), ("as_of", "2026-10-01")])
def test_bad_envelope_clocks_rejected(field, value):
    raw = public.project(pack(), now=NOW)
    raw[field] = value
    with pytest.raises(wakeflows.WakePackError):
        public.validate(raw, now=NOW)


@pytest.mark.parametrize("field,value", [("reference_date", "2026-10-01"), ("release_date", "2026-09-01"), ("retrieved_at", "2026-08-01T00:00:00+00:00"), ("source", "different"), ("url", "https://example.invalid")])
def test_source_identity_and_clock_order_rejected(field, value):
    raw = public.project(pack(), now=NOW)
    raw["sources"]["cftc"][field] = value
    with pytest.raises(wakeflows.WakePackError):
        public.validate(raw, now=NOW)


def test_reading_date_must_match_source():
    raw = public.project(pack(), now=NOW)
    raw["values"]["basis_trade"]["ref_date"] = "2026-09-30"
    with pytest.raises(wakeflows.WakePackError, match="differs"):
        public.validate(raw, now=NOW)


def test_no_readings_and_nonfinite_values_fail_closed():
    raw = public.project(pack(), now=NOW)
    for bad in ({}, {"basis_trade": {"size_usd_bn": float("nan"), "gross_short_usd_bn": 20, "ref_date": "2026-09-22"}}):
        raw["values"] = bad
        with pytest.raises(wakeflows.WakePackError):
            public.validate(raw, now=NOW)


def test_fixed_endpoint_bounded_load(monkeypatch):
    body = json.dumps(public.project(pack(), now=NOW)).encode()
    requests = []
    class Opener:
        def open(self, request, timeout):
            requests.append((request.full_url, timeout))
            return io.BytesIO(body)
    monkeypatch.setattr(public.urllib.request, "build_opener", lambda *args: Opener())
    assert public.load(now=NOW)["status"] == "fresh"
    assert requests == [(public.URL, 8)]
    body = b" " * (public.MAX_BYTES + 1)
    with pytest.raises(wakeflows.WakePackError):
        public.load(now=NOW)


def test_redirect_handler_never_follows():
    assert public._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid") is None


def test_remote_does_not_expose_unexpected_private_keys():
    raw = public.project(pack(), now=NOW)
    raw["values"]["basis_trade"]["private"] = "secret"
    raw["sources"]["cftc"]["private"] = "secret"
    assert "secret" not in json.dumps(public.validate(raw, now=NOW))


def test_exact_caddy_route_is_read_only_and_cannot_expose_private_pack(tmp_path):
    caddy = shutil.which("caddy")
    if not caddy:
        pytest.skip("Caddy route contract is exercised in Linux release qualification")
    root = tmp_path / "public"
    root.mkdir()
    body = json.dumps(public.project(pack(), now=NOW)).encode()
    (root / "public-institutional-flows.json").write_bytes(body)
    (tmp_path / "wake_seiche.json").write_text("private-pack")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    source = Path(__file__).resolve().parents[2] / "ops/wake-publication/caddy-route.conf"
    route = source.read_text().replace("/var/lib/seiche-wake-public", str(root))
    config = tmp_path / "Caddyfile"
    config.write_text("{\n admin off\n auto_https off\n}\nhttp://127.0.0.1:" + str(port) + " {\n" + route + "\nhandle {\n respond 404\n}\n}\n")
    subprocess.run([caddy, "validate", "--config", str(config)], check=True, capture_output=True)
    process = subprocess.Popen([caddy, "run", "--config", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/api/public-institutional-flows.json"
    try:
        for attempt in range(50):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    assert response.read() == body
                    assert response.headers["Cache-Control"] == "no-store"
                break
            except urllib.error.URLError:
                if attempt == 49:
                    raise
                time.sleep(.1)
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=2) as response:
            assert response.status == 200 and response.read() == b""
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url, method="POST", data=b""), timeout=2)
        assert error.value.code == 405
        for path in ("/api/wake_seiche.json", "/api/public-institutional-flows.json/wake_seiche.json", "/api/"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"http://127.0.0.1:{port}" + path, timeout=2)
            assert error.value.code == 404
    finally:
        process.terminate()
        process.wait(timeout=5)
