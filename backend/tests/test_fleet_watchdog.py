"""The watchdog's own failure modes.

The alarm is the last thing that notices a silent bot, so the ways it can go
quiet matter more than the ways a bot can: a config that does not parse, a
state file someone truncated, a probe that raises. Each of those used to take
the whole run down before save_state(), which meant no counter advanced and no
alert could ever fire again: the "alive but answering nothing" failure, in the
watcher itself.

Loaded by path because it ships as a standalone script to /opt on the box, not
as part of this package.
"""

import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).resolve().parents[2] / "ops" / "fleet-watchdog" / "watchdog.py"
_INSTALLER = _SRC.with_name("install.sh")


def _load():
    spec = importlib.util.spec_from_file_location("fleet_watchdog", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wd = _load()


# ---- config -----------------------------------------------------------------

_GOOD = {
    "default_alert_via": "alpha-bot",
    "bots": [
        {"unit": "alpha-bot", "env": "/etc/a.env", "var": "A", "alert_via": "beta-bot"},
        {"unit": "beta-bot", "env": "/etc/b.env", "var": "B",
         "state": "/var/lib/b/offset.json"},
    ],
    "mcp_remotes": [{"name": "mcp-a", "url": "https://example.test/mcp"}],
    "liquilens_rails": {
        "url": "https://api.liquilens.in/api/public-signals/rails",
        "alert_via": "beta-bot",
    },
}

_RUNNER_UNIT = "actions.runner.beepboop2025-LiquiLens.hetzner-cpx32.service"
_CHECKER_UNIT = "liquilens-runner-restart-debt.service"
_TIMER_UNIT = "liquilens-runner-restart-debt.timer"
_BOOT = "a906bbf5-4fc5-4ecd-8a3d-13042488c4ca"
_SOURCE_SHA = "a" * 40
_NOW = 1788336000


def _utc(epoch):
    return wd.datetime.fromtimestamp(epoch, wd.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _maintenance_entry(directory):
    return {
        "name": "liquilens-runner-restart-debt",
        "status_file": str(directory / "status.json"),
        "debt_file": str(directory / "restart-debt.json"),
        "service_unit": _CHECKER_UNIT,
        "timer_unit": _TIMER_UNIT,
        "monitored_unit": _RUNNER_UNIT,
        "max_age_seconds": 1200,
    }


def _clean_status(**changes):
    payload = {
        "schema": "liquilens-runner-maintenance-status.v1",
        "unit": _RUNNER_UNIT,
        "checked_at_utc": _utc(_NOW - 60),
        "checked_at_epoch": _NOW - 60,
        "result": "clean",
        "debt_state": "clear",
        "first_seen_utc": None,
        "first_seen_epoch": None,
        "deadline_utc": None,
        "deadline_epoch": None,
        "debt_age_seconds": None,
        "boot_id": _BOOT,
        "active_state": "active",
        "sub_state": "running",
        "active_enter_timestamp_monotonic": 4264372672776,
        "source_sha": _SOURCE_SHA,
        "reason": "no runner restart debt reported",
    }
    payload.update(changes)
    return payload


def _debt_status(*, first_seen=None, **changes):
    first_seen = _NOW - 3600 if first_seen is None else first_seen
    deadline = first_seen + wd.MAINTENANCE_DEBT_DEADLINE_S
    payload = _clean_status(
        result="debt",
        debt_state="overdue" if _NOW - 60 >= deadline else "pending",
        first_seen_utc=_utc(first_seen),
        first_seen_epoch=first_seen,
        deadline_utc=_utc(deadline),
        deadline_epoch=deadline,
        debt_age_seconds=(_NOW - 60) - first_seen,
    )
    payload.update(changes)
    return payload


def _debt_marker(status):
    return {
        "schema": "liquilens-runner-restart-debt.v1",
        "unit": status["unit"],
        "first_seen_utc": status["first_seen_utc"],
        "first_seen_epoch": status["first_seen_epoch"],
        "deadline_utc": status["deadline_utc"],
        "deadline_epoch": status["deadline_epoch"],
        "boot_id": status["boot_id"],
        "active_enter_timestamp_monotonic": status[
            "active_enter_timestamp_monotonic"
        ],
        "source_sha": status["source_sha"],
        "reason": "needrestart reported the exact runner unit",
    }


def _install_maintenance_fixture(tmp_path, monkeypatch, status=None, debt=None):
    directory = tmp_path / "runner-maintenance"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    status_path = directory / "status.json"
    status_path.write_text(json.dumps(status or _clean_status()))
    status_path.chmod(0o600)
    if debt is not None:
        debt_path = directory / "restart-debt.json"
        debt_path.write_text(json.dumps(debt))
        debt_path.chmod(0o600)

    probe = wd.MaintenanceProbe(
        "liquilens-runner-restart-debt",
        str(status_path),
        str(directory / "restart-debt.json"),
        _CHECKER_UNIT,
        _TIMER_UNIT,
        _RUNNER_UNIT,
        1200,
        "",
    )
    properties = {
        _TIMER_UNIT: {
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "waiting",
            "UnitFileState": "enabled",
        },
        _CHECKER_UNIT: {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "SubState": "dead",
            "Result": "success",
            "ExecMainStatus": "0",
        },
        _RUNNER_UNIT: {
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "ActiveEnterTimestampMonotonic": "4264372672776",
        },
    }
    monkeypatch.setattr(
        wd, "_systemd_properties",
        lambda unit, requested: properties.get(unit),
    )
    monkeypatch.setattr(wd, "_current_boot_id", lambda: _BOOT)
    return probe, properties, status_path


def _write(tmp_path, payload) -> str:
    p = tmp_path / "cfg.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return str(p)


def _heartbeat(tmp_path, monkeypatch, payload=b"nyx=1\n") -> Path:
    path = tmp_path / "mac.heartbeat"
    path.write_bytes(payload)
    monkeypatch.setattr(wd, "MAC_HEARTBEAT", str(path))
    return path


def test_config_round_trips(tmp_path):
    cfg = wd.load_config(_write(tmp_path, _GOOD))
    assert [b.unit for b in cfg.bots] == ["alpha-bot", "beta-bot"]
    assert cfg.bots[0].via == "beta-bot"
    assert cfg.bots[1].state == "/var/lib/b/offset.json"
    assert cfg.bots[0].state is None
    assert [r.name for r in cfg.remotes] == ["mcp-a"]
    assert cfg.rails.url.endswith("/api/public-signals/rails")
    assert cfg.rails.via == "beta-bot"


@pytest.mark.parametrize("payload", ["[]", "null", "not json at all", '{"bots": 3}'])
def test_malformed_config_yields_empty_not_an_exception(tmp_path, payload):
    cfg = wd.load_config(_write(tmp_path, payload))
    assert cfg.bots == [] and cfg.remotes == []


def test_one_bad_entry_does_not_drop_the_good_ones(tmp_path):
    bad = {"bots": [{"unit": "ok", "env": "/etc/ok.env", "var": "OK"},
                    {"unit": "no-env-path"},
                    "a string where an object belongs"]}
    cfg = wd.load_config(_write(tmp_path, bad))
    assert [b.unit for b in cfg.bots] == ["ok"]


def test_missing_config_file_is_survivable(tmp_path):
    cfg = wd.load_config(str(tmp_path / "absent.json"))
    assert cfg.bots == [] and cfg.remotes == []


@pytest.mark.parametrize("entry", [[], {}, {"url": ""}, "not an object"])
def test_malformed_rails_probe_is_not_enabled(tmp_path, entry):
    cfg = wd.load_config(_write(tmp_path, {"liquilens_rails": entry}))
    assert cfg.rails is None


def test_optional_maintenance_status_round_trips(tmp_path):
    maintenance_dir = tmp_path / "maintenance"
    raw = {**_GOOD, "maintenance_status": _maintenance_entry(maintenance_dir)}
    cfg = wd.load_config(_write(tmp_path, raw))

    assert cfg.config_problems == []
    assert cfg.maintenance.name == "liquilens-runner-restart-debt"
    assert cfg.maintenance.status_file == str(maintenance_dir / "status.json")
    assert cfg.maintenance.debt_file == str(
        maintenance_dir / "restart-debt.json"
    )
    assert cfg.maintenance.service_unit == _CHECKER_UNIT
    assert cfg.maintenance.timer_unit == _TIMER_UNIT
    assert cfg.maintenance.monitored_unit == _RUNNER_UNIT
    assert cfg.maintenance.max_age_seconds == 1200
    assert cfg.maintenance.via == ""


@pytest.mark.parametrize(
    "entry",
    [
        [],
        {},
        {"name": "secret value must not be echoed"},
        {**_maintenance_entry(Path("relative")), "max_age_seconds": True},
        {**_maintenance_entry(Path("/tmp/status")), "timer_unit": "not a timer"},
        {**_maintenance_entry(Path("/tmp/status")), "alert_via": False},
    ],
)
def test_malformed_maintenance_status_fails_closed_without_echoing_value(
    tmp_path, capsys, entry,
):
    cfg = wd.load_config(_write(tmp_path, {**_GOOD, "maintenance_status": entry}))

    assert cfg.maintenance is None
    assert cfg.config_problems == ["maintenance_status configuration is invalid"]
    output = capsys.readouterr().out
    assert "check will fail closed" in output
    assert "secret value" not in output


def test_malformed_sole_maintenance_opt_in_reaches_the_synthetic_alarm(
    tmp_path, monkeypatch,
):
    sent = []
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "maintenance_status": {"name": "invalid"},
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(wd, "CONSECUTIVE", 1)
    monkeypatch.setattr(
        wd, "notify",
        lambda cfg, via, text: sent.append((via, text)) or True,
    )

    assert wd.main() == 0
    assert any(
        "watchdog-config" in text and "maintenance_status" in text
        for _, text in sent
    )


def test_alert_never_routes_through_the_thing_being_reported(tmp_path):
    cfg = wd.load_config(_write(tmp_path, _GOOD))
    # configured value wins
    assert wd.pick_via("beta-bot", "alpha-bot", cfg) == "alpha-bot"
    # a self-referential config entry is ignored, not obeyed
    assert wd.pick_via("alpha-bot", "alpha-bot", cfg) == "beta-bot"
    # and so is a self-referential default
    assert wd.pick_via("alpha-bot", "", cfg) == "beta-bot"
    # anything else falls back to the default sender
    assert wd.pick_via("mcp-a", "", cfg) == "alpha-bot"


def test_read_env_survives_a_json_config_of_the_wrong_shape(tmp_path):
    p = tmp_path / "bot.json"
    p.write_text("[1, 2, 3]")
    assert wd.read_env(str(p), "bot_token") == ""


def test_read_env_reads_both_formats(tmp_path):
    env = tmp_path / "bot.env"
    env.write_text("# comment\nOTHER=x\nTOK='secret'\n")
    assert wd.read_env(str(env), "TOK") == "secret"
    js = tmp_path / "bot.json"
    js.write_text(json.dumps({"bot_token": "secret"}))
    assert wd.read_env(str(js), "bot_token") == "secret"


# ---- state ------------------------------------------------------------------

@pytest.mark.parametrize("payload", ["[]", "null", "3", "{oops"])
def test_load_state_returns_a_dict_whatever_the_file_says(tmp_path, monkeypatch, payload):
    p = tmp_path / "state.json"
    p.write_text(payload)
    monkeypatch.setattr(wd, "STATE_PATH", str(p))
    assert wd.load_state() == {}


# ---- probe verdicts ---------------------------------------------------------

_URL = "https://example.test/mcp"


def _init_ok(extra=None):
    return json.dumps({"jsonrpc": "2.0", "id": 1,
                       "result": {"protocolVersion": "2025-06-18",
                                  "serverInfo": {"name": "x", "version": "1"},
                                  **(extra or {})}})


def test_a_real_initialize_reply_is_healthy():
    assert wd._verdict(_URL, _init_ok()) == []


def test_an_sse_framed_reply_is_healthy():
    assert wd._verdict(_URL, f"event: message\ndata: {_init_ok()}\n\n") == []


def test_a_jsonrpc_error_still_proves_an_mcp_server_answered():
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "error": {"code": -32600, "message": "bad request"}})
    assert wd._verdict(_URL, body) == []


