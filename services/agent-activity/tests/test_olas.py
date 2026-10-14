import importlib
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_activity import olas
from agent_activity.api import create_app
from agent_activity.collect import collect
from agent_activity.store import Store, Rejected


def block(number, timestamp=None):
    return {"number": hex(number), "hash": "0x"+format(number, "064x"),
            "timestamp": hex(timestamp or int(time.time())-100)}


def log(number=99, index=1, name="MarketplaceRequest"):
    topic = next(t for t, abi in olas.EVENTS.items() if abi["name"] == name)
    abi = olas.EVENTS[topic]
    return {"address": olas.MARKETPLACE, "topics": [topic]+["0x"+"00"*12+"11"*20 for f in abi["inputs"] if f["indexed"]],
        "data": "0x"+"00"*32*sum(not f["indexed"] for f in abi["inputs"]),
        "blockNumber": hex(number), "blockHash": block(number)["hash"],
        "transactionHash": "0x"+format(number, "064x"), "logIndex": hex(index), "removed": False}


def install_rpc(monkeypatch, logs=None, head=100):
    calls = []
    def fake(method, params):
        calls.append((method, params))
        if method == "eth_chainId": return "0x64"
        if method == "eth_getBlockByNumber": return block(head if params[0] == "finalized" else int(params[0], 16))
        if method == "eth_getCode": return "0x6001"
        if method == "eth_getLogs":
            selected = logs if logs is not None else [log()]
            return selected(params[0]) if callable(selected) else selected
        raise AssertionError(method)
    monkeypatch.setattr(olas, "rpc", fake)
    return calls


def test_log_observation_does_not_invent_batch_members_or_ai_authorship():
    r = block(99)
    value = olas.normalize(log(), {"number": 99, "hash": r["hash"], "timestamp": int(r["timestamp"], 16)})
    assert value["kind"] == "OlasMarketplaceRequest"
    assert value["count_unit"] == "event_log" and value["batch_members"] == "not_decoded"
    assert value["ai_authorship"] == "not_verified" and "amount" not in value
    assert "data" not in value and len(value["payload_sha256"]) == 64
    assert all(abi["name"] != "Deliver" for abi in olas.EVENTS.values())


@pytest.mark.parametrize("change", [{"removed": True}, {"blockHash": "0x"+"ff"*32},
    {"data": "0x01"}, {"transactionHash": "not-a-hash"}, {"address": "0x"+"00"*20}])
def test_malformed_or_unfinalized_log_rejected(change):
    r = block(99)
    with pytest.raises(Rejected):
        olas.normalize(dict(log(), **change), {"number": 99, "hash": r["hash"], "timestamp": int(r["timestamp"], 16)})


def test_collector_advances_exactly_one_contiguous_window(monkeypatch, tmp_path):
    store = Store(tmp_path/"state")
    calls = install_rpc(monkeypatch)
    result = olas.collect_olas(store, max_blocks=10)
    assert result["event_logs"] == 1 and result["from_block"] == 91
    checkpoint = store.checkpoint(olas.SOURCE)
    assert checkpoint["through_block"] == 100 and checkpoint["unique_jobs"] is None
    assert checkpoint["history_before_start"] == "not_collected"
    receipt = store.receipt(store.public_events()[0]["receipt"], raw=True)
    assert receipt["body"]["abi_source_revision"] == olas.REVISION
    assert receipt["body"]["payload_content_fetched"] is False
    assert {method for method, _ in calls} == {"eth_chainId", "eth_getBlockByNumber", "eth_getCode", "eth_getLogs"}
    calls = install_rpc(monkeypatch, logs=[], head=105)
    olas.collect_olas(store)
    query = next(params[0] for method, params in calls if method == "eth_getLogs")
    assert query["fromBlock"] == hex(101) and query["toBlock"] == hex(105)
    assert store.checkpoint(olas.SOURCE)["from_block"] == 91


