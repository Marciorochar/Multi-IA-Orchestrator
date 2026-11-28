import asyncio
import sqlite3
import subprocess
import sys

import pytest

from multi_ia.contracts import AgentResult, Operation
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.policies import ApprovalGate, CancellationController, ExecutionPolicy
from multi_ia.store import RunStore


def plan(cycle=1, action="write_workspace"):
    return {"plan": "simulated change", "operation": {
        "version": "1.0", "operation_id": "op-1", "run_id": "run-1",
        "cycle_id": cycle, "scope": "simulation", "effect": "simulated",
        "action": action,
    }}


def operation(cycle=1):
    return Operation.from_plan(plan(cycle), run_id="run-1", cycle_id=cycle)


def done(payload=None):
    return AgentResult(status="completed", payload=payload or {}, evidence=["fake:ok"])


class Adapter:
    def __init__(self, result, calls, role):
        self.result = result
        self.calls = calls
        self.role = role

    async def run(self, request):
        self.calls.append(self.role)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def make_engine(store, executor, calls, cycle=1, **kwargs):
    gate = ApprovalGate()
    gate.approve(operation(cycle), approved=True)
    return Orchestrator(
        Adapter(done(plan(cycle)), calls, "planner"), executor,
        Adapter(done({"approved": True}), calls, "auditor"),
        store=store, approval_gate=gate, **kwargs,
    )


def test_intent_is_durable_before_executor_and_confirmation_is_atomic(tmp_path):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    calls = []

    class InspectingExecutor:
        async def run(self, request):
            calls.append("executor")
            other = RunStore(db)
            try:
                checkpoint = other.load_checkpoint("run-1")
                assert checkpoint["write_status"] == "needs_reconciliation"
                assert checkpoint["state"] == "executing"
                assert other.list_operations("run-1")[0]["status"] == "pending"
            finally:
                other.close()
            return done({"write_status": "confirmed"})

    try:
        result = asyncio.run(make_engine(store, InspectingExecutor(), calls).run_cycle("run-1", 1))
        assert result.state is OrchestratorState.COMPLETED
        assert calls == ["planner", "executor", "auditor"]
        entries = store.list_operations("run-1")
        assert len(entries) == 1
        assert entries[0]["status"] == "confirmed"
        assert entries[0]["plan_digest"] == operation().plan_digest
        assert store.load_checkpoint("run-1")["write_status"] == "confirmed"
        types = [event.get("type") for event in store.list_events("run-1")]
        assert types.index("write_intent") < types.index("write_confirmed")
    finally:
        store.close()


@pytest.mark.parametrize("result", [
    RuntimeError("fake secret"), {"status": "completed"},
    AgentResult(status="running", payload={}, evidence=[]),
    done({"write_status": "unconfirmed"}), done(),
])
def test_uncertain_executor_never_clears_intent_or_runs_auditor(tmp_path, result):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []
    try:
        outcome = asyncio.run(make_engine(store, Adapter(result, calls, "executor"), calls).run_cycle("run-1", 1))
        assert outcome.state is OrchestratorState.FAILED
        assert calls == ["planner", "executor"]
        assert store.load_checkpoint("run-1")["write_status"] == "needs_reconciliation"
        assert store.list_operations("run-1")[0]["status"] == "pending"
        store.save_checkpoint("run-1", {"state": "completed", "write_status": "confirmed"})
        assert store.load_checkpoint("run-1")["write_status"] == "needs_reconciliation"
    finally:
        store.close()


