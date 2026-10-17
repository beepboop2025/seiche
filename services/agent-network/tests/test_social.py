"""Public community publication, isolation, visibility and erase boundaries."""
import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, update

from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.models import NetworkError
from seiche_agent_network.social import (
    POLICY, PostCreate, PostReport, ProfilePublish, Social, holds, posts, profiles, public_url, quotas, reports,
)
from seiche_agent_network.store import Store


@pytest.fixture
def community():
    now = [1800000000.0]
    store = Store(":memory:", **generate_keys(), clock=lambda: now[0])
    owners, agents, owner_tokens, agent_tokens = [], [], [], []
    for name in ("PRIVATE NAME ALPHA", "PRIVATE NAME BETA"):
        owner_token = store.bootstrap_org(name)["owner_token"]
        owner = store.authenticate(owner_token)
        token = store.register_agent(owner, name, capabilities=["PRIVATE CAPABILITY"], public=True)["token"]
        owners.append(owner)
        agents.append(store.authenticate(token))
        owner_tokens.append(owner_token)
        agent_tokens.append(token)
    second_token = store.register_agent(owners[0], "PRIVATE SECOND AGENT")["token"]
    with TestClient(create_app(store, purge_interval=0)) as client:
        yield {"store": store, "now": now, "client": client, "social": Social(store), "owners": owners,
               "agents": agents, "second": store.authenticate(second_token), "owner_tokens": owner_tokens,
               "agent_tokens": agent_tokens,
               "owner_headers": [{"Authorization": "Bearer " + value} for value in owner_tokens],
               "agent_headers": [{"Authorization": "Bearer " + value} for value in agent_tokens]}
    store.close()


def profile(**changes):
    value = {"display_name": "Synthetic public research agent", "bio": "Synthetic public research profile",
             "role": "finance_research", "topics": ["funding", "liquidity"], "website_url": "https://example.com/research",
             "traffic_class": "real", "policy_version": POLICY, "acknowledged": True}
    return ProfilePublish(**(value | changes))


def publish(f, index=0, **changes):
    return f["social"].publish(f["owners"][index], f["agents"][index].agent_id, profile(**changes))["grant_id"]


def post(grant, **changes):
    return PostCreate(**({"event_id": str(uuid4()), "grant_id": grant, "topic": "funding", "kind": "question",
                         "text": "Synthetic community question", "source_urls": [], "parent_id": None} | changes))


def create(f, index=0, **changes):
    grant = publish(f, index)
    body = post(grant, **changes)
    return body, f["social"].create_post(f["agents"][index], body)["post"]


def failure(call, code, status):
    with pytest.raises(NetworkError) as result:
        call()
    assert result.value.code == code and result.value.status == status


def test_private_directory_never_publishes_implicitly_and_profile_projection_is_explicit(community):
    f = community
    assert f["social"].profiles()["profiles"] == []
    own = f["social"].me(f["owners"][0])
    assert len(own["profiles"]) == 2
    assert all(item["profile"] is None and item["grant_id"] is None and not item["published"] for item in own["profiles"])
    assert "PRIVATE" not in json.dumps(own)
    publish(f)
    public = f["social"].profile(f["agents"][0].agent_id)
    assert set(public) == {"agent_id", "display_name", "bio", "role", "topics", "website_url", "traffic_class",
                           "published_at", "updated_at", "evidence_kind", "identity_verification"}
    assert public["identity_verification"] == "self_declared"
    assert "PRIVATE" not in json.dumps(public)
    assert f["owners"][0].org_id not in json.dumps(public)
    assert "grant" not in json.dumps(public)
    assert len(f["social"].me(f["agents"][0])["profiles"]) == 1


def test_publication_owner_boundary_acknowledgement_and_put_cors(community):
    f = community
    agent_id = f["agents"][0].agent_id
    body = profile().model_dump()
    path = "/v1/social/profiles/" + agent_id
    assert f["client"].put(path, json=body, headers=f["agent_headers"][0]).status_code == 403
    assert f["client"].put(path, json=body, headers=f["owner_headers"][1]).status_code == 404
    for changes in ({"acknowledged": False}, {"acknowledged": 1}, {"policy_version": "old"}, {"private_prompt": "private canary"}):
        response = f["client"].put(path, json=body | changes, headers=f["owner_headers"][0])
        assert response.status_code == 422 and "private canary" not in response.text
    response = f["client"].put(path, json=body, headers=f["owner_headers"][0])
    assert response.status_code == 200
    assert response.json()["published"] and response.json()["grant_id"]
    cors = f["client"].options(path, headers={"Origin": "http://127.0.0.1:8765", "Access-Control-Request-Method": "PUT"})
    assert cors.status_code == 200 and "PUT" in cors.headers["access-control-allow-methods"]


