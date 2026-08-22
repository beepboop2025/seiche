"""Fail-closed Telegram projection of the China macro evidence catalog."""

import copy
import os
import sys

import pytest

_BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BOT_DIR)
sys.path.insert(0, _BOT_DIR)
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

import seiche_bot as bot  # noqa: E402
from seiche.markets.world import project_world_markets  # noqa: E402


_REVIEWED_LABELS = {
    "CN.NBS.CPI_INDEX": ("Consumer Price Index (The same month last year=100)"),
    "CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY": (
        "Value-added of Industrial Enterprises above Designated Size, "
        "Growth Rate (The same period last year=100)(%)"
    ),
    "CN.NBS.MANUFACTURING_PMI": "Manufacturing Purchasing Managers' Index (%)",
    "CN.NBS.PPI_INDEX": (
        "Producer Price Index for Industrial Products (The same month last year=100)"
    ),
}


def _series(series_id: str) -> dict:
    return {
        "series_id": series_id,
        "catalogid": "catalog-id",
        "catalog_label": "Catalog label",
        "row_id": "row-id",
        "i": "indicator-id",
        "ek": "export-key",
        "ek_dp": "export-key-dimension",
        "dp": "1",
        "dp_name": "dimension",
        "label": _REVIEWED_LABELS[series_id],
        "reference_release_url": "https://www.stats.gov.cn/release",
        "release_url": "https://www.stats.gov.cn/release",
        "source_unit_label_exact": "%",
        "source_unit_semantically_authoritative": True,
        "semantic_contract": {
            "value_kind": "index_level",
            "canonical_unit": "index_points",
            "comparison_base": None,
            "transform": None,
            "threshold": None,
        },
        "value_publication": "withheld_pending_rights_review",
    }


def _payload(*, available: bool = True) -> dict:
    status = "restricted" if available else "structural"
    china = {
        "status": status,
        "evidence_status": "restricted" if available else "unavailable",
        "as_of": None,
        "schema": "seiche.nbs-macro-context.v1",
        "available": available,
        "dataset": "CN.NBS.MACRO_CONTEXT",
        "publisher": "National Bureau of Statistics of China",
        "source_url": (
            "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData"
        ),
        "terms_url": (
            "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html"
        ),
        "context_only": True,
        "scoring_eligible": False,
        "cn_cny_gauge_eligible": False,
        "values_published": False,
        "raw_evidence_included": False,
        "history_included": False,
        "public_distribution": "metadata_only",
        "rights_status": "redistribution_review_required",
        "series_catalog": [_series(series_id) for series_id in bot._CHINA_SERIES_IDS],
        "series_count": 4,
        "reading": "Metadata only.",
        "boundaries": ["owner", "values", "scoring"],
    }
    if available:
        china.update(
            {
                "knowledge_time": "2026-08-10T02:00:00Z",
                "revision_id": "nbs-2026-07-r1",
                "predecessor_revision_id": None,
                "source_registry_ids": [
                    "nbs_monthly_data_browser",
                    "nbs_terms_of_service",
                ],
                "provenance": {
                    "manifest_sha256": "a" * 64,
                    "owner_attestation": "ed25519",
                },
                "attestation": {
                    "schema": "seiche.nbs-owner-export-signature.v1",
                    "algorithm": "ed25519",
                    "domain": "seiche-nbs-owner-export-v1",
                    "export_id": "nbs-2026-07-r1",
                    "signer_key_id": "c" * 64,
                    "signed_at": "2026-08-10T02:05:00Z",
                    "manifest_sha256": "a" * 64,
                    "public_projection_sha256": "d" * 64,
                    "signature": "e" * 128,
                },
            }
        )
    else:
        china["reason_code"] = "signed_owner_export_required"
    return {
        "ok": True,
        "schema": "seiche.world-markets.v1",
        "status": status,
        "selection": "china_macro",
        "as_of": None,
        "context_only": True,
        "generated_at": None,
        "clocks": {
            "snapshot_generated_at": None,
            "evaluation_at": None,
            "latest_domain_as_of": None,
            "domains": {
                "money_markets": None,
                "forex": None,
                "capital_markets": None,
            },
            "selected_evidence_as_of": None,
            "excluded_from_observation_clocks": ["china_macro.knowledge_time"],
            "boundary": (
                "Response time never advances a source or observation as-of clock."
            ),
        },
        "citation": {
            "topic_url": bot.CHINA_TOPIC_URL,
            "generated_at": None,
            "evidence_as_of": None,
        },
        "china_macro": china,
    }


def test_restricted_context_renders_only_identity_metadata():
    payload = _payload()
    payload["china_macro"]["raw_evidence"] = "RAW-NBS-SENTINEL"
    payload["china_macro"]["series_catalog"][0]["latest_value"] = 100.5

    text = bot.fmt_china_macro(payload)

    assert "did not pass" in text
    assert "RAW-NBS-SENTINEL" not in text
    assert "100.5" not in text
    assert "Consumer Price Index" not in text


def test_restricted_context_names_the_clock_and_all_four_identities():
    text = bot.fmt_china_macro(_payload())

    assert "restricted" in text
    assert "4 identities · 0 values" in text
    assert "2026-08-10T02:00:00Z" in text
    assert "evidence receipt, not an observation date" in text
    assert "not an NBS digital signature" in text
    assert "cannot enter Seiche scoring or the CN-CNY gauge" in text
    assert all(series_id in text for series_id in bot._CHINA_SERIES_IDS)
    assert bot.CHINA_TOPIC_URL in text


