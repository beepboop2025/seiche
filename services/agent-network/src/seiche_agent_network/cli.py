"""Local operator provisioning and isolated service entrypoint.

Tokens are written to newly created mode-0600 files, never printed. Registration
is intentionally not available to anonymous HTTP clients.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys

from .crypto import generate_keys
from .models import NetworkError
from .store import Store


def read_private_json(path):
    """Refuse symlink/world-readable credentials rather than trusting a filename."""
    fd = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Credential files must be regular files readable only by their owner (0600)")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ValueError("Credential file must belong to the current operator")
        if info.st_size > 16384:
            raise ValueError("Credential file is too large")
        value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("Credential file must contain a JSON object")
        return value


@contextmanager
def private_output(path):
    """Reserve output before mutating credentials; never overwrite or follow links."""
    fd = os.open(os.fspath(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # Preserve any successfully written bytes to aid credential recovery.
        if Path(path).exists() and Path(path).stat().st_size == 0:
            Path(path).unlink()
        raise


def _write(handle, value):
    json.dump(value, handle, indent=2)
    handle.write("\n")


def _token(path=None):
    if path:
        record = read_private_json(path)
        token = record.get("owner_token", record.get("token"))
    else:
        token = os.environ.get("SEICHE_NETWORK_TOKEN")
    if not isinstance(token, str) or not token or len(token) > 256:
        raise ValueError("A private credential file or SEICHE_NETWORK_TOKEN is required")
    return token


def _store(args):
    if args.keys_file:
        keys = read_private_json(args.keys_file)
    else:
        keys = {"encryption_key": os.environ.get("SEICHE_NETWORK_ENCRYPTION_KEY"),
                "signing_key": os.environ.get("SEICHE_NETWORK_SIGNING_KEY")}
    if not all(isinstance(keys.get(name), str) and keys[name] for name in ("encryption_key", "signing_key")):
        raise ValueError("Service encryption/signing keys must be explicitly provisioned")
    return Store(args.database, keys["encryption_key"], keys["signing_key"],
                 retention_days=args.retention_days, metadata_retention_days=args.metadata_retention_days)


def parser():
    cli = argparse.ArgumentParser(prog="seiche-agent-network", description="Operate the permissioned Seiche Agent Network")
    cli.add_argument("--database", default=os.environ.get("SEICHE_NETWORK_DATABASE", "network.sqlite3"))
    cli.add_argument("--keys-file", default=os.environ.get("SEICHE_NETWORK_KEYS_FILE"))
    cli.add_argument("--retention-days", type=int, default=30)
    cli.add_argument("--metadata-retention-days", type=int, default=90)
    commands = cli.add_subparsers(dest="command", required=True)
    keys = commands.add_parser("init-keys", help="Create a new private encryption/signing key file")
    keys.add_argument("--output", required=True)
    org = commands.add_parser("init-org", help="Provision an accountable organisation owner locally")
    org.add_argument("--label", required=True)
    org.add_argument("--output", required=True)
    agent = commands.add_parser("provision-agent", aliases=["provision"], help="Owner: create a delegated agent credential")
    agent.add_argument("--owner-credential", required=True)
    agent.add_argument("--label", required=True)
    agent.add_argument("--capability", action="append", default=[])
    agent.add_argument("--public", action="store_true", help="Make the profile discoverable to authenticated network participants")
    agent.add_argument("--output", required=True)
    rotate = commands.add_parser("rotate-owner", help="Rotate the owner's credential into a new private file")
    rotate.add_argument("--owner-credential", required=True)
    rotate.add_argument("--output", required=True)
    remove = commands.add_parser("delete-org", help="Delete this organisation and its network records")
    remove.add_argument("--owner-credential", required=True)
    remove.add_argument("--confirm-org-id", required=True)
    verify = commands.add_parser("verify-org", help="Record an operator-reviewed organisation verification state")
    verify.add_argument("--organization-id", required=True)
    verify.add_argument("--unverify", action="store_true", help="Remove a previous operator verification")
    serve = commands.add_parser("serve", help="Run the isolated HTTP network service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--public-url", default=os.environ.get("SEICHE_NETWORK_PUBLIC_URL", "http://127.0.0.1:8765"))
    serve.add_argument("--allow-origin", action="append")
    serve.add_argument("--allow-host", action="append")
    serve.add_argument("--gateway", action="store_true", help="Enable the explicitly configured central product gateway")
    mcp = commands.add_parser("mcp", help="Run the official MCP stdio transport for one provisioned identity")
    mcp.add_argument("--credential-file", default=os.environ.get("SEICHE_NETWORK_CREDENTIAL_FILE"))
    commands.add_parser("purge", help="Apply expiry/retention without sending any messages")
    social_reports = commands.add_parser("social-reports", help="Local operator: inspect private public-community report queue")
    social_reports.add_argument("--limit", type=int, default=50)
    social_reports.add_argument("--cursor")
    moderate = commands.add_parser("social-moderate", help="Local operator: hide and erase a reported community post")
    moderate.add_argument("--post-id", required=True)
    moderate.add_argument("--reason", required=True)
    suspend = commands.add_parser("social-suspend", help="Local operator: withdraw public material and suspend workspace publishing")
    suspend.add_argument("--organization-id", required=True)
    suspend.add_argument("--reason", required=True)
    restore = commands.add_parser("social-restore", help="Local operator: allow fresh owner-authorized publishing; erased material stays erased")
    restore.add_argument("--organization-id", required=True)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == "init-keys":
            with private_output(args.output) as handle:
                _write(handle, generate_keys())
            print("Service keys written to the private output file.")
            return 0
        store = _store(args)
        if args.command == "init-org":
            with private_output(args.output) as handle:
                _write(handle, store.bootstrap_org(args.label))
            print("Organisation owner credential written to the private output file.")
        elif args.command in {"provision-agent", "provision"}:
            owner = store.authenticate(_token(args.owner_credential))
            with private_output(args.output) as handle:
                _write(handle, store.register_agent(owner, args.label, capabilities=args.capability, public=args.public))
            print("Agent credential written to the private output file.")
        elif args.command == "rotate-owner":
            owner = store.authenticate(_token(args.owner_credential))
            with private_output(args.output) as handle:
                _write(handle, store.rotate_owner_token(owner))
            print("Replacement owner credential written; the previous credential is revoked.")
        elif args.command == "delete-org":
            owner = store.authenticate(_token(args.owner_credential))
            if owner.agent_id is not None or owner.org_id != args.confirm_org_id:
                raise ValueError("The confirmed organisation must match this owner credential")
            store.delete_org(owner)
            print("Organisation records deleted from this service database. Manage retained backups separately.")
        elif args.command == "verify-org":
            store.verify_organization(args.organization_id, verified=not args.unverify)
            print("Operator-reviewed verification state recorded.")
        elif args.command == "purge":
            print(json.dumps(store.purge_expired(), sort_keys=True))
        elif args.command == "social-reports":
            from .social import Social
            print(json.dumps(Social(store).report_queue(limit=args.limit, cursor=args.cursor), sort_keys=True))
        elif args.command == "social-moderate":
            from .social import Social
            print(json.dumps(Social(store).moderate(args.post_id, args.reason), sort_keys=True))
        elif args.command == "social-suspend":
            from .social import Social
            print(json.dumps(Social(store).suspend_organization(args.organization_id, args.reason), sort_keys=True))
        elif args.command == "social-restore":
            from .social import Social
            print(json.dumps(Social(store).restore_organization(args.organization_id), sort_keys=True))
        elif args.command == "serve":
            import uvicorn
            from .api import create_app
            if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.public_url.startswith("https://"):
                raise ValueError("A non-loopback listener requires an HTTPS public URL and TLS termination")
            app = create_app(store, public_url=args.public_url, allowed_origins=args.allow_origin, allowed_hosts=args.allow_host, gateway=args.gateway)
            uvicorn.run(app, host=args.host, port=args.port, root_path=app.root_path,
                        access_log=False, proxy_headers=False,
                        timeout_graceful_shutdown=10)
        elif args.command == "mcp":
            from .mcp import create_mcp
            server = create_mcp(store, _token(args.credential_file))
            server.run(transport="stdio")
        return 0
    except NetworkError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, TypeError):
        print("Operation failed. Check the explicit configuration, private file permissions, and unused output path; credentials are never printed.", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
