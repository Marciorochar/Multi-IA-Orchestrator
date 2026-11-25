# Core Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fortalecer o núcleo antes de integrar agentes reais.
**Architecture:** Serviço de aplicação comum à GUI e à CLI; persistência de estado versionado; workspace isolado e controles fail-closed.
**Tech Stack:** Python 3.12+, asyncio, SQLite, Git, PySide6, pytest.
**Spec:** `docs/superpowers/specs/2026-11-24-core-hardening-design.md`

## Global constraints
Seguir invariantes da spec. Manter fake explícito, preservar GUI, main intacta, sem merge automático. Cada tarefa: teste RED observado, alteração mínima, GREEN focado, suíte completa e commit. Não promover PR se qualquer gate estiver pendente.

## Review Focus
- Aprovação ausente, nula ou truthy não booleana: nunca completed.
- Processo cai durante executor: intenção durável, retomada bloqueada.
- Dois processos no mesmo repositório/worktrees: segundo não adquire lock.
- Checkpoint legado, config divergente, estado desconhecido: falhar fechado.
- Stop durante pausa, timeout após despacho, orçamento desconhecido: impedir novas etapas e preservar evidências.

## 8.1 — Baseline e auditoria
Files: engine.py, policies.py, store.py, workspace.py, cli.py, gui/worker.py (src/multi_ia); tests existentes.
- [ ] Registrar baseline e mapa de falhas reproduzíveis, distinguindo componentes isolados de proteções integradas.
- [ ] Criar regressões por prioridade; não atribuir sucesso a teste ainda não rodado.

## 8.2 — Workspace e exclusividade
Files: src/multi_ia/workspace.py, tests/test_workspace.py.
- [ ] RED: repos com .git arquivo, alias de caminho, IDs que colidem, lock estrangeiro, falha de criação e workspace sujo.
- [ ] Resolver git common dir; identidade de lock por repo + run + token; adquirir atomicamente; validar propriedade no release. Nunca forçar remoção, nem apagar lock de outro proprietário.
- [ ] GREEN: concorrência multiprocesso e preservação de alterações.

## 8.3 — Aprovação e permissões
Files: src/multi_ia/policies.py, contracts.py, engine.py; tests/test_policies.py, tests/test_engine.py.
- [ ] RED: ação desconhecida, perigosa sem aprovação, aprovação reutilizada em outra operação, metadados de ação inválidos.
- [ ] Definir declaração de operação versionada (ID, run, ciclo, escopo, efeito); aprovação vinculada ao conteúdo/identidade da operação, decisão explícita. Bloquear produção/publicação e ações externas nesta fase. Ações simuladas não concedem autorização real.
- [ ] GREEN: nenhum executor é invocado sem autorização compatível.

## 8.4 — Journal e checkpoints
Files: src/multi_ia/store.py, engine.py; tests/test_store.py, tests/test_engine.py.
- [ ] RED: executor consulta checkpoint antes da ação; crash após despacho; timeout; sobrescrita por checkpoint final; falha de persistência.
- [ ] Journal de operação e snapshot versionado, transação SQLite de intenção anterior ao despacho, atualização atômica de resultado validado e estado. Preservar needs_reconciliation quando ausência de confirmação verificável; nunca limpar marcador por atualização genérica.
- [ ] GREEN: simular reinício do processo após intenção e antes de confirmação.

## 8.5 — Retomada
Files: src/multi_ia/service.py (novo), engine.py, store.py, cli.py; tests/test_service.py (novo).
- [ ] RED: start duplicado, resume de completed, checkpoint legado, escrita incerta, fase já concluída, configuração divergente.
- [ ] Introduzir ExecutionService comum e snapshot de run com fase, ciclo, resultados saneados, identidade/config/workspace e limites. Retomar somente a próxima fronteira segura; recusar quando provas insuficientes. Preservar compatibilidade explícita dos dados legados, sem inventar resultados.
- [ ] GREEN: retomada não repete despacho confirmado nem incerto.

## 8.6 — Limites e retries
Files: src/multi_ia/policies.py, engine.py, service.py; tests/test_policies.py, tests/test_engine.py.
- [ ] RED: deadline total em vários estágios/ciclos, NaN/inf, tentativa segura esgotada, executor incerto, custo desconhecido e budget zero.
- [ ] Definir deadline monotônico total (inclui espera/pausa), orçamento persistido com unidade e fonte confiável, retry apenas de operação segura e transitória, dentro do deadline. Não usar retry como autorização para repetir escrita.
- [ ] GREEN: limites acumulados e preservados na recuperação.

## 8.7 — Erros, cancelamento e segredos
Files: src/multi_ia/engine.py, policies.py, store.py, gui/worker.py; tests/test_engine.py, tests/test_store.py, tests/test_gui_worker.py.
- [ ] RED: exceção de adaptador contendo segredo; parada durante pausa; exceção de abertura de DB; erros em log/observer.
- [ ] Normalizar falhas de adaptador sem persistir texto bruto; manter estado terminal/checkpoint quando DB disponível. Tratar controles thread-safe e limpar recursos. Erro de persistência impede escrita. Redação estruturada e testes de segredos conhecidos; não prometer detectar qualquer segredo arbitrário.
- [ ] GREEN: fluxo não avança após falha ou stop.

## 8.8 — Validação rigorosa
Files: src/multi_ia/engine.py, contracts.py; tests/test_engine.py, tests/test_contracts.py.
- [ ] RED: auditor completed com approved ausente, None, string, inteiro; resultado com protocolo incompatível; resultado inválido após mutação.
- [ ] Exigir booleano literal de auditor (True encerra; False reavalia; demais falham). Validar contrato e evidência antes de aceitar transição; não confiar na autodeclaração de escrita confirmada de agente real.
- [ ] GREEN: falhas não liberam ciclo e não produzem sucesso falso.

## 8.9 — Integração GUI/CLI
Files: src/multi_ia/service.py, cli.py, gui/worker.py, gui/main_window.py, README.md; tests/test_service.py, tests/test_gui_worker.py, testes CLI existentes.
- [ ] RED: mesmas regras nas duas entradas, caminhos/DB coerentes, start/resume duplicados, falha na aquisição do workspace.
- [ ] Centralizar factory fake, autorização, journal e ciclo de vida; expor bloqueio/reconciliação no status sem alteração visual estrutural. Manter APIs CLI documentadas ou migrar com mensagens claras.
- [ ] GREEN: integração ponta a ponta usando repo descartável.

## 8.10 — Auditoria e plataforma
Files: .github/workflows/tests.yml, docs/integrations/compatibility.md, README.md.
- [ ] Adicionar matriz Linux/Windows; Qt offscreen, Python 3.12 e versão Windows local validada.
- [ ] Executar suíte e ensaios de crash/concorrência/recovery, registrar logs e SHAs.
- [ ] Revisão de segurança independente quando disponível; teste Windows do usuário; Task 0 permanece pendente.
- [ ] Solicitar merge somente quando gates aplicáveis estiverem comprovados.

## Ordem de execução
Primeiro reproduzir e corrigir o falso sucesso do auditor (8.8 parcial) e a exceção do adaptador (8.7 parcial), pois são correções localizadas e independentes. Depois workspace, permissões, journal, retomada, limites, integração e auditoria final. Cada gate restante permanece pendente até prova específica.