@pytest.mark.parametrize("url", [
    "http://example.com/path", "https://user:secret@example.com/path", "https://example.com/#secret",
    "https://example.com/#", "https://localhost/path", "https://test.localhost/path", "https://router.local/",
    "https://127.0.0.1/", "https://10.0.0.1/", "https://169.254.169.254/", "https://[::1]/",
    "https://[::ffff:127.0.0.1]/", "https://2130706433/", "https://0177.0.0.1/", "https://0x7f.0.0.1/",
    "https://127%2e0%2e0%2e1/", "https://example.com:0/", "https://example.com:65536/", "https://example.com/secret\ncanary",
])
def test_private_or_ambiguous_source_urls_rejected(url):
    with pytest.raises(ValueError):
        public_url(url)


@pytest.mark.parametrize("url", ["https://example.com/source", "https://example.com:8443/source?a=1", "https://8.8.8.8/source"])
def test_public_https_source_urls_allowed_without_fetching(url):
    assert public_url(url) == url


def test_source_requirements_and_strict_post_fields(community):
    f = community
    body = post(publish(f)).model_dump()
    for changes in ({"kind": "research_note"}, {"kind": "source_update"}, {"kind": "reply"},
                    {"parent_id": str(uuid4())}, {"text": " "}, {"text": "x" * 2001},
                    {"event_id": body["event_id"].upper()}, {"source_urls": ["https://127.0.0.1/"]},
                    {"source_urls": ["https://example.com/" for _ in range(4)]}, {"org_id": "private canary"}):
        response = f["client"].post("/v1/social/posts", json=body | changes, headers=f["agent_headers"][0])
        assert response.status_code == 422
        assert "private canary" not in response.text
    response = f["client"].post("/v1/social/posts", json=body | {"kind": "research_note", "source_urls": ["https://example.com/source"]},
                                 headers=f["agent_headers"][0])
    assert response.status_code == 201 and response.json()["post"]["evidence_kind"] == "agent_authored"


@pytest.mark.parametrize("header", ["", "Basic secret", "Bearer", "Bearer invalid", "Bearer invalid extra"])
def test_invalid_supplied_credentials_never_fall_back_to_anonymous(community, header):
    for route in ("contract", "profiles", "posts"):
        result = community["client"].get("/v1/social/" + route, headers={"Authorization": header})
        assert result.status_code == 401


def test_post_agent_authority_grant_isolation_and_idempotency(community):
    f = community
    body = post(publish(f))
    failure(lambda: f["social"].create_post(f["owners"][0], body), "forbidden", 403)
    failure(lambda: f["social"].create_post(f["agents"][1], body), "publication_required", 403)
    first = f["client"].post("/v1/social/posts", json=body.model_dump(), headers=f["agent_headers"][0])
    second = f["client"].post("/v1/social/posts", json=body.model_dump(), headers=f["agent_headers"][0])
    assert first.status_code == 201 and second.status_code == 200
    assert second.json() == first.json() | {"status": "duplicate"}
    failure(lambda: f["social"].create_post(f["agents"][0], body.model_copy(update={"text": "changed"})), "idempotency_conflict", 409)
    value = first.json()["post"]
    assert "grant_id" not in json.dumps(value) and "org_id" not in json.dumps(value)
    assert f["social"].post(value["id"]) == value
    assert len(f["social"].feed()["posts"]) == 1


def test_profile_edit_preserves_grant_post_traffic_and_withdrawal_invalidates(community):
    f = community
    grant = publish(f, traffic_class="synthetic")
    body = post(grant)
    value = f["social"].create_post(f["agents"][0], body)["post"]
    assert f["social"].feed()["posts"] == [] and f["social"].profiles()["profiles"] == []
    assert len(f["social"].feed(include_synthetic=True)["posts"]) == 1
    assert publish(f, display_name="Edited synthetic profile", traffic_class="real") == grant
    assert f["social"].post(value["id"])["traffic_class"] == "synthetic"
    assert f["social"].feed()["posts"] == []
    f["social"].withdraw(f["owners"][0], f["agents"][0].agent_id)
    failure(lambda: f["social"].post(value["id"]), "not_found", 404)
    assert f["social"].feed(include_synthetic=True)["posts"] == []
    assert publish(f) != grant
    failure(lambda: f["social"].create_post(f["agents"][0], body), "publication_required", 403)
    with f["store"].engine.connect() as db:
        row = db.execute(select(posts)).mappings().one()
        assert row["text"] is None and row["source_urls"] is None and row["state"] == "withdrawn"
        assert db.scalar(select(func.sum(quotas.c.count))) == 2


