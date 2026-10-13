"""Reviewed OpenBB and NoiseFloor surfaces, never an arbitrary reverse proxy.

NoiseFloor's owned 0.4.0 schema is retained verbatim from source
18e7290a022c7cba0d1e5e417a4a467cccfaa7a0. Its submitted panels and headlines
are transient computation inputs, not new collected datasets.
"""
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from html import escape
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from starlette.datastructures import QueryParams

from . import data_catalog
from .models import NetworkError

VERSION = "central-surfaces-2026-10-13"
RESEARCH_ORIGIN = "https://api.seiche.info/openbb"
NOISEFLOOR_ORIGIN = "https://api.seiche.info/noisefloor"
NOISEFLOOR_MAX_BODY = 2 * 1024 * 1024
NOISEFLOOR_POST = {
    "v1/market/assess": "market_assessment",
    "v1/narrative/triage": "narrative_triage",
    "v1/spectral/assess": "spectral_assessment",
    "v1/research/dyson": "dyson_reference",
}
NOISEFLOOR_GET = frozenset({"healthz", "v1/capabilities", "openapi.json"})
RESEARCH_UI = frozenset({"", "widgets.json", "apps.json", "guide", "thumbnail.svg", "docs", "openapi.json", "healthz"})
RESEARCH_API = frozenset(data_catalog.RESEARCH_READS) | {"query", "agent-query", "agent-review", "workflow", "funding-review.csv"}
PUBLIC_MCP_PATHS = frozenset("/products/" + product + "/mcp" for product in data_catalog.tool_registry())
CSV_HEADERS = frozenset({"content-disposition", "x-financial-evidence-capture", "x-data-readiness", "x-release-identity",
                         "x-funding-review-scope", "x-funding-review-as-of", "etag"})
PUBLIC_ORIGINS = frozenset({"https://beepboop2025.github.io", "https://pro.openbb.co", "https://my.openbb.co",
                            "https://liquilens.in", "https://www.liquilens.in"})


def relative_path(scope):
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    return path[len(root):] if root and path.startswith(root + "/") else path


def public_surface(scope):
    """CORS admission is an exact method/path contract, not a broad namespace."""
    if scope.get("type") != "http":
        return False
    path, method = relative_path(scope), scope.get("method")
    headers = dict(scope.get("headers", []))
    if method == "OPTIONS":
        method = headers.get(b"access-control-request-method", b"").decode("latin1").upper()
    if path in {"/v1/research", "/v1/research/"} or path.removeprefix("/v1/research/") in RESEARCH_UI and path.startswith("/v1/research/"):
        return method == "GET"
    if path.startswith("/v1/research/api/v1/"):
        return method == "GET" and path[len("/v1/research/api/v1/"):] in RESEARCH_API
    if path.startswith("/v1/noisefloor/"):
        suffix = path[len("/v1/noisefloor/"):]
        return (method == "GET" and suffix in NOISEFLOOR_GET) or (method == "POST" and suffix in NOISEFLOOR_POST)
    if path in PUBLIC_MCP_PATHS:
        return method in {"GET", "POST", "DELETE"}
    return False


def body_limit(scope, default):
    path = relative_path(scope)
    if scope.get("method") == "POST" and path in {"/v1/noisefloor/" + item for item in NOISEFLOOR_POST}:
        return NOISEFLOOR_MAX_BODY
    return default


def no_query(query):
    if list(query.multi_items()):
        raise NetworkError("invalid_query", "This reviewed surface accepts no query parameters", 400)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def finite(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise ValueError("Nonfinite JSON number")
        elif isinstance(value, str):
            value.encode("utf-8")
        elif isinstance(value, dict):
            for key, item in value.items():
                key.encode("utf-8")
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)

    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
    finite(value)
    return value


@lru_cache(maxsize=1)
def noisefloor_schema():
    return strict_json(Path(__file__).with_name("noisefloor_schema.json").read_bytes())


def _validate(value, schema, depth=0):
    """Only the keywords in our pinned schema; no coercion or remote refs."""
    if depth > 16:
        raise ValueError()
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
               "number": type(value) in {int, float}, "integer": type(value) is int, "null": value is None}
    if kinds and not any(matches.get(kind, False) for kind in kinds):
        raise ValueError()
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError()
    if value is None:
        return
    if type(value) in {int, float}:
        if not math.isfinite(value):
            raise ValueError()
        for key, wrong in (("minimum", lambda n: value < n), ("maximum", lambda n: value > n),
                           ("exclusiveMinimum", lambda n: value <= n), ("exclusiveMaximum", lambda n: value >= n)):
            if key in schema and wrong(schema[key]):
                raise ValueError()
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 65536):
            raise ValueError()
        if schema.get("format") == "utc-date-time":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
                raise ValueError()
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 2048):
            raise ValueError()
        for item in value:
            _validate(item, schema.get("items", {}), depth + 1)
    elif isinstance(value, dict):
        if len(value) > 128 or set(schema.get("required", [])) - value.keys():
            raise ValueError()
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key not in properties:
                raise ValueError()
            _validate(item, properties[key], depth + 1)


