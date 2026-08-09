"""Offline tests for the marker-backed Rissaga channel fallback."""

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "bot"))

import rissaga_channel_fallback as fb  # noqa: E402


NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def route(desk="SEICHE", candidate=True):
    return {
        "desk": desk,
        "desk_nice": desk.title(),
        "beat": "plumbing",
        "label": "plumbing",
        "relevance": 8.0,
        "desk_line": "Gauge 42 STRAIN",
        "angle": "mechanics first",
        "fallback_commentary": "Funding mechanics decide whether the story persists.",
        "channel_candidate": candidate,
    }


def item(number=1, desk="SEICHE", dispatch=True):
    out = {
        "story_id": f"rissaga-story-{number}",
        "title": f"Repo story {number}",
        "link": f"https://example.com/{number}",
        "source": "Federal Reserve",
        "n_sources": 1,
        "age": "1h old",
        "score": 8.0,
        "routes": [route(desk)],
    }
    if dispatch:
        out["dispatch_id"] = f"rissaga-dispatch-{number}"
    return out


def payload(items=None, generated=None):
    return {
        "schema": "rissaga.news.v2",
        "generated": generated or NOW.isoformat(timespec="seconds"),
        "items": items if items is not None else [item()],
        "channel_candidates": [0],
    }


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    latest = tmp_path / "latest.json"
    marker = tmp_path / "state" / "rissaga_posted.json"
    monkeypatch.setattr(fb, "LATEST_PATH", str(latest))
    monkeypatch.setattr(fb, "MARKER_PATH", str(marker))
    monkeypatch.setattr(fb, "HELPER_PATH", "/usr/local/bin/lab-channel-post")
    return latest, marker


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_fresh_v2_handoff_required_and_stale_refused(isolated):
    latest, _ = isolated
    write_json(latest, payload())
    assert fb.load_handoff(NOW)["schema"] == "rissaga.news.v2"

    write_json(latest, payload(generated=(NOW - timedelta(hours=8, seconds=1))
                               .isoformat(timespec="seconds")))
    with pytest.raises(fb.HandoffError, match="stale"):
        fb.load_handoff(NOW)

    write_json(latest, {**payload(), "schema": "rissaga.news.v1"})
    with pytest.raises(fb.HandoffError, match="v2"):
        fb.load_handoff(NOW)


def test_only_explicit_candidates_and_caps_are_enforced():
    quiet = item(1)
    quiet["routes"][0]["channel_candidate"] = 1
    assert fb.select_candidates(payload([quiet])) == []

    too_many = payload([item(1), item(2), item(3)])
    with pytest.raises(fb.HandoffError, match="globally"):
        fb.select_candidates(too_many)

    doubled = item(1)
    doubled["routes"].append(route("LIQUILENS"))
    with pytest.raises(fb.HandoffError, match="one channel route"):
        fb.select_candidates(payload([doubled]))


def test_compose_is_scannable_escaped_and_uses_only_safe_links():
    raw = item()
    raw["title"] = "A <tag> & quoted \u2014 second line\njoined"
    raw["link"] = "https://example.com/read?a=1&b=2"
    raw["source"] = "Wire & Co"
    raw["routes"][0]["label"] = "funding <watch>"
    raw["routes"][0]["desk_line"] = "Gauge <42> & STRAIN"
    raw["routes"][0]["fallback_commentary"] = "Mechanics & reserves remain the bounded read."
    selected = fb.select_candidates(payload([raw]))[0]
    message = fb.compose(selected)
    lines = message.splitlines()
    assert len(lines) == 14
    assert "WHAT HAPPENED" in message
    assert "WHY THIS DESK CARES" in message
    assert "LIVE DESK CHECK" in message
    assert "WHAT TO WATCH NEXT" in message
    assert "&lt;tag&gt; &amp; quoted , second line joined" in message
    assert 'href="https://example.com/read?a=1&amp;b=2"' in message
    assert "Wire &amp; Co" in message
    assert "Gauge &lt;42&gt; &amp; STRAIN" in message
    assert "Mechanics &amp; reserves remain the bounded read." in message
    assert "\u2014" not in message and "\u2013" not in message

    raw["link"] = "javascript:alert(1)"
    message = fb.compose(fb.select_candidates(payload([raw]))[0])
    assert "href=" not in message


