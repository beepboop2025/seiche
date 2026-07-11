"""x402 — machine-payable MCP tool calls (USDC per call, no account).

The hosted /mcp endpoint already has two ways in: the anonymous free surface
and subscriber bearer tokens. This adds a third for AI agents with wallets:
pay per tools/call over the x402 protocol (HTTP 402 + a signed stablecoin
transfer authorization, verified and settled through a facilitator). The
subscriber tools become callable one call at a time, no signup.

OFF BY DEFAULT and fail-closed at every step: the feature only exists when
SEICHE_X402_PAY_TO is set, and any decode/verify/settle failure returns 402 —
a tool result is never served on an unverified or unsettled payment.

Env (operator dials):
  SEICHE_X402_PAY_TO       receiving address (required; empty = feature off)
  SEICHE_X402_NETWORK      default "base"
  SEICHE_X402_FACILITATOR  default "https://x402.org/facilitator"
  SEICHE_X402_ASSET        default USDC on Base
Prices per tool live in config.X402_PRICES_USD (public tools stay free).

Implements x402 v1, "exact" scheme. The facilitator API surface is two POSTs
(/verify and /settle); the URL is a dial so operators can switch facilitators
without a release.
"""

from __future__ import annotations

import base64
import binascii
import json
import os

import httpx

from seiche.config import X402_PRICES_USD

X402_VERSION = 1
_ASSET_DECIMALS = 6          # USDC
_TIMEOUT_S = 15
_MAX_PAYMENT_HEADER_B = 8192

# USDC on Base mainnet — the default asset agents actually hold.
_DEFAULT_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def enabled() -> bool:
    return bool(_env("SEICHE_X402_PAY_TO"))


def price_usd(tool: str | None) -> float | None:
    """Price for a tool, or None if the tool is not payable (free or unknown)."""
    if not tool:
        return None
    return X402_PRICES_USD.get(tool)


def _atomic(usd: float) -> str:
    return str(int(round(usd * 10 ** _ASSET_DECIMALS)))


def requirements(tool: str, resource: str) -> dict:
    """PaymentRequirements for one tool call (x402 v1, exact scheme)."""
    usd = price_usd(tool)
    if usd is None:
        raise ValueError(f"tool {tool!r} has no x402 price")
    return {
        "scheme": "exact",
        "network": _env("SEICHE_X402_NETWORK", "base"),
        "maxAmountRequired": _atomic(usd),
        "resource": resource,
        "description": f"Seiche MCP tools/call: {tool}",
        "mimeType": "application/json",
        "payTo": _env("SEICHE_X402_PAY_TO"),
        "maxTimeoutSeconds": 60,
        "asset": _env("SEICHE_X402_ASSET", _DEFAULT_ASSET),
        "extra": {
            "name": _env("SEICHE_X402_ASSET_NAME", "USD Coin"),
            "version": _env("SEICHE_X402_ASSET_VERSION", "2"),
        },
    }


def payment_required(tool: str, resource: str, error: str) -> dict:
    """The HTTP-402 response body an x402-capable client acts on."""
    return {
        "x402Version": X402_VERSION,
        "error": error,
        "accepts": [requirements(tool, resource)],
    }


def decode_payment(header: str | None) -> dict | None:
    """X-PAYMENT header -> payload dict, or None on any malformation."""
    if not header or len(header) > _MAX_PAYMENT_HEADER_B:
        return None
    try:
        payload = json.loads(base64.b64decode(header, validate=True))
    except (ValueError, binascii.Error):
        return None
    return payload if isinstance(payload, dict) else None


def _facilitator_post(path: str, body: dict) -> dict:
    url = _env("SEICHE_X402_FACILITATOR", "https://x402.org/facilitator").rstrip("/")
    r = httpx.post(f"{url}{path}", json=body, timeout=_TIMEOUT_S)
    r.raise_for_status()
    out = r.json()
    if not isinstance(out, dict):
        raise ValueError("facilitator returned a non-object body")
    return out


def verify(payment: dict, reqs: dict) -> tuple[bool, str]:
    """Ask the facilitator whether the signed payment satisfies `reqs`."""
    try:
        out = _facilitator_post("/verify", {
            "x402Version": X402_VERSION,
            "paymentPayload": payment,
            "paymentRequirements": reqs,
        })
    except Exception as e:  # network, HTTP, JSON — all fail closed
        return False, f"facilitator verify unavailable: {type(e).__name__}"
    if out.get("isValid") is True:
        return True, ""
    return False, str(out.get("invalidReason") or "payment invalid")


def settle(payment: dict, reqs: dict) -> tuple[bool, dict]:
    """Settle on-chain via the facilitator. Fail-closed: no settle, no tool."""
    try:
        out = _facilitator_post("/settle", {
            "x402Version": X402_VERSION,
            "paymentPayload": payment,
            "paymentRequirements": reqs,
        })
    except Exception as e:
        return False, {"success": False, "errorReason": f"facilitator settle unavailable: {type(e).__name__}"}
    if out.get("success") is True:
        return True, out
    return False, out


def settle_header(receipt: dict) -> str:
    """X-PAYMENT-RESPONSE header value (base64 JSON, per spec)."""
    return base64.b64encode(json.dumps(receipt).encode()).decode()


def annotate_tools_list(resp: dict) -> dict:
    """On the anonymous surface with x402 on, advertise the payable tools.

    The public tools/list normally hides subscriber tools entirely; an agent
    with a wallet needs to know they exist and what a call costs. Adds one
    catalogue entry per priced tool (name + price note only — schemas come
    from the full surface once the call is paid)."""
    from seiche import mcp_server

    result = resp.get("result")
    if not isinstance(result, dict) or "tools" not in result:
        return resp
    listed = {t.get("name") for t in result["tools"]}
    for name, usd in X402_PRICES_USD.items():
        spec = mcp_server.TOOLS.get(name)
        if spec is None or name in listed:
            continue
        title, desc, schema = spec[0], spec[1], spec[2]
        result["tools"].append({
            "name": name,
            "title": title,
            "description": f"{desc} [paid tool: ${usd:.2f} per call via x402 — "
                           f"retry the call with an X-PAYMENT header]",
            "inputSchema": schema,
        })
    return resp
