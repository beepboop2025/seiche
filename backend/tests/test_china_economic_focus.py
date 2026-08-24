"""Business-policy tests for concise China economic context."""

from __future__ import annotations

import pytest

from seiche.china_economic_focus import featured_series


def test_featured_series_preserves_editorial_order_and_filters_availability() -> None:
    available = {
        "cn.wdi.gdp_real_growth",
        "cn.wdi.consumer_price_inflation",
        "cn.wdi.broad_money_growth",
    }

    assert featured_series("money_market", available) == (
        "cn.wdi.broad_money_growth",
        "cn.wdi.consumer_price_inflation",
        "cn.wdi.gdp_real_growth",
    )


def test_featured_series_is_bounded_and_rejects_unknown_channels() -> None:
    available = {
        "cn.wdi.equity_market_cap_share",
        "cn.wdi.equity_turnover_ratio",
        "cn.wdi.fdi_net_inflows_share",
    }

    assert featured_series("capital_market", available, limit=2) == (
        "cn.wdi.equity_market_cap_share",
        "cn.wdi.equity_turnover_ratio",
    )
    with pytest.raises(ValueError, match="unsupported"):
        featured_series("fx", available)
    with pytest.raises(ValueError, match="limit"):
        featured_series("capital_market", available, limit=0)
