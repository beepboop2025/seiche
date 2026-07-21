"""Windfetch — the current-affairs wind over this basin (overlay, never blended).

In the lab's oceanography, FETCH is the stretch of open water the wind
blows over to generate the waves every other engine here measures. The
Undertow repo runs the actual instrument (GDELT DOC 2.0 attention across
seven cited transmission channels, each grounded in retrieved literature
and routed to the lab surface it plausibly shocks); this engine reads the
published pack back and shows Seiche its own slice: the funding-routed
channels, with the rest of the world's wind as context.

Doctrine, stated the way the FUNDING overlay states it on the Undertow
board: this is an OVERLAY. It never enters the composite, never moves the
regime, never claims lead-time. Whether attention surges LEAD funding
stress is the Undertow prereg's bar 7a — declared there, not asserted
here. Until that study passes, the overlay answers one modest question
honestly: what is the wind doing right now, and through which cited
mechanism could it reach this water?

Honesty notes:
  - the pack's own accrual gates ride through untouched: a channel whose
    percentile is withheld (n < 30 snapshots) is shown as accruing, never
    filled, never defaulted to calm;
  - an empty funding slice is stated as exactly that — the pack routes
    channels by mechanism, and some days no funding-routed channel has
    qualifying data;
  - live-only: the pack has no archive, so a Time Machine replay before
    the pack's asof refuses rather than pretending the wind of the past
    was recorded (it was not);
  - provenance: every channel carries its source citation from the pack;
    this engine adds none of its own.
"""

from __future__ import annotations

SURFACE_KEY = "seiche_funding"


def analyze(windfetch: dict | None) -> dict:
    """The Seiche-facing read of the Fetch pack.

    windfetch: the source payload {"fetched_at", "pack"}, optionally
    carrying "replay_asof" (stamped by the Time Machine's source
    truncation); the overlay is live-only and refuses replays before the
    pack's asof.
    """
    if not windfetch or not isinstance(windfetch.get("pack"), dict):
        return {"ok": False,
                "reason": ("no Fetch pack — the Undertow public window is "
                           "unreachable and no cached copy exists; absence "
                           "is stated, never calm")}
    pack = windfetch["pack"]
    asof = pack.get("asof")
    replay_asof = windfetch.get("replay_asof")

    if replay_asof is not None and asof is not None and replay_asof < asof:
        return {"ok": False,
                "reason": (f"live-only overlay: the Fetch pack has no archive, "
                           f"so the wind of {replay_asof} was not recorded — "
                           "refusing to backfill a replay")}

    channels = [c for c in pack.get("channels", []) if isinstance(c, dict)]
    funding = [c for c in channels if c.get("surface") == SURFACE_KEY]
    others = [c for c in channels if c.get("surface") != SURFACE_KEY]

    def _row(c: dict) -> dict:
        return {
            "channel": c.get("channel"),
            "name": c.get("name"),
            "latest_surge": c.get("latest_surge"),
            "stress_pctl": c.get("stress_pctl"),
            "accruing": c.get("stress_pctl") is None,
            "obs": c.get("obs"),
            "mechanism": c.get("mechanism"),
            "source": c.get("source_id") or c.get("source"),
            "note": c.get("note"),
        }

    surges = [c.get("latest_surge") for c in channels
              if isinstance(c.get("latest_surge"), (int, float))]

    return {
        "ok": True,
        "asof": asof,
        "fetched_at": windfetch.get("fetched_at"),
        "funding_channels": [_row(c) for c in funding],
        "funding_channels_note": (
            None if funding else
            "no funding-routed channel published qualifying data in this "
            "pack — an absent read, stated not smoothed"),
        "world_context": [_row(c) for c in others],
        "max_surge_any_channel": round(max(surges), 4) if surges else None,
        "overlay": True,
        "doctrine": ("overlay only: never enters the composite, never moves "
                     "the regime, never claims lead-time — whether attention "
                     "surges LEAD funding stress is the Undertow prereg bar "
                     "7a, declared not asserted"),
        "provenance": ("Undertow FETCH pack via api.seiche.info/undertow/"
                       "fetch.json (GDELT DOC 2.0, cited transmission "
                       "taxonomy; built in the liquilens-undertow repo)"),
    }
