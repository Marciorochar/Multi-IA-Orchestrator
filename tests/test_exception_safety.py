import asyncio
import json
import sqlite3
import threading

import pytest

from multi_ia.contracts import AgentResult
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.policies import CancellationController, PauseController
from multi_ia.store import RunStore


SECRET = "TEST_SECRET_987654321"


def done(payload=None):
    return AgentResult("completed", payload or {}, ["fake:ok"])


class Adapter:
    def __init__(self, role, calls, result):
        self.role, self.calls, self.result = role, calls, result

    async def run(self, request):
        self.calls.append(self.role)
        return self.result


def engine(store, calls, *, sink=None, executor=None, **kwargs):
    return Orchestrator(
        Adapter("planner", calls, done()),
        executor or Adapter("executor", calls, done({"write_status": "confirmed"})),
        Adapter("auditor", calls, done({"approved": True})),
        store=store, event_sink=sink, **kwargs,
    )


@pytest.mark.parametrize("event_type,role,expected_calls", [
    ("run_started", None, []),
    ("cycle_started", None, []),
    ("stage_started", "executor", ["planner"]),
    ("stage_completed", "executor", ["planner", "executor"]),
])
def test_observer_failure_stops_dispatch_without_leaking(tmp_path, event_type, role, expected_calls):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []

    def sink(event):
        if event["type"] == event_type and (role is None or event.get("role") == role):
            raise RuntimeError("Authorization: Bearer " + SECRET)

    try:
        outcome = asyncio.run(engine(store, calls, sink=sink).run("r"))
        assert outcome.state is OrchestratorState.FAILED
        assert outcome.reason == "observer_failure"
        assert calls == expected_calls
        checkpoint = store.load_checkpoint("r")
        assert checkpoint["state"] == "failed"
        if event_type == "stage_completed":
            assert checkpoint["write_status"] == "needs_reconciliation"
        assert SECRET not in str(checkpoint) + str(store.list_events("r"))
    finally:
        store.close()


def test_terminal_notification_failure_does_not_undo_committed_result(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []

    def sink(event):
        if event["type"] == "run_completed":
            raise RuntimeError(SECRET)

    try:
        result = asyncio.run(engine(store, calls, sink=sink).run("r"))
        assert result.state is OrchestratorState.COMPLETED
        assert store.load_checkpoint("r")["state"] == "completed"
        assert calls == ["planner", "executor", "auditor"]
    finally:
        store.close()


def test_invalid_adapter_status_not_forwarded_to_observer(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    calls, events = [], []

    class Bad:
        status = SECRET

    try:
        outcome = asyncio.run(engine(
            store, calls, sink=events.append, executor=Adapter("executor", calls, Bad())
        ).run("r"))
        assert outcome.reason == "malformed_result"
        assert SECRET not in str(events)
        assert store.load_checkpoint("r")["write_status"] == "needs_reconciliation"
    finally:
        store.close()


def test_external_task_cancellation_preserves_pending_checkpoint(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    calls = []

    async def scenario():
        started = asyncio.Event()

        class WaitingExecutor:
            async def run(self, request):
                calls.append("executor")
                started.set()
                await asyncio.sleep(60)

        task = asyncio.create_task(engine(store, calls, executor=WaitingExecutor()).run("r"))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
        assert calls == ["planner", "executor"]
        cp = store.load_checkpoint("r")
        assert cp["state"] == "cancelled"
        assert cp["write_status"] == "needs_reconciliation"
    finally:
        store.close()


def test_pause_requested_by_stage_observer_prevents_dispatch(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    pause, cancel = PauseController(), CancellationController()
    calls = []

    def sink(event):
        if event["type"] == "stage_started" and event.get("role") == "executor":
            pause.request_pause()

    async def scenario():
        task = asyncio.create_task(engine(
            store, calls, sink=sink, pause=pause, cancellation=cancel
        ).run("r"))
        await asyncio.sleep(.05)
        assert calls == ["planner"]
        thread = threading.Thread(target=cancel.request_stop)
        thread.start()
        thread.join()
        result = await asyncio.wait_for(task, timeout=2)
        assert result.state is OrchestratorState.CANCELLED

    try:
        asyncio.run(scenario())
        assert store.list_operations("r") == []
    finally:
        store.close()


def test_structured_credential_aliases_and_known_text_patterns_are_redacted(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        store.append_event("r", {
            "Authorization": "Bearer " + SECRET,
            "apiKey": SECRET,
            "access_token": SECRET,
            "client-secret": SECRET,
            "nested": [{"refreshToken": SECRET}, {"message": "Authorization: Bearer " + SECRET}],
            "details": "password=" + SECRET,
            "message": "safe",
        })
        event = store.list_events("r")[0]
        assert SECRET not in json.dumps(event)
        assert event["message"] == "safe"
        assert event["apiKey"] == "<redacted>"
        store.save_checkpoint("r", {"state": "failed", "reason": "api_key=" + SECRET})
        assert SECRET not in json.dumps(store.load_checkpoint("r"))
    finally:
        store.close()


def test_worker_database_open_failure_emits_one_sanitized_error(tmp_path, monkeypatch):
    from multi_ia.gui import worker as mod

    def broken(*args):
        raise sqlite3.OperationalError(SECRET)

    monkeypatch.setattr(mod, "RunStore", broken)
    worker = mod.OrchestratorWorker(run_id="r", goal="demo", repo_path=str(tmp_path))
    errors, finished = [], []
    worker.failed.connect(errors.append)
    worker.finished.connect(finished.append)
    worker.run()
    assert errors == ["persistence_failure"]
    assert finished == []


def test_worker_close_failure_never_emits_success(tmp_path, monkeypatch):
    from multi_ia.gui import worker as mod

    class BrokenClose(RunStore):
        def close(self):
            super().close()
            raise sqlite3.OperationalError(SECRET)

    monkeypatch.setattr(mod, "RunStore", BrokenClose)
    worker = mod.OrchestratorWorker(run_id="r", goal="demo", repo_path=str(tmp_path),
                                    db_path=tmp_path / "runs.sqlite")
    errors, finished = [], []
    worker.failed.connect(errors.append)
    worker.finished.connect(finished.append)
    worker.run()
    assert errors == ["persistence_failure"]
    assert finished == []


def test_cli_database_open_failure_is_sanitized(tmp_path, monkeypatch, capsys):
    from multi_ia import cli

    def broken(*args):
        raise sqlite3.OperationalError(SECRET)

    monkeypatch.setattr(cli, "RunStore", broken)
    assert cli.main(["start", "--run-id", "r", "--goal", "demo",
                     "--repo", str(tmp_path)]) == 2
    output = capsys.readouterr().out
    assert "persistence_failure" in output
    assert SECRET not in output
