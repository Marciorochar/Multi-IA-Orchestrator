"""Time spent blocked in a pause does not consume the cumulative time limit."""
import asyncio
from contextlib import closing

import pytest

from multi_ia.contracts import AgentResult
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.policies import (
    CancellationController, ExecutionLimitError, ExecutionPolicy, PauseController,
    RetryableReadError,
)
from multi_ia.service import ExecutionService
from multi_ia.store import RunStore


def done(payload=None):
    return AgentResult("completed", payload or {}, ["fake:ok"])


class Adapter:
    def __init__(self, role, calls, result=None, before_return=None):
        self.role, self.calls = role, calls
        self.result = result or done()
        self.before_return = before_return

    async def run(self, request):
        self.calls.append(self.role)
        if self.before_return is not None:
            self.before_return()
        return self.result


def adapters(calls, *, planner=None, executor=None):
    return (
        Adapter("planner", calls, before_return=planner),
        Adapter("executor", calls, before_return=executor),
        Adapter("auditor", calls, done({"approved": True})),
    )


async def resume_after(pause, task, seconds, *, while_paused=None):
    await asyncio.sleep(seconds)
    if while_paused is not None:
        while_paused()
    pause.resume()
    return await asyncio.wait_for(task, timeout=2)


class ControlledClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_pause_longer_than_limit_does_not_expire_run():
    calls, pause = [], PauseController()
    pause.request_pause()
    engine = Orchestrator(*adapters(calls), policy=ExecutionPolicy(1, .2, 0), pause=pause)

    async def scenario():
        task = asyncio.create_task(engine.run("r"))
        return await resume_after(pause, task, .5)

    assert asyncio.run(scenario()).state is OrchestratorState.COMPLETED
    assert calls == ["planner", "executor", "auditor"]


@pytest.mark.parametrize("after_pause,state", [
    (1, OrchestratorState.COMPLETED),
    (4, OrchestratorState.FAILED),
])
def test_active_time_before_and_after_pause_still_counts(monkeypatch, after_pause, state):
    from multi_ia import limits

    clock = ControlledClock()
    monkeypatch.setattr(limits, "time", clock)
    calls, pause = [], PauseController()

    def plan():
        clock.advance(4)
        pause.request_pause()

    engine = Orchestrator(
        *adapters(calls, planner=plan, executor=lambda: clock.advance(after_pause)),
        policy=ExecutionPolicy(1, 6, 0), pause=pause,
    )

    async def scenario():
        task = asyncio.create_task(engine.run("r"))
        return await resume_after(pause, task, .05, while_paused=lambda: clock.advance(100))

    outcome = asyncio.run(scenario())

    assert outcome.state is state
    assert outcome.reason == (None if state is OrchestratorState.COMPLETED else "timeout")


def test_pausing_cannot_revive_an_expired_limit(monkeypatch):
    from multi_ia import limits

    clock = ControlledClock()
    monkeypatch.setattr(limits, "time", clock)
    calls, pause = [], PauseController()

    def plan():
        clock.advance(10)
        pause.request_pause()

    engine = Orchestrator(
        *adapters(calls, planner=plan), policy=ExecutionPolicy(1, 6, 0), pause=pause,
    )

    async def scenario():
        return await asyncio.wait_for(engine.run("r"), timeout=1)

    assert asyncio.run(scenario()).reason == "timeout"
    assert calls == ["planner"]


def test_pause_between_scheduling_and_invocation_does_not_consume_limit(monkeypatch):
    calls, pause = [], PauseController()
    original_wait_for = asyncio.wait_for
    injected = []

    async def wait_for(awaitable, timeout):
        # Simulates a GUI-thread pause after the boundary check, before invocation.
        if getattr(awaitable, "__name__", "") == "invoke" and not injected:
            injected.append(True)
            pause.request_pause()
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    engine = Orchestrator(
        *adapters(calls), policy=ExecutionPolicy(1, .2, 0), pause=pause,
    )

    async def scenario():
        task = asyncio.create_task(engine.run("r"))
        await asyncio.sleep(.5)
        assert calls == []
        pause.resume()
        return await asyncio.wait_for(task, timeout=2)

    assert asyncio.run(scenario()).state is OrchestratorState.COMPLETED
    assert calls == ["planner", "executor", "auditor"]