def test_post_delete_is_owned_tombstoned_and_does_not_resurrect_on_retry(community):
    f = community
    body, value = create(f)
    failure(lambda: f["social"].delete_post(f["agents"][1], value["id"]), "not_found", 404)
    failure(lambda: f["social"].delete_post(f["second"], value["id"]), "not_found", 404)
    assert f["social"].delete_post(f["owners"][0], value["id"])["deleted"]
    assert f["social"].delete_post(f["agents"][0], value["id"])["deleted"]
    failure(lambda: f["social"].create_post(f["agents"][0], body), "post_unavailable", 409)
    failure(lambda: f["social"].post(value["id"]), "not_found", 404)
    with f["store"].engine.connect() as db:
        assert db.scalar(select(posts.c.text)) is None
        assert db.scalar(select(func.count()).select_from(posts)) == 1


def test_reply_thread_visible_parent_same_topic_one_level_and_deletion(community):
    f = community
    _, root = create(f)
    grant = publish(f, 1)
    reply = f["social"].create_post(f["agents"][1], post(grant, kind="reply", parent_id=root["id"]))["post"]
    assert [p["id"] for p in f["social"].feed()["posts"]] == [root["id"]]
    assert [p["id"] for p in f["social"].feed(parent_id=root["id"])["posts"]] == [reply["id"]]
    failure(lambda: f["social"].create_post(f["agents"][1], post(grant, kind="reply", parent_id=root["id"], topic="macro")), "invalid_thread", 409)
    failure(lambda: f["social"].create_post(f["agents"][1], post(grant, kind="reply", parent_id=reply["id"])), "invalid_thread", 409)
    f["social"].delete_post(f["agents"][0], root["id"])
    failure(lambda: f["social"].post(reply["id"]), "not_found", 404)
    failure(lambda: f["social"].feed(parent_id=root["id"]), "not_found", 404)


def test_current_agent_status_and_blocks_filter_public_views_and_replies(community):
    f = community
    _, root = create(f)
    grant = publish(f, 1)
    reply = f["social"].create_post(f["agents"][1], post(grant, kind="reply", parent_id=root["id"]))["post"]
    f["store"].block_agent(f["agents"][1], f["agents"][0].agent_id)
    assert f["social"].feed(f["agents"][1])["posts"] == []
    assert f["social"].feed(f["owners"][1])["posts"] == []
    assert f["social"].feed()["posts"]  # Public publication is still public.
    failure(lambda: f["social"].post(reply["id"], f["agents"][1]), "not_found", 404)
    failure(lambda: f["social"].create_post(f["agents"][1], post(grant, kind="reply", parent_id=root["id"])), "not_found", 404)
    f["store"].set_agent_status(f["owners"][0], f["agents"][0].agent_id, "paused")
    assert f["social"].feed()["posts"] == []
    failure(lambda: f["social"].post(root["id"]), "not_found", 404)
    failure(lambda: f["social"].post(reply["id"]), "not_found", 404)
    assert f["social"].me(f["owners"][0])["profiles"][0]["status"] in {"active", "paused"}
    with f["store"].engine.begin() as db:
        from seiche_agent_network.store import orgs
        db.execute(update(orgs).where(orgs.c.id == f["owners"][1].org_id).values(status="inactive"))
    assert f["social"].profiles()["profiles"] == []


def test_sql_pagination_filters_and_escaped_search(community):
    f = community
    grant = publish(f, display_name="Synthetic 100% research")
    publish(f, 1, display_name="Synthetic other", role="financial_journalism", topics=["macro"])
    for index in range(7):
        f["social"].create_post(f["agents"][0], post(grant, text=f"Synthetic public note {index}"))
    seen, cursor = [], None
    for _ in range(4):
        result = f["social"].feed(limit=2, cursor=cursor)
        seen.extend(item["id"] for item in result["posts"])
        cursor = result["next_cursor"]
    assert len(seen) == len(set(seen)) == 7 and cursor is None
    assert len(f["social"].profiles(q="%", topic="funding")["profiles"]) == 1
    assert len(f["social"].profiles(role="financial_journalism")["profiles"]) == 1
    assert f["social"].feed(topic="macro")["posts"] == []
    first = f["social"].profiles(limit=1)
    second = f["social"].profiles(limit=1, cursor=first["next_cursor"])
    assert first["profiles"][0]["agent_id"] != second["profiles"][0]["agent_id"]
    for query in ("limit=0", "limit=51", "topic=secret", "cursor=not-valid-json"):
        assert f["client"].get("/v1/social/posts?" + query).status_code in {400, 422}


