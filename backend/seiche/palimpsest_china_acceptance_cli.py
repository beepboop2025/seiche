"""Offline claim/receipt utility for Palimpsest China economic acceptance.

The command never handles a private key.  ``claim`` emits the exact bytes an
offline Ed25519 signer covers; ``receipt`` verifies the returned signature
against Seiche's release-pinned trust policy before emitting a sidecar.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from seiche.palimpsest_china_intake import (
    PalimpsestChinaIntakeError,
    build_acceptance_claim_from_files,
    build_acceptance_receipt_from_files,
    encode_acceptance_claim,
    load_accepted_export,
)


def _timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        raise argparse.ArgumentTypeError("timestamp must be canonical UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise argparse.ArgumentTypeError("timestamp must be canonically encoded")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)

    def export_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("manifest", type=Path)
        command.add_argument("artifact", type=Path)
        command.add_argument("--accepted-at", type=_timestamp, required=True)
        command.add_argument("--signer-key-id", required=True)

    claim = commands.add_parser("claim", help="emit exact bytes for offline signing")
    export_arguments(claim)

    receipt = commands.add_parser(
        "receipt", help="verify a detached signature and emit its receipt"
    )
    export_arguments(receipt)
    receipt.add_argument("--signature", required=True)
    receipt.add_argument("--attest-dir", type=Path)

    verify = commands.add_parser("verify", help="verify an installed accepted export")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("artifact", type=Path)
    verify.add_argument("acceptance", type=Path)
    verify.add_argument("--attest-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "claim":
            claim = build_acceptance_claim_from_files(
                args.manifest,
                args.artifact,
                accepted_at=args.accepted_at,
                signer_key_id=args.signer_key_id,
            )
            sys.stdout.buffer.write(encode_acceptance_claim(claim))
            return 0
        if args.action == "receipt":
            receipt = build_acceptance_receipt_from_files(
                args.manifest,
                args.artifact,
                accepted_at=args.accepted_at,
                signer_key_id=args.signer_key_id,
                signature=args.signature,
                attest_dir=args.attest_dir,
            )
            sys.stdout.buffer.write(receipt)
            return 0

        context = load_accepted_export(
            args.manifest,
            args.artifact,
            args.acceptance,
            attest_dir=args.attest_dir,
        )
        payload = context.to_dict()
        print(
            json.dumps(
                {
                    "schema": payload["schema"],
                    "status": "accepted",
                    "clocks": payload["clocks"],
                    "rights": payload["rights"],
                    "provenance": payload["provenance"],
                    "observation_count": payload["observation_count"],
                    "current_series_count": payload["current_series_count"],
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, PalimpsestChinaIntakeError) as exc:
        print(
            json.dumps(
                {
                    "schema": "seiche.palimpsest-china-acceptance-error.v1",
                    "status": "rejected",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