def test_success_is_marked_per_item_before_later_failure(isolated, monkeypatch):
    latest, marker_path = isolated
    write_json(latest, payload([item(1), item(2, "LIQUILENS")]))
    write_json(marker_path, {"posted": {}, "preserve": "yes"})
    calls = []

    def first_pass(message):
        calls.append(message)
        return len(calls) == 1

    monkeypatch.setattr(fb, "publish", first_pass)
    assert fb.run(now=NOW) == 1
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(marker["posted"]) == {"rissaga-dispatch-1:SEICHE"}
    assert marker["preserve"] == "yes"
    assert stat.S_IMODE(marker_path.stat().st_mode) == 0o600
    lock_path = marker_path.with_name(marker_path.name + ".lock")
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600

    retry_calls = []

    def retry_success(message):
        retry_calls.append(message)
        return True

    monkeypatch.setattr(fb, "publish", retry_success)
    assert fb.run(now=NOW) == 0
    assert len(retry_calls) == 1
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(marker["posted"]) == {
        "rissaga-dispatch-1:SEICHE",
        "rissaga-dispatch-2:LIQUILENS",
    }

    monkeypatch.setattr(fb, "publish",
                        lambda message: pytest.fail("already posted replayed"))
    assert fb.run(now=NOW) == 0


def test_process_crash_after_first_item_retries_only_the_second(isolated,
                                                                 monkeypatch):
    latest, marker_path = isolated
    write_json(latest, payload([item(1), item(2, "LIQUILENS")]))
    calls = []

    def crash_second(message):
        calls.append(message)
        if len(calls) == 2:
            raise RuntimeError("simulated process crash")
        return True

    monkeypatch.setattr(fb, "publish", crash_second)
    with pytest.raises(RuntimeError, match="simulated"):
        fb.run(now=NOW)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(marker["posted"]) == {"rissaga-dispatch-1:SEICHE"}

    retry = []

    def retry_success(message):
        retry.append(message)
        return True

    monkeypatch.setattr(fb, "publish", retry_success)
    assert fb.run(now=NOW) == 0
    assert len(retry) == 1
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(marker["posted"]) == {
        "rissaga-dispatch-1:SEICHE",
        "rissaga-dispatch-2:LIQUILENS",
    }


def test_old_generated_and_story_markers_are_honored(isolated, monkeypatch):
    latest, marker_path = isolated
    handoff = payload([item(1)])
    write_json(latest, handoff)
    write_json(marker_path, {"generated": handoff["generated"]})
    monkeypatch.setattr(fb, "publish",
                        lambda message: pytest.fail("old run marker ignored"))
    assert fb.run(now=NOW) == 0

    legacy_item = item(2, dispatch=False)
    write_json(latest, payload([legacy_item]))
    write_json(marker_path, {
        "posted": {"rissaga-story-2:SEICHE": handoff["generated"]},
        "preserve": "yes",
    })
    assert fb.run(now=NOW) == 0


def test_dry_run_has_no_lock_write_or_send(isolated, monkeypatch, capsys):
    latest, marker_path = isolated
    write_json(latest, payload([item(1), item(2, "LIQUILENS")]))
    original = b'{"posted":{"rissaga-dispatch-1:SEICHE":"old"}}'
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_bytes(original)

    @fb.contextmanager
    def no_lock():
        pytest.fail("dry run acquired delivery lock")
        yield

    monkeypatch.setattr(fb, "delivery_lock", no_lock)
    monkeypatch.setattr(fb, "publish",
                        lambda message: pytest.fail("dry run published"))
    monkeypatch.setattr(fb, "save_marker",
                        lambda marker: pytest.fail("dry run wrote marker"))
    assert fb.run(dry_run=True, now=NOW) == 0
    counts = json.loads(capsys.readouterr().out)
    assert counts == {"already_posted": 1, "candidates": 2,
                      "pending": 1, "posted": 0}
    assert marker_path.read_bytes() == original
    assert not os.path.exists(str(marker_path) + ".lock")