def test_failed_read_preserves_checkpoint_and_prior_events(monkeypatch, tmp_path):
    store = Store(tmp_path/"state")
    install_rpc(monkeypatch)
    olas.collect_olas(store)
    before = store.checkpoint(olas.SOURCE)
    install_rpc(monkeypatch, [dict(log(102), blockHash="0x"+"ff"*32)], head=105)
    assert collect(store, [olas.SOURCE], force=True)[olas.SOURCE]["status"] == "unavailable"
    assert store.checkpoint(olas.SOURCE) == before
    assert len(store.public_events()) == 1
    status = next(r for r in store.source_status() if r["id"] == olas.SOURCE)
    assert status["status"] == "error" and status["error"] == "olas_block_log_mismatch"


def test_large_window_shrinks_without_skipping_its_start(monkeypatch, tmp_path):
    store = Store(tmp_path/"state")
    def rows(query):
        return [log(n) for n in range(int(query["fromBlock"], 16), int(query["toBlock"], 16)+1)]
    calls = install_rpc(monkeypatch, rows, head=600)
    result = olas.collect_olas(store)
    queries = [params[0] for method, params in calls if method == "eth_getLogs"]
    assert all(q["fromBlock"] == "0x1" for q in queries)
    assert result["through_block"] == 75 and result["event_logs"] == 75
    assert result["backlog_blocks"] == 525


def test_duplicate_logs_and_changed_checkpoint_fail_closed(monkeypatch, tmp_path):
    store = Store(tmp_path/"state")
    install_rpc(monkeypatch, [log(), log()])
    with pytest.raises(Rejected, match="duplicate"):
        olas.collect_olas(store)
    assert store.checkpoint(olas.SOURCE) is None and not store.public_events()
    install_rpc(monkeypatch)
    olas.collect_olas(store)
    with store.db(write=True) as db:
        db.execute("UPDATE checkpoints SET value=replace(value,?,?) WHERE source=?", (block(100)["hash"], "0x"+"ff"*32, olas.SOURCE))
    with pytest.raises(Rejected, match="checkpoint_hash"):
        olas.collect_olas(store)


def test_stale_chain_head_is_not_reported_as_fresh_collection(monkeypatch, tmp_path):
    store = Store(tmp_path/"state")
    install_rpc(monkeypatch)
    monkeypatch.setattr(olas, "header", lambda _: {"number": 100, "hash": block(100)["hash"], "timestamp": time.time()-1801})
    result = collect(store, [olas.SOURCE], force=True)
    assert result[olas.SOURCE]["reason"] == "olas_stale_finalized_head"
    assert store.checkpoint(olas.SOURCE) is None


def test_public_protocol_view_excludes_account_records_and_opaque_payload(monkeypatch, tmp_path):
    store = Store(tmp_path/"state")
    install_rpc(monkeypatch)
    olas.collect_olas(store)
    store.capture("hyperliquid", "https://api.hyperliquid.xyz/info", {}, events=[
        {"id": "private-account", "kind": "fill", "occurred_at": time.time(), "account": "never-public"}])
    with TestClient(create_app(store, "test-operator")) as client:
        response = client.get("/v1/protocol-events").json()
        assert len(response["items"]) == 1
        assert response["items"][0]["source"] == olas.SOURCE
        assert response["items"][0]["chain_id"] == 100
        assert "payload_sha256" not in response["items"][0]
        assert client.get("/v1/protocol-events?source=hyperliquid").status_code == 400
        assert client.get("/v1/protocol-events?source=virtuals_acp").json()["items"] == []
        assert client.get("/v1/coverage").json()["coverage"]["unique_active_ai_agents"] is None


def test_rpc_uses_only_fixed_public_reads_without_credentials(monkeypatch):
    module = importlib.import_module("agent_activity.collect")
    original, observed = httpx.Client, []
    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"})
    monkeypatch.setattr(module.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(olas.time, "sleep", lambda _: None)
    assert olas.rpc("eth_chainId", []) == "0x64"
    assert observed[0].url.host == "rpc.gnosischain.com"
    assert "authorization" not in observed[0].headers
    with pytest.raises(Rejected, match="read_only"):
        olas.rpc("eth_sendRawTransaction", ["0x00"])
    assert len(observed) == 1
