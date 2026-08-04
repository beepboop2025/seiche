"""seiche.subscribe: the optional email field behind The Week Ahead.

Seiche is a free public good and this module can never change that. It takes an
address, hands it to Listmonk, and returns. Nothing on this site consults it: no
page, no tab, no dispatch, no CSV export, no MCP tool and no API route reads a
subscriber row before deciding what to show. The field is a way to be told when
the Monday letter lands, not a door.

Where the address goes, and where it must never go
--------------------------------------------------
Listmonk runs on the box, on loopback, with its OWN Postgres database, reached
through ``SEICHE_LIST_ENDPOINT``. This module imports neither ``seiche.store``
nor ``sqlite3``, and that is the point rather than an accident: ``seiche.sqlite``
holds subscriber password hashes, and a marketing list has no business in the
same file as a credential store. Different engine, different user, different
backup target. The test suite asserts the import boundary instead of trusting
this paragraph.

Off by default, and off means off
---------------------------------
With ``SEICHE_LIST_ENDPOINT`` or ``SEICHE_LIST_ID`` unset the feature is OFF,
not broken. ``enabled()`` is False, ``GET /api/subscribe`` says so, and the
front door renders a plain ``mailto:`` link to the desk instead of a form. That
is what makes this safe to merge before Listmonk exists, and it is also the
rollback: rename the environment file, restart, and the front door quietly goes
back to a mailto link with no edit under pressure.

``SEICHE_LIST_ID`` is the list **UUID**, not the numeric id. The public endpoint
takes ``list_uuids``; the numeric id belongs to the authenticated admin API,
which this deliberately never calls, because that would mean holding an admin
credential to do a job that needs none.

Double opt-in is not optional and not ours to skip
--------------------------------------------------
The public endpoint creates the subscriber ``unconfirmed`` against a double
opt-in list, mails the one confirmation link, and only a click on that link
promotes them to ``confirmed``. Only ``confirmed`` addresses receive a campaign.
Nothing here can bypass that, because nothing here holds a credential that
could: the endpoint is unauthenticated by design and has no other mode. The
click, with its timestamp, is the consent record.

A failure downstream is not the reader's problem
------------------------------------------------
If Listmonk is slow, down, or answers 500, that is an operator problem. The
reader gets a normal response and the failure lands in the log, because a
newsletter box being unhappy must never turn into an error in someone's face on
a free public board. ``submit()`` returns False in that case so the caller can
tell the reader something honest rather than something confident.

No address is ever logged. A log line that would help debug a delivery problem
is also a log line that spills a reader's email into a file that gets tailed,
grepped and pasted; the status code is enough to tell a dead endpoint from a
rejected payload.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

log = logging.getLogger("seiche.subscribe")

# Where a reader is sent when the list is not wired up. Also the address in
# every "this did not work" path, so there is always a human at the end.
DESK_EMAIL = "desk@seiche.info"

# What the reader is signing up for, named in one place so the API, the UI copy
# and the runbook cannot drift.
LIST_NAME = "The Week Ahead"

# Listmonk sits on loopback. Four seconds is generous for that hop and short
# enough that a wedged newsletter box cannot hold a web worker.
TIMEOUT_S = 4.0

# RFC 5321 caps a reverse-path at 254 octets. Anything longer is not an address.
MAX_EMAIL_LEN = 254

# Deliberately permissive: this is a typo catcher, not an RFC 5322 parser.
# Listmonk validates for real, and a regex strict enough to be "correct" here
# would reject valid addresses and hand a practitioner a false error.
_EMAIL_RE = re.compile(r"^[^@\s,;:<>\"'\\]{1,64}@[A-Za-z0-9]([A-Za-z0-9.-]{0,182}[A-Za-z0-9])?\.[A-Za-z]{2,63}$")


def endpoint() -> str:
    """Listmonk's public subscription endpoint, from the operator's env file."""
    return (os.getenv("SEICHE_LIST_ENDPOINT") or "").strip()


def list_id() -> str:
    """The list UUID. Not the numeric id: the public endpoint takes UUIDs."""
    return (os.getenv("SEICHE_LIST_ID") or "").strip()


def enabled() -> bool:
    """True only when BOTH dials are set. Half-configured is off, not partly on:
    posting to a real endpoint with an empty list UUID would create subscribers
    attached to no list, which is a silent data mess rather than a visible
    failure."""
    return bool(endpoint() and list_id())


def clean_email(raw: object) -> str | None:
    """Normalise and sanity-check. Returns None for anything that is not
    plausibly an address, so the caller can say so without calling Listmonk."""
    if not isinstance(raw, str):
        return None
    email = raw.strip()
    if not email or len(email) > MAX_EMAIL_LEN:
        return None
    # Only the domain is case-insensitive; the local part is not ours to fold.
    local, _, domain = email.rpartition("@")
    if local and domain:
        email = f"{local}@{domain.lower()}"
    return email if _EMAIL_RE.match(email) else None


def submit(email: str) -> bool:
    """Hand the address to Listmonk. True if it accepted, False if anything went
    wrong on our side of the wire.

    False is not an error the reader should see as a failure of their action:
    the caller still answers them normally. It exists so the caller can say "if
    the confirmation does not arrive, mail the desk" instead of promising an
    email that a dead endpoint will never send.
    """
    if not enabled():
        return False

    body = json.dumps({
        "email": email,
        "name": "",
        # The public endpoint takes UUIDs. A numeric id here is accepted as
        # JSON and then matches no list.
        "list_uuids": [list_id()],
    }).encode()
    req = urllib.request.Request(
        endpoint(),
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "seiche-front-door"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            ok = 200 <= r.status < 300
        if not ok:
            log.warning("listmonk refused a subscription (status %s)", r.status)
        return ok
    except urllib.error.HTTPError as exc:
        # 409 is Listmonk saying the address is already on the list. That is a
        # fine outcome for the reader (they are subscribed either way) and a
        # boring one for the log, so it is not a warning.
        if exc.code == 409:
            log.info("listmonk: address already on the list")
            return True
        log.warning("listmonk rejected a subscription (status %s)", exc.code)
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Timeout, connection refused, DNS, malformed endpoint. The operator
        # needs to see this; the reader does not.
        log.warning("listmonk unreachable: %s", type(exc).__name__)
        return False


def status() -> dict:
    """What the front door needs to decide between a form and a mailto link.

    Never leaks the endpoint or the list UUID: those are operator configuration,
    and a public GET is not the place to publish where the newsletter box lives.
    """
    return {
        "enabled": enabled(),
        "list": LIST_NAME,
        "mailto": DESK_EMAIL,
        "double_opt_in": True,
        "gates_nothing": True,
    }
