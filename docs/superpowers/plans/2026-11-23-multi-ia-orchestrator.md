# Multi-IA Orchestrator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar um MVP Windows em Python que coordene planejamento, execução e auditoria em ciclos sequenciais, verificáveis e interrompíveis.

**Architecture:** Núcleo assíncrono independente de fornecedores, adaptadores substituíveis e persistência SQLite. O MVP valida primeiro agentes simulados, depois integra serviços reais um a um. Automação visual é fallback, não dependência do núcleo.

**Tech Stack:** Python 3.12+, asyncio, pytest, SQLite, Git/worktrees; PySide6 em fase posterior.

**Spec:** `README.md` (proposta aprovada em 23/11/2026).

## Global Constraints

- Windows desktop; Python 3.12+.
- Ordem: ChatGPT (planejador) → Antigravity (executor) → Codex (auditor) → avaliação.
- Priorizar API/SDK/CLI; automação visual somente quando necessária, permitida e comprovadamente estável.
- Nenhuma leitura de raciocínio interno não exposto pelos modelos.
- Uma etapa mutável por vez; não operar em produção sem autorização explícita.
- Limites de ciclos, tempo, tentativas e orçamento; nunca pressupor execução ilimitada.
- Aprovação humana para ações destrutivas, externas ou publicação.
- Credenciais fora de código, banco e logs; redigir segredos em registros.
- **Decisão ainda aberta:** sessões já abertas versus sessões independentes. Não implementar adaptadores reais antes de decidir e validar as interfaces disponíveis.
- **Escopo deste plano:** descoberta e MVP de núcleo/integrações. Interface desktop completa e empacotamento serão planejados separadamente após validação do ciclo real.

## Review Focus

1. Resposta parcial de um agente não pode ser marcada como conclusão — teste no Task 2.
2. Cancelamento durante execução impede novas etapas, mesmo após resposta tardia — teste no Task 3.
3. Reinício após queda não duplica uma operação de escrita — teste no Task 4.
4. Saída inválida/malformada de adaptador não pode avançar o ciclo — teste no Task 2.
5. Ciclos sem progresso devem parar antes de consumir recursos indefinidamente — teste no Task 5.

---

## Mapa de arquivos proposto

- `pyproject.toml`: dependências, ferramentas e configuração de testes.
- `src/multi_ia/contracts.py`: estados, mensagens e validação.
- `src/multi_ia/adapters/base.py`: protocolo de adaptadores.
- `src/multi_ia/adapters/fake.py`: agentes simulados determinísticos.
- `src/multi_ia/engine.py`: transições e execução sequencial.
- `src/multi_ia/policies.py`: limites, aprovações e progresso.
- `src/multi_ia/store.py`: eventos e checkpoints SQLite.
- `src/multi_ia/workspace.py`: isolamento Git e verificação de alterações.
- `src/multi_ia/adapters/planner.py`: integração do planejador (após descoberta).
- `src/multi_ia/adapters/executor.py`: integração do Antigravity (após descoberta).
- `src/multi_ia/adapters/auditor.py`: integração do Codex (após descoberta).
- `src/multi_ia/cli.py`: entrada de execução controlada do MVP.
- `tests/test_contracts.py`, `test_engine.py`, `test_store.py`, `test_policies.py`, `test_workspace.py`, `test_integrations.py`: testes por responsabilidade.
- `docs/integrations/compatibility.md`: matriz de compatibilidade e evidências.

## Task 0: Descoberta e decisão de integração (gate obrigatório)

**Files:** Create `docs/integrations/compatibility.md`.

**Interfaces:** Produz a decisão `session_mode` (`existing_ui` ou `independent`) e uma matriz por agente: mecanismo, autenticação, início, término, resultado, cancelamento, restrições e custo.

- [ ] **Step 1:** Confirmar com o usuário se as sessões existentes são obrigatórias ou se sessões independentes são aceitas.
- [ ] **Step 2:** Inspecionar versões instaladas e documentação oficial de cada ferramenta; não assumir que há SDK/CLI com funcionalidades específicas.
- [ ] **Step 3:** Registrar uma prova de leitura/execução não destrutiva para cada mecanismo disponível e suas limitações.
- [ ] **Step 4:** Revisar a matriz com o usuário; se algum agente não oferecer canal permitido e confiável, interromper planejamento da integração e propor fallback explícito.
- [ ] **Step 5:** Commitar somente a documentação validada (`docs: document integration feasibility`).

