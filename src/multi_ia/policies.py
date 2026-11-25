from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass

from multi_ia.contracts import Operation


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

    def allows_cycle(self, cycle_id: int) -> bool:
        return 1 <= cycle_id <= self.max_cycles


class CancellationController:
    def __init__(self) -> None:
        self._stop_requested = False

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def request_stop(self) -> None:
        self._stop_requested = True


class ApprovalGate:
    """One-use grants for exact simulated operations; no real side effects authorized."""

    _APPROVAL_REQUIRED = frozenset({"write_workspace", "delete"})

    def __init__(self) -> None:
        self._approved: dict[tuple[str, int, str], Operation] = {}
        self._consumed: set[tuple[str, int, str]] = set()
        self._lock = threading.Lock()

    def check(self, action: str) -> bool:
        # Legacy callers cannot grant or check broad mutation permissions.
        return type(action) is str and action == "read"

    def approve(self, operation: Operation, *, approved: bool) -> None:
        if type(approved) is not bool:
            raise ValueError("explicit boolean decision required")
        if type(operation) is not Operation or operation.action not in self._APPROVAL_REQUIRED:
            raise ValueError("operation cannot receive approval in simulation mode")
        with self._lock:
            key = operation.identity
            if key in self._consumed:
                raise ValueError("operation already consumed")
            existing = self._approved.get(key)
            if existing is not None and existing != operation:
                raise ValueError("approval identity already bound to different content")
            if approved:
                self._approved[key] = operation
            else:
                self._approved.pop(key, None)

    def authorize(self, operation: Operation) -> str | None:
        if type(operation) is not Operation:
            return "permission_denied"
        if operation.action == "read":
            return None
        if operation.action not in self._APPROVAL_REQUIRED:
            return "permission_denied"
        with self._lock:
            key = operation.identity
            if key in self._consumed:
                return "operation_replayed"
            if self._approved.get(key) != operation:
                return "approval_required"
            del self._approved[key]
            self._consumed.add(key)
        return None


class ProgressGuard:
    def __init__(self, max_stagnant_cycles: int = 2) -> None:
        if max_stagnant_cycles < 1:
            raise ValueError("max_stagnant_cycles must be at least 1")
        self.max_stagnant_cycles = max_stagnant_cycles
        self._stagnant_cycles = 0

    @staticmethod
    def _signal(snapshot: dict) -> tuple:
        return (
            snapshot.get("status"),
            tuple(snapshot.get("evidence", ())),
            tuple(snapshot.get("artifacts", ())),
            tuple(snapshot.get("tests", ())),
            tuple(sorted(snapshot.get("metrics", {}).items())),
        )

    def evaluate(self, previous: dict, current: dict) -> str:
        if current.get("verified_success") is True:
            self._stagnant_cycles = 0
            return "complete"

        if self._signal(previous) == self._signal(current):
            self._stagnant_cycles += 1
            if self._stagnant_cycles >= self.max_stagnant_cycles:
                return "pause"
        else:
            self._stagnant_cycles = 0
        return "continue"


class PauseController:
    def __init__(self) -> None:
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def request_pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    async def wait_until_resumed(
        self,
        stop_requested: Callable[[], bool],
        *,
        poll_seconds: float = 0.01,
    ) -> bool:
        while self._paused:
            if stop_requested():
                return False
            await asyncio.sleep(poll_seconds)
        return not stop_requested()
