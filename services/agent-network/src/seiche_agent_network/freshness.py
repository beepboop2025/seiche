"""Conservative source-clock inspection and scheduled public MCP probes.

This module inspects metadata; it does not collect financial values, repair a
source, remove rights gates, or infer observation freshness from HTTP latency.
Run ``python -m seiche_agent_network.freshness --output /private/report.json``.
"""
import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time as day_time, timedelta, timezone
from functools import lru_cache
import fcntl
import hashlib
import json
import math
import os
import stat
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .gateway import PRODUCTS
from .upstream_auth import ProbeAuth, load_undertow_credential, read_private_json

# These match Seiche's explicit generic publication contracts. Series with a
# calendar-based override are deliberately NOT evaluated using a generic age.
SEICHE_GRACE_DAYS = {"D": 4, "W": 10, "M": 45, "ML": 100, "Q": 120, "QL": 300}
PREFERRED_TOOLS = {product: ("data_health",) for product in PRODUCTS}
PREFERRED_TOOLS["palimpsest"] = ("query_economic_observations", "research_catalog")
PREFERRED_TOOLS["undertow"] = ("exit_cost",)
PREFERRED_TOOLS["liquilens"] = ("crypto_regime_board",)
PREFERRED_TOOLS["narcoscope"] = ("get_market_catalog",)
PREFERRED_TOOLS["noisefloor"] = ()  # Caller-owned calculations, not a hosted market feed.
PREFERRED_TOOLS["wake"] = ("wake_economy_now",)
FUNDING_CORE_SOURCES = ("fred:SOFR", "fred:EFFR", "fred:IORB")
# Exact native identities in backend/seiche/sources/publication.py and
# backend/seiche/trade_safety.py. Caller-provided labels cannot admit a policy.
H10_IDS = frozenset({"DEXUSUK", "DEXUSAL", "DEXCAUS", "DEXSZUS", "DEXMXUS", "DEXBZUS", "DEXSFUS", "DEXUSNZ", "DEXDNUS", "DEXHKUS", "DEXMAUS", "DEXNOUS", "DEXSDUS", "DEXSIUS", "DEXTAUS", "DEXTHUS", "DEXSLUS", "DEXCHUS", "DEXJPUS", "DEXKOUS", "DEXUSEU", "DEXINUS", "DTWEXBGS", "DTWEXAFEGS", "DTWEXEMEGS"})
H41_IDS = frozenset({"WALCL", "WRESBAL", "WTREGEN", "WCURCIR", "WLCFLPCL", "SWPT", "WLRRAFOIAL", "WMTSECL1", "H41RESPPALGTRFNWW"})
RETIRED_IDENTITIES = {
    ("fred", "IOER", "IOER", "D"): ("2021-07-28", "2021-07-29", "IORB"),
    ("fred", "TED", "TEDRATE", "D"): ("2022-01-21", "2022-01-31", None),
}
UNDERTOW_VENUES = frozenset({"binance", "bitfinex", "coinbase", "gemini", "kraken", "okx"})
UNDERTOW_MAX_AGE = 7200
ADMISSION_CACHE_SECONDS = 3600
_NY = ZoneInfo("America/New_York")


def _date(value):
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except ValueError:
        return None


@lru_cache(maxsize=8)
def _federal_holidays(year):
    """US federal calendar used by the native H.10/H.4.1 contract, 1986+.

    Include adjacent New Year's observed date; holidays remain schedule
    expectations, never evidence that a source actually published.
    """
    def nth(month, weekday, n):
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    def observed(day):
        return day + timedelta(days=-1 if day.weekday() == 5 else 1 if day.weekday() == 6 else 0)
    days = {nth(1, 0, 3), nth(2, 0, 3), nth(9, 0, 1), nth(10, 0, 2), nth(11, 3, 4)}
    days.add(date(year, 5, 31) - timedelta(days=date(year, 5, 31).weekday()))
    days.update(observed(date(year, month, day)) for month, day in ((1, 1), (7, 4), (11, 11), (12, 25)))
    days.add(observed(date(year + 1, 1, 1)))
    if year >= 2021:
        days.add(observed(date(year, 6, 19)))
    return frozenset(days)


