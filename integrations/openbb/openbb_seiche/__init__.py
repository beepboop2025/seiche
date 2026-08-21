"""Seiche provider extension for OpenBB."""

from openbb_core.provider.abstract.provider import Provider

from openbb_seiche.models.data_health import SeicheDataHealthFetcher
from openbb_seiche.models.funding_stress import SeicheFundingStressFetcher
from openbb_seiche.models.world_markets import SeicheWorldMarketsFetcher

seiche_provider = Provider(
    name="seiche",
    website="https://seiche.info",
    description=(
        "Public, source-clocked funding-liquidity and world-markets evidence "
        "with explicit observed, derived, restricted, and unavailable boundaries."
    ),
    credentials=[],
    fetcher_dict={
        "SeicheFundingStress": SeicheFundingStressFetcher,
        "SeicheWorldMarkets": SeicheWorldMarketsFetcher,
        "SeicheDataHealth": SeicheDataHealthFetcher,
    },
    repr_name="Seiche",
)

__all__ = ["seiche_provider"]
