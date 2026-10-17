"""Read the collector's private AI ecosystem projection; never fetch caller URLs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

# Match the reviewed Wake collection contracts. A newly written report never
# extends a feed's lifetime, and incoming metadata cannot lengthen these limits.
SOURCE_MAX_AGE = {
    "cloudflare_radar": 10800,
    "huggingface_datasets": 43200,
    "huggingface_finance_datasets": 43200,
    "mcp_registry": 43200,
    "openrouter_rankings": 7200,
    "fleet": 7200,
}


def _clock(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def read_report():
    location = os.environ.get("SEICHE_AGENT_ACTIVITY_REPORT", "")
    unavailable = {"schema": "wake.agent-activity.v1", "status": "not_configured",
                   "report_current": False, "sources": [], "observations": [],
                   "coverage_complete": False, "scope": "operator_global"}
    if not location:
        return unavailable
    try:
        with Path(location).open("rb") as stream:
            raw = stream.read(4 * 1024**2 + 1)
        if len(raw) > 4 * 1024**2:
            raise ValueError("report exceeds bound")
        value = json.loads(raw)
        if value.get("schema") != "wake.agent-activity.v1" or not isinstance(value.get("sources"), list) or not isinstance(value.get("observations"), list):
            raise ValueError("invalid report contract")
        names = set(SOURCE_MAX_AGE)
        if len(value["sources"]) > len(names) or len(value["observations"]) > 5000:
            raise ValueError("report row bound exceeded")
        if any(s.get("source") not in names for s in value["sources"]) or any(r.get("source") not in names for r in value["observations"]):
            raise ValueError("unknown source")
        if len({s["source"] for s in value["sources"]}) != len(value["sources"]):
            raise ValueError("duplicate source identity")
        at = datetime.fromisoformat(value["as_of"].replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("report clock lacks timezone")
        now = datetime.now(timezone.utc)
        age = (now - at).total_seconds()
        if age < -60:
            raise ValueError("future report clock")
        current = -60 <= age <= 1200
        value.update(scope="operator_global", status="ok" if current else "stale_report", report_current=current)
        radar = next((s for s in value["sources"] if s["source"] == "cloudflare_radar"), None)
        if radar:
            expiry = radar.get("rights_expires_at")
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00")) if expiry else None
            if expiry is None or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                radar.update(status="not_admitted", fresh=False, observations=0)
                value["observations"] = [r for r in value["observations"] if r["source"] != "cloudflare_radar"]
        eligible = {}
        for source in value["sources"]:
            name = source["source"]
            observed = _clock(source.get("knowledge_time"))
            source_age = (now - observed).total_seconds() if observed else None
            clocks_current = (source_age is not None and 0 <= source_age <= SOURCE_MAX_AGE[name]
                              and observed <= at)
            source["collection_age_seconds"] = round(source_age) if source_age is not None else None
            source["fresh"] = (current and source.get("fresh") is True
                               and source.get("status") == "ok" and clocks_current)
            if not source["fresh"] and source.get("status") == "ok":
                source["status"] = "stale" if observed else "unknown"
            if source["fresh"] and isinstance(source.get("receipt_id"), str):
                eligible[name] = source
        retained = []
        for row in value["observations"]:
            source = eligible.get(row["source"])
            if (source and row.get("collection_fresh") is True
                    and row.get("knowledge_time") == source["knowledge_time"]
                    and row.get("receipt_id") == source["receipt_id"]):
                retained.append(row)
        value["withheld_observations"] = len(value["observations"]) - len(retained)
        value["observations"] = retained
        value["current_only"] = True
        for source in value["sources"]:
            source["observations"] = sum(r["source"] == source["source"] for r in retained)
        return value
    except FileNotFoundError:
        return unavailable | {"status": "missing_report"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return unavailable | {"status": "invalid_report"}
