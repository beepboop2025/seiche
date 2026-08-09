"""Legacy source specs owned by the US-USD pack during v1 migration.

The v1 assembler still consumes these lists directly. Keeping the aliases in
``seiche.config`` preserves byte-for-byte legacy behavior while moving market
knowledge out of the universal configuration module.
"""

from seiche.markets.base import SourceSeriesSpec


FRED_SERIES = [
    SourceSeriesSpec("WALCL", "fred", "WALCL", "Fed total assets (H.4.1)", "$M", "W", 720),
    SourceSeriesSpec(
        "WRESBAL",
        "fred",
        "WRESBAL",
        "Reserve balances with Federal Reserve Banks",
        "$M",
        "W",
        720,
    ),
    SourceSeriesSpec(
        "WTREGEN",
        "fred",
        "WTREGEN",
        "Treasury General Account (weekly avg)",
        "$M",
        "W",
        720,
    ),
    SourceSeriesSpec("RRPONTSYD", "fred", "RRPONTSYD", "ON RRP take-up", "$B", "D", 360),
    SourceSeriesSpec("IORB", "fred", "IORB", "Interest on reserve balances", "%", "D", 720),
    SourceSeriesSpec(
        "SRF_CEILING",
        "fred",
        "DFEDTARU",
        "Fed funds target range top (equals the SRF offering rate since Jul 2021)",
        "%",
        "D",
        720,
    ),
    SourceSeriesSpec(
        "WCURCIR",
        "fred",
        "WCURCIR",
        "Currency in circulation (H.4.1)",
        "$M",
        "W",
        720,
    ),
    SourceSeriesSpec(
        "IOER",
        "fred",
        "IOER",
        "Interest on excess reserves (pre-2021 splice leg)",
        "%",
        "D",
        100000,
    ),
    SourceSeriesSpec("EFFR", "fred", "EFFR", "Effective federal funds rate", "%", "D", 360),
    SourceSeriesSpec(
        "SOFR", "fred", "SOFR", "Secured overnight financing rate", "%", "D", 360
    ),
    SourceSeriesSpec("GDP", "fred", "GDP", "Nominal GDP (SAAR)", "$B", "Q", 10080),
    SourceSeriesSpec(
        "DISCOUNT_WINDOW",
        "fred",
        "WLCFLPCL",
        "Discount window primary credit (Wed level)",
        "$M",
        "W",
        720,
    ),
]


MARKET_SERIES = [
    SourceSeriesSpec("VIX", "fred", "VIXCLS", "CBOE VIX index", "pts", "D", 360),
    SourceSeriesSpec(
        "HY_OAS", "fred", "BAMLH0A0HYM2", "ICE BofA US High Yield OAS", "%", "D", 360
    ),
    SourceSeriesSpec(
        "IG_OAS", "fred", "BAMLC0A0CM", "ICE BofA US Corporate (IG) OAS", "%", "D", 360
    ),
    SourceSeriesSpec("DGS2", "fred", "DGS2", "2y Treasury constant maturity yield", "%", "D", 360),
    SourceSeriesSpec(
        "DGS10", "fred", "DGS10", "10y Treasury constant maturity yield", "%", "D", 360
    ),
    SourceSeriesSpec(
        "DGS30", "fred", "DGS30", "30y Treasury constant maturity yield", "%", "D", 360
    ),
    SourceSeriesSpec(
        "TB3M", "fred", "DTB3", "3-month T-bill secondary market rate", "%", "D", 360
    ),
    SourceSeriesSpec(
        "TB4W", "fred", "DTB4WK", "4-week T-bill secondary market rate", "%", "D", 360
    ),
    SourceSeriesSpec("SP500", "fred", "SP500", "S&P 500 index", "pts", "D", 360),
    SourceSeriesSpec(
        "NFCI",
        "fred",
        "NFCI",
        "Chicago Fed National Financial Conditions Index",
        "z",
        "W",
        720,
    ),
]
