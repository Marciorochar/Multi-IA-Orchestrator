import asyncio
import time
import subprocess
import sys

import pytest

from multi_ia.contracts import AgentResult, Operation
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.policies import ApprovalGate, ExecutionPolicy, PauseController
from multi_ia.store import RunStore


def done(payload=None):
    return AgentResult("completed", payload or {}, ["fake:ok"])


class Adapter:
    def __init__(self, role, calls, result, delay=0):
        self.role, self.calls, self.result, self.delay = role, calls, result, delay

    async def run(self, request):
        self.calls.append(self.role)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("kwargs", [
    {"max_seconds": float("nan")}, {"max_seconds": float("inf")},
    {"max_budget": float("nan")}, {"max_budget": float("inf")},
    {"max_cycles": True}, {"max_cycles": 1.5}, {"max_retries": True},
    {"max_retries": 1.5}, {"max_seconds": True}, {"max_budget": True},
])
def test_policy_rejects_invalid_limits(kwargs):
    params = dict(max_cycles=2, max_seconds=30, max_retries=1)
    params.update(kwargs)
    with pytest.raises(ValueError):
        ExecutionPolicy(**params)


def test_deadline_is_shared_by_stages():
    calls = []
    engine = Orchestrator(
        Adapter("planner", calls, done(), .04),
        Adapter("executor", calls, done(), .04),
        Adapter("auditor", calls, done({"approved": True})),
        policy=ExecutionPolicy(1, .06, 0),
    )
    result = asyncio.run(engine.run("r"))
    assert result.reason == "timeout"
    assert calls == ["planner", "executor"]


def test_pause_consumes_total_deadline():
    calls = []
    pause = PauseController()
    pause.request_pause()
    engine = Orchestrator(
        Adapter("planner", calls, done()),
        Adapter("executor", calls, done()),
        Adapter("auditor", calls, done({"approved": True})),
        policy=ExecutionPolicy(1, .03, 0), pause=pause,
    )
    async def scenario():
        return await asyncio.wait_for(engine.run("r"), timeout=1)
    result = asyncio.run(scenario())
    assert result.reason == "timeout"
    assert calls == []


def test_direct_cycle_cannot_bypass_cycle_limit():
    calls = []
    engine = Orchestrator(
        Adapter("planner", calls, done()),
        Adapter("executor", calls, done()),
        Adapter("auditor", calls, done({"approved": True})),
        policy=ExecutionPolicy(1, 30, 0),
    )
    result = asyncio.run(engine.run_cycle("r", 2))
    assert result.reason == "max_cycles"
    assert calls == []


def test_deadline_is_shared_across_cycles():
    calls = []
    engine = Orchestrator(
        Adapter("planner", calls, done(), .04),
        Adapter("executor", calls, done()),
        Adapter("auditor", calls, done({"approved": False})),
        policy=ExecutionPolicy(3, .06, 0),
    )
    result = asyncio.run(engine.run("r"))
    assert result.reason == "timeout"
    assert calls == ["planner", "executor", "auditor", "planner"]


@pytest.mark.parametrize("budget,reason", [(0, "budget_exhausted"), (1, "budget_unverified")])
def test_finite_budget_blocks_unknown_cost_before_dispatch(budget, reason):
    calls = []
    engine = Orchestrator(
        Adapter("planner", calls, done()),
        Adapter("executor", calls, done()),
        Adapter("auditor", calls, done({"approved": True})),
        policy=ExecutionPolicy(1, 30, 0, budget),
    )
    result = asyncio.run(engine.run("r"))
    assert result.reason == reason
    assert calls == []


