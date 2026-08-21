"""Typed registry for independently deployable monetary-area packs."""

from __future__ import annotations

from functools import lru_cache

from seiche.markets.base import MarketPack


class UnknownMarketError(KeyError):
    pass


class MarketRegistry:
    def __init__(self, packs: tuple[MarketPack, ...] = ()) -> None:
        self._packs: dict[str, MarketPack] = {}
        for pack in packs:
            self.register(pack)

    def register(self, pack: MarketPack) -> None:
        market_id = pack.market_id.upper()
        if market_id in self._packs:
            raise ValueError(f"market pack {market_id!r} is already registered")
        self._packs[market_id] = pack

    def get(self, market_id: str) -> MarketPack:
        normalized = market_id.upper()
        try:
            return self._packs[normalized]
        except KeyError as exc:
            raise UnknownMarketError(normalized) from exc

    def list(self) -> tuple[MarketPack, ...]:
        return tuple(self._packs[key] for key in sorted(self._packs))

    def summaries(self) -> list[dict[str, object]]:
        return [pack.summary() for pack in self.list()]


@lru_cache(maxsize=1)
def default_registry() -> MarketRegistry:
    # Imports are intentionally local and declarative: registry discovery must
    # never import source adapters or perform network I/O.
    from seiche.markets.australia_aud import PACK as australia_aud
    from seiche.markets.china_cny import PACK as china_cny
    from seiche.markets.euro_eur import PACK as euro_eur
    from seiche.markets.hong_kong_hkd import PACK as hong_kong_hkd
    from seiche.markets.india_inr import PACK as india_inr
    from seiche.markets.japan_jpy import PACK as japan_jpy
    from seiche.markets.korea_krw import PACK as korea_krw
    from seiche.markets.new_zealand_nzd import PACK as new_zealand_nzd
    from seiche.markets.singapore_sgd import PACK as singapore_sgd
    from seiche.markets.uk_gbp import PACK as uk_gbp
    from seiche.markets.us_usd import PACK as us_usd

    return MarketRegistry(
        (
            us_usd,
            euro_eur,
            uk_gbp,
            japan_jpy,
            china_cny,
            hong_kong_hkd,
            india_inr,
            korea_krw,
            australia_aud,
            new_zealand_nzd,
            singapore_sgd,
        )
    )
