"""One source of truth for how catalog copy describes Seiche access."""

from typing import Literal

AccessMode = Literal["subscriber", "invite_or_grant", "operator_cost"]

PUBLIC_COMMITMENT = (
    "Twelve public evidence tools remain anonymous and free: no account, "
    "email address, or payment is required."
)

_GATED_STATEMENTS: dict[AccessMode, str] = {
    "subscriber": (
        "A subscriber bearer token unlocks the five compute-heavy tools; the "
        "twelve public evidence tools remain anonymous and free."
    ),
    "invite_or_grant": (
        "An operator-issued invite or grant unlocks the five compute-heavy tools; "
        "the twelve public evidence tools remain anonymous and free."
    ),
    "operator_cost": (
        "A provisioned bearer token or an operator-enabled posted per-call rail "
        "unlocks the five compute-heavy tools; the twelve public evidence tools "
        "remain anonymous and free."
    ),
}


def gated_access_statement(mode: AccessMode) -> str:
    """Describe the five compute-heavy tools without weakening the public promise."""

    try:
        return _GATED_STATEMENTS[mode]
    except KeyError as exc:
        raise ValueError(f"unsupported listing access mode: {mode!r}") from exc
