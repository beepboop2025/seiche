import json
from datetime import datetime, timezone, timedelta
import time

from fastapi.testclient import TestClient
import pytest

from agent_activity.api import create_app
from agent_activity.analysis import reconcile, weekly_export, export_status, same_fill
from agent_activity.cli import backup, restore, write_new
from agent_activity.collect import registry_entities, bazaar_entities, decode_log, EVENTS, ACP
from agent_activity.contracts import Contribution
from agent_activity.sdk import Client, Outbox
from agent_activity.store import Store, Rejected, digest
from cryptography.fernet import Fernet


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


def enroll(store, **kwargs):
    return store.enroll("Synthetic test organization", "operator", "test-grant", "test-independent", **kwargs)


def event(**kwargs):
    value = {"event_id": "event-1", "agent_id": "agent-1", "agent_version": "v1", "journey_id": "journey-1",
        "kind": "tool_call", "occurred_at": datetime.now(timezone.utc).isoformat(), "mode": "test",
        "venue": "test", "evidence_sha256": "a"*64, "tool": "funding"}
    value.update(kwargs)
    return value


def test_permissions_idempotency_and_encrypted_storage(store):
    source = enroll(store)
    payload = event()
    first = store.contribute(source["credential"], payload)
    again = store.contribute(source["credential"], payload)
    assert again["duplicate"] and first["sha256"] == again["sha256"]
    with pytest.raises(Rejected, match="conflict"):
        store.contribute(source["credential"], dict(payload, tool="different"))
    with store.db() as db:
        row = db.execute("SELECT * FROM contributions").fetchone()
        assert b"journey-1" not in row["payload"]
        assert db.execute("SELECT token_hash FROM contributors").fetchone()[0] != source["credential"]


def test_tenant_isolation_and_withdrawal(store):
    a, b = enroll(store), enroll(store)
    store.contribute(a["credential"], event())
    assert store.private_report(b["credential"])["retained_events"] == 0
    store.withdraw(a["credential"])
    with pytest.raises(Rejected, match="credential"):
        store.private_report(a["credential"])
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM contributions").fetchone()[0] == 0


def test_permission_time_and_source_authority(store):
    source = enroll(store)
    for when in [datetime.now(timezone.utc)-timedelta(days=1), datetime.now(timezone.utc)+timedelta(hours=1)]:
        with pytest.raises(Rejected, match="window"):
            store.contribute(source["credential"], event(occurred_at=when.isoformat()))
    with pytest.raises(Rejected, match="authority"):
        store.contribute(source["credential"], event(kind="payment_settled", economic_domain="digital_service", external_ref="payment-1"))


def test_event_validation_rejects_prompts_and_nonfinite_amounts():
    for change in [{"prompt": "private"}, {"amount": "NaN", "currency": "USD"}, {"quantity": "0"}, {"kind": "fill"}]:
        with pytest.raises(ValueError): Contribution.model_validate(event(**change))


def test_live_api_never_exposes_private_rows(store):
    source = enroll(store)
    store.contribute(source["credential"], event())
    with TestClient(create_app(store, "operator-test")) as client:
        assert client.get("/v1/me").status_code == 401
        assert client.get("/v1/operator/entities", headers={"Authorization": "Bearer "+source["credential"]}).status_code == 403
        public = client.get("/v1/coverage").json()
        assert "agent-1" not in json.dumps(public)
        assert public["coverage"]["global_coverage_percent"] is None
        assert client.get("/v1/me", headers={"Authorization": "Bearer "+source["credential"]}).json()["retained_events"] == 1
        assert client.get("/", headers={"Host": "malicious.example"}).status_code == 400
        assert client.get("/").headers["content-security-policy"].startswith("default-src 'none'")
        assert client.post("/v1/events", content=b"x"*65537, headers={"Content-Type":"application/json"}).status_code == 413


def test_mcp_is_read_only_and_handles_invalid_inputs(store):
    with TestClient(create_app(store, "operator-test")) as client:
        response = client.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"tools/list"}).json()
        assert {x["name"] for x in response["result"]["tools"]} == {"agent_activity_coverage", "agent_activity_sources"}
        for params in [[1], {"name":"enroll"}, {"name":"agent_activity_coverage","arguments":{"credential":"x"}}]:
            assert "error" in client.post("/mcp", json={"jsonrpc":"2.0","id":2,"method":"tools/call","params":params}).json()


def test_capture_deduplicates_but_does_not_overwrite_events(store):
    value = {"id":"base:test:1","kind":"JobFunded","occurred_at":time.time(),"amount":"10"}
    r = store.capture("virtuals_acp","https://mainnet.base.org",{"original":1},events=[value])
    store.capture("virtuals_acp","https://mainnet.base.org",{"original":2},events=[value])
    assert len(store.public_events()) == 1
    with pytest.raises(Rejected, match="conflicting"):
        store.capture("virtuals_acp","https://mainnet.base.org",{},events=[dict(value,amount="99")],checkpoint={"wrong":1})
    assert store.checkpoint("virtuals_acp") is None
    assert store.receipt(r,raw=True)["body"] == {"original":1}


def test_source_failure_is_not_hidden_by_previous_success(store):
    store.capture("x402", "https://example.test", {"items":[]})
    store.capture("x402", "https://example.test", {"error":"timeout"},error="timeout")
    assert next(x for x in store.source_status() if x["id"]=="x402")["status"] == "error"


