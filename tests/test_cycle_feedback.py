import asyncio
from contextlib import closing

from multi_ia.adapters.fake import FakeAdapter
from multi_ia.contracts import AgentResult
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.policies import ExecutionPolicy
from multi_ia.service import ExecutionService
from multi_ia.store import RunStore


def completed(payload, evidence):
    return AgentResult("completed", payload, evidence)


def scripted_adapters(requests, audits, execution=None):
    """Planner and executor are constant; the auditor follows the given script."""
    script = iter(audits)

    def plan(request):
        requests.append(request.payload)
        return completed({"step": "x"}, ["planner:ok"])

    return (
        FakeAdapter(plan),
        FakeAdapter(lambda request: completed(
            {"write_status": "confirmed", **(execution or {})},
            [f"executor:{request.cycle_id}"],
        )),
        FakeAdapter(lambda request: completed(next(script), [f"auditor:{request.cycle_id}"])),
    )


def run(adapters, max_cycles=3):
    engine = Orchestrator(
        *adapters,
        policy=ExecutionPolicy(max_cycles=max_cycles, max_seconds=30, max_retries=0),
        initial_payload={"goal": "g"},
    )
    return asyncio.run(engine.run("run-feedback"))


def test_planner_receives_previous_cycle_summary_after_rejection():
    requests = []
    adapters = scripted_adapters(
        requests,
        [{"approved": False, "tests_passed": False, "feedback": "fix the tests"},
         {"approved": True}],
        execution={"artifacts": ["a.py"], "tests": ["pytest: 1 failed"]},
    )

    outcome = run(adapters)

    assert outcome.state is OrchestratorState.COMPLETED
    assert requests[0] == {"goal": "g"}
    assert requests[1] == {
        "goal": "g",
        "previous_cycle": {
            "cycle_id": 1,
            "execution": {
                "evidence": ["executor:1"],
                "artifacts": ["a.py"],
                "tests": ["pytest: 1 failed"],
                "write_status": "confirmed",
            },
            "audit": {
                "evidence": ["auditor:1"],
                "approved": False,
                "tests_passed": False,
                "feedback": "fix the tests",
            },
            "truncated": False,
        },
    }


def test_only_the_latest_cycle_is_forwarded():
    requests = []
    adapters = scripted_adapters(
        requests,
        [{"approved": False, "feedback": "first"},
         {"approved": False, "feedback": "second"},
         {"approved": True}],
    )

    run(adapters)

    assert requests[2]["previous_cycle"]["cycle_id"] == 2
    assert requests[2]["previous_cycle"]["audit"]["feedback"] == "second"
    assert "first" not in str(requests[2])


def test_unlisted_fields_are_not_forwarded():
    requests = []
    adapters = scripted_adapters(
        requests,
        [{"approved": False, "raw_output": "audit dump"}, {"approved": True}],
        execution={"raw_output": "execution dump"},
    )

    run(adapters)

    assert "dump" not in str(requests[1])
    assert "raw_output" not in str(requests[1])


def test_forwarded_text_is_redacted_and_bounded():
    requests = []
    adapters = scripted_adapters(
        requests,
        [{"approved": False,
          "feedback": "password=hunter2 " + "x" * 5000},
         {"approved": True}],
        execution={"artifacts": [f"file-{index}" for index in range(50)]},
    )

    run(adapters)

    previous = requests[1]["previous_cycle"]
    assert "hunter2" not in str(previous)
    assert len(previous["audit"]["feedback"]) == 2000
    assert len(previous["execution"]["artifacts"]) == 20
    assert previous["truncated"] is True


def test_non_text_feedback_fails_closed():
    requests = []
    adapters = scripted_adapters(
        requests, [{"approved": False, "feedback": {"nested": "data"}}]
    )

    outcome = run(adapters)

    assert outcome.state is OrchestratorState.FAILED
    assert outcome.reason == "invalid_audit_decision"
    assert len(requests) == 1


def test_service_forwards_feedback_between_durable_cycles(tmp_path):
    requests = []
    adapters = scripted_adapters(
        requests, [{"approved": False, "feedback": "again"}, {"approved": True}]
    )

    with closing(RunStore(tmp_path / "runs.sqlite")) as store:
        service = ExecutionService(
            store, goal="g", repo=str(tmp_path),
            policy=ExecutionPolicy(max_cycles=2, max_seconds=30, max_retries=0),
            adapters=adapters,
        )
        outcome = asyncio.run(service.start("durable"))
        persisted = str(store.list_events("durable")) + str(store.load_checkpoint("durable"))

    assert outcome.state is OrchestratorState.COMPLETED
    assert requests[1]["previous_cycle"]["audit"]["feedback"] == "again"
    # The summary travels in memory only; it is not written to the database.
    assert "again" not in persisted
