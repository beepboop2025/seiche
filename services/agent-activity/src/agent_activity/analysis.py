"""Reconcile specific observations; no profitability inferred from token movement."""

from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import time

from .contracts import Contribution, IdentityClaim
from .store import Rejected, canonical, digest


def same_fill(submitted, observed):
    return (submitted.kind == "fill" and observed.get("kind") == "fill"
        and submitted.venue == "hyperliquid" and submitted.mode == "live"
        and submitted.external_ref == observed.get("external_ref")
        and submitted.instrument == observed.get("instrument") and submitted.side == observed.get("side")
        and Decimal(submitted.quantity) == Decimal(observed["quantity"])
        and (submitted.price is None or Decimal(submitted.price) == Decimal(observed["price"]))
        and abs(submitted.occurred_at.timestamp()-observed["occurred_at"]) <= 1
        and (submitted.fee is None or (submitted.currency == observed.get("fee_currency") and Decimal(submitted.fee) == Decimal(observed["fee"])))
        and (submitted.amount is None))  # Notional currency is not inferred from the fee currency.


def reconcile(store):
    links, count, reviewed = store.links(), 0, []
    with store.db(write=True) as db:
        rows = db.execute("""SELECT e.*,c.revoked,c.expires AS permission_expires FROM contributions e
            JOIN contributors c ON e.source=c.id WHERE e.expires>? AND c.expires>? AND c.revoked=0""", (time.time(), time.time())).fetchall()
        public = [json.loads(r[0]) for r in db.execute("SELECT data FROM public_events WHERE kind='fill'")]
        index = {(e.get("account"), e.get("external_ref")): e for e in public}
        for row in rows:
            event = Contribution.model_validate(store.decrypt(row["payload"]))
            if event.kind != "fill" or event.mode != "live":
                continue
            count += 1
            for link in links:
                if link["contributor"] != row["source"] or link["agent_id"] != event.agent_id or link["agent_version"] != event.agent_version or link["venue"] != event.venue:
                    continue
                claim = IdentityClaim.model_validate({k: v for k, v in link.items() if k in IdentityClaim.model_fields})
                if not claim.valid_from <= event.occurred_at <= claim.valid_until:
                    continue
                observed = index.get((link["account"].lower(), event.external_ref))
                if observed and same_fill(event, observed):
                    db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?)",
                               (row["source"], event.event_id, observed["id"], time.time(), digest(observed)))
                    reviewed.append({"contributor": row["source"], "event_id": event.event_id, "public_event": observed["id"]})
                    break
    return {"considered_live_fills": count, "reconciled": len(reviewed), "unmatched": count-len(reviewed),
            "coverage_percent": len(reviewed)/count*100 if count else None,
            "denominator": "submitted live fills in active contributor records", "ai_authorship": "reviewer_attested_identity; not cryptographically proven"}


def dossier(store, identifier):
    with store.db() as db:
        entity = db.execute("SELECT * FROM entities WHERE id=?", (identifier,)).fetchone()
    if not entity:
        raise Rejected("entity_not_found", 404)
    record = json.loads(entity["data"])
    return {"schema": "agent-activity.dossier.v1", "identity": record, "source": entity["source"],
        "first_seen": entity["first_seen"], "last_seen": entity["last_seen"],
        "evidence": store.receipt(entity["receipt"]), "verification": {
            "registration_observed": entity["kind"] == "registered_agent", "ai_control_verified": False,
            "trading_account_link": "not_established", "performance": "not_established"},
        "missing": ["independent agent-to-account attribution", "complete execution or commerce outcomes", "portfolio cash flows and costs"],
        "use": "research evidence; no performance or capability endorsement"}


def rights_fingerprint(db):
    rows = [dict(r) for r in db.execute("SELECT id,expires,revoked,grant_ref,resale_ref,independent_ref FROM contributors ORDER BY id")]
    return digest(rows)