def _seiche_calendar(row, now):
    """Recompute admitted calendars at inspection time; ignore cached verdicts."""
    source, remote, frequency = (row.get(k) for k in ("source", "remote_id", "freq"))
    observed = _date(row.get("asof"))
    local_dubai = now.astimezone(ZoneInfo("Asia/Dubai"))
    if observed and date(2018, 1, 1) <= observed <= local_dubai.date() and frequency == "D":
        fx_ids = {"VAT/AED_PER_" + currency for currency in ("USD", "INR", "EUR", "GBP", "JPY", "CHF", "SGD")}
        fx = source == "cbuae_fx" and isinstance(remote, str) and remote in fx_ids
        donia = source == "cbuae_donia" and remote == "UAE_INTEREST_RATES/DONIA"
        if fx and row.get("freshness_policy") == "cbuae-vat-fx-calendar-age-v1":
            return {"freshness": "fresh" if observed == local_dubai.date() else "stale",
                    "policy": "cbuae-vat-fx-calendar-age-v1", "schedule_is_publication_receipt": False,
                    "reason": "native_calendar_age_exceeded" if observed != local_dubai.date() else None}
        if donia and row.get("freshness_policy") == "cbuae-donia-publication-aim-v1":
            # Native 09:30 publication aim plus 90 minutes collector grace;
            # no invented UAE public-holiday exemptions.
            expected = local_dubai.date()
            if expected.weekday() >= 5 or local_dubai.time() < day_time(11):
                expected -= timedelta(days=1)
                while expected.weekday() >= 5:
                    expected -= timedelta(days=1)
            return {"freshness": "fresh" if observed >= expected else "stale",
                    "policy": "cbuae-donia-publication-aim-v1", "expected_observation_period_start": expected.isoformat(),
                    "schedule_is_publication_receipt": False, "unverified_public_holidays_not_excused": True,
                    "reason": "native_publication_aim_not_met" if observed < expected else None}
    if source != "fred" or not isinstance(remote, str):
        return None
    h10 = frequency == "D" and remote in H10_IDS and row.get("freshness_policy") == "fred-h10-weekly-v1"
    h41 = frequency == "W" and remote in H41_IDS and row.get("freshness_policy") == "fred-h41-weekly-v1"
    if not (h10 or h41) or not 1986 <= now.year <= 2100:
        return None
    local = now.astimezone(_NY)
    if observed is None or observed > local.date():
        return None
    monday = local.date() - timedelta(days=local.weekday())
    def release(week):
        day = week if h10 else week + timedelta(days=3)
        while day.weekday() >= 5 or day in _federal_holidays(day.year):
            day += timedelta(days=1)
        return datetime.combine(day, day_time(16, 15 if h10 else 30), _NY)
    due = release(monday)
    if local < due:
        monday -= timedelta(weeks=1)
        due = release(monday)
    expected = monday - timedelta(weeks=1) if h10 else monday + timedelta(days=2)
    observed_period = observed - timedelta(days=observed.weekday()) if h10 else observed
    missed = max(0, (expected - observed_period).days + (0 if h10 else 6)) // 7
    return {"freshness": "fresh" if missed == 0 else "stale", "missed_release_weeks": missed,
            "policy": row["freshness_policy"], "latest_expected_release_at": due.isoformat(),
            "expected_observation_period_start": expected.isoformat(),
            "schedule_is_publication_receipt": False}


