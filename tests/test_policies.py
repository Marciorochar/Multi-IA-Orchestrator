import asyncio

from multi_ia.contracts import AgentResult
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.policies import ApprovalGate, CancellationController, ExecutionPolicy


class StaticAdapter:
    def __init__(self, result, calls=None, name="adapter"):
        self.result = result
        self.calls = calls
        self.name = name

    async def run(self, request):
        if self.calls is not None:
            self.calls.append(self.name)
        return self.result


class CancellingAdapter(StaticAdapter):
    def __init__(self, controller, result, calls=None, name="planner", delay=0):
        super().__init__(result, calls, name)
        self.controller = controller
        self.delay = delay

    async def run(self, request):
        if self.calls is not None:
            self.calls.append(self.name)
        self.controller.request_stop()
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


class SlowAdapter:
    async def run(self, request):
        await asyncio.sleep(0.05)
        return AgentResult(status="completed", payload={}, evidence=["late"])


def ok(payload=None):
    return AgentResult(status="completed", payload=payload or {}, evidence=["ok"])


def test_stop_prevents_next_agent():
    calls = []
    cancellation = CancellationController()
    orchestrator = Orchestrator(
        CancellingAdapter(cancellation, ok({"plan": "p"}), calls),
        StaticAdapter(ok(), calls, "executor"),
        StaticAdapter(ok({"approved": True}), calls, "auditor"),
        cancellation=cancellation,
    )

    outcome = asyncio.run(orchestrator.run_cycle("run-1", 1))

    assert calls == ["planner"]
    assert outcome.state is OrchestratorState.CANCELLED


def test_late_response_cannot_resume_cancelled_run():
    calls = []
    cancellation = CancellationController()
    orchestrator = Orchestrator(
        CancellingAdapter(cancellation, ok({"plan": "late"}), calls, delay=0.01),
        StaticAdapter(ok(), calls, "executor"),
        StaticAdapter(ok({"approved": True}), calls, "auditor"),
        cancellation=cancellation,
    )

    outcome = asyncio.run(orchestrator.run_cycle("run-1", 1))

    assert calls == ["planner"]
    assert outcome.state is OrchestratorState.CANCELLED
    assert outcome.reason == "stop_requested"


def test_timeout_sets_failed_state():
    policy = ExecutionPolicy(max_cycles=3, max_seconds=0.01, max_retries=1)
    orchestrator = Orchestrator(
        SlowAdapter(),
        StaticAdapter(ok()),
        StaticAdapter(ok({"approved": True})),
        policy=policy,
    )

    outcome = asyncio.run(orchestrator.run_cycle("run-1", 1))

    assert outcome.state is OrchestratorState.FAILED
    assert outcome.reason == "timeout"


def test_dangerous_action_requires_approval():
    gate = ApprovalGate(dangerous_actions={"publish"})

    assert gate.check("read") is True
    assert gate.check("publish") is False
    gate.approve("publish")
    assert gate.check("publish") is True


def test_stop_while_paused_cancels_without_resume():
    from multi_ia.policies import PauseController

    calls = []
    cancellation = CancellationController()
    pause = PauseController()

    class PausingPlanner(StaticAdapter):
        async def run(self, request):
            self.calls.append(self.name)
            pause.request_pause()
            return self.result

    orchestrator = Orchestrator(
        PausingPlanner(ok({"plan": "p"}), calls, "planner"),
        StaticAdapter(ok(), calls, "executor"),
        StaticAdapter(ok({"approved": True}), calls, "auditor"),
        cancellation=cancellation,
        pause=pause,
    )

    async def scenario():
        task = asyncio.create_task(orchestrator.run_cycle("run-stop-pause", 1))
        await asyncio.sleep(0.03)
        cancellation.request_stop()
        return await asyncio.wait_for(task, timeout=1)

    outcome = asyncio.run(scenario())

    assert calls == ["planner"]
    assert outcome.state is OrchestratorState.CANCELLED
    assert outcome.reason == "stop_requested"


def operation_plan(action="write_workspace", **overrides):
    declaration = {
        "version": "1.0", "operation_id": "op-1", "run_id": "run-1",
        "cycle_id": 1, "scope": "simulation", "effect": "simulated",
        "action": action,
    }
    declaration.update(overrides)
    return {"plan": "change A", "operation": declaration}


