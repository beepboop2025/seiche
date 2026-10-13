"""Read the collector's private AI ecosystem projection; never fetch caller URLs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path


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
        names = {"cloudflare_radar", "huggingface_datasets", "huggingface_finance_datasets", "mcp_registry", "openrouter_rankings", "fleet"}
        if len(value["sources"]) > len(names) or len(value["observations"]) > 5000:
            raise ValueError("report row bound exceeded")
        if any(s.get("source") not in names for s in value["sources"]) or any(r.get("source") not in names for r in value["observations"]):
            raise ValueError("unknown source")
        at = datetime.fromisoformat(value["as_of"].replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("report clock lacks timezone")
        age = (datetime.now(timezone.utc) - at).total_seconds()
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
        if not current:
            for source in value["sources"]:
                source["fresh"] = False
            for row in value["observations"]:
                row["collection_fresh"] = False
        return value
    except FileNotFoundError:
        return unavailable | {"status": "missing_report"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return unavailable | {"status": "invalid_report"}
