"""Explicit public browser contracts, with fixed origins and bounded selectors.

Source Data 1.2.0 and Corpus 1.0.0 remain separate services from OpenBB. The
admitted source identifiers are a reviewed inventory, never values learned from
untrusted request payloads. Original source receipts and monitor hashes remain
unchanged. Corpus bulk shards are intentionally outside this bounded transport.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from html import escape
import json
from pathlib import Path
import re

from . import data_catalog
from .models import NetworkError

VERSION = "central-browser-surfaces-2026-10-13"
SOURCE_ORIGIN = "https://api.seiche.info/api/v2/research-data"
BANKING_ORIGIN = "https://api.liquilens.in/api/experimental/v1/banking"
ECONOMY_ORIGIN = "https://api.seiche.info/api/series"
CORPUS_ORIGIN = "https://api.seiche.info/api/v2/corpus"
SOURCE_DATASETS = {
    "ofr-fnyr": "seiche", "ofr-mmf": "seiche", "ofr-nypd": "seiche",
    "ofr-repo": "seiche", "ofr-tyld": "seiche", "fdic-financials": "liquilens",
    "fdic-institutions": "liquilens", "ncua-call-reports": "liquilens",
    "bitcoin-settlement": "undertow", "liquid-settlement": "undertow",
    "india-reviewed-filings": "liquilens", "ecb-fx": "seiche", "ecb-estr": "seiche",
    "treasury-debt": "seiche", "treasury-cash": "seiche", "treasury-interest": "seiche",
}
ECONOMY_SERIES = frozenset({"SOFR", "DGS2", "DGS10", "INR", "WRESBAL"})
SOURCE_READS = frozenset({"catalog", "workspace", "analysis", "series", "entities", "observations",
                          "series_summary", "tracked_institutions", "export.csv", "openapi.json", "healthz"})
PROVENANCE_HEADERS = frozenset({"x-research-source-sha", "x-seiche-release-sha",
                               "x-seiche-railway-deployment", "x-seiche-railway-authority",
                               "content-disposition", "etag"})
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def demand_entries():
    result = {}
    for dataset, product in SOURCE_DATASETS.items():
        key = "source." + dataset
        result[key] = {"id": key, "product": product, "label": "Source archive: " + dataset,
                       "route": "/v1/source-data/series?dataset=" + dataset, "tier": "research_wrapper"}
    for mnemonic in sorted(ECONOMY_SERIES):
        key = "seiche.series." + mnemonic
        result[key] = {"id": key, "product": "seiche", "label": "Public series " + mnemonic,
                       "route": "/v1/economy/series/" + mnemonic, "tier": "primary"}
    for key, label, route in (
        ("liquilens.banking_coverage", "Reviewed banking coverage", "/v1/banking/coverage"),
        ("liquilens.banking_filing", "Reviewed institution filing and history", None),
        ("liquilens.institution_monitoring", "Complete institution monitoring register and reviews", "/v1/banking/monitoring/watchlist"),
        ("seiche.series_catalog", "Public series metadata", "/v1/economy/series/index.json"),
    ):
        result[key] = {"id": key, "product": key.split(".")[0], "label": label, "route": route, "tier": "primary"}
    return result


def _query(query, allowed):
    pairs = list(query.multi_items())
    if (len(pairs) > 15 or len({key for key, _ in pairs}) != len(pairs)
            or {key for key, _ in pairs} - set(allowed)
            or any(len(key) > 40 or len(value) > 10100 or any(ord(c) < 32 for c in value) for key, value in pairs)):
        raise ValueError()
    return dict(pairs)


def _integer(params, field, minimum, maximum):
    if field in params and (not re.fullmatch(r"[0-9]{1,8}", params[field]) or not minimum <= int(params[field]) <= maximum):
        raise ValueError()


def _date(params, field, *, not_future=False):
    value = params.get(field)
    if value:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        parsed = date.fromisoformat(value)
        if not_future and parsed > datetime.now(timezone.utc).date():
            raise ValueError()


def _slug(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 100 or not SLUG.fullmatch(value):
        raise ValueError()


def source_request(operation, query):
    if operation not in SOURCE_READS | {""}:
        raise NetworkError("not_found", "Unknown reviewed source-data route", 404)
    try:
        selectors = {"dataset", "q", "limit", "offset"}
        observations = {"dataset", "series", "entity", "start", "end", "as_known_at", "limit", "offset"}
        allowed = {
            "": {"product"}, "catalog": {"product"}, "healthz": {"product"},
            "workspace": {"product"}, "analysis": {"product"}, "openapi.json": set(),
            "series": selectors, "entities": selectors, "observations": observations, "export.csv": observations,
            "series_summary": {"dataset", "series", "entity", "window"},
            "tracked_institutions": {"scope", "q", "limit", "offset"},
        }[operation]
        params = _query(query, allowed)
        if "product" in params and params["product"] not in {"seiche", "liquilens", "undertow"}:
            raise ValueError()
        if "dataset" in allowed and params.get("dataset") not in SOURCE_DATASETS:
            raise ValueError()
        for key in ("series", "entity", "q"):
            if len(params.get(key, "")) > 150:
                raise ValueError()
        if "series" in allowed and not params.get("series"):
            raise ValueError()
        if "scope" in params and params["scope"] not in {"dossiers", "rbi_registry"}:
            raise ValueError()
        _integer(params, "limit", 1, 500 if operation == "tracked_institutions" else 10000)
        _integer(params, "offset", 0, 10000000)
        _integer(params, "window", 1, 260)
        _date(params, "start")
        _date(params, "end")
        if params.get("start") and params.get("end") and params["start"] > params["end"]:
            raise ValueError()
        if params.get("as_known_at"):
            stamp = params["as_known_at"]
            if len(stamp) > 40 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", stamp):
                raise ValueError()
            if datetime.fromisoformat(stamp).tzinfo is None:
                raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise NetworkError("invalid_query", "Unsupported source-data parameters", 400) from None
    if "dataset" in params:
        dataset = params["dataset"]
        classification = data_catalog.Classification(SOURCE_DATASETS[dataset], "source." + dataset, "exact", "rest_json")
    else:
        discovery = operation in {"", "catalog", "healthz", "openapi.json"}
        classification = data_catalog.Classification("research-data", "research-data." + ("discovery" if discovery else "research"),
                                                    "discovery" if discovery else "family", "rest_json")
    # The packaged UI has no remote HTML dependency. Source schema availability
    # still gates its response, and only its local controls use the product query.
    target = operation or "openapi.json"
    return SOURCE_ORIGIN + "/" + target, params if operation else {}, classification


def banking_request(operation, query):
    if operation not in {"coverage", "monitoring/watchlist"} and not re.fullmatch(r"(?:monitoring/)?institutions/[^/]+", operation):
        raise NetworkError("not_found", "Unknown reviewed banking route", 404)
    try:
        if operation == "coverage":
            params = _query(query, {"sector", "as_of"})
            if "sector" in params and params["sector"] not in {"all", "bank", "sfb", "ucb"}:
                raise ValueError()
            dataset, basis = "liquilens.banking_coverage", "exact"
        elif operation == "monitoring/watchlist":
            params = _query(query, {"slugs", "as_of"})
            if "slugs" in params:
                slugs = params["slugs"].split(",")
                if not 1 <= len(slugs) <= 100 or len(set(slugs)) != len(slugs):
                    raise ValueError()
                for slug in slugs:
                    _slug(slug)
            dataset, basis = "liquilens.institution_monitoring", "family"
        else:
            _slug(operation.rsplit("/", 1)[1])
            params = _query(query, {"as_of", "include_history"})
            if "include_history" in params and params["include_history"] not in {"true", "false"}:
                raise ValueError()
            dataset, basis = ("liquilens.institution_monitoring" if operation.startswith("monitoring/") else "liquilens.banking_filing"), "family"
        if "as_of" in params and not params["as_of"]:
            raise ValueError()
        _date(params, "as_of", not_future=True)
    except (ValueError, TypeError, OverflowError):
        raise NetworkError("invalid_query", "Unsupported banking parameters", 400) from None
    return BANKING_ORIGIN + "/" + operation, params, data_catalog.Classification("liquilens", dataset, basis, "rest_json")


def economy_request(operation, query):
    if operation == "index.json":
        dataset, basis = "seiche.series_catalog", "discovery"
    else:
        mnemonic = operation.removesuffix(".csv")
        if mnemonic not in ECONOMY_SERIES:
            raise NetworkError("not_found", "Unknown reviewed economic series", 404)
        dataset, basis = "seiche.series." + mnemonic, "exact"
    try:
        params = _query(query, set() if operation == "index.json" or operation.endswith(".csv") else {"n"})
        _integer(params, "n", 1, 2000)
    except ValueError:
        raise NetworkError("invalid_query", "Unsupported economic series parameters", 400) from None
    return ECONOMY_ORIGIN + "/" + operation, params, data_catalog.Classification("seiche", dataset, basis, "rest_json")


def public_surface(path, method):
    if method != "GET":
        return False
    if path in {"/v1/source-data", "/v1/source-data/"}:
        return True
    if path.startswith("/v1/source-data/"):
        return path.removeprefix("/v1/source-data/") in SOURCE_READS
    if path.startswith("/v1/banking/"):
        operation = path.removeprefix("/v1/banking/")
        return operation in {"coverage", "monitoring/watchlist"} or bool(re.fullmatch(r"(?:monitoring/)?institutions/[a-z0-9]+(?:-[a-z0-9]+)*", operation)) and len(operation.rsplit("/", 1)[1]) <= 100
    if path.startswith("/v1/economy/series/"):
        operation = path.removeprefix("/v1/economy/series/")
        return operation == "index.json" or operation.removesuffix(".csv") in ECONOMY_SERIES
    return path == "/v1/corpus/v1/catalog"


def transform_source(operation, raw, media, status, base):
    if not 200 <= status < 300 or operation not in {"", "openapi.json"}:
        return raw, media
    from .hosted_surfaces import strict_json
    schema = strict_json(raw)
    if (not isinstance(schema, dict) or schema.get("info", {}).get("version") != "1.2.0"
            or set(schema.get("paths", {})) != {"/" + x for x in SOURCE_READS - {"healthz", "openapi.json"}}
            or any(not isinstance(path, dict) or set(path) != {"get"} for path in schema["paths"].values())):
        raise ValueError("Source-data schema no longer matches the reviewed contract")
    if operation == "":
        html = Path(__file__).with_name("static").joinpath("source-data.html").read_text()
        # Public_url is operator validated; replacement is only the owned root.
        return html.replace("__PUBLIC_ROOT__", escape(base.rstrip("/"), quote=True)).encode(), "text/html"
    schema = deepcopy(schema)
    schema["servers"] = [{"url": base.rstrip("/") + "/v1/source-data"}]
    schema["externalDocs"] = {"url": base.rstrip("/") + "/v1/source-data/"}
    schema["x-mcp"] = {"url": base.rstrip("/") + "/products/research-data/mcp"}
    for path in schema["paths"].values():
        path["get"].pop("servers", None)
        for item in path["get"].get("parameters", []):
            name, definition = item["name"], item["schema"]
            if name == "dataset":
                definition.update(enum=sorted(SOURCE_DATASETS), maxLength=100)
            elif name in {"series", "entity", "q"}:
                definition["maxLength"] = 150
            elif name in {"start", "end"}:
                definition["format"] = "date"
            elif name == "as_known_at":
                definition.update(format="date-time", maxLength=40)
    return json.dumps(schema, separators=(",", ":"), allow_nan=False).encode(), "application/json"


def transform_economy(operation, raw, media, status, base):
    if operation != "index.json" or not 200 <= status < 300:
        return raw, media
    from .hosted_surfaces import strict_json
    result = strict_json(raw)
    if not isinstance(result, dict) or result.get("schema") != "seiche.series-index.v1" or not isinstance(result.get("series"), list):
        raise ValueError("Invalid economic series catalog")
    for row in result["series"]:
        if not isinstance(row, dict) or not isinstance(row.get("mnemonic"), str):
            raise ValueError("Invalid economic series entry")
        mnemonic = row["mnemonic"]
        admitted = mnemonic in ECONOMY_SERIES and not row.get("csv_restricted")
        for field, suffix in (("json", ""), ("csv", ".csv")):
            original = row.get(field)
            expected = "/api/series/" + mnemonic + suffix
            if original is not None and original not in {expected, "https://api.seiche.info" + expected}:
                raise ValueError("Unexpected series transport URL")
            row[field] = base.rstrip("/") + "/v1/economy/series/" + mnemonic + suffix if admitted and original else None
        row["central_transport_available"] = admitted and bool(row.get("json"))
    result["central_transport_scope"] = "Five reviewed browser series. Other catalog members retain metadata only on this central route; source availability and rights fields are unchanged."
    return json.dumps(result, separators=(",", ":"), allow_nan=False).encode(), "application/json"


def catalog():
    return {"version": VERSION, "source_data_base": "/v1/source-data", "banking_base": "/v1/banking",
            "source_datasets": sorted(SOURCE_DATASETS), "economy_series": sorted(ECONOMY_SERIES),
            "economy_base": "/v1/economy/series", "corpus_catalog": "/v1/corpus/v1/catalog",
            "raw_selectors_retained": False, "raw_responses_retained": False,
            "compatibility": "Publisher identity, source receipts, full corpus manifests and bulk download links may reference native origins. Bulk shards and wider native APIs are outside central coverage."}
