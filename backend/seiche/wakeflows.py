"""Institutional-flows pack reader (wake engine -> the board).

The wake engine (repo: ~/dev/wake, box: /opt/wake) nowcasts hedge-fund
/ pension / sovereign positioning from public prints only — CFTC TFF,
Fed H.4.1 custody, OFR repo, EDGAR — and drops a provenance-stamped
pack on a timer. This module loads + validates that pack; the
`institutional_flows` MCP tool serves it. Stdlib only: a missing or
malformed pack must degrade the tool, never the server.
"""

from __future__ import annotations

import datetime as dt
import json
import os

DEFAULT_PATH = "/var/lib/wake/wake_seiche.json"

# Weekly inputs (CoT Friday, custody Thursday) + weekend slack. Older
# than this and the reading is not current enough to serve.
MAX_AGE_DAYS = 9

_REQUIRED = ("product", "schema_version", "generated_at", "as_of",
             "method_versions", "provenance", "payload")


class WakePackError(RuntimeError):
    pass


def pack_path() -> str:
    return os.environ.get("WAKE_PACK_PATH", DEFAULT_PATH)


def load(path: str | None = None,
         max_age_days: int = MAX_AGE_DAYS,
         today: dt.date | None = None) -> dict:
    p = path or pack_path()
    try:
        with open(p) as f:
            pack = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise WakePackError(f"no readable pack at {p} ({e})") from e
    for k in _REQUIRED:
        if k not in pack:
            raise WakePackError(f"pack missing envelope key '{k}'")
    if pack["product"] != "seiche":
        raise WakePackError(f"pack is for '{pack['product']}', not seiche")
    if str(pack["schema_version"]).split(".")[0] != "1":
        raise WakePackError(f"unsupported schema {pack['schema_version']}")
    try:
        as_of = dt.date.fromisoformat(str(pack["as_of"])[:10])
    except ValueError as e:
        raise WakePackError(f"bad as_of {pack['as_of']!r}") from e
    age = ((today or dt.date.today()) - as_of).days
    if age > max_age_days:
        raise WakePackError(f"pack stale: as_of {as_of} is {age}d old "
                            f"(limit {max_age_days}d)")
    return pack


def readings(pack: dict) -> dict:
    """Flatten the board-relevant numbers. Degraded sections stay
    absent — the caller sees exactly what the engine could not fill."""
    pf = pack["payload"].get("positioning_flows", {})
    out: dict = {"as_of": pack["as_of"],
                 "method_versions": pack["method_versions"]}
    basis = pf.get("basis_nowcast") or {}
    if basis:
        out["basis_trade"] = {
            "size_usd_bn": basis.get("basis_size_usd_bn"),
            "gross_short_usd_bn": basis.get("gross_short_usd_bn"),
            "z_3y": basis.get("z_basis"),
            "delta_4w_usd_bn": basis.get("delta_4w_usd_bn"),
            "fragile": basis.get("fragile"),
            "funding_spread_bp": basis.get("funding_spread_bp"),
            "ref_date": basis.get("ref_date"),
            "notional_basis": basis.get("notional_basis"),
        }
        out["pension_duration"] = {
            "net_long_usd_bn": basis.get("pension_long_usd_bn"),
            "z_3y": basis.get("z_pension"),
        }
    custody = pf.get("sovereign_custody") or {}
    if custody:
        out["sovereign_custody"] = custody
    fusion = pf.get("fusion_index") or {}
    if fusion:
        out["fusion_index"] = fusion
    hawkes = pack["payload"].get("stress_endogeneity") or {}
    if hawkes:
        out["stress_endogeneity"] = {
            "branching_ratio": hawkes.get("branching_ratio"),
            "verdict": hawkes.get("endogeneity"),
            "p_event_next": hawkes.get("p_event_next"),
            "n_events": hawkes.get("n_events"),
            "lr_stat_vs_poisson": hawkes.get("lr_stat_vs_poisson"),
        }
    return out
