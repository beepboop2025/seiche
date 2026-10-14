"""Bounded public Olas event observations. No keys, transactions or payload URLs."""

import json
from pathlib import Path
import re
import time

from .collect import GNOSIS, request, signature
from .store import Rejected, digest

SOURCE = "olas_marketplace"
CHAIN_ID = 100
MARKETPLACE = "0x735faab1c4ec41128c367afb5c3bac73509f70bb"
REVISION = "a9398109ec939edb3d1e6aed15f8ced26d0258a0"
ABI = json.loads(Path(__file__).with_name("olas-events.json").read_text())
EVENTS = {signature(x["name"]+"("+",".join(i["type"] for i in x["inputs"])+")"): x for x in ABI}
HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")


def rpc(method, params):
    if method not in {"eth_chainId", "eth_getBlockByNumber", "eth_getLogs", "eth_getCode"}:
        raise Rejected("olas_rpc_method_not_read_only")
    time.sleep(2)
    result = request(GNOSIS, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if not isinstance(result, dict) or result.get("jsonrpc") != "2.0" or result.get("id") != 1 or result.get("error") or result.get("result") is None:
        raise Rejected("olas_rpc_invalid_response")
    return result["result"]


def header(tag):
    value = rpc("eth_getBlockByNumber", [tag, False])
    if not isinstance(value, dict) or not HASH.fullmatch(str(value.get("hash", ""))):
        raise Rejected("olas_invalid_block_header")
    number, timestamp = int(value["number"], 16), int(value["timestamp"], 16)
    if number < 0 or timestamp <= 0 or timestamp > time.time()+60:
        raise Rejected("olas_invalid_block_clock")
    if tag != "finalized" and number != int(tag, 16):
        raise Rejected("olas_block_identity_mismatch")
    return {"number": number, "timestamp": timestamp, "hash": value["hash"].lower()}


def normalize(log, block):
    """Name the ABI event, preserving its payload as opaque source evidence.

    Batched requests and deliveries are deliberately not counted as jobs. The
    contract's per-request Deliver logs are excluded to avoid a second tally of
    the same delivery alongside its MarketplaceDeliveryWithSignatures event.
    """
    if log.get("removed") or str(log.get("address", "")).lower() != MARKETPLACE:
        raise Rejected("olas_unexpected_or_removed_log")
    topics = log.get("topics")
    if not isinstance(topics, list) or not topics or any(not HASH.fullmatch(str(t)) for t in topics):
        raise Rejected("olas_invalid_topics")
    abi = EVENTS.get(topics[0].lower())
    if not abi or len(topics) != 1+sum(bool(f["indexed"]) for f in abi["inputs"]):
        raise Rejected("olas_unreviewed_event")
    for topic in topics[1:]:  # All indexed fields in this reviewed ABI subset are addresses.
        if int(topic[2:26], 16):
            raise Rejected("olas_malformed_indexed_address")
    payload = log.get("data")
    if not isinstance(payload, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{64})+", payload):
        raise Rejected("olas_malformed_event_data")
    if (len(payload)-2)//64 < sum(not f["indexed"] for f in abi["inputs"]):
        raise Rejected("olas_truncated_event_data")
    tx, block_hash = log.get("transactionHash"), log.get("blockHash")
    if not HASH.fullmatch(str(tx)) or not HASH.fullmatch(str(block_hash)):
        raise Rejected("olas_invalid_event_identity")
    number, index = int(log["blockNumber"], 16), int(log["logIndex"], 16)
    if number != block["number"] or block_hash.lower() != block["hash"] or index < 0:
        raise Rejected("olas_block_log_mismatch")
    tx = tx.lower()
    return {"id": f"gnosis:{MARKETPLACE}:{tx}:{index}", "source": SOURCE,
        "kind": "Olas"+abi["name"], "chain_id": CHAIN_ID, "contract": MARKETPLACE,
        "occurred_at": block["timestamp"], "block_number": number, "block_hash": block["hash"],
        "transaction_hash": tx, "log_index": index, "external_ref": f"{tx}:{index}",
        "explorer_url": "https://gnosisscan.io/tx/"+tx, "finality": "rpc_finalized",
        "verification": "observed_contract_event", "ai_authorship": "not_verified",
        "protocol_association": "olas_marketplace_contract", "count_unit": "event_log",
        "batch_members": "not_decoded", "payload_sha256": digest(bytes.fromhex(payload[2:])),
        "value_semantics": "protocol acknowledgement; no independently verified quality, payment amount or revenue",
        "schema": "olas-marketplace-"+REVISION}


def collect_olas(store, max_blocks=600):
    if not 1 <= max_blocks <= 600:
        raise Rejected("olas_block_window_limit")
    if rpc("eth_chainId", []) != hex(CHAIN_ID):
        raise Rejected("olas_unexpected_chain")
    head = header("finalized")
    if time.time()-head["timestamp"] > 1800:
        raise Rejected("olas_stale_finalized_head")
    prior = store.checkpoint(SOURCE)
    if prior:
        if head["number"] < prior["through_block"]:
            raise Rejected("olas_finalized_head_behind_checkpoint")
        anchor = header(hex(prior["through_block"]))
        if anchor["hash"] != prior["block_hash"]:
            raise Rejected("olas_finalized_checkpoint_hash_changed")
        start = prior["through_block"]+1
        if start > head["number"]:
            store.capture(SOURCE, GNOSIS, {"finalized_head": head, "no_new_finalized_blocks": True})
            return {"event_logs": 0, "through_block": prior["through_block"]}
    else:
        start = max(0, head["number"]-max_blocks+1)
    stop = min(head["number"], start+max_blocks-1)
    code = rpc("eth_getCode", [MARKETPLACE, hex(stop)])
    if not isinstance(code, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", code):
        raise Rejected("olas_contract_code_missing")
    code_hash = digest(bytes.fromhex(code[2:]))
    if prior and prior["contract_code_sha256"] != code_hash:
        raise Rejected("olas_contract_code_changed")
    while True:
        logs = rpc("eth_getLogs", [{"address": MARKETPLACE, "fromBlock": hex(start),
            "toBlock": hex(stop), "topics": [list(EVENTS)]}])
        if not isinstance(logs, list) or len(logs) > 20000:
            raise Rejected("olas_log_result_limit")
        numbers = sorted({int(log["blockNumber"], 16) for log in logs})
        if any(n < start or n > stop for n in numbers):
            raise Rejected("olas_log_outside_requested_range")
        if len(numbers) <= 100 and len(logs) <= 2000:
            break
        if start == stop:
            raise Rejected("olas_block_budget_exceeded")
        # Preserve the starting block while shrinking: no holes in the checkpoint.
        stop = start+(stop-start)//2
    blocks = {n: header(hex(n)) for n in sorted(set(numbers+[stop]))}
    events = [normalize(log, blocks[int(log["blockNumber"], 16)]) for log in logs]
    if len({e["id"] for e in events}) != len(events):
        raise Rejected("olas_duplicate_upstream_log")
    # Detect a changing endpoint view before advancing the durable checkpoint.
    if header(hex(stop))["hash"] != blocks[stop]["hash"]:
        raise Rejected("olas_boundary_changed_during_collection")
    checkpoint = {"from_block": prior["from_block"] if prior else start, "through_block": stop,
        "block_hash": blocks[stop]["hash"], "through_block_timestamp": blocks[stop]["timestamp"],
        "observed_finalized_head": head["number"], "finalized_head_timestamp": head["timestamp"],
        "backlog_blocks": head["number"]-stop, "contract_code_sha256": code_hash,
        "history_before_start": "not_collected", "scope": "reviewed marketplace event topics only",
        "count_unit": "event_log", "unique_jobs": None, "independent_outcomes": False}
    proof = {"from_block": start, "through_block": stop, "finalized_head": head,
        "contract": MARKETPLACE, "contract_code_sha256": code_hash,
        "abi_source_revision": REVISION, "abi_sha256": digest(ABI), "logs": logs, "blocks": blocks,
        "payload_content_fetched": False, "rpc_documentation": "https://docs.gnosischain.com/tools/RPC%20Providers/"}
    store.capture(SOURCE, GNOSIS, proof, events=events, checkpoint=checkpoint)
    return {"event_logs": len(events), "from_block": start, "through_block": stop,
        "backlog_blocks": head["number"]-stop, "unique_jobs": None}