def test_an_echo_of_the_probe_is_not_proof_of_health():
    """A substring scan passed this: the probe's own body carries 'jsonrpc'."""
    assert wd._verdict(_URL, json.dumps(wd.MCP_INIT)) != []


def test_a_result_without_serverinfo_is_not_an_mcp_server():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}})
    assert wd._verdict(_URL, body) != []


def test_an_error_message_containing_the_word_result_is_not_healthy():
    """The old scan asked for '\"error\"' without '\"result\"' anywhere in the
    text, so an error whose message used the word was read as a success."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "error": {"code": -32000,
                                 "message": 'no "result" could be produced'}})
    assert wd._verdict(_URL, body) == []  # parsed: an error envelope, server up
    plain = 'upstream said: no "result" could be produced (jsonrpc)'
    assert wd._verdict(_URL, plain) != []  # unparseable: reported, not waved through


def test_html_from_a_misroute_is_reported():
    problems = wd._verdict(_URL, "<html><body>welcome to nginx</body></html>")
    assert problems and "wrong service" in problems[0]


# ---- LiquiLens rails freshness ----------------------------------------------

_RAILS = wd.RailsProbe(
    "https://api.liquilens.in/api/public-signals/rails", "alpha-bot")
_RAILS_TODAY = wd.date(2026, 8, 27)


class _JSONResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.read_size = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        self.read_size = size
        return self.payload[:size]


def _rails_problems(monkeypatch, payload):
    monkeypatch.setattr(wd, "_fetch_json_object",
                        lambda url: (payload, None))
    return wd.check_liquilens_rails(_RAILS, _RAILS_TODAY)


def test_rails_http_failure_is_unhealthy(monkeypatch):
    def unavailable(request, timeout):
        raise wd.urllib.error.HTTPError(
            request.full_url, 503, "unavailable", {}, None)

    monkeypatch.setattr(wd._OPENER, "open", unavailable)
    assert wd.check_liquilens_rails(_RAILS, _RAILS_TODAY) == [
        "HTTP 503 from rails endpoint"]


def test_rails_fetch_is_a_bounded_read_only_json_get(monkeypatch):
    response = _JSONResponse(json.dumps({
        "available": True, "stale": False, "as_of": "2026-08-27",
    }).encode())
    captured = {}

    def open_probe(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(wd._OPENER, "open", open_probe)

    assert wd.check_liquilens_rails(_RAILS, _RAILS_TODAY) == []
    assert captured["request"].get_method() == "GET"
    assert captured["request"].data is None
    assert captured["request"].get_header("Accept") == "application/json"
    assert captured["timeout"] == wd.TIMEOUT
    assert response.read_size == wd.RAILS_READ_MAX + 1


@pytest.mark.parametrize(("body", "problem"), [
    (b"[]", "not an object"),
    (b"not json", "not valid UTF-8 JSON"),
])
def test_rails_transport_rejects_wrong_json_shape(monkeypatch, body, problem):
    monkeypatch.setattr(wd._OPENER, "open",
                        lambda request, timeout: _JSONResponse(body))
    assert problem in wd.check_liquilens_rails(_RAILS, _RAILS_TODAY)[0]


@pytest.mark.parametrize(("payload", "problem"), [
    ({"available": False, "stale": False, "as_of": "2026-08-27"},
     "available=false"),
    ({"available": True, "stale": True, "as_of": "2026-08-27"},
     "stale=true"),
    ({"available": "yes", "stale": False, "as_of": "2026-08-27"},
     "available is missing or not boolean"),
    ({"available": True, "stale": "no", "as_of": "2026-08-27"},
     "stale is missing or not boolean"),
    ({"available": True, "stale": False},
     "as_of is missing or invalid"),
    ({"available": True, "stale": False, "as_of": "20260827"},
     "as_of is missing or invalid"),
])
def test_rails_status_and_shape_fail_closed(monkeypatch, payload, problem):
    assert any(problem in item for item in _rails_problems(monkeypatch, payload))


def test_rails_future_as_of_is_unhealthy(monkeypatch):
    problems = _rails_problems(monkeypatch, {
        "available": True, "stale": False, "as_of": "2026-08-28",
    })
    assert len(problems) == 1
    assert "future" in problems[0] and "UTC day 2026-08-27" in problems[0]


@pytest.mark.parametrize(("as_of", "healthy"), [
    ("2026-08-27", True),
    ("2026-08-26", True),
    ("2026-08-25", False),
    ("2026-08-20", False),
])
def test_rails_warns_at_two_utc_days_not_after_public_hold(
        monkeypatch, as_of, healthy):
    problems = _rails_problems(monkeypatch, {
        "available": True, "stale": False, "as_of": as_of,
    })
    assert (problems == []) is healthy
    if not healthy:
        assert "age_days=" in problems[0]


# ---- runner maintenance debt ------------------------------------------------

def test_maintenance_accepts_expected_inactive_successful_oneshot(
    tmp_path, monkeypatch,
):
    probe, _, _ = _install_maintenance_fixture(tmp_path, monkeypatch)
    assert wd.check_maintenance_status(probe, _NOW) == []


def test_maintenance_status_contract_allows_bounded_forward_metadata(
    tmp_path, monkeypatch,
):
    status = _clean_status(producer_metadata={"revision": 1})
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status,
    )
    assert wd.check_maintenance_status(probe, _NOW) == []


def test_maintenance_tolerates_checker_currently_activating(
    tmp_path, monkeypatch,
):
    probe, properties, _ = _install_maintenance_fixture(tmp_path, monkeypatch)
    properties[_CHECKER_UNIT].update({
        "ActiveState": "activating",
        "SubState": "start",
        "Result": "exit-code",
        "ExecMainStatus": "1",
    })
    assert wd.check_maintenance_status(probe, _NOW) == []


@pytest.mark.parametrize(
    ("unit", "changes", "problem"),
    [
        (_TIMER_UNIT, {"UnitFileState": "disabled"}, "timer"),
        (_TIMER_UNIT, {"ActiveState": "inactive"}, "timer"),
        (
            _CHECKER_UNIT,
            {"ActiveState": "failed", "SubState": "failed", "Result": "exit-code"},
            "last execution",
        ),
        (_RUNNER_UNIT, {"ActiveState": "inactive"}, "runner is not active"),
    ],
)
def test_maintenance_fails_closed_on_unit_states(
    tmp_path, monkeypatch, unit, changes, problem,
):
    probe, properties, _ = _install_maintenance_fixture(tmp_path, monkeypatch)
    properties[unit].update(changes)
    assert any(
        problem in item for item in wd.check_maintenance_status(probe, _NOW)
    )


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"unit": "wrong.service"}, "identity or types"),
        ({"source_sha": "not-a-sha"}, "identity or types"),
        ({"result": []}, "identity or types"),
        ({"debt_state": []}, "identity or types"),
        (
            {
                "checked_at_utc": _utc(_NOW + 60),
                "checked_at_epoch": _NOW + 60,
            },
            "inconsistent",
        ),
        (
            {
                "checked_at_utc": _utc(_NOW - 1201),
                "checked_at_epoch": _NOW - 1201,
            },
            "stale",
        ),
        ({"debt_state": "pending"}, "carries debt metadata"),
        ({"reason": "x" * (wd.MAINTENANCE_REASON_MAX + 1)}, "reason"),
    ],
)
def test_maintenance_status_rejects_bad_identity_clocks_and_shape(
    tmp_path, monkeypatch, changes, problem,
):
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=_clean_status(**changes),
    )
    assert any(
        problem in item for item in wd.check_maintenance_status(probe, _NOW)
    )


def test_maintenance_status_age_budget_is_exactly_twenty_minutes(
    tmp_path, monkeypatch,
):
    at_limit = _clean_status(
        checked_at_utc=_utc(_NOW - 1200),
        checked_at_epoch=_NOW - 1200,
    )
    probe, _, status_path = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=at_limit,
    )
    assert probe.max_age_seconds == 1200
    assert wd.check_maintenance_status(probe, _NOW) == []

    status_path.write_text(json.dumps(_clean_status(
        checked_at_utc=_utc(_NOW - 1201),
        checked_at_epoch=_NOW - 1201,
    )))
    status_path.chmod(0o600)
    assert "maintenance checker status is stale" in wd.check_maintenance_status(
        probe, _NOW,
    )


@pytest.mark.parametrize("missing", ["debt_age_seconds", "reason"])
def test_maintenance_status_requires_every_v1_core_field(
    tmp_path, monkeypatch, missing,
):
    status = _clean_status()
    status.pop(missing)
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status,
    )
    assert any(
        "missing required fields" in item
        for item in wd.check_maintenance_status(probe, _NOW)
    )


def test_error_status_allows_unavailable_provenance_without_going_green(
    tmp_path, monkeypatch,
):
    status = _clean_status(
        result="error",
        debt_state="error",
        boot_id=None,
        active_state=None,
        sub_state=None,
        active_enter_timestamp_monotonic=None,
        source_sha=None,
        reason="system evidence unavailable",
    )
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status,
    )
    assert wd.check_maintenance_status(probe, _NOW) == [
        "maintenance checker reported a scan error"
    ]


def test_maintenance_status_is_bound_to_current_boot_and_runner_generation(
    tmp_path, monkeypatch,
):
    probe, properties, _ = _install_maintenance_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        wd, "_current_boot_id",
        lambda: "00000000-0000-0000-0000-000000000000",
    )
    properties[_RUNNER_UNIT]["ActiveEnterTimestampMonotonic"] = "4264372672777"
    problems = wd.check_maintenance_status(probe, _NOW)
    assert any("previous boot" in problem for problem in problems)
    assert any("runner generation" in problem for problem in problems)


@pytest.mark.parametrize(
    ("first_seen", "expected"),
    [
        (_NOW - 3600, "pending"),
        (_NOW - 90000, "overdue"),
    ],
)
def test_active_restart_debt_is_unhealthy_until_safely_resolved(
    tmp_path, monkeypatch, first_seen, expected,
):
    status = _debt_status(first_seen=first_seen)
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status, debt=_debt_marker(status),
    )
    problems = wd.check_maintenance_status(probe, _NOW)
    assert any(f"debt is {expected}" in problem for problem in problems)


def test_pending_debt_allows_current_identity_to_advance_past_marker(
    tmp_path, monkeypatch,
):
    status = _debt_status()
    marker = _debt_marker(status)
    marker.update({
        "boot_id": "00000000-0000-0000-0000-000000000001",
        "active_enter_timestamp_monotonic": 17,
        "source_sha": "b" * 40,
    })
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status, debt=marker,
    )
    problems = wd.check_maintenance_status(probe, _NOW)
    assert problems == ["runner restart maintenance debt is pending"]


def test_restart_debt_deadline_reserves_the_full_scheduler_window(
    tmp_path, monkeypatch,
):
    assert wd.MAINTENANCE_DEBT_DEADLINE_S == 85_440
    status = _debt_status()
    marker = _debt_marker(status)
    marker["deadline_epoch"] = marker["first_seen_epoch"] + 86_400
    marker["deadline_utc"] = _utc(marker["deadline_epoch"])
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status, debt=marker,
    )
    assert any(
        "marker clocks are inconsistent" in item
        for item in wd.check_maintenance_status(probe, _NOW)
    )


@pytest.mark.parametrize("age", [None, 0, 3601])
def test_debt_status_requires_exact_nonnegative_debt_age(
    tmp_path, monkeypatch, age,
):
    status = _debt_status(debt_age_seconds=age)
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status, debt=_debt_marker(status),
    )
    assert any(
        "debt status age is invalid" in item
        for item in wd.check_maintenance_status(probe, _NOW)
    )


def test_error_status_preserves_valid_marker_age_and_stays_unhealthy(
    tmp_path, monkeypatch,
):
    status = _debt_status(
        result="error",
        debt_state="error",
        reason="needrestart scan failed",
    )
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status, debt=_debt_marker(status),
    )
    problems = wd.check_maintenance_status(probe, _NOW)
    assert "maintenance checker reported a scan error" in problems
    assert "runner restart maintenance debt is pending" in problems


def test_error_status_rejects_an_invented_or_inconsistent_debt_age(
    tmp_path, monkeypatch,
):
    status = _debt_status(
        result="error",
        debt_state="error",
        debt_age_seconds=12,
        reason="needrestart scan failed",
    )
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status, debt=_debt_marker(status),
    )
    assert any(
        "error status age is invalid" in item
        for item in wd.check_maintenance_status(probe, _NOW)
    )


def test_debt_status_without_marker_and_clean_status_with_marker_fail_closed(
    tmp_path, monkeypatch,
):
    status = _debt_status()
    probe, _, status_path = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status,
    )
    assert any(
        "without a valid marker" in item
        for item in wd.check_maintenance_status(probe, _NOW)
    )

    status_path.write_text(json.dumps(_clean_status()))
    status_path.chmod(0o600)
    debt_path = Path(probe.debt_file)
    debt_path.write_text(json.dumps(_debt_marker(status)))
    debt_path.chmod(0o600)
    assert any(
        "clean while a debt marker remains" in item
        for item in wd.check_maintenance_status(probe, _NOW)
    )


def test_checker_error_status_is_unhealthy_without_disclosing_reason(
    tmp_path, monkeypatch,
):
    status = _clean_status(result="error", debt_state="error", reason="private detail")
    probe, _, _ = _install_maintenance_fixture(
        tmp_path, monkeypatch, status=status,
    )
    problems = wd.check_maintenance_status(probe, _NOW)
    assert any("scan error" in item for item in problems)
    assert all("private detail" not in item for item in problems)


@pytest.mark.parametrize(
    "unsafe",
    ["mode", "hardlink", "symlink", "fifo", "oversize", "owner"],
)
def test_maintenance_status_rejects_unsafe_files(
    tmp_path, monkeypatch, unsafe,
):
    probe, _, status_path = _install_maintenance_fixture(tmp_path, monkeypatch)
    if unsafe == "mode":
        status_path.chmod(0o644)
    elif unsafe == "hardlink":
        os.link(status_path, status_path.with_name("second-link.json"))
    elif unsafe == "symlink":
        target = tmp_path / "forged.json"
        target.write_text(json.dumps(_clean_status()))
        status_path.unlink()
        status_path.symlink_to(target)
    elif unsafe == "fifo":
        status_path.unlink()
        os.mkfifo(status_path, mode=0o600)
    elif unsafe == "oversize":
        status_path.write_bytes(b"{" + b" " * wd.MAINTENANCE_READ_MAX + b"}")
        status_path.chmod(0o600)
    else:
        original_fstat = wd.os.fstat

        def wrong_file_owner(fd):
            info = original_fstat(fd)
            if stat.S_ISREG(info.st_mode):
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_uid=info.st_uid + 1,
                    st_nlink=info.st_nlink,
                    st_size=info.st_size,
                )
            return info

        monkeypatch.setattr(wd.os, "fstat", wrong_file_owner)

    assert any(
        token in item
        for item in wd.check_maintenance_status(probe, _NOW)
        for token in ("unsafe", "size limit")
    )


def test_systemd_probe_uses_argv_without_a_shell(monkeypatch):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout="LoadState=loaded\nActiveState=active\n",
        )

    monkeypatch.setattr(wd.subprocess, "run", run)
    assert wd._systemd_properties(
        _RUNNER_UNIT, ("LoadState", "ActiveState"),
    ) == {"LoadState": "loaded", "ActiveState": "active"}
    assert captured["command"][0:3] == ["systemctl", "show", _RUNNER_UNIT]
    assert "shell" not in captured["kwargs"]


def test_watchdog_installer_shell_is_valid():
    subprocess.run(["bash", "-n", str(_INSTALLER)], check=True)


def test_watchdog_installer_preserves_current_private_config_transactionally():
    installer = _INSTALLER.read_text()

    for required in (
        'https://github.com/beepboop2025/seiche.git',
        'rev-parse --verify HEAD',
        'status --porcelain=v1 --untracked-files=all',
        'GIT_NO_REPLACE_OBJECTS=1 git -C "$REPO_ROOT" show',
        '"$SOURCE_SHA:ops/fleet-watchdog/watchdog.py" >"$SOURCE_CANDIDATE"',
        'cmp -s -- "$SOURCE_SCRIPT" "$SOURCE_CANDIDATE"',
        'LIVE_CONFIG=/etc/fleet-watchdog.json',
        'CONFIG_BEFORE_SHA=$(sha256sum "$LIVE_CONFIG"',
        'hashlib.sha256(original_bytes).hexdigest() != expected_sha',
        'live watchdog config changed during candidate derivation',
        'live watchdog config changed during preflight',
        'copy.deepcopy(original)',
        'without_probe.pop("maintenance_status")',
        'original_without_probe.pop("maintenance_status", None)',
        'without_probe != original_without_probe',
        'has_existing = "maintenance_status" in original',
        'if has_existing and existing != desired:',
        'existing maintenance_status differs; refusing replacement',
        'action = "added"',
        'with contextlib.redirect_stdout(diagnostics):',
        'candidate watchdog configuration emitted diagnostics',
        'case "$CONFIG_ACTION" in added|unchanged)',
        '"max_age_seconds": 1200',
        'SOURCE_CANDIDATE="$WORK_DIR/watchdog.py"',
        'SCRIPT_CANDIDATE_SHA=$(sha256sum "$SOURCE_CANDIDATE"',
        'install -o root -g root -m 0750 "$SOURCE_CANDIDATE" "$SCRIPT_STAGE"',
        'mv -fT -- "$CONFIG_STAGE" "$LIVE_CONFIG"',
        '"preservation": "deep_equal_addition_only"',
        '"previous_sha256": "$CONFIG_BEFORE_SHA"',
        '"installed_sha256": "$CONFIG_CANDIDATE_SHA"',
        'previous-config.json',
    ):
        assert required in installer
    assert "rollback-config.json" not in installer
    assert 'cat "$LIVE_CONFIG"' not in installer
    assert "set -x" not in installer


def test_watchdog_installer_never_masks_an_incomplete_rollback():
    installer = _INSTALLER.read_text()

    assert "rollback || true" not in installer
    assert "watchdog rollback incomplete; timer remains quiesced" in installer
    assert "watchdog timer deliberately left quiesced" in installer
    assert "recovery bytes retained at $RELEASE_STAGE" in installer
    assert 'PRESERVE_RELEASE=true' in installer
    assert 'if ! rollback; then' in installer
    assert 'if ! restore_timer; then' in installer


def test_watchdog_installer_quiesces_reader_and_restores_exact_timer_state():
    installer = _INSTALLER.read_text()

    assert 'flock -n 9' in installer
    assert 'systemctl is-active "$WATCHDOG_SERVICE"' in installer
    quiesce = installer.index('if [ "$TIMER_WAS_ACTIVE" = active ]; then')
    stop = installer.index('systemctl stop "$WATCHDOG_TIMER"', quiesce)
    assert stop < installer.index(
        'mv -fT -- "$SCRIPT_STAGE" "$LIVE_SCRIPT"'
    )
    assert stop < installer.index(
        'mv -fT -- "$CONFIG_STAGE" "$LIVE_CONFIG"'
    )
    receipt_seal = installer.rindex('mv -T -- "$RELEASE_STAGE" "$RELEASE_DIR"')
    timer_restore = installer.rindex("restore_timer")
    commit_marker = installer.rindex('"fleet-watchdog-release-commit.v1"')
    assert receipt_seal < timer_restore
    assert timer_restore < commit_marker
    assert 'TIMER_WAS_ENABLED' in installer
    assert 'TIMER_WAS_ACTIVE' in installer
    assert 'restore_timer' in installer
    assert installer.count("assert_live_metadata") >= 4
    assert "live watchdog bytes changed before release commit" in installer
    assert "sys.dont_write_bytecode = True" in installer
    assert 'fleet-watchdog-release.v2' in installer
    assert '"transaction_state": "prepared_timer_restore_pending"' in installer
    assert '"verification": "live_bytes_verified_and_timer_state_restored"' in installer
    assert 'needrestart' not in installer


# ---- bounded read -----------------------------------------------------------

class _Trickle:
    """An SSE stream that answers, then keeps the connection open forever."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.reads = 0

    def read1(self, _n):
        self.reads += 1
        if self.frames:
            return self.frames.pop(0)
        raise AssertionError("read past the answer on a stream held open")

    read = read1