def test_quota_rollback_retries_withdrawal_and_windows(community, monkeypatch):
    import seiche_agent_network.social as module
    monkeypatch.setattr(module, "posting_limits", lambda: {"agent_per_day": 3, "organization_per_day": 4,
                                                         "agent_per_minute": 1, "organization_per_minute": 2})
    f = community
    grant = publish(f)
    body = post(grant)
    assert f["social"].create_post(f["agents"][0], body)["status"] == "accepted"
    assert f["social"].create_post(f["agents"][0], body)["status"] == "duplicate"
    failure(lambda: f["social"].create_post(f["agents"][0], post(grant)), "social_capacity", 429)
    with f["store"].engine.connect() as db:
        assert db.scalar(select(quotas.c.count).where(quotas.c.bucket == "post_day")) == 1
    f["now"][0] += 60
    f["social"].create_post(f["agents"][0], post(grant))
    f["now"][0] += 60
    f["social"].create_post(f["agents"][0], post(grant))
    f["social"].withdraw(f["owners"][0], f["agents"][0].agent_id)
    grant = publish(f)
    f["now"][0] += 60
    failure(lambda: f["social"].create_post(f["agents"][0], post(grant)), "social_capacity", 429)
    f["now"][0] += 86400
    assert f["social"].create_post(f["agents"][0], post(grant))["status"] == "accepted"


def test_concurrent_duplicates_accept_once_and_org_quota_shared(community, monkeypatch):
    import seiche_agent_network.social as module
    monkeypatch.setattr(module, "posting_limits", lambda: {"agent_per_day": 3, "organization_per_day": 3,
                                                         "agent_per_minute": 3, "organization_per_minute": 3})
    f = community
    grant = publish(f)
    body = post(grant)
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: f["social"].create_post(f["agents"][0], body), range(8)))
    assert sum(item["status"] == "accepted" for item in result) == 1
    other_grant = f["social"].publish(f["owners"][0], f["second"].agent_id, profile())["grant_id"]
    def write(index):
        actor, active_grant = (f["agents"][0], grant) if index % 2 else (f["second"], other_grant)
        try:
            return f["social"].create_post(actor, post(active_grant))["status"]
        except NetworkError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(write, range(8)))
    assert result.count("accepted") == 2 and result.count("social_capacity") == 6


def test_reports_private_encrypted_not_automatic_hiding_and_operator_moderation(community):
    f = community
    body, root = create(f)
    grant = publish(f, 1)
    reply = f["social"].create_post(f["agents"][1], post(grant, kind="reply", parent_id=root["id"]))["post"]
    response = f["client"].post(f"/v1/social/posts/{root['id']}/report", json={"reason": "PRIVATE report canary"},
                                 headers=f["owner_headers"][1])
    assert response.status_code == 201 and response.json()["reviewed"] is False
    assert "canary" not in response.text
    with f["store"].engine.connect() as db:
        assert "canary" not in db.scalar(select(reports.c.reason))
    assert f["social"].post(root["id"])
    queue = f["social"].report_queue()
    assert queue["reports"][0]["reason"] == "PRIVATE report canary"
    assert f["client"].get("/v1/social/reports", headers=f["owner_headers"][0]).status_code == 404
    assert f["social"].moderate(root["id"], "Synthetic operator review")["hidden"] is True
    assert f["social"].report_queue()["reports"] == []
    failure(lambda: f["social"].post(reply["id"]), "not_found", 404)
    failure(lambda: f["social"].create_post(f["agents"][0], body), "post_unavailable", 409)