def test_helper_failure_does_not_mark_item(isolated, monkeypatch):
    latest, marker_path = isolated
    write_json(latest, payload())
    monkeypatch.setattr(fb, "publish", lambda message: False)
    assert fb.run(now=NOW) == 1
    assert not marker_path.exists()


def test_all_candidates_are_validated_before_first_send(isolated, monkeypatch):
    latest, marker_path = isolated
    malformed = item(2, "LIQUILENS")
    del malformed["routes"][0]["fallback_commentary"]
    write_json(latest, payload([item(1), malformed]))
    monkeypatch.setattr(fb, "publish",
                        lambda message: pytest.fail("partial run published"))
    with pytest.raises(fb.HandoffError, match="fallback_commentary"):
        fb.run(now=NOW)
    assert not marker_path.exists()


def test_helper_receives_0600_temp_file(monkeypatch):
    observed = {}

    def fake_run(argv, **kwargs):
        assert argv[:2] == ["/usr/local/bin/lab-channel-post", "--text-file"]
        path = argv[2]
        observed["path"] = path
        observed["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        with open(path, encoding="utf-8") as fh:
            observed["text"] = fh.read()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fb.subprocess, "run", fake_run)
    assert fb.publish("four\nline\nmessage\nhere") is True
    assert observed["mode"] == 0o600
    assert observed["text"] == "four\nline\nmessage\nhere"
    assert not os.path.exists(observed["path"])


def test_units_schedule_hardening_and_prose_rules():
    deploy = os.path.join(_ROOT, "bot", "deploy")
    service_path = os.path.join(deploy, "rissaga-channel-fallback.service")
    timer_path = os.path.join(deploy, "rissaga-channel-fallback.timer")
    script_path = os.path.join(_ROOT, "bot", "rissaga_channel_fallback.py")
    skill_path = os.path.join(
        _ROOT, "ops", "hermes", "rissaga-desk-reads", "SKILL.md"
    )
    with open(service_path, encoding="utf-8") as fh:
        service = fh.read()
    with open(timer_path, encoding="utf-8") as fh:
        timer = fh.read()
    with open(script_path, encoding="utf-8") as fh:
        script = fh.read()
    with open(skill_path, encoding="utf-8") as fh:
        skill = fh.read()

    assert "User=hermes" in service and "Group=hermes" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=read-only" in service
    assert "PrivateTmp=true" in service
    assert "ReadWritePaths=-/home/hermes/.hermes/state" in service
    assert "/var/lib/rissaga/latest.json" in service
    assert "/usr/local/bin/lab-channel-post" in service
    assert "EnvironmentFile" not in service
    for hour in ("03", "09", "15", "21"):
        assert f"OnCalendar=*-*-* {hour}:15:00 UTC" in timer
    assert timer.count("OnCalendar=") == 4
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=0" in timer
    assert "one logical channel owner" in script
    assert "* CRYPTO:" in skill
    assert "flock -x 9" in skill
    for heading in (
        "WHAT HAPPENED", "WHY THIS DESK CARES", "LIVE DESK CHECK",
        "WHAT TO WATCH NEXT",
    ):
        assert heading in skill
    for text in (service, timer, script):
        assert "\u2014" not in text and "\u2013" not in text


def test_helper_refuses_overlong_html_instead_of_slicing_entities():
    helper = __import__("runpy").run_path(os.path.join(
        _ROOT, "bot", "deploy", "lab-channel-post"
    ))
    assert helper["message_with_footer"]("safe <b>read</b>").startswith("safe")
    with pytest.raises(ValueError, match="Telegram limit"):
        helper["message_with_footer"]("<b>" + "x" * 5000 + "</b>")
    assert helper["plain_text"]("<b>A &amp; B</b>") == "A & B"
