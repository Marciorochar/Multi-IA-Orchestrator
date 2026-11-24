import asyncio

from multi_ia.contracts import AgentResult
from multi_ia.engine import Orchestrator, OrchestratorState


class RecordingAdapter:
    def __init__(self, name, calls, result):
        self.name = name
        self.calls = calls
        self.result = result

    async def run(self, request):
        self.calls.append(self.name)
        return self.result


def completed(payload=None, evidence=None):
    return AgentResult(
        status="completed",
        payload=payload or {},
        evidence=evidence or ["ok"],
    )


def test_agents_run_in_order():
    calls = []
    orchestrator = Orchestrator(
        RecordingAdapter("planner", calls, completed({"plan": "p"})),
        RecordingAdapter("executor", calls, completed({"result": "r"})),
        RecordingAdapter("auditor", calls, completed({"approved": True})),
    )

    outcome = asyncio.run(orchestrator.run_cycle("run-1", 1))

    assert calls == ["planner", "executor", "auditor"]
    assert outcome.state is OrchestratorState.COMPLETED


def test_partial_result_does_not_advance():
    calls = []
    orchestrator = Orchestrator(
        RecordingAdapter(
            "planner",
            calls,
            AgentResult(status="running", payload={}, evidence=[]),
        ),
        RecordingAdapter("executor", calls, completed()),
        RecordingAdapter("auditor", calls, completed({"approved": True})),
    )

    outcome = asyncio.run(orchestrator.run_cycle("run-1", 1))

    assert calls == ["planner"]
    assert outcome.state is OrchestratorState.FAILED
    assert outcome.reason == "non_terminal_result"


def test_malformed_result_fails_closed():
    calls = []
    orchestrator = Orchestrator(
        RecordingAdapter("planner", calls, {"status": "completed"}),
        RecordingAdapter("executor", calls, completed()),
        RecordingAdapter("auditor", calls, completed({"approved": True})),
    )

    outcome = asyncio.run(orchestrator.run_cycle("run-1", 1))

    assert calls == ["planner"]
    assert outcome.state is OrchestratorState.FAILED
    assert outcome.reason == "malformed_result"


def test_audit_rejection_requests_next_cycle():
    calls = []
    orchestrator = Orchestrator(
        RecordingAdapter("planner", calls, completed({"plan": "p"})),
        RecordingAdapter("executor", calls, completed({"result": "r"})),
        RecordingAdapter("auditor", calls, completed({"approved": False})),
    )

    outcome = asyncio.run(orchestrator.run_cycle("run-1", 1))

    assert calls == ["planner", "executor", "auditor"]
    assert outcome.state is OrchestratorState.EVALUATING
    assert outcome.next_cycle is True



def test_run_honors_max_cycles():
    from multi_ia.policies import ExecutionPolicy

    calls = []
    orchestrator = Orchestrator(
        RecordingAdapter("planner", calls, completed({"plan": "p"})),
        RecordingAdapter("executor", calls, completed({"result": "r"})),
        RecordingAdapter("auditor", calls, completed({"approved": False})),
        policy=ExecutionPolicy(max_cycles=2, max_seconds=30, max_retries=0),
    )

    outcome = asyncio.run(orchestrator.run("run-1"))

    assert calls == ["planner", "executor", "auditor"] * 2
    assert outcome.state is OrchestratorState.PAUSED
    assert outcome.reason == "max_cycles"


def test_run_emits_stage_events_in_order():
    events = []
    calls = []
    orchestrator = Orchestrator(
        RecordingAdapter("planner", calls, completed({"plan": "p"})),
        RecordingAdapter("executor", calls, completed({"result": "r"})),
        RecordingAdapter("auditor", calls, completed({"approved": True})),
        event_sink=events.append,
    )

    outcome = asyncio.run(orchestrator.run("run-events"))

    assert outcome.state is OrchestratorState.COMPLETED
    assert [event["type"] for event in events] == [
        "run_started",
        "cycle_started",
        "stage_started",
        "stage_completed",
        "stage_started",
        "stage_completed",
        "stage_started",
        "stage_completed",
        "state_changed",
        "run_completed",
    ]
    assert [
        event["role"]
        for event in events
        if event["type"] == "stage_started"
    ] == ["planner", "executor", "auditor"]


def test_pause_holds_before_next_stage():
    from multi_ia.policies import PauseController

    calls = []
    pause = PauseController()

    class PausingPlanner(RecordingAdapter):
        async def run(self, request):
            self.calls.append(self.name)
            pause.request_pause()
            return self.result

    orchestrator = Orchestrator(
        PausingPlanner("planner", calls, completed({"plan": "p"})),
        RecordingAdapter("executor", calls, completed({"result": "r"})),
        RecordingAdapter("auditor", calls, completed({"approved": True})),
        pause=pause,
    )

    async def scenario():
        task = asyncio.create_task(orchestrator.run_cycle("run-pause", 1))
        await asyncio.sleep(0.03)
        assert calls == ["planner"]
        pause.resume()
        return await asyncio.wait_for(task, timeout=1)

    outcome = asyncio.run(scenario())

    assert outcome.state is OrchestratorState.COMPLETED
    assert calls == ["planner", "executor", "auditor"]
