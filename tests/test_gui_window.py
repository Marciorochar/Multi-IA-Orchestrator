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


def test_dashboard_has_compact_configuration_and_operational_widgets(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")

    assert window.browse_button.maximumWidth() <= 140
    assert list(window.agent_cards) == ["planner", "executor", "auditor"]
    assert window.progress_bar.minimum() == 0
    assert window.progress_bar.maximum() == 100
    assert window.progress_bar.value() == 0
    assert window.clear_log_button.text() == "Limpar"
    assert window.minimumWidth() >= 900
    assert window.minimumHeight() >= 680

    window.close()


def test_agent_flow_is_explicit_left_to_right(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")

    assert [label.text() for label in window.agent_flow_arrows] == ["→", "→"]
    assert "ChatGPT" in window.agent_cards["planner"].toolTip()
    assert "Antigravity" in window.agent_cards["executor"].toolTip()
    assert "Codex" in window.agent_cards["auditor"].toolTip()

    window.close()


def test_start_resets_visual_log_and_shows_starting_state(app, tmp_path):
    window = MainWindow(
        db_path=tmp_path / "runs.sqlite",
        worker_factory=HoldingWorker,
    )
    window.log_view.setPlainText("execução anterior")
    window.goal_edit.setPlainText("Nova execução")
    window.repo_edit.setText(str(tmp_path))
    app.processEvents()

    window._start_run()
    app.processEvents()

    assert "execução anterior" not in window.log_view.toPlainText()
    assert "Execução solicitada" in window.log_view.toPlainText()
    assert window.state_label.text() == "Estado: INICIANDO"
    assert window.cycle_label.text() == "Ciclo: 0/5"
    assert window.progress_bar.value() == 0

    window._shutdown_thread_for_test()


def test_cycle_event_updates_and_clamps_progress(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")
    window.max_cycles_spin.setValue(5)

    window._on_worker_event({"type": "cycle_started", "cycle_id": 2})
    assert window.cycle_label.text() == "Ciclo: 2/5"
    assert window.progress_bar.value() == 40
    assert window.progress_bar.format() == "40%"

    window._on_worker_event({"type": "cycle_started", "cycle_id": 9})
    assert window.progress_bar.value() == 100

    window._on_worker_event({"type": "cycle_started", "cycle_id": -1})
    assert window.progress_bar.value() == 0

    window.close()


def test_completed_forces_progress_to_100(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")
    window.progress_bar.setValue(20)

    window._on_worker_finished(
        {"run_id": "x", "state": "completed", "reason": None, "mode": "fake"}
    )

    assert window.progress_bar.value() == 100
    assert window.progress_bar.format() == "100%"
    assert window.state_label.text() == "Estado: COMPLETED"

    window.close()


def test_clear_logs_is_visual_only(app, tmp_path):
    db_path = tmp_path / "runs.sqlite"
    db_path.write_bytes(b"sentinel")
    window = MainWindow(db_path=db_path)
    window.log_view.setPlainText("linha 1\nlinha 2")

    window.clear_log_button.click()
    app.processEvents()

    assert window.log_view.toPlainText() == ""
    assert db_path.read_bytes() == b"sentinel"

    window.close()


def test_log_format_is_compact_without_brackets(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")

    window._append_log("Planner iniciado")
    line = window.log_view.toPlainText().strip()

    assert "[" not in line
    assert "]" not in line
    assert "  Planner iniciado" in line

    window.close()


def test_final_theme_prioritizes_state_and_logs(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")
    root_layout = window.centralWidget().layout()

    assert window.state_label.objectName() == "stateBadge"
    assert 'QLabel#stateBadge[runState="COMPLETED"]' in window.styleSheet()
    assert "QPushButton:disabled" in window.styleSheet()
    assert "color: #98a2b3;" in window.styleSheet()
    assert window.log_view.minimumHeight() >= 180

    log_index = root_layout.indexOf(window.logs_panel)
    assert log_index >= 0
    assert root_layout.stretch(log_index) >= 1

    assert "Aguardando" in window.planner_status.text()
    assert "Aguardando" in window.executor_status.text()
    assert "Aguardando" in window.auditor_status.text()

    window.close()


def test_agent_cards_change_visual_state_with_agent_status(app, tmp_path):
    window = MainWindow(db_path=tmp_path / "runs.sqlite")

    assert window.agent_cards["planner"].property("agentState") == "waiting"

    window._set_agent_status("planner", "Planejando")
    assert window.agent_cards["planner"].property("agentState") == "active"

    window._set_agent_status("planner", "Concluído")
    assert window.agent_cards["planner"].property("agentState") == "completed"

    window._set_agent_status("planner", "Falhou")
    assert window.agent_cards["planner"].property("agentState") == "failed"

    style = window.styleSheet()
    assert 'QFrame#agentCard[agentState="active"]' in style
    assert 'QFrame#agentCard[agentState="completed"]' in style
    assert 'QFrame#agentCard[agentState="failed"]' in style

    window.close()
