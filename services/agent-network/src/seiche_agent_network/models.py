"""Shared domain values. No HTTP or agent SDK dependencies."""
from dataclasses import dataclass


class NetworkError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Principal:
    org_id: str
    agent_id: str | None
    credential_id: str


MESSAGE_KINDS = frozenset({"question", "reply", "evidence", "status", "cancel", "escalation"})
TASK_STATES = frozenset({"submitted", "accepted", "running", "completed", "failed", "cancelled"})
