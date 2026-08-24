"""Offline claim/receipt utility for Palimpsest China economic acceptance.

The command never handles a private key.  ``claim`` emits the exact bytes an
offline Ed25519 signer covers; ``receipt`` verifies the returned signature
against Seiche's release-pinned trust policy before emitting a sidecar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from seiche.palimpsest_china_intake import (
    PalimpsestChinaIntakeError,
    MAX_PRODUCER_MAIN_EVIDENCE_BYTES,
    build_acceptance_claim_from_files,
    build_acceptance_receipt_from_files,
    encode_acceptance_claim,
    load_accepted_export,
)

_PRODUCER_MAIN_URL = (
    "https://api.github.com/repos/beepboop2025/palimpsest/branches/main"
)
_GITHUB_API_VERSION = "2022-11-28"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _fetch_producer_main_evidence() -> tuple[bytes, datetime]:
    """Fetch ``branches/main`` and timestamp only after its body is complete."""

    token = os.getenv("GH_TOKEN", "").strip() or os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise PalimpsestChinaIntakeError(
            "claim preparation requires GH_TOKEN or GITHUB_TOKEN"
        )
    request = Request(
        _PRODUCER_MAIN_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "seiche-palimpsest-china-acceptance",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed HTTPS URL
            status = getattr(response, "status", None)
            raw = response.read(MAX_PRODUCER_MAIN_EVIDENCE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise PalimpsestChinaIntakeError(
            "could not fetch authenticated Palimpsest main-branch evidence"
        ) from exc
    if status != 200 or not raw or len(raw) > MAX_PRODUCER_MAIN_EVIDENCE_BYTES:
        raise PalimpsestChinaIntakeError(
            "Palimpsest main-branch evidence response is invalid or too large"
        )
    return raw, _utc_now()


def _write_new_evidence(path: Path, raw: bytes) -> None:
    """Durably create, but never overwrite, the exact fetched response bytes."""

    parent = path.parent
    if path.name in {"", ".", ".."}:
        raise PalimpsestChinaIntakeError(
            "producer main evidence output path is invalid"
        )
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


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

    def export_arguments(
        command: argparse.ArgumentParser, *, accepted_at_required: bool
    ) -> None:
        command.add_argument("manifest", type=Path)
        command.add_argument("artifact", type=Path)
        command.add_argument("--input-ledger", type=Path, required=True)
        command.add_argument("--availability-receipt", type=Path, required=True)
        command.add_argument("--producer-commit-evidence", type=Path, required=True)
        command.add_argument(
            "--producer-main-evidence",
            type=Path,
            required=True,
            help=(
                "claim: new output path for an authenticated branches/main response; "
                "receipt: exact previously captured path"
            ),
        )
        command.add_argument("--handoff-receipt", type=Path, required=True)
        command.add_argument("--checksum-subject", type=Path, required=True)
        command.add_argument("--lineage-chain", type=Path, required=True)
        command.add_argument("--lineage-evidence", type=Path, required=True)
        if accepted_at_required:
            command.add_argument("--accepted-at", type=_timestamp, required=True)
        command.add_argument("--signer-key-id", required=True)
        command.add_argument(
            "--confirm-github-run-attestation-verified",
            action="store_true",
            required=True,
            help=(
                "confirm independent verification of the completed exact-SHA "
                "GitHub run and its bundle attestation"
            ),
        )
        command.add_argument(
            "--confirm-exact-input-hashes-verified",
            action="store_true",
            required=True,
            help=(
                "confirm independent verification of the manifest, artifact, "
                "input ledger, availability, producer-commit, policy, and "
                "series-registry hashes"
            ),
        )
        command.add_argument(
            "--confirm-producer-raw-identity-verified",
            action="store_true",
            required=True,
        )
        command.add_argument(
            "--confirm-detached-first-parent-lineage-rebuild-verified",
            action="store_true",
            required=True,
        )
        command.add_argument(
            "--confirm-current-main-branch-evidence-verified",
            action="store_true",
            required=True,
        )
        command.add_argument(
            "--confirm-rights-freshness-reviewed",
            action="store_true",
            required=True,
        )

    claim = commands.add_parser("claim", help="emit exact bytes for offline signing")
    export_arguments(claim, accepted_at_required=False)

    receipt = commands.add_parser(
        "receipt", help="verify a detached signature and emit its receipt"
    )
    export_arguments(receipt, accepted_at_required=True)
    receipt.add_argument("--signature", required=True)
    receipt.add_argument("--attest-dir", type=Path)

    verify = commands.add_parser("verify", help="verify an installed accepted export")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("artifact", type=Path)
    verify.add_argument("acceptance", type=Path)
    verify.add_argument("--input-ledger", type=Path, required=True)
    verify.add_argument("--availability-receipt", type=Path, required=True)
    verify.add_argument("--producer-commit-evidence", type=Path, required=True)
    verify.add_argument("--producer-main-evidence", type=Path, required=True)
    verify.add_argument("--handoff-receipt", type=Path, required=True)
    verify.add_argument("--checksum-subject", type=Path, required=True)
    verify.add_argument("--lineage-chain", type=Path, required=True)
    verify.add_argument("--lineage-evidence", type=Path, required=True)
    verify.add_argument("--attest-dir", type=Path)
    return parser


def _operator_confirmations(args: argparse.Namespace) -> dict[str, bool]:
    return {
        "github_attestation_verified": (args.confirm_github_run_attestation_verified),
        "exact_checksum_subject_set_verified": (
            args.confirm_exact_input_hashes_verified
        ),
        "producer_raw_identity_verified": (args.confirm_producer_raw_identity_verified),
        "detached_first_parent_lineage_rebuild_verified": (
            args.confirm_detached_first_parent_lineage_rebuild_verified
        ),
        "current_main_branch_evidence_verified": (
            args.confirm_current_main_branch_evidence_verified
        ),
        "rights_and_freshness_reviewed": args.confirm_rights_freshness_reviewed,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "claim":
            main_evidence, accepted_at = _fetch_producer_main_evidence()
            _write_new_evidence(args.producer_main_evidence, main_evidence)
            claim = build_acceptance_claim_from_files(
                args.manifest,
                args.artifact,
                input_ledger_path=args.input_ledger,
                availability_path=args.availability_receipt,
                producer_commit_evidence_path=args.producer_commit_evidence,
                producer_main_evidence_path=args.producer_main_evidence,
                handoff_path=args.handoff_receipt,
                checksums_path=args.checksum_subject,
                lineage_chain_path=args.lineage_chain,
                lineage_evidence_path=args.lineage_evidence,
                operator_confirmations=_operator_confirmations(args),
                accepted_at=accepted_at,
                signer_key_id=args.signer_key_id,
            )
            sys.stdout.buffer.write(encode_acceptance_claim(claim))
            return 0
        if args.action == "receipt":
            receipt = build_acceptance_receipt_from_files(
                args.manifest,
                args.artifact,
                input_ledger_path=args.input_ledger,
                availability_path=args.availability_receipt,
                producer_commit_evidence_path=args.producer_commit_evidence,
                producer_main_evidence_path=args.producer_main_evidence,
                handoff_path=args.handoff_receipt,
                checksums_path=args.checksum_subject,
                lineage_chain_path=args.lineage_chain,
                lineage_evidence_path=args.lineage_evidence,
                operator_confirmations=_operator_confirmations(args),
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
            input_ledger_path=args.input_ledger,
            availability_path=args.availability_receipt,
            producer_commit_evidence_path=args.producer_commit_evidence,
            producer_main_evidence_path=args.producer_main_evidence,
            handoff_path=args.handoff_receipt,
            checksums_path=args.checksum_subject,
            lineage_chain_path=args.lineage_chain,
            lineage_evidence_path=args.lineage_evidence,
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
