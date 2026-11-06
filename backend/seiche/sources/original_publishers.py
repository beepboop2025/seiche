"""One fail-closed lookup for explicitly qualified original publisher series."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from seiche.sources import fed_releases, official_statistics
from seiche.sources.base import utcnow_iso

CONTRACTS = {**fed_releases.CONTRACTS, **official_statistics.CONTRACTS}
QUALIFIED_TYPES = (fed_releases.FedSeries, official_statistics.StatisticsSeries)


def load_many(mnemonics: list[str]) -> dict:
    return {**fed_releases.load_many(mnemonics), **official_statistics.load_many(mnemonics)}


def load_series(mnemonic: str):
    return load_many([mnemonic]).get(mnemonic)


async def fetch_many(client, mnemonics: list[str], faults: list[dict] | None = None) -> dict:
    fed, statistics = await asyncio.gather(fed_releases.fetch_many(client, mnemonics, faults),
                                         official_statistics.fetch_many(client, mnemonics, faults))
    return {**fed, **statistics}


def main() -> int:
    import argparse
    import httpx
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    async def collect():
        faults = []
        async with httpx.AsyncClient() as client:
            result = await fetch_many(client, list(CONTRACTS), faults)
        return result, faults

    result, faults = asyncio.run(collect())
    receipt = {
        "schema": "seiche.original-publisher-qualification.v1", "generated_at": utcnow_iso(),
        "contracted_aliases": len(CONTRACTS), "available_aliases": len(result),
        "distinct_publisher_series": len({(s.source, s.remote_id) for s in result.values()}),
        "non_null_observations_including_aliases": sum(int(s.points.count()) for s in result.values()),
        "faults": faults, "series": {m: {**s.provenance(), "first_observation": s.points.index[0].date().isoformat()}
                                    for m, s in sorted(result.items())},
        "scope": "Qualified individual series. Existing mixed-source product and publication gates remain unchanged.",
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"contracted": len(CONTRACTS), "available": len(result), "faults": len(faults), "receipt": str(args.receipt)}))
    return 0 if len(result) == len(CONTRACTS) and not faults else 1


if __name__ == "__main__":
    raise SystemExit(main())
