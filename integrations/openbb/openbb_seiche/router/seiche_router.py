"""User-facing OpenBB commands backed by the Seiche provider."""

from openbb_core.app.model.command_context import CommandContext
from openbb_core.app.model.example import APIEx, PythonEx
from openbb_core.app.model.obbject import OBBject
from openbb_core.app.provider_interface import (
    ExtraParams,
    ProviderChoices,
    StandardParams,
)
from openbb_core.app.query import Query
from openbb_core.app.router import Router

router = Router(
    prefix="",
    description="Source-clocked funding-liquidity and world-markets evidence from Seiche.",
)


@router.command(
    model="SeicheFundingStress",
    examples=[
        APIEx(parameters={"provider": "seiche"}),
        PythonEx(
            description="Read the latest public Seiche funding-stress regime.",
            code=['obb.seiche.funding_stress(provider="seiche")'],
        ),
    ],
)
async def funding_stress(
    cc: CommandContext,
    provider_choices: ProviderChoices,
    standard_params: StandardParams,
    extra_params: ExtraParams,
) -> OBBject:
    """Get Seiche's latest completed funding-stress regime and public ensemble context."""
    return await OBBject.from_query(Query(**locals()))


@router.command(
    model="SeicheWorldMarkets",
    examples=[
        APIEx(parameters={"selector": "summary", "provider": "seiche"}),
        PythonEx(
            description="Read signed metadata-only China macro provenance.",
            code=[
                'obb.seiche.world_markets(selector="china_macro", provider="seiche")'
            ],
        ),
    ],
)
async def world_markets(
    cc: CommandContext,
    provider_choices: ProviderChoices,
    standard_params: StandardParams,
    extra_params: ExtraParams,
) -> OBBject:
    """Get bounded market evidence or metadata-only China macro provenance."""
    return await OBBject.from_query(Query(**locals()))


@router.command(
    model="SeicheDataHealth",
    examples=[
        APIEx(parameters={"staleness": "aging", "provider": "seiche"}),
        PythonEx(
            description="Inspect freshness and provenance for public OFR inputs.",
            code=['obb.seiche.data_health(source="ofr", provider="seiche")'],
        ),
    ],
)
async def data_health(
    cc: CommandContext,
    provider_choices: ProviderChoices,
    standard_params: StandardParams,
    extra_params: ExtraParams,
) -> OBBject:
    """Inspect Seiche's cadence-aware public source health and provenance ledger."""
    return await OBBject.from_query(Query(**locals()))