def test_reading_stops_at_the_first_complete_message():
    stream = _Trickle([b"event: message\n", f"data: {_init_ok()}\n\n".encode()])
    assert wd._verdict(_URL, wd._read_bounded(stream)) == []
    assert stream.reads == 2


def test_reading_gives_up_rather_than_hanging_on_a_keepalive_stream(monkeypatch):
    monkeypatch.setattr(wd, "MCP_READ_DEADLINE_S", 0.05)

    class _Keepalive:
        def read1(self, _n):
            return b": keepalive\n"

    assert "jsonrpc" not in wd._read_bounded(_Keepalive())


# ---- the run itself ---------------------------------------------------------

def test_a_raising_probe_costs_one_check_not_the_run():
    def boom():
        raise RuntimeError("kaboom")

    problems = wd.guarded("alpha-bot", boom)
    assert problems and "RuntimeError" in problems[0]


# ---- NYX heartbeat ----------------------------------------------------------

@pytest.mark.parametrize("payload", [b"nyx=1", b"nyx=1\n"])
def test_nyx_requires_one_exact_healthy_line(monkeypatch, tmp_path, payload):
    _heartbeat(tmp_path, monkeypatch, payload)
    assert wd.check_mac_heartbeat() == []


def test_a_fresh_nyx_zero_is_unhealthy(monkeypatch, tmp_path):
    _heartbeat(tmp_path, monkeypatch, b"nyx=0\n")
    problems = wd.check_mac_heartbeat()
    assert len(problems) == 1
    assert "nyx=0" in problems[0]


