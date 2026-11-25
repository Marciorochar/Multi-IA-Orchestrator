from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

VALID_STATUSES = frozenset({"pending", "running", "completed", "failed", "cancelled"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class AgentRequest:
    run_id: str
    cycle_id: int
    role: str
    payload: dict[str, Any]
    protocol_version: str = "1.0"

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(self.cycle_id, int) or self.cycle_id < 1:
            raise ValueError("cycle_id must be a positive integer")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("role must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dict")


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: str
    payload: dict[str, Any]
    evidence: list[str] = field(default_factory=list)
    protocol_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {self.status}")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dict")
        if not isinstance(self.evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in self.evidence
        ):
            raise ValueError("evidence must be a list of non-empty strings")
        if self.status == "completed" and not self.evidence:
            raise ValueError("completed results require evidence")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def canonical_plan(payload: dict[str, Any]) -> str:
    """Return a deterministic JSON snapshot of the proposed plan."""
    if type(payload) is not dict:
        raise ValueError("plan must be a dict")
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


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
