from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, QTimer, QTime, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from multi_ia.gui.worker import OrchestratorWorker


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        worker_factory: Callable[..., OrchestratorWorker] = OrchestratorWorker,
    ) -> None:
        super().__init__()
        self.worker_factory = worker_factory
        self.db_path = Path(db_path) if db_path is not None else (
            Path.home() / ".multi-ia-orchestrator" / "runs.sqlite"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._thread: QThread | None = None
        self._worker: OrchestratorWorker | None = None
        self._active = False
        self._closing_requested = False
        self._started_at: float | None = None

        self.setWindowTitle("Multi-IA Orchestrator")
        self.setMinimumSize(900, 680)
        self.resize(980, 760)

        self._build_ui()
        self._apply_theme()
        self._connect_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_timer)

        self._set_running_ui(False)
        self._validate_inputs()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        title_row = QHBoxLayout()
        title = QLabel("MULTI-IA ORCHESTRATOR")
        title.setObjectName("title")
        subtitle = QLabel("Orquestração e auditoria multiagente")
        subtitle.setObjectName("subtitle")
        self.mode_label = QLabel("Modo simulado")
        self.mode_label.setObjectName("modeBadge")
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        title_row.addLayout(title_block)
        title_row.addStretch()
        title_row.addWidget(self.mode_label)
        layout.addLayout(title_row)

        input_panel = self._panel()
        input_layout = QVBoxLayout(input_panel)
        input_layout.setContentsMargins(18, 14, 18, 14)
        input_layout.setSpacing(8)

        objective_label = QLabel("OBJETIVO")
        objective_label.setObjectName("fieldLabel")
        input_layout.addWidget(objective_label)

        self.goal_edit = QTextEdit()
        self.goal_edit.setPlaceholderText(
            "Ex.: analisar o projeto, executar a tarefa e auditar o resultado"
        )
        self.goal_edit.setFixedHeight(72)
        input_layout.addWidget(self.goal_edit)

        repo_row = QHBoxLayout()
        repo_row.setSpacing(10)

        repo_block = QVBoxLayout()
        repo_block.setSpacing(5)
        repo_label = QLabel("REPOSITÓRIO")
        repo_label.setObjectName("fieldLabel")
        self.repo_edit = QLineEdit()
        self.repo_edit.setPlaceholderText(r"C:\Users\marci\Desktop\MeuProjeto")
        repo_block.addWidget(repo_label)
        repo_block.addWidget(self.repo_edit)
        repo_row.addLayout(repo_block, 1)

        self.browse_button = QPushButton("Procurar")
        self.browse_button.setObjectName("secondaryButton")
        self.browse_button.setMaximumWidth(120)
        self.browse_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        repo_row.addWidget(self.browse_button, 0, Qt.AlignmentFlag.AlignVCenter)

        cycles_block = QVBoxLayout()
        cycles_block.setSpacing(5)
        self.max_cycles_label = QLabel("CICLOS")
        self.max_cycles_label.setObjectName("fieldLabel")
        self.max_cycles_spin = QSpinBox()
        self.max_cycles_spin.setRange(1, 100)
        self.max_cycles_spin.setValue(5)
        self.max_cycles_spin.setFixedWidth(82)
        cycles_block.addWidget(self.max_cycles_label)
        cycles_block.addWidget(self.max_cycles_spin)
        repo_row.addLayout(cycles_block)
        input_layout.addLayout(repo_row)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.start_button = QPushButton("▶  INICIAR")
        self.start_button.setObjectName("startButton")
        self.pause_button = QPushButton("⏸  PAUSAR")
        self.pause_button.setObjectName("pauseButton")
        self.stop_button = QPushButton("■  PARAR")
        self.stop_button.setObjectName("stopButton")
        controls.addWidget(self.start_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        controls.addStretch()
        input_layout.addLayout(controls)
        layout.addWidget(input_panel)

        agents_panel = self._panel()
        agents_layout = QHBoxLayout(agents_panel)
        agents_layout.setContentsMargins(18, 14, 18, 14)
        agents_layout.setSpacing(10)

        self.planner_status = QLabel()
        self.executor_status = QLabel()
        self.auditor_status = QLabel()
        self.agent_cards: dict[str, QFrame] = {}
        self.agent_flow_arrows: list[QLabel] = []

        agent_specs = (
            ("planner", "ChatGPT", "Planejador", self.planner_status),
            ("executor", "Antigravity", "Executor", self.executor_status),
            ("auditor", "Codex", "Auditor", self.auditor_status),
        )
        for index, (role, name, role_name, status_label) in enumerate(agent_specs):
            card = self._make_agent_card(name, role_name, status_label)
            card.setToolTip(f"{name} — {role_name}")
            self.agent_cards[role] = card
            agents_layout.addWidget(card, 1)
            if index < len(agent_specs) - 1:
                arrow = QLabel("→")
                arrow.setObjectName("flowArrow")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.agent_flow_arrows.append(arrow)
                agents_layout.addWidget(arrow)

        self._set_agent_status("planner", "Aguardando")
        self._set_agent_status("executor", "Aguardando")
        self._set_agent_status("auditor", "Aguardando")
        layout.addWidget(agents_panel)

        run_panel = self._panel()
        run_layout = QHBoxLayout(run_panel)
        run_layout.setContentsMargins(18, 12, 18, 12)
        run_layout.setSpacing(14)
        self.cycle_label = QLabel("Ciclo: 0/5")
        self.timer_label = QLabel("Tempo: 00:00:00")
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("cycleProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("0%")
        self.progress_bar.setMinimumWidth(220)
        self.state_label = QLabel("Estado: IDLE")
        self.state_label.setObjectName("stateBadge")
        run_layout.addWidget(self.cycle_label)
        run_layout.addWidget(self.progress_bar, 1)
        run_layout.addWidget(self.timer_label)
        run_layout.addStretch()
        run_layout.addWidget(self.state_label)
        layout.addWidget(run_panel)

        self.logs_panel = self._panel()
        logs_layout = QVBoxLayout(self.logs_panel)
        logs_layout.setContentsMargins(18, 14, 18, 14)
        logs_header = QHBoxLayout()
        logs_title = QLabel("LOGS DA EXECUÇÃO")
        logs_title.setObjectName("sectionTitle")
        self.clear_log_button = QPushButton("Limpar")
        self.clear_log_button.setObjectName("clearLogButton")
        self.clear_log_button.setMaximumWidth(90)
        logs_header.addWidget(logs_title)
        logs_header.addStretch()
        logs_header.addWidget(self.clear_log_button)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(180)
        self.log_view.setPlaceholderText("Os eventos da execução aparecerão aqui.")
        logs_layout.addLayout(logs_header)
        logs_layout.addWidget(self.log_view, 1)
        layout.addWidget(self.logs_panel, 1)

    @staticmethod
    def _panel() -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        return panel

    @staticmethod
    def _make_agent_card(name: str, role: str, status_label: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("agentCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(4)

        name_label = QLabel(name)
        name_label.setObjectName("agentName")
        role_label = QLabel(role)
        role_label.setObjectName("agentRole")
        status_label.setObjectName("agentStatus")

        card_layout.addWidget(name_label)
        card_layout.addWidget(role_label)
        card_layout.addSpacing(4)
        card_layout.addWidget(status_label)
        return card

    def _connect_ui(self) -> None:
        self.goal_edit.textChanged.connect(self._validate_inputs)
        self.repo_edit.textChanged.connect(self._validate_inputs)
        self.max_cycles_spin.valueChanged.connect(self._update_cycle_limit)
        self.browse_button.clicked.connect(self._browse_repo)
        self.clear_log_button.clicked.connect(self._clear_visual_logs)
        self.start_button.clicked.connect(self._start_run)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.stop_button.clicked.connect(self._request_stop)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #15171b;
                color: #f3f4f6;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QLabel#title {
                font-size: 24px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#subtitle, QLabel#agentRole {
                color: #8d96a5;
            }
            QLabel#modeBadge {
                color: #f4d58d;
                background: #3a2f18;
                border: 1px solid #6d5727;
                border-radius: 11px;
                padding: 5px 10px;
                font-weight: 600;
            }
            QFrame#panel {
                background: #1d2026;
                border: 1px solid #303640;
                border-radius: 10px;
            }
            QFrame#agentCard {
                background: #191d23;
                border: 1px solid #353d49;
                border-radius: 10px;
            }
            QFrame#agentCard[agentState="waiting"] {
                background: #191d23;
                border-color: #353d49;
            }
            QFrame#agentCard[agentState="active"] {
                background: #172b43;
                border-color: #3f78b4;
            }
            QFrame#agentCard[agentState="completed"] {
                background: #173326;
                border-color: #357653;
            }
            QFrame#agentCard[agentState="failed"],
            QFrame#agentCard[agentState="cancelled"] {
                background: #402027;
                border-color: #8f3f4b;
            }
            QLabel#flowArrow {
                color: #5d6878;
                font-size: 22px;
                font-weight: 700;
                padding: 0 2px;
            }
            QLabel#fieldLabel {
                color: #9aa4b2;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }
            QTextEdit, QLineEdit, QPlainTextEdit, QSpinBox {
                background: #111318;
                border: 1px solid #39414d;
                border-radius: 7px;
                padding: 8px;
                selection-background-color: #2563eb;
            }
            QTextEdit:focus, QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
                border: 1px solid #4c8dff;
            }
            QPushButton {
                min-height: 34px;
                padding: 4px 16px;
                border-radius: 7px;
                border: 1px solid #3b4350;
                background: #292e37;
                font-weight: 600;
            }
            QPushButton:hover:enabled {
                background: #333a46;
            }
            QPushButton:disabled {
                color: #98a2b3;
                background: #20242b;
                border-color: #303640;
            }
            QPushButton#startButton {
                background: #1f6f46;
                border-color: #2f9664;
            }
            QPushButton#startButton:hover:enabled {
                background: #258454;
            }
            QPushButton#pauseButton {
                background: #2a4f7a;
                border-color: #3f78b4;
            }
            QPushButton#stopButton {
                background: #742d34;
                border-color: #a5444f;
            }
            QLabel#agentName {
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#agentStatus {
                font-weight: 600;
            }
            QLabel#stateBadge {
                font-weight: 700;
                color: #c5ced9;
                background: #252a32;
                border: 1px solid #3a424e;
                border-radius: 10px;
                padding: 5px 10px;
            }
            QLabel#stateBadge[runState="IDLE"] {
                color: #aeb7c3;
                background: #252a32;
                border-color: #3a424e;
            }
            QLabel#stateBadge[runState="INICIANDO"],
            QLabel#stateBadge[runState="PLANNING"],
            QLabel#stateBadge[runState="EXECUTING"],
            QLabel#stateBadge[runState="AUDITING"],
            QLabel#stateBadge[runState="RETOMANDO"] {
                color: #b8d7ff;
                background: #18324f;
                border-color: #315f90;
            }
            QLabel#stateBadge[runState="PAUSED"],
            QLabel#stateBadge[runState="PAUSA SOLICITADA"] {
                color: #f2d38b;
                background: #3a301b;
                border-color: #6c5728;
            }
            QLabel#stateBadge[runState="COMPLETED"] {
                color: #a8e5c1;
                background: #173a29;
                border-color: #2f7450;
            }
            QLabel#stateBadge[runState="FAILED"],
            QLabel#stateBadge[runState="CANCELLED"],
            QLabel#stateBadge[runState="CANCELAMENTO SOLICITADO"] {
                color: #ffc0c5;
                background: #4a2027;
                border-color: #8a3945;
            }
            QLabel#sectionTitle {
                color: #c7ced8;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }
            QProgressBar#cycleProgress {
                background: #111318;
                border: 1px solid #39414d;
                border-radius: 6px;
                min-height: 18px;
                text-align: center;
                color: #d7dde6;
            }
            QProgressBar#cycleProgress::chunk {
                background: #2f78d1;
                border-radius: 5px;
            }
            QPushButton#clearLogButton {
                min-height: 28px;
                padding: 2px 12px;
                color: #b7c0cc;
                background: #252a32;
            }
            """
        )

    def _validate_inputs(self) -> None:
        goal_ok = bool(self.goal_edit.toPlainText().strip())
        repo = self.repo_edit.text().strip()
        repo_ok = bool(repo) and Path(repo).is_dir()
        self.start_button.setEnabled(goal_ok and repo_ok and not self._active)

    def _update_cycle_limit(self) -> None:
        current_text = self.cycle_label.text()
        try:
            current = int(current_text.split(":", 1)[1].split("/", 1)[0].strip())
        except (IndexError, ValueError):
            current = 0
        self._update_progress(current)

    def _browse_repo(self) -> None:
        start = self.repo_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Selecionar repositório", start)
        if selected:
            self.repo_edit.setText(selected)

    def _start_run(self) -> None:
        if self._active:
            return
        self._validate_inputs()
        if not self.start_button.isEnabled():
            return

        run_id = f"gui-{uuid.uuid4().hex[:12]}"
        self._worker = self.worker_factory(
            run_id=run_id,
            goal=self.goal_edit.toPlainText().strip(),
            repo_path=self.repo_edit.text().strip(),
            db_path=self.db_path,
            max_cycles=self.max_cycles_spin.value(),
        )
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.event.connect(self._on_worker_event)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(lambda _result: self._request_thread_quit())
        self._worker.failed.connect(lambda _error: self._request_thread_quit())
        self._thread.finished.connect(self._on_thread_finished)

        self._clear_visual_logs()
        self._set_running_ui(True)
        self._reset_agent_statuses()
        self._set_run_state("INICIANDO")
        self._update_progress(0)
        self._started_at = time.monotonic()
        self.timer_label.setText("Tempo: 00:00:00")
        self._timer.start()
        self._append_log("Execução solicitada")
        self._thread.start()

    def _set_running_ui(self, running: bool) -> None:
        self._active = running
        self.goal_edit.setEnabled(not running)
        self.repo_edit.setEnabled(not running)
        self.browse_button.setEnabled(not running)
        self.max_cycles_spin.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.stop_button.setEnabled(running)
        if not running:
            self.pause_button.setText("⏸  PAUSAR")
            self._validate_inputs()
        else:
            self.start_button.setEnabled(False)

    def _reset_agent_statuses(self) -> None:
        for role in ("planner", "executor", "auditor"):
            self._set_agent_status(role, "Aguardando")
        self._set_run_state("IDLE")
        self._update_progress(0)

    def _set_agent_status(self, role: str, text: str) -> None:
        label = {
            "planner": self.planner_status,
            "executor": self.executor_status,
            "auditor": self.auditor_status,
        }[role]
        glyph = {
            "Aguardando": "○",
            "Planejando": "●",
            "Executando": "●",
            "Auditando": "●",
            "Concluído": "●",
            "Falhou": "●",
            "Cancelado": "●",
        }.get(text, "●")
        label.setText(f"{glyph}  {text}")
        color = {
            "Aguardando": "#8d96a5",
            "Planejando": "#8bc1ff",
            "Executando": "#8bc1ff",
            "Auditando": "#8bc1ff",
            "Concluído": "#8ed7aa",
            "Falhou": "#ff9aa3",
            "Cancelado": "#ff9aa3",
        }.get(text, "#f3f4f6")
        label.setStyleSheet(f"color: {color}; font-weight: 600;")

        state = {
            "Aguardando": "waiting",
            "Planejando": "active",
            "Executando": "active",
            "Auditando": "active",
            "Concluído": "completed",
            "Falhou": "failed",
            "Cancelado": "cancelled",
        }.get(text, "waiting")
        card = self.agent_cards.get(role)
        if card is not None:
            card.setProperty("agentState", state)
            card.style().unpolish(card)
            card.style().polish(card)

    def _on_worker_event(self, event: dict) -> None:
        event_type = event.get("type")

        if event_type == "cycle_started":
            cycle = int(event.get("cycle_id", 0))
            self._update_progress(cycle)
            self._append_log(f"Ciclo {cycle} iniciado")
            return

        if event_type == "stage_started":
            role = str(event.get("role"))
            active_text = {
                "planner": "Planejando",
                "executor": "Executando",
                "auditor": "Auditando",
            }.get(role, "Executando")
            if role in {"planner", "executor", "auditor"}:
                self._set_agent_status(role, active_text)
            state = str(event.get("state", "")).upper()
            if state:
                self._set_run_state(state)
            self._append_log(
                {
                    "planner": "Planner iniciado",
                    "executor": "Executor iniciado",
                    "auditor": "Auditor iniciado",
                }.get(role, f"{role} iniciado")
            )
            return

        if event_type == "stage_completed":
            role = str(event.get("role"))
            status = str(event.get("status"))
            if role in {"planner", "executor", "auditor"}:
                self._set_agent_status(
                    role, "Concluído" if status == "completed" else "Falhou"
                )
            self._append_log(
                {
                    "planner": "Planner concluído",
                    "executor": "Executor concluído",
                    "auditor": "Auditor concluído",
                }.get(role, f"{role} concluído")
            )
            return

        if event_type == "state_changed":
            state = str(event.get("state", "unknown")).upper()
            self._set_run_state(state)
            return

        if event_type == "pause_changed":
            paused = bool(event.get("paused"))
            self.pause_button.setText("▶  RETOMAR" if paused else "⏸  PAUSAR")
            self._set_run_state("PAUSA SOLICITADA" if paused else "RETOMANDO")
            self._append_log("Pausa solicitada" if paused else "Execução retomada")
            return

        if event_type == "stop_requested":
            self._append_log("Cancelamento solicitado")
            return

        if event_type == "run_completed":
            self._append_log("Execução concluída")
        elif event_type == "run_cancelled":
            self._append_log("Execução cancelada")
        elif event_type == "run_failed":
            self._append_log("Execução falhou")
        elif event_type == "run_paused":
            self._append_log("Execução pausada")

    def _toggle_pause(self) -> None:
        if self._worker is not None and self._active:
            self._worker.toggle_pause()

    def _request_stop(self) -> None:
        if self._worker is None or not self._active:
            return
        self._worker.request_stop()
        self.stop_button.setEnabled(False)
        self._set_run_state("CANCELAMENTO SOLICITADO")

    def _on_worker_finished(self, result: dict) -> None:
        state = str(result.get("state", "unknown")).upper()
        self._set_run_state(state)
        if state == "COMPLETED":
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("100%")
        self._append_log(f"Execução encerrada: {state}")
        self._finish_ui()

    def _on_worker_failed(self, message: str) -> None:
        self._set_run_state("FAILED")
        self._append_log(f"Erro: {message}")
        self._finish_ui()

    def _finish_ui(self) -> None:
        self._timer.stop()
        self._update_timer()
        if self._thread is None or not self._thread.isRunning():
            self._set_running_ui(False)

    def _request_thread_quit(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()

    def _on_thread_finished(self) -> None:
        self._worker = None
        thread = self._thread
        self._thread = None
        if self._active:
            self._set_running_ui(False)
        if thread is not None:
            thread.deleteLater()
        if self._closing_requested:
            QTimer.singleShot(0, self.close)

    def _set_run_state(self, state: str) -> None:
        normalized = state.strip().upper() or "UNKNOWN"
        self.state_label.setText(f"Estado: {normalized}")
        self.state_label.setProperty("runState", normalized)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)

    def _update_progress(self, cycle: int) -> None:
        maximum = max(1, self.max_cycles_spin.value())
        clamped = max(0, min(int(cycle), maximum))
        percentage = int((clamped / maximum) * 100)
        self.cycle_label.setText(f"Ciclo: {clamped}/{maximum}")
        self.progress_bar.setValue(percentage)
        self.progress_bar.setFormat(f"{percentage}%")

    def _clear_visual_logs(self) -> None:
        self.log_view.clear()

    def _update_timer(self) -> None:
        if self._started_at is None:
            return
        elapsed = max(0, int(time.monotonic() - self._started_at))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.timer_label.setText(f"Tempo: {hours:02d}:{minutes:02d}:{seconds:02d}")

    def _append_log(self, message: str) -> None:
        stamp = QTime.currentTime().toString("HH:mm:ss")
        self.log_view.appendPlainText(f"{stamp}  {message}")
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _shutdown_thread_for_test(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(1500)
        self._thread = None
        self._worker = None
        self._set_running_ui(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._closing_requested = True
            if self._worker is not None:
                self._worker.request_stop()
            self._thread.quit()
            if not self._thread.wait(1500):
                event.ignore()
                return
        event.accept()
