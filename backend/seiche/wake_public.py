"""Bounded public-only transport for Wake readings across the Railway boundary.

This is a projection of the existing public MCP fields, not the private pack.
Source clocks are checked on every read; a newly generated pack cannot make an
old CFTC/Fed observation current. No redirect or user-provided URL is followed.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import urllib.request

from seiche import wakeflows

URL = "https://api.seiche.info/api/public-institutional-flows.json"
SCHEMA = "seiche.public-institutional-flows.v1"
MAX_BYTES = 65536
FIELDS = {
    "basis_trade": ("size_usd_bn", "gross_short_usd_bn", "z_3y", "delta_4w_usd_bn", "fragile", "funding_spread_bp", "ref_date", "notional_basis"),
    "pension_duration": ("net_long_usd_bn", "z_3y"),
    "sovereign_custody": ("ref_date", "level_usd_bn", "chg_13w_usd_bn", "z_level_5y"),
    "fusion_index": ("date", "index", "sigma", "slope", "band68"),
    "stress_endogeneity": ("branching_ratio", "verdict", "p_event_next", "n_events", "lr_stat_vs_poisson"),
}
SOURCES = {
    "cftc": ("CFTC TFF (Socrata gpe5-46if)", "https://publicreporting.cftc.gov/resource/gpe5-46if.json", 10),
    "custody": ("Fed H.4.1 custody WMTSECL1 (FRED)", "https://fred.stlouisfed.org/graph/fredgraph.csv", 10),
    "spread": ("SOFR-IORB spread (FRED)", "https://fred.stlouisfed.org/graph/fredgraph.csv", 4),
}
DEPENDENCIES = {
    "basis_trade": ("cftc", "spread"), "pension_duration": ("cftc",),
    "sovereign_custody": ("custody",), "fusion_index": ("cftc", "custody", "spread"),
    "stress_endogeneity": ("spread",),
}
REQUIRED = {
    "basis_trade": ("size_usd_bn", "gross_short_usd_bn", "ref_date"),
    "pension_duration": ("net_long_usd_bn",),
    "sovereign_custody": ("level_usd_bn", "ref_date"),
    "fusion_index": ("index", "sigma", "date", "band68"),
    "stress_endogeneity": ("branching_ratio", "n_events", "p_event_next"),
}


def _public_values(name: str, value: dict) -> dict:
    out = {key: value[key] for key in FIELDS[name] if key in value}
    if "p_event_next" in out:
        if not isinstance(out["p_event_next"], dict):
            raise ValueError("invalid event horizon values")
        out["p_event_next"] = {key: out["p_event_next"][key] for key in ("5d", "21d") if key in out["p_event_next"]}
    strings = {"ref_date", "date", "notional_basis", "verdict"}
    for key, item in out.items():
        if item is None:
            continue
        if key in strings:
            if not isinstance(item, str) or len(item) > 80:
                raise ValueError("invalid public reading label")
        elif key == "fragile":
            if not isinstance(item, bool):
                raise ValueError("invalid fragility flag")
        else:
            numbers = list(item.values()) if isinstance(item, dict) else item if isinstance(item, list) else [item]
            if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) for number in numbers):
                raise ValueError("invalid numeric reading")
    return out


def _date(value: object) -> dt.date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("date must be an ISO date")
    result = dt.date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("date must be normalized YYYY-MM-DD")
    return result


def _timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO datetime")
    stamp = dt.datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return stamp


def project(pack: dict, *, now: dt.datetime | None = None) -> dict:
    """Explicit allowlist prevents future private pack fields becoming public."""
    if pack.get("product") != "seiche" or str(pack.get("schema_version", "")).split(".")[0] != "1":
        raise wakeflows.WakePackError("invalid private pack identity")
    readings = wakeflows.readings(pack)
    values = {name: _public_values(name, readings[name])
              for name in FIELDS if isinstance(readings.get(name), dict)}
    sources = {}
    for key, (label, url, _grace) in SOURCES.items():
        matches = [p for p in pack.get("provenance", []) if isinstance(p, dict) and p.get("source") == label and p.get("url") == url]
        if len(matches) == 1:
            p = matches[0]
            sources[key] = {field: p.get(field) for field in ("source", "url", "reference_date", "release_date", "retrieved_at")}
    envelope = {"schema": SCHEMA, "generated_at": pack.get("generated_at"),
                "as_of": pack.get("as_of"), "values": values, "sources": sources}
    # Do not publish malformed or already stale envelopes.
    current = validate(envelope, now=now)
    envelope.update(sources=current["sources"], sections=current["sections"], status=current["status"], evaluated_at=current["evaluated_at"])
    return envelope


def validate(envelope: dict, *, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        if not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA:
            raise ValueError("invalid public projection schema")
        generated = _timestamp(envelope.get("generated_at"))
        age = (now - generated).total_seconds()
        if age < -300 or age > 2 * 86400:
            raise ValueError("public projection generation clock is stale or future")
        as_of = _date(envelope.get("as_of"))
        if as_of > now.date() or as_of < generated.date() - dt.timedelta(days=1):
            raise ValueError("invalid nowcast date")
        values, sources = envelope["values"], envelope["sources"]
        if not isinstance(values, dict) or not isinstance(sources, dict):
            raise ValueError("invalid values or source clocks")
        public_sources = {}
        for key, source in sources.items():
            if key not in SOURCES or not isinstance(source, dict):
                raise ValueError("unexpected source")
            label, url, grace = SOURCES[key]
            if source.get("source") != label or source.get("url") != url:
                raise ValueError("source identity mismatch")
            ref, release, fetched = _date(source["reference_date"]), _date(source["release_date"]), _timestamp(source["retrieved_at"])
            if (ref > release or release > generated.date() or fetched.date() < release
                    or fetched > generated + dt.timedelta(minutes=5)
                    or fetched > now + dt.timedelta(minutes=5) or fetched < generated - dt.timedelta(days=2)):
                raise ValueError("invalid source clock ordering")
            public_sources[key] = {field: source[field] for field in ("source", "url", "reference_date", "release_date", "retrieved_at")}
            public_sources[key].update(age_days=(now.date() - ref).days,
                                       freshness="fresh" if (now.date() - ref).days <= grace else "stale")
        out = {"as_of": as_of.isoformat(), "generated_at": generated.isoformat(),
               "evaluated_at": now.isoformat(), "sources": public_sources, "sections": {}}
        for name, fields in FIELDS.items():
            dependencies = DEPENDENCIES[name]
            value = values.get(name)
            available = (isinstance(value, dict) and all(value.get(key) is not None for key in REQUIRED[name])
                         and all(key in public_sources for key in dependencies))
            state = "unavailable" if not available else "fresh" if all(public_sources[key]["freshness"] == "fresh" for key in dependencies) else "stale"
            dates = [public_sources[key]["reference_date"] for key in dependencies if key in public_sources]
            out["sections"][name] = {"status": state, "source_ids": list(dependencies),
                                     "oldest_input_date": min(dates) if dates else None,
                                     "latest_input_date": max(dates) if dates else None}
            if state == "fresh":
                # Return only deliberately public fields even if a remote file is malformed.
                projected = _public_values(name, value)
                if name == "basis_trade" and projected.get("ref_date") != public_sources["cftc"]["reference_date"]:
                    raise ValueError("basis reference date differs from source")
                if name == "sovereign_custody" and projected.get("ref_date") != public_sources["custody"]["reference_date"]:
                    raise ValueError("custody reference date differs from source")
                if name == "fusion_index" and projected.get("date") != max(dates):
                    raise ValueError("fusion reference date differs from sources")
                out[name] = projected
        states = [section["status"] for section in out["sections"].values()]
        out["status"] = "fresh" if all(state == "fresh" for state in states) else "partial" if "fresh" in states else "unavailable"
        if out["status"] == "unavailable":
            raise ValueError("no fresh institutional-flow readings")
        return out
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise wakeflows.WakePackError(f"public institutional-flow projection unavailable: {exc}") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load(*, now: dt.datetime | None = None) -> dict:
    try:
        request = urllib.request.Request(URL, headers={"Accept": "application/json", "User-Agent": "Seiche-Public-Flow-Reader/1"})
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=8) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("public projection exceeds size bound")
        return validate(json.loads(raw), now=now)
    except (OSError, ValueError) as exc:
        raise wakeflows.WakePackError("no valid public institutional-flow projection") from exc
