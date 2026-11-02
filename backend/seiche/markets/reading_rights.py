"""Whether a market pack's published reading may be served, from declared rights alone.

The universal kernel (``materialize.build_local_products``) reads only the
instruments whose adapter and instrument policy are derivable, so a kernel
reading depends on nothing else. A bridged reading copies another product
instead: the US-USD legacy parity bridge (``us_usd.materialize``) copies the v1
board, so it depends on every v1 input (``seiche.config.ALL_SERIES``). An input
the pack maps keeps its adapter's declaration; an input it does not map has no
declared rights at all.

The API asks this before it reads any sealed snapshot, so a snapshot sealed
under an older or wrong visibility stamp cannot reopen a restricted reading.
"""

from __future__ import annotations

from seiche.domain.observation import RedistributionStatus
from seiche.markets.base import MarketPack
from seiche.markets.materialize import DERIVABLE_REDISTRIBUTION, _public_instrument_ids

# Calibrations whose reading is copied from the v1 board rather than computed
# by the universal kernel from canonical rows.
BRIDGED_CALIBRATIONS = frozenset({"us-usd-legacy-parity-v1"})


def reading_dependencies(pack: MarketPack) -> dict[str, RedistributionStatus | None]:
    """Each input of the pack's reading with its declared redistribution status.

    Keys are v1 mnemonics for a bridged reading and instrument IDs for a kernel
    reading. ``None`` marks an input the pack does not declare.
    """

    if pack.calibration_id in BRIDGED_CALIBRATIONS:
        from seiche.config import ALL_SERIES

        by_mnemonic = {item.mnemonic: item for item in pack.instruments}
        return {
            mnemonic: (
                pack.adapter_map[by_mnemonic[mnemonic].source_adapter_id].redistribution_status
                if mnemonic in by_mnemonic
                else None
            )
            for mnemonic in sorted(ALL_SERIES)
        }
    public = set(_public_instrument_ids(pack))
    return {
        item.instrument_id: pack.adapter_map[item.source_adapter_id].redistribution_status
        for item in pack.instruments
        if item.instrument_id in public
    }


def reading_restricted(pack: MarketPack) -> bool:
    """True when any input of the reading is undeclared or outside ALLOWED/DERIVED_ONLY."""

    dependencies = reading_dependencies(pack)
    if pack.calibration_id in BRIDGED_CALIBRATIONS and not dependencies:
        return True
    return any(status not in DERIVABLE_REDISTRIBUTION for status in dependencies.values())