def test_series_label_cannot_smuggle_an_observation_value():
    payload = _payload()
    payload["china_macro"]["series_catalog"][0]["label"] = (
        "Consumer Price Index (The same month last year=100): 100.5"
    )

    text = bot.fmt_china_macro(payload)

    assert "did not pass" in text
    assert "100.5" not in text
    assert _REVIEWED_LABELS["CN.NBS.CPI_INDEX"] not in text


def test_structural_context_says_that_no_trusted_export_is_available():
    text = bot.fmt_china_macro(_payload(available=False))

    assert "structural" in text
    assert "no trusted owner export" in text
    assert "4 identities · 0 values" in text
    assert "Knowledge time" not in text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["china_macro"].update(values_published=True),
        lambda payload: payload["china_macro"].update(scoring_eligible=True),
        lambda payload: payload["china_macro"].update(raw_evidence="sentinel"),
        lambda payload: payload.update(as_of="2026-08-10"),
        lambda payload: payload["clocks"].update(
            selected_evidence_as_of="2026-08-10T02:00:00Z"
        ),
        lambda payload: payload["citation"].update(
            topic_url="https://attacker.example/china"
        ),
        lambda payload: payload["china_macro"]["series_catalog"].reverse(),
    ],
)
def test_boundary_drift_fails_closed(mutate):
    payload = copy.deepcopy(_payload())
    mutate(payload)

    text = bot.fmt_china_macro(payload)

    assert "did not pass" in text
    assert "Consumer Price Index" not in text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["china_macro"].update(
            predecessor_revision_id="../prior"
        ),
        lambda payload: (
            payload["china_macro"].update(revision_id="nbs 2026 r1"),
            payload["china_macro"]["attestation"].update(export_id="nbs 2026 r1"),
        ),
    ],
)
def test_revision_identifiers_require_canonical_export_syntax(mutate):
    payload = copy.deepcopy(_payload())
    mutate(payload)

    text = bot.fmt_china_macro(payload)

    assert "did not pass" in text
    assert "4 identities" not in text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["clocks"].update(evaluation_at="2026-08-10T02:05:00Z"),
        lambda payload: payload["clocks"]["domains"].update(money_markets="2026-08-10"),
        lambda payload: payload["clocks"].update(evaluated_at=None),
    ],
)
def test_standalone_china_rejects_contaminated_or_aliased_clocks(mutate):
    payload = copy.deepcopy(_payload())
    mutate(payload)

    text = bot.fmt_china_macro(payload)

    assert "did not pass" in text
    assert "Consumer Price Index" not in text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("knowledge_time", "2026-08-10T02:00:00+00:00"),
        ("signed_at", "2026-08-10T02:05:00.1Z"),
    ],
)
def test_attestation_timestamps_require_canonical_utc(field, value):
    payload = _payload()
    target = (
        payload["china_macro"]
        if field == "knowledge_time"
        else payload["china_macro"]["attestation"]
    )
    target[field] = value

    assert "did not pass" in bot.fmt_china_macro(payload)


def test_future_year_fails_the_five_minute_skew_bound():
    payload = _payload()
    payload["china_macro"]["knowledge_time"] = "2099-01-01T00:00:00Z"
    payload["china_macro"]["attestation"]["signed_at"] = "2099-01-01T00:00:01Z"

    assert "did not pass" in bot.fmt_china_macro(payload)


def test_fractional_second_chronology_is_compared_as_time_not_text():
    payload = _payload()
    payload["china_macro"]["knowledge_time"] = "2026-08-10T02:00:00.900000Z"
    payload["china_macro"]["attestation"]["signed_at"] = "2026-08-10T02:00:00.100000Z"

    assert "did not pass" in bot.fmt_china_macro(payload)


def test_canonical_fractional_second_chronology_is_accepted():
    payload = _payload()
    payload["china_macro"]["knowledge_time"] = "2026-08-10T02:00:00.100000Z"
    payload["china_macro"]["attestation"]["signed_at"] = "2026-08-10T02:00:00.900000Z"

    text = bot.fmt_china_macro(payload)

    assert "4 identities · 0 values" in text
    assert "did not pass" not in text


def test_actual_backend_structural_projection_passes_the_bot_contract():
    payload = project_world_markets(
        {"generated_at": "2099-01-01T00:00:00Z"},
        selector="china_macro",
        evaluation_asof="2099-01-01T00:00:00Z",
    )

    text = bot.fmt_china_macro(payload)

    assert payload["clocks"]["evaluation_at"] is None
    assert set(payload["clocks"]["domains"].values()) == {None}
    assert "structural" in text
    assert "4 identities · 0 values" in text
    assert all(bot.esc(label) in text for label in _REVIEWED_LABELS.values())
    assert "2099-01-01" not in text


def test_china_command_reads_the_exact_public_projection_in_groups(monkeypatch):
    calls = []
    sent = []
    payload = _payload()
    monkeypatch.setattr(
        bot,
        "api_get",
        lambda path: calls.append(path) or payload,
    )
    monkeypatch.setattr(
        bot,
        "send",
        lambda chat_id, text, keyboard=None: sent.append((chat_id, text, keyboard)),
    )

    bot.handle(-100123, "/china", "group")

    assert calls == ["/api/v2/world-markets?section=china_macro"]
    assert sent[0][0] == -100123
    assert "4 identities · 0 values" in sent[0][1]
    assert sent[0][2] == bot.keyboard_for("/china")
    assert sent[0][2][0][0]["url"] == bot.CHINA_TOPIC_URL


def test_help_and_command_menu_discover_the_metadata_only_view():
    assert "/china" in bot.HELP
    assert "no values" in bot.HELP
    assert any(entry["command"] == "china" for entry in bot.BOT_COMMANDS)
    assert any(
        button.get("callback_data") == "/china"
        for row in bot.keyboard_for("/help")
        for button in row
    )
