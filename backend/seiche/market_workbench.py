"""Bounded public research over cached FX and accepted Palimpsest evidence.

No request starts collection, fits an engine, or grants publication rights.
Crosses use the intersection of observation dates, never a filled chart frame.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from seiche import context_views, methodology, store
from seiche.config import ALL_SERIES, ECB_FX_CURRENCIES
from seiche.palimpsest_china_intake import PalimpsestChinaEconomicContext
from seiche.sources.base import Series

SCHEMA = "seiche.market-workbench.v1"
# The H.10 starred quotes are USD per foreign unit. Other quotes are foreign
# units per USD. EUR's legacy mnemonic is intentionally retained.
FX_MNEMONICS = {
    c: c
    for c in (
        "GBP",
        "JPY",
        "AUD",
        "CAD",
        "CHF",
        "NZD",
        "CNY",
        "INR",
        "KRW",
        "MXN",
        "BRL",
        "ZAR",
        "DKK",
        "HKD",
        "MYR",
        "NOK",
        "SEK",
        "SGD",
        "TWD",
        "THB",
        "LKR",
    )
} | {"EUR": "EURUSD"}
INVERTED = frozenset({"EUR", "GBP", "AUD", "NZD"})
ECB_MNEMONICS = {c: f"ECBFX_{c}" for c in ECB_FX_CURRENCIES}
PROVIDER_CURRENCIES = {
    "h10": tuple(sorted({"USD", *FX_MNEMONICS})),
    "ecb": tuple(sorted({"EUR", *ECB_MNEMONICS})),
}
CURRENCIES = tuple(
    sorted(set(PROVIDER_CURRENCIES["h10"]) | set(PROVIDER_CURRENCIES["ecb"]))
)
INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "provider": {"type": "string", "enum": ["h10", "ecb"], "default": "h10"},
        "base": {"type": "string", "enum": list(CURRENCIES), "default": "USD"},
        "quote": {"type": "string", "enum": list(CURRENCIES), "default": "CNY"},
        "days": {"type": "integer", "minimum": 30, "maximum": 3650, "default": 365},
        "china_series": {"type": "string", "maxLength": 120},
    },
}


def selection(args: object) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - set(INPUT_SCHEMA["properties"]):
        raise ValueError(
            "expected provider, base, quote, days and optional china_series"
        )
    out = {
        "provider": "h10",
        "base": "USD",
        "quote": "CNY",
        "days": 365,
        "china_series": "",
        **args,
    }
    if type(out["provider"]) is not str or out["provider"] not in PROVIDER_CURRENCIES:
        raise ValueError("provider must be h10 or ecb")
    for name in ("base", "quote"):
        if (
            not isinstance(out[name], str)
            or out[name] not in PROVIDER_CURRENCIES[out["provider"]]
        ):
            raise ValueError(f"{name} must be a supported currency code")
    if out["base"] == out["quote"]:
        raise ValueError("base and quote must differ")
    if type(out["days"]) is not int or not 30 <= out["days"] <= 3650:
        raise ValueError("days must be an integer between 30 and 3650")
    if not isinstance(out["china_series"], str) or (
        out["china_series"]
        and not re.fullmatch(r"cn\.wdi\.[a-z0-9_]{1,110}", out["china_series"])
    ):
        raise ValueError("china_series must be a cn.wdi series identifier")
    return out


def _clock(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _normalized(
    currency: str,
    series: Series | None,
    now: datetime,
    days: int,
    provider: str = "h10",
) -> tuple[dict[str, float], dict | None]:
    if series is None:
        return {}, None
    mnemonic = (FX_MNEMONICS if provider == "h10" else ECB_MNEMONICS)[currency]
    spec = ALL_SERIES[mnemonic]
    fetched_at = _clock(series.fetched_at)
    # Cache metadata must still match the public, reviewed H.10 registry.
    if (
        series.mnemonic != mnemonic
        or series.source != spec.source
        or series.remote_id != spec.remote_id
        or series.unit != spec.unit
        or series.freq != spec.freq
        or methodology.csv_restriction(mnemonic)
        or fetched_at is None
        or fetched_at > now
    ):
        return {}, None
    cutoff = now.date() - timedelta(days=days)
    values: dict[str, float] = {}
    for timestamp, value in series.points.items():
        try:
            day = pd.Timestamp(timestamp).date()
            number = float(value)
        except (ValueError, TypeError, OverflowError):
            continue
        if (
            not cutoff <= day <= min(now.date(), fetched_at.date())
            or not math.isfinite(number)
            or number <= 0
        ):
            continue
        key = day.isoformat()
        # An ambiguous date is not a public reference observation.
        if key in values:
            return {}, None
        normalized = (
            1.0 / number if provider == "h10" and currency in INVERTED else number
        )
        if math.isfinite(normalized) and normalized > 0:
            values[key] = normalized
    provenance = {
        "mnemonic": mnemonic,
        "source_id": spec.remote_id,
        "publisher": "Board of Governors of the Federal Reserve System",
        "transport": "Federal Reserve Bank of St. Louis FRED",
        "source_url": f"https://fred.stlouisfed.org/series/{spec.remote_id}",
        "publisher_url": "https://www.federalreserve.gov/releases/h10/current/",
        "raw_unit": spec.unit,
        "raw_quote_convention": "USD per currency"
        if currency in INVERTED
        else "currency per USD",
        "fetched_at": series.fetched_at,
        "source_publication_time": None,
        "redistribution_status": "allowed",
        "vintage": "current_cached_vintage",
    }
    if provider == "ecb":
        provenance.update(
            publisher="European Central Bank",
            transport="ECB euro foreign exchange reference XML",
            source_url="https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml",
            publisher_url="https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html",
            raw_quote_convention="currency per EUR",
            attribution="Source: European Central Bank. ECB reference data is freely available from the ECB; calculated crosses and statistics are Seiche transformations.",
            terms_url="https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        )
    return dict(sorted(values.items())), provenance


def _pair(
    base: str,
    quote: str,
    data: Mapping[str, dict[str, float]],
    sources: Mapping[str, dict | None],
    now: datetime,
    provider: str = "h10",
) -> tuple[dict, list[dict]]:
    anchor = "USD" if provider == "h10" else "EUR"
    legs = [currency for currency in (base, quote) if currency != anchor]
    dates = set(data.get(legs[0], {}))
    for leg in legs[1:]:
        dates.intersection_update(data.get(leg, {}))
    history = []
    for day in sorted(dates):
        numerator = 1.0 if quote == anchor else data[quote][day]
        denominator = 1.0 if base == anchor else data[base][day]
        value = numerator / denominator
        if math.isfinite(value) and value > 0:
            history.append({"date": day, "value": value})
    latest = history[-1] if history else None
    age = (now.date() - date.fromisoformat(latest["date"])).days if latest else None
    grace = 7 if provider == "h10" else 3
    status = (
        "unavailable"
        if age is None
        else "fresh"
        if age <= grace
        else "aging"
        if age <= grace * 2
        else "stale"
        if age <= grace * 6
        else "dead"
    )
    observed = (base == "USD" and quote not in INVERTED) or (
        quote == "USD" and base in INVERTED
    )
    if provider == "ecb":
        observed = base == "EUR"

    def change(periods: int) -> float | None:
        value = (
            (history[-1]["value"] / history[-periods - 1]["value"] - 1) * 100
            if len(history) > periods
            else None
        )
        return value if value is not None and math.isfinite(value) else None

    vol = None
    # A long gap must not masquerade as a single daily return.
    if len(history) >= 21:
        sample = history[-21:]
        if all(
            (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days <= 7
            for a, b in zip(sample, sample[1:])
        ):
            returns = [
                math.log(b["value"]) - math.log(a["value"])
                for a, b in zip(sample, sample[1:])
            ]
            mean = sum(returns) / len(returns)
            vol = (
                math.sqrt(sum((r - mean) ** 2 for r in returns) / len(returns) * 252)
                * 100
            )
    return {
        "pair": f"{base}/{quote}",
        "base_currency": base,
        "quote_currency": quote,
        "provider": provider,
        "value": latest["value"] if latest else None,
        "as_of": latest["date"] if latest else None,
        "status": status,
        "evidence_status": ("observed" if observed else "derived")
        if latest
        else "unavailable",
        "unit": f"{quote} per {base}",
        "observation_count": len(history),
        "age_days": age,
        **{f"change_{n}obs_pct": change(n) for n in (1, 5, 20, 60)},
        "realized_vol_20obs_pct": vol,
        "sources": [sources[c] for c in legs if sources.get(c)],
        "reason": None
        if latest
        else "No eligible matching-date observations for the selected window.",
    }, history


CHINA_CHANNELS = (
    (
        "funding",
        "Domestic money and credit",
        "Money growth, private credit and inflation describe the domestic funding backdrop; annual values cannot establish today's repo pressure.",
        ("broad_money", "bank_credit", "inflation"),
    ),
    (
        "external",
        "External balance and yuan demand",
        "Current-account, trade and investment evidence describes potential foreign-currency supply and demand; it does not measure today's spot order flow.",
        ("current_account", "exports", "imports", "trade", "fdi"),
    ),
    (
        "reserves",
        "Reserves and external buffers",
        "Reserve levels and import cover describe external buffers; a stock change alone cannot establish intervention.",
        ("reserves", "external_debt"),
    ),
    (
        "activity",
        "Growth and policy backdrop",
        "Growth and investment place monetary and currency policy in context. Source vintages and annual frequency remain visible.",
        ("gdp", "investment", "industry", "manufacturing"),
    ),
)


def _china(context: object, selected: str, usd_cny: dict) -> dict:
    out: dict[str, Any] = {
        "status": "unavailable",
        "reason": "No owner-accepted Palimpsest economic export is configured.",
        "economic_context": {},
        "series": [],
        "selected_series": selected or None,
        "history": [],
        "fx": usd_cny,
        "channels": [],
        "gaps": [
            {
                "id": "cfets",
                "label": "DR007 / FDR007 / SHIBOR",
                "status": "restricted",
                "reason": "Publisher rights are required for public benchmark values. FDR007 and DR007 are distinct series.",
            },
            {
                "id": "cnh",
                "label": "Offshore CNH and CNY–CNH basis",
                "status": "unavailable",
                "reason": "No entitled, matching-time offshore quote is available; a CNY reference is not CNH.",
            },
            {
                "id": "forwards",
                "label": "FX forwards, swaps and NDFs",
                "status": "unavailable",
                "reason": "No entitled tenor curves or executable dealer quotes are configured.",
            },
            {
                "id": "fixing",
                "label": "PBOC central parity deviation",
                "status": "unavailable",
                "reason": "The selected CNY reference is not the PBOC central parity fixing.",
            },
        ],
    }
    if isinstance(context, PalimpsestChinaEconomicContext) and context.owner_attested:
        economic = context.to_dict()
        if economic.get("rights", {}).get("decision") == "allowed":
            economic["publication_status"] = "provisional"
            rows = [
                row.public_record(accepted_at=context.accepted_at)
                for row in context.current_observations
            ]
            for row in rows:
                row["label"] = (
                    row["series_id"]
                    .removeprefix("cn.wdi.")
                    .replace("_", " ")
                    .capitalize()
                )
            selected = selected or (rows[0]["series_id"] if rows else "")
            if selected and selected not in {row["series_id"] for row in rows}:
                raise ValueError("china_series is not present in the accepted export")
            by_period = {}

            def rank(row: dict) -> tuple:
                return (
                    row["revision"],
                    row["released_at"],
                    row["collected_at"],
                    row["observation_id"],
                )

            for observation in context.observations:
                if observation.series_id != selected:
                    continue
                row = observation.public_record(accepted_at=context.accepted_at)
                key = row["period_end"]
                if key not in by_period or rank(row) > rank(by_period[key]):
                    by_period[key] = row
            history = [by_period[key] for key in sorted(by_period)]
            out.update(
                status="structural",
                reason=None,
                economic_context=economic,
                series=rows,
                selected_series=selected or None,
                history=history[-100:],
                history_total=len(history),
                history_truncated=len(history) > 100,
                history_vintage="latest_revision_in_accepted_export",
            )
    for identifier, title, reading, tokens in CHINA_CHANNELS:
        ids = [
            row["series_id"]
            for row in out["series"]
            if any(t in row["series_id"] for t in tokens)
        ]
        out["channels"].append(
            {
                "id": identifier,
                "title": title,
                "reading": reading,
                "series_ids": ids,
                "available_series": len(ids),
            }
        )
    return out


def project(
    series: Mapping[str, Series],
    args: object = None,
    *,
    china_context: object = None,
    evaluated_at: datetime | None = None,
) -> dict:
    query = selection({} if args is None else args)
    now = evaluated_at or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("evaluation clock must be timezone-aware")
    now = now.astimezone(UTC)
    provider = query["provider"]
    mnemonics = FX_MNEMONICS if provider == "h10" else ECB_MNEMONICS
    data, sources = {}, {}
    for currency, mnemonic in mnemonics.items():
        data[currency], sources[currency] = _normalized(
            currency, series.get(mnemonic), now, query["days"], provider
        )
    rows = []
    selected_history = []
    for quote in PROVIDER_CURRENCIES[provider]:
        if quote == query["base"]:
            continue
        row, history = _pair(query["base"], quote, data, sources, now, provider)
        rows.append(row)
        if quote == query["quote"]:
            selected_history = history
    usd_cny, _ = _pair("USD", "CNY", data, sources, now, provider)
    china = _china(china_context, query["china_series"], usd_cny)
    available = sum(row["value"] is not None for row in rows)
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "context_only": True,
        "status": "available"
        if available == len(rows) and china["series"]
        else "partial"
        if available or china["series"]
        else "unavailable",
        "selection": query,
        "forex": {
            "base_currency": query["base"],
            "quote_currency": query["quote"],
            "quote_convention": "quote currency units per one base currency",
            "reference_only": True,
            "provider": provider,
            "currencies": list(PROVIDER_CURRENCIES[provider]),
            "rows": rows,
            "history": selected_history,
            "coverage": {
                "declared_pairs": len(rows),
                "available_pairs": available,
                "missing_pairs": len(rows) - available,
                "fresh_pairs": sum(r["status"] == "fresh" for r in rows),
                "returned_observations": len(selected_history),
            },
            "methodology": [
                (
                    "Federal Reserve H.10 daily reference observations delivered through FRED."
                    if provider == "h10"
                    else "Source: European Central Bank. ECB reference data is freely available from the ECB; calculated crosses and statistics are Seiche transformations."
                )
                + " Not executable quotes, intraday data, or forward rates.",
                f"Crosses divide quote-per-{'USD' if provider == 'h10' else 'EUR'} by base-per-{'USD' if provider == 'h10' else 'EUR'} on exact shared observation dates; no forward filling or mixed-date quote construction.",
                "Changes use 1, 5, 20 and 60 observed intervals. Positive means the quote currency weakened against the base currency.",
                "Volatility uses 20 log returns, population standard deviation and sqrt(252) annualization; any interval longer than seven days makes it unavailable.",
                (
                    "Age states use seven calendar days for fresh, fourteen for aging, forty-two for stale, then dead, allowing for H.10 weekly delivery."
                    if provider == "h10"
                    else "Age states use three calendar days for fresh, six for aging, eighteen for stale, then dead for ECB daily reference data."
                )
                + " These are reference-data age labels, not market-open checks.",
                "History is the current cached vintage, not a point-in-time backtest. Fetch and response clocks do not advance observation dates.",
            ],
        },
        "china": china,
        "money_markets": {
            "catalog_url": "/api/v2/markets",
            "series_url_template": "/api/v2/markets/{market_id}/series",
        },
        "eligibility": {"scoring": False, "forecast": False, "execution": False},
    }


def read(args: object = None) -> dict:
    query = selection({} if args is None else args)
    now = datetime.now(UTC)
    mnemonics = FX_MNEMONICS if query["provider"] == "h10" else ECB_MNEMONICS
    captures = {}
    failure = None
    window = {
        "start": (now.date() - timedelta(days=query["days"])).isoformat(),
        "end": now.date().isoformat(),
    }
    try:
        if query["provider"] == "ecb":
            cached, captures = store.load_series_window_snapshot(
                tuple(mnemonics.values()),
                **window,
                blob_keys=("ecb_fx:latest", "ecb_fx:full-history"),
            )
            latest = _public_capture(captures.get("ecb_fx:latest"), now)
            if latest is None:
                cached = {}
                failure = "ECB capture metadata is unavailable or invalid."
            else:
                cached = {
                    key: value
                    for key, value in cached.items()
                    if _clock(value.fetched_at) == _clock(latest["fetched_at"])
                    and value.asof == latest["last_observation_date"]
                }
        else:
            cached = store.load_series_window(tuple(mnemonics.values()), **window)
    except (sqlite3.Error, ValueError):
        cached = {}
        failure = "The cached source window could not be verified."
    # Invalid configured acceptance/rights raises; it must never masquerade as
    # an accepted empty or refreshed economic panel.
    china = context_views.public_china_economic_context()
    result = project(cached, query, china_context=china, evaluated_at=now)
    if failure:
        result["forex"]["reason"] = failure
    if query["provider"] == "ecb":
        latest = _public_capture(captures.get("ecb_fx:latest"), now)
        full = _public_capture(captures.get("ecb_fx:full-history"), now)
        result["forex"]["capture_documents"] = {
            "latest": latest,
            "full_history": full,
            "boundary": "Each hash covers its named downloaded document and date interval, not the entire merged history. Retired currencies in full-history captures are excluded from current coverage.",
        }
        if latest:
            for row in [*result["forex"]["rows"], result["china"]["fx"]]:
                for source in row["sources"]:
                    source["source_url"] = latest["source_url"]
                    source["latest_capture_sha256"] = latest["evidence_sha256"]
                    source["capture_interval"] = [
                        latest["first_observation_date"],
                        latest["last_observation_date"],
                    ]
    return result


def _public_capture(value: object, now: datetime) -> dict | None:
    if not isinstance(value, dict):
        return None
    suffix = {"daily": "daily", "history90d": "hist-90d", "history": "hist"}.get(
        str(value.get("mode"))
    )
    clock = _clock(value.get("fetched_at"))
    if (
        value.get("schema") != "seiche.ecb-fx-capture.v1"
        or value.get("source") != "ecb_fx"
        or value.get("base_currency") != "EUR"
        or suffix is None
        or value.get("source_url")
        != f"https://www.ecb.europa.eu/stats/eurofxref/eurofxref-{suffix}.xml"
        or clock is None
        or clock > now
        or not isinstance(value.get("evidence_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["evidence_sha256"]) is None
    ):
        return None
    try:
        first = date.fromisoformat(value["first_observation_date"])
        last = date.fromisoformat(value["last_observation_date"])
        if not date(1999, 1, 1) <= first <= last <= clock.date():
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {
        key: value.get(key)
        for key in (
            "schema",
            "source",
            "source_url",
            "evidence_sha256",
            "fetched_at",
            "source_publication_time",
            "byte_count",
            "first_observation_date",
            "last_observation_date",
            "observation_count",
            "mode",
        )
    }