def noise_request(operation, query, method, content_type, raw):
    no_query(query)
    if method == "GET" and operation in NOISEFLOOR_GET:
        if raw:
            raise NetworkError("invalid_request", "Discovery reads accept no body", 400)
        classification = data_catalog.Classification("noisefloor", "noisefloor.discovery", "discovery", "rest_json")
        return NOISEFLOOR_ORIGIN + "/" + operation, classification, "none"
    if operation in NOISEFLOOR_GET | NOISEFLOOR_POST.keys() and method not in ({"POST"} if operation in NOISEFLOOR_POST else {"GET"}):
        raise NetworkError("method_not_allowed", "Method is not supported for this reviewed operation", 405)
    if method != "POST" or operation not in NOISEFLOOR_POST:
        raise NetworkError("not_found", "Unknown reviewed NoiseFloor operation", 404)
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise NetworkError("invalid_media", "NoiseFloor requires application/json", 415)
    if len(raw) > NOISEFLOOR_MAX_BODY:
        raise NetworkError("body_too_large", "NoiseFloor request exceeds 2 MiB", 413)
    try:
        payload = strict_json(raw)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise NetworkError("invalid_json", "A strict finite JSON object is required", 400) from None
    try:
        schema = noisefloor_schema()["paths"]["/" + operation]["post"]["requestBody"]["content"]["application/json"]["schema"]
        _validate(payload, schema)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise NetworkError("invalid_input", "Input does not match the bounded NoiseFloor contract", 422) from None
    tool = NOISEFLOOR_POST[operation]
    family = "scientific_reference" if tool == "dyson_reference" else "statistical_methods"
    classification = data_catalog.Classification("noisefloor", "noisefloor." + family, "family", "rest_json")
    return NOISEFLOOR_ORIGIN + "/" + operation, classification, tool


def research_schema(value, base):
    value = deepcopy(value)
    if not isinstance(value, dict) or not isinstance(value.get("paths"), dict):
        raise ValueError()
    value["servers"] = [{"url": base + "/v1/research"}]
    admitted = {"/api/v1/" + item for item in RESEARCH_API} | {"/", "/healthz", "/guide"}
    value["paths"] = {path: {"get": item["get"]} for path, item in value["paths"].items()
                      if path in admitted and isinstance(item, dict) and "get" in item}
    for path, item in value["paths"].items():
        item["get"].pop("servers", None)
        if path == "/api/v1/workflow":
            for parameter in item["get"].get("parameters", []):
                if parameter.get("name") == "workflow":
                    parameter.update(required=True, schema={"type": "string", "enum": ["funding", "institutions", "exit"]})
                elif parameter.get("name") == "selection":
                    parameter.update(required=True, schema={"type": "string", "minLength": 1, "maxLength": 160, "default": "USD"},
                                     description="Funding currency (USD), institution slugs (au-sfb), or exit sizes (10000,100000), matching the workflow.")
    value["x-central-surface"] = {"version": VERSION, "mcp": base + "/products/financial-evidence/mcp",
                                 "submitted_payloads_retained": False}
    return value


def research_docs(value, base):
    """Working GET forms need neither third-party scripts nor weaker CSP."""
    spec = research_schema(value, base)
    sections = []
    for path, item in spec["paths"].items():
        operation = item["get"]
        fields = []
        for param in operation.get("parameters", []):
            if param.get("in") != "query":
                continue
            name, schema = escape(param["name"], quote=True), param.get("schema", {})
            reference = schema.get("$ref", "")
            if reference.startswith("#/components/schemas/"):
                schema = spec.get("components", {}).get("schemas", {}).get(reference[len("#/components/schemas/"):], schema)
            required = " required" if param.get("required") else ""
            if "enum" in schema:
                field = '<select name="' + name + '">' + ''.join('<option>' + escape(str(v)) + '</option>' for v in schema["enum"]) + '</select>'
            else:
                field = '<input name="' + name + '" value="' + escape(str(schema.get("default", "")), quote=True) + '"' + required + '>'
            fields.append('<label>' + name + ' ' + field + '</label> ')
        sections.append('<section><h2>GET ' + escape(path) + '</h2><p>' + escape(operation.get("summary", "")) +
                        '</p><form method="get" action="' + escape(base + '/v1/research' + path, quote=True) + '">' +
                        ''.join(fields) + '<button type="submit">Read response</button></form></section>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Financial Evidence Research API</title><link rel="stylesheet" href="' + escape(base + '/static/console.css', quote=True) + '">'
            '</head><body><header><a class="brand" href="' + escape(base + '/connect', quote=True) + '">Financial Evidence</a></header>'
            '<main><h1>Financial Evidence Research API</h1>'
            '<p>Free hosted research. Select a dataset and inspect source dates, rights and missing values with the result. '
            'Requests are counted by reviewed data family; query text and response bodies are not retained in demand reports.</p>'
            '<p><a href="' + escape(base + '/v1/research/openapi.json', quote=True) + '">Download the central OpenAPI schema</a></p>' +
            ''.join(sections) + '</main></body></html>').encode()