def test_expiry_idle_purge_and_tenant_delete(community):
    f = community
    _, root = create(f)
    _, other = create(f, 1)
    f["social"].report_post(f["agents"][1], root["id"], PostReport(reason="Synthetic report"))
    f["store"].delete_organization(f["owners"][0])
    for table in (profiles, posts, reports, quotas):
        with f["store"].engine.connect() as db:
            assert db.scalar(select(func.count()).select_from(table).where(table.c.org_id == f["owners"][0].org_id)) == 0
    assert f["social"].post(other["id"])
    f["now"][0] += 30 * 86400
    assert f["social"].feed()["posts"] == []
    failure(lambda: f["social"].post(other["id"]), "not_found", 404)
    receipt = f["store"].purge_expired()
    assert receipt["purged_social_posts"] == 1
    with f["store"].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(posts)) == 0
        assert db.scalar(select(func.count()).select_from(quotas)) == 0


def test_cli_moderation_uses_operator_database_not_owner_http_authority(community, monkeypatch, capsys):
    from seiche_agent_network import cli
    f = community
    _, value = create(f)
    f["social"].report_post(f["owners"][1], value["id"], PostReport(reason="Synthetic moderation reason"))
    monkeypatch.setattr(cli, "_store", lambda args: f["store"])
    monkeypatch.setattr(f["store"], "close", lambda: None)
    assert cli.main(["social-reports"]) == 0
    assert json.loads(capsys.readouterr().out)["reports"][0]["reason"] == "Synthetic moderation reason"
    assert cli.main(["social-moderate", "--post-id", value["id"], "--reason", "Synthetic confirmed policy violation"]) == 0
    assert json.loads(capsys.readouterr().out)["hidden"] is True
    assert cli.main(["social-suspend", "--organization-id", f["owners"][0].org_id,
                     "--reason", "Synthetic community hold"]) == 0
    assert json.loads(capsys.readouterr().out)["publication_suspended"] is True
    assert cli.main(["social-restore", "--organization-id", f["owners"][0].org_id]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["publication_suspended"] is False and restored["public_material_restored"] is False


def test_operator_hold_removes_public_material_owner_cannot_self_lift_and_restore_is_empty(community):
    f = community
    old_body, root = create(f)
    grant = publish(f, 1)
    reply = f["social"].create_post(f["agents"][1], post(grant, kind="reply", parent_id=root["id"]))["post"]
    f["social"].report_post(f["owners"][1], root["id"], PostReport(reason="Synthetic profile abuse"))
    assert f["social"].report_queue()["reports"][0]["author_org_id"] == f["owners"][0].org_id
    assert f["social"].suspend_organization(f["owners"][0].org_id, "PRIVATE operator hold reason")["publication_suspended"]
    mine = f["social"].me(f["owners"][0])
    assert mine["publication_suspended"] and all(item["publication_suspended"] for item in mine["profiles"])
    assert not any(item["published"] for item in mine["profiles"])
    failure(lambda: publish(f), "community_suspended", 403)
    f["social"].withdraw(f["owners"][0], f["agents"][0].agent_id)
    failure(lambda: publish(f), "community_suspended", 403)
    new_agent = f["store"].authenticate(f["store"].register_agent(f["owners"][0], "Synthetic replacement")["token"])
    failure(lambda: f["social"].publish(f["owners"][0], new_agent.agent_id, profile()), "community_suspended", 403)
    failure(lambda: f["social"].post(root["id"]), "not_found", 404)
    failure(lambda: f["social"].post(reply["id"]), "not_found", 404)
    assert f["social"].report_queue()["reports"] == []
    with f["store"].engine.connect() as db:
        assert "PRIVATE" not in db.scalar(select(holds.c.reason))
    assert f["social"].restore_organization(f["owners"][0].org_id)["public_material_restored"] is False
    assert f["social"].me(f["owners"][0])["publication_suspended"] is False
    assert not any(item["published"] for item in f["social"].me(f["owners"][0])["profiles"])
    failure(lambda: f["social"].create_post(f["agents"][0], old_body), "publication_required", 403)
    assert publish(f) != old_body.grant_id
    failure(lambda: f["social"].post(root["id"]), "not_found", 404)
    f["social"].suspend_organization(f["owners"][0].org_id, "Synthetic second hold")
    f["store"].delete_organization(f["owners"][0])
    with f["store"].engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(holds)) == 0


def test_feed_expiry_clock_is_captured_after_transaction_lock_wait(community, monkeypatch):
    f = community
    _, value = create(f)
    f["now"][0] = value["expires_at"] - 1
    original = f["store"]._transaction

    @contextmanager
    def delayed_transaction(*args, **kwargs):
        with original(*args, **kwargs) as db:
            f["now"][0] += 2
            yield db

    monkeypatch.setattr(f["store"], "_transaction", delayed_transaction)
    assert f["social"].feed()["posts"] == []
