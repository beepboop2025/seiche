"""The shared api.seiche.info origin publishes honest RFC 9727 discovery."""

from fastapi.testclient import TestClient

from seiche.api import API_CATALOG, API_CATALOG_MEDIA_TYPE, API_CATALOG_URL, app


EXPECTED_ANCHORS = {
    "https://api.seiche.info/api",
    "https://api.seiche.info/mcp",
    "https://api.seiche.info/undertow/x402/",
    "https://api.seiche.info/undertow/mcp",
    "https://api.seiche.info/riptide/",
    "https://api.seiche.info/riptide/mcp",
    "https://api.seiche.info/palimpsest/mcp",
}


def test_origin_catalog_keeps_sibling_apis_separate_and_bounded():
    linkset = API_CATALOG["linkset"]
    assert {entry["anchor"] for entry in linkset} == EXPECTED_ANCHORS
    assert len(linkset) == len(EXPECTED_ANCHORS)
    for entry in linkset:
        assert set(entry) <= {
            "anchor",
            "service-desc",
            "service-doc",
            "service-meta",
            "status",
        }
        assert entry.get("service-doc") or entry.get("service-meta")
        for relation, links in entry.items():
            if relation == "anchor":
                continue
            assert links
            assert all(link["href"].startswith("https://") for link in links)
            assert all(link["type"] for link in links)


def test_origin_catalog_has_profiled_cors_read_contract():
    with TestClient(app) as client:
        response = client.get("/.well-known/api-catalog")
        head = client.head("/.well-known/api-catalog")
        options = client.options("/.well-known/api-catalog")
        mutation = client.post("/.well-known/api-catalog")

    assert response.status_code == 200
    assert response.json() == API_CATALOG
    assert response.headers["content-type"] == API_CATALOG_MEDIA_TYPE
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["access-control-expose-headers"] == "Link"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["link"] == (
        f'<{API_CATALOG_URL}>; rel="api-catalog"; type="application/linkset+json"'
    )

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"] == API_CATALOG_MEDIA_TYPE
    assert options.status_code == 204
    assert options.content == b""
    assert options.headers["allow"] == "GET, HEAD, OPTIONS"
    assert mutation.status_code == 405
    assert set(mutation.headers["allow"].split(", ")) == {
        "GET",
        "HEAD",
        "OPTIONS",
    }


def test_origin_catalog_does_not_invent_agent_protocols_or_flatten_products():
    serialized = str(API_CATALOG).lower()
    assert "/.well-known/mcp.json" not in serialized
    assert "agent-card" not in serialized
    assert "a2a" not in serialized
    assert "composite" not in serialized


def test_origin_catalog_media_hints_match_live_representations():
    entries = {entry["anchor"]: entry for entry in API_CATALOG["linkset"]}

    assert entries["https://api.seiche.info/api"]["service-desc"] == [
        {
            "href": "https://api.seiche.info/api/openapi.json",
            "type": "application/json",
        }
    ]
    assert (
        entries["https://api.seiche.info/undertow/x402/"]["service-desc"][0]["type"]
        == "application/json"
    )
    assert (
        entries["https://api.seiche.info/riptide/"]["service-desc"][0]["type"]
        == "application/json"
    )
    assert (
        entries["https://api.seiche.info/palimpsest/mcp"]["service-meta"][1]["type"]
        == "application/json"
    )
