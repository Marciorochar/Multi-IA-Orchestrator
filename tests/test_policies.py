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