def _iorb_announcement(row, snapshot_at, now):
    if tuple(row.get(k) for k in ("source", "mnemonic", "remote_id", "freq")) != ("fred", "IORB", "IORB", "D"):
        return None
    effective = _date(row.get("asof"))
    fetched_raw = row.get("fetched_at")
    fetched = _clock(fetched_raw)
    snapshot = _clock(snapshot_at)
    if (effective is None or fetched is None or _date(fetched_raw) is not None or snapshot is None
            or not fetched <= snapshot <= now or effective <= now.astimezone(_NY).date()):
        return None
    forward = (effective - fetched.astimezone(_NY).date()).days
    if not 1 <= forward <= 14:
        return None
    return {"effective_at": effective.isoformat(), "available_at": fetched.isoformat().replace("+00:00", "Z"),
            "availability_clock_kind": "first_party_collection_not_original_publication",
            "availability_age_seconds": (now - fetched).total_seconds(),
            "current_observation_available": False, "clock_role": "announced_forward_effective",
            "forward_effective_days": forward, "policy": "seiche-fred-iorb-forward-effective-v1"}


@dataclass(frozen=True)
class SourcePolicy:
    """An operator-qualified observation contract, never supplied by an agent."""
    clock_field: str
    cadence: str
    max_age_seconds: float


def _clock(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _payload(response):
    if hasattr(response, "model_dump"):
        response = response.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(response, dict):
        return {}, True
    if response.get("jsonrpc") == "2.0":
        if "error" in response:
            return {}, True
        response = response.get("result", {})
    if not isinstance(response, dict) or response.get("isError") is True:
        return {}, True
    if isinstance(response.get("structuredContent"), dict):
        return response["structuredContent"], False
    if "content" in response:
        for item in response["content"][:16] if isinstance(response["content"], list) else []:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str) and len(item["text"]) <= 1048576:
                try:
                    value = json.loads(item["text"])
                    if isinstance(value, dict):
                        return value, False
                except (ValueError, RecursionError):
                    pass
        return {}, False
    return response, False


