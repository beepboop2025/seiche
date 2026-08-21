"""Credential-safe fault storage and public projections.

Exception text is operationally useful inside a private log sink, but it is
not a safe persistence or API contract.  Upstream libraries commonly include
request URLs, response bodies, credentials, and chained exceptions in
``str(exc)``.  This module classifies failures from type/status metadata only
and emits a small, stable vocabulary whose messages never reuse caller text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, TypedDict


class PublicFaultCategory(StrEnum):
    ACCESS_POLICY = "ACCESS_POLICY"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    TIMEOUT = "TIMEOUT"
    HTTP_ERROR = "HTTP_ERROR"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    WORKER_HEALTH = "WORKER_HEALTH"
    SOURCE_ERROR = "SOURCE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_PUBLIC_DETAILS = {
    PublicFaultCategory.ACCESS_POLICY: "source access policy is unavailable",
    PublicFaultCategory.CIRCUIT_OPEN: "source circuit breaker is open",
    PublicFaultCategory.TIMEOUT: "source collection timed out",
    PublicFaultCategory.HTTP_ERROR: "official source returned an HTTP error",
    PublicFaultCategory.TRANSPORT_ERROR: "official source connection failed",
    PublicFaultCategory.PERSISTENCE_ERROR: "collector persistence failed",
    PublicFaultCategory.VALIDATION_ERROR: "source response failed validation",
    PublicFaultCategory.WORKER_HEALTH: "collector worker health is degraded",
    PublicFaultCategory.SOURCE_ERROR: "official source collection failed",
    PublicFaultCategory.INTERNAL_ERROR: "collector failed",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MARKET_ID_RE = re.compile(r"^[A-Z0-9]+-[A-Z]{3}$|^GLOBAL$")
_STATUS_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_LOWER_ENUM_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_CADENCE_RE = re.compile(r"^P(?:T\d+[HMS]|\d+[DW])$")
_EXCEPTION_PREFIX_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Fault|Timeout))\s*:",
    re.IGNORECASE,
)
_PRIVATE_DIAGNOSTIC_RE = re.compile(
    r"(?:"
    r"(?:https?|postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?|file)://|"
    r"(?:^|[\s\"'])/(?:Users|home|root|private|etc|var|tmp|opt|srv|proc)/|"
    r"[A-Za-z]:\\|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|"
    r"authorization)\s*[:=]|"
    r"bearer\s+|"
    r"<\s*/?\s*(?:script|html|body|pre|code)\b|"
    r"traceback\s+\(most\s+recent\s+call\s+last\)"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


class PublicFailureEnvelope(TypedDict):
    """Stable machine-readable failure shape safe for durable/public use."""

    ok: Literal[False]
    status: str
    category: str
    reason: str


def _type_names(value: BaseException | str | None) -> tuple[str, ...]:
    if isinstance(value, BaseException):
        return tuple(cls.__name__.casefold() for cls in type(value).__mro__)
    if isinstance(value, str):
        # Only a bounded, identifier-shaped prefix is used for legacy rows.
        # The remainder may be attacker-controlled exception text and is never
        # inspected or copied into the sanitized result.
        prefix = value.partition(":")[0].strip()
        if len(prefix) <= 64 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", prefix):
            return (prefix.casefold(),)
    return ()


def fault_category(
    value: BaseException | str | None,
    *,
    status: object = None,
) -> PublicFaultCategory:
    """Classify a fault without formatting it or following exception chains."""

    status_name = status.strip().upper() if isinstance(status, str) else ""
    if status_name == "CIRCUIT_OPEN":
        return PublicFaultCategory.CIRCUIT_OPEN
    if status_name in {"OVERDUE", "MISSING", "UNKNOWN"}:
        return PublicFaultCategory.WORKER_HEALTH

    names = _type_names(value)
    if names and names[0].upper() in PublicFaultCategory._value2member_map_:
        return PublicFaultCategory(names[0].upper())
    joined = " ".join(names)
    if "accesspolicy" in joined or "sourcepolicyunavailable" in joined:
        return PublicFaultCategory.ACCESS_POLICY
    if "deadline" in joined or "timeout" in joined:
        return PublicFaultCategory.TIMEOUT
    if "httpstatus" in joined or "httperror" in joined:
        return PublicFaultCategory.HTTP_ERROR
    if any(token in joined for token in ("connect", "transport", "network", "oserror")):
        return PublicFaultCategory.TRANSPORT_ERROR
    if "persistence" in joined:
        return PublicFaultCategory.PERSISTENCE_ERROR
    if any(
        token in joined
        for token in (
            "validation",
            "decode",
            "parse",
            "valueerror",
            "typeerror",
            "keyerror",
        )
    ):
        return PublicFaultCategory.VALIDATION_ERROR
    if any(token in joined for token in ("sourceerror", "sourcefault")):
        return PublicFaultCategory.SOURCE_ERROR
    if status_name == "UNAVAILABLE":
        return PublicFaultCategory.ACCESS_POLICY
    return PublicFaultCategory.INTERNAL_ERROR


def public_fault_detail(category: PublicFaultCategory | str) -> str:
    """Return the fixed public sentence for a category."""

    try:
        resolved = PublicFaultCategory(str(category).upper())
    except ValueError:
        resolved = PublicFaultCategory.INTERNAL_ERROR
    return _PUBLIC_DETAILS[resolved]


def sanitize_fault(
    value: BaseException | str | None,
    *,
    status: object = None,
) -> str | None:
    """Return a stable persistence-safe fault string, or ``None`` on success."""

    status_name = status.strip().upper() if isinstance(status, str) else ""
    if value is None and status_name not in {
        "FAILED",
        "UNAVAILABLE",
        "CIRCUIT_OPEN",
        "OVERDUE",
        "MISSING",
        "UNKNOWN",
    }:
        return None
    category = fault_category(value, status=status_name)
    return f"{category.value}: {_PUBLIC_DETAILS[category]}"


def safe_failure_envelope(
    value: BaseException | str | None,
    *,
    status: object = "FAILED",
) -> PublicFailureEnvelope:
    """Build a typed failure without ever copying exception diagnostics.

    Callers use this at exception boundaries *before* caching or persistence.
    ``value`` contributes only its type/category; its message, chained causes,
    response body, URL, and local path are deliberately ignored.
    """

    safe_status = _safe_status(status)
    category = fault_category(value, status=safe_status)
    return {
        "ok": False,
        "status": safe_status,
        "category": category.value,
        "reason": _PUBLIC_DETAILS[category],
    }


def _unsafe_diagnostic_category(
    value: Any,
    *,
    status: object = "FAILED",
) -> PublicFaultCategory | None:
    """Classify legacy diagnostic text that is unsafe on a public boundary.

    Static product explanations remain intact.  Exception-shaped strings and
    common credential/URL/path/body canaries are considered untrusted.  This
    is a containment layer for old persisted payloads; new exception paths
    should use :func:`safe_failure_envelope` instead.
    """

    if isinstance(value, BaseException):
        return fault_category(value, status=status)
    if value is None:
        return None
    if not isinstance(value, str):
        return PublicFaultCategory.INTERNAL_ERROR
    if (
        len(value) > 1024
        or any(ord(character) < 32 and character not in "\t" for character in value)
        or _EXCEPTION_PREFIX_RE.search(value)
        or _PRIVATE_DIAGNOSTIC_RE.search(value)
        or (
            value.lstrip().startswith(("{", "["))
            and any(
                marker in value.casefold()
                for marker in ("error", "detail", "token", "password", "secret")
            )
        )
    ):
        return fault_category(value, status=status)
    return None


def _safe_identifier(value: object, fallback: str) -> str:
    candidate = value if isinstance(value, str) else ""
    candidate = candidate.strip()
    return candidate if _IDENTIFIER_RE.fullmatch(candidate) else fallback


def _safe_market_id(value: object, fallback: str | None) -> str | None:
    candidate = value if isinstance(value, str) else ""
    candidate = candidate.strip().upper()
    if _MARKET_ID_RE.fullmatch(candidate):
        return candidate
    if fallback is None:
        return None
    normalized = fallback.strip().upper()
    return normalized if _MARKET_ID_RE.fullmatch(normalized) else None


def _safe_status(value: object) -> str:
    candidate = value if isinstance(value, str) else ""
    candidate = candidate.strip().upper()
    return candidate if _STATUS_RE.fullmatch(candidate) else "FAILED"


def _safe_timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC).isoformat()


def sanitize_fault_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Copy an internal fault/run record while replacing unsafe fault text.

    This preserves collector-run fields needed by materializers and the atlas.
    Use :func:`project_public_fault` for the narrower public wire shape.
    """

    output = dict(record)
    status = output.get("status", output.get("last_run_status"))
    raw_fault = output.get("fault")
    raw_detail = output.get("detail")
    raw_reason = output.get("reason")
    raw = (
        raw_fault
        if raw_fault is not None
        else raw_detail
        if raw_detail is not None
        else raw_reason
    )
    source_value = output.get("source", output.get("adapter_id"))
    if "source" in output:
        output["source"] = _safe_identifier(source_value, "unknown_source")
    if "adapter_id" in output:
        output["adapter_id"] = _safe_identifier(source_value, "unknown_source")
    if "market_id" in output:
        output["market_id"] = _safe_market_id(output["market_id"], None) or "GLOBAL"
    status_name = _safe_status(status)
    is_failure = raw is not None or status_name in {
        "FAILED",
        "UNAVAILABLE",
        "CIRCUIT_OPEN",
        "OVERDUE",
        "MISSING",
        "UNKNOWN",
    }
    if is_failure:
        category = fault_category(raw, status=status)
        output["category"] = category.value
        output["status"] = status_name
    if "fault" in output:
        output["fault"] = sanitize_fault(raw_fault, status=status)
    if "detail" in output:
        output["detail"] = public_fault_detail(fault_category(raw, status=status))
    if "reason" in output:
        output["reason"] = public_fault_detail(fault_category(raw, status=status))
    return output


