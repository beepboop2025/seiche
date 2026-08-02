"""Read + validate a wake seiche-pack. Self-contained: stdlib only,
no seiche imports, so this file cannot break the deploy path.

Usage:
    from integrations.wake.wake_source import load, readings
    pack = load()                      # WAKE_PACK_PATH or default
    cells = readings(pack)             # flat dict for board wiring
"""

from __future__ import annotations

import datetime as dt
import json
import os

DEFAULT_PATH = os.environ.get(
    "WAKE_PACK_PATH", os.path.expanduser("~/dev/wake/out/wake_seiche.json"))

MAX_AGE_DAYS = 9   # weekly inputs + weekend slack; older packs refuse

REQUIRED_ENVELOPE = ("product", "schema_version", "generated_at", "as_of",
                     "method_versions", "provenance", "payload")


class WakePackError(RuntimeError):
    pass


def load(path: str | None = None, max_age_days: int = MAX_AGE_DAYS) -> dict:
    p = path or DEFAULT_PATH
    try:
        with open(p) as f:
            pack = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise WakePackError(f"cannot read wake pack at {p}: {e}") from e
    for k in REQUIRED_ENVELOPE:
        if k not in pack:
            raise WakePackError(f"wake pack missing envelope key: {k}")
    if pack["product"] != "seiche":
        raise WakePackError(f"not a seiche pack: {pack['product']}")
    if pack["schema_version"].split(".")[0] != "1":
        raise WakePackError(
            f"unsupported wake schema {pack['schema_version']}")
    age = (dt.date.today()
           - dt.date.fromisoformat(pack["as_of"][:10])).days
    if age > max_age_days:
        raise WakePackError(f"wake pack stale: as_of {pack['as_of']} "
                            f"({age}d > {max_age_days}d)")
    return pack


def readings(pack: dict) -> dict:
    """Flatten the board-relevant numbers. Missing sections stay absent
    rather than defaulting — the caller sees exactly what degraded."""
    pf = pack["payload"].get("positioning_flows", {})
    out: dict = {"as_of": pack["as_of"],
                 "method_versions": pack["method_versions"]}
    basis = pf.get("basis_nowcast") or {}
    if basis:
        out["basis_size_usd_bn"] = basis.get("basis_size_usd_bn")
        out["basis_z"] = basis.get("z_basis")
        out["basis_fragile"] = basis.get("fragile")
        out["basis_delta_4w_usd_bn"] = basis.get("delta_4w_usd_bn")
    custody = pf.get("sovereign_custody") or {}
    if custody:
        out["custody_level_usd_bn"] = custody.get("level_usd_bn")
        out["custody_chg_13w_usd_bn"] = custody.get("chg_13w_usd_bn")
        out["custody_z_5y"] = custody.get("z_level_5y")
    fusion = pf.get("fusion_index") or {}
    if fusion:
        out["fusion_index"] = fusion.get("index")
        out["fusion_band68"] = fusion.get("band68")
        out["fusion_slope"] = fusion.get("slope")
    hawkes = pack["payload"].get("stress_endogeneity") or {}
    if hawkes:
        out["stress_branching_ratio"] = hawkes.get("branching_ratio")
        out["stress_endogeneity"] = hawkes.get("endogeneity")
        out["p_stress_event_21d"] = (hawkes.get("p_event_next") or {}
                                     ).get("21d")
    return out


if __name__ == "__main__":
    print(json.dumps(readings(load()), indent=2))
