"""Bounded operational aggregates for the opt-in, participating product ingress.

No request body, IP address, user-agent, query, credential or market entity is
accepted by this interface. Countries must come from the trusted edge adapter;
they locate a request's network origin, not an institution or a person.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, delete, insert, select, update

from .crypto import canonical, digest
from .models import NetworkError
from .store import orgs

PRODUCTS = frozenset({"seiche", "liquilens", "undertow", "palimpsest", "noisefloor", "narcoscope",
                      "financial-evidence", "seiche-agent-network", "other"})
METHODS = frozenset({"GET", "POST", "DELETE", "PATCH", "PUT", "HEAD", "OPTIONS", "MCP", "other",
    "initialize", "notifications/initialized", "notifications/cancelled", "ping", "tools/list", "tools/call",
    "resources/list", "resources/read", "resources/templates/list", "prompts/list", "prompts/get", "logging/setLevel", "completion/complete"})
TRAFFIC_CLASSES = frozenset({"synthetic", "internal", "backtest", "shadow", "paper", "operator_reported_production", "unknown"})
FRESHNESS = frozenset({"fresh", "stale", "restricted", "unknown"})
OUTCOMES = frozenset({"success", "error", "unknown"})
COUNTRIES = frozenset("""AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split())
LATENCY_BOUNDS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000)
meta = MetaData()
daily = Table("network_analytics_daily", meta,
    Column("key", String(64), primary_key=True), Column("day", String(10), index=True),
    Column("org_id", String(36), index=True), Column("agent_id", String(36)), Column("product", String(32)),
    Column("tool", String(80)), Column("method", String(40)), Column("http_status", Integer),
    Column("country", String(7)), Column("traffic_class", String(32)), Column("freshness", String(16)),
    Column("tool_outcome", String(8)), Column("requests", Integer), Column("duration_sum_ms", Float),
    Column("histogram", Text), Column("source_age_count", Integer), Column("source_age_sum_seconds", Float),
    Column("source_age_max_seconds", Float))
freshness_checks = Table("network_source_freshness_daily", meta,
    Column("key", String(64), primary_key=True), Column("day", String(10), index=True),
    Column("product", String(32)), Column("source", String(80)), Column("freshness", String(16)),
    Column("checks", Integer), Column("source_age_count", Integer), Column("source_age_sum_seconds", Float),
    Column("source_age_max_seconds", Float))


def _day(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).date()


