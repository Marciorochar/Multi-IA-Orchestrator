from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from collections.abc import Callable
from typing import Any

from multi_ia.adapters.base import AgentAdapter
from multi_ia.contracts import AgentRequest, AgentResult, Operation, canonical_plan
from multi_ia.policies import ApprovalGate, CancellationController, ExecutionPolicy, PauseController, ProgressGuard
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


class _AdapterFailure(Exception):
    pass


class _PermissionDenied(Exception):
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
        initial_payload: dict[str, Any] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        pause: PauseController | None = None,
        approval_gate: ApprovalGate | None = None,
        execution_mode: str = "fake",
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.auditor = auditor
        self.policy = policy or ExecutionPolicy(max_cycles=1, max_seconds=300, max_retries=0)
        self.cancellation = cancellation or CancellationController()
        self.store = store
        self.initial_payload = dict(initial_payload or {})
        self.event_sink = event_sink
        self.pause = pause or PauseController()
        self.approval_gate = approval_gate if approval_gate is not None else ApprovalGate()
        self.execution_mode = execution_mode
        self.state = OrchestratorState.IDLE

    async def run(
        self,
        run_id: str,
        *,
        progress_guard: ProgressGuard | None = None,
    ) -> CycleOutcome:
        guard = progress_guard or ProgressGuard()
        previous: dict = {}
        self._emit("run_started", run_id=run_id)
        last_outcome: CycleOutcome | None = None

        for cycle_id in range(1, self.policy.max_cycles + 1):
            self._emit("cycle_started", run_id=run_id, cycle_id=cycle_id)
            outcome = await self.run_cycle(run_id, cycle_id)
            last_outcome = outcome
            if outcome.state in {
                OrchestratorState.COMPLETED,
                OrchestratorState.CANCELLED,
                OrchestratorState.FAILED,
            }:
                self._emit("state_changed", run_id=run_id, state=outcome.state.value)
                terminal_type = {
                    OrchestratorState.COMPLETED: "run_completed",
                    OrchestratorState.CANCELLED: "run_cancelled",
                    OrchestratorState.FAILED: "run_failed",
                }[outcome.state]
                self._emit(
                    terminal_type,
                    run_id=run_id,
                    state=outcome.state.value,
                    reason=outcome.reason,
                )
                return outcome

            current = {
                "status": outcome.state.value,
                "evidence": [
                    item for result in outcome.results.values() for item in result.evidence
                ],
                "artifacts": [
                    artifact
                    for result in outcome.results.values()
                    for artifact in result.payload.get("artifacts", [])
                ],
                "tests": [
                    test
                    for result in outcome.results.values()
                    for test in result.payload.get("tests", [])
                ],
            }
            if guard.evaluate(previous, current) == "pause":
                self.state = OrchestratorState.PAUSED
                paused = self._finish(
                    run_id,
                    cycle_id,
                    CycleOutcome(
                        state=self.state,
                        results=outcome.results,
                        reason="no_progress",
                    ),
                )
                self._emit("state_changed", run_id=run_id, state=paused.state.value)
                self._emit(
                    "run_paused",
                    run_id=run_id,
                    state=paused.state.value,
                    reason=paused.reason,
                )
                return paused
            if not outcome.next_cycle:
                return outcome
            previous = current

        self.state = OrchestratorState.PAUSED
        paused = self._finish(
            run_id,
            self.policy.max_cycles,
            CycleOutcome(
                state=self.state,
                results={} if last_outcome is None else last_outcome.results,
                reason="max_cycles",
            ),
        )
        self._emit("state_changed", run_id=run_id, state=paused.state.value)
        self._emit(
            "run_paused",
            run_id=run_id,
            state=paused.state.value,
            reason=paused.reason,
        )
        return paused

    async def run_cycle(self, run_id: str, cycle_id: int) -> CycleOutcome:
        results: dict[str, AgentResult] = {}

        if self.execution_mode != "fake":
            self.state = OrchestratorState.FAILED
            return self._finish(
                run_id, cycle_id,
                CycleOutcome(state=self.state, reason="real_mode_disabled"),
            )

        try:
            planner_result = await self._run_stage(
                adapter=self.planner,
                state=OrchestratorState.PLANNING,
                request=AgentRequest(
                    run_id=run_id,
                    cycle_id=cycle_id,
                    role="planner",
                    payload=self.initial_payload,
                ),
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
        except _PermissionDenied as exc:
            self.state = OrchestratorState.FAILED
            return self._finish(
                run_id, cycle_id,
                CycleOutcome(state=self.state, results=results, reason=str(exc)),
            )
        except _AdapterFailure:
            self.state = OrchestratorState.FAILED
            return self._finish(
                run_id,
                cycle_id,
                CycleOutcome(state=self.state, results=results, reason="adapter_exception"),
            )
        except TimeoutError:
            self.state = OrchestratorState.FAILED
            return self._finish(
                run_id,
                cycle_id,
                CycleOutcome(state=self.state, results=results, reason="timeout"),
            )

        decision = auditor_result.payload.get("approved")
        if type(decision) is not bool:
            self.state = OrchestratorState.FAILED
            return self._finish(
                run_id,
                cycle_id,
                CycleOutcome(
                    state=self.state, results=results, reason="invalid_audit_decision"
                ),
            )

        self.state = OrchestratorState.EVALUATING
        if decision is False:
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
        if not await self.pause.wait_until_resumed(
            lambda: self._stop_requested(request.run_id)
        ):
            raise _StopRequested
        if self._stop_requested(request.run_id):
            raise _StopRequested
        if request.role == "executor":
            try:
                snapshot = json.loads(canonical_plan(request.payload))
                operation = Operation.from_plan(
                    snapshot, run_id=request.run_id, cycle_id=request.cycle_id
                )
            except (ValueError, TypeError, RecursionError):
                raise _PermissionDenied("invalid_operation") from None
            denial = self.approval_gate.authorize(operation)
            if denial is not None:
                raise _PermissionDenied(denial)
            request = replace(request, payload=snapshot)
        self.state = state
        self._emit(
            "stage_started",
            run_id=request.run_id,
            cycle_id=request.cycle_id,
            role=request.role,
            state=state.value,
        )
        if self.store is not None:
            self.store.append_event(
                request.run_id,
                {"cycle_id": request.cycle_id, "state": state.value, "role": request.role},
            )
        try:
            result = await asyncio.wait_for(
                adapter.run(request),
                timeout=self.policy.max_seconds,
            )
        except TimeoutError:
            raise
        except Exception:
            # Do not propagate adapter-provided exception text into logs or UI.
            raise _AdapterFailure("adapter_exception") from None
        self._emit(
            "stage_completed",
            run_id=request.run_id,
            cycle_id=request.cycle_id,
            role=request.role,
            status=getattr(result, "status", "invalid"),
        )
        if self._stop_requested(request.run_id):
            raise _StopRequested
        return result

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.event_sink is None:
            return
        self.event_sink({"type": event_type, **payload})

    def _stop_requested(self, run_id: str) -> bool:
        return self.cancellation.stop_requested or (
            self.store is not None and self.store.stop_requested(run_id)
        )

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
