from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

VALID_STATUSES = frozenset({"pending", "running", "completed", "failed", "cancelled"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
VALID_ROLES = frozenset({"planner", "executor", "auditor"})
PROTOCOL_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class AgentRequest:
    run_id: str
    cycle_id: int
    role: str
    payload: dict[str, Any]
    protocol_version: str = "1.0"

    def __post_init__(self) -> None:
        if type(self.run_id) is not str or not self.run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if type(self.cycle_id) is not int or self.cycle_id < 1:
            raise ValueError("cycle_id must be a positive integer")
        if type(self.role) is not str or self.role not in VALID_ROLES:
            raise ValueError("invalid role")
        if type(self.protocol_version) is not str or self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        # Requests own their payload; adapter mutations cannot modify prior results.
        object.__setattr__(self, "payload", json.loads(canonical_plan(self.payload)))


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: str
    payload: dict[str, Any]
    evidence: list[str] = field(default_factory=list)
    protocol_version: str = "1.0"

    def __post_init__(self) -> None:
        if type(self.status) is not str or self.status not in VALID_STATUSES:
            raise ValueError("invalid status")
        if type(self.protocol_version) is not str or self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        canonical_plan(self.payload)
        if type(self.evidence) is not list or not all(
            type(item) is str and item.strip() for item in self.evidence
        ):
            raise ValueError("evidence must be a list of non-empty strings")
        if self.status == "completed" and not self.evidence:
            raise ValueError("completed results require evidence")
        for name in ("artifacts", "tests"):
            if name in self.payload:
                items = self.payload[name]
                if type(items) is not list or not all(
                    type(item) is str and item.strip() for item in items
                ):
                    raise ValueError("invalid progress evidence")
        if "write_status" in self.payload and (
            type(self.payload["write_status"]) is not str
            or self.payload["write_status"] not in {
                "confirmed", "unconfirmed", "needs_reconciliation"
            }
        ):
            raise ValueError("invalid write status")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def _check_json(value: Any) -> None:
    kind = type(value)
    if value is None or kind in (str, bool, int):
        return
    if kind is float and math.isfinite(value):
        return
    if kind is list:
        for item in value:
            _check_json(item)
        return
    if kind is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            _check_json(item)
        return
    raise ValueError("unsupported JSON value")


def canonical_plan(payload: dict[str, Any]) -> str:
    """Return a deterministic, strict JSON snapshot of a payload."""
    if type(payload) is not dict:
        raise ValueError("payload must be a dict")
    try:
        _check_json(payload)
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, RecursionError, OverflowError):
        raise ValueError("invalid JSON payload") from None


def snapshot_result(result: AgentResult) -> AgentResult:
    """Validate again at the adapter boundary and detach mutable fields."""
    if type(result) is not AgentResult:
        raise ValueError("invalid result type")
    if type(result.evidence) is not list:
        raise ValueError("invalid evidence")
    return AgentResult(
        status=result.status,
        payload=json.loads(canonical_plan(result.payload)),
        evidence=list(result.evidence),
        protocol_version=result.protocol_version,
    )


def audit_decision(result: AgentResult) -> bool:
    """Only explicit, consistent decisions can authorize a transition."""
    decision = result.payload.get("approved")
    if type(decision) is not bool:
        raise ValueError("invalid audit decision")
    if "tests_passed" in result.payload:
        tests_passed = result.payload["tests_passed"]
        if type(tests_passed) is not bool or (decision and not tests_passed):
            raise ValueError("inconsistent audit decision")
    return decision


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: str
    run_id: str
    cycle_id: int
    action: str
    scope: str
    effect: str
    plan_digest: str
    version: str = "1.0"

    def __post_init__(self) -> None:
        for value in (self.operation_id, self.run_id, self.action):
            if type(value) is not str or not value.strip():
                raise ValueError("invalid operation identity")
        if type(self.cycle_id) is not int or self.cycle_id < 1:
            raise ValueError("invalid operation cycle")
        if self.version != "1.0" or self.scope != "simulation" or self.effect != "simulated":
            raise ValueError("unsupported operation declaration")
        if (
            type(self.plan_digest) is not str
            or len(self.plan_digest) != 64
            or any(c not in "0123456789abcdef" for c in self.plan_digest)
        ):
            raise ValueError("invalid plan digest")

    @property
    def identity(self) -> tuple[str, int, str]:
        return self.run_id, self.cycle_id, self.operation_id

    @classmethod
    def from_plan(cls, payload: dict[str, Any], *, run_id: str, cycle_id: int) -> Operation:
        # Approval is supplied by trusted application code, never by the planner.
        if {"approved", "approval", "action", "actions", "operations", "effect", "scope", "operation_id"} & payload.keys():
            raise ValueError("untrusted or ambiguous operation metadata")
        digest = hashlib.sha256(canonical_plan(payload).encode("utf-8")).hexdigest()
        if "operation" not in payload:
            # Compatibility for existing non-mutating fake adapters only.
            return cls("implicit-read", run_id, cycle_id, "read", "simulation", "simulated", digest)
        declaration = payload["operation"]
        fields = {"version", "operation_id", "run_id", "cycle_id", "scope", "effect", "action"}
        if type(declaration) is not dict or set(declaration) != fields:
            raise ValueError("invalid operation declaration")
        operation = cls(**declaration, plan_digest=digest)
        if operation.run_id != run_id or operation.cycle_id != cycle_id:
            raise ValueError("operation does not belong to the current run and cycle")
        return operation