def _nonnegative(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise NetworkError("invalid_analytics", f"{name} must be a finite nonnegative number within operating bounds")
    return float(value)


class Analytics:
    def __init__(self, store, *, tool_registry=None, source_registry=None, retention_days=30):
        if not isinstance(retention_days, int) or not 1 <= retention_days <= 30:
            raise ValueError("Operational analytics retention must be between 1 and 30 UTC days")
        self.store, self.retention_days = store, retention_days
        self.tool_registry = self._registry(tool_registry)
        self.source_registry = self._registry(source_registry)
        meta.create_all(store.engine)

    @staticmethod
    def _registry(value):
        result = {}
        for product, labels in (value or {}).items():
            if product not in PRODUCTS or not isinstance(labels, (set, frozenset, list, tuple)) or len(labels) > 256:
                raise ValueError("Registry must use known products and at most 256 labels per product")
            if any(not isinstance(label, str) or not label or len(label) > 80 or not all(c.isalnum() or c in "_.:-" for c in label) for label in labels):
                raise ValueError("Registry labels must be bounded tool/source identifiers")
            result[product] = frozenset(labels)
        return result

    def _cutoff(self):
        # Exactly 30 day buckets including the current UTC day, not 31.
        return (_day(self.store.clock()) - timedelta(days=self.retention_days - 1)).isoformat()

    @staticmethod
    def _product(product):
        if not isinstance(product, str) or len(product) > 80:
            raise NetworkError("invalid_analytics", "Product label is invalid")
        return product if product in PRODUCTS else "other"

    def record(self, principal, *, product, tool, method, http_status, duration_ms, country="unknown",
               traffic_class="unknown", freshness="unknown", source_age_seconds=None, tool_outcome="unknown"):
        """Record one HTTP/MCP exchange; identity None is anonymous service traffic.

        This local method is not an unauthenticated analytics-ingestion API.
        Callers provide only trusted protocol metadata and registry labels.
        """
        product = self._product(product)
        if not isinstance(tool, str) or len(tool) > 256 or not isinstance(method, str) or len(method) > 80:
            raise NetworkError("invalid_analytics", "Tool or method label is invalid")
        tool = tool if tool in self.tool_registry.get(product, ()) else ("none" if tool in {"", "none"} else "other")
        method = method if method in METHODS else "other"
        if isinstance(http_status, bool) or not isinstance(http_status, int) or not 100 <= http_status <= 599:
            raise NetworkError("invalid_analytics", "HTTP status must be a standard status code")
        duration_ms = _nonnegative(duration_ms, "duration_ms", 86400000)
        if country != "unknown" and country not in COUNTRIES:
            raise NetworkError("invalid_analytics", "Country must be an ISO 3166 alpha-2 code or unknown")
        if traffic_class not in TRAFFIC_CLASSES or freshness not in FRESHNESS or tool_outcome not in OUTCOMES:
            raise NetworkError("invalid_analytics", "Unknown bounded analytics classification")
        age = None if source_age_seconds is None else _nonnegative(source_age_seconds, "source_age_seconds", 100 * 366 * 86400)
        labels = dict(day=_day(self.store.clock()).isoformat(), org_id=principal.org_id if principal else "",
                      agent_id=(principal.agent_id or "") if principal else "", product=product, tool=tool,
                      method=method, http_status=http_status, country=country, traffic_class=traffic_class,
                      freshness=freshness, tool_outcome=tool_outcome)
        key = digest(canonical(labels))
        histogram_index = next((i for i, upper in enumerate(LATENCY_BOUNDS) if duration_ms <= upper), len(LATENCY_BOUNDS))
        with self.store._transaction() as db:
            if principal is not None:
                self.store._principal(db, principal)
            prior = db.execute(select(daily).where(daily.c.key == key)).mappings().first()
            histogram = json.loads(prior["histogram"]) if prior else [0] * (len(LATENCY_BOUNDS) + 1)
            histogram[histogram_index] += 1
            values = dict(requests=(prior["requests"] if prior else 0) + 1,
                          duration_sum_ms=(prior["duration_sum_ms"] if prior else 0) + duration_ms,
                          histogram=json.dumps(histogram), source_age_count=(prior["source_age_count"] if prior else 0) + (age is not None),
                          source_age_sum_seconds=(prior["source_age_sum_seconds"] if prior else 0) + (age or 0),
                          source_age_max_seconds=max(prior["source_age_max_seconds"] if prior else 0, age or 0))
            if prior:
                db.execute(update(daily).where(daily.c.key == key).values(**values))
            else:
                db.execute(insert(daily).values(key=key, **labels, **values))
            self._purge(db)

    def record_source_freshness(self, *, product, source, freshness, source_age_seconds=None):
        """Local monitoring worker hook, distinct from latency and user activity."""
        product = self._product(product)
        if not isinstance(source, str) or len(source) > 256 or freshness not in FRESHNESS:
            raise NetworkError("invalid_analytics", "Source freshness labels are invalid")
        source = source if source in self.source_registry.get(product, ()) else "other"
        age = None if source_age_seconds is None else _nonnegative(source_age_seconds, "source_age_seconds", 100 * 366 * 86400)
        labels = dict(day=_day(self.store.clock()).isoformat(), product=product, source=source, freshness=freshness)
        key = digest(canonical(labels))
        with self.store._transaction() as db:
            prior = db.execute(select(freshness_checks).where(freshness_checks.c.key == key)).mappings().first()
            values = dict(checks=(prior["checks"] if prior else 0) + 1,
                          source_age_count=(prior["source_age_count"] if prior else 0) + (age is not None),
                          source_age_sum_seconds=(prior["source_age_sum_seconds"] if prior else 0) + (age or 0),
                          source_age_max_seconds=max(prior["source_age_max_seconds"] if prior else 0, age or 0))
            if prior:
                db.execute(update(freshness_checks).where(freshness_checks.c.key == key).values(**values))
            else:
                db.execute(insert(freshness_checks).values(key=key, **labels, **values))
            self._purge(db)

    def _purge(self, db):
        cutoff = self._cutoff()
        count = db.execute(delete(daily).where(daily.c.day < cutoff)).rowcount
        count += db.execute(delete(freshness_checks).where(freshness_checks.c.day < cutoff)).rowcount
        return count

    def purge(self):
        with self.store._transaction() as db:
            return {"purged_aggregate_buckets": self._purge(db)}

    @staticmethod
    def _latency(histogram, duration_sum):
        count = sum(histogram)
        def percentile(fraction):
            if count == 0:
                return None
            target, cumulative = math.ceil(count * fraction), 0
            for index, bucket_count in enumerate(histogram):
                cumulative += bucket_count
                if cumulative >= target:
                    return {"lower_bound_ms": 0 if index == 0 else LATENCY_BOUNDS[index - 1],
                            "upper_bound_ms": LATENCY_BOUNDS[index] if index < len(LATENCY_BOUNDS) else None,
                            "exact": False, "overflow": index == len(LATENCY_BOUNDS)}
        return {"unit": "ms", "observations": count, "mean_ms": duration_sum / count if count else None,
                "p50": percentile(.5), "p95": percentile(.95),
                "buckets": [{"upper_bound_ms": LATENCY_BOUNDS[i] if i < len(LATENCY_BOUNDS) else None, "count": value}
                            for i, value in enumerate(histogram)]}

    @staticmethod
    def _summarize(rows):
        counts = Counter()
        histogram = [0] * (len(LATENCY_BOUNDS) + 1)
        duration_sum, source_age_sum, source_age_count, source_age_max = 0.0, 0.0, 0, 0.0
        countries, products, tools, days, freshness, traffic = (Counter() for _ in range(6))
        performance = defaultdict(lambda: {"requests": 0, "http_errors": 0, "tool_errors": 0,
            "duration_sum": 0.0, "histogram": [0] * (len(LATENCY_BOUNDS) + 1), "freshness": Counter(), "countries": Counter()})
        for row in rows:
            n = row["requests"]
            counts["requests"] += n
            counts["http_errors"] += n if row["http_status"] >= 400 else 0
            counts["http_2xx"] += n if 200 <= row["http_status"] < 300 else 0
            counts["tool_errors"] += n if row["tool_outcome"] == "error" else 0
            counts["tool_successes"] += n if row["tool_outcome"] == "success" else 0
            counts["tool_outcome_unknown"] += n if row["tool_outcome"] == "unknown" else 0
            counts["anonymous_requests"] += n if not row["org_id"] else 0
            counts["country_unknown_requests"] += n if row["country"] == "unknown" else 0
            countries[row["country"]] += n
            products[row["product"]] += n
            tools[(row["product"], row["tool"])] += n
            days[row["day"]] += n
            freshness[row["freshness"]] += n
            traffic[row["traffic_class"]] += n
            duration_sum += row["duration_sum_ms"]
            source_age_sum += row["source_age_sum_seconds"]
            source_age_count += row["source_age_count"]
            source_age_max = max(source_age_max, row["source_age_max_seconds"])
            for i, value in enumerate(json.loads(row["histogram"])):
                histogram[i] += value
            for group in ((row["product"],), (row["product"], row["tool"])):
                item = performance[group]
                item["requests"] += n
                item["http_errors"] += n if row["http_status"] >= 400 else 0
                item["tool_errors"] += n if row["tool_outcome"] == "error" else 0
                item["duration_sum"] += row["duration_sum_ms"]
                item["freshness"][row["freshness"]] += n
                item["countries"][row["country"]] += n
                for i, value in enumerate(json.loads(row["histogram"])):
                    item["histogram"][i] += value
        def group_detail(key):
            item = performance[key]
            return {"requests": item["requests"], "http_errors": item["http_errors"], "tool_errors": item["tool_errors"],
                    "latency": Analytics._latency(item["histogram"], item["duration_sum"]),
                    "freshness": {k: item["freshness"][k] for k in sorted(FRESHNESS)}, "countries": dict(item["countries"])}
        base_counts = dict.fromkeys(("requests", "http_errors", "http_2xx", "tool_errors", "tool_successes", "tool_outcome_unknown", "anonymous_requests", "country_unknown_requests"), 0)
        return {"totals": base_counts | dict(counts),
                "countries": [{"country": key, "requests": count} for key, count in countries.most_common()],
                "products": [{"product": key} | group_detail((key,)) for key, count in products.most_common()],
                "tools": [{"product": key[0], "tool": key[1]} | group_detail(key) for key, count in tools.most_common()],
                "days": [{"day": key, "requests": count} for key, count in sorted(days.items())],
                "traffic_classes": dict(traffic), "freshness": {k: freshness[k] for k in sorted(FRESHNESS)},
                "source_age": {"observations": source_age_count, "mean_seconds": source_age_sum / source_age_count if source_age_count else None,
                               "max_seconds": source_age_max if source_age_count else None},
                "latency": Analytics._latency(histogram, duration_sum)}

    def report(self, principal=None):
        """Owner sees its own tenants; agent sees itself. None is LOCAL ADMIN ONLY.

        Do not expose the None form from an unauthenticated endpoint. Counts of
        observed identities are not verified institutions, users, trades or use.
        """
        with self.store._transaction() as db:
            if principal is not None:
                self.store._principal(db, principal)
            query = select(daily).where(daily.c.day >= self._cutoff())
            if principal is not None:
                query = query.where(daily.c.org_id == principal.org_id)
                if principal.agent_id:
                    query = query.where(daily.c.agent_id == principal.agent_id)
            rows = [dict(row) for row in db.execute(query).mappings()]
            known = {row["id"]: row["verification"] for row in db.execute(select(orgs)).mappings()}
            source_rows = [dict(row) for row in db.execute(select(freshness_checks).where(freshness_checks.c.day >= self._cutoff())).mappings()] if principal is None else []
        result = self._summarize(rows)
        today = _day(self.store.clock())
        current_start, previous_start = (today - timedelta(days=6)).isoformat(), (today - timedelta(days=13)).isoformat()
        activity_rows = [r for r in rows if r["traffic_class"] not in {"synthetic", "internal"}]
        current_orgs = {r["org_id"] for r in activity_rows if r["org_id"] and r["day"] >= current_start}
        previous_orgs = {r["org_id"] for r in activity_rows if r["org_id"] and previous_start <= r["day"] < current_start}
        active_orgs = {r["org_id"] for r in rows if r["org_id"]}
        active_agents = {r["agent_id"] for r in rows if r["agent_id"]}
        numerator, denominator = len(current_orgs & previous_orgs), len(previous_orgs)
        result.update({"scope": "operator_global" if principal is None else ("own_agent" if principal.agent_id else "own_organization"),
            "retention_days": self.retention_days, "day_basis": "UTC", "cutoff_day": self._cutoff(),
            "activity": {"observed_org_ids": len(active_orgs), "observed_agent_ids": len(active_agents),
                         "operator_verified_org_ids": sum(known.get(org) == "operator_verified" for org in active_orgs),
                         "verified_external_users": None, "verified_trading_adoption": None,
                         "weekly_returning_orgs": {"numerator": numerator, "eligible_denominator": denominator,
                             "fraction": numerator / denominator if denominator else None,
                             "previous_start_day": previous_start, "current_start_day": current_start,
                             "through_day": today.isoformat(), "continuous_collection_verified": False,
                             "traffic_filter": "Excludes synthetic and internal classifications; unknown remains unknown",
                             "definition": "Organizations with observed requests in both adjacent seven-day windows; not installation-cohort retention"}},
            "source_monitor": [{k: v for k, v in row.items() if k != "key"} for row in source_rows],
            "source_monitor_scope": "local_operator_only", "model_training": False,
            "counts_mean": "Observed HTTP/MCP exchanges; not people, customers, completed work, trades or downloads",
            "country_meaning": "Trusted edge network origin; VPNs and cloud gateways may differ from user location",
            "source_freshness": "Explicit source assessment only; never inferred from transport latency"})
        return result
