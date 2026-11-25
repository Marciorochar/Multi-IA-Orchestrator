from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field, replace
from enum import Enum
from collections.abc import Callable
from typing import Any

from multi_ia.adapters.base import AgentAdapter
from multi_ia.contracts import AgentRequest, AgentResult, Operation, VALID_STATUSES, canonical_plan
from multi_ia.policies import ApprovalGate, CancellationController, ExecutionLimitError, ExecutionPolicy, PauseController, ProgressGuard, RetryableReadError
from multi_ia.limits import ExecutionLimits
from multi_ia.store import JournalBlocked, RunStore
from multi_ia.recovery import recovery_marker
from multi_ia.security import redact


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


class _ObserverFailure(Exception):
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
        recovery_enabled: bool = False,
        limits: ExecutionLimits | None = None,
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
        self.recovery_enabled = recovery_enabled
        self._provided_limits = limits
        self._run_limits: dict[str, ExecutionLimits] = {}
        self.state = OrchestratorState.IDLE

    def _limits_for(self, run_id: str) -> ExecutionLimits:
        if self._provided_limits is not None:
            if self._provided_limits.run_id != run_id:
                raise ExecutionLimitError("limits_evidence_invalid")
            return self._provided_limits
        if run_id not in self._run_limits:
            self._run_limits[run_id] = ExecutionLimits(self.policy)
        return self._run_limits[run_id]

    async def run(
        self,
        run_id: str,
        *,
        progress_guard: ProgressGuard | None = None,
    ) -> CycleOutcome:
        guard = progress_guard or ProgressGuard()
        previous: dict = {}
        try:
            self._emit("run_started", run_id=run_id)
        except _ObserverFailure:
            return self._observer_abort(run_id, 1)
        last_outcome: CycleOutcome | None = None

        for cycle_id in range(1, self.policy.max_cycles + 1):
            try:
                self._emit("cycle_started", run_id=run_id, cycle_id=cycle_id)
            except _ObserverFailure:
                return self._observer_abort(run_id, cycle_id)
            outcome = await self.run_cycle(run_id, cycle_id)
            last_outcome = outcome
            if outcome.state in {
                OrchestratorState.COMPLETED,
                OrchestratorState.CANCELLED,
                OrchestratorState.FAILED,
            }:
                self._emit_terminal("state_changed", run_id=run_id, state=outcome.state.value)
                terminal_type = {
                    OrchestratorState.COMPLETED: "run_completed",
                    OrchestratorState.CANCELLED: "run_cancelled",
                    OrchestratorState.FAILED: "run_failed",
                }[outcome.state]
                self._emit_terminal(
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
                self._emit_terminal("state_changed", run_id=run_id, state=paused.state.value)
                self._emit_terminal(
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
        self._emit_terminal("state_changed", run_id=run_id, state=paused.state.value)
        self._emit_terminal(
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
            if not self.policy.allows_cycle(cycle_id):
                return CycleOutcome(OrchestratorState.FAILED, reason="max_cycles")
            if self.store is not None:
                reason = self.store.dispatch_block_reason(run_id, cycle_id)
                if reason:
                    raise JournalBlocked(reason)
            self._limits_for(run_id).checkpoint()
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
                recovery_plan=planner_result,
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
        except asyncio.CancelledError:
            self.state = OrchestratorState.CANCELLED
            self._finish(run_id, cycle_id, CycleOutcome(
                self.state, results=results, reason="task_cancelled"
            ))
            raise
        except _ObserverFailure:
            self.state = OrchestratorState.FAILED
            return self._finish(run_id, cycle_id, CycleOutcome(
                self.state, results=results, reason="observer_failure"
            ))
        except JournalBlocked as exc:
            self.state = OrchestratorState.FAILED
            # Never overwrite an existing run's authoritative checkpoint.
            return CycleOutcome(state=self.state, results=results, reason=str(exc))
        except sqlite3.Error:
            self.state = OrchestratorState.FAILED
            return CycleOutcome(state=self.state, results=results, reason="persistence_failure")
        except _StopRequested:
            self.state = OrchestratorState.CANCELLED
            return self._finish(
                run_id,
                cycle_id,
                CycleOutcome(state=self.state, results=results, reason="stop_requested"),
            )
        except ExecutionLimitError as exc:
            self.state = OrchestratorState.FAILED
            return self._finish(
                run_id, cycle_id, CycleOutcome(self.state, results=results, reason=str(exc))
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

        except Exception:
            self.state = OrchestratorState.FAILED
            return self._finish(run_id, cycle_id, CycleOutcome(
                self.state, results=results, reason="internal_failure"
            ))

        return self._finish_audit(run_id, cycle_id, results, auditor_result)

    def _finish_audit(
        self, run_id: str, cycle_id: int, results: dict[str, AgentResult],
        auditor_result: AgentResult,
        *, recovered: bool = False,
    ) -> CycleOutcome:
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
        if decision is False and recovered:
            self.state = OrchestratorState.PAUSED
            return self._finish(
                run_id, cycle_id, CycleOutcome(
                    state=self.state, results=results, reason="recovery_review_required"
                ),
            )
        if decision is False:
            return self._finish(
                run_id,
                cycle_id,
                CycleOutcome(state=self.state, next_cycle=True, results=results),
            )

        self.state = OrchestratorState.COMPLETED
        return self._finish(run_id, cycle_id, CycleOutcome(state=self.state, results=results))

    async def _resume_confirmed_audit(
        self, run_id: str, cycle_id: int,
        planner_result: AgentResult, executor_result: AgentResult,
    ) -> CycleOutcome:
        """Internal service entry point: never dispatch a planner or executor."""
        results = {"planner": planner_result, "executor": executor_result}
        if self.execution_mode != "fake" or not self.recovery_enabled or self.store is None:
            return CycleOutcome(OrchestratorState.FAILED, reason="recovery_boundary_unsupported")
        try:
            auditor_result = await self._run_stage(
                adapter=self.auditor, state=OrchestratorState.AUDITING,
                request=AgentRequest(
                    run_id, cycle_id, "auditor",
                    {"plan": planner_result.payload, "execution": executor_result.payload},
                ),
            )
            failure = self._validate_stage_result(auditor_result, results, "auditor")
            if failure is not None:
                return self._finish(run_id, cycle_id, failure)
        except asyncio.CancelledError:
            self.state = OrchestratorState.CANCELLED
            self._finish(run_id, cycle_id, CycleOutcome(
                self.state, results=results, reason="task_cancelled"
            ))
            raise
        except _ObserverFailure:
            self.state = OrchestratorState.FAILED
            return self._finish(run_id, cycle_id, CycleOutcome(
                self.state, results=results, reason="observer_failure"
            ))
        except JournalBlocked as exc:
            self.state = OrchestratorState.FAILED
            return CycleOutcome(self.state, reason=str(exc))
        except sqlite3.Error:
            self.state = OrchestratorState.FAILED
            return CycleOutcome(self.state, reason="persistence_failure")
        except ExecutionLimitError as exc:
            self.state = OrchestratorState.FAILED
            return CycleOutcome(self.state, reason=str(exc))
        except (_StopRequested, _AdapterFailure, TimeoutError) as exc:
            self.state = (OrchestratorState.CANCELLED if isinstance(exc, _StopRequested)
                          else OrchestratorState.FAILED)
            reason = ("stop_requested" if isinstance(exc, _StopRequested)
                      else "timeout" if isinstance(exc, TimeoutError) else "adapter_exception")
            return self._finish(run_id, cycle_id, CycleOutcome(self.state, results=results, reason=reason))
        except Exception:
            self.state = OrchestratorState.FAILED
            return self._finish(run_id, cycle_id, CycleOutcome(
                self.state, results=results, reason="internal_failure"
            ))
        return self._finish_audit(run_id, cycle_id, results, auditor_result, recovered=True)

    async def _run_stage(
        self,
        *,
        adapter: AgentAdapter,
        state: OrchestratorState,
        request: AgentRequest,
        recovery_plan: AgentResult | None = None,
    ) -> Any:
        limits = self._limits_for(request.run_id)
        if self._stop_requested(request.run_id):
            raise _StopRequested
        if not await asyncio.wait_for(
            self.pause.wait_until_resumed(lambda: self._stop_requested(request.run_id)),
            timeout=limits.checkpoint(),
        ):
            raise _StopRequested
        if self._stop_requested(request.run_id):
            raise _StopRequested
        limits.checkpoint()
        operation = None
        if request.role == "executor":
            try:
                snapshot = json.loads(canonical_plan(request.payload))
                operation = Operation.from_plan(
                    snapshot, run_id=request.run_id, cycle_id=request.cycle_id
                )
            except (ValueError, TypeError, RecursionError):
                raise _PermissionDenied("invalid_operation") from None
            if operation.action in {"write_workspace", "delete"} and self.store is None:
                raise _PermissionDenied("journal_required")
            denial = self.approval_gate.authorize(operation)
            if denial is not None:
                raise _PermissionDenied(denial)
            request = replace(request, payload=snapshot)
        if request.role == "auditor" and self.recovery_enabled and self.store is not None:
            self.store.claim_audit(request.run_id, request.cycle_id)
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
        # Observers may request pause/stop during stage_started. Recheck the
        # boundary before creating a journal intent or scheduling the adapter.
        await self._wait_dispatch(request.run_id, limits)
        if operation is not None and self.store is not None:
            self.store.begin_operation(operation)
        while True:
            await self._wait_dispatch(request.run_id, limits)
            timeout = limits.checkpoint()

            async def invoke():
                # Check again after scheduling, immediately before invocation.
                await self._wait_dispatch(request.run_id, limits)
                return await adapter.run(request)

            try:
                result = await asyncio.wait_for(invoke(), timeout=timeout)
            except (_StopRequested, ExecutionLimitError, TimeoutError, sqlite3.Error):
                raise
            except RetryableReadError:
                # Never retry executor or audit dispatch, even for a transient error.
                if request.role != "planner":
                    raise _AdapterFailure("adapter_exception") from None
                if self._stop_requested(request.run_id):
                    raise _StopRequested
                if not await asyncio.wait_for(
                    self.pause.wait_until_resumed(lambda: self._stop_requested(request.run_id)),
                    timeout=limits.remaining(),
                ):
                    raise _StopRequested
                limits.reserve_retry()
                if self.store is not None:
                    self.store.append_event(request.run_id, {
                        "type": "retry_reserved", "role": request.role,
                        "cycle_id": request.cycle_id,
                        "retries_used": limits.record["retries_used"],
                    })
                self._emit("retry_reserved", run_id=request.run_id,
                           cycle_id=request.cycle_id, role=request.role,
                           retries_used=limits.record["retries_used"])
                continue
            except Exception:
                # Do not propagate adapter-provided exception text into logs or UI.
                raise _AdapterFailure("adapter_exception") from None
            break
        if self._stop_requested(request.run_id):
            raise _StopRequested
        limits.checkpoint()
        self._emit(
            "stage_completed",
            run_id=request.run_id,
            cycle_id=request.cycle_id,
            role=request.role,
            status=(result.status if type(result) is AgentResult
                    and type(result.status) is str and result.status in VALID_STATUSES
                    else "invalid"),
        )
        if self._stop_requested(request.run_id):
            raise _StopRequested
        limits.checkpoint()
        if operation is not None and self.store is not None:
            if isinstance(result, AgentResult) and result.status == "completed":
                try:
                    AgentResult(
                        status=result.status, payload=result.payload,
                        evidence=result.evidence, protocol_version=result.protocol_version,
                    )
                except (ValueError, TypeError):
                    raise _PermissionDenied("needs_reconciliation") from None
                write_status = result.payload.get("write_status")
                if result.protocol_version != "1.0" or not (
                    write_status == "confirmed"
                    or (operation.action == "read" and "write_status" not in result.payload)
                ):
                    raise _PermissionDenied("needs_reconciliation")
                # Simulated completion only. Real mutation verification is not enabled.
                marker = None
                if self.recovery_enabled and recovery_plan is not None:
                    marker = recovery_marker(self.initial_payload, recovery_plan, result, operation)
                self.store.confirm_operation(operation, recovery=marker)
        return result

    async def _wait_dispatch(self, run_id: str, limits: ExecutionLimits) -> None:
        if self._stop_requested(run_id):
            raise _StopRequested
        if self.pause.paused:
            if not await asyncio.wait_for(
                self.pause.wait_until_resumed(lambda: self._stop_requested(run_id)),
                timeout=limits.checkpoint(),
            ):
                raise _StopRequested
        limits.checkpoint()
        if self._stop_requested(run_id):
            raise _StopRequested

    def _observer_abort(self, run_id: str, cycle_id: int) -> CycleOutcome:
        self.state = OrchestratorState.FAILED
        outcome = CycleOutcome(self.state, reason="observer_failure")
        if self.store is not None:
            try:
                if self.store.dispatch_block_reason(run_id, cycle_id):
                    return outcome
            except (sqlite3.Error, ValueError, TypeError):
                return CycleOutcome(self.state, reason="persistence_failure")
        return self._finish(run_id, cycle_id, outcome)

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(redact({"type": event_type, **payload}))
        except Exception:
            raise _ObserverFailure("observer_failure") from None

    def _emit_terminal(self, event_type: str, **payload: Any) -> None:
        # A delivery failure cannot undo a committed terminal outcome.
        try:
            self._emit(event_type, **payload)
        except _ObserverFailure:
            if self.store is not None:
                try:
                    self.store.append_event(payload["run_id"], {
                        "type": "observer_failure", "phase": "terminal_notification"
                    })
                except Exception:
                    # No raw exception or recursive observer call.
                    pass

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
            if executor_result is not None:
                write_status = executor_result.payload.get("write_status")
                if type(write_status) is str and write_status in {
                    "confirmed", "unconfirmed", "needs_reconciliation"
                }:
                    checkpoint["write_status"] = write_status
            try:
                self.store.save_checkpoint(run_id, checkpoint)
            except sqlite3.Error:
                self.state = OrchestratorState.FAILED
                return CycleOutcome(
                    state=self.state, results=outcome.results, reason="persistence_failure"
                )
        return outcome
