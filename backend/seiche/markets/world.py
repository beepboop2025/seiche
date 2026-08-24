"""Bounded, citable world-markets projection of a completed Seiche board.

This module is intentionally pure: it accepts one already assembled snapshot
and returns a small public catalog.  It never reads a repository, calls a
collector, fits a model, or exposes the chart/history arrays carried by some
browser engines.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from seiche.engines import money_market as money_market_engine
from seiche.public_faults import sanitize_public_fault_payload

WORLD_MARKETS_SCHEMA = "seiche.world-markets.v1"
WORLD_MARKETS_STATUSES = (
    "observed",
    "derived",
    "structural",
    "restricted",
    "unavailable",
)
WORLD_MARKETS_SELECTORS = (
    "summary",
    "money_markets",
    "forex",
    "capital_markets",
    "china_macro",
    "sources",
    "methodology",
    "all",
)

_WORLD_DOMAIN_IDS = (
    "money_markets",
    "forex",
    "capital_markets",
)

CANONICAL_URLS = {
    "world_markets": "https://seiche.info/markets/",
    "money_markets": "https://seiche.info/money-markets/",
    "forex": "https://seiche.info/markets/forex/",
    "capital_markets": "https://seiche.info/markets/capital-markets/",
    "china_macro": "https://seiche.info/markets/china-macro/",
    "api": "https://api.seiche.info/api/v2/world-markets",
    "mcp": "https://api.seiche.info/mcp",
    "realtime_venue": "https://api.seiche.info/undertow/live/quotes.json",
}

# These are publisher-controlled documentation or data pages, not mirrors.
# ``status=structural`` means inclusion in the source catalog and does not
# claim that every source has a current observation in a particular snapshot.
OFFICIAL_SOURCE_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "id": "bis_stats_api_v2",
        "publisher": "Bank for International Settlements",
        "domains": ["money_markets", "forex", "capital_markets"],
        "url": "https://stats.bis.org/api-doc/v2/",
        "status": "structural",
    },
    {
        "id": "bis_exchange_rates",
        "publisher": "Bank for International Settlements",
        "domains": ["forex"],
        "url": "https://www.bis.org/statistics/dataportal/exr.htm",
        "status": "structural",
    },
    {
        "id": "ecb_data_api",
        "publisher": "European Central Bank",
        "domains": ["money_markets", "forex", "capital_markets"],
        "url": "https://data.ecb.europa.eu/help/api/data",
        "status": "structural",
    },
    {
        "id": "us_treasury_interest_rate_xml",
        "publisher": "United States Department of the Treasury",
        "domains": ["money_markets", "capital_markets"],
        "url": "https://home.treasury.gov/treasury-daily-interest-rate-xml-feed",
        "status": "structural",
    },
    {
        "id": "us_treasury_fiscaldata_api",
        "publisher": "United States Department of the Treasury",
        "domains": ["money_markets", "capital_markets"],
        "url": "https://api.fiscaldata.treasury.gov/services/api/fiscal_service",
        "status": "structural",
    },
    {
        "id": "cftc_commitments_of_traders",
        "publisher": "Commodity Futures Trading Commission",
        "domains": ["capital_markets"],
        "url": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
        "status": "structural",
    },
    {
        "id": "sec_edgar_api",
        "publisher": "United States Securities and Exchange Commission",
        "domains": ["capital_markets"],
        "url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        "status": "structural",
    },
    {
        "id": "ny_fed_markets_api",
        "publisher": "Federal Reserve Bank of New York",
        "domains": ["money_markets", "capital_markets"],
        "url": "https://markets.newyorkfed.org/static/docs/markets-api.html",
        "status": "structural",
    },
    {
        "id": "federal_reserve_h41",
        "publisher": "Board of Governors of the Federal Reserve System",
        "domains": ["money_markets", "forex", "capital_markets"],
        "url": "https://www.federalreserve.gov/releases/h41/",
        "status": "structural",
    },
    {
        "id": "federal_reserve_h10",
        "publisher": "Board of Governors of the Federal Reserve System",
        "domains": ["forex"],
        "url": "https://www.federalreserve.gov/releases/h10/current/",
        "status": "structural",
    },
    {
        "id": "fred",
        "publisher": "Federal Reserve Bank of St. Louis",
        "domains": ["money_markets", "forex", "capital_markets"],
        "url": "https://fred.stlouisfed.org/",
        "status": "structural",
    },
    {
        "id": "ofr_short_term_funding_data_api",
        "publisher": "Office of Financial Research",
        "domains": ["money_markets", "capital_markets"],
        "url": "https://data.financialresearch.gov/v1",
        "status": "structural",
    },
    {
        "id": "cboe_vix",
        "publisher": "Cboe Global Markets",
        "domains": ["capital_markets"],
        "url": "https://www.cboe.com/tradable_products/vix/",
        "status": "structural",
    },
    {
        "id": "eia_open_data",
        "publisher": "United States Energy Information Administration",
        "domains": ["forex", "capital_markets"],
        "url": "https://www.eia.gov/opendata/",
        "status": "structural",
    },
    {
        "id": "uk_boe_database",
        "publisher": "Bank of England",
        "domains": ["money_markets", "forex"],
        "url": "https://www.bankofengland.co.uk/boeapps/database/",
        "status": "structural",
    },
    {
        "id": "japan_boj_statistics",
        "publisher": "Bank of Japan",
        "domains": ["money_markets", "forex"],
        "url": "https://www.stat-search.boj.or.jp/",
        "status": "structural",
    },
    {
        "id": "nbs_monthly_data_browser",
        "publisher": "National Bureau of Statistics of China",
        "domains": ["china_macro"],
        "url": "https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData",
        "status": "structural",
    },
    {
        "id": "nbs_terms_of_service",
        "publisher": "National Bureau of Statistics of China",
        "domains": ["china_macro"],
        "url": "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html",
        "status": "structural",
    },
    {
        "id": "world_bank_wdi",
        "publisher": "World Bank",
        "domains": ["china_macro"],
        "url": "https://datacatalog.worldbank.org/search/dataset/0037712/world-development-indicators",
        "status": "structural",
    },
)

STATUS_DEFINITIONS = {
    "observed": "A source-reported value retained with its own as-of clock; it may be stale at evaluation time.",
    "derived": "A bounded Seiche transformation, comparison, percentile, proxy, or association; not a raw observation.",
    "structural": "Method, source, market architecture, or routing metadata; not a current market print.",
    "restricted": "Evidence intentionally omitted because redistribution or raw-history rights do not permit this surface.",
    "unavailable": "No eligible completed evidence is present; absence is not interpreted as calm or zero.",
}

_MONEY_SECTION_IDS = (
    "policy_corridor",
    "secured_distributions",
    "repo_segments",
    "unsecured_funding",
    "bills_cash_curve",
    "liquidity_buffers",
    "mmf_plumbing",
)
_MAX_METRICS_PER_SECTION = 8
_MAX_FOREX_LEADERS = 22
_MAX_NETWORK_EDGES = 12
_MAX_CAPITAL_CARDS = 8
_NESTED_LIST_LIMIT = 32
_OMITTED_NESTED_KEYS = frozenset(
    {
        "chart",
        "charts",
        "history",
        "series",
        "rows",
        "rate_rows",
        "fx_rows",
        "yoy_rows",
        "index_series",
    }
)
_CAPITAL_POSITIONING_BLOCKS = (
    (
        "rvxray",
        (
            "asof",
            "pair_proxy_b",
            "gross_short_b",
            "net_b",
            "dv01_m_per_bp",
            "pair_change_13w_b",
            "size_z",
            "dvp_volume_b",
        ),
        "derived",
    ),
    (
        "warehouse",
        (
            "asof",
            "total_net_b",
            "total_pctl",
            "chg_13w_b",
            "long_end_share_pct",
            "buckets",
        ),
        "derived",
    ),
    (
        "officialbid",
        (
            "asof",
            "classification",
            "custody_b",
            "custody_chg_4w_b",
            "custody_chg_13w_b",
            "foreign_rrp_b",
            "foreign_rrp_chg_13w_b",
            "fima_repo_b",
            "fima_drawn",
            "footprint_b",
            "footprint_chg_13w_b",
            "letter_line",
        ),
        "derived",
    ),
)
_CAPITAL_PRIMARY_BLOCKS = (
    ("auctions", ("asof", "digestion_index", "recent_auctions"), "derived"),
    (
        "reportcard",
        (
            "asof",
            "funding_asof",
            "n_cards",
            "window_bd",
            "demand_fatigue",
            "letter_line",
        ),
        "derived",
    ),
    (
        "supplydesk",
        ("asof", "horizon_end", "announced_through", "totals", "heaviest_day"),
        "derived",
    ),
)
_CAPITAL_SOURCE_IDS = {
    "rvxray": ["cftc_commitments_of_traders"],
    "warehouse": ["ny_fed_markets_api"],
    "officialbid": ["federal_reserve_h41", "fred"],
    "auctions": ["us_treasury_fiscaldata_api"],
    "reportcard": ["us_treasury_fiscaldata_api"],
    "supplydesk": ["us_treasury_fiscaldata_api"],
}


def _object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value[:limit] if isinstance(item, Mapping)]


def _bounded_value(value: Any, depth: int = 0) -> Any:
    """Copy JSON-like nested metadata while stripping history-shaped fields."""

    if depth >= 6:
        return None
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_value(item, depth + 1)
            for key, item in list(value.items())[:64]
            if not str(key).startswith("_")
            and str(key).lower() not in _OMITTED_NESTED_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth + 1) for item in value[:_NESTED_LIST_LIMIT]]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _pick(value: Any, *fields: str) -> dict[str, Any]:
    source = _object(value)
    return {
        field: _bounded_value(source.get(field)) for field in fields if field in source
    }


def _reason(engine: Any, fallback: str) -> str:
    value = _object(engine).get("reason")
    if not isinstance(value, str) or not value.strip():
        return fallback
    sanitized = sanitize_public_fault_payload(
        {"status": "unavailable", "reason": value}
    )
    safe = _object(sanitized).get("reason")
    return safe if isinstance(safe, str) and safe.strip() else fallback


def _source_registry_ids(source: Any) -> list[str]:
    """Link a displayed source label only where the publisher is explicit."""

    label = str(source or "").lower()
    linked: list[str] = []
    if "new york fed" in label or "federal reserve bank of new york" in label:
        linked.append("ny_fed_markets_api")
    if "office of financial research" in label or label.startswith("ofr"):
        linked.append("ofr_short_term_funding_data_api")
    if "fiscaldata" in label:
        linked.append("us_treasury_fiscaldata_api")
    if "h.4.1" in label:
        linked.append("federal_reserve_h41")
    if "cboe" in label or "vix" in label:
        linked.append("cboe_vix")
    if "fred" in label:
        linked.append("fred")
    return linked


def _observed_or_derived(metric: Mapping[str, Any]) -> str:
    if metric.get("value") is None and not any(
        metric.get(key) is not None
        for key in ("last", "last_pct", "value_bp", "spread_bp")
    ):
        return "unavailable"
    if metric.get("formula") or metric.get("stress_percentile") is not None:
        return "derived"
    return "observed"


def _metric(metric: Any) -> dict[str, Any]:
    """Whitelist one current metric; never copy a history or chart field."""

    raw = _object(metric)
    out = _pick(
        raw,
        "id",
        "key",
        "label",
        "value",
        "unit",
        "asof",
        "event_time",
        "published_at",
        "knowledge_time",
        "cadence",
        "freshness",
        "source",
        "source_url",
        "semantic_role",
        "explanation",
        "formula",
        "alignment",
        "stress_percentile",
        "percentile_3y",
        "robust_z_1y",
        "change_1_observation",
        "change_5_observations",
        "change_20_observations",
    )
    out["status"] = _observed_or_derived(raw)
    source_status = raw.get("status")
    if isinstance(source_status, str):
        out["source_status"] = source_status
    linked_sources = _source_registry_ids(raw.get("source"))
    if linked_sources and out["status"] != "unavailable":
        out["source_registry_ids"] = linked_sources
    return out


def _money_sections(engine: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_sections = {
        section.get("id"): section
        for section in _items(engine.get("sections"), len(_MONEY_SECTION_IDS))
        if section.get("id") in _MONEY_SECTION_IDS
    }
    sections = []
    for section_id in _MONEY_SECTION_IDS:
        section = raw_sections.get(section_id)
        if section is None:
            sections.append(
                {
                    "id": section_id,
                    "status": "unavailable",
                    "metrics": [],
                }
            )
            continue
        metrics = [
            _metric(item)
            for item in _items(
                section.get("metrics"),
                _MAX_METRICS_PER_SECTION,
            )
        ]
        sections.append(
            {
                **_pick(section, "id", "title", "label", "plain_language"),
                "status": (
                    "observed"
                    if any(item["status"] == "observed" for item in metrics)
                    else "derived"
                    if any(item["status"] == "derived" for item in metrics)
                    else "unavailable"
                ),
                "metrics": metrics,
            }
        )
    return sections


def _money_markets(
    snapshot: Mapping[str, Any],
    *,
    evaluation_asof: Any,
) -> dict[str, Any]:
    engine = _object(_object(snapshot.get("engines")).get("money_market"))
    if (
        engine.get("ok") is not True
        or engine.get("schema") != "seiche.money-market-desk.v1"
        or not isinstance(engine.get("asof"), str)
    ):
        return {
            "status": "unavailable",
            "as_of": engine.get("asof"),
            "reason": _reason(
                engine,
                "the completed snapshot has no eligible money-market desk",
            ),
            "sections": [],
        }
    if evaluation_asof is None:
        return {
            "status": "unavailable",
            "as_of": engine.get("asof"),
            "reason": "money-market freshness cannot be evaluated without an explicit clock",
            "sections": [],
        }
    try:
        engine = money_market_engine.refresh_for_evaluation(
            engine,
            evaluation_asof=evaluation_asof,
        )
    except Exception:  # noqa: BLE001 - corrupt LKG state must fail closed
        return {
            "status": "unavailable",
            "as_of": engine.get("asof"),
            "reason": "money-market freshness evaluation failed",
            "sections": [],
        }
    return {
        "status": "observed",
        "as_of": engine.get("asof"),
        "coverage_boundary": (
            "This block is the assembled institutional USD desk; the separate "
            "/api/v2/money-markets atlas preserves other monetary areas at "
            "their native cadence."
        ),
        "plain_language": engine.get("plain_language"),
        "quant_read": engine.get("quant_read"),
        "regime": _pick(
            engine.get("regime"),
            "state",
            "worst_stress_percentile",
            "status",
        ),
        "strongest_signal": _pick(
            engine.get("strongest_signal"),
            "metric_id",
            "label",
            "value",
            "unit",
            "asof",
            "stress_percentile",
            "reading",
            "use",
        ),
        "countercase": _pick(
            engine.get("countercase"),
            "metric_id",
            "label",
            "value",
            "unit",
            "asof",
            "stress_percentile",
            "reading",
        ),
        "coverage": _pick(
            engine.get("coverage"),
            "coverage_pct",
            "status",
            "available",
            "total",
        ),
        "freshness": _pick(
            engine.get("freshness"),
            "desk_asof",
            "evaluation_asof",
            "status",
            "status_counts",
        ),
        "sections": _money_sections(engine),
        "caveats": list(engine.get("caveats") or [])[:12],
    }


def _forex_currencies(fx: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        _forex_currency(item)
        for item in _items(fx.get("currencies"), _MAX_FOREX_LEADERS)
    ]


def _forex_currency(item: Mapping[str, Any]) -> dict[str, Any]:
    raw = _object(item)
    spot_value = _bounded_value(raw.get("last_local_per_usd", raw.get("spot")))
    spot_as_of = _bounded_value(raw.get("asof"))
    spot = {
        "status": (
            "observed"
            if spot_value is not None and isinstance(spot_as_of, str)
            else "unavailable"
        ),
        "value": spot_value,
        "unit": _bounded_value(raw.get("unit")),
        "as_of": spot_as_of,
        "source_id": _bounded_value(raw.get("source_id")),
    }
    if spot["status"] == "observed":
        spot["source_registry_ids"] = ["federal_reserve_h10", "fred"]
    analytics = _pick(
        raw,
        "change_5d_pct",
        "change_20d_pct",
        "change_60d_pct",
        "realized_vol_20d_pct",
        "depreciation_percentile",
        "volatility_percentile",
        "pressure",
        "direction",
        "policy_diff_vs_effr_bp",
        "policy_asof",
        "policy_rate_label",
        "policy_rate_cadence",
    )
    analytics["status"] = (
        "derived"
        if any(value is not None for value in analytics.values())
        else "unavailable"
    )
    return {
        **_pick(raw, "key", "label", "bucket"),
        "status": ("derived" if analytics["status"] == "derived" else spot["status"]),
        "spot": spot,
        "analytics": analytics,
    }


def _forex_harbors(engine: Mapping[str, Any]) -> list[dict[str, Any]]:
    if engine.get("ok") is not True:
        return []
    return [
        {
            **_pick(item, "harbor", "regime", "stress", "stress_coverage", "note"),
            "rate": _pick(
                item.get("rate"),
                "label",
                "last_pct",
                "asof",
                "chg_6m_bp",
                "chg_1y_bp",
            ),
            "fx": _pick(
                item.get("fx"),
                "label",
                "last",
                "asof",
                "chg_60d_pct",
                "vol10_ann_pct",
            ),
            "status": "derived",
        }
        for item in _items(engine.get("harbors"), _MAX_FOREX_LEADERS)
    ]


def _forex_basins(engine: Mapping[str, Any]) -> list[dict[str, Any]]:
    if engine.get("ok") is not True:
        return []
    fields = ("basin", "anchor", "value_bp", "z", "vol_z", "asof")
    return [
        {**_pick(item, *fields), "status": "derived"}
        for item in _items(engine.get("basins"), _MAX_FOREX_LEADERS)
    ]


def _forex_network(engine: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "derived" if engine.get("ok") is True else "unavailable",
        **_pick(
            engine,
            "asof",
            "total_connectedness",
            "source",
            "sink",
            "verdict",
        ),
        "directional": [
            {
                **_pick(item, "node", "to", "from", "net", "role"),
                "status": "derived",
            }
            for item in _items(engine.get("directional"), _MAX_NETWORK_EDGES)
        ],
    }


def _forex_passage(engine: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_pick(engine, "earned", "tentative", "not_earned", "doctrine"),
        "status": "derived",
        "edges": [
            {
                **_pick(
                    item,
                    "source",
                    "target",
                    "lag_bd",
                    "status",
                    "corr_discovery",
                    "corr_holdout",
                    "interpretation",
                ),
                "evidence_status": "derived",
            }
            for item in _items(engine.get("edges"), _MAX_NETWORK_EDGES)
        ],
    }


def _forex_coverage(engine: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **_pick(item, "aspect", "status", "coverage", "reason"),
            "evidence_status": (
                "unavailable"
                if str(item.get("status", "")).lower()
                in {"out_of_scope", "unavailable"}
                else "structural"
            ),
        }
        for item in _items(engine.get("coverage_matrix"), 16)
    ]


def _forex_breadth(fx: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("index", "change_20d_pct", "pressure_percentile", "asof")
    out = {
        "broad": _pick(fx.get("broad"), *fields),
        "advanced": _pick(fx.get("advanced"), *fields),
        "emerging": _pick(fx.get("emerging"), *fields),
        "median_pair_depreciation_percentile": fx.get(
            "median_pair_depreciation_percentile"
        ),
        "median_pair_volatility_percentile": fx.get(
            "median_pair_volatility_percentile"
        ),
    }
    if any(
        isinstance(_object(out.get(key)).get("asof"), str)
        and any(
            _object(out.get(key)).get(field) is not None
            for field in ("index", "change_20d_pct", "pressure_percentile")
        )
        for key in ("broad", "advanced", "emerging")
    ):
        out["source_registry_ids"] = ["federal_reserve_h10", "fred"]
    return out


def _dated_current_block(value: Any) -> bool:
    block = _object(value)
    return isinstance(block.get("asof"), str) and any(
        item is not None for key, item in block.items() if key != "asof"
    )


def _forex_dollar_system(estuary: Mapping[str, Any]) -> dict[str, Any]:
    out = _pick(
        estuary.get("dollar_system"),
        "swap_lines",
        "foreign_official_rrp",
        "fima_repo",
        "offshore_dollar_credit",
    )
    if any(
        _dated_current_block(out.get(key))
        for key in ("swap_lines", "foreign_official_rrp", "fima_repo")
    ):
        out["source_registry_ids"] = ["federal_reserve_h41", "fred"]
    if _dated_current_block(out.get("offshore_dollar_credit")):
        out.setdefault("source_registry_ids", []).append("bis_stats_api_v2")
    return out


def _forex_headline(estuary: Mapping[str, Any]) -> dict[str, Any]:
    out = _pick(
        estuary.get("headline"),
        "regime",
        "verdict",
        "upstream_pressure",
        "fx_pressure",
        "materials_pressure",
        "funding_priced",
        "transmission_gap",
        "coverage_pct",
        "context_only",
    )
    linked: list[str] = []
    if out.get("fx_pressure") is not None:
        linked.extend(["federal_reserve_h10", "fred"])
    material_rows = _items(
        _object(estuary.get("materials")).get("instruments"),
        _NESTED_LIST_LIMIT,
    )
    if any(
        row.get("key") in {"WTI", "NATGAS"}
        and row.get("last") is not None
        and isinstance(row.get("asof"), str)
        for row in material_rows
    ):
        linked.extend(["eia_open_data", "fred"])
    if linked:
        out["source_registry_ids"] = list(dict.fromkeys(linked))
    return out


def _forex(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    engines = _object(snapshot.get("engines"))
    estuary = _object(engines.get("estuary"))
    if estuary.get("ok") is not True or not isinstance(estuary.get("asof"), str):
        return {
            "status": "unavailable",
            "as_of": estuary.get("asof"),
            "reason": _reason(
                estuary,
                "the completed snapshot has no eligible forex context",
            ),
            "currencies": [],
        }

    fx = _object(estuary.get("fx"))
    return {
        "status": "derived",
        "as_of": estuary.get("asof"),
        "headline": _forex_headline(estuary),
        "breadth": _forex_breadth(fx),
        "currencies": _forex_currencies(fx),
        "passage": _forex_passage(_object(estuary.get("passage"))),
        "harbors": _forex_harbors(_object(engines.get("harbors"))),
        "basins": _forex_basins(_object(engines.get("basins"))),
        "network": _forex_network(_object(engines.get("spillover"))),
        "dollar_system": _forex_dollar_system(estuary),
        "settlement_structure": _pick(
            estuary.get("settlement_structure"),
            "survey_asof",
            "fx_turnover",
            "settlement_risk",
            "principles",
        ),
        "coverage_matrix": _forex_coverage(estuary),
        "caveats": list(estuary.get("caveats") or [])[:12],
    }


def _capital_blocks(
    engines: Mapping[str, Any],
    specs: tuple[tuple[str, tuple[str, ...], str], ...],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for engine_id, fields, evidence_status in specs:
        engine = _object(engines.get(engine_id))
        if engine_id == "warehouse":
            blocks.append(_warehouse_block(engine))
            continue
        available = engine.get("ok") is True and isinstance(engine.get("asof"), str)
        if engine_id == "rvxray" and engine.get("current_available") is False:
            available = False
        item = {
            "id": engine_id,
            "status": evidence_status if available else "unavailable",
            **_pick(engine, *fields),
        }
        if engine_id == "rvxray":
            quality = _pick(
                engine,
                "score_eligible",
                "metric_coverage",
                "pair_change_13w_quality",
                "series_quality",
            )
            if quality:
                item["quality"] = quality
        if available:
            item["source_registry_ids"] = list(_CAPITAL_SOURCE_IDS.get(engine_id, []))
            if engine_id == "rvxray" and engine.get("dvp_volume_b") is not None:
                item["source_registry_ids"].append("ofr_short_term_funding_data_api")
            if engine_id == "supplydesk":
                item["clock_role"] = "scenario_evaluation_not_evidence_clock"
        if engine_id == "auctions" and isinstance(item.get("recent_auctions"), list):
            recent = item["recent_auctions"][-_MAX_CAPITAL_CARDS:]
            item["recent_auctions"] = [
                dict(value) for value in recent if isinstance(value, Mapping)
            ]
        if not available:
            item["reason"] = (
                engine.get("current_reason")
                if engine_id == "rvxray"
                and isinstance(engine.get("current_reason"), str)
                else _reason(engine, f"{engine_id} is unavailable")
            )
        blocks.append(item)
    return blocks


def _warehouse_block(engine: Mapping[str, Any]) -> dict[str, Any]:
    if engine.get("ok") is not True or not isinstance(engine.get("asof"), str):
        return {
            "id": "warehouse",
            "status": "unavailable",
            "asof": engine.get("asof"),
            "reason": _reason(engine, "warehouse is unavailable"),
        }
    buckets = _items(engine.get("buckets"), _MAX_CAPITAL_CARDS)
    observed = {
        "status": "observed",
        "as_of": engine.get("asof"),
        "total_net_b": _bounded_value(engine.get("total_net_b")),
        "buckets": [_pick(item, "bucket", "net_b") for item in buckets],
    }
    analytics = {
        "status": "derived",
        "as_of": engine.get("asof"),
        **_pick(engine, "total_pctl", "chg_13w_b", "long_end_share_pct"),
        "buckets": [_pick(item, "bucket", "pctl") for item in buckets],
    }
    return {
        "id": "warehouse",
        "status": "derived",
        "evidence_statuses": ["observed", "derived"],
        "asof": engine.get("asof"),
        "observed_facts": observed,
        "analytics": analytics,
        "source_registry_ids": list(_CAPITAL_SOURCE_IDS["warehouse"]),
    }


def _capital_price(value: Any, source_ids: list[str]) -> dict[str, Any]:
    raw = _object(value)
    observed_value = _bounded_value(raw.get("value"))
    available = observed_value is not None and isinstance(raw.get("asof"), str)
    if not available:
        return {"status": "unavailable", "as_of": raw.get("asof")}
    return {
        "status": "observed",
        "value": observed_value,
        "as_of": raw.get("asof"),
        "source_registry_ids": list(source_ids),
    }


def _capital_risk_context(
    composite: Mapping[str, Any],
    tell: Mapping[str, Any],
    headline: Mapping[str, Any],
) -> dict[str, Any]:
    vix = _capital_price(headline.get("vix"), ["cboe_vix", "fred"])
    high_yield = _capital_price(headline.get("hy_oas_pct"), ["fred"])
    tell_available = tell.get("ok") is True and isinstance(tell.get("asof"), str)
    clocks = [
        item.get("as_of")
        for item in (vix, high_yield)
        if isinstance(item.get("as_of"), str)
    ]
    if tell_available:
        clocks.append(tell["asof"])
    if not clocks:
        return {
            "status": "unavailable",
            "as_of": None,
            "reason": "no dated market-price or market-vs-plumbing evidence is available",
            "market_prices": {"vix": vix, "high_yield_oas": high_yield},
        }
    composite_available = composite.get("ok") is True or (
        composite.get("ok") is None and composite.get("value") is not None
    )
    derived = tell_available or composite_available
    return {
        "status": "derived" if derived else "observed",
        "as_of": max(clocks),
        "funding_stress": _pick(
            composite if composite_available else {},
            "value",
            "regime",
            "coverage_pct",
            "dead_inputs",
        ),
        "market_vs_plumbing": _pick(
            tell if tell_available else {},
            "asof",
            "tell",
            "plumbing_pctl",
            "market_pctl",
            "reading",
            "components",
        ),
        "market_prices": {"vix": vix, "high_yield_oas": high_yield},
        "clock_boundary": (
            "as_of is the latest dated child observation; the composite has no "
            "independent evidence clock and does not advance it"
        ),
    }


def _capital_global_liquidity(engine: Mapping[str, Any]) -> dict[str, Any]:
    if engine.get("ok") is not True or not isinstance(engine.get("asof"), str):
        return {
            "status": "unavailable",
            "as_of": engine.get("asof"),
            "reason": _reason(engine, "BIS global-liquidity context is unavailable"),
        }
    return {
        "status": "derived",
        "source_registry_ids": ["bis_stats_api_v2"],
        **_pick(
            engine,
            "asof",
            "publication_lag_days",
            "stock",
            "composition",
            "eme",
            "credit_gaps",
            "reading",
            "caveats",
        ),
    }


def _capital_evidence_clocks(values: list[dict[str, Any]]) -> list[str]:
    clocks: list[str] = []
    for value in values:
        if (
            value.get("status") == "unavailable"
            or value.get("clock_role") == "scenario_evaluation_not_evidence_clock"
        ):
            continue
        keys = ("as_of", "asof", "funding_asof")
        clocks.extend(value[key] for key in keys if isinstance(value.get(key), str))
    return clocks


def _capital_markets(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    engines = _object(snapshot.get("engines"))
    composite = _object(engines.get("composite"))
    tell = _object(_object(snapshot.get("deep")).get("tell"))
    headline = _object(snapshot.get("headline"))
    positioning = _capital_blocks(engines, _CAPITAL_POSITIONING_BLOCKS)
    primary_market = _capital_blocks(engines, _CAPITAL_PRIMARY_BLOCKS)
    global_liquidity = _capital_global_liquidity(_object(engines.get("thermohaline")))
    risk_context = _capital_risk_context(composite, tell, headline)
    evidence = [risk_context, global_liquidity, *positioning, *primary_market]
    as_of_candidates = _capital_evidence_clocks(evidence)
    if not as_of_candidates:
        return {
            "status": "unavailable",
            "as_of": None,
            "reason": "the completed snapshot has no dated eligible capital-market evidence",
            "risk_context": risk_context,
            "positioning": positioning,
            "primary_market": primary_market,
            "global_liquidity": global_liquidity,
        }
    status = (
        "derived"
        if any(value.get("status") == "derived" for value in evidence)
        else "observed"
    )
    return {
        "status": status,
        "as_of": max(as_of_candidates),
        "risk_context": risk_context,
        "positioning": positioning,
        "primary_market": primary_market,
        "global_liquidity": global_liquidity,
        "execution_liquidity": {
            "status": "structural",
            "scope": (
                "Position-sized depth, liquidity-provider concentration and exit "
                "cost belong to Undertow; this World Markets projection does not "
                "manufacture execution quotes."
            ),
            "url": CANONICAL_URLS["realtime_venue"],
        },
        "caveats": [
            "This is macro-capital transmission context: public positioning proxies, Treasury primary-market absorption, market stress and global dollar credit; it is not broad security-level capital-market coverage.",
            "Positioning proxies and percentiles are derived context, not observed holdings or trade recommendations.",
        ],
    }


_CHINA_PUBLIC_RECORD_FIELDS = frozenset(
    {
        "schema",
        "available",
        "evidence_status",
        "dataset",
        "revision_id",
        "predecessor_revision_id",
        "predecessor_manifest_sha256",
        "knowledge_time",
        "publisher",
        "source_url",
        "publication_policy",
        "values_published",
        "series",
        "provenance",
        "caveats",
        "attestation",
    }
)
_CHINA_PROVENANCE_FIELDS = frozenset({"manifest_sha256", "owner_attestation"})
_CHINA_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "algorithm",
        "domain",
        "export_id",
        "signer_key_id",
        "signed_at",
        "manifest_sha256",
        "public_projection_sha256",
        "signature",
    }
)
_LOWER_HEX_64 = re.compile(r"[0-9a-f]{64}")
_LOWER_HEX_128 = re.compile(r"[0-9a-f]{128}")
_SAFE_REVISION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _aware_china_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return (
        parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    )


def _verified_china_record_matches_catalog(
    candidate: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> bool:
    """Validate the closed public envelope before labeling it available."""

    if set(candidate) != _CHINA_PUBLIC_RECORD_FIELDS:
        return False
    revision_id = candidate.get("revision_id")
    predecessor = candidate.get("predecessor_revision_id")
    predecessor_hash = candidate.get("predecessor_manifest_sha256")
    if (
        not isinstance(revision_id, str)
        or _SAFE_REVISION_ID.fullmatch(revision_id) is None
    ):
        return False
    if predecessor is None:
        if predecessor_hash is not None:
            return False
    elif (
        not isinstance(predecessor, str)
        or _SAFE_REVISION_ID.fullmatch(predecessor) is None
        or not isinstance(predecessor_hash, str)
        or _LOWER_HEX_64.fullmatch(predecessor_hash) is None
    ):
        return False

    knowledge_time = _aware_china_time(candidate.get("knowledge_time"))
    provenance = candidate.get("provenance")
    attestation = candidate.get("attestation")
    if (
        knowledge_time is None
        or not isinstance(provenance, Mapping)
        or set(provenance) != _CHINA_PROVENANCE_FIELDS
        or not isinstance(attestation, Mapping)
        or set(attestation) != _CHINA_ATTESTATION_FIELDS
    ):
        return False
    manifest_hash = provenance.get("manifest_sha256")
    signed_at = _aware_china_time(attestation.get("signed_at"))
    return bool(
        candidate.get("schema") == catalog.get("schema")
        and candidate.get("dataset") == catalog.get("dataset")
        and candidate.get("publisher") == catalog.get("publisher")
        and candidate.get("source_url") == catalog.get("source_url")
        and candidate.get("publication_policy") == catalog.get("publication_policy")
        and candidate.get("available") is True
        and candidate.get("evidence_status") == "restricted"
        and candidate.get("values_published") is False
        and candidate.get("series") == catalog.get("series")
        and isinstance(candidate.get("caveats"), list)
        and candidate.get("caveats")
        and isinstance(manifest_hash, str)
        and _LOWER_HEX_64.fullmatch(manifest_hash) is not None
        and provenance.get("owner_attestation") == "ed25519"
        and attestation.get("schema") == "seiche.nbs-owner-export-signature.v1"
        and attestation.get("algorithm") == "ed25519"
        and attestation.get("domain") == "seiche-nbs-owner-export-v1"
        and attestation.get("export_id") == revision_id
        and attestation.get("manifest_sha256") == manifest_hash
        and isinstance(attestation.get("public_projection_sha256"), str)
        and _LOWER_HEX_64.fullmatch(attestation["public_projection_sha256"]) is not None
        and isinstance(attestation.get("signer_key_id"), str)
        and _LOWER_HEX_64.fullmatch(attestation["signer_key_id"]) is not None
        and isinstance(attestation.get("signature"), str)
        and _LOWER_HEX_128.fullmatch(attestation["signature"]) is not None
        and signed_at is not None
        and signed_at >= knowledge_time
    )


def _china_macro(
    context: object | None,
    economic_context: object | None = None,
) -> dict[str, Any]:
    """Project only the release-reviewed, metadata-only NBS public contract.

    The caller may inject a signature-verified public revision.  This second
    whitelist is deliberate defense in depth: even a malformed injected
    mapping cannot move observations, raw evidence, or history into World
    Markets, and this module never opens the restricted intake store.
    """

    from seiche.nbs_intake import (
        NBS_DATASET,
        NBS_PUBLIC_SCHEMA,
        NBSMacroContext,
        nbs_public_catalog,
    )
    from seiche.palimpsest_china_intake import (
        CONTEXT_SCHEMA as PALIMPSEST_CHINA_CONTEXT_SCHEMA,
        PalimpsestChinaEconomicContext,
    )

    catalog = nbs_public_catalog()
    candidate = context.to_dict() if isinstance(context, NBSMacroContext) else None
    # Only the typed result of the signature-verifying public loader can become
    # available.  Requiring the complete code-owned series catalog also keeps a
    # partial owner capture from overstating the stable public contract.
    candidate_is_verified = bool(
        candidate is not None
        and candidate.get("schema") == NBS_PUBLIC_SCHEMA
        and candidate.get("dataset") == NBS_DATASET
        and _verified_china_record_matches_catalog(candidate, catalog)
    )
    context = candidate if candidate_is_verified else catalog
    available = candidate_is_verified

    policy = _object(catalog.get("publication_policy"))
    series: list[dict[str, Any]] = []
    raw_series = catalog.get("series")
    for source in raw_series[:4] if isinstance(raw_series, list) else []:
        if not isinstance(source, Mapping):
            continue
        row = {
            key: source.get(key)
            for key in (
                "series_id",
                "catalogid",
                "catalog_label",
                "row_id",
                "i",
                "ek",
                "ek_dp",
                "dp",
                "dp_name",
                "label",
                "reference_release_url",
                "release_url",
                "source_unit_label_exact",
                "source_unit_semantically_authoritative",
                "semantic_contract",
                "value_publication",
            )
            if key in source
        }
        if isinstance(row.get("series_id"), str):
            series.append(row)

    provenance_in = _object(context.get("provenance")) if available else {}
    provenance = {
        key: provenance_in.get(key)
        for key in (
            "manifest_sha256",
            "owner_attestation",
        )
        if key in provenance_in
    }
    attestation_in = _object(context.get("attestation")) if available else {}
    attestation = {
        key: attestation_in.get(key)
        for key in (
            "schema",
            "algorithm",
            "domain",
            "export_id",
            "signer_key_id",
            "signed_at",
            "manifest_sha256",
            "public_projection_sha256",
            "signature",
        )
        if key in attestation_in
    }
    evidence_status = "restricted" if available else "unavailable"
    reading = (
        "A trusted owner-attested NBS browser export is present. Its exact "
        "series identities and provenance are public; values remain withheld "
        "pending redistribution review."
        if available
        else (
            "The release-reviewed NBS series catalog is public, but no trusted "
            "owner export is currently available."
        )
    )
    out: dict[str, Any] = {
        "status": "restricted" if available else "structural",
        "evidence_status": evidence_status,
        "as_of": None,
        "schema": catalog["schema"],
        "available": available,
        "dataset": catalog["dataset"],
        "publisher": catalog["publisher"],
        "source_url": catalog["source_url"],
        "context_only": True,
        "scoring_eligible": False,
        "cn_cny_gauge_eligible": False,
        "values_published": False,
        "raw_evidence_included": False,
        "history_included": False,
        "public_distribution": policy.get("public_distribution", "metadata_only"),
        "rights_status": policy.get("rights_status", "redistribution_review_required"),
        "terms_url": policy.get(
            "terms_url",
            "https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html",
        ),
        "series_catalog": series,
        "series_count": len(series),
        "reading": reading,
        "boundaries": [
            "Owner attestation is not an NBS digital signature.",
            "No NBS value, raw export, or history is redistributed.",
            "China macro context cannot enter CN-CNY gauges or Seiche scoring.",
        ],
    }
    if available:
        out["source_registry_ids"] = [
            "nbs_monthly_data_browser",
            "nbs_terms_of_service",
        ]
    for key in ("revision_id", "predecessor_revision_id", "knowledge_time"):
        if available and key in context:
            out[key] = context.get(key)
    if provenance:
        out["provenance"] = provenance
    if attestation:
        out["attestation"] = attestation
    if not available and isinstance(context.get("reason_code"), str):
        out["reason_code"] = context.get("reason_code")
    economic_is_owner_attested = bool(
        isinstance(economic_context, PalimpsestChinaEconomicContext)
        and economic_context.owner_attested
    )
    economic = (
        economic_context.to_dict()
        if isinstance(economic_context, PalimpsestChinaEconomicContext)
        else None
    )
    if (
        economic_is_owner_attested
        and isinstance(economic, dict)
        and economic.get("schema") == PALIMPSEST_CHINA_CONTEXT_SCHEMA
        and economic.get("source_id") == "world_bank_wdi"
        and economic.get("context_only") is True
        and economic.get("scoring_eligible") is False
        and economic.get("cn_cny_gauge_eligible") is False
        and economic.get("market_observation_eligible") is False
    ):
        # This is a separate, rights-cleared and owner-attested annual
        # structural panel. Existing NBS fields above remain metadata-only and
        # retain their exact meaning. A direct ``verify_export`` result is
        # intentionally insufficient: only ``load_accepted_export`` holds the
        # private process capability required at this public boundary.
        out["economic_context"] = economic
    return out


def _source_link_paths(value: Any, path: str = "") -> dict[str, set[str]]:
    links: dict[str, set[str]] = {}
    if isinstance(value, Mapping):
        ids = value.get("source_registry_ids")
        if isinstance(ids, list):
            for source_id in ids:
                if isinstance(source_id, str):
                    links.setdefault(source_id, set()).add(path or "root")
        for key, child in value.items():
            if key != "source_registry_ids":
                child_path = f"{path}.{key}" if path else str(key)
                for source_id, paths in _source_link_paths(child, child_path).items():
                    links.setdefault(source_id, set()).update(paths)
    elif isinstance(value, list):
        for child in value:
            for source_id, paths in _source_link_paths(child, f"{path}[]").items():
                links.setdefault(source_id, set()).update(paths)
    return links


def _source_registry(domains: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    links = _source_link_paths(domains)
    return [
        {
            **item,
            "domains": list(item["domains"]),
            "used_in_snapshot": item["id"] in links,
            "projection_paths": sorted(links.get(item["id"], set())),
            "catalog_role": (
                "linked_projected_evidence"
                if item["id"] in links
                else "official_reference_only"
            ),
        }
        for item in OFFICIAL_SOURCE_REGISTRY
    ]


def _methodology() -> dict[str, Any]:
    return {
        "status": "structural",
        "projection": (
            "Pure projection of a completed Seiche snapshot; no request-triggered "
            "collection, repository scan, model fitting, or imputation."
        ),
        "boundedness": {
            "chart_history_included": False,
            "raw_history_arrays_included": False,
            "money_market_sections_max": len(_MONEY_SECTION_IDS),
            "metrics_per_section_max": _MAX_METRICS_PER_SECTION,
            "forex_leaders_max": _MAX_FOREX_LEADERS,
            "network_edges_max": _MAX_NETWORK_EDGES,
            "capital_cards_max": _MAX_CAPITAL_CARDS,
            "china_macro_series_max": 4,
        },
        "data_minimization": (
            "Only named current-value and structural fields are projected; "
            "chart/history-shaped fields are omitted recursively. This is a "
            "bounded output contract, not a per-record licensing audit or "
            "rights-enforcement claim."
        ),
        "source_catalog_boundary": (
            "Official source entries are references. used_in_snapshot=true and "
            "projection_paths provide claim linkage only where the projected "
            "snapshot exposes a defensible publisher connection."
        ),
        "evidence_classes": dict(STATUS_DEFINITIONS),
        "claim_boundary": (
            "Descriptive and diagnostic context only. Derived associations and "
            "proxies are not causal estimates, forecasts, probabilities, "
            "investment advice, or executable prices."
        ),
        "freshness": (
            "Snapshot generation time and domain/source as-of clocks remain "
            "separate. A recent response does not make a dated observation live."
        ),
    }


def _domain_summary(name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = {
        "id": name,
        "status": payload.get("status", "unavailable"),
        "as_of": payload.get("as_of"),
    }
    if name == "money_markets":
        summary["reading"] = payload.get("plain_language")
        summary["coverage"] = payload.get("coverage")
    elif name == "forex":
        summary["reading"] = _object(payload.get("headline")).get("verdict")
        summary["coverage"] = {
            "currency_leaders": len(payload.get("currencies") or []),
            "harbors": len(payload.get("harbors") or []),
            "basins": len(payload.get("basins") or []),
        }
    elif name == "capital_markets":
        summary["reading"] = _object(
            _object(payload.get("risk_context")).get("market_vs_plumbing")
        ).get("reading")
        summary["coverage"] = {
            "positioning_blocks": sum(
                item.get("status") != "unavailable"
                for item in payload.get("positioning") or []
                if isinstance(item, Mapping)
            ),
            "primary_market_blocks": sum(
                item.get("status") != "unavailable"
                for item in payload.get("primary_market") or []
                if isinstance(item, Mapping)
            ),
        }
    else:
        summary["reading"] = payload.get("reading")
        summary["coverage"] = {
            "series_catalogued": len(payload.get("series_catalog") or []),
            "signed_revision_available": payload.get("available") is True,
            "values_published": False,
        }
    return summary


def _world_coverage(domain_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    available = [
        summary for summary in domain_summaries if summary["status"] != "unavailable"
    ]
    status_counts = {status: 0 for status in WORLD_MARKETS_STATUSES}
    for summary in domain_summaries:
        status = str(summary["status"])
        status_counts[status if status in status_counts else "unavailable"] += 1
    status_counts["structural"] += 1
    status_counts["restricted"] += 1
    return {
        "domains": domain_summaries,
        "available_domains": len(available),
        "declared_domains": len(domain_summaries),
        "status_counts": status_counts,
        "boundaries": [
            {
                "status": "restricted",
                "category": "history_and_non_allowlisted_fields",
                "included": False,
                "reason": (
                    "The projection is a data-minimized whitelist and excludes "
                    "chart/history arrays. This boundary is not a per-record "
                    "licensing determination."
                ),
            },
            {
                "status": "structural",
                "category": "official_source_registry",
                "included": True,
                "reason": (
                    "Catalog inclusion identifies an official reference and "
                    "does not assert use in this snapshot; inspect "
                    "used_in_snapshot and projection_paths."
                ),
            },
        ],
    }


def _world_clocks(
    snapshot: Mapping[str, Any],
    domain_summaries: list[dict[str, Any]],
    evaluation_asof: Any,
) -> dict[str, Any]:
    clocks = [
        summary.get("as_of")
        for summary in domain_summaries
        if isinstance(summary.get("as_of"), str)
    ]
    return {
        "snapshot_generated_at": snapshot.get("generated_at"),
        "evaluation_at": (
            evaluation_asof.isoformat()
            if hasattr(evaluation_asof, "isoformat")
            else evaluation_asof
        ),
        "latest_domain_as_of": max(clocks, default=None),
        "domains": {
            summary["id"]: summary.get("as_of") for summary in domain_summaries
        },
        "excluded_from_observation_clocks": ["china_macro.knowledge_time"],
        "boundary": "Response time never advances a source or observation as-of clock.",
    }


def _selection_state(
    selector: str,
    domains: Mapping[str, Mapping[str, Any]],
    latest_domain_as_of: Any,
    china_macro: Mapping[str, Any],
) -> tuple[bool, str, Any]:
    if selector == "china_macro":
        status = str(china_macro.get("status", "structural"))
        return True, status, None
    if selector in domains:
        selected = domains[selector]
        status = str(selected.get("status", "unavailable"))
        available = status != "unavailable"
        return available, status if available else "unavailable", selected.get("as_of")
    if selector in {"sources", "methodology"}:
        return True, "structural", None
    available = any(
        domain.get("status") != "unavailable" for domain in domains.values()
    )
    return available, "derived" if available else "unavailable", latest_domain_as_of


def _base(
    snapshot: Mapping[str, Any],
    selector: str,
    domains: Mapping[str, Mapping[str, Any]],
    evaluation_asof: Any,
    china_macro: Mapping[str, Any],
) -> dict[str, Any]:
    domain_summaries = [
        _domain_summary(name, domains[name]) for name in _WORLD_DOMAIN_IDS
    ]
    coverage = _world_coverage(domain_summaries)
    clocks = _world_clocks(snapshot, domain_summaries, evaluation_asof)
    available, status, selected_as_of = _selection_state(
        selector,
        domains,
        clocks["latest_domain_as_of"],
        china_macro,
    )
    clocks["selected_evidence_as_of"] = selected_as_of
    return {
        "ok": bool(available),
        "schema": WORLD_MARKETS_SCHEMA,
        "status": status,
        "selection": selector,
        "generated_at": snapshot.get("generated_at"),
        "as_of": selected_as_of,
        "clocks": clocks,
        "context_only": True,
        "chart_history_included": False,
        "available_selectors": list(WORLD_MARKETS_SELECTORS),
        "canonical_urls": dict(CANONICAL_URLS),
        "citation": {
            "publisher": "Seiche",
            "title": "Seiche World Markets",
            "canonical_url": CANONICAL_URLS["world_markets"],
            "topic_url": CANONICAL_URLS.get(selector, CANONICAL_URLS["world_markets"]),
            "api_url": CANONICAL_URLS["api"],
            "generated_at": snapshot.get("generated_at"),
            "evidence_as_of": selected_as_of,
        },
        "scope": {
            "coverage_claim": "curated_partial_non_exhaustive",
            "included": [*_WORLD_DOMAIN_IDS, "china_macro"],
            "not_claimed": [
                "every jurisdiction, currency, security, venue, or issuer",
                "a consolidated real-time market data feed",
                "executable prices, depth, or transaction-cost estimates",
                "investment advice or a complete capital-markets taxonomy",
            ],
        },
        "coverage": coverage,
        "status_definitions": dict(STATUS_DEFINITIONS),
        "disclaimer": "Research context, not investment advice.",
    }


def project_world_markets(
    snapshot: Mapping[str, Any],
    *,
    selector: str = "all",
    evaluation_asof: Any = None,
    china_macro_context: object | None = None,
    china_economic_context: object | None = None,
) -> dict[str, Any]:
    """Project one completed snapshot into a selector-bounded public contract."""

    if selector not in WORLD_MARKETS_SELECTORS:
        raise ValueError(
            "selector must be one of: " + ", ".join(WORLD_MARKETS_SELECTORS)
        )
    if not isinstance(snapshot, Mapping):
        return unavailable_world_markets(
            selector=selector,
            reason="no completed snapshot is available",
            china_macro_context=china_macro_context,
            china_economic_context=china_economic_context,
        )

    # A standalone China response is an independent metadata-only evidence
    # surface. Do not let a caller-supplied market snapshot lend it freshness,
    # domain summaries, or a generation clock. The combined `all` selector
    # deliberately retains the completed market snapshot.
    projection_snapshot = {} if selector == "china_macro" else snapshot
    evaluation_clock = (
        None
        if selector == "china_macro"
        else evaluation_asof or projection_snapshot.get("generated_at")
    )
    domains = {
        "money_markets": _money_markets(
            projection_snapshot,
            evaluation_asof=evaluation_clock,
        ),
        "forex": _forex(projection_snapshot),
        "capital_markets": _capital_markets(projection_snapshot),
    }
    china_macro = _china_macro(china_macro_context, china_economic_context)
    out = _base(
        projection_snapshot,
        selector,
        domains,
        evaluation_clock,
        china_macro,
    )
    if selector == "summary":
        out["summary"] = {"domains": out["coverage"]["domains"]}
    elif selector in domains:
        out[selector] = domains[selector]
    elif selector == "china_macro":
        out["china_macro"] = china_macro
    elif selector == "sources":
        out["sources"] = _source_registry({**domains, "china_macro": china_macro})
    elif selector == "methodology":
        out["methodology"] = _methodology()
    else:
        out.update(domains)
        out["china_macro"] = china_macro
        out["sources"] = _source_registry({**domains, "china_macro": china_macro})
        out["methodology"] = _methodology()
    return out


def unavailable_world_markets(
    *,
    selector: str = "all",
    reason: str = "no completed cached or persisted snapshot is available",
    china_macro_context: object | None = None,
    china_economic_context: object | None = None,
) -> dict[str, Any]:
    """Return the same typed envelope for a cold cache without implying a build."""

    if selector not in WORLD_MARKETS_SELECTORS:
        selector = "all"
    domains = {
        name: {"status": "unavailable", "as_of": None, "reason": reason}
        for name in _WORLD_DOMAIN_IDS
    }
    china_macro = _china_macro(china_macro_context, china_economic_context)
    out = _base({}, selector, domains, None, china_macro)
    if selector != "china_macro":
        out.update(ok=False, status="unavailable", reason=reason)
    if selector == "summary":
        out["summary"] = {"domains": out["coverage"]["domains"]}
    elif selector in domains:
        out[selector] = domains[selector]
    elif selector == "china_macro":
        out["china_macro"] = china_macro
    elif selector == "sources":
        out["sources"] = _source_registry({**domains, "china_macro": china_macro})
    elif selector == "methodology":
        out["methodology"] = _methodology()
    else:
        out.update(domains)
        out["china_macro"] = china_macro
        out["sources"] = _source_registry({**domains, "china_macro": china_macro})
        out["methodology"] = _methodology()
    return out
