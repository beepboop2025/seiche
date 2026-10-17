"""Daily native-product audit; source dates and publication dates stay distinct.

This observes fixed owned endpoints. It never rewrites a financial observation,
admits a restricted source, or promotes a historical filing into current data.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

import httpx

from .freshness import inspect_result, save_report

URLS = {
    "liquilens": "https://api.liquilens.in/api/failure-radar/board",
    "liquilens_crypto": "https://api.liquilens.in/api/public-signals/crypto-regime",
    "liquilens_rails": "https://api.liquilens.in/api/public-signals/rails",
    "seiche": "https://api.seiche.info/api/health",
    "undertow": "https://api.seiche.info/undertow/board.json",
}
PRODUCTS = {"liquilens", "seiche", "undertow"}


def _date(value):
    if not isinstance(value, str):
        raise ValueError("missing observation clock")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def inspect(product, payload, now):
    """Return source metadata only, with no values or model-readiness claim."""
    result = {"product": product, "status": "partial", "current_view_violation_count": 0,
              "all_sources_fresh": False, "training_eligible": False,
              "production_model_eligible": False, "coverage_complete": False}
    # The institution board uses ml.merton_dd.authority_today(), an India
    # calendar date. Its date-only clock can be tomorrow relative to UTC.
    calendar_zone = "Asia/Kolkata" if product == "liquilens" else "UTC"
    today = now.astimezone(ZoneInfo(calendar_zone)).date()
    result["evaluation_calendar_timezone"] = calendar_zone
    if product == "seiche":
        native = inspect_result("seiche", "data_health", payload, now=now)
        rows = native["clocks"]
        result.update(
            checked_at=payload.get("generated_at"),
            source_counts=native["coverage"],
            current_source_ids=[r["source_id"] for r in rows if r["freshness"] == "fresh"],
            withheld_sources=[{k: r.get(k) for k in ("source_id", "observed_at", "freshness", "reason")}
                              for r in rows if r["freshness"] != "fresh"],
            all_sources_fresh=native["freshness"] == "fresh",
            scope="native source health; current ranking and individual engine gates remain authoritative",
        )
        # The upstream check is separate from source-observation age. A recent
        # fetch of an unchanged release does not refresh its observation date.
        result["not_checked_within_day"] = [
            r["source"] + ":" + r["mnemonic"] for r in payload.get("provenance", [])
            if r.get("mnemonic") not in {"IOER", "TED"}
            and (not r.get("fetched_at") or (today - _date(r["fetched_at"])).days not in {0, 1})
        ]
    elif product == "liquilens":
        if not isinstance(payload.get("rows"), list) or not isinstance(payload.get("excluded_stale"), list):
            raise ValueError("unrecognized institution board")
        rows = []
        for value in payload["rows"]:
            policy = value.get("freshness", {})
            maximum = {"quarterly": 183, "annual": 400}.get(policy.get("cadence"))
            age = (today - _date(value.get("as_of"))).days
            current = maximum is not None and 0 <= age <= maximum and policy.get("current_eligible") is True
            rows.append({"source_id": value["slug"], "observed_at": value["as_of"], "fresh": current})
        disclosures = payload.get("current_disclosures", {})
        if not isinstance(disclosures.get("rows"), list):
            raise ValueError("missing disclosure evidence")
        for value in disclosures["rows"]:
            if value.get("status") != "observed":
                continue
            maximum = value.get("freshness_limit_days")
            age = (today - _date(value.get("period_end"))).days
            current = maximum in {183, 400} and 0 <= age <= maximum
            rows.append({"source_id": value["slug"], "observed_at": value["period_end"], "fresh": current})
        result.update(
            checked_at=payload.get("as_of"),
            inspected_current_rows=rows,
            current_view_violation_count=sum(not r["fresh"] for r in rows),
            historical_corpus_rows_excluded=len(payload["excluded_stale"]),
            all_inspected_current_rows_fresh=bool(rows) and all(r["fresh"] for r in rows),
            scope="published vetted dossiers and observed disclosure reviews; not the complete institution universe",
        )
    elif product in {"liquilens_crypto", "liquilens_rails"}:
        if payload.get("available") is not True or payload.get("stale") is not False:
            raise ValueError("signal is not currently available")
        result.update(checked_at=payload.get("as_of"),
                      scope="published market context; no decision or model authority")
        if product == "liquilens_crypto":
            rows = []
            for name, value in payload["keys"].items():
                dates = value.get("series", {}).get("dates", [])
                if not dates:
                    raise ValueError("missing underlying crypto observation dates")
                age = (today - _date(dates[-1])).days
                rows.append({"source_id": name, "observed_at": dates[-1], "fresh": 0 <= age <= 1})
            if not rows:
                raise ValueError("empty crypto signal")
            result.update(inspected_current_rows=rows,
                          current_view_violation_count=sum(not r["fresh"] for r in rows),
                          all_inspected_current_rows_fresh=all(r["fresh"] for r in rows))
        else:
            if not payload.get("rows"):
                raise ValueError("empty stablecoin signal")
            result.update(native_decision_eligible=payload.get("decision_eligible") is True,
                          native_display_only=payload.get("display_only") is True,
                          inspected_current_rows=len(payload["rows"]),
                          underlying_clock_coverage="not_qualified_by_this_check")
    elif product == "undertow":
        if payload.get("schema") != "undertow.board.v1":
            raise ValueError("unrecognized market-liquidity board")
        provenance = payload["provenance"]
        findings, violations = [], []
        for name, pack in provenance.get("input_health", {}).get("packs", {}).items():
            findings.append({"pack": name, "stale_series": pack.get("stale_series", {}),
                             "empty_series": pack.get("empty_series", []),
                             "fault_count": pack.get("n_declared_faults", 0)})
        inspected = 0
        for segment, cell in payload["segments"].items():
            for row in cell.get("measures", []):
                inspected += 1
                maximum = row.get("freshness_max_age_days")
                unavailable = row.get("source_status") in {"restricted", "unavailable", "stale"}
                if type(maximum) is int and maximum > 0:
                    age = (today - _date(row.get("asof"))).days
                    unavailable |= not 0 <= age <= maximum
                if unavailable and row.get("stress_pctl") is not None:
                    violations.append({"segment": segment, "measure": row["measure"]})
        result.update(
            checked_at=provenance.get("generated_at"), source_findings=findings,
            inspected_public_measures=inspected, current_view_violations=violations,
            current_view_violation_count=len(violations),
            scope="public measure subset and declared source faults; hidden measures and unqualified clocks remain unknown",
        )
    else:
        raise ValueError("unknown product")
    publication_age_days = (today - _date(result.get("checked_at"))).days
    result["publication_checked_within_day"] = 0 <= publication_age_days <= 1
    if not result["publication_checked_within_day"] or result["current_view_violation_count"]:
        result["status"] = "failed"
    return result


async def audit(output, *, transport=None):
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport,
                                headers={"X-Financial-Evidence-Traffic-Class": "synthetic"}) as client:
        async def one(product, url):
            try:
                raw = bytearray()
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    if "application/json" not in response.headers.get("content-type", ""):
                        raise ValueError("unexpected content type")
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 4 * 1024**2:
                            raise ValueError("source response exceeds bound")
                result = inspect(product, json.loads(raw), now)
                return result | {"url": url, "response_sha256": hashlib.sha256(raw).hexdigest()}
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
                return {"product": product, "url": url, "status": "unavailable",
                        "all_sources_fresh": False, "training_eligible": False}
        checks = await asyncio.gather(*(one(product, url) for product, url in URLS.items()))
    bank = next(r for r in checks if r["product"] == "liquilens")
    bank["market_checks"] = [r for r in checks if r["product"].startswith("liquilens_")]
    if any(r["status"] in {"failed", "unavailable"} for r in bank["market_checks"]):
        bank["status"] = "failed" if bank["status"] != "unavailable" else "unavailable"
    rows = [r for r in checks if r["product"] in PRODUCTS]
    report = {"schema": "financial-evidence.core-freshness.v1", "checked_at": now.isoformat(),
              "products": rows, "all_products_fully_fresh": False,
              "model_tracks": ["market_liquidity", "economic_demand"],
              "interpretation": "Scoped source and current-view checks; unknown coverage is not 100 percent readiness."}
    destination = Path(output)
    # Retain one immutable first observation per UTC day and a latest projection.
    history = destination.parent / "daily"
    history.mkdir(mode=0o700, exist_ok=True)
    daily = history / (now.date().isoformat() + ".json")
    staged = history / ("." + uuid.uuid4().hex + ".json")
    save_report(staged, report)
    try:
        try:
            os.link(staged, daily)
        except FileExistsError:
            pass
    finally:
        staged.unlink()
    save_report(destination, report)
    return report


def read_report():
    """Read the bounded status projection, excluding stale reports from readiness."""
    location = os.environ.get("SEICHE_CORE_FRESHNESS_REPORT", "")
    unavailable = {"schema": "financial-evidence.core-freshness.v1", "status": "unavailable",
                   "monitor_current": False, "products": [], "all_products_fully_fresh": False}
    if not location:
        return unavailable
    try:
        with Path(location).open("rb") as handle:
            raw = handle.read(1024**2 + 1)
        if len(raw) > 1024**2:
            raise ValueError("report exceeds bound")
        result = json.loads(raw)
        if (result.get("schema") != unavailable["schema"] or len(result["products"]) != len(PRODUCTS)
                or {r["product"] for r in result["products"]} != PRODUCTS):
            raise ValueError("invalid core report")
        at = datetime.fromisoformat(result["checked_at"].replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("missing report timezone")
        age = (datetime.now(timezone.utc) - at).total_seconds()
        current = 0 <= age <= 7200
        result.update(monitor_current=current, all_products_fully_fresh=False,
                      status="current" if current else "stale_report")
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return unavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = asyncio.run(audit(args.output))
    print(json.dumps({r["product"]: r["status"] for r in report["products"]}))
    return 1 if any(r["status"] in {"failed", "unavailable"} for r in report["products"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
