"""seiche.badge — the sealed-record badge.

One public JSON endpoint in the shields.io "endpoint" schema, so the README
(or anyone's page) can render a live badge of the one asset a competitor
cannot clone in a weekend: how long the as-published record has run unbroken.

The day count comes from the same `pit:*` keys the pit_gap dead-man alert
scans, and the color turns red the moment the chain has a hole. The badge has
to be capable of embarrassing us — a badge that can only say good things
proves nothing.
"""

from __future__ import annotations

import sqlite3

from seiche.config import DB_PATH


def record_badge() -> dict:
    """The shields.io endpoint payload for the as-published record.

    Green: every business day from the first sealed reading to the latest is
    present. Red: at least one business day is missing, and the message says
    how many — the same holes pit_gap alerts on, published rather than hidden.
    """
    import pandas as pd

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS blobs (key TEXT PRIMARY KEY, payload TEXT)"
        )
        keys = [
            r[0]
            for r in conn.execute(
                "SELECT key FROM blobs WHERE key LIKE 'pit:%' ORDER BY key"
            )
        ]
    days = pd.DatetimeIndex([k.split("pit:", 1)[1] for k in keys])
    if len(days) == 0:
        return {
            "schemaVersion": 1,
            "label": "sealed record",
            "message": "no record yet",
            "color": "lightgrey",
        }
    expected = pd.bdate_range(days.min(), days.max())
    missing = expected.difference(days)
    if len(missing) > 0:
        return {
            "schemaVersion": 1,
            "label": "sealed record",
            "message": f"HOLE: {len(missing)} business days missing",
            "color": "red",
        }
    span_days = (days.max() - days.min()).days + 1
    return {
        "schemaVersion": 1,
        "label": "sealed record",
        "message": f"{span_days} days unbroken since {days.min().date().isoformat()}",
        "color": "brightgreen",
    }
