from datetime import datetime, timezone, timedelta
import importlib
import json
import time

import httpx
import pytest

from agent_activity.adapters import AlpacaReadOnly, merchant_event
from agent_activity.analysis import reconcile, weekly_export, export_status
from agent_activity.collect import block_batch
from agent_activity.store import Store, Rejected
from test_evidence import event, enroll


@pytest.mark.parametrize("distinct_accounts", [True, False])
def test_aggregate_requires_distinct_reviewed_accounts_and_live_evidence(tmp_path, monkeypatch, distinct_accounts):
    store = Store(tmp_path / "state")
    now = datetime.now(timezone.utc)
    start = (now-timedelta(days=now.weekday()+7)).replace(hour=0, minute=0, second=0, microsecond=0)
    clock = [start.timestamp()-10]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    sources = [store.enroll("Test contributor "+str(i), "operator", "test-grant", "independent-"+str(i), resale_ref="test-resale") for i in range(3)]
    clock[0] = start.timestamp()+10
    for i in range(20):
        source = sources[0 if i < 8 else (1 if i < 14 else 2)]
        addr = "0x"+format(i+1 if distinct_accounts else i%3+1, "040x")
        agent = "agent-"+str(i)
        claim = store.claim(source["credential"], {"agent_id": agent, "agent_version": "v1", "venue": "hyperliquid", "account": addr,
            "evidence_sha256": "b"*64, "valid_from": start.isoformat(), "valid_until": (start+timedelta(days=30)).isoformat()})
        store.review_identity(claim["claim_id"], "test-independent-review")
        payload = event(event_id="fill-"+str(i), agent_id=agent, kind="fill", venue="hyperliquid", external_ref=str(i), instrument="BTC",
            quantity="1", side="buy", mode="live", occurred_at=datetime.fromtimestamp(clock[0], timezone.utc).isoformat())
        store.contribute(source["credential"], payload)
        observed = {"id": "observed-"+str(i), "kind": "fill", "account": addr, "external_ref": str(i), "instrument": "BTC",
                    "quantity": "1", "side": "buy", "occurred_at": clock[0]}
        store.capture("hyperliquid", "https://api.hyperliquid.xyz/info", {}, events=[observed])
    assert reconcile(store)["reconciled"] == 20
    clock[0] = start.timestamp()+7*86400+1
    result = weekly_export(store, start.strftime("%Y-%m-%d"))
    assert export_status(store, result["export_id"])["release_eligible"] is distinct_accounts
    if distinct_accounts:
        assert result["rows"][0]["participating_agent_accounts"] == 20
        with store.db(write=True) as db:
            db.execute("DELETE FROM public_events WHERE id='observed-0'")
        assert not export_status(store, result["export_id"])["release_eligible"]
        report = store.private_report(sources[0]["credential"])
        assert next(e for e in report["events"] if e["event"]["event_id"] == "fill-0")["verification"] == "source_reported"


def test_batched_headers_reject_duplicate_or_wrong_blocks(monkeypatch):
    module = importlib.import_module("agent_activity.collect")
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "request", lambda *args: [{"id": 1, "result": {"number": "0x1"}}, {"id": 1, "result": {"number": "0x1"}}])
    with pytest.raises(Rejected, match="duplicate"):
        block_batch([1, 2])


def test_alpaca_adapter_uses_fixed_read_only_endpoint_and_keeps_paper_mode(monkeypatch):
    module = importlib.import_module("agent_activity.adapters")
    original = httpx.Client
    observed = []
    row = {"activity_type": "FILL", "id": "activity-1", "transaction_time": datetime.now(timezone.utc).isoformat(),
        "symbol": "AAPL", "side": "buy", "qty": "2", "price": "100"}
    def handler(request):
        observed.append(request)
        return httpx.Response(200, json=[row])
    monkeypatch.setattr(module.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    adapter = AlpacaReadOnly("synthetic-test-oauth", paper=True)
    rows = list(adapter.fills("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"))
    normalized = adapter.event(rows[0], agent_id="agent-1", agent_version="v1", account_ref="local-account", journey_id="test-journey")
    assert normalized["mode"] == "paper" and normalized["price"] == "100" and "synthetic-test-oauth" not in json.dumps(normalized)
    assert observed[0].method == "GET" and observed[0].url.host == "paper-api.alpaca.markets"
    assert observed[0].url.path == "/v2/account/activities"


def test_merchant_settlement_and_fulfillment_remain_separate():
    kwargs = dict(economic_domain="digital_service", event_id="merchant-1", agent_id="agent-1", agent_version="v1", journey_id="order-1",
        occurred_at=datetime.now(timezone.utc).isoformat(), external_ref="receipt-1", evidence_sha256="b"*64)
    assert merchant_event(kind="payment_settled", amount="10", currency="USD", **kwargs)["kind"] == "payment_settled"
    assert merchant_event(kind="fulfilled", **kwargs)["amount"] is None
    with pytest.raises(ValueError):
        merchant_event(kind="payment_settled", amount="10", **kwargs)


def test_chunked_backup_rejects_truncation_and_cleans_failed_restore(tmp_path):
    import base64
    import os
    from cryptography.fernet import Fernet
    from agent_activity.cli import backup, restore, write_new
    store = Store(tmp_path / "state")
    store.capture("x402", "https://example.test", {"synthetic_entropy": base64.b64encode(os.urandom(1_100_000)).decode()})
    key, archive = tmp_path / "key", tmp_path / "backup.enc"
    write_new(key, Fernet.generate_key())
    assert backup(store, archive, key)["chunks"] >= 2
    assert restore(archive, tmp_path / "good", key)["status"] == "offline_restore_verified"
    truncated = tmp_path / "truncated.enc"
    write_new(truncated, archive.read_bytes()[:-20])
    with pytest.raises(Rejected, match="truncated"):
        restore(truncated, tmp_path / "bad", key)
    assert not (tmp_path / "bad").exists()