@pytest.mark.parametrize("payload", [
    b"", b"nyx=1 ", b"NYX=1\n", b"nyx=1\r\n", b"nyx=1\nextra\n",
    b"nyx=2\n", b"x" * (wd.MAC_HEARTBEAT_MAX_BYTES + 1),
])
def test_malformed_nyx_heartbeats_fail_loud(monkeypatch, tmp_path, payload):
    _heartbeat(tmp_path, monkeypatch, payload)
    problems = wd.check_mac_heartbeat()
    assert len(problems) == 1
    assert "malformed" in problems[0]


def test_nyx_one_still_has_to_be_fresh(monkeypatch, tmp_path):
    path = _heartbeat(tmp_path, monkeypatch)
    now = 10_000.0
    stale_mtime = now - wd.MAC_STALE_S - 1
    os.utime(path, (stale_mtime, stale_mtime))

    problems = wd.check_mac_heartbeat(now)
    assert len(problems) == 1
    assert "no Mac check-in" in problems[0]


def test_missing_nyx_heartbeat_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(wd, "MAC_HEARTBEAT", str(tmp_path / "absent"))
    problems = wd.check_mac_heartbeat()
    assert len(problems) == 1
    assert "missing" in problems[0]


def test_symlinked_nyx_heartbeat_is_not_trusted(monkeypatch, tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"nyx=1\n")
    link = tmp_path / "mac.heartbeat"
    link.symlink_to(target)
    monkeypatch.setattr(wd, "MAC_HEARTBEAT", str(link))

    problems = wd.check_mac_heartbeat()
    assert len(problems) == 1
    assert "unsafe" in problems[0]