def project_public_fault(
    record: Mapping[str, Any] | BaseException | str,
    *,
    default_market_id: str | None = None,
    default_source: str = "unknown_source",
) -> dict[str, Any]:
    """Project one fault to an allowlisted, credential-safe public contract."""

    if isinstance(record, Mapping):
        status = _safe_status(record.get("status", record.get("last_run_status")))
        raw = record.get("fault", record.get("detail"))
        market_id = _safe_market_id(record.get("market_id"), default_market_id)
        source = _safe_identifier(
            record.get("source", record.get("adapter_id")),
            _safe_identifier(default_source, "unknown_source"),
        )
    else:
        status = "FAILED"
        raw = record
        market_id = _safe_market_id(None, default_market_id)
        source = _safe_identifier(default_source, "unknown_source")

    category = fault_category(raw, status=status)
    output: dict[str, Any] = {
        "source": source,
        "status": status,
        "category": category.value,
        "detail": _PUBLIC_DETAILS[category],
    }
    if market_id is not None:
        output["market_id"] = market_id
    if isinstance(record, Mapping):
        for key in ("finished_at", "next_due", "heartbeat_at", "expected_by"):
            timestamp = _safe_timestamp(record.get(key))
            if timestamp is not None:
                output[key] = timestamp
    return output


