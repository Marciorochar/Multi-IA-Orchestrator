from __future__ import annotations

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
