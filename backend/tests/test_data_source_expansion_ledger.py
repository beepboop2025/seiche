"""Contract checks for the durable data-coverage documentation."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from urllib.parse import urlparse

from seiche.domain.observation import (
    ConnectorClassification,
    RedistributionStatus,
    SemanticRole,
)
from seiche.markets.calibration import get_local_calibration
from seiche.markets.materialize import _public_instrument_ids
from seiche.markets.registry import default_registry
from seiche.sources.official import PRODUCTION_ADAPTER_KEYS

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "docs" / "data-source-expansion.json"
DOCUMENT_PATH = ROOT / "docs" / "DATA_COVERAGE_LEDGER.md"

REQUIRED_PARALLEL_CANDIDATES = {
    "sec_nmfp_bulk",
    "ecb_data_api",
    "bis_data_portal",
    "imf_sdmx_api",
    "bcb_open_data",
    "hkma_open_api",
    "bok_ecos_api",
    "bank_canada_valet_corra",
    "eia_bulk_data",
    "treasury_tic",
    "ofr_short_term_funding_monitor",
}

REQUIRED_CANDIDATE_FIELDS = {
    "id",
    "priority",
    "publisher",
    "publisher_url",
    "access_url",
    "access_format",
    "cadence",
    "rights_status",
    "rights_evidence_url",
    "target_markets",
    "semantic_roles",
    "proposed_roles",
    "target_engines",
    "implementation_status",
    "blocker",
    "next_action",
}

# Explicit bindings keep the source-rights ledger and executable market packs
# comparable without guessing from publisher names or overlapping semantic
# roles.  A candidate can describe more than one production adapter.
CANDIDATE_ADAPTER_KEYS = {
    "ecb_data_api": (
        ("EA-EUR", "ecb_benchmark"),
        ("EA-EUR", "ecb_policy"),
        ("EA-EUR", "ecb_liquidity"),
    ),
    "hkma_open_api": (("HK-HKD", "hkma_official"),),
    "bok_ecos_api": (
        ("KR-KRW", "bok_ecos_policy"),
        ("KR-KRW", "bok_ecos_money_market"),
    ),
    "cfets_money_market_rates": (("CN-CNY", "cfets_rates"),),
    "pbc_open_market_operations": (("CN-CNY", "pbc_operations"),),
    "rbnz_statistics": (
        ("NZ-NZD", "rbnz_policy"),
        ("NZ-NZD", "rbnz_wholesale"),
    ),
    "ksd_kofr": (("KR-KRW", "ksd_kofr"),),
    "rbi_money_market_operations": (("IN-INR", "rbi_official"),),
    "rba_statistical_tables": (
        ("AU-AUD", "rba_cash"),
        ("AU-AUD", "rba_policy"),
    ),
    "mas_domestic_interest_rates": (
        ("SG-SGD", "mas_sora"),
        ("SG-SGD", "mas_rates"),
    ),
    "boj_stat_search": (
        ("JP-JPY", "boj_rates"),
        ("JP-JPY", "boj_accounts"),
    ),
    "boe_iadb": (
        ("UK-GBP", "boe_sonia"),
        ("UK-GBP", "boe_policy"),
    ),
    "nyfed_markets_api": (
        ("US-USD", "nyfed_rates"),
        ("US-USD", "nyfed_unsecured_rates"),
        ("US-USD", "nyfed_facilities"),
    ),
    "treasury_fiscaldata": (("US-USD", "fiscaldata"),),
}


def _ledger() -> dict:
    return json.loads(LEDGER_PATH.read_text())


def test_data_source_ledger_has_complete_ranked_contract() -> None:
    ledger = _ledger()

    assert ledger["schema_version"] == "1.0"
    assert date.fromisoformat(ledger["as_of"]) == date(2026, 8, 22)
    gaps = ledger["top_gaps"]
    assert [item["rank"] for item in gaps] == list(range(1, 21))
    assert len({item["id"] for item in gaps}) == 20

    candidates = ledger["candidates"]
    candidate_ids = {item["id"] for item in candidates}
    assert len(candidate_ids) == len(candidates)
    assert REQUIRED_PARALLEL_CANDIDATES <= candidate_ids
    assert all(set(item["candidate_ids"]) <= candidate_ids for item in gaps)

    snapshot = ledger["audit_snapshot"]
    assert (
        snapshot["scope"] == "read_only_local_development_cache_not_hetzner_production"
    )
    assert (
        snapshot["fetched_legacy_series"] + snapshot["missing_registered_series"]
        == snapshot["configured_legacy_series"]
    )
    assert (
        snapshot["fresh_series"]
        + snapshot["aging_series"]
        + snapshot["dead_by_design_series"]
        == snapshot["fetched_legacy_series"]
    )

    validation = ledger["post_audit_validation"]
    assert validation["scope"] == "local_clean_release_worktree_not_hetzner_production"
    assert validation["source_groups"] == validation["successful_source_groups"]
    assert validation["degraded_source_groups"] == 0
    assert validation["failed_source_groups"] == 0
    assert len(validation["h10_currency_series_filled"]) == 10
    assert validation["h10_observations_filled"] == (
        len(validation["h10_currency_series_filled"])
        * validation["observations_per_h10_series"]
    )
    assert validation["legacy_observations_after_sweep"] == (
        snapshot["legacy_observations"]
        + validation["new_observations_since_audit_snapshot"]
    )
    assert validation["full_board_result"]["coverage_pct"] == 100.0


def test_source_candidates_have_safe_urls_roles_and_explicit_rights() -> None:
    ledger = _ledger()
    enums = ledger["enumerations"]
    priorities = set(enums["priority"])
    rights = set(enums["rights_status"])
    implementations = set(enums["implementation_status"])
    semantic_roles = {role.value for role in SemanticRole}

    for candidate in ledger["candidates"]:
        assert set(candidate) == REQUIRED_CANDIDATE_FIELDS
        assert candidate["priority"] in priorities
        assert candidate["rights_status"] in rights
        assert candidate["implementation_status"] in implementations
        assert candidate["publisher"].strip()
        assert candidate["access_format"]
        assert candidate["cadence"].strip()
        assert candidate["target_markets"]
        assert candidate["target_engines"]
        assert candidate["semantic_roles"] or candidate["proposed_roles"]
        assert set(candidate["semantic_roles"]) <= semantic_roles
        assert candidate["blocker"].strip()
        assert candidate["next_action"].strip()

        for field in ("publisher_url", "access_url"):
            parsed = urlparse(candidate[field])
            assert parsed.scheme == "https"
            assert parsed.netloc
            assert parsed.username is None
            assert parsed.password is None
            assert "api_key" not in parsed.query.lower()
            assert "token" not in parsed.query.lower()

        rights_url = candidate["rights_evidence_url"]
        if rights_url is not None:
            parsed = urlparse(rights_url)
            assert parsed.scheme == "https"
            assert parsed.netloc

    by_id = {item["id"]: item for item in ledger["candidates"]}
    assert by_id["bcb_open_data"]["rights_status"] == "odbl_1_0_confirmed"
    assert by_id["cfets_money_market_rates"]["rights_status"] == "metadata_only"
    assert by_id["ksd_kofr"]["rights_status"] == "metadata_only"
    assert by_id["rbnz_statistics"]["rights_status"] == "permission_required"


def test_allowed_pack_adapters_have_reviewed_production_contracts() -> None:
    """An ALLOWED declaration needs both implemented rights and runtime code."""

    candidates = {item["id"]: item for item in _ledger()["candidates"]}
    registry = default_registry()

    for candidate_id, adapter_keys in CANDIDATE_ADAPTER_KEYS.items():
        candidate = candidates[candidate_id]
        for market_id, adapter_id in adapter_keys:
            adapter = registry.get(market_id).adapter_map[adapter_id]
            if adapter.redistribution_status is not RedistributionStatus.ALLOWED:
                continue
            assert candidate["rights_status"] != "review_required", (
                f"{market_id}/{adapter_id} is ALLOWED while {candidate_id} "
                "still requires rights review"
            )
            assert candidate["implementation_status"] != "declared_not_implemented", (
                f"{market_id}/{adapter_id} is ALLOWED while {candidate_id} "
                "is declared_not_implemented"
            )
            assert (market_id, adapter_id) in PRODUCTION_ADAPTER_KEYS

    for pack in registry.list():
        for adapter in pack.source_adapters:
            if adapter.redistribution_status is RedistributionStatus.ALLOWED:
                assert (pack.market_id, adapter.adapter_id) in PRODUCTION_ADAPTER_KEYS


def test_pbc_operations_stay_out_of_the_public_boundary_until_reviewed() -> None:
    candidate = {item["id"]: item for item in _ledger()["candidates"]}[
        "pbc_open_market_operations"
    ]
    pack = default_registry().get("CN-CNY")
    adapter = pack.adapter_map["pbc_operations"]
    pbc_instrument_ids = {
        instrument.instrument_id
        for instrument in pack.instruments
        if instrument.source_adapter_id == "pbc_operations"
    }

    assert candidate["rights_status"] == "review_required"
    assert candidate["implementation_status"] == "declared_not_implemented"
    assert adapter.classification is ConnectorClassification.UNAVAILABLE
    assert adapter.redistribution_status is RedistributionStatus.METADATA_ONLY
    assert (pack.market_id, adapter.adapter_id) not in PRODUCTION_ADAPTER_KEYS
    assert pbc_instrument_ids
    assert pbc_instrument_ids.isdisjoint(_public_instrument_ids(pack))


def test_documented_korea_calibration_matches_executable_contract() -> None:
    documented = _ledger()["calibration_contracts"]["KR-KRW"]
    calibration = get_local_calibration("KR-KRW")

    assert documented["calibration_id"] == calibration.calibration_id
    assert documented["maturity"] == calibration.maturity
    assert documented["components"] == [
        {
            "component_id": item.component_id,
            "kind": item.kind.value,
            "required": item.required,
            "weight": item.weight,
            "minimum_history": item.minimum_history,
            "center": item.center,
            "scale": item.scale,
            "stress_direction": item.stress_direction,
            "overnight_role": (
                item.overnight_role.value if item.overnight_role is not None else None
            ),
            "anchor_role": (
                item.anchor_role.value if item.anchor_role is not None else None
            ),
        }
        for item in calibration.components
    ]


def test_human_ledger_links_machine_ledger_and_lists_all_twenty_gaps() -> None:
    document = DOCUMENT_PATH.read_text()

    assert "[`data-source-expansion.json`](data-source-expansion.json)" in document
    assert "## Ranked top 20 data gaps" in document
    for rank in range(1, 21):
        assert f"| {rank} |" in document
