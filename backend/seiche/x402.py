"""x402 v2 machine payments for individual hosted MCP analysis calls.

The public MCP surface remains free and subscriber bearer tokens remain the
only identity mechanism. x402 can pay for one of the five explicitly priced
analysis tools, but it cannot create an identity, discover Agent Room tools,
or grant room/trading authority.

Activation is deliberately stricter than setting a receiving wallet. An
operator must select one complete, validated profile:

``base-sepolia-testnet``
    Base Sepolia USDC through the public x402.org testnet facilitator.

``base-mainnet-authenticated``
    Reserved but rejected. Coinbase facilitator JWTs are short-lived and bound
    to the HTTP method, host, and path; Seiche does not yet carry the required
    per-request signer for both ``/verify`` and ``/settle``.

No x402 variable means the feature is dormant. A profile or pay-to value on
its own is treated as an attempted but invalid activation so the API can fail
the paid request explicitly before contacting a facilitator.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import os
import re
from urllib.parse import urlsplit

import httpx

from seiche.config import X402_PRICES_USD

X402_VERSION = 2
_ASSET_DECIMALS = 6  # USDC
_TIMEOUT_S = 15
_MAX_PAYMENT_HEADER_B = 8192
_TESTNET_PROFILE = "base-sepolia-testnet"
_MAINNET_PROFILE = "base-mainnet-authenticated"

_BASE_SEPOLIA_NETWORK = "eip155:84532"
_BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_X402_ORG_FACILITATOR = "https://x402.org/facilitator"
_EVM_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_EVM_TRANSACTION_RE = re.compile(r"0x[0-9a-fA-F]{64}")
_MAX_REASON_CHARS = 512


class X402ConfigurationError(ValueError):
    """The operator attempted an incomplete or inconsistent activation."""


@dataclass(frozen=True)
class FacilitatorProfile:
    name: str
    network: str
    asset: str
    pay_to: str
    facilitator: str
    authorization: str | None


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def activation_attempted() -> bool:
    """Return whether the operator touched either activation switch.

    Network/facilitator hints alone do not disturb a dormant deployment; a
    selected profile or receiving wallet does. In particular, the historical
    pay-to-only configuration is surfaced as invalid instead of silently
    activating with unsafe defaults.
    """

    return bool(_env("SEICHE_X402_PROFILE") or _env("SEICHE_X402_PAY_TO"))


def _canonical_facilitator(raw: str) -> str:
    if not raw:
        raise X402ConfigurationError("SEICHE_X402_FACILITATOR is required")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise X402ConfigurationError(
            "SEICHE_X402_FACILITATOR must be a valid HTTPS URL"
        ) from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise X402ConfigurationError(
            "SEICHE_X402_FACILITATOR must be an HTTPS origin/path without "
            "credentials, query, or fragment"
        )
    return raw.rstrip("/")


def _require_evm_address(name: str, value: str) -> str:
    if _EVM_ADDRESS_RE.fullmatch(value) is None:
        raise X402ConfigurationError(f"{name} must be a 20-byte EVM address")
    return value


def configuration() -> FacilitatorProfile:
    """Build the active profile or raise a non-secret configuration error."""

    profile = _env("SEICHE_X402_PROFILE")
    if not profile:
        raise X402ConfigurationError(
            "SEICHE_X402_PROFILE is required; a pay-to address alone is not "
            "an activation profile"
        )

    pay_to = _require_evm_address("SEICHE_X402_PAY_TO", _env("SEICHE_X402_PAY_TO"))
    authorization = _env("SEICHE_X402_FACILITATOR_AUTHORIZATION")

    if profile == _TESTNET_PROFILE:
        network = _env("SEICHE_X402_NETWORK", _BASE_SEPOLIA_NETWORK)
        asset = _require_evm_address(
            "SEICHE_X402_ASSET",
            _env("SEICHE_X402_ASSET", _BASE_SEPOLIA_USDC),
        )
        facilitator = _canonical_facilitator(
            _env("SEICHE_X402_FACILITATOR", _X402_ORG_FACILITATOR)
        )
        if network != _BASE_SEPOLIA_NETWORK:
            raise X402ConfigurationError(
                f"{_TESTNET_PROFILE} requires network {_BASE_SEPOLIA_NETWORK}"
            )
        if asset.lower() != _BASE_SEPOLIA_USDC.lower():
            raise X402ConfigurationError(
                f"{_TESTNET_PROFILE} requires the Base Sepolia USDC asset"
            )
        if facilitator != _X402_ORG_FACILITATOR:
            raise X402ConfigurationError(
                f"{_TESTNET_PROFILE} requires {_X402_ORG_FACILITATOR}"
            )
        if authorization:
            raise X402ConfigurationError(
                f"{_TESTNET_PROFILE} must not carry production facilitator "
                "authorization"
            )
        return FacilitatorProfile(
            name=profile,
            network=network,
            asset=asset,
            pay_to=pay_to,
            facilitator=facilitator,
            authorization=None,
        )

    if profile == _MAINNET_PROFILE:
        raise X402ConfigurationError(
            f"{_MAINNET_PROFILE} is dormant until Seiche implements "
            "per-request path-bound facilitator JWT signing"
        )

    raise X402ConfigurationError(
        "SEICHE_X402_PROFILE must be base-sepolia-testnet or "
        "base-mainnet-authenticated"
    )


def configuration_error() -> str | None:
    if not activation_attempted():
        return None
    try:
        configuration()
    except X402ConfigurationError as exc:
        return str(exc)
    return None


def enabled() -> bool:
    return activation_attempted() and configuration_error() is None


def price_usd(tool: str | None) -> float | None:
    """Price for one analysis tool, never for an Agent Room operation."""

    if not tool or tool.startswith("agent_room_"):
        return None
    return X402_PRICES_USD.get(tool)


def _atomic(usd: float) -> str:
    return str(int(round(usd * 10**_ASSET_DECIMALS)))


def resource_info(tool: str, resource: str) -> dict:
    return {
        "url": resource,
        "description": f"Seiche MCP tools/call: {tool}",
        "mimeType": "application/json",
    }


def requirements(tool: str, resource: str) -> dict:
    """Return one x402 v2 PaymentRequirements object."""

    del resource  # resource metadata is top-level in PaymentRequired v2
    usd = price_usd(tool)
    if usd is None:
        raise ValueError(f"tool {tool!r} has no x402 price")
    profile = configuration()
    return {
        "scheme": "exact",
        "network": profile.network,
        "amount": _atomic(usd),
        "asset": profile.asset,
        "payTo": profile.pay_to,
        "maxTimeoutSeconds": 60,
        "extra": {
            "name": "USDC",
            "version": "2",
        },
    }


def payment_required(tool: str, resource: str, error: str) -> dict:
    """Return the x402 v2 body mirrored in ``PAYMENT-REQUIRED``."""

    return {
        "x402Version": X402_VERSION,
        "error": error,
        "resource": resource_info(tool, resource),
        "accepts": [requirements(tool, resource)],
        "extensions": {},
    }


def _encode_header(payload: dict) -> str:
    wire = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.b64encode(wire).decode("ascii")


def payment_required_header(challenge: dict) -> str:
    """Encode one PaymentRequired document for the v2 response header."""

    return _encode_header(challenge)


def decode_payment(header: str | None) -> dict | None:
    """Decode and structurally check a v2 ``PAYMENT-SIGNATURE`` header."""

    if not header or len(header) > _MAX_PAYMENT_HEADER_B:
        return None
    try:
        payload = json.loads(base64.b64decode(header, validate=True))
    except (UnicodeDecodeError, ValueError, binascii.Error):
        return None
    if not isinstance(payload, dict) or payload.get("x402Version") != X402_VERSION:
        return None
    if not isinstance(payload.get("accepted"), dict):
        return None
    if not isinstance(payload.get("payload"), dict):
        return None
    if "resource" in payload and not isinstance(payload["resource"], dict):
        return None
    if "extensions" in payload and not isinstance(payload["extensions"], dict):
        return None
    return payload


def _payment_matches(payment: dict, reqs: dict) -> bool:
    """Require the client to echo exactly the option Seiche advertised."""

    return (
        payment.get("x402Version") == X402_VERSION and payment.get("accepted") == reqs
    )


def payment_resource_matches(payment: dict, tool: str, resource: str) -> bool:
    """If a v2 client echoes optional resource metadata, require exact parity."""

    return "resource" not in payment or payment.get("resource") == resource_info(
        tool, resource
    )


def _safe_reason(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    return value.strip()[:_MAX_REASON_CHARS]


def _valid_settlement(out: dict, reqs: dict) -> bool:
    transaction = out.get("transaction")
    payer = out.get("payer")
    return (
        out.get("success") is True
        and isinstance(transaction, str)
        and _EVM_TRANSACTION_RE.fullmatch(transaction) is not None
        and out.get("network") == reqs.get("network")
        and (
            payer is None
            or (isinstance(payer, str) and _EVM_ADDRESS_RE.fullmatch(payer) is not None)
        )
        and ("amount" not in out or out.get("amount") == reqs.get("amount"))
        and ("extensions" not in out or isinstance(out.get("extensions"), dict))
    )


def _facilitator_post(path: str, body: dict) -> dict:
    profile = configuration()
    headers = (
        {"Authorization": profile.authorization}
        if profile.authorization is not None
        else None
    )
    response = httpx.post(
        f"{profile.facilitator}{path}",
        json=body,
        headers=headers,
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    out = response.json()
    if not isinstance(out, dict):
        raise ValueError("facilitator returned a non-object body")
    return out


def verify(payment: dict, reqs: dict) -> tuple[bool, str]:
    """Ask the facilitator whether this exact signed option is valid."""

    if not _payment_matches(payment, reqs):
        return False, "payment does not match the advertised requirements"
    try:
        out = _facilitator_post(
            "/verify",
            {
                "x402Version": X402_VERSION,
                "paymentPayload": payment,
                "paymentRequirements": reqs,
            },
        )
    except Exception as exc:  # network, HTTP, JSON — all fail closed
        return False, f"facilitator verify unavailable: {type(exc).__name__}"
    if out.get("isValid") is True:
        return True, ""
    return False, _safe_reason(out.get("invalidReason"), "payment invalid")


def settle(payment: dict, reqs: dict) -> tuple[bool, dict]:
    """Settle through the facilitator; no confirmed settle means no tool."""

    if not _payment_matches(payment, reqs):
        return False, {
            "success": False,
            "errorReason": "payment does not match the advertised requirements",
        }
    try:
        out = _facilitator_post(
            "/settle",
            {
                "x402Version": X402_VERSION,
                "paymentPayload": payment,
                "paymentRequirements": reqs,
            },
        )
    except Exception as exc:
        return False, {
            "success": False,
            "errorReason": (f"facilitator settle unavailable: {type(exc).__name__}"),
        }
    if _valid_settlement(out, reqs):
        return True, out
    return False, {
        "success": False,
        "errorReason": _safe_reason(
            out.get("errorReason"), "facilitator settlement response invalid"
        ),
        "transaction": "",
        "network": reqs.get("network", ""),
    }


def settle_header(receipt: dict) -> str:
    """Encode a SettlementResponse for the v2 ``PAYMENT-RESPONSE`` header."""

    return _encode_header(receipt)


def annotate_tools_list(resp: dict) -> dict:
    """Advertise payable analysis tools on a valid, active wallet surface."""

    from seiche import mcp_server

    result = resp.get("result")
    if not isinstance(result, dict) or "tools" not in result:
        return resp
    listed = {tool.get("name") for tool in result["tools"]}
    for name, usd in X402_PRICES_USD.items():
        spec = mcp_server.TOOLS.get(name)
        if spec is None or name in listed or name.startswith("agent_room_"):
            continue
        title, description, schema = spec[0], spec[1], spec[2]
        result["tools"].append(
            {
                "name": name,
                "title": title,
                "description": (
                    f"{description} [paid tool: ${usd:.2f} per call via x402 v2 "
                    "— retry with a PAYMENT-SIGNATURE header]"
                ),
                "inputSchema": schema,
            }
        )
    return resp
