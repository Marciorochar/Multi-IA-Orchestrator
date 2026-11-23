from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from multi_ia.adapters.base import AgentAdapter
from multi_ia.contracts import AgentRequest, AgentResult
from multi_ia.policies import CancellationController, ExecutionPolicy, ProgressGuard
from multi_ia.store import RunStore


class OrchestratorState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING = "waiting"
    AUDITING = "auditing"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class CycleOutcome:
    state: OrchestratorState
    next_cycle: bool = False
    results: dict[str, AgentResult] = field(default_factory=dict)
    reason: str | None = None


class _StopRequested(Exception):
    pass


class Orchestrator:
    def __init__(
        self,
        planner: AgentAdapter,
        executor: AgentAdapter,
        auditor: AgentAdapter,
        *,
        policy: ExecutionPolicy | None = None,
        cancellation: CancellationController | None = None,
        store: RunStore | None = None,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.auditor = auditor
        self.policy = policy or ExecutionPolicy(max_cycles=1, max_seconds=300, max_retries=0)
        self.cancellation = cancellation or CancellationController()
        self.store = store
        self.state = OrchestratorState.IDLE

    async def run_cycle(self, run_id: str, cycle_id: int) -> CycleOutcome:
        results: dict[str, AgentResult] = {}

        try:
            planner_result = await self._run_stage(
                adapter=self.planner,
                state=OrchestratorState.PLANNING,
                request=AgentRequest(run_id=run_id, cycle_id=cycle_id, role="planner", payload={}),
            )
            failure = self._validate_stage_result(planner_result, results, "planner")
            if failure is not None:
                return self._finish(run_id, cycle_id, failure)

            executor_result = await self._run_stage(
                adapter=self.executor,
                state=OrchestratorState.EXECUTING,
                request=AgentRequest(
                    run_id=run_id,
                    cycle_id=cycle_id,
                    role="executor",
                    payload=planner_result.payload,
                ),
            )
            failure = self._validate_stage_result(executor_result, results, "executor")
            if failure is not None:
                return self._finish(run_id, cycle_id, failure)

            auditor_result = await self._run_stage(
                adapter=self.auditor,
                state=OrchestratorState.AUDITING,
                request=AgentRequest(
                    run_id=run_id,
                    cycle_id=cycle_id,
                    role="auditor",
                    payload={"plan": planner_result.payload, "execution": executor_result.payload},
                ),
            )
            failure = self._validate_stage_result(auditor_result, results, "auditor")
            if failure is not None:
                return self._finish(run_id, cycle_id, failure)
        except _StopRequested:
            self.state = OrchestratorState.CANCELLED
            return self._finish(
                run_id,
                cycle_id,
                CycleOutcome(state=self.state, results=results, reason="stop_requested"),
            )
        except TimeoutError:
            self.state = OrchestratorState.FAILED
            return self._finish(
                run_id,
                cycle_id,
                CycleOutcome(state=self.state, results=results, reason="timeout"),
            )

        self.state = OrchestratorState.EVALUATING
        if auditor_result.payload.get("approved") is False:
            return self._finish(
                run_id,
                cycle_id,
                CycleOutcome(state=self.state, next_cycle=True, results=results),
            )

        self.state = OrchestratorState.COMPLETED
        return self._finish(run_id, cycle_id, CycleOutcome(state=self.state, results=results))

    async def _run_stage(
        self,
        *,
        adapter: AgentAdapter,
        state: OrchestratorState,
        request: AgentRequest,
    ) -> Any:
        if self.cancellation.stop_requested:
            raise _StopRequested
        self.state = state
        if self.store is not None:
            self.store.append_event(
                request.run_id,
                {"cycle_id": request.cycle_id, "state": state.value, "role": request.role},
            )
        result = await asyncio.wait_for(
            adapter.run(request),
            timeout=self.policy.max_seconds,
        )
        if self.cancellation.stop_requested:
            raise _StopRequested
        return result

    def _validate_stage_result(
        self,
        result: Any,
        results: dict[str, AgentResult],
        role: str,
    ) -> CycleOutcome | None:
        if not isinstance(result, AgentResult):
            self.state = OrchestratorState.FAILED
            return CycleOutcome(state=self.state, results=results, reason="malformed_result")

        results[role] = result
        if not result.is_terminal:
            self.state = OrchestratorState.FAILED
            return CycleOutcome(state=self.state, results=results, reason="non_terminal_result")

        if result.status == "cancelled":
            self.state = OrchestratorState.CANCELLED
            return CycleOutcome(state=self.state, results=results, reason=f"{role}_cancelled")

        if result.status == "failed":
            self.state = OrchestratorState.FAILED
            return CycleOutcome(state=self.state, results=results, reason=f"{role}_failed")

        return None

    def _finish(self, run_id: str, cycle_id: int, outcome: CycleOutcome) -> CycleOutcome:
        if self.store is not None:
            checkpoint = {"cycle_id": cycle_id, "state": outcome.state.value}
            if outcome.reason is not None:
                checkpoint["reason"] = outcome.reason
            executor_result = outcome.results.get("executor")
            if executor_result is not None and "write_status" in executor_result.payload:
                checkpoint["write_status"] = executor_result.payload["write_status"]
            self.store.save_checkpoint(run_id, checkpoint)
        return outcome