def test_fifo_nyx_heartbeat_is_rejected_without_blocking(monkeypatch, tmp_path):
    fifo = tmp_path / "mac.heartbeat"
    os.mkfifo(fifo)
    monkeypatch.setattr(wd, "MAC_HEARTBEAT", str(fifo))

    problems = wd.check_mac_heartbeat()
    assert len(problems) == 1
    assert "not a regular file" in problems[0]


def test_writable_by_another_account_is_not_trusted(monkeypatch, tmp_path):
    path = _heartbeat(tmp_path, monkeypatch)
    path.chmod(0o666)
    problems = wd.check_mac_heartbeat()
    assert len(problems) == 1
    assert "unsafe" in problems[0]


def test_heartbeat_owned_by_another_account_is_rejected(monkeypatch, tmp_path):
    path = _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(wd.os, "geteuid", lambda: path.stat().st_uid + 1)

    problems = wd.check_mac_heartbeat()

    assert len(problems) == 1
    assert "not owned by the watchdog account" in problems[0]


def test_hard_linked_heartbeat_is_rejected(monkeypatch, tmp_path):
    path = _heartbeat(tmp_path, monkeypatch)
    alias = tmp_path / "mac.heartbeat.alias"
    os.link(path, alias)

    problems = wd.check_mac_heartbeat()

    assert len(problems) == 1
    assert "has multiple hard links" in problems[0]


