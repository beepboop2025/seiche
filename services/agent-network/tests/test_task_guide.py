from fastapi.testclient import TestClient

from seiche_agent_network.api import create_app
from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.store import Store
from seiche_agent_network.task_guide import GOALS, TOPICS


def test_free_task_routing_does_not_enroll_or_record_signals(tmp_path):
    store = Store(tmp_path / "network.db", **generate_keys())
    try:
        with TestClient(create_app(store)) as client:
            metadata = client.get("/v1/task-guide").json()
            assert metadata["account_required"] is False
            assert metadata["defaults"] == {"goal": "research", "topic": "ai_economy"}
            for goal in GOALS:
                for topic in TOPICS:
                    response = client.post("/v1/task-guide", json={"goal": goal, "topic": topic})
                    assert response.status_code == 200
                    value = response.json()
                    assert value["not_recorded"] is True
                    assert value["shareable_context"] == {"goal": goal, "topic": topic}
                    assert value["recommended_products"][0]["id"] == TOPICS[topic][1]
                    assert len(value["recommended_products"]) <= 3
            from sqlalchemy import func, select
            with store.engine.connect() as db:
                from seiche_agent_network.store import orgs
                assert db.execute(select(func.count()).select_from(orgs)).scalar_one() == 0
    finally:
        store.close()


def test_private_free_text_and_unknown_choices_are_rejected_without_echo(tmp_path):
    store = Store(tmp_path / "network.db", **generate_keys())
    try:
        with TestClient(create_app(store)) as client:
            private = "private-buying-plan@example.test"
            for body in ({"goal": private, "topic": "ai_economy"},
                         {"goal": "research", "topic": "ai_economy", "prompt": private}):
                response = client.post("/v1/task-guide", json=body)
                assert response.status_code == 422
                assert private not in response.text
            arbitrage = client.post("/v1/task-guide", json={"goal": "arbitrage", "topic": "market_liquidity"}).json()
            assert [p["id"] for p in arbitrage["recommended_products"]] == ["undertow", "seiche"]
            assert "executable price spread" in arbitrage["coverage_note"]
            buying = client.post("/v1/task-guide", json={"goal": "buy", "topic": "ai_economy"}).json()
            assert "checkout" in buying["coverage_note"]
    finally:
        store.close()
