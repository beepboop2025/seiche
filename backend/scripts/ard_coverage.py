#!/usr/bin/env python3
"""Measure agent discovery coverage for the Liquidity Lab product line.

The hard gate checks that every product publishes a valid ARD catalog, has a
current official MCP Registry record, and exposes its expected anonymous tool
inventory. Semantic-registry ranking is reported separately: indexing is an
eventual distribution outcome, not evidence that a catalog is malformed.

Examples:

    python backend/scripts/ard_coverage.py
    python backend/scripts/ard_coverage.py --json-out ard-coverage.json
    python backend/scripts/ard_coverage.py --strict-indexing
    python backend/scripts/ard_coverage.py --local-only \
      --catalog seiche=frontend/public/.well-known/ai-catalog.json
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


MCP_REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers"
ARD_REGISTRIES = {
    "github": "https://agentfinder.github.com/api/v1/search",
    "ora": "https://ora.ai/api/ard/search",
    "huggingFace": "https://huggingface-hf-discover.hf.space/search",
}
ARD_REGISTRY_LABELS = {
    "github": "GitHub",
    "ora": "Ora",
    "huggingFace": "HF",
}
USER_AGENT = "LiquidityLab-ARD-Coverage/1.0 (+https://seiche.info/developers)"
URN = re.compile(r"^urn:air:[a-zA-Z0-9.-]+(:[a-zA-Z0-9._-]+)+$")
SCALAR = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class Product:
    slug: str
    catalog_url: str
    mcp_identifier: str
    mcp_name: str
    mcp_version: str
    mcp_endpoint: str
    openapi_identifier: str
    openapi_url: str
    first_tool: str
    public_tool_count: int
    intent_query: str


PRODUCTS = (
    Product(
        slug="liquilens",
        catalog_url="https://liquilens.in/.well-known/ai-catalog.json",
        mcp_identifier="urn:air:liquilens.in:mcp:failure-radar",
        mcp_name="io.github.beepboop2025/liquilens",
        mcp_version="1.6.0",
        mcp_endpoint="https://api.liquilens.in/mcp",
        openapi_identifier="urn:air:liquilens.in:openapi:failure-radar",
        openapi_url="https://api.liquilens.in/api/openapi.json",
        first_tool="latest_article",
        public_tool_count=18,
        intent_query="Which Indian banks or NBFCs are showing failure risk?",
    ),
    Product(
        slug="seiche",
        catalog_url="https://seiche.info/.well-known/ai-catalog.json",
        mcp_identifier="urn:air:seiche.info:mcp:funding-stress",
        mcp_name="io.github.beepboop2025/seiche",
        mcp_version="0.10.0",
        mcp_endpoint="https://api.seiche.info/mcp",
        openapi_identifier="urn:air:seiche.info:openapi:funding-stress",
        openapi_url="https://api.seiche.info/api/openapi.json",
        first_tool="latest_article",
        public_tool_count=9,
        intent_query="What is the current US dollar funding stress regime?",
    ),
    Product(
        slug="undertow",
        catalog_url=(
            "https://liquilens-undertow.com/.well-known/ai-catalog.json"),
        mcp_identifier=(
            "urn:air:liquilens-undertow.com:mcp:market-liquidity"),
        mcp_name="io.github.beepboop2025/undertow",
        mcp_version="1.8.0",
        mcp_endpoint="https://api.seiche.info/undertow/mcp",
        openapi_identifier=(
            "urn:air:liquilens-undertow.com:openapi:x402-market-liquidity"),
        openapi_url="https://api.seiche.info/undertow/x402/openapi.json",
        first_tool="latest_article",
        public_tool_count=9,
        intent_query="What would it cost to sell $100,000 of BTC across venues?",
    ),
)


class ProbeError(RuntimeError):
    """A remote or local discovery surface could not be read."""


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _decode_json_or_sse(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        if not line.startswith("data:"):
            continue
        try:
            value = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ProbeError("response was neither a JSON object nor JSON SSE data")


def _request_json(url: str, *, timeout: float, payload: Any = None
                  ) -> tuple[dict[str, Any], dict[str, str]]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "User-Agent": USER_AGENT,
    }
    method = "GET"
    body = None
    if payload is not None:
        method = "POST"
        body = _json_bytes(payload)
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise ProbeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProbeError(f"could not read {url}: {exc}") from exc
    return _decode_json_or_sse(raw), response_headers


def _catalog_payload(source: str, timeout: float
                     ) -> tuple[dict[str, Any], dict[str, str]]:
    if urlparse(source).scheme in {"http", "https"}:
        return _request_json(source, timeout=timeout)
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"could not read local catalog {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProbeError(f"local catalog {source} is not a JSON object")
    return payload, {}


def validate_catalog(catalog: dict[str, Any], product: Product) -> list[str]:
    """Apply the stable ARD 1.0 envelope and product-specific contracts."""
    errors: list[str] = []
    if catalog.get("specVersion") != "1.0":
        errors.append("specVersion must be 1.0")
    host = catalog.get("host")
    if host is not None and (
            not isinstance(host, dict) or not host.get("displayName")):
        errors.append("host must contain displayName when present")
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not entries:
        return errors + ["entries must be a non-empty array"]

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        for field in ("identifier", "displayName", "type"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                errors.append(f"{where}.{field} must be a non-empty string")
        identifier = entry.get("identifier")
        if isinstance(identifier, str):
            if not URN.fullmatch(identifier):
                errors.append(f"{where}.identifier is not a valid urn:air URN")
            if identifier in seen:
                errors.append(f"{where}.identifier is duplicated")
            seen.add(identifier)
        has_url = "url" in entry
        has_data = "data" in entry
        if has_url == has_data:
            errors.append(f"{where} must contain exactly one of url or data")
        if has_url:
            parsed = urlparse(entry.get("url", ""))
            if parsed.scheme != "https" or not parsed.netloc:
                errors.append(f"{where}.url must be an absolute HTTPS URL")
        if has_data and not isinstance(entry.get("data"), dict):
            errors.append(f"{where}.data must be an object")
        queries = entry.get("representativeQueries")
        if queries is not None and (
                not isinstance(queries, list) or not 2 <= len(queries) <= 5
                or any(not isinstance(query, str) or not query.strip()
                       for query in queries)):
            errors.append(
                f"{where}.representativeQueries must contain 2-5 strings")
        metadata = entry.get("metadata", {})
        if not isinstance(metadata, dict) or any(
                not isinstance(value, SCALAR) for value in metadata.values()):
            errors.append(f"{where}.metadata values must be scalar")

    matches = [entry for entry in entries if isinstance(entry, dict)
               and entry.get("identifier") == product.mcp_identifier]
    if len(matches) != 1:
        errors.append(
            f"expected one MCP entry {product.mcp_identifier}, found {len(matches)}")
        return errors
    mcp = matches[0]
    if mcp.get("type") != "application/mcp-server-card+json":
        errors.append("product MCP entry has the wrong media type")
    if mcp.get("version") != product.mcp_version:
        errors.append(
            f"catalog MCP version is {mcp.get('version')!r}, "
            f"expected {product.mcp_version}")
    card = mcp.get("data")
    if not isinstance(card, dict):
        errors.append("product MCP entry must embed its server card")
        return errors
    if card.get("name") != product.mcp_name:
        errors.append(f"embedded MCP name is not {product.mcp_name}")
    if card.get("version") != product.mcp_version:
        errors.append("embedded MCP version does not match the catalog version")
    remotes = card.get("remotes", [])
    if product.mcp_endpoint not in [
            remote.get("url") for remote in remotes if isinstance(remote, dict)]:
        errors.append("embedded MCP card does not advertise the live endpoint")
    capabilities = mcp.get("capabilities")
    if not isinstance(capabilities, list) or len(capabilities) != (
            product.public_tool_count):
        errors.append(
            f"catalog exposes {len(capabilities) if isinstance(capabilities, list) else 0} "
            f"capabilities, expected {product.public_tool_count}")

    openapi_matches = [entry for entry in entries if isinstance(entry, dict)
                       and entry.get("identifier") == product.openapi_identifier]
    if len(openapi_matches) != 1:
        errors.append(
            f"expected one OpenAPI entry {product.openapi_identifier}, "
            f"found {len(openapi_matches)}")
    else:
        openapi = openapi_matches[0]
        if openapi.get("type") != "application/vnd.oai.openapi+json":
            errors.append("product OpenAPI entry has the wrong media type")
        if openapi.get("url") != product.openapi_url:
            errors.append("product OpenAPI entry does not reference the live contract")
    return errors


def probe_catalog(product: Product, source: str, timeout: float) -> dict[str, Any]:
    try:
        payload, headers = _catalog_payload(source, timeout)
        errors = validate_catalog(payload, product)
        remote = urlparse(source).scheme in {"http", "https"}
        cors = headers.get("access-control-allow-origin") if remote else None
        if remote and cors not in {"*", product.catalog_url}:
            errors.append("catalog is not cross-origin readable")
        return {
            "ok": not errors,
            "source": source,
            "entryCount": len(payload.get("entries", [])),
            "cors": cors,
            "errors": errors,
        }
    except ProbeError as exc:
        return {"ok": False, "source": source, "errors": [str(exc)]}


def probe_registry(product: Product, timeout: float) -> dict[str, Any]:
    url = f"{MCP_REGISTRY}?search={quote(product.mcp_name, safe='')}"
    try:
        payload, _ = _request_json(url, timeout=timeout)
        rows = payload.get("servers", [])
        exact = [row for row in rows if isinstance(row, dict)
                 and row.get("server", {}).get("name") == product.mcp_name]
        latest = [row for row in exact if row.get("_meta", {}).get(
            "io.modelcontextprotocol.registry/official", {}).get("isLatest")]
        row = latest[0] if latest else (exact[-1] if exact else None)
        version = row.get("server", {}).get("version") if row else None
        errors = []
        if row is None:
            errors.append("no exact official MCP Registry record")
        elif version != product.mcp_version:
            errors.append(
                f"official MCP Registry version is {version}, "
                f"expected {product.mcp_version}")
        return {
            "ok": not errors,
            "version": version,
            "versionsFound": [row.get("server", {}).get("version")
                              for row in exact],
            "errors": errors,
        }
    except ProbeError as exc:
        return {"ok": False, "errors": [str(exc)]}


def probe_mcp(product: Product, timeout: float) -> dict[str, Any]:
    request = {
        "jsonrpc": "2.0",
        "id": "ard-coverage",
        "method": "tools/list",
        "params": {},
    }
    try:
        payload, _ = _request_json(
            product.mcp_endpoint, timeout=timeout, payload=request)
        if payload.get("error"):
            raise ProbeError(f"MCP error: {payload['error']}")
        tools = payload.get("result", {}).get("tools", [])
        names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
        errors = []
        if len(names) != product.public_tool_count:
            errors.append(
                f"live tools/list returned {len(names)} tools, "
                f"expected {product.public_tool_count}")
        if product.first_tool not in names:
            errors.append(f"first activation tool {product.first_tool} is missing")
        return {
            "ok": not errors,
            "toolCount": len(names),
            "firstTool": product.first_tool,
            "firstToolListed": product.first_tool in names,
            "errors": errors,
        }
    except ProbeError as exc:
        return {"ok": False, "errors": [str(exc)]}


def probe_openapi(product: Product, timeout: float) -> dict[str, Any]:
    try:
        payload, _ = _request_json(product.openapi_url, timeout=timeout)
        version = payload.get("openapi")
        paths = payload.get("paths")
        errors = []
        if not isinstance(version, str) or not version.startswith("3."):
            errors.append(f"OpenAPI version is {version!r}, expected 3.x")
        if not isinstance(paths, dict) or not paths:
            errors.append("OpenAPI contract has no paths")
        return {
            "ok": not errors,
            "version": version,
            "pathCount": len(paths) if isinstance(paths, dict) else 0,
            "errors": errors,
        }
    except ProbeError as exc:
        return {"ok": False, "errors": [str(exc)]}


def _matching_ard_result(
        results: list[Any], product: Product) -> tuple[int | None, str | None]:
    """Return the first rank and canonical product signal in ARD results."""
    signals = (
        ("identifier", product.mcp_identifier),
        ("mcpName", product.mcp_name),
        ("endpoint", product.mcp_endpoint),
        ("catalogHost", urlparse(product.catalog_url).hostname or ""),
    )
    for index, row in enumerate(results, start=1):
        if not isinstance(row, dict):
            continue
        serialized = json.dumps(row, sort_keys=True).casefold()
        for label, signal in signals:
            if signal and signal.casefold() in serialized:
                return index, label
    return None, None


def probe_ard_search(
        product: Product, registry: str, url: str,
        timeout: float) -> dict[str, Any]:
    request = {
        "query": {
            "text": product.intent_query,
            "filter": {"type": ["application/mcp-server-card+json"]},
        },
        "pageSize": 10,
    }
    try:
        payload, _ = _request_json(url, timeout=timeout, payload=request)
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise ProbeError(f"{registry} response has no results array")
        rank, matched_by = _matching_ard_result(results, product)
        return {
            "ok": True,
            "indexed": rank is not None,
            "rank": rank,
            "matchedBy": matched_by,
            "resultCount": len(results),
            "query": product.intent_query,
            "registry": registry,
            "url": url,
            "errors": [],
        }
    except ProbeError as exc:
        return {
            "ok": False,
            "indexed": False,
            "rank": None,
            "query": product.intent_query,
            "registry": registry,
            "url": url,
            "errors": [str(exc)],
        }


def _run_product(product: Product, source: str, timeout: float,
                 local_only: bool) -> tuple[str, dict[str, Any]]:
    report: dict[str, Any] = {
        "expected": asdict(product),
        "catalog": probe_catalog(product, source, timeout),
    }
    if not local_only:
        report["mcpRegistry"] = probe_registry(product, timeout)
        report["mcpInventory"] = probe_mcp(product, timeout)
        report["openapi"] = probe_openapi(product, timeout)
        with ThreadPoolExecutor(max_workers=len(ARD_REGISTRIES)) as pool:
            searches = {
                name: pool.submit(
                    probe_ard_search, product, name, url, timeout)
                for name, url in ARD_REGISTRIES.items()
            }
            report["ardSearch"] = {
                name: future.result() for name, future in searches.items()
            }
    return product.slug, report


def run(*, catalog_sources: dict[str, str], timeout: float,
        local_only: bool, strict_indexing: bool) -> dict[str, Any]:
    products: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(PRODUCTS)) as pool:
        futures = [pool.submit(
            _run_product, product,
            catalog_sources.get(product.slug, product.catalog_url),
            timeout, local_only) for product in PRODUCTS]
        for future in futures:
            slug, report = future.result()
            products[slug] = report

    hard_failures: list[str] = []
    indexed = 0
    for product in PRODUCTS:
        row = products[product.slug]
        for key in ("catalog", "mcpRegistry", "mcpInventory", "openapi"):
            if key in row and not row[key]["ok"]:
                hard_failures.append(f"{product.slug}.{key}")
        searches = row.get("ardSearch", {})
        product_indexed = any(
            search.get("indexed") for search in searches.values())
        if product_indexed:
            indexed += 1
        elif strict_indexing and searches:
            hard_failures.append(f"{product.slug}.ardSearch")

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "localOnly": local_only,
        "strictIndexing": strict_indexing,
        "summary": {
            "hardChecksPassed": not hard_failures,
            "hardFailures": hard_failures,
            "catalogsHealthy": sum(
                bool(products[p.slug]["catalog"]["ok"]) for p in PRODUCTS),
            "productsIndexed": indexed if not local_only else None,
            "registryCoverage": ({
                name: sum(bool(products[p.slug]["ardSearch"][name]["indexed"])
                          for p in PRODUCTS)
                for name in ARD_REGISTRIES
            } if not local_only else None),
            "productCount": len(PRODUCTS),
        },
        "products": {p.slug: products[p.slug] for p in PRODUCTS},
    }


def _status(value: bool) -> str:
    return "PASS" if value else "FAIL"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agent discovery coverage",
        "",
        f"Generated: `{report['generatedAt']}`",
        "",
    ]
    if report["localOnly"]:
        lines += ["| Product | ARD catalog | Entries |", "|---|---:|---:|"]
        for product in PRODUCTS:
            row = report["products"][product.slug]["catalog"]
            lines.append(
                f"| {product.slug} | {_status(row['ok'])} | "
                f"{row.get('entryCount', '—')} |")
    else:
        registry_headers = " | ".join(ARD_REGISTRY_LABELS.values())
        registry_rules = "|".join("---:" for _ in ARD_REGISTRIES)
        lines += [
            "| Product | ARD catalog | MCP Registry | Live tools | OpenAPI | "
            f"{registry_headers} |",
            f"|---|---:|---:|---:|---:|{registry_rules}|",
        ]
        for product in PRODUCTS:
            row = report["products"][product.slug]
            search_states = {}
            for name, search in row["ardSearch"].items():
                if not search["ok"]:
                    search_states[name] = "probe failed"
                elif search.get("indexed"):
                    search_states[name] = f"rank {search['rank']}"
                else:
                    search_states[name] = "not indexed"
            lines.append(
                f"| {product.slug} | {_status(row['catalog']['ok'])} | "
                f"{_status(row['mcpRegistry']['ok'])} "
                f"({row['mcpRegistry'].get('version', '—')}) | "
                f"{_status(row['mcpInventory']['ok'])} "
                f"({row['mcpInventory'].get('toolCount', '—')}) | "
                f"{_status(row['openapi']['ok'])} "
                f"({row['openapi'].get('pathCount', '—')} paths) | "
                + " | ".join(search_states[name] for name in ARD_REGISTRIES)
                + " |")

    details = []
    for product in PRODUCTS:
        row = report["products"][product.slug]
        for surface, result in row.items():
            if not isinstance(result, dict):
                continue
            for error in result.get("errors", []):
                details.append(f"- `{product.slug}.{surface}`: {error}")
            if surface == "ardSearch":
                for registry, search in result.items():
                    for error in search.get("errors", []):
                        details.append(
                            f"- `{product.slug}.ardSearch.{registry}`: {error}")
    if details:
        lines += ["", "## Findings", "", *details]
    if not report["localOnly"] and not report["strictIndexing"]:
        lines += [
            "",
            "ARD search misses are coverage gaps, not hard failures. Use "
            "`--strict-indexing` once the registries have had time to ingest "
            "the catalogs.",
        ]
    return "\n".join(lines) + "\n"


def _catalog_overrides(values: list[str]) -> dict[str, str]:
    valid = {product.slug for product in PRODUCTS}
    out = {}
    for value in values:
        slug, separator, source = value.partition("=")
        if not separator or slug not in valid or not source:
            raise argparse.ArgumentTypeError(
                "--catalog must be PRODUCT=PATH_OR_URL for "
                + ", ".join(sorted(valid)))
        out[slug] = source
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", action="append", default=[], metavar="PRODUCT=SOURCE",
        help="override one product's default live catalog URL")
    parser.add_argument(
        "--local-only", action="store_true",
        help="validate catalogs without registry or MCP network probes")
    parser.add_argument(
        "--strict-indexing", action="store_true",
        help="treat a missing semantic-registry result as a hard failure")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    try:
        sources = _catalog_overrides(args.catalog)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    report = run(
        catalog_sources=sources,
        timeout=args.timeout,
        local_only=args.local_only,
        strict_indexing=args.strict_indexing,
    )
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    sys.stdout.write(render_markdown(report))
    return 0 if report["summary"]["hardChecksPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
