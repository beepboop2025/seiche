"""Fixed-source, read-only collection. Discovery URLs are never fetched as instructions."""

from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit

from Crypto.Hash import keccak
import httpx

from .store import Rejected, SOURCES, digest

REGISTRY = "https://api.8004scan.io/api/v1/agents"
BAZAAR = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
BASE = "https://mainnet.base.org"
HYPERLIQUID = "https://api.hyperliquid.xyz/info"
ACP = "0x238e541bfefd82238730d00a2208e5497f1832e0"
ABI = json.loads(Path(__file__).with_name("acp-events.json").read_text())


def signature(text):
    return "0x" + keccak.new(digest_bits=256, data=text.encode()).hexdigest()


EVENTS = {signature(x["name"]+"("+",".join(i["type"] for i in x["inputs"])+")"): x for x in ABI}


def request(url, payload=None):
    # No source URL, redirect or arbitrary user-provided endpoint is followed.
    if url.split("?", 1)[0] not in {REGISTRY, BAZAAR, BASE, HYPERLIQUID}:
        raise Rejected("unreviewed_upstream")
    if url == BASE and os.environ.get("ACTIVITY_RPC_CONFIG"):
        config_path = Path(os.environ["ACTIVITY_RPC_CONFIG"])
        if config_path.exists():
            if config_path.is_symlink() or config_path.stat().st_mode & 0o077 or config_path.stat().st_uid != os.getuid():
                raise Rejected("rpc_config_must_be_owned_and_private")
            config = json.loads(config_path.read_text())
            endpoint = urlsplit(config.get("endpoint", ""))
            host = endpoint.hostname or ""
            if endpoint.scheme != "https" or endpoint.port not in {None, 443} or endpoint.username or endpoint.password or endpoint.fragment or not config.get("review_ref") or not (host == "base-mainnet.g.alchemy.com" or host.endswith(".quiknode.pro")):
                raise Rejected("unreviewed_rpc_provider")
            url = config["endpoint"]  # Never retained in evidence or error messages: may contain an API key.
    headers = {"User-Agent": "Seiche-Agent-Activity/0.1 (+https://agents.seiche.info/activity/)", "Accept": "application/json"}
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        with client.stream("POST" if payload is not None else "GET", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                raise Rejected(f"upstream_http_{response.status_code}")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 8_000_000:
                    raise Rejected("upstream_size_limit")
            return json.loads(data)


def rpc(method, params):
    if method not in {"eth_chainId", "eth_getBlockByNumber", "eth_getLogs", "eth_getCode", "eth_call"}:
        raise Rejected("rpc_method_not_read_only")
    time.sleep(2)  # Public endpoint: deliberately low request rate; never retry 429 in a loop.
    try:
        value = request(BASE, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    except Rejected as exc:
        raise Rejected(exc.code + "_" + method) from None
    if "error" in value or value.get("result") is None:
        raise Rejected("rpc_error_" + str(value.get("error", {}).get("code", "missing_result")))
    return value["result"]


def block_batch(numbers):
    """Batch two read-only headers at a time and pace public RPC use."""
    result = {}
    for start in range(0, len(numbers), 2):
        chosen = numbers[start:start+2]
        time.sleep(2)
        body = [{"jsonrpc": "2.0", "id": n, "method": "eth_getBlockByNumber", "params": [hex(n), False]} for n in chosen]
        response = request(BASE, body)
        if not isinstance(response, list) or len(response) != len(chosen):
            raise Rejected("rpc_batch_incomplete")
        for row in response:
            if row.get("id") not in chosen or row.get("error") or not isinstance(row.get("result"), dict):
                raise Rejected("rpc_batch_invalid")
            block = row["result"]
            if int(block["number"], 16) != row["id"]:
                raise Rejected("rpc_batch_identity_mismatch")
            result[row["id"]] = block
        if len(result) != min(start+2, len(numbers)):
            raise Rejected("rpc_batch_duplicate")
    return result


def registry_entities(body):
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise Rejected("registry_schema_changed")
    result = []
    for r in body["items"]:
        if r.get("is_testnet"):
            continue
        aid = r.get("agent_id")
        if not isinstance(aid, str) or len(aid) > 200:
            raise Rejected("registry_identity_missing")
        words = (str(r.get("name") or "") + " " + str(r.get("description") or "")).lower()
        result.append({"id": "erc8004:"+aid, "kind": "registered_agent", "name": str(r.get("name") or aid)[:150],
            "registry_id": aid, "chain_id": r.get("chain_id"), "registry_contract": r.get("contract_address"),
            "owner_address": r.get("owner_address"), "owner_is_trading_account": False,
            "declared_protocols": r.get("supported_protocols") or [], "x402_declared": bool(r.get("x402_supported")),
            "finance_keyword_match": bool(re.search(r"\b(trad(?:e|ing)|financ\w*|payment|commerce|arbitrage|defi)\b", words)),
            "classification": "keyword_metadata_only", "ai_authorship": "not_verified", "economic_activity_verified": False})
    return result


def bazaar_entities(body):
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise Rejected("bazaar_schema_changed")
    result = []
    for r in body["items"]:
        url = r.get("resource")
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2048:
            continue
        # Keep request examples and arbitrary schema bodies out of normalized records.
        quality = r.get("quality") or {}
        result.append({"id": "x402:"+digest(url.encode()), "kind": "paid_service",
            "name": str(r.get("serviceName") or url.split('/')[2])[:150], "resource_url": url,
            "reported_last_called_at": quality.get("lastCalledAt"),
            "reported_calls_30d": quality.get("l30DaysTotalCalls"),
            "reported_unique_payers_30d": quality.get("l30DaysUniquePayers"),
            "merchant_addresses": sorted({str(a["payTo"]).lower() for a in r.get("accepts", []) if a.get("payTo")}),
            "measurement": "facilitator_reported_rolling_window", "payers_are_agents": None,
            "economic_activity_verified": False})
    return result


def collect_catalog(store, source, pages=5):
    url, normalize = (REGISTRY, registry_entities) if source == "erc8004" else (BAZAAR, bazaar_entities)
    cursor = store.checkpoint(source) or {"next_offset": 0}
    offset = int(cursor.get("next_offset", 0))
    seen_pages, observed = set(), 0
    for _ in range(pages):
        target = url + "?" + urlencode({"limit": 100, "offset": offset})
        body = request(target)
        entities = normalize(body)
        page_ids = digest([e["id"] for e in entities])
        if page_ids in seen_pages:
            raise Rejected("pagination_repeated_page")
        seen_pages.add(page_ids)
        n = len(body["items"])
        pagination = body.get("pagination") or body
        total = pagination.get("total")
        complete = not n or body.get("has_more") is False or (isinstance(total, int) and offset+n >= total)
        next_offset = 0 if complete else offset+n
        store.capture(source, target, body, entities=entities, checkpoint={"next_offset": next_offset,
            "provider_reported_records": total, "enumeration_complete": False, "last_page_count": n,
            "sampling": "rolling offset sample; moving registry cannot establish snapshot completeness"})
        observed += n
        offset = next_offset
        if complete:
            break
    return {"records_observed": observed, "next_offset": offset}


def decode_log(log, block_time):
    if log.get("removed") or str(log.get("address", "")).lower() != ACP:
        raise Rejected("unexpected_or_removed_log")
    topics = log.get("topics") or []
    abi = EVENTS.get(topics[0] if topics else "")
    if not abi:
        return None
    if len(topics) != 1+sum(bool(i["indexed"]) for i in abi["inputs"]):
        raise Rejected("event_topic_shape_changed")
    data = bytes.fromhex(log.get("data", "0x")[2:])
    plain = [data[i:i+32] for i in range(0, len(data), 32)]
    if len(plain) != sum(not i["indexed"] for i in abi["inputs"]) or any(len(x) != 32 for x in plain):
        raise Rejected("event_data_shape_changed")
    indexed, unindexed, fields = iter(topics[1:]), iter(plain), {}
    for field in abi["inputs"]:
        word = bytes.fromhex(next(indexed)[2:]) if field["indexed"] else next(unindexed)
        if len(word) != 32:
            raise Rejected("malformed_event_word")
        typ = field["type"]
        if typ == "address":
            if any(word[:12]):
                raise Rejected("malformed_event_address")
            value = "0x"+word[-20:].hex()
        elif typ.startswith("uint"):
            value = str(int.from_bytes(word, "big"))
        elif typ == "bytes32":
            value = "0x"+word.hex()
        else:
            raise Rejected("unsupported_abi_type")
        fields[field["name"]] = value
    tx, index = log["transactionHash"], int(log["logIndex"], 16)
    return {"id": f"base:{ACP}:{tx}:{index}", "kind": abi["name"], "source": "virtuals_acp",
        "occurred_at": block_time, "block_number": int(log["blockNumber"], 16), "block_hash": log["blockHash"],
        "transaction_hash": tx, "log_index": index, "contract": ACP, "chain_id": 8453, "fields": fields,
        "finality": "rpc_finalized", "verification": "observed_contract_event", "ai_authorship": "not_verified",
        "external_ref": f"{tx}:{index}", "explorer_url": "https://basescan.org/tx/"+tx,
        "value_semantics": "native contract event; funded budget is not revenue", "schema": "virtuals-acp-sdk-0.1.15"}


def collect_acp(store, max_blocks=1200):
    if rpc("eth_chainId", []) != "0x2105":
        raise Rejected("unexpected_chain")
    head = rpc("eth_getBlockByNumber", ["finalized", False])
    end = int(head["number"], 16)
    prior = store.checkpoint("virtuals_acp")
    if prior:
        anchor = rpc("eth_getBlockByNumber", [hex(prior["through_block"]), False])
        if anchor["hash"] != prior["block_hash"]:
            raise Rejected("finalized_checkpoint_hash_changed")
        start = prior["through_block"]+1
        if start > end:
            store.capture("virtuals_acp", BASE, {"finalized_head": head, "no_new_finalized_blocks": True})
            return {"events": 0, "through_block": end}
    else:
        start = max(0, end-max_blocks+1)
    stop = min(end, start+max_blocks-1)
    boundary = rpc("eth_getBlockByNumber", [hex(stop), False])
    logs = rpc("eth_getLogs", [{"address": ACP, "fromBlock": hex(start), "toBlock": hex(stop), "topics": [list(EVENTS)]}])
    if not isinstance(logs, list) or len(logs) > 20000:
        raise Rejected("log_result_limit")
    numbers = sorted({int(log["blockNumber"], 16) for log in logs})
    if len(numbers) > 200:
        # Keep the same checkpoint and retry with a smaller contiguous block range.
        if max_blocks <= 1:
            raise Rejected("block_header_budget_exceeded")
        return collect_acp(store, max_blocks=max(1, max_blocks//2))
    block_cache, events = block_batch(numbers), []
    for log in logs:
        number = int(log["blockNumber"], 16)
        if not start <= number <= stop:
            raise Rejected("log_outside_requested_range")
        block = block_cache[number]
        if block["hash"] != log["blockHash"]:
            raise Rejected("block_log_mismatch")
        event = decode_log(log, int(block["timestamp"], 16))
        if event:
            events.append(event)
    proof = {"from_block": start, "through_block": stop, "boundary_hash": boundary["hash"],
        "logs": logs, "blocks": block_cache, "finalized_head": {k: head[k] for k in ["number", "hash", "timestamp"]},
        "contract": ACP, "abi_source": "@virtuals-protocol/acp-node-v2@0.1.15"}
    store.capture("virtuals_acp", BASE, proof, events=events,
        checkpoint={"from_block": prior["from_block"] if prior else start, "through_block": stop,
                    "block_hash": boundary["hash"], "observed_finalized_head": end, "history_before_start": "not_collected"})
    return {"events": len(events), "from_block": start, "through_block": stop, "backlog_blocks": end-stop}


def normalize_fill(address, fill):
    if not isinstance(fill.get("tid"), int) or fill.get("side") not in {"A", "B"}:
        raise Rejected("fill_schema_changed")
    timestamp = int(fill["time"])/1000
    if timestamp > time.time()+60:
        raise Rejected("future_fill")
    return {"id": f"hyperliquid:{address}:{fill['tid']}", "source": "hyperliquid", "kind": "fill",
        "occurred_at": timestamp, "account": address, "external_ref": str(fill["tid"]),
        "instrument": str(fill["coin"]), "side": "buy" if fill["side"] == "B" else "sell",
        "quantity": str(fill["sz"]), "price": str(fill["px"]), "fee": str(fill["fee"]),
        "fee_currency": str(fill["feeToken"]), "venue_reported_closed_pnl": str(fill.get("closedPnl", "")),
        "transaction_hash": fill.get("hash"), "order_id": str(fill.get("oid", "")),
        "ai_authorship": "requires_valid_identity_and_action_link", "verification": "venue_observed",
        "return_performance": "not_computed_incomplete_portfolio"}


def collect_hyperliquid(store):
    # Only explicitly reviewed PUBLIC account links are polled and published.
    # Private account ingestion stays with the contributor until separate publication permission.
    addresses = sorted({r["account"].lower() for r in store.links(public_only=True) if r["venue"] == "hyperliquid"})[:100]
    if not addresses:
        raise Rejected("no_reviewed_public_accounts")
    total = 0
    prior = store.checkpoint("hyperliquid") or {}
    for address in addresses:
        start = prior.get(address, int((time.time()-86400)*1000))
        body = request(HYPERLIQUID, {"type": "userFillsByTime", "user": address, "startTime": start, "aggregateByTime": False})
        if not isinstance(body, list):
            raise Rejected("fill_response_schema_changed")
        events = [normalize_fill(address, f) for f in body]
        # Inclusive replay prevents same-millisecond fills being skipped; ids deduplicate.
        latest = max([start, *[int(f["time"]) for f in body]])
        if len(body) >= 2000 and latest == start:
            raise Rejected("fill_timestamp_saturation_gap")
        prior[address] = latest
        store.capture("hyperliquid", HYPERLIQUID, {"account": address, "start_time": start, "fills": body,
            "history_limit": 10000, "full_history_established": False}, events=events, checkpoint=prior)
        total += len(events)
    return {"accounts": len(addresses), "fills": total}


def collect(store, sources=None, force=False):
    results = {}
    with (store.root / "collection.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("collector_already_running", 409)
        status = {r["id"]: r for r in store.source_status()}
        for source in sources or SOURCES:
            if source not in SOURCES:
                raise Rejected("unknown_source")
            last = status[source]["captured_at"]
            interval = min(SOURCES[source][3], 900) if status[source]["status"] == "error" else SOURCES[source][3]
            if last and not force and time.time()-last < interval:
                results[source] = {"status": "not_due"}
                continue
            try:
                if source in {"erc8004", "x402"}:
                    result = collect_catalog(store, source)
                elif source == "virtuals_acp":
                    result = collect_acp(store)
                else:
                    result = collect_hyperliquid(store)
                results[source] = dict(result, status="ok")
            except (Rejected, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                code = exc.code if isinstance(exc, Rejected) else type(exc).__name__
                store.capture(source, SOURCES[source][1], {"error": code}, error=code)
                results[source] = {"status": "unavailable", "reason": code}
        store.expire()
    return results
