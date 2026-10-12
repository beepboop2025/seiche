"""Bounded dataset-demand aggregates, committed with their operational exchange."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import (Column, Float, Index, Integer, MetaData, String, Table, case, delete,
                        func, insert, literal, select, union_all, update)

from .crypto import canonical, digest
from .data_catalog import VERSION, entries, validate_classification
from .models import NetworkError

meta = MetaData()
daily = Table("network_dataset_daily", meta,
    Column("key", String(64), primary_key=True), Column("day", String(10), nullable=False),
    Column("org_id", String(36), nullable=False), Column("agent_id", String(36), nullable=False),
    Column("product", String(40), nullable=False), Column("tool", String(80), nullable=False),
    Column("dataset_id", String(96), nullable=False), Column("basis", String(16), nullable=False),
    Column("operation", String(24), nullable=False), Column("catalog_version", String(40), nullable=False),
    Column("http_status", Integer, nullable=False), Column("tool_outcome", String(8), nullable=False),
    Column("traffic_class", String(32), nullable=False), Column("response_state", String(16), nullable=False),
    Column("requests", Integer, nullable=False), Column("duration_sum_ms", Float, nullable=False))
Index("ix_network_dataset_scope_day", daily.c.org_id, daily.c.agent_id, daily.c.day)
Index("ix_network_dataset_day_family", daily.c.day, daily.c.product, daily.c.dataset_id)
Index("ix_network_dataset_traffic_day", daily.c.traffic_class, daily.c.day)


def prepare(labels, classification, response_state):
    validate_classification(classification, labels["product"])
    if response_state not in {"unknown", "empty", "nonempty"}:
        raise NetworkError("invalid_analytics", "Invalid bounded response classification", 400)
    values = {key: labels[key] for key in ("day", "org_id", "agent_id", "product", "tool", "http_status", "tool_outcome", "traffic_class")}
    values.update({"dataset_id": classification.dataset_id, "basis": classification.basis,
                   "operation": classification.operation, "catalog_version": classification.catalog_version,
                   "response_state": response_state})
    return digest(canonical(values)), values


def record(db, key, values, duration_ms):
    """Use the caller's transaction and already-held dataset bucket lock."""
    previous = db.execute(select(daily.c.requests, daily.c.duration_sum_ms).where(daily.c.key == key)).mappings().first()
    counters = {"requests": (previous["requests"] if previous else 0) + 1,
                "duration_sum_ms": (previous["duration_sum_ms"] if previous else 0) + duration_ms}
    if previous:
        db.execute(update(daily).where(daily.c.key == key).values(**counters))
    else:
        db.execute(insert(daily).values(key=key, **values, **counters))


def purge(db, cutoff):
    return db.execute(delete(daily).where(daily.c.day < cutoff)).rowcount


def _sum_when(condition):
    return func.coalesce(func.sum(case((condition, daily.c.requests), else_=0)), 0)


def _counts():
    return [func.coalesce(func.sum(daily.c.requests), 0).label("requests"),
            _sum_when(daily.c.http_status >= 400).label("http_errors"),
            _sum_when(daily.c.tool_outcome == "error").label("tool_errors"),
            _sum_when(daily.c.tool_outcome == "success").label("tool_successes"),
            _sum_when(daily.c.response_state == "empty").label("empty_responses"),
            _sum_when(daily.c.org_id == "").label("anonymous_requests"),
            _sum_when(daily.c.agent_id != "").label("authenticated_agent_requests"),
            _sum_when((daily.c.org_id != "") & (daily.c.agent_id == "")).label("owner_requests")]


