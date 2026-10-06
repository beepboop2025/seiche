"""India–UAE funding evidence and explicit gold-inventory cost scenarios.

The desk reads completed source stores. Scenarios use caller assumptions, never
infer executable quotes, and never write customer inputs to a public data store.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from seiche import market_workbench, store
from seiche.markets.atlas import build_global_money_market_atlas
from seiche.markets.registry import default_registry
from seiche.repository import get_repository
from seiche.domain.observation import RedistributionStatus

SCHEMA = "seiche.gift-city.v1"
CARRY_SCHEMA = "seiche.gold-carry.v1"
TROY_OUNCE_GRAMS = Decimal("31.1034768")
DECIMAL_FIELDS = ("quantity_kg", "fineness", "price_usd_per_oz", "annual_rate_pct", "fx_inr_per_usd", "fees_usd")
CARRY_INPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {**{key: {"type": "string", "pattern": r"^[0-9]{1,12}(\.[0-9]{1,12})?$", "maxLength": 25} for key in DECIMAL_FIELDS},
                   "days": {"type": "integer", "minimum": 0, "maximum": 3660},
                   "day_count": {"type": "integer", "enum": [360, 365], "default": 360}},
    "required": [*DECIMAL_FIELDS, "days"],
}
SOURCES = [
    {"title": "IFSCA metals and commodities: current circulars", "url": "https://www.ifsca.gov.in/Pages/Contents/Metals%20and%20Commodities"},
    {"title": "IIBX product and participation information", "url": "https://www.iibx.co.in/"},
    {"title": "CBUAE exchange rates for VAT obligations", "url": "https://centralbank.ae/en/forex-eibor/exchange-rates/"},
    {"title": "CBUAE DONIA methodology", "url": "https://centralbank.ae/media/kuqd0q5o/attachment-7_donia-term-sheet.pdf"},
    {"title": "RBI money market operations", "url": "https://www.rbi.org.in/Scripts/BS_ViewMMO.aspx"},
    {"title": "CFTC disaggregated commitments of traders", "url": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"},
    {"title": "CBUAE DONIA dated rate history", "url": "https://centralbank.ae/en/our-operations/monetary-policy-and-domestic-markets/"},
]


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 25 or re.fullmatch(r"[0-9]{1,12}(\.[0-9]{1,12})?", value) is None:
        raise ValueError(f"{name} must be a non-negative decimal string with at most 12 decimal places")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {name}") from exc


def gold_carry(args: object) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - set(CARRY_INPUT_SCHEMA["properties"]):
        raise ValueError("unknown gold carry input")
    if set(CARRY_INPUT_SCHEMA["required"]) - set(args):
        raise ValueError("quantity, fineness, price, annual rate, FX, fees and days are required")
    values = {key: _decimal(args[key], key) for key in DECIMAL_FIELDS}
    days, basis = args["days"], args.get("day_count", 360)
    if type(days) is not int or not 0 <= days <= 3660:
        raise ValueError("days must be an integer between 0 and 3660")
    if type(basis) is not int or basis not in (360, 365):
        raise ValueError("day_count must be 360 or 365")
    for key in ("quantity_kg", "fineness", "price_usd_per_oz", "fx_inr_per_usd"):
        if values[key] <= 0:
            raise ValueError(f"{key} must be positive")
    if values["fineness"] > 1 or values["annual_rate_pct"] > 1000:
        raise ValueError("fineness must be at most 1 and annual_rate_pct at most 1000")
    with localcontext() as ctx:
        ctx.prec = 96
        fine_grams = values["quantity_kg"] * 1000 * values["fineness"]
        ounces = fine_grams / TROY_OUNCE_GRAMS
        metal = ounces * values["price_usd_per_oz"]
        carry = metal * values["annual_rate_pct"] / 100 * Decimal(days) / basis
        total = metal + carry + values["fees_usd"]
        result = {"fine_grams": fine_grams, "fine_troy_oz": ounces,
                  "metal_value_usd": metal, "funding_cost_usd": carry,
                  "fees_usd": values["fees_usd"], "total_cost_usd": total,
                  "total_cost_inr": total * values["fx_inr_per_usd"],
                  "all_in_inr_per_gram": total * values["fx_inr_per_usd"] / fine_grams,
                  "break_even_usd_per_fine_oz": total / ounces}
        outputs = {key: format(value.quantize(Decimal("0.00000001")), "f") for key, value in result.items()}
    return {"schema": CARRY_SCHEMA, "status": "scenario", "context_only": True,
            "inputs": {**args, "day_count": basis}, "outputs": outputs,
            "assumptions": ["All prices, funding rates, FX and fees are supplied by the caller; no quote is verified or executed.",
                            f"Simple interest on metal value using ACT/{basis}; fees are paid separately and are not financed.",
                            "Quantity is gross kilograms; fineness converts it to fine gold. One troy ounce is 31.1034768 grams.",
                            "FX is INR per USD. All-in cost is per fine gram, before any unentered taxes, duties, delivery, insurance or hedging costs.",
                            "An inventory-cost scenario does not establish import eligibility, collateral eligibility, available cash or settlement certainty."],
            "eligibility": {"execution": False, "scoring": False}, "persisted": False}


def _funding(now: datetime) -> list[dict]:
    registry = default_registry()
    packs = tuple(registry.get(key) for key in ("US-USD", "IN-INR"))
    observations = {}
    failures = set()
    try:
        repository = get_repository()
    except Exception:
        repository = None
        logging.getLogger(__name__).exception("corridor funding repository unavailable")
    for pack in packs:
        if repository is None:
            failures.add(pack.market_id)
            continue
        try:
            # Bounded canonical reads preserve source rights and native clocks.
            loaded = repository.load_observations_as_of(
                pack.market_id, now, event_time=now, event_time_from=now-timedelta(days=45),
                instrument_ids=[i.instrument_id for i in pack.instruments
                                if i.mnemonic in {"SOFR", "IORB", "CALL_WAR", "RBI_POLICY_REPO", "RBI_SDF"}])
            observations[pack.market_id] = [r for r in loaded
                if r.instrument_id in pack.instrument_map
                and r.redistribution_status is RedistributionStatus.ALLOWED
                and pack.adapter_map[pack.instrument_map[r.instrument_id].source_adapter_id].redistribution_status is RedistributionStatus.ALLOWED]
        except Exception:
            # A failure in one currency cannot suppress another market's
            # independently available canonical evidence.
            failures.add(pack.market_id)
            logging.getLogger(__name__).exception("corridor funding read unavailable for %s", pack.market_id)
    atlas = build_global_money_market_atlas(packs, observations, as_of=now)
    rows = []
    for market in atlas["markets"]:
        metric = market.get("benchmark") or {}
        rows.append({"currency": market["currency"], "label": market["display_name"],
                     "instrument": metric.get("mnemonic"), "value": metric.get("value"), "unit": "%",
                     "as_of": metric.get("asof") or metric.get("event_date"), "status": metric.get("status", "UNAVAILABLE"),
                     "missed_publication_opportunities": metric.get("missed_publication_opportunities"),
                     "source": metric.get("source"), "source_url": metric.get("source_url"),
                     "reason": "The canonical funding source could not be read." if market["market_id"] in failures else None,
                     "evidence": metric})
    from seiche.sources import cbuae_donia
    rows.append(cbuae_donia.read_reference(now=now))
    return rows


def gold_positioning(blob: object, now: datetime) -> dict:
    out = {"status": "unavailable", "as_of": None, "source_url": SOURCES[5]["url"],
           "contract": "GOLD - COMMODITY EXCHANGE INC.", "cftc_contract_market_code": "088691",
           "boundary": "Weekly futures-only positioning, not spot price, venue depth, IIBX inventory or an executable quote."}
    if not isinstance(blob, dict):
        return out
    if not isinstance(blob.get("rows"), list) or any(not isinstance(row, dict) for row in blob["rows"]):
        return out
    try:
        fetched = datetime.fromisoformat(blob["fetched_at"].replace("Z", "+00:00"))
        if fetched.tzinfo is None or fetched > now:
            return out
        eligible = [r for r in blob.get("rows", []) if r.get("cftc_contract_market_code") == "088691"
                    and date.fromisoformat(r["report_date_as_yyyy_mm_dd"][:10]) <= min(now.date(), fetched.date())]
        if not eligible:
            return out
        row = max(eligible, key=lambda r: r["report_date_as_yyyy_mm_dd"])
        report_date = row["report_date_as_yyyy_mm_dd"][:10]
        fields = ["open_interest_all", "m_money_positions_long_all", "m_money_positions_short_all", "prod_merc_positions_long", "prod_merc_positions_short"]
        numbers = [_decimal(str(row[k]), k) for k in fields]
        if any(n != n.to_integral_value() for n in numbers):
            return out
        local = now.astimezone(ZoneInfo("America/New_York"))
        friday = (local - timedelta(days=local.weekday()) + timedelta(days=4)).replace(hour=15, minute=30, second=0, microsecond=0)
        if local < friday:
            friday -= timedelta(weeks=1)
        expected = friday.date() - timedelta(days=3)
        overdue = date.fromisoformat(report_date) < expected
        out.update(status="stale" if overdue else "fresh", as_of=report_date, fetched_at=blob["fetched_at"],
                   open_interest_contracts=int(numbers[0]), managed_money_net_contracts=int(numbers[1]-numbers[2]),
                   producer_net_contracts=int(numbers[3]-numbers[4]), source_publication_time=None,
                   vintage="current collected vintage; not historical publication-time knowledge",
                   freshness_basis="Nominal Friday 15:30 New York release for Tuesday positions; special CFTC releases may differ. Not a publication receipt.")
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return out
    return out


def read() -> dict:
    now = datetime.now(UTC)
    fx = market_workbench.read({"provider": "ecb", "base": "USD", "quote": "INR", "days": 90}, include_china=False)["forex"]
    rows = [{**r, "provider": "ecb", "source_url": r["sources"][0]["source_url"] if r.get("sources") else None}
            for r in fx["rows"] if r["quote_currency"] in {"INR", "EUR", "GBP", "JPY", "SGD", "CHF"}]
    # The UAE reader validates an atomic capture and never mixes its references
    # with ECB legs. It is independent of the optional DONIA funding benchmark.
    from seiche.sources import cbuae_fx
    uae = cbuae_fx.read_references(now=now)
    rows.extend(uae["rows"])
    funding = _funding(now)
    try:
        blob = store.load_blob("cftc_disagg_ballast")
    except Exception:
        blob = None
    positioning = gold_positioning(blob, now)
    return {"schema": SCHEMA, "generated_at": now.isoformat(), "context_only": True,
            "status": "partial" if any(r.get("value") is not None for r in rows + funding) or positioning["status"] != "unavailable" else "unavailable",
            "funding": funding, "forex": {"rows": rows, "reference_only": True,
                "quote_convention": "quote currency units per one base currency", "uae_capture": uae.get("capture")},
            "gold": {"positioning": positioning, "price": None,
                     "notes": ["Enter your own bullion quote in the financing scenario; the desk does not redistribute IIBX/LBMA trading prices.",
                               "Keep physical bullion, exchange futures and gold-backed tokens distinct; their settlement and liquidity differ."]},
            "sources": SOURCES,
            "methodology": ["Funding rows retain separate observation dates; a cross-country difference is not an executable arbitrage spread.",
                            "ECB and CBUAE FX references remain separate providers. Crosses join matching dates only.",
                            "CBUAE FX references support VAT valuation context, not an executable dealer quote or FX hedge.",
                            "DONIA is a mixed secured/unsecured AED overnight reference using ACT/360. Its publisher chart date is retained; actual publication time is unknown.",
                            "Use IFSCA and venue documents for current participation and delivery requirements; this desk does not determine eligibility."],
            "eligibility": {"execution": False, "scoring": False}}