def project_public_faults(
    records: Iterable[Mapping[str, Any] | BaseException | str],
    *,
    default_market_id: str | None = None,
    default_source: str = "unknown_source",
) -> list[dict[str, Any]]:
    return [
        project_public_fault(
            record,
            default_market_id=default_market_id,
            default_source=default_source,
        )
        for record in records
    ]


def _sanitize_public_fault_context(record: Mapping[str, Any]) -> dict[str, Any]:
    """Retain useful adapter context while dropping arbitrary diagnostics."""

    projected = project_public_fault(record)
    output: dict[str, Any] = {
        "source": projected["source"],
        "status": projected["status"],
        "category": projected["category"],
        "detail": projected["detail"],
        "fault": sanitize_fault(
            record.get("fault"),
            status=record.get("status", record.get("last_run_status")),
        ),
    }
    if "adapter_id" in record:
        output["adapter_id"] = _safe_identifier(
            record.get("adapter_id"), "unknown_source"
        )
    if "market_id" in projected:
        output["market_id"] = projected["market_id"]
    if "last_run_status" in record:
        output["last_run_status"] = _safe_status(record.get("last_run_status"))
    for key in ("classification", "redistribution_status"):
        candidate = record.get(key)
        if isinstance(candidate, str) and _LOWER_ENUM_RE.fullmatch(candidate):
            output[key] = candidate
    cadence = record.get("expected_cadence")
    if isinstance(cadence, str) and _CADENCE_RE.fullmatch(cadence):
        output["expected_cadence"] = cadence
    for key in (
        "started_at",
        "finished_at",
        "last_finished_at",
        "next_due",
        "heartbeat_at",
        "expected_by",
        "circuit_open_until",
    ):
        timestamp = _safe_timestamp(record.get(key))
        if timestamp is not None:
            output[key] = timestamp
    for key in (
        "observations_written",
        "attempts",
        "consecutive_failures",
    ):
        candidate = record.get(key)
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and candidate >= 0
        ):
            output[key] = candidate
    return output