def test_timeout_after_intent_preserves_pending_write(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []

    class SlowExecutor:
        async def run(self, request):
            calls.append("executor")
            await asyncio.sleep(1)

    try:
        engine = make_engine(store, SlowExecutor(), calls,
                             policy=ExecutionPolicy(1, 0.03, 0))
        outcome = asyncio.run(engine.run_cycle("run-1", 1))
        assert outcome.reason == "timeout"
        assert calls == ["planner", "executor"]
        assert store.load_checkpoint("run-1")["write_status"] == "needs_reconciliation"
    finally:
        store.close()


def test_stop_after_dispatch_keeps_intent_pending(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []
    cancellation = CancellationController()

    class StoppingExecutor:
        async def run(self, request):
            calls.append("executor")
            cancellation.request_stop()
            return done({"write_status": "confirmed"})

    try:
        outcome = asyncio.run(make_engine(store, StoppingExecutor(), calls,
                                          cancellation=cancellation).run_cycle("run-1", 1))
        assert outcome.state is OrchestratorState.CANCELLED
        assert calls == ["planner", "executor"]
        assert store.load_checkpoint("run-1")["write_status"] == "needs_reconciliation"
    finally:
        store.close()


def test_crash_after_dispatch_blocks_replay_after_reopen(tmp_path):
    db = tmp_path / "runs.sqlite"
    code = """
import asyncio, os, sys
from multi_ia.contracts import AgentResult, Operation
from multi_ia.engine import Orchestrator
from multi_ia.policies import ApprovalGate
from multi_ia.store import RunStore
plan = {"plan": "simulated change", "operation": {
    "version": "1.0", "operation_id": "op-1", "run_id": "run-1",
    "cycle_id": 1, "scope": "simulation", "effect": "simulated",
    "action": "write_workspace"}}
class Planner:
    async def run(self, request):
        return AgentResult(status="completed", payload=plan, evidence=["fake:ok"])
class Crash:
    async def run(self, request):
        os._exit(71)
gate = ApprovalGate()
gate.approve(Operation.from_plan(plan, run_id="run-1", cycle_id=1), approved=True)
asyncio.run(Orchestrator(Planner(), Crash(), Crash(), store=RunStore(sys.argv[1]),
                         approval_gate=gate).run_cycle("run-1", 1))
"""
    child = subprocess.run([sys.executable, "-c", code, str(db)], timeout=20)
    assert child.returncode == 71
    store = RunStore(db)
    calls = []
    try:
        checkpoint = store.load_checkpoint("run-1")
        assert checkpoint["write_status"] == "needs_reconciliation"
        outcome = asyncio.run(make_engine(store, Adapter(done(), calls, "executor"), calls).run_cycle("run-1", 1))
        assert outcome.state is OrchestratorState.FAILED
        assert outcome.reason == "needs_reconciliation"
        assert calls == []
        assert store.load_checkpoint("run-1") == checkpoint
    finally:
        store.close()


def test_confirmed_operation_cannot_be_dispatched_twice_after_reopen(tmp_path):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    calls = []
    outcome = asyncio.run(make_engine(store, Adapter(done({"write_status": "confirmed"}), calls, "executor"), calls).run_cycle("run-1", 1))
    assert outcome.state is OrchestratorState.COMPLETED
    store.close()
    reopened = RunStore(db)
    calls.clear()
    try:
        checkpoint = reopened.load_checkpoint("run-1")
        outcome = asyncio.run(make_engine(reopened, Adapter(done(), calls, "executor"), calls).run_cycle("run-1", 1))
        assert outcome.reason == "operation_replayed"
        assert calls == []
        assert reopened.load_checkpoint("run-1") == checkpoint
    finally:
        reopened.close()


def test_intent_transaction_rolls_back_if_checkpoint_cannot_be_saved(tmp_path):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON checkpoints BEGIN SELECT RAISE(ABORT, 'injected'); END")
    calls = []
    try:
        outcome = asyncio.run(make_engine(store, Adapter(done(), calls, "executor"), calls).run_cycle("run-1", 1))
        assert outcome.state is OrchestratorState.FAILED
        assert outcome.reason == "persistence_failure"
        assert calls == ["planner"]
        assert store.list_operations("run-1") == []
        assert not any(e.get("type") == "write_intent" for e in store.list_events("run-1"))
    finally:
        store.close()


def test_mutation_requires_durable_store(tmp_path):
    calls = []
    outcome = asyncio.run(make_engine(None, Adapter(done(), calls, "executor"), calls).run_cycle("run-1", 1))
    assert outcome.reason == "journal_required"
    assert calls == ["planner"]


def test_two_connections_cannot_reserve_same_run_cycle(tmp_path):
    db = tmp_path / "runs.sqlite"
    one, two = RunStore(db), RunStore(db)
    try:
        one.begin_operation(operation())
        with pytest.raises(RuntimeError, match="needs_reconciliation"):
            two.begin_operation(operation())
        assert len(two.list_operations("run-1")) == 1
    finally:
        one.close()
        two.close()


def test_legacy_uncertainty_cannot_be_erased_by_generic_checkpoint(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        store.save_checkpoint("run-1", {"state": "executing", "write_status": "unconfirmed"})
        store.save_checkpoint("run-1", {"state": "failed"})
        assert store.load_checkpoint("run-1")["write_status"] == "needs_reconciliation"
    finally:
        store.close()
