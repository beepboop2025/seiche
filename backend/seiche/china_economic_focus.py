"""Editorial priority policy for concise China structural context.

The Palimpsest export remains complete.  This module only selects a small,
ordered set of already-verified series for clients that need an initial reading
list rather than the full annual panel.  Selection never changes eligibility,
freshness, a score, or a gauge.
"""

from __future__ import annotations

from collections.abc import Iterable

MARKET_CHANNELS = frozenset({"money_market", "capital_market"})


def _priority_order(channel: str) -> tuple[str, ...]:
    """Return the product's editorial ordering for one market channel.

    TODO(mrinal): this small mapping is the intended owner contribution point.
    Reorder or replace series based on what Seiche analysts should see first;
    keep the distinction between slow structural context and live indicators.
    """

    priorities = {
        "money_market": (
            "cn.wdi.broad_money_growth",
            "cn.wdi.bank_credit_private_sector_share",
            "cn.wdi.consumer_price_inflation",
            "cn.wdi.current_account_balance_share",
            "cn.wdi.reserves_months_imports",
            "cn.wdi.gdp_real_growth",
        ),
        "capital_market": (
            "cn.wdi.equity_market_cap_share",
            "cn.wdi.equity_turnover_ratio",
            "cn.wdi.fdi_net_inflows_share",
            "cn.wdi.cereal_production",
            "cn.wdi.electric_power_consumption_per_capita",
            "cn.wdi.container_port_traffic",
        ),
    }
    return priorities[channel]


def featured_series(
    channel: str,
    available_series: Iterable[str],
    *,
    limit: int = 6,
) -> tuple[str, ...]:
    """Select available priority series without broadening data authority."""

    if channel not in MARKET_CHANNELS:
        raise ValueError(f"unsupported China economic channel: {channel}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 12:
        raise ValueError("featured-series limit must be an integer in [1, 12]")
    available = set(available_series)
    if any(type(series_id) is not str for series_id in available):
        raise TypeError("available series IDs must be strings")
    return tuple(
        series_id for series_id in _priority_order(channel) if series_id in available
    )[:limit]


__all__ = ["MARKET_CHANNELS", "featured_series"]