def test_unreadable_nyx_heartbeat_fails_loud(monkeypatch, tmp_path):
    path = _heartbeat(tmp_path, monkeypatch)
    real_open = wd.os.open

    def deny_heartbeat(candidate, flags):
        if candidate == str(path):
            raise PermissionError("denied for test")
        return real_open(candidate, flags)

    monkeypatch.setattr(wd.os, "open", deny_heartbeat)
    problems = wd.check_mac_heartbeat()
    assert len(problems) == 1
    assert "unreadable" in problems[0]


def test_nyx_payload_failure_uses_one_debounced_alarm(monkeypatch, tmp_path):
    sent = []
    heartbeat = _heartbeat(tmp_path, monkeypatch, b"nyx=0\n")
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "default_alert_via": "alpha-bot",
        "bots": [{"unit": "alpha-bot", "env": str(tmp_path / "a.env"),
                  "var": "A"}],
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(wd, "check", lambda bot: [])
    monkeypatch.setattr(wd, "notify",
                        lambda cfg, via, text: sent.append(text) or True)
    monkeypatch.setattr(wd, "CONSECUTIVE", 1)

    assert wd.main() == 0
    assert wd.main() == 0
    assert len(sent) == 1 and sent[0].startswith("🔴 mac-bots:")

    heartbeat.write_bytes(b"nyx=1\n")
    assert wd.main() == 0
    assert wd.main() == 0
    assert len(sent) == 2 and sent[-1] == "🟢 mac-bots: recovered"


