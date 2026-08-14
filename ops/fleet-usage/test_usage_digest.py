from __future__ import annotations

import importlib.util
import gzip
import json
from pathlib import Path

HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("usage_digest", HERE / "usage_digest.py")
usage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(usage)


def _edge_line(ts, host, uri, method="GET", ua="", status=200, ip="192.0.2.1"):
    return json.dumps({
        "ts": ts,
        "status": status,
        "request": {
            "host": host,
            "uri": uri,
            "method": method,
            "remote_ip": ip,
            "headers": {"User-Agent": [ua]},
        },
    })


def test_bucket_order_keeps_undertow_mcp_out_of_pack_traffic():
    assert usage.bucket_for("api.seiche.info", "/undertow/mcp") == "undertow-mcp"
    assert usage.bucket_for("api.seiche.info", "/undertow/packs/latest") == "undertow-packs"


def test_automation_classifier_is_explicit_and_conservative():
    assert usage.automation_category("fleet-watchdog/1") == "owned-monitor"
    assert usage.automation_category("io.VerifyMCP/probe") == "directory-probe"
    assert usage.automation_category("Mozilla MJ12bot") == "crawler"
    assert usage.automation_category("CarbonMonitor/2") == "external-monitor"
    assert usage.automation_category("x402-list-monitor") == "external-monitor"
    assert usage.automation_category("mcpregistry-bot") == "directory-probe"
    assert usage.automation_category("402explorer/0.1") == "directory-probe"
    assert usage.automation_category("l9scan/1") == "scanner"
    for generic in ("", "node", "undici", "python-httpx/0.27", "Go-http-client/1.1"):
        assert usage.automation_category(generic) is None


def test_edge_parser_separates_known_automation_from_unclassified(tmp_path):
    log = tmp_path / "access.log"
    log.write_text("\n".join([
        _edge_line(101, "breach.seiche.info", "/mcp", "POST",
                   "io.VerifyMCP/probe", ip="192.0.2.1"),
        _edge_line(102, "breach.seiche.info", "/mcp", "POST",
                   "python-httpx/0.27", ip="192.0.2.2"),
        _edge_line(103, "api.seiche.info", "/mcp", status=500,
                   ua="fleet-watchdog/1"),
        "not-json",
    ]), encoding="utf-8")

    stats = usage.parse_log(100, log)

    breach = stats["breach-mcp"]
    assert (breach["req"], breach["post"], breach["automation_req"]) == (2, 2, 1)
    assert breach["automation_post"] == 1
    assert len(breach["ips"]) == 2
    assert stats["seiche-mcp"]["err"] == 1


def test_edge_parser_keeps_redacted_query_request_in_route_counts(tmp_path):
    log = tmp_path / "access.log"
    log.write_text(
        _edge_line(
            101,
            "api.seiche.info",
            "/mcp?api_key=[REDACTED]&source=catalog",
            "POST",
            "catalog-probe/1",
        )
        + "\n",
        encoding="utf-8",
    )

    stats = usage.parse_log(100, log)

    assert stats["seiche-mcp"]["req"] == 1
    assert stats["seiche-mcp"]["post"] == 1


def test_edge_parser_reads_rotation_boundary_once_and_keeps_cutoff(tmp_path):
    current = tmp_path / "access.log"
    rotated = tmp_path / "access-2026-08-09.log.gz"
    boundary = _edge_line(
        100, "api.seiche.info", "/mcp", "POST", "node", ip="192.0.2.9")
    with gzip.open(rotated, "wt", encoding="utf-8") as fh:
        fh.write(_edge_line(99, "api.seiche.info", "/mcp") + "\n")
        fh.write(boundary + "\n")
    # Simulate a copy/rotate overlap: the exact boundary row is also current.
    current.write_text(boundary + "\n" + _edge_line(
        101, "api.seiche.info", "/mcp", "POST", "node", ip="192.0.2.10") + "\n",
        encoding="utf-8")

    stats = usage.parse_log(100, current, [rotated])

    assert stats["seiche-mcp"]["req"] == 2
    assert stats["seiche-mcp"]["post"] == 2
    assert len(stats["seiche-mcp"]["ips"]) == 2


def test_activation_parser_counts_known_calls_and_invalid_probes_separately():
    stats = usage.parse_activation_lines([
        "INFO mcp_activation product=breach surface=public "
        "tool=check_exposure outcome=success origin=edge",
        "mcp_activation product=breach surface=public tool=unknown "
        "outcome=error origin=unknown",
        "mcp_activation product=groundcheck surface=paid "
        "tool=extract_claims outcome=success origin=edge",
        "mcp_activation product=groundcheck surface=public "
        "tool=verify_claim outcome=error origin=direct",
        "undertow mcp: activation surface=subscriber "
        "tool=board_full outcome=success",
        "unrelated request log",
    ])

    assert stats["breach"]["known"] == 1
    assert stats["breach"]["invalid"] == 1
    assert stats["breach"]["tools"] == {"check_exposure": 1}
    assert stats["groundcheck"]["known"] == 2
    assert stats["groundcheck"]["surfaces"] == {"paid": 1, "public": 1}
    assert stats["groundcheck"]["origins"] == {"edge": 1, "direct": 1}
    assert stats["undertow"]["known"] == 1
    assert stats["undertow"]["origins"] == {"unknown": 1}


def test_digest_never_labels_transport_posts_as_activations():
    now = 1_800_000_000.0
    edge = {"breach-mcp": usage._edge_stat()}
    edge["breach-mcp"].update({"req": 949, "post": 949,
                                "automation_req": 940, "automation_post": 940})
    activations = usage.parse_activation_lines([
        "mcp_activation product=breach surface=public tool=unknown "
        "outcome=error origin=unknown"
    ])
    armed = {product: now - usage.WINDOW_S for product in usage.ACTIVATION_PRODUCTS}

    digest = usage.render_digest(
        now=now, window_s=usage.WINDOW_S, edge=edge,
        activations=activations, armed=armed, funnel=None)

    assert "MCP transport POSTs: 949 (not activations)" in digest
    assert "breach: 0 known calls (0 ok, 0 error); invalid probes 1" in digest
    assert "known-auto 940 req/940 POST" in digest
    assert "unclassified 9" in digest


def test_unarmed_product_reports_na_instead_of_false_zero():
    line = usage._activation_summary(
        "seiche", {}, {}, 100.0, 200.0, usage.WINDOW_S)
    assert line == "seiche: n/a — telemetry not armed"
