from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from multi_ia.adapters.fake import FakeAdapter
from multi_ia.contracts import AgentResult
from multi_ia.engine import Orchestrator
from multi_ia.policies import CancellationController, ExecutionPolicy, PauseController
from multi_ia.store import RunStore


class OrchestratorWorker(QObject):
    event = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        run_id: str,
        goal: str,
        repo_path: str,
        db_path: str | Path = "multi_ia.sqlite",
        max_cycles: int = 5,
        max_seconds: float = 300,
    ) -> None:
        super().__init__()
        self.run_id = run_id
        self.goal = goal
        self.repo_path = repo_path
        self.db_path = Path(db_path)
        self.max_cycles = max_cycles
        self.max_seconds = max_seconds
        self.cancellation = CancellationController()
        self.pause = PauseController()

    @staticmethod
    def _fake_adapters() -> tuple[FakeAdapter, FakeAdapter, FakeAdapter]:
        planner = FakeAdapter(
            lambda request: AgentResult(
                status="completed",
                payload={
                    "goal": request.payload.get("goal"),
                    "repo": request.payload.get("repo"),
                },
                evidence=["fake:planner:completed"],
            )
        )
        executor = FakeAdapter(
            lambda request: AgentResult(
                status="completed",
                payload={"result": "fake execution", "write_status": "confirmed"},
                evidence=["fake:executor:completed"],
            )
        )
        auditor = FakeAdapter(
            lambda request: AgentResult(
                status="completed",
                payload={"approved": True},
                evidence=["fake:auditor:approved"],
            )
        )
        return planner, executor, auditor

    @Slot()
    def run(self) -> None:
        store = RunStore(self.db_path)
        try:
            planner, executor, auditor = self._fake_adapters()
            orchestrator = Orchestrator(
                planner,
                executor,
                auditor,
                policy=ExecutionPolicy(
                    max_cycles=self.max_cycles,
                    max_seconds=self.max_seconds,
                    max_retries=0,
                ),
                cancellation=self.cancellation,
                pause=self.pause,
                store=store,
                initial_payload={
                    "goal": self.goal,
                    "repo": self.repo_path,
                    "mode": "fake",
                },
                event_sink=self.event.emit,
            )
            outcome = asyncio.run(orchestrator.run(self.run_id))
            self.finished.emit(
                {
                    "run_id": self.run_id,
                    "state": outcome.state.value,
                    "reason": outcome.reason,
                    "mode": "fake",
                }
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            store.close()

    @Slot()
    def request_stop(self) -> None:
        self.cancellation.request_stop()
        self.event.emit({"type": "stop_requested"})

    @Slot()
    def toggle_pause(self) -> None:
        if self.pause.paused:
            self.pause.resume()
        else:
            self.pause.request_pause()
        self.event.emit({"type": "pause_changed", "paused": self.pause.paused})