def test_no_owner_chat_exits_non_zero(monkeypatch):
    monkeypatch.setattr(wd, "OWNER_CHAT", "")
    assert wd.main() == 1


def test_nothing_configured_exits_non_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", str(tmp_path / "absent.json"))
    assert wd.main() == 1


def test_every_remote_unreachable_is_one_alarm_not_n(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "default_alert_via": "alpha-bot",
        "bots": [{"unit": "alpha-bot", "env": str(tmp_path / "a.env"), "var": "A"}],
        "mcp_remotes": [{"name": f"mcp-{i}", "url": f"https://{i}.test/mcp"}
                        for i in range(6)],
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(wd, "check", lambda bot: [])
    monkeypatch.setattr(wd, "check_mcp", lambda url: ["unreachable (gaierror)"])
    monkeypatch.setattr(wd, "notify", lambda cfg, via, text: sent.append(text) or True)
    monkeypatch.setattr(wd, "CONSECUTIVE", 1)

    assert wd.main() == 0
    assert len(sent) == 1
    assert "all 6 remotes unreachable" in sent[0]


def test_a_single_remote_outage_still_names_that_remote(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "default_alert_via": "alpha-bot",
        "bots": [{"unit": "alpha-bot", "env": str(tmp_path / "a.env"), "var": "A"}],
        "mcp_remotes": [{"name": "mcp-a", "url": "https://a.test/mcp"},
                        {"name": "mcp-b", "url": "https://b.test/mcp"}],
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(wd, "check", lambda bot: [])
    monkeypatch.setattr(wd, "check_mcp",
                        lambda url: ["HTTP 502 on initialize"] if "a.test" in url else [])
    monkeypatch.setattr(wd, "notify", lambda cfg, via, text: sent.append(text) or True)
    monkeypatch.setattr(wd, "CONSECUTIVE", 1)

    assert wd.main() == 0
    assert len(sent) == 1 and "mcp-a" in sent[0]


def test_rails_probe_uses_shared_debounce_and_recovery(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "default_alert_via": "alpha-bot",
        "bots": [{"unit": "alpha-bot", "env": str(tmp_path / "a.env"),
                  "var": "A"}],
        "liquilens_rails": {
            "url": "https://api.liquilens.in/api/public-signals/rails",
        },
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(wd, "check", lambda bot: [])
    monkeypatch.setattr(wd, "notify",
                        lambda cfg, via, text: sent.append((via, text)) or True)
    results = iter([
        ["rails evidence age_days=2"],
        ["rails evidence age_days=2"],
        [],
    ])
    monkeypatch.setattr(wd, "check_liquilens_rails",
                        lambda probe, current_day: next(results))

    assert wd.main() == 0
    assert sent == []  # first bad run is debounced
    assert wd.main() == 0
    assert sent == [("alpha-bot",
                     "🔴 liquilens-rails: rails evidence age_days=2")]
    assert wd.main() == 0
    assert sent[-1] == ("alpha-bot", "🟢 liquilens-rails: recovered")

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["liquilens-rails"]["fails"] == 0


def test_maintenance_probe_uses_shared_debounce_and_recovery(
    monkeypatch, tmp_path,
):
    sent = []
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "default_alert_via": "alpha-bot",
        "bots": [{
            "unit": "alpha-bot",
            "env": str(tmp_path / "a.env"),
            "var": "A",
        }],
        "maintenance_status": _maintenance_entry(tmp_path / "maintenance"),
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(wd, "check", lambda bot: [])
    monkeypatch.setattr(
        wd, "notify",
        lambda cfg, via, text: sent.append((via, text)) or True,
    )
    results = iter([
        ["runner restart maintenance debt is pending"],
        ["runner restart maintenance debt is pending"],
        [],
    ])
    monkeypatch.setattr(
        wd, "check_maintenance_status",
        lambda probe, now: next(results),
    )

    assert wd.main() == 0
    assert sent == []
    assert wd.main() == 0
    assert sent == [(
        "alpha-bot",
        (
            "🔴 liquilens-runner-restart-debt: "
            "runner restart maintenance debt is pending"
        ),
    )]
    assert wd.main() == 0
    assert sent[-1] == (
        "alpha-bot", "🟢 liquilens-runner-restart-debt: recovered",
    )

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["liquilens-runner-restart-debt"]["fails"] == 0


def test_a_corrupt_state_entry_does_not_stop_the_run(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"alpha-bot": "this used to be a dict"}))
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "bots": [{"unit": "alpha-bot", "env": str(tmp_path / "a.env"), "var": "A"}],
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(state))
    _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(wd, "check", lambda bot: [])

    assert wd.main() == 0
    assert isinstance(json.loads(state.read_text())["alpha-bot"], dict)