**Aceite:** cada integração tem evidência de entrada, término, resultado e cancelamento, ou está claramente marcada como indisponível; `session_mode` decidido.

## Task 1: Contratos e adaptadores simulados

**Files:** Create `pyproject.toml`, `src/multi_ia/contracts.py`, `src/multi_ia/adapters/base.py`, `src/multi_ia/adapters/fake.py`, `tests/test_contracts.py`.

**Interfaces:** `AgentRequest(run_id: str, cycle_id: int, role: str, payload: dict)`; `AgentResult(status: str, payload: dict, evidence: list[str])`; `AgentAdapter.run(request: AgentRequest) -> AgentResult` (assíncrono).

- [ ] **Step 1:** Escrever testes `test_rejects_missing_run_id`, `test_rejects_invalid_status`, `test_rejects_completed_without_evidence`, `test_fake_adapter_returns_terminal_result`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_contracts.py -v`; esperar falha por módulos ausentes.
- [ ] **Step 3:** Implementar contratos versionados e adaptador fake mínimo; distinguir `pending/running` de `completed/failed/cancelled`.
- [ ] **Step 4:** Reexecutar o comando; esperar PASS.
- [ ] **Step 5:** Commit `feat: define agent contracts and fake adapters`.

## Task 2: Máquina de estados e validação de resultados

**Files:** Create `src/multi_ia/engine.py`, `tests/test_engine.py`.

**Interfaces:** `Orchestrator.run_cycle(run_id: str, cycle_id: int) -> CycleOutcome`; recebe três `AgentAdapter` e política de execução.

- [ ] **Step 1:** Escrever testes `test_agents_run_in_order`, `test_partial_result_does_not_advance`, `test_malformed_result_fails_closed`, `test_audit_rejection_requests_next_cycle`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_engine.py -v`; esperar FAIL.
- [ ] **Step 3:** Implementar transições `IDLE/PLANNING/EXECUTING/WAITING/AUDITING/EVALUATING/COMPLETED/FAILED/PAUSED/CANCELLED`; validar resultado terminal antes de avançar.
- [ ] **Step 4:** Rodar o mesmo teste; esperar PASS.
- [ ] **Step 5:** Commit `feat: orchestrate one verified cycle`.

## Task 3: Interrupção, timeout e aprovação

**Files:** Create `src/multi_ia/policies.py`, `tests/test_policies.py`; Modify `src/multi_ia/engine.py`.

**Interfaces:** `ExecutionPolicy(max_cycles: int, max_seconds: int, max_retries: int, max_budget: float | None)`; `CancellationController.request_stop() -> None`; `ApprovalGate.check(action: str) -> bool`.

- [ ] **Step 1:** Escrever testes `test_stop_prevents_next_agent`, `test_late_response_cannot_resume_cancelled_run`, `test_timeout_sets_failed_state`, `test_dangerous_action_requires_approval`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_policies.py -v`; esperar FAIL.
- [ ] **Step 3:** Implementar cancelamento cooperativo e encerramento seguro de subprocessos quando houver; aplicar limites e bloquear ações sem aprovação.
- [ ] **Step 4:** Rodar testes; esperar PASS.
- [ ] **Step 5:** Commit `feat: enforce execution controls`.

## Task 4: Eventos, checkpoints e recuperação

**Files:** Create `src/multi_ia/store.py`, `tests/test_store.py`; Modify `src/multi_ia/engine.py`.

**Interfaces:** `RunStore.append_event(run_id: str, event: dict) -> None`; `RunStore.save_checkpoint(run_id: str, checkpoint: dict) -> None`; `RunStore.load_checkpoint(run_id: str) -> dict | None`.

- [ ] **Step 1:** Escrever testes `test_events_preserve_order`, `test_restart_loads_last_consistent_checkpoint`, `test_restart_does_not_repeat_unconfirmed_write`, `test_secrets_are_redacted`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_store.py -v`; esperar FAIL.
- [ ] **Step 3:** Implementar persistência transacional SQLite; marcar ações de escrita incertas como `needs_reconciliation`, sem replay automático.
- [ ] **Step 4:** Rodar testes; esperar PASS.
- [ ] **Step 5:** Commit `feat: persist events and checkpoints`.