def run_permission_case(plan, gate=None, execution_mode="fake"):
    calls = []
    kwargs = {"execution_mode": execution_mode}
    if gate is not None:
        kwargs["approval_gate"] = gate
    engine = Orchestrator(
        StaticAdapter(ok(plan), calls, "planner"),
        StaticAdapter(ok(), calls, "executor"),
        StaticAdapter(ok({"approved": True}), calls, "auditor"),
        **kwargs,
    )
    outcome = asyncio.run(engine.run_cycle("run-1", 1))
    return calls, outcome


def test_unknown_legacy_action_is_denied():
    assert ApprovalGate().check("unknown_action") is False


def test_engine_denies_unknown_and_unapproved_actions():
    for action in ("unknown", "write_workspace", "delete", "publish", "production", "write_external"):
        calls, outcome = run_permission_case(operation_plan(action))
        assert calls == ["planner"]
        assert outcome.state is OrchestratorState.FAILED


def test_engine_rejects_malformed_and_mismatched_declarations():
    invalid = [
        {"operation": None},
        {"operation": {}},
        operation_plan(version="2.0"),
        operation_plan(run_id="another"),
        operation_plan(cycle_id=True),
        operation_plan(cycle_id=2),
        operation_plan(scope="../other"),
        operation_plan(effect="real"),
        operation_plan(operation_id=""),
        operation_plan(approved=True),
        {"actions": ["publish"]},
    ]
    for plan in invalid:
        calls, outcome = run_permission_case(plan)
        assert calls == ["planner"]
        assert outcome.state is OrchestratorState.FAILED


def test_scoped_approval_is_single_use_and_bound_to_plan_content():
    from multi_ia.contracts import Operation

    gate = ApprovalGate()
    plan = operation_plan()
    op = Operation.from_plan(plan, run_id="run-1", cycle_id=1)
    gate.approve(op, approved=True)

    changed = operation_plan()
    changed["plan"] = "change B"
    calls, outcome = run_permission_case(changed, gate)
    assert calls == ["planner"]
    assert outcome.state is OrchestratorState.FAILED

    calls, outcome = run_permission_case(plan, gate)
    assert calls == ["planner", "executor", "auditor"]
    assert outcome.state is OrchestratorState.COMPLETED

    calls, outcome = run_permission_case(plan, gate)
    assert calls == ["planner"]
    assert outcome.state is OrchestratorState.FAILED


def test_approval_is_not_transferable_to_another_operation_or_cycle():
    from multi_ia.contracts import Operation

    gate = ApprovalGate()
    original = Operation.from_plan(operation_plan(), run_id="run-1", cycle_id=1)
    gate.approve(original, approved=True)
    other = Operation.from_plan(operation_plan(operation_id="op-2"), run_id="run-1", cycle_id=1)
    next_cycle = Operation.from_plan(operation_plan(cycle_id=2), run_id="run-1", cycle_id=2)
    other_run = Operation.from_plan(operation_plan(run_id="run-2"), run_id="run-2", cycle_id=1)
    for op in (other, next_cycle, other_run):
        assert gate.authorize(op) is not None
    assert gate.authorize(original) is None


def test_human_approval_cannot_enable_external_actions_or_real_mode():
    import pytest
    from multi_ia.contracts import Operation

    gate = ApprovalGate()
    for action in ("publish", "production", "write_external"):
        plan = operation_plan(action)
        op = Operation.from_plan(plan, run_id="run-1", cycle_id=1)
        with pytest.raises(ValueError):
            gate.approve(op, approved=True)
        calls, outcome = run_permission_case(plan, gate)
        assert calls == ["planner"]
        assert outcome.state is OrchestratorState.FAILED
    calls, outcome = run_permission_case(operation_plan("read"), execution_mode="real")
    assert calls == []
    assert outcome.state is OrchestratorState.FAILED


def test_planner_cannot_grant_its_own_approval():
    plan = operation_plan()
    plan["approved"] = True
    calls, outcome = run_permission_case(plan)
    assert calls == ["planner"]
    assert outcome.state is OrchestratorState.FAILED


def test_approval_requires_literal_human_decision():
    import pytest
    from multi_ia.contracts import Operation

    gate = ApprovalGate()
    op = Operation.from_plan(operation_plan(), run_id="run-1", cycle_id=1)
    for decision in (1, "true", None):
        with pytest.raises(ValueError):
            gate.approve(op, approved=decision)
    gate.approve(op, approved=False)
    assert gate.authorize(op) is not None


def test_read_simulation_does_not_need_mutation_approval():
    calls, outcome = run_permission_case(operation_plan("read"))
    assert calls == ["planner", "executor", "auditor"]
    assert outcome.state is OrchestratorState.COMPLETED
