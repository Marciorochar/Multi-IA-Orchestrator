import asyncio

import pytest

from multi_ia.contracts import AgentResult
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.store import RunStore


def completed(payload):
    return AgentResult(status="completed", payload=payload, evidence=["fake:ok"])


class Adapter:
    def __init__(self, result, name, calls):
        self.result = result
        self.name = name
        self.calls = calls

    async def run(self, request):
        self.calls.append(self.name)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("payload", [{}, {"approved": None}, {"approved": "true"}, {"approved": 1}, {"approved": 0}])
def test_auditor_requires_explicit_boolean_approval(payload, tmp_path):
    calls = []
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        engine = Orchestrator(
            Adapter(completed({}), "planner", calls),
            Adapter(completed({}), "executor", calls),
            Adapter(completed(payload), "auditor", calls),
            store=store,
        )
        result = asyncio.run(engine.run("invalid-audit"))
        assert result.state is OrchestratorState.FAILED
        assert result.reason == "invalid_audit_decision"
        assert result.next_cycle is False
        assert store.load_checkpoint("invalid-audit")["state"] == "failed"
        assert calls == ["planner", "executor", "auditor"]
    finally:
        store.close()


def test_adapter_exception_fails_closed_without_raw_secret(tmp_path):
    calls = []
    events = []
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        engine = Orchestrator(
            Adapter(RuntimeError("secret=DO_NOT_PERSIST"), "planner", calls),
            Adapter(completed({}), "executor", calls),
            Adapter(completed({"approved": True}), "auditor", calls),
            store=store,
            event_sink=events.append,
        )
        result = asyncio.run(engine.run("adapter-error"))
        assert result.state is OrchestratorState.FAILED
        assert result.reason == "adapter_exception"
        assert calls == ["planner"]
        assert store.load_checkpoint("adapter-error")["state"] == "failed"
        assert "DO_NOT_PERSIST" not in str(events)
        assert "DO_NOT_PERSIST" not in str(store.list_events("adapter-error"))
        assert "DO_NOT_PERSIST" not in str(store.load_checkpoint("adapter-error"))
    finally:
        store.close()