def test_pause_before_retry_does_not_consume_limit():
    calls, pause = [], PauseController()

    class TransientPlanner:
        async def run(self, request):
            calls.append("planner")
            if calls.count("planner") == 1:
                pause.request_pause()
                raise RetryableReadError("transient")
            return done()

    engine = Orchestrator(
        TransientPlanner(), *adapters(calls)[1:],
        policy=ExecutionPolicy(1, .2, 1), pause=pause,
    )

    async def scenario():
        task = asyncio.create_task(engine.run("r"))
        return await resume_after(pause, task, .5)

    assert asyncio.run(scenario()).state is OrchestratorState.COMPLETED
    assert calls == ["planner", "planner", "executor", "auditor"]


def test_stop_while_paused_beyond_limit_cancels():
    calls, pause, cancel = [], PauseController(), CancellationController()
    pause.request_pause()
    engine = Orchestrator(
        *adapters(calls), policy=ExecutionPolicy(1, .2, 0),
        pause=pause, cancellation=cancel,
    )

    async def scenario():
        task = asyncio.create_task(engine.run("r"))
        await asyncio.sleep(.4)
        cancel.request_stop()
        return await asyncio.wait_for(task, timeout=2)

    assert asyncio.run(scenario()).reason == "stop_requested"
    assert calls == []


def test_service_persists_the_extended_deadline(tmp_path):
    pause = PauseController()
    pause.request_pause()
    with closing(RunStore(tmp_path / "runs.sqlite")) as store:
        service = ExecutionService(
            store, goal="g", repo=str(tmp_path),
            policy=ExecutionPolicy(1, 1, 0), pause=pause,
        )

        async def scenario():
            task = asyncio.create_task(service.start("r"))
            return await resume_after(pause, task, 1.3)

        outcome = asyncio.run(scenario())
        record = store.load_limits("r")

    assert outcome.state is OrchestratorState.COMPLETED
    assert record["paused_seconds"] >= 1.2
    assert record["deadline"] == (
        record["started_at"] + record["max_seconds"] + record["paused_seconds"]
    )


def stored_limits(store, change):
    from multi_ia.limits import initial_limits

    record = initial_limits(ExecutionPolicy(1, 30, 0), known_zero=True)
    change(record)
    store.register_run("r", "digest", limits=record)


def test_records_without_pause_accounting_remain_valid(tmp_path):
    from multi_ia.limits import ExecutionLimits

    with closing(RunStore(tmp_path / "runs.sqlite")) as store:
        stored_limits(store, lambda record: record.pop("paused_seconds"))
        limits = ExecutionLimits(
            ExecutionPolicy(1, 30, 0), store=store, run_id="r", known_zero=True,
        )
        assert limits.checkpoint() > 0


@pytest.mark.parametrize("value", [-1, float("inf"), True, "5", 5])
def test_unsupported_pause_accounting_blocks_dispatch(tmp_path, value):
    from multi_ia.limits import ExecutionLimits

    with closing(RunStore(tmp_path / "runs.sqlite")) as store:
        record_change = lambda record: record.update(paused_seconds=value)
        if value == float("inf"):
            # Persistence already refuses non-finite numbers.
            with pytest.raises(ValueError):
                stored_limits(store, record_change)
            return
        # 5 is well typed but disagrees with the stored deadline.
        stored_limits(store, record_change)
        with pytest.raises(ExecutionLimitError, match="limits_evidence_invalid"):
            ExecutionLimits(
                ExecutionPolicy(1, 30, 0), store=store, run_id="r", known_zero=True,
            )
