"""Single-owner Telegram poller and persisted in-process scheduler.

The worker starts only after the root supervisor validates a Phase-7 grant.
All Telegram input handling and scheduled delivery is sequential, so the one
mounted state generation never has competing writers.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any, Callable, Mapping

from seiche import telegram_migration as migration


SCHEDULE_SCHEMA = "seiche.railway-telegram-schedule.v1"
HEARTBEAT_SCHEMA = "seiche.railway-telegram-heartbeat.v1"
BOT_PATH = Path("/workspace/bot/seiche_bot.py")
POLL_TIMEOUT = 20

_SCHEDULE_NAMES = ("alert", "letter", "tandem")
_MAX_CATCHUP = {
    "alert": timedelta(minutes=45),
    "letter": timedelta(hours=2),
    "tandem": timedelta(hours=7),
}


class TelegramWorkerError(RuntimeError):
    """The granted worker cannot safely continue."""


def _load_bot(path: Path = BOT_PATH) -> ModuleType:
    spec = importlib.util.spec_from_file_location("seiche_railway_bot", path)
    if spec is None or spec.loader is None:
        raise TelegramWorkerError("Telegram bot module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _slot_text(moment: datetime) -> str:
    return (
        moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _parse_slot(value: object, *, label: str) -> datetime:
    try:
        return migration._utc(value, label=label)
    except migration.TelegramMigrationError as exc:
        raise TelegramWorkerError(str(exc)) from exc


def latest_slot(name: str, observed: datetime) -> datetime:
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise TelegramWorkerError("scheduler clock is not timezone-aware")
    now = observed.astimezone(UTC).replace(second=0, microsecond=0)
    if name == "alert":
        minute = 35 if now.minute >= 35 else 5 if now.minute >= 5 else None
        if minute is None:
            return (now - timedelta(hours=1)).replace(minute=35)
        return now.replace(minute=minute)
    if name == "letter":
        slot = now.replace(hour=11, minute=30)
        return slot if slot <= now else slot - timedelta(days=1)
    if name == "tandem":
        hours = (1, 7, 13, 19)
        eligible = [hour for hour in hours if (hour, 10) <= (now.hour, now.minute)]
        if eligible:
            return now.replace(hour=max(eligible), minute=10)
        return (now - timedelta(days=1)).replace(hour=19, minute=10)
    raise TelegramWorkerError("scheduler job is unsupported")


def scheduler_baseline(frozen_at: datetime) -> dict[str, str]:
    return {name: _slot_text(latest_slot(name, frozen_at)) for name in _SCHEDULE_NAMES}


def _write_mutable(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    body = migration.canonical(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        offset = 0
        while offset < len(body):
            count = os.write(descriptor, body[offset:])
            if count <= 0:
                raise OSError("Telegram state write made no progress")
            offset += count
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _initial_schedule(path: Path, frozen_at: datetime) -> dict[str, Any]:
    baseline = scheduler_baseline(frozen_at)
    value = {
        "schema": SCHEDULE_SCHEMA,
        "completed": baseline,
        "inflight": None,
        "last_outcome": {name: "source-baseline" for name in _SCHEDULE_NAMES},
    }
    _write_mutable(path, value)
    return value


def load_schedule(path: Path, frozen_at: datetime) -> dict[str, Any]:
    if not path.exists():
        return _initial_schedule(path, frozen_at)
    try:
        body = migration.platform._stable_read(path, maximum_bytes=32 * 1024)
        value = migration.platform._decode_canonical_json(
            body, label="Telegram schedule state"
        )
    except migration.platform.MigrationContractError as exc:
        raise TelegramWorkerError(str(exc)) from exc
    if (
        set(value) != {"schema", "completed", "inflight", "last_outcome"}
        or value.get("schema") != SCHEDULE_SCHEMA
        or not isinstance(value.get("completed"), dict)
        or set(value["completed"]) != set(_SCHEDULE_NAMES)
        or not isinstance(value.get("last_outcome"), dict)
        or set(value["last_outcome"]) != set(_SCHEDULE_NAMES)
        or any(not isinstance(item, str) for item in value["completed"].values())
        or any(not isinstance(item, str) for item in value["last_outcome"].values())
    ):
        raise TelegramWorkerError("Telegram schedule state is invalid")
    for name, slot in value["completed"].items():
        _parse_slot(slot, label=f"{name} completed slot")
    inflight = value.get("inflight")
    if inflight is not None:
        if (
            not isinstance(inflight, dict)
            or set(inflight) != {"job", "slot", "started_at"}
            or inflight.get("job") not in _SCHEDULE_NAMES
        ):
            raise TelegramWorkerError("Telegram in-flight delivery is invalid")
        _parse_slot(inflight.get("slot"), label="in-flight slot")
        _parse_slot(inflight.get("started_at"), label="in-flight started_at")
        raise TelegramWorkerError(
            "Telegram delivery outcome is uncertain; operator reconciliation required"
        )
    return value


def run_due_jobs(
    schedule: dict[str, Any],
    *,
    schedule_path: Path,
    now: datetime,
    jobs: Mapping[str, Callable[[], None]],
) -> None:
    due: list[tuple[datetime, str]] = []
    for name in _SCHEDULE_NAMES:
        slot = latest_slot(name, now)
        completed = _parse_slot(
            schedule["completed"][name], label=f"{name} completed slot"
        )
        if slot > completed:
            due.append((slot, name))
    for slot, name in sorted(due):
        delay = now.astimezone(UTC) - slot
        slot_text = _slot_text(slot)
        if delay > _MAX_CATCHUP[name]:
            schedule["completed"][name] = slot_text
            schedule["last_outcome"][name] = "stale-slot-skipped"
            _write_mutable(schedule_path, schedule)
            continue
        schedule["inflight"] = {
            "job": name,
            "slot": slot_text,
            "started_at": migration.iso_now(),
        }
        _write_mutable(schedule_path, schedule)
        jobs[name]()
        schedule["completed"][name] = slot_text
        schedule["last_outcome"][name] = "completed"
        schedule["inflight"] = None
        _write_mutable(schedule_path, schedule)


def _process_updates(bot: ModuleType, updates: list[object], offset: int) -> int:
    observed = offset
    for raw in updates:
        if not isinstance(raw, dict) or not isinstance(raw.get("update_id"), int):
            raise TelegramWorkerError("Telegram update envelope is invalid")
        observed = max(observed, raw["update_id"] + 1)
        inline = raw.get("inline_query")
        if inline:
            try:
                bot.answer_inline(inline)
            except Exception as exc:  # one malformed update must not stop authority
                print(f"inline failed: {exc}", file=sys.stderr, flush=True)
            continue
        callback = raw.get("callback_query")
        if callback:
            bot.tg_call("answerCallbackQuery", {"callback_query_id": callback["id"]})
            message = {
                "text": callback.get("data") or "",
                "chat": (callback.get("message") or {}).get("chat") or {},
            }
        else:
            message = raw.get("message") or {}
        text = message.get("text")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if text and chat_id:
            try:
                bot.handle(chat_id, text, chat.get("type") or "private")
            except Exception as exc:
                print(f"handle failed: {exc}", file=sys.stderr, flush=True)
    bot.save_state("offset.json", observed)
    return observed


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    body = migration.canonical(value)
    if path.exists() or path.is_symlink():
        existing, _document = migration.load_document(
            path, label="Telegram worker proof"
        )
        if existing != body:
            raise TelegramWorkerError("Telegram worker proof already differs")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o440)
    try:
        offset = 0
        while offset < len(body):
            count = os.write(descriptor, body[offset:])
            if count <= 0:
                raise OSError("Telegram proof write made no progress")
            offset += count
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o440)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _get_updates(bot: ModuleType, offset: int) -> list[object] | None:
    response = bot.tg_call(
        "getUpdates",
        {
            "timeout": POLL_TIMEOUT,
            "offset": offset,
            "allowed_updates": ["message", "callback_query", "inline_query"],
        },
    )
    if not isinstance(response, dict) or response.get("ok") is not True:
        code = response.get("error_code") if isinstance(response, dict) else None
        if code == 409:
            raise TelegramWorkerError("another Telegram getUpdates consumer exists")
        if code == 401:
            raise TelegramWorkerError("Telegram rejected the granted token")
        return None
    results = response.get("result")
    if not isinstance(results, list):
        raise TelegramWorkerError("Telegram getUpdates result is malformed")
    return results


def _context(environment: Mapping[str, str]) -> dict[str, Any]:
    _image_body, image = migration.image_context(environment)
    railway = migration.railway_identity(environment)
    paths = {
        name: Path(environment.get(variable, ""))
        for name, variable in (
            ("request", "SEICHE_TELEGRAM_TRANSFER_PATH"),
            ("candidate", "SEICHE_TELEGRAM_CANDIDATE_PATH"),
            ("grant", "SEICHE_TELEGRAM_GRANT_PATH"),
            ("state", "SEICHE_BOT_STATE"),
            ("proof", "SEICHE_TELEGRAM_WORKER_PROOF_PATH"),
            ("heartbeat", "SEICHE_TELEGRAM_HEARTBEAT_PATH"),
        )
    }
    for path in paths.values():
        if not path.is_absolute():
            raise TelegramWorkerError("Telegram worker path is not absolute")
    _request_body, request = migration.load_document(
        paths["request"], label="Telegram transfer request"
    )
    migration.validate_transfer(
        request,
        image_request=image,
        railway=railway,
        require_fresh=False,
        expected_lab_channel_id=migration.lab_channel_identity(environment),
    )
    _candidate_body, candidate = migration.load_document(
        paths["candidate"], label="Telegram candidate receipt"
    )
    migration.validate_candidate(
        candidate,
        request=request,
        railway=railway,
    )
    migration.recover_live_state_temps(paths["state"])
    migration.validate_live_state(paths["state"], baseline=request["state"])
    _grant_body, grant = migration.load_document(
        paths["grant"], label="Telegram authority grant"
    )
    migration.validate_grant(
        grant,
        request=request,
        candidate=candidate,
        require_fresh=False,
    )
    token = environment.get("SEICHE_BOT_TOKEN", "")
    if (
        not token
        or hashlib.sha256(token.encode()).hexdigest() != grant["bot_token_sha256"]
    ):
        raise TelegramWorkerError("Telegram token does not match the authority grant")
    return {
        "image": image,
        "railway": railway,
        "paths": paths,
        "request": request,
        "candidate": candidate,
        "grant": grant,
    }


def run(environment: Mapping[str, str] | None = None) -> int:
    env = dict(environment or os.environ)
    if os.geteuid() != migration.RUNTIME_UID or os.getegid() != migration.RUNTIME_GID:
        raise TelegramWorkerError("Telegram worker identity is invalid")
    context = _context(env)
    request = context["request"]
    candidate = context["candidate"]
    grant = context["grant"]
    railway = context["railway"]
    paths = context["paths"]
    os.environ.update(env)
    bot = _load_bot()
    restored_offset = request["state"]["offset"]
    offset = migration.inspect_state(paths["state"])["offset"]
    schedule_path = paths["state"] / "railway_schedule.json"
    frozen_at = migration._utc(
        request["fence"]["frozen_at"], label="Telegram frozen_at"
    )
    schedule = load_schedule(schedule_path, frozen_at)
    baseline = dict(schedule["completed"])
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    me = bot.tg_call("getMe", {})
    if not isinstance(me, dict) or me.get("ok") is not True:
        raise TelegramWorkerError("Telegram bot identity probe failed")
    profile = me.get("result")
    if (
        not isinstance(profile, dict)
        or not isinstance(profile.get("id"), int)
        or not isinstance(profile.get("username"), str)
    ):
        raise TelegramWorkerError("Telegram bot identity is malformed")
    proof_written = paths["proof"].exists()
    if proof_written:
        _proof_body, existing_proof = migration.load_document(
            paths["proof"], label="Telegram worker proof"
        )
        migration.validate_worker_proof(
            existing_proof,
            request=request,
            candidate=candidate,
            grant=grant,
            railway=railway,
        )
    while not stopping:
        results = _get_updates(bot, offset)
        if results is None:
            time.sleep(5)
            continue
        offset = _process_updates(bot, results, offset)
        first_poll_at = migration.iso_now()
        if not proof_written:
            proof = {
                "schema": migration.WORKER_PROOF_SCHEMA,
                "repository": migration.REPOSITORY,
                "commit": request["commit"],
                "request_id": request["request_id"],
                "candidate_receipt_sha256": migration.digest(
                    migration.canonical(candidate)
                ),
                "grant_sha256": migration.digest(migration.canonical(grant)),
                "railway": dict(railway),
                "bot": {"id": profile["id"], "username": profile["username"]},
                "initial_offset": restored_offset,
                "observed_offset": offset,
                "first_poll_at": first_poll_at,
                "scheduler_baseline": baseline,
                "get_updates_ok": True,
                "conflict_observed": False,
            }
            migration.validate_worker_proof(
                proof,
                request=request,
                candidate=candidate,
                grant=grant,
                railway=railway,
            )
            _write_once(paths["proof"], proof)
            proof_written = True
        now = datetime.now(UTC).replace(microsecond=0)
        run_due_jobs(
            schedule,
            schedule_path=schedule_path,
            now=now,
            jobs={
                "alert": bot.run_alert_scan,
                "letter": bot.run_letter,
                "tandem": bot.run_tandem,
            },
        )
        heartbeat = {
            "schema": HEARTBEAT_SCHEMA,
            "commit": request["commit"],
            "deployment_id": railway["deployment_id"],
            "request_id": request["request_id"],
            "observed_at": migration.iso_now(),
            "offset": offset,
            "mode": "production",
            "faults": [],
        }
        _write_mutable(paths["heartbeat"], heartbeat)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (TelegramWorkerError, migration.TelegramMigrationError) as error:
        print(f"seiche Railway Telegram worker: {error}", file=sys.stderr)
        raise SystemExit(1) from None
