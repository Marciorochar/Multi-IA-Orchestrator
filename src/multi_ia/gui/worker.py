from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from multi_ia.service import ExecutionService
from multi_ia.paths import default_db_path, prepare_db_path
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
        db_path: str | Path | None = None,
        max_cycles: int = 5,
        max_seconds: float = 300,
    ) -> None:
        super().__init__()
        self.run_id = run_id
        self.goal = goal
        self.repo_path = repo_path
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.max_cycles = max_cycles
        self.max_seconds = max_seconds
        self.cancellation = CancellationController()
        self.pause = PauseController()

    @Slot()
    def run(self) -> None:
        try:
            with closing(RunStore(prepare_db_path(self.db_path))) as store:
                service = ExecutionService(
                    store, goal=self.goal, repo=self.repo_path,
                    policy=ExecutionPolicy(
                        max_cycles=self.max_cycles,
                        max_seconds=self.max_seconds,
                        max_retries=0,
                    ),
                    cancellation=self.cancellation,
                    pause=self.pause,
                    event_sink=self.event.emit,
                )
                outcome = asyncio.run(service.start(self.run_id))
            self.finished.emit(
                {
                    "run_id": self.run_id,
                    "state": outcome.state.value,
                    "reason": outcome.reason,
                    "mode": "fake",
                }
            )
        except (sqlite3.Error, OSError):
            self.failed.emit("persistence_failure")
        except ValueError:
            self.failed.emit("invalid_run_configuration")
        except Exception:
            self.failed.emit("internal_failure")

    @Slot()
    def request_stop(self) -> None:
        self.cancellation.request_stop()
        self.event.emit({"type": "stop_requested"})

    @Slot()
    def toggle_pause(self) -> None:
        paused = self.pause.toggle()
        self.event.emit({"type": "pause_changed", "paused": paused})