def report(store, principal=None, *, traffic="all", limit=100):
    """None is local/operator authority only, never an anonymous public report."""
    from .analytics import TRAFFIC_CLASSES
    if not isinstance(traffic, str) or traffic not in TRAFFIC_CLASSES | {"all", "non_test"}:
        raise NetworkError("invalid_query", "Unsupported traffic filter", 400)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise NetworkError("invalid_query", "Report limit must be between 1 and 200", 400)
    with store._transaction(shared=True) as db:
        if principal is not None:
            store._principal(db, principal)
        cutoff = (datetime.fromtimestamp(store.clock(), timezone.utc).date() - timedelta(days=29)).isoformat()
        conditions = [daily.c.day >= cutoff]
        if principal is not None:
            conditions.append(daily.c.org_id == principal.org_id)
            if principal.agent_id:
                conditions.append(daily.c.agent_id == principal.agent_id)
        if traffic == "non_test":
            conditions.append(daily.c.traffic_class.not_in(["synthetic", "internal"]))
        elif traffic != "all":
            conditions.append(daily.c.traffic_class == traffic)
        basis_counts = [
            _sum_when(daily.c.basis == "unknown").label("unknown_requests"),
            _sum_when(daily.c.basis == "discovery").label("discovery_requests"),
            _sum_when(daily.c.basis.in_(["family", "exact"])).label("data_requests"),
            _sum_when(daily.c.basis == "exact").label("exact_requests"),
            _sum_when(daily.c.basis == "family").label("family_requests")]
        groups = (daily.c.product, daily.c.dataset_id, daily.c.basis, daily.c.operation)
        count_names = [column.name for column in _counts()]
        basis_names = [column.name for column in basis_counts]
        empty_groups = [literal("").label(column.name) for column in groups]
        totals_query = select(literal("totals").label("row_kind"), *empty_groups,
            literal("").label("traffic_class"), *_counts(), *basis_counts).where(*conditions)
        top = (select(*groups, *_counts()).where(*conditions).group_by(*groups)
               .order_by(func.sum(daily.c.requests).desc(), *groups).limit(limit + 1).subquery())
        rows_query = select(literal("row"), *(top.c[column.name] for column in groups),
            literal(""), *(top.c[name] for name in count_names), *(literal(0) for _ in basis_names))
        traffic_rows = (select(daily.c.traffic_class, func.sum(daily.c.requests).label("requests"))
                   .where(*conditions).group_by(daily.c.traffic_class).limit(len(TRAFFIC_CLASSES)).subquery())
        traffic_query = select(literal("traffic"), *empty_groups, traffic_rows.c.traffic_class,
            traffic_rows.c.requests, *(literal(0) for _ in count_names[1:] + basis_names))
        # One SQL statement gives all three projections the same PostgreSQL
        # READ COMMITTED snapshot while allowing concurrent shared-lock writes.
        results = db.execute(union_all(totals_query, rows_query, traffic_query)).mappings().all()
        totals = next({name: row[name] for name in count_names + basis_names}
                      for row in results if row["row_kind"] == "totals")
        rows = [{name: row[name] for name in [column.name for column in groups] + count_names}
                for row in results if row["row_kind"] == "row"]
        rows.sort(key=lambda row: (-row["requests"], *(row[column.name] for column in groups)))
        traffic_classes = {row["traffic_class"]: row["requests"] for row in results if row["row_kind"] == "traffic"}
    catalog = entries()
    for row in rows[:limit]:
        row["label"] = catalog.get(row["dataset_id"], {"label": "Previously reviewed dataset"})["label"]
    return {"schema": "seiche-agent-network.dataset-usage.v1", "catalog_version": VERSION,
            "scope": "operator_global" if principal is None else ("own_agent" if principal.agent_id else "own_organization"),
            "retention_days": 30, "cutoff_day": cutoff, "traffic": traffic, "limit": limit,
            "rows": rows[:limit], "totals": totals, "truncated": len(rows) > limit,
            "traffic_classes": traffic_classes, "collection_complete": None,
            "empty_response_meaning": "Accepted empty body or top-level JSON null, empty array, object or string; nested rows and dataset completeness are not assessed",
            "interpretation": "Observed central exchanges, not people, unique tasks, purchases or trades. "
                              "Exact means an admitted endpoint/selector, not an individual record. MCP families "
                              "do not prove exact data use. Research wrapper and upstream calls may overlap. "
                              "non_test excludes synthetic/internal only; remaining activity is not verified adoption.",
            "raw_arguments_retained": False, "raw_responses_retained": False,
            "model_training": False, "data_resale_permission": False}
