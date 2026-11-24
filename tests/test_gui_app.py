import importlib.metadata
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal

from multi_ia.gui.app import create_application, create_main_window


class IdleWorker(QObject):
    event = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, **kwargs):
        super().__init__()

    def run(self):
        return None

    def request_stop(self):
        return None

    def toggle_pause(self):
        return None


def test_app_constructs_window_offscreen(tmp_path):
    app = create_application([])
    window = create_main_window(db_path=tmp_path / "runs.sqlite")

    assert app.applicationName() == "Multi-IA Orchestrator"
    assert window.windowTitle() == "Multi-IA Orchestrator"

    window.close()


def test_console_script_registers_gui_entrypoint():
    scripts = {
        item.name: item.value
        for item in importlib.metadata.entry_points(group="console_scripts")
    }

    assert scripts["multi-ia-gui"] == "multi_ia.gui.app:entrypoint"


def test_closing_active_window_stops_qthread(tmp_path):
    app = create_application([])
    window = create_main_window(
        db_path=tmp_path / "runs.sqlite",
        worker_factory=IdleWorker,
    )
    window.goal_edit.setPlainText("Fechar com segurança")
    window.repo_edit.setText(str(tmp_path))
    app.processEvents()
    window._start_run()
    app.processEvents()

    thread = window._thread
    assert thread is not None
    assert thread.isRunning() is True

    window.close()
    app.processEvents()

    assert thread.isRunning() is False
