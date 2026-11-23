from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    max_cycles: int
    max_seconds: float
    max_retries: int
    max_budget: float | None = None

    def __post_init__(self) -> None:
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be at least 1")
        if self.max_seconds <= 0:
            raise ValueError("max_seconds must be greater than zero")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.max_budget is not None and self.max_budget < 0:
            raise ValueError("max_budget cannot be negative")


class CancellationController:
    def __init__(self) -> None:
        self._stop_requested = False

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def request_stop(self) -> None:
        self._stop_requested = True


@dataclass(slots=True)
class ApprovalGate:
    dangerous_actions: set[str] = field(
        default_factory=lambda: {"delete", "publish", "production", "write_external"}
    )
    _approved: set[str] = field(default_factory=set, init=False, repr=False)

    def approve(self, action: str) -> None:
        self._approved.add(action)

    def check(self, action: str) -> bool:
        return action not in self.dangerous_actions or action in self._approved