def test_explicit_safe_planner_retry_is_bounded():
    from multi_ia.policies import RetryableReadError
    calls = []
    class TransientPlanner:
        async def run(self, request):
            calls.append("planner")
            if calls.count("planner") == 1:
                raise RetryableReadError("transient")
            return done()
    engine = Orchestrator(
        TransientPlanner(), Adapter("executor", calls, done()),
        Adapter("auditor", calls, done({"approved": True})),
        policy=ExecutionPolicy(1, 30, 1),
    )
    assert asyncio.run(engine.run("r")).state is OrchestratorState.COMPLETED
    assert calls == ["planner", "planner", "executor", "auditor"]


def test_retry_limit_is_global_across_cycles():
    from multi_ia.policies import RetryableReadError
    calls = []
    class Planner:
        async def run(self, request):
            calls.append("planner")
            if calls.count("planner") in {1, 3}:
                raise RetryableReadError("transient")
            return done()
    engine = Orchestrator(
        Planner(), Adapter("executor", calls, done()),
        Adapter("auditor", calls, done({"approved": False})),
        policy=ExecutionPolicy(3, 30, 1),
    )
    assert asyncio.run(engine.run("r")).reason == "retry_exhausted"
    assert calls == ["planner", "planner", "executor", "auditor", "planner"]


def test_never_retry_executor_even_on_transient_error(tmp_path):
    from multi_ia.policies import RetryableReadError
    calls = []
    plan = {"operation": {"version": "1.0", "operation_id": "op", "run_id": "r",
            "cycle_id": 1, "scope": "simulation", "effect": "simulated", "action": "write_workspace"}}
    gate = ApprovalGate()
    gate.approve(Operation.from_plan(plan, run_id="r", cycle_id=1), approved=True)
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        engine = Orchestrator(
            Adapter("planner", calls, done(plan)),
            Adapter("executor", calls, RetryableReadError("transient")),
            Adapter("auditor", calls, done({"approved": True})),
            policy=ExecutionPolicy(1, 30, 3), store=store, approval_gate=gate,
        )
        assert asyncio.run(engine.run("r")).state is OrchestratorState.FAILED
        assert calls == ["planner", "executor"]
        assert store.load_checkpoint("r")["write_status"] == "needs_reconciliation"
    finally:
        store.close()


def test_service_builtin_fake_budget_has_explicit_zero_cost(tmp_path):
    from multi_ia.service import ExecutionService
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        service = ExecutionService(store, goal="demo", repo=str(tmp_path),
                                   policy=ExecutionPolicy(1, 30, 0, 1))
        assert asyncio.run(service.start("r")).state is OrchestratorState.COMPLETED
        limits = store.load_limits("r")
        assert limits["spent"] == 0
        assert limits["cost_source"] == "builtin_fake_zero"
        assert limits["budget"] == 1
    finally:
        store.close()


def test_resume_does_not_reset_expired_deadline(tmp_path):
    from multi_ia.service import ExecutionService
    db = tmp_path / "runs.sqlite"
    code = """
import asyncio, os, sys
from multi_ia.store import RunStore
from multi_ia.service import ExecutionService
from multi_ia.policies import ExecutionPolicy
class CrashStore(RunStore):
    def confirm_operation(self, *args, **kwargs):
        super().confirm_operation(*args, **kwargs)
        os._exit(73)
service = ExecutionService(CrashStore(sys.argv[1]), goal="demo", repo=sys.argv[2],
                           policy=ExecutionPolicy(1, 1, 0))
asyncio.run(service.start("r"))
"""
    child = subprocess.run([sys.executable, "-c", code, str(db), str(tmp_path)],
                           capture_output=True, text=True, timeout=10)
    assert child.returncode == 73, child.stderr
    time.sleep(1.05)
    store = RunStore(db)
    try:
        events = []
        service = ExecutionService(store, goal="demo", repo=str(tmp_path),
                                   policy=ExecutionPolicy(1, 1, 0), event_sink=events.append)
        before = store.load_checkpoint("r")
        assert asyncio.run(service.resume("r")).reason == "timeout"
        assert events == []
        assert store.load_checkpoint("r") == before
    finally:
        store.close()
