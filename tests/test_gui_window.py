import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from multi_ia.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


class HoldingWorker(QObject):
    event = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    created = 0

    def __init__(self, **kwargs):
        super().__init__()
        HoldingWorker.created += 1

    def run(self):
        return None

    def request_stop(self):
        self.event.emit({"type": "stop_requested"})

    def toggle_pause(self):
        self.event.emit({"type": "pause_changed", "paused": True})


def test_initial_window_state(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")

    assert window.windowTitle() == "Multi-IA Orchestrator"
    assert window.start_button.isEnabled() is False
    assert window.pause_button.isEnabled() is False
    assert window.stop_button.isEnabled() is False
    assert window.state_label.text() == "Estado: IDLE"
    assert window.cycle_label.text() == "Ciclo: 0/5"
    assert "Modo simulado" in window.mode_label.text()

    window.close()


def test_valid_goal_and_repository_enable_start(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")
    window.goal_edit.setPlainText("Melhorar meu projeto")
    window.repo_edit.setText(str(tmp_path))
    app.processEvents()

    assert window.start_button.isEnabled() is True

    window.close()


def test_start_locks_inputs_and_prevents_double_start(app, tmp_path):
    HoldingWorker.created = 0
    window = MainWindow(
        db_path=tmp_path / "runs.sqlite",
        worker_factory=HoldingWorker,
    )
    window.goal_edit.setPlainText("Melhorar")
    window.repo_edit.setText(str(tmp_path))
    app.processEvents()

    window._start_run()
    window._start_run()
    app.processEvents()

    assert HoldingWorker.created == 1
    assert window.goal_edit.isEnabled() is False
    assert window.repo_edit.isEnabled() is False
    assert window.start_button.isEnabled() is False
    assert window.pause_button.isEnabled() is True
    assert window.stop_button.isEnabled() is True

    window._shutdown_thread_for_test()


def test_agent_events_update_status_and_log(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")

    window._on_worker_event(
        {"type": "stage_started", "role": "planner", "state": "planning"}
    )
    window._on_worker_event(
        {"type": "stage_completed", "role": "planner", "status": "completed"}
    )
    window._on_worker_event(
        {"type": "stage_started", "role": "executor", "state": "executing"}
    )

    assert "Concluído" in window.planner_status.text()
    assert "Executando" in window.executor_status.text()
    assert "Aguardando" in window.auditor_status.text()
    assert "Planner concluído" in window.log_view.toPlainText()
    assert window.state_label.text() == "Estado: EXECUTING"

    window.close()


def test_terminal_result_restores_controls(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")
    window.goal_edit.setPlainText("Meta")
    window.repo_edit.setText(str(tmp_path))
    window._set_running_ui(True)

    window._on_worker_finished(
        {"run_id": "x", "state": "completed", "reason": None, "mode": "fake"}
    )

    assert window.goal_edit.isEnabled() is True
    assert window.repo_edit.isEnabled() is True
    assert window.pause_button.isEnabled() is False
    assert window.stop_button.isEnabled() is False
    assert window.state_label.text() == "Estado: COMPLETED"

    window.close()


def test_worker_error_does_not_close_window(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")
    window.show()
    window._set_running_ui(True)

    window._on_worker_failed("RuntimeError: falha simulada")
    app.processEvents()

    assert window.isVisible() is True
    assert window.state_label.text() == "Estado: FAILED"
    assert "falha simulada" in window.log_view.toPlainText()

    window.close()


def test_terminal_signal_does_not_unlock_before_thread_stops(app, tmp_path):
    class StillRunningThread:
        def isRunning(self):
            return True

    window = MainWindow(db_path=tmp_path / "runs.sqlite")
    window.goal_edit.setPlainText("Meta")
    window.repo_edit.setText(str(tmp_path))
    window._set_running_ui(True)
    window._thread = StillRunningThread()

    window._on_worker_finished(
        {"run_id": "x", "state": "completed", "reason": None, "mode": "fake"}
    )

    assert window.start_button.isEnabled() is False
    assert window.goal_edit.isEnabled() is False
    assert window.repo_edit.isEnabled() is False

    window._thread = None
    window._set_running_ui(False)
    window.close()