def test_registry_and_catalog_do_not_promote_metadata():
    r=registry_entities({"items":[{"agent_id":"8453:test:1","name":"Trading Agent","owner_address":"0x123","is_verified":True}]})[0]
    assert r["finance_keyword_match"] and r["economic_activity_verified"] is False and not r["owner_is_trading_account"]
    s=bazaar_entities({"items":[{"resource":"https://example.test/api","quality":{"l30DaysUniquePayers":100}}]})[0]
    assert s["payers_are_agents"] is None


def test_abi_decoding_preserves_event_kind_and_native_units():
    topic=next(k for k,v in EVENTS.items() if v["name"]=="JobFunded")
    log={"address":ACP,"topics":[topic,"0x"+format(123,"064x"),"0x"+"00"*12+"11"*20],
         "data":"0x"+format(500,"064x"),"transactionHash":"0x"+"aa"*32,"logIndex":"0x1","blockNumber":"0x10","blockHash":"0x"+"bb"*32}
    decoded=decode_log(log,12345678)
    assert decoded["kind"]=="JobFunded" and decoded["fields"]["amount"]=="500"
    assert decoded["ai_authorship"]=="not_verified"
    with pytest.raises(Rejected):decode_log(dict(log,removed=True),1)
    with pytest.raises(Rejected):decode_log(dict(log,data="0x01"),1)


def test_independent_fill_reconciliation_requires_identity_and_exact_match(store):
    source=enroll(store)
    now=datetime.now(timezone.utc)
    addr="0x"+"11"*20
    claim={"agent_id":"agent-1","agent_version":"v1","venue":"hyperliquid","account":addr,"evidence_sha256":"b"*64,
           "valid_from":now.isoformat(),"valid_until":(now+timedelta(days=1)).isoformat()}
    claim_id=store.claim(source["credential"],claim)["claim_id"]
    payload=event(kind="fill",venue="hyperliquid",external_ref="123",instrument="BTC",quantity="0.1",side="buy",mode="live")
    store.contribute(source["credential"],payload)
    observed={"id":"hyperliquid:sample:123","kind":"fill","account":addr,"external_ref":"123","instrument":"BTC","quantity":"0.1","side":"buy","occurred_at":datetime.fromisoformat(payload["occurred_at"]).timestamp()}
    store.capture("hyperliquid","https://api.hyperliquid.xyz/info",{},events=[observed])
    assert reconcile(store)["reconciled"]==0
    store.review_identity(claim_id,"independent-test-review")
    assert reconcile(store)["reconciled"]==1
    assert store.private_report(source["credential"])["events"][0]["verification"]=="independently_reconciled"
    assert not same_fill(Contribution.model_validate(payload),dict(observed,quantity="0.2"))


def test_empty_export_cannot_be_released_and_rights_change_invalidates(store):
    monday=datetime.now(timezone.utc)-timedelta(days=datetime.now(timezone.utc).weekday()+7)
    result=weekly_export(store,monday.strftime("%Y-%m-%d"))
    assert result["status"]=="insufficient_qualified_data"
    assert not export_status(store,result["export_id"])["release_eligible"]
    enroll(store)
    assert not export_status(store,result["export_id"])["valid"]


def test_recovery_cannot_resurrect_contributor_access(store,tmp_path):
    c=enroll(store);store.contribute(c["credential"],event())
    key=tmp_path/"backup.key";write_new(key,Fernet.generate_key())
    out=tmp_path/"snapshot.enc";backup(store,out,key)
    restored=tmp_path/"restored";restore(out,restored,key)
    rs=Store(restored)
    with pytest.raises(Rejected):rs.private_report(c["credential"])
    assert not rs.links()
    with pytest.raises(Rejected):restore(out,restored,key)


def test_outbox_idempotency_receipts_and_destination_binding(tmp_path):
    class Fake:
        endpoint="https://example.test/activity"
        credential="synthetic-test-credential"
        def send(self,value):return {"event_id":value["event_id"],"sha256":digest(value)}
    out=Outbox(tmp_path/"outbox",Fake());value=event();out.enqueue(value);out.enqueue(value)
    assert out.flush()["delivered"]==1 and out.status()=={"delivered":1}
    with out.db() as db:assert db.execute("SELECT payload FROM pending").fetchone()[0] is None
    other=Fake();other.endpoint="https://elsewhere.test"
    with pytest.raises(ValueError,match="bound"):Outbox(tmp_path/"outbox",other)


def test_client_refuses_insecure_or_credential_urls():
    for u in ["http://example.test","https://user:secret@example.test","https://example.test?secret=1"]:
        with pytest.raises(ValueError):Client(u,"key")


def test_retention_removes_private_records(store):
    c=enroll(store);store.contribute(c["credential"],event())
    with store.db(write=True) as db:db.execute("UPDATE contributions SET expires=?",(time.time()-1,))
    store.expire()
    assert store.private_report(c["credential"])["retained_events"]==0


def test_pilot_does_not_grant_access_and_public_reads_hide_contact(store):
    with TestClient(create_app(store)) as client:
        r=client.post("/v1/pilot-requests",json={"organization":"Synthetic","contact_email":"example@example.test","role":"platform","use_case":"due_diligence","notice_accepted":True})
        assert r.status_code==202 and not r.json()["contributor_access_granted"]
        assert "example@example.test" not in client.get("/v1/coverage").text
