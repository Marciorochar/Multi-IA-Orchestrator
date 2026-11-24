from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QApplication

from multi_ia.gui.main_window import MainWindow
from multi_ia.gui.worker import OrchestratorWorker


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        app = existing
    else:
        app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setOrganizationName("M2R")
    app.setApplicationName("Multi-IA Orchestrator")
    app.setApplicationDisplayName("Multi-IA Orchestrator")
    app.setStyle("Fusion")
    return app


def create_main_window(
    *,
    db_path: str | Path | None = None,
    worker_factory: Callable[..., OrchestratorWorker] = OrchestratorWorker,
) -> MainWindow:
    return MainWindow(db_path=db_path, worker_factory=worker_factory)


def main(argv: Sequence[str] | None = None) -> int:
    app = create_application(argv)
    window = create_main_window()
    window.show()
    return app.exec()


def entrypoint() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(entrypoint())