def sanitize_public_fault_payload(value: Any) -> Any:
    """Copy a payload and sanitize every conventional fault projection.

    Lists named ``faults`` or ``read_faults`` become the narrow public shape.
    Singular ``fault`` fields (for example atlas adapter rows) retain their
    surrounding record but receive a fixed persistence-safe value.
    """

    if isinstance(value, Mapping):
        if value.get("fault") is not None:
            return _sanitize_public_fault_context(value)
        unsafe_diagnostics = [
            (key, item, _unsafe_diagnostic_category(item, status=value.get("status")))
            for key, item in value.items()
            if key in {"reason", "detail"}
        ]
        unsafe_diagnostics = [
            (key, item, category)
            for key, item, category in unsafe_diagnostics
            if category is not None
        ]
        if unsafe_diagnostics:
            _, raw, category = unsafe_diagnostics[0]
            envelope = safe_failure_envelope(
                raw,
                status=value.get("status", value.get("last_run_status", "FAILED")),
            )
            # A marker-only legacy string (for example a credential URL with
            # no exception prefix) is conservatively INTERNAL_ERROR.  Preserve
            # a more specific detector category when one is available.
            envelope["category"] = category.value
            envelope["reason"] = _PUBLIC_DETAILS[category]
            output = {
                key: sanitize_public_fault_payload(item)
                for key, item in value.items()
                if key not in {"ok", "status", "category", "reason", "detail"}
            }
            output.update(envelope)
            if "detail" in value:
                output["detail"] = envelope["reason"]
            return output
        output: dict[Any, Any] = {}
        status = value.get("status", value.get("last_run_status"))
        for key, item in value.items():
            if key in {"faults", "read_faults"} and isinstance(item, (list, tuple)):
                output[key] = project_public_faults(item)
            elif key == "fault":
                output[key] = sanitize_fault(item, status=status)
            else:
                output[key] = sanitize_public_fault_payload(item)
        return output
    if isinstance(value, list):
        return [sanitize_public_fault_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_public_fault_payload(item) for item in value)
    return value
