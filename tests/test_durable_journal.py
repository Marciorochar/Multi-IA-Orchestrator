"""Mutating operations need a journal that survives the process."""
import asyncio

import pytest

from multi_ia.contracts import AgentResult, Operation
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.policies import ApprovalGate
from multi_ia.store import RunStore


def done(payload=None):
    return AgentResult("completed", payload or {}, ["fake:ok"])


class Static:
    def __init__(self, result, calls, role):
        self.result, self.calls, self.role = result, calls, role

    async def run(self, request):
        self.calls.append(self.role)
        return self.result


def run_approved(store, action):
    calls = []
    plan = {"operation": {
        "version": "1.0", "operation_id": "op-1", "run_id": "run-1",
        "cycle_id": 1, "scope": "simulation", "effect": "simulated",
        "action": action,
    }}
    gate = ApprovalGate()
    operation = Operation.from_plan(plan, run_id="run-1", cycle_id=1)
    gate.approve(operation, approved=True)
    engine = Orchestrator(
        Static(done(plan), calls, "planner"),
        Static(done({"write_status": "confirmed"}), calls, "executor"),
        Static(done({"approved": True}), calls, "auditor"),
        store=store, approval_gate=gate,
    )
    return asyncio.run(engine.run_cycle("run-1", 1)), calls, gate, operation


@pytest.mark.parametrize("path", [":memory:", ""])
@pytest.mark.parametrize("action", ["write_workspace", "delete"])
def test_non_durable_store_never_dispatches_a_mutation(path, action):
    store = RunStore(path)
    try:
        outcome, calls, gate, operation = run_approved(store, action)
        journal = store.list_operations("run-1")
    finally:
        store.close()

    assert outcome.state is OrchestratorState.FAILED
    assert outcome.reason == "journal_required"
    assert calls == ["planner"]
    assert journal == []
    # The refusal happens before the one-use approval is consumed.
    assert gate.authorize(operation) is None


def test_file_store_still_dispatches_an_approved_mutation(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        outcome, calls, _, _ = run_approved(store, "write_workspace")
    finally:
        store.close()

    assert outcome.state is OrchestratorState.COMPLETED
    assert calls == ["planner", "executor", "auditor"]


@pytest.mark.parametrize("name,durable", [
    (":memory:", False), ("", False), ("runs.sqlite", True),
])
def test_store_reports_whether_it_is_durable(tmp_path, monkeypatch, name, durable):
    monkeypatch.chdir(tmp_path)
    store = RunStore(name)
    try:
        assert store.durable is durable
    finally:
        store.close()