def test_the_collapsed_remote_alarm_can_say_it_is_over(monkeypatch, tmp_path):
    """A collapsed alarm that is only ever appended when it fires can never
    recover: the synthetic name is absent from checks on a healthy run, so its
    counter keeps the value the outage left and the next outage pages on its
    first run instead of waiting out a blip."""
    sent = []
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "default_alert_via": "a-bot",
        "bots": [{"unit": "a-bot", "env": str(tmp_path / "a.env"),
                  "var": "T"}],
        "mcp_remotes": [{"name": "m1", "url": "https://x.test/mcp"},
                        {"name": "m2", "url": "https://y.test/mcp"}],
    }))
    (tmp_path / "a.env").write_text("T=1\n")
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(wd, "check", lambda bot: [])
    monkeypatch.setattr(wd, "notify",
                        lambda cfg, via, text: sent.append(text) or True)
    monkeypatch.setattr(wd, "CONSECUTIVE", 1)

    monkeypatch.setattr(wd, "check_mcp", lambda url: ["unreachable (gaierror)"])
    wd.main()
    assert any("unreachable" in t for t in sent), sent

    sent.clear()
    monkeypatch.setattr(wd, "check_mcp", lambda url: [])
    wd.main()
    assert any("recovered" in t.lower() for t in sent), \
        f"the collapsed alarm never said it was over: {sent}"

    # And the counter really is back to zero, so a later outage waits again.
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["mcp"]["fails"] == 0, state["mcp"]
