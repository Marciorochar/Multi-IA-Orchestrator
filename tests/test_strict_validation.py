import asyncio
import pytest

from multi_ia.contracts import AgentRequest, AgentResult, canonical_plan
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.store import RunStore


def done(payload=None):
    return AgentResult("completed", payload or {}, ["fake:ok"])


class Adapter:
    def __init__(self, role, calls, result):
        self.role, self.calls, self.result = role, calls, result

    async def run(self, request):
        self.calls.append(self.role)
        return self.result


@pytest.mark.parametrize("changes", [
    {"protocol_version": "2.0"}, {"cycle_id": True}, {"role": "unknown"},
    {"payload": {"a": float("nan")}}, {"payload": {1: "ambiguous"}},
])
def test_request_contract_rejects_invalid_values(changes):
    fields = dict(run_id="r", cycle_id=1, role="planner", payload={})
    fields.update(changes)
    with pytest.raises(ValueError):
        AgentRequest(**fields)


@pytest.mark.parametrize("payload", [
    {"value": float("inf")}, {"value": {1: "integer key"}},
    {"value": (1, 2)}, {"value": object()},
])
def test_canonical_plan_rejects_non_json_values(payload):
    with pytest.raises(ValueError):
        canonical_plan(payload)


@pytest.mark.parametrize("role", ["planner", "executor", "auditor"])
def test_incompatible_result_protocol_fails_before_transition(tmp_path, role):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []
    results = [done(), done({"write_status": "confirmed"}), done({"approved": True})]
    index = ["planner", "executor", "auditor"].index(role)
    # Simulates a malformed response crossing an adapter boundary.
    object.__setattr__(results[index], "protocol_version", "2.0")
    try:
        engine = Orchestrator(*[Adapter(r, calls, value) for r, value in zip(
            ["planner", "executor", "auditor"], results)], store=store)
        outcome = asyncio.run(engine.run("r"))
        assert outcome.state is OrchestratorState.FAILED
        assert calls == ["planner", "executor", "auditor"][:index + 1]
        assert outcome.reason == "invalid_result"
        if role == "executor":
            assert store.list_operations("r")[0]["status"] == "pending"
        if role == "auditor":
            assert store.list_operations("r")[0]["status"] == "confirmed"
        assert store.load_checkpoint("r")["state"] == "failed"
    finally:
        store.close()


@pytest.mark.parametrize("field,value", [
    ("evidence", []), ("payload", {"nested": float("nan")}),
    ("payload", {"artifacts": None}), ("payload", {"tests": "not a list"}),
    ("payload", {"write_status": {"confirmed": True}}),
])
def test_invalid_executor_result_never_confirms_journal(tmp_path, field, value):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []
    result = done({"write_status": "confirmed"})
    object.__setattr__(result, field, value)
    try:
        engine = Orchestrator(
            Adapter("planner", calls, done()), Adapter("executor", calls, result),
            Adapter("auditor", calls, done({"approved": True})), store=store)
        outcome = asyncio.run(engine.run("r"))
        assert outcome.state is OrchestratorState.FAILED
        assert calls == ["planner", "executor"]
        assert store.list_operations("r")[0]["status"] == "pending"
        assert store.load_checkpoint("r")["write_status"] == "needs_reconciliation"
    finally:
        store.close()


def test_observer_cannot_replace_audit_decision_after_adapter_returns(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []
    audit = done({"approved": False})

    def observer(event):
        if event["type"] == "stage_completed" and event["role"] == "auditor":
            audit.payload["approved"] = True

    try:
        engine = Orchestrator(
            Adapter("planner", calls, done()), Adapter("executor", calls, done()),
            Adapter("auditor", calls, audit), store=store, event_sink=observer)
        outcome = asyncio.run(engine.run_cycle("r", 1))
        assert outcome.state is OrchestratorState.EVALUATING
        assert outcome.next_cycle is True
        assert outcome.results["auditor"].payload["approved"] is False
    finally:
        store.close()


def test_auditor_cannot_mutate_prior_results_through_request(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []
    class Auditor:
        async def run(self, request):
            request.payload["plan"]["plan"] = "different"
            request.payload["execution"]["write_status"] = "unconfirmed"
            return done({"approved": True})
    try:
        engine = Orchestrator(
            Adapter("planner", calls, done({"plan": "original"})),
            Adapter("executor", calls, done({"write_status": "confirmed"})),
            Auditor(), store=store)
        outcome = asyncio.run(engine.run("r"))
        assert outcome.state is OrchestratorState.COMPLETED
        assert outcome.results["planner"].payload["plan"] == "original"
        assert outcome.results["executor"].payload["write_status"] == "confirmed"
        assert store.load_checkpoint("r")["write_status"] == "confirmed"
    finally:
        store.close()


def test_direct_read_executor_cannot_report_uncertain_write_and_succeed():
    calls = []
    engine = Orchestrator(
        Adapter("planner", calls, done()),
        Adapter("executor", calls, done({"write_status": "unconfirmed"})),
        Adapter("auditor", calls, done({"approved": True})))
    outcome = asyncio.run(engine.run("r"))
    assert outcome.state is OrchestratorState.FAILED
    assert calls == ["planner", "executor"]


def test_inconsistent_audit_approval_and_explicit_failed_tests_cannot_complete():
    calls = []
    engine = Orchestrator(
        Adapter("planner", calls, done()), Adapter("executor", calls, done()),
        Adapter("auditor", calls, done({"approved": True, "tests_passed": False})))
    outcome = asyncio.run(engine.run("r"))
    assert outcome.state is OrchestratorState.FAILED
    assert outcome.reason == "invalid_audit_decision"