def transform_research(operation, raw, media, status, base):
    if status >= 400:
        return raw, media
    if operation == "thumbnail.svg":
        # Never promote an arbitrary upstream active SVG to our trusted origin.
        approved = Path(__file__).with_name("static").joinpath("research-thumbnail.svg").read_bytes()
        if raw != approved:
            raise ValueError()
        return raw, media
    if operation in {"guide", "healthz", "widgets.json", "api/v1/funding-review.csv"}:
        if operation == "guide" and media != "text/plain":
            raise ValueError()
        if operation == "widgets.json":
            value = strict_json(raw)
            if not isinstance(value, dict) or any(not isinstance(widget, dict) or widget.get("endpoint") not in {
                    "guide", "api/v1/query", "api/v1/funding-review"} for widget in value.values()):
                raise ValueError()
        return raw, media
    value = strict_json(raw)
    prefix = base + "/v1/research"
    if operation == "":
        if not isinstance(value, dict):
            raise ValueError()
        value.update(docs=prefix + "/docs", workspace_backend=prefix, mcp=base + "/products/financial-evidence/mcp",
                     start=prefix + "/api/v1/start")
    elif operation == "apps.json":
        if not isinstance(value, list):
            raise ValueError()
        for app in value:
            app["img"] = prefix + "/thumbnail.svg"
            for server in app.get("mcp_servers", []):
                server["url"] = base + "/products/financial-evidence/mcp"
    elif operation == "openapi.json":
        value = research_schema(value, base)
    elif operation == "docs":
        return research_docs(value, base), "text/html"
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), "application/json"


def transform_noise(operation, raw, media, status, base):
    if status >= 400:
        return raw, media
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError()
    if operation not in {"openapi.json", "v1/capabilities"}:
        return raw, media
    prefix = base + "/v1/noisefloor"
    if operation == "openapi.json":
        value["servers"] = [{"url": prefix}]
        # The gateway implements the pinned input contract, even if the
        # independently deployed native service later adds optional fields.
        value["paths"] = deepcopy(noisefloor_schema()["paths"])
        value["x-input-schema-source-commit"] = "18e7290a022c7cba0d1e5e417a4a467cccfaa7a0"
    else:
        value["openapi"] = prefix + "/openapi.json"
        value["rest"] = {prefix + "/" + path: tool for path, tool in NOISEFLOOR_POST.items()}
    value["x-central-surface"] = {"version": VERSION, "submitted_payloads_retained": False}
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), "application/json"


def transform_csv(raw, media, status, base):
    if status < 400:
        if media != "text/csv":
            raise ValueError()
        return raw, media
    if media != "application/json" and not media.endswith("+json"):
        raise ValueError()
    value = strict_json(raw)
    if isinstance(value, dict) and value.get("review") == RESEARCH_ORIGIN + "/api/v1/funding-review":
        value["review"] = base + "/v1/research/api/v1/funding-review"
        raw = json.dumps(value, allow_nan=False).encode()
    return raw, media


def transform_start(raw, media, status, base):
    if status >= 400:
        return raw, media
    value = strict_json(raw)
    if not isinstance(value, dict) or value.get("schema") != "financial-evidence.start.v1" or not isinstance(value.get("tasks"), list):
        raise ValueError()
    value["api_base"] = base + "/v1/research"
    value["mcp_url"] = base + "/products/financial-evidence/mcp"
    for task in value["tasks"]:
        parsed = urlsplit(task["api_url"])
        # Only an admitted workflow can become an actionable central task URL.
        # Keep browser/guide URLs and source citations at their actual sites.
        if parsed.path not in {"/openbb/api/v1/workflow", "/v1/research/api/v1/workflow"} or parsed.fragment:
            raise ValueError()
        try:
            data_catalog.research_request("workflow", QueryParams(parsed.query))
        except NetworkError:
            raise ValueError() from None
        task["api_url"] = base + "/v1/research/api/v1/workflow?" + parsed.query
    value["x-central-surface"] = {"version": VERSION, "submitted_payloads_retained": False}
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), "application/json"


def catalog():
    return {"version": VERSION, "research_backend": "/v1/research",
            "research_ui": ["/v1/research" + ("/" + item if item else "") for item in sorted(RESEARCH_UI)],
            "funding_csv": "/v1/research/api/v1/funding-review.csv",
            "noisefloor_routes": [{"method": "GET", "route": "/v1/noisefloor/" + item} for item in sorted(NOISEFLOOR_GET)] +
                                 [{"method": "POST", "route": "/v1/noisefloor/" + item} for item in NOISEFLOOR_POST],
            "noisefloor_max_body_bytes": NOISEFLOOR_MAX_BODY, "submitted_payloads_retained": False,
            "query_parameters": "Only the separately reviewed research API operations accept bounded query parameters",
            "measurement": "Reviewed routes, tool families and outcomes; no numeric panels, source URLs, headlines or free text retained"}
