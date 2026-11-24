# GUI Visual Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refinar a interface PySide6 existente em um dashboard operacional profissional, sem alterar o Orchestrator nem adicionar agentes reais.

**Architecture:** A mudança fica concentrada em `MainWindow` e, se necessário, em um módulo de tema. O worker/QThread e o núcleo continuam intactos. O layout passa a ter quatro zonas: configuração compacta, fluxo horizontal dos agentes, faixa de progresso/estado e logs expandidos.

**Tech Stack:** Python 3.12+, PySide6, pytest, Qt offscreen CI.

**Spec:** `docs/superpowers/specs/2026-11-24-gui-visual-refinement-design.md`

## Global Constraints

- Manter PySide6 e a arquitetura `MainWindow → OrchestratorWorker/QThread → Orchestrator`.
- Não adicionar integrações reais com ChatGPT, Antigravity ou Codex.
- Manter a barra de título nativa do Windows.
- Manter modo escuro, sem visual neon/gamer, sem gradientes e sem title bar customizada.
- `INICIANDO` é apenas estado visual da GUI.
- A progress bar representa somente `cycle_id / max_cycles`.
- O botão **Limpar** afeta apenas a área visual de logs.
- Uma nova execução limpa somente os logs visuais atuais.
- Fechamento seguro da `QThread` e proteção contra double-start devem continuar verdes.

## Review Focus

1. `max_cycles` alterado antes de iniciar precisa atualizar ciclo/progresso sem divisão por zero ou texto inconsistente.
2. Evento `cycle_started` fora da faixa esperada deve ser limitado visualmente a 0–100%.
3. `COMPLETED` deve forçar 100% mesmo quando a execução termina antes do máximo de ciclos.
4. Limpar logs durante execução não pode alterar o estado do worker nem SQLite.
5. Redimensionar a janela até o tamanho mínimo não pode cortar os controles principais.

---

### Task 1: Estrutura visual do dashboard

**Files:**
- Modify: `src/multi_ia/gui/main_window.py`
- Test: `tests/test_gui_window.py`

**Interfaces:**
- Consumes: widgets e eventos já existentes de `MainWindow`.
- Produces: `agent_cards: dict[str, QFrame]`, `progress_bar: QProgressBar`, `clear_log_button: QPushButton`, mantendo os atributos públicos já usados pelos testes atuais.

- [ ] **Step 1: Write failing layout tests**

Adicionar testes que verificam:
- botão `browse_button` com largura máxima compacta;
- três cards presentes em ordem planner/executor/auditor;
- progress bar presente com faixa 0–100;
- botão `clear_log_button` presente;
- tamanho mínimo da janela preservado.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_gui_window.py -q`  
Expected: FAIL porque cards/progress bar/clear button ainda não existem ou o browse ainda expande.

- [ ] **Step 3: Implement dashboard layout**

Reestruturar `_build_ui()` preservando:
- `goal_edit`, `repo_edit`, `browse_button`, `max_cycles_spin`;
- `start_button`, `pause_button`, `stop_button`;
- `planner_status`, `executor_status`, `auditor_status`;
- `cycle_label`, `timer_label`, `state_label`, `log_view`.

Adicionar:
- `agent_cards`;
- setas visuais entre cards;
- `progress_bar`;
- `clear_log_button`.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_gui_window.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

Commit: `feat: refine desktop dashboard layout`.

### Task 2: Progresso, badges e logs

**Files:**
- Modify: `src/multi_ia/gui/main_window.py`
- Test: `tests/test_gui_window.py`

**Interfaces:**
- Consumes: `progress_bar`, `clear_log_button` e eventos existentes.
- Produces: `_set_run_state(state: str) -> None`, `_update_progress(cycle: int) -> None`, `_clear_visual_logs() -> None`.

- [ ] **Step 1: Write failing behavior tests**

Adicionar testes que provam:
- início mostra `INICIANDO`, ciclo 0/N e 0%;
- `cycle_started` atualiza progress bar proporcionalmente;
- progresso nunca sai de 0–100%;
- `COMPLETED` força 100%;
- nova execução limpa o `log_view` antes de registrar o novo início;
- botão Limpar esvazia somente `log_view`;
- SQLite/arquivo de banco não é tocado pelo clear visual;
- estado geral continua textual além da cor.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_gui_window.py -q`  
Expected: FAIL nos novos comportamentos.

- [ ] **Step 3: Implement state/progress/log behavior**

Centralizar atualização do estado visual e progresso.  
`_append_log()` passa a usar `HH:mm:ss  mensagem`, sem colchetes.  
`_start_run()` chama limpeza visual antes de `Execução solicitada`.

- [ ] **Step 4: Run GREEN + regression**

Run: `python -m pytest tests/test_gui_window.py tests/test_gui_app.py tests/test_gui_worker.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

Commit: `feat: improve run feedback and log usability`.

### Task 3: Tema, legibilidade e documentação

**Files:**
- Modify: `src/multi_ia/gui/main_window.py`
- Modify: `README.md`
- Test: `tests/test_gui_window.py`

**Interfaces:**
- Consumes: layout e comportamentos das Tasks 1–2.
- Produces: tema final do dashboard sem alterar contratos do núcleo.

- [ ] **Step 1: Write failing theme/accessibility assertions**

Cobrir propriedades verificáveis:
- botões desabilitados mantêm texto legível por stylesheet;
- status dos agentes continuam contendo texto explícito;
- badge de estado tem objectName estável;
- logs são o widget com stretch vertical principal.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/test_gui_window.py -q`  
Expected: FAIL até o tema final e object names estarem aplicados.

- [ ] **Step 3: Apply final theme and README update**

Ajustar stylesheet, espaçamentos, contraste, cartões e documentação da interface refinada.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest -q`  
Expected: all PASS.

- [ ] **Step 5: Commit**

Commit: `style: polish operational desktop dashboard`.

## Final Verification

- Executar CI do PR com `QT_QPA_PLATFORM=offscreen`.
- Confirmar que todos os testes antigos e novos passam.
- Revisar diff contra a spec.
- Confirmar que `worker.py`, `engine.py` e adapters reais não foram alterados por esta fase.
- Manter PR em draft até o usuário validar a tela no Windows.