## Task 5: Progresso, isolamento e ciclo repetível

**Files:** Create `src/multi_ia/workspace.py`, `tests/test_workspace.py`; Modify `src/multi_ia/policies.py`, `src/multi_ia/engine.py`, `tests/test_policies.py`.

**Interfaces:** `WorkspaceManager.prepare(repo_path: str, run_id: str) -> str`; `ProgressGuard.evaluate(previous: dict, current: dict) -> str` (`continue`, `complete`, `pause`).

- [ ] **Step 1:** Escrever testes `test_workspace_is_isolated`, `test_parallel_writes_are_rejected`, `test_repeated_no_progress_pauses`, `test_max_cycles_stops`, `test_verified_success_completes`.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_workspace.py tests/test_policies.py -v`; esperar FAIL.
- [ ] **Step 3:** Implementar isolamento por worktree, bloqueio de escritor único e detecção de estagnação por resultados/evidências, sem depender apenas de texto idêntico.
- [ ] **Step 4:** Rodar testes; esperar PASS.
- [ ] **Step 5:** Commit `feat: isolate workspace and guard progress`.

## Task 6: Adaptadores reais e integração ponta a ponta (condicional ao Task 0)

**Files:** Create `src/multi_ia/adapters/planner.py`, `executor.py`, `auditor.py`, `tests/test_integrations.py`; Modify `docs/integrations/compatibility.md`.

**Interfaces:** Cada adaptador implementa `AgentAdapter.run(request: AgentRequest) -> AgentResult`; mecanismos concretos e comandos devem ser preenchidos **somente com base nas descobertas do Task 0**.

- [ ] **Step 1:** Escrever testes de contrato e mocks para sucesso, erro, timeout, cancelamento, saída parcial e saída inválida de cada adaptador.
- [ ] **Step 2:** Rodar `python -m pytest tests/test_integrations.py -v`; esperar FAIL.
- [ ] **Step 3:** Implementar um adaptador por vez, com autenticação externa aos logs; adicionar fallback visual apenas se validado e permitido.
- [ ] **Step 4:** Rodar testes mockados e smoke tests reais não destrutivos; registrar evidências e custos.
- [ ] **Step 5:** Commit `feat: connect validated agent adapters`.

## Task 7: CLI de MVP e ensaio completo

**Files:** Create `src/multi_ia/cli.py`; Modify `README.md`; Test `tests/test_integrations.py`.

**Interfaces:** CLI com `start`, `status`, `stop`, `resume`; recebe objetivo, repositório, modo supervisionado e limites.

- [ ] **Step 1:** Escrever teste de execução fake completa e interrupção com persistência.
- [ ] **Step 2:** Rodar `python -m pytest -q`; esperar FAIL antes da implementação.
- [ ] **Step 3:** Implementar CLI mínima; `resume` deve recusar repetição automática de escrita incerta.
- [ ] **Step 4:** Rodar `python -m pytest -q` e um ensaio real de ciclo único em repositório descartável; registrar duração, evidências e resultado.
- [ ] **Step 5:** Commit `feat: deliver controlled orchestration MVP`.

## Handoff e fases posteriores

Após aceitação do MVP: produzir **plano separado** para interface PySide6, automação visual robusta, empacotamento Windows e monitoramento de custos. Não alegar que essas partes estão implementadas pelo plano atual.

## Revisão do plano

- Cobertura: fluxo, contratos, estados, interrupção, persistência, limites, isolamento, integração e ensaio cobertos.
- Dependência crítica: Task 0 determina os mecanismos reais e pode alterar a viabilidade; Tasks 1–5 são independentes de fornecedor.
- Testes críticos: parcial, inválido, cancelamento tardio, recuperação incerta e estagnação explicitamente incluídos.
- Status: planejamento somente; nenhuma implementação ou teste executado.