def inspect_result(product, tool, response, *, now=None, policies=None):
    """Return only status/clock metadata; unknown never becomes fresh by default.

``policies`` maps (product, source_id) to a SourcePolicy admitted by the service
operator. The normalized input schema is financial-evidence.source-freshness.v1
with observations [{source_id, cadence, <qualified clock field>}].
"""
    now = datetime.now(timezone.utc) if now is None else now
    if isinstance(now, (int, float)):
        now = datetime.fromtimestamp(now, timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Evaluation clock must be timezone-aware")
    payload, error = _payload(response)
    clocks, reasons, states = [], [], []
    result = {"freshness": "unknown", "source_age_seconds": None, "clocks": clocks, "reasons": reasons,
              "scope": "inspected_source_rows_only_not_product_wide_readiness"}

    def finish():
        counts = Counter(states)
        result["coverage"] = {"inspected_rows": len(states), "observation_clock_rows": len(clocks),
                              **{state: counts[state] for state in ("fresh", "stale", "unknown", "restricted")}}
        result["source_reason_counts"] = dict(Counter(row["reason"] for row in clocks if row["reason"]))
        if product == "seiche" and tool == "data_health":
            selected = [row for row in clocks if row["source_id"] in FUNDING_CORE_SOURCES]
            missing = sorted(set(FUNDING_CORE_SOURCES) - {row["source_id"] for row in selected})
            state = "stale" if any(row["freshness"] == "stale" for row in selected) else "unknown"
            if not missing and selected and all(row["freshness"] == "fresh" for row in selected):
                state = "fresh"
            result["critical_subset"] = {"definition": "seiche-us-funding-core-v1", "freshness": state,
                "source_ids": list(FUNDING_CORE_SOURCES), "missing_source_ids": missing, "clocks": selected,
                "scope": "funding_input_clock_check_not_order_authority_or_complete_source_coverage"}
            announced = [row["source_id"] for row in selected if row.get("clock_role") == "announced_forward_effective"]
            if announced:
                result["critical_subset"]["announced_not_yet_effective_sources"] = announced
                result["critical_subset"]["current_observation_required"] = announced
        return result
    if error:
        reasons.append("tool_error_or_invalid_response")
        return finish()
    restricted = (payload.get("publication_allowed") is False or payload.get("scoring_allowed") is False
                  or payload.get("context_only") is True or payload.get("status") == "restricted"
                  or payload.get("availability") == "restricted" or payload.get("evidence_class") == "restricted")
    if restricted:
        result["freshness"] = "restricted"
        states.append("restricted")
        reasons.append("context_only_not_scoring_input" if payload.get("context_only") is True or payload.get("scoring_allowed") is False else "source_rights_restrict_use")
        if product == "palimpsest" and payload.get("schema_version") == "palimpsest.mcp-china-economic-rights.v1":
            result["scope"] = "china_economic_observations_and_affected_derivatives_not_all_china_sources"
            result["rights"] = {"publication_allowed": False, "policy": "native_default_deny_cfets_chinamoney_lineage"}
        return finish()

    def add(source_id, value, field, cadence, max_age=None, state=None):
        observed = _clock(value)
        age = (now - observed).total_seconds() if observed else None
        date_precision = isinstance(value, str) and len(value) == 10
        policy_age = ((now.astimezone(timezone.utc).date() - observed.date()).days * 86400 if observed and date_precision else age)
        reason = None
        if observed is None:
            state, reason = "unknown", "observation_clock_missing_or_invalid"
        elif age < 0:
            state, reason, age = "unknown", "future_observation_clock", None
        elif state == "stale":
            reason = "source_reports_aging_stale_or_dead"
        elif max_age is None:
            state, reason = "unknown", "qualified_cadence_policy_missing"
        else:
            state = "fresh" if policy_age <= max_age else "stale"
        states.append(state)
        clocks.append({"source_id": str(source_id)[:160], "field": field,
                       "observed_at": observed.isoformat().replace("+00:00", "Z") if observed else None,
                       "source_age_seconds": age, "cadence": cadence, "max_age_seconds": max_age,
                       "clock_precision": "date" if date_precision else "timestamp", "policy_age_seconds": policy_age,
                       "freshness": state, "reason": reason})

    if product == "seiche" and tool == "data_health":
        result["use_boundary"] = "source_clock_metadata_only_not_real_money_execution_authority"
        rows = payload.get("provenance")
        if not isinstance(rows, list) or len(rows) > 2000:
            reasons.append("seiche_provenance_missing_or_out_of_bounds")
        else:
            for row in rows:
                if not isinstance(row, dict):
                    states.append("unknown")
                    continue
                if not all(isinstance(row.get(key), str) and len(row[key]) <= 80 for key in ("source", "mnemonic")):
                    states.append("unknown")
                    continue
                source_id = f"{row['source']}:{row['mnemonic']}"
                identity = tuple(row.get(k) for k in ("source", "mnemonic", "remote_id", "freq"))
                retired = RETIRED_IDENTITIES.get(identity) if all(isinstance(v, str) for v in identity) else None
                observed_date = _date(row.get("asof"))
                if retired and observed_date and observed_date.isoformat() <= retired[0] and now.date().isoformat() >= retired[1]:
                    result.setdefault("historical_reference_clocks", []).append({"source_id": source_id,
                        "observed_at": row["asof"], "scope": "native_registered_retired_reference",
                        "retired_from": retired[1], "replacement": retired[2], "excluded_from_current_input_health": True})
                    continue
                frequency, grace = row.get("freq"), row.get("freshness_grace_days")
                if not isinstance(frequency, str) or len(frequency) > 16:
                    frequency = None
                policy = None
                if (not row.get("freshness_policy") and not row.get("publication_schedule")
                        and frequency in SEICHE_GRACE_DAYS and type(grace) in {int, float}
                        and grace == SEICHE_GRACE_DAYS[frequency]):
                    policy = grace * 86400
                reported = "stale" if isinstance(row.get("staleness"), str) and row["staleness"] in {"aging", "stale", "dead"} else None
                add(source_id, row.get("asof"), "asof", frequency, policy, reported)
                calendar = _seiche_calendar(row, now)
                if calendar:
                    states[-1] = calendar["freshness"]
                    clocks[-1].update(calendar, reason=calendar.get("reason", "scheduled_release_missed") if calendar["freshness"] == "stale" else None)
                announcement = _iorb_announcement(row, payload.get("generated_at"), now)
                if announcement:
                    clocks[-1].update(announcement, reason="announced_effective_date_not_current_observation")
                if (identity == ("fred", "GDP", "GDP", "Q")
                        and row.get("observation_date_semantics") == "quarter_start"
                        and row.get("publication_freshness") == "unknown"):
                    states[-1] = "unknown"
                    clocks[-1].update(freshness="unknown", clock_role="reference_period_start",
                        reference_period_age_seconds=clocks[-1]["source_age_seconds"], source_age_seconds=None,
                        max_age_seconds=None, policy_age_seconds=None,
                        reason="reference_period_start_does_not_establish_publication_freshness")
                if (row.get("asof") is None and row.get("freq") is None
                        and row.get("freshness_basis") == "fetch clock only; this table contains heterogeneous observation dates"):
                    clocks[-1].update(clock_role="heterogeneous_table_envelope",
                                     reason="per_observation_clocks_not_exposed_by_health_envelope")
            if result.get("historical_reference_clocks"):
                reasons.append("native_retired_references_excluded_from_current_input_health")
            if payload.get("faults"):
                states.append("unknown")
                reasons.append("source_faults_present")
    elif product == "undertow" and tool in {"exit_cost", "crypto_exit_check", "trade_safety_exit_context"}:
        result["scope"] = "btc_venue_observation_clocks_only_not_executable_quotes"
        result["use_boundary"] = "research_observe_paper_only_no_order_authority"
        native_context = tool == "trade_safety_exit_context"
        expected_schema = "undertow.trade-safety-exit-context.v1" if native_context else "undertow.crypto-workbench.v1"
        if (native_context or tool == "crypto_exit_check") and payload.get("schema") != expected_schema:
            reasons.append("undertow_schema_missing_or_unqualified")
        else:
            rights = payload.get("rights") if native_context else None
            result["rights"] = {"status": "not_verified_by_this_tool", "scope": "public_research_metadata"}
            if isinstance(rights, dict):
                approved = rights.get("status") == "approved" and rights.get("scope") == "derived_metadata_only"
                status = rights.get("status")
                status = status if isinstance(status, str) else "unavailable"
                result["rights"] = {"status": status if status in {"approved", "pending_owner_review", "denied", "restricted", "unavailable"} else "unavailable", "scope": "derived_metadata_only"}
                valid_until = _clock(rights.get("valid_until"))
                if not approved or valid_until is None or valid_until <= now:
                    states.append("restricted" if status in {"pending_owner_review", "denied", "restricted"} or (valid_until is not None and valid_until <= now) else "unknown")
                    reasons.append("native_rights_not_currently_approved")
            elif native_context:
                states.append("unknown")
                reasons.append("native_rights_metadata_missing")
            raw_clocks = payload.get("clocks", {}).get("venue_observation_at_by_venue") if native_context and isinstance(payload.get("clocks"), dict) else payload.get("venue_freshness")
            if not isinstance(raw_clocks, dict) or not raw_clocks or len(raw_clocks) > 16:
                reasons.append("venue_observation_clocks_missing_or_out_of_bounds")
                states.append("unknown")
            else:
                for venue in sorted(UNDERTOW_VENUES):
                    raw = raw_clocks.get(venue)
                    observed = raw if native_context else raw.get("observed_at") if isinstance(raw, dict) else None
                    if _date(observed) is not None:
                        observed = None  # Venue quotes require an aware intraday clock.
                    add("undertow:BTC:" + venue, observed, "venue_observation_at_by_venue" if native_context else "venue_freshness.observed_at", "PT1H", UNDERTOW_MAX_AGE)
                    if native_context and clocks[-1]["source_age_seconds"] is not None and clocks[-1]["source_age_seconds"] >= UNDERTOW_MAX_AGE:
                        states[-1] = "stale"
                        clocks[-1].update(freshness="stale", reason="native_observation_expiry_reached")
                result["source_policy"] = "undertow-native-two-hour-research-observation-limit-v1"
            native_status = payload.get("status")
            native_status = native_status if isinstance(native_status, str) else None
            if native_status in {"available", "partial", "unavailable"}:
                result["native_availability"] = native_status
            if native_status == "unavailable":
                states.append("unknown")
                reasons.append("native_result_unavailable_clock_freshness_is_insufficient")
    elif product == "wake" and tool == "wake_economy_now":
        result["scope"] = "published_usage_and_retail_reference_periods_not_individual_purchases"
        result["use_boundary"] = "collection_recency_does_not_establish_source_publication_freshness"
        rows = payload.get("observations")
        if payload.get("schema") != "wake.public-economy.v1" or not isinstance(rows, list) or len(rows) > 200:
            reasons.append("wake_public_schema_missing_or_out_of_bounds")
        else:
            for row in rows:
                if not isinstance(row, dict) or row.get("source") not in {"openrouter_rankings", "census_ecommerce"}:
                    states.append("unknown")
                    continue
                # A reference period has no publication calendar admission here.
                # Preserve its clock without promoting a recent fetch to fresh.
                add(row["source"], row.get("period_end") or row.get("event_time"),
                    "period_end", "reference_period")
                clocks[-1]["clock_role"] = "reference_period_end"
            reasons.append("source_publication_calendar_not_yet_qualified")
    elif payload.get("schema") == "financial-evidence.source-freshness.v1":
        rows = payload.get("observations")
        if isinstance(rows, list) and len(rows) <= 2000:
            for row in rows:
                if not isinstance(row, dict):
                    states.append("unknown")
                    continue
                source_id = row.get("source_id")
                if not isinstance(source_id, str) or len(source_id) > 160 or not isinstance(row.get("cadence"), str) or len(row["cadence"]) > 16:
                    states.append("unknown")
                    continue
                policy = (policies or {}).get((product, source_id))
                if (not isinstance(policy, SourcePolicy) or policy.clock_field not in {"observed_at", "source_as_of", "observation_date"}
                        or row.get("cadence") != policy.cadence or type(policy.max_age_seconds) not in {int, float}
                        or not math.isfinite(policy.max_age_seconds) or policy.max_age_seconds <= 0):
                    add(source_id, None, "unqualified", row.get("cadence"))
                else:
                    add(source_id, row.get(policy.clock_field), policy.clock_field, policy.cadence, policy.max_age_seconds)
    elif product == "palimpsest" and tool == "research_catalog":
        reasons.append("catalog_artifact_clocks_are_not_underlying_observation_clocks")
    elif product == "narcoscope" and tool == "get_market_catalog":
        result["scope"] = "historical_market_catalog_not_live_trading_feed"
        reasons.append("reference_periods_and_source_rights_require_dataset_specific_release_contracts")
    elif product == "noisefloor":
        result["scope"] = "caller_supplied_series_not_hosted_market_feed"
        reasons.append("caller_data_freshness_cannot_establish_hosted_source_freshness")
    else:
        reasons.append("no_qualified_source_clock_adapter")
    if "restricted" in states:
        result["freshness"] = "restricted"
    elif "stale" in states:
        result["freshness"] = "stale"
    elif states and all(state == "fresh" for state in states):
        result["freshness"] = "fresh"
    ages = [row["source_age_seconds"] for row in clocks if row["source_age_seconds"] is not None]
    result["source_age_seconds"] = max(ages) if ages else None
    if not states:
        reasons.append("no_observation_freshness_proof")
    elif "unknown" in states:
        reasons.append("incomplete_source_clock_or_calendar_policy")
    return finish()


class _LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        total = 0
        async for chunk in self.stream:
            total += len(chunk)
            if total > 4194304:
                raise httpx.StreamError("Probe response exceeded metadata limit")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class _ProbeTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        response.stream = _LimitedStream(response.stream)
        return response


def _iso_epoch(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


async def _undertow_admission(session, *, cache_path, timeout, clock=time.time):
    """At most one attempted native proof per hour for this monitor/cache.

    Persist the attempt before making a request, including failed/cancelled
    requests. Only an explicit restriction is reusable; current quote clocks
    and positive admission are never cached as fresh. The native 60/day limit
    remains authoritative and is shared with other calls using that identity.
    """
    unknown = {"freshness": "unknown", "clocks": [], "source_age_seconds": None,
               "cached": False, "cache_ttl_seconds": ADMISSION_CACHE_SECONDS}
    if cache_path is None:
        return {**unknown, "reasons": ["admission_cache_not_configured"]}
    credential = load_undertow_credential(now=clock())
    if credential.status not in {"ready", "not_configured"}:
        return {**unknown, "reasons": ["upstream_credential_unavailable"]}
    path = Path(cache_path)
    lock_fd = None
    try:
        lock_fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError("Invalid admission cache lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            stored = read_private_json(path)
        except FileNotFoundError:
            stored = None
        now = clock()
        if stored is not None:
            if (not isinstance(stored, dict) or stored.get("schema") != "undertow.admission-cache.v1"
                    or type(stored.get("attempted_at")) not in {int, float}
                    or not math.isfinite(stored["attempted_at"])):
                raise ValueError("Invalid admission cache")
            age = now - stored["attempted_at"]
            if age < 0:
                return {**unknown, "reasons": ["admission_cache_clock_in_future"]}
            if age < ADMISSION_CACHE_SECONDS and stored.get("identity") == credential.cache_identity:
                metadata = {"next_attempt_at": _iso_epoch(stored["attempted_at"] + ADMISSION_CACHE_SECONDS)}
                checked_at, restriction = stored.get("checked_at"), stored.get("restriction")
                if (type(checked_at) in {int, float} and math.isfinite(checked_at)
                        and 0 <= now - checked_at < ADMISSION_CACHE_SECONDS
                        and isinstance(restriction, dict)
                        and restriction.get("status") in {"pending_owner_review", "denied", "restricted"}
                        and restriction.get("scope") == "derived_metadata_only"):
                    return {**unknown, **metadata, "freshness": "restricted", "cached": True,
                            "checked_at": _iso_epoch(checked_at), "age_seconds": round(now - checked_at, 3),
                            "rights": {"status": restriction["status"], "scope": "derived_metadata_only"},
                            "reasons": ["cached_native_restriction_within_one_hour_ttl"]}
                return {**unknown, **metadata, "reasons": ["admission_refresh_waiting_for_hourly_budget"]}
        attempt = {"schema": "undertow.admission-cache.v1", "identity": credential.cache_identity,
                   "attempted_at": now, "checked_at": None, "restriction": None}
        save_report(path, attempt)  # Durable reservation survives cancellation/restart.
        metadata = {"next_attempt_at": _iso_epoch(now + ADMISSION_CACHE_SECONDS)}
        request = {"request_hash": hashlib.sha256(b"seiche-freshness-metadata-probe-v1").hexdigest(),
                   "mode": "observe", "instrument": "BTC/USD", "side": "sell", "venue": None,
                   "requested_size_usd": 1000}
        try:
            async with asyncio.timeout(min(10, timeout)):
                admission = await session.call_tool("trade_safety_exit_context", request)
            checked_at = clock()
            checked = inspect_result("undertow", "trade_safety_exit_context", admission, now=checked_at)
            if admission.isError:
                return {**unknown, **metadata, "transport": "tool_error", "reasons": ["native_admission_tool_error"]}
            rights = checked.get("rights", {})
            if (checked["freshness"] == "restricted" and rights.get("status") in {"pending_owner_review", "denied", "restricted"}
                    and rights.get("scope") == "derived_metadata_only"):
                attempt.update(checked_at=checked_at, restriction={"status": rights["status"], "scope": rights["scope"]})
                save_report(path, attempt)
            return {**checked, **metadata, "checked_at": _iso_epoch(checked_at), "age_seconds": 0,
                    "cached": False, "cache_ttl_seconds": ADMISSION_CACHE_SECONDS}
        except Exception:
            return {**unknown, **metadata, "transport": "unavailable", "reasons": ["native_admission_check_unavailable"]}
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return {**unknown, "reasons": ["admission_cache_unavailable_or_busy"]}
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


async def probe_product(product, *, timeout=30, admission_cache_path=None, clock=time.time, transport=None):
    if product not in PRODUCTS:
        raise ValueError("Only fixed public product endpoints may be probed")
    started = time.monotonic()
    summary = {"product": product, "transport": "unavailable", "tool": None,
               "freshness": "unknown", "source_age_seconds": None, "clocks": [], "reasons": []}
    auth = ProbeAuth(clock=clock)
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(transport=transport or _ProbeTransport(), follow_redirects=False, timeout=timeout,
                                         event_hooks={"request": [auth.request], "response": [auth.response]},
                                         headers={"X-Financial-Evidence-Traffic-Class": "synthetic"}) as client:
                async with streamable_http_client(PRODUCTS[product][1], http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        summary["transport"] = "available"
                        available = {tool.name for tool in tools.tools}
                        name = next((name for name in PREFERRED_TOOLS[product] if name in available), None)
                        if name:
                            summary["tool"] = name
                            args = {"offset": 0, "limit": 12} if name == "research_catalog" else {}
                            value = await session.call_tool(name, args)
                            summary.update(inspect_result(product, name, value, now=clock()))
                            if value.isError:
                                summary["transport"] = "tool_error"
                            if product == "undertow" and "trade_safety_exit_context" in available:
                                summary["clock_freshness"] = summary["freshness"]
                                checked = await _undertow_admission(session, cache_path=admission_cache_path, timeout=timeout, clock=clock)
                                summary["admission_check"] = checked
                                if checked["freshness"] == "restricted":
                                    summary["freshness"] = "restricted"
                                    summary["reasons"].append("observation_clocks_do_not_override_native_rights_restriction")
                                elif checked["freshness"] != "fresh":
                                    summary["freshness"] = "unknown"
                                    summary["reasons"].append("native_admission_check_not_current")
                            elif product == "undertow":
                                summary["clock_freshness"] = summary["freshness"]
                                summary["freshness"] = "unknown"
                                summary["reasons"].append("native_admission_tool_not_advertised")
                        else:
                            summary["reasons"] = ["no_qualified_health_tool_advertised"]
                            if product == "noisefloor":
                                summary["scope"] = "caller_supplied_series_not_hosted_market_feed"
                                summary["reasons"] = ["computational_tools_have_no_hosted_source_observation_clock"]
    except Exception:
        # Connection/SDK errors may embed endpoints or server text; retain no raw errors.
        summary = {"product": product, "tool": summary.get("tool"), "transport": "unavailable",
                   "reasons": ["probe_failed_or_timed_out"], "freshness": "unknown",
                   "clocks": [], "source_age_seconds": None}
    if product == "undertow":
        summary["upstream_auth"] = load_undertow_credential(now=clock()).metadata()
    summary["transport_duration_ms"] = round((time.monotonic() - started) * 1000)
    return summary


def save_report(path, report):
    """Atomic private status-file replacement; never store MCP payloads."""
    destination = Path(path)
    temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


async def probe_products(output_path, *, timeout=30):
    if not 1 <= timeout <= 60:
        raise ValueError("Probe timeout must be between 1 and 60 seconds")
    cache = Path(output_path).with_name("undertow-admission-cache.json")
    rows = await asyncio.gather(*(probe_product(product, timeout=timeout, admission_cache_path=cache) for product in PRODUCTS))
    report = {"schema": "seiche-agent-network.freshness.v1", "evaluated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "products": rows, "traffic_class": "synthetic", "raw_payloads_retained": False,
              "interpretation": "Transport availability and scoped source-clock checks; not trade execution approval or complete product coverage"}
    save_report(output_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Probe fixed public MCP services; store bounded status metadata only")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    report = asyncio.run(probe_products(args.output, timeout=args.timeout))
    print(json.dumps({row["product"]: {"transport": row["transport"], "freshness": row["freshness"]} for row in report["products"]}))
    if any(row["transport"] != "available" or row["freshness"] in {"stale", "restricted"} for row in report["products"]):
        return 1
    return 2 if any(row["freshness"] == "unknown" for row in report["products"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