def weekly_export(store, week):
    try:
        start = datetime.strptime(week, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise Rejected("week_must_be_iso_date")
    end = start+timedelta(days=7)
    if start.weekday() != 0 or end.timestamp() > time.time():
        raise Rejected("only_closed_utc_monday_weeks")
    cells = defaultdict(list)
    with store.db(write=True) as db:
        rows = db.execute("""SELECT e.*,c.independent_ref,c.resale_ref,c.expires AS grant_expires,r.public_event,r.evidence_digest
            FROM contributions e JOIN contributors c ON c.id=e.source JOIN reviews r ON r.source=e.source AND r.event_id=e.id
            WHERE c.revoked=0 AND c.expires>? AND e.expires>? AND c.resale_ref IS NOT NULL
            AND e.received<? AND r.reviewed<?""", (time.time(), time.time(), end.timestamp(), end.timestamp())).fetchall()
        used = set()
        for row in rows:
            value = store.decrypt(row["payload"])
            event = Contribution.model_validate(value)
            if event.mode != "live" or not start <= event.occurred_at < end:
                continue
            independent = db.execute("SELECT data FROM public_events WHERE id=?", (row["public_event"],)).fetchone()
            if not independent or digest(json.loads(independent[0])) != row["evidence_digest"]:
                continue
            if row["public_event"] in used:
                continue
            used.add(row["public_event"])
            observed = json.loads(independent[0])
            account = observed.get("account")
            if not account:
                continue
            cells[(event.venue, event.kind)].append({"agent": event.venue+":"+account.lower(),
                "proof": [row["public_event"], row["evidence_digest"]],
                "contributor": row["independent_ref"], "expiry": min(row["expires"], row["grant_expires"])})
        output, suppressed, expiry, proofs = [], 0, time.time()+86400, []
        for (venue, kind), values in sorted(cells.items()):
            owners = defaultdict(set)
            for value in values:
                owners[value["agent"]].add(value["contributor"])
            values = [v for v in values if len(owners[v["agent"]]) == 1]
            agents = {v["agent"]: v["contributor"] for v in values}
            concentration = Counter(agents.values())
            if len(agents) < 20 or len(concentration) < 3 or max(concentration.values())/len(agents) > .5:
                suppressed += 1
                continue
            expiry = min(expiry, *[v["expiry"] for v in values])
            proofs.extend(v["proof"] for v in values)
            output.append({"venue": venue, "kind": kind, "participating_agent_accounts": len(agents),
                "independent_contributors": len(concentration), "reconciled_events": len(values)})
        manifest = {"schema": "agent-activity.weekly.v1", "week": week, "created_at": time.time(), "expires_at": expiry,
            "rows": output, "suppressed_cells": suppressed, "status": "qualified_candidate" if output else "insufficient_qualified_data",
            "rights_hash": rights_fingerprint(db), "record_level_resale": False,
            "training_permission": False, "representativeness": "participating account cohort only", "delivered": False, "_evidence": proofs}
        identifier = digest(manifest)
        db.execute("INSERT INTO exports VALUES(?,?,?,?)", (identifier, manifest["created_at"], manifest["rights_hash"], canonical(manifest).decode()))
    return dict({k:v for k,v in manifest.items() if k != "_evidence"}, export_id=identifier)


def export_status(store, identifier):
    with store.db() as db:
        row = db.execute("SELECT * FROM exports WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise Rejected("export_not_found", 404)
        manifest = json.loads(row["manifest"])
        valid = row["rights_hash"] == rights_fingerprint(db) and manifest["expires_at"] > time.time()
        for event_id, expected in manifest.get("_evidence", []):
            proof = db.execute("SELECT data FROM public_events WHERE id=?", (event_id,)).fetchone()
            if not proof or digest(json.loads(proof[0])) != expected:
                valid = False
    return {"export_id": identifier, "valid": valid, "release_eligible": valid and manifest["status"] == "qualified_candidate",
            "status": manifest["status"] if valid else "invalidated", "delivery_requires_current_revalidation": True}
