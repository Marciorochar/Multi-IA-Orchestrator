# Multi-IA Orchestrator

> **Status:** núcleo MVP e interface PySide6 disponíveis na `main`; branch Core Hardening em validação. Integrações reais continuam bloqueadas pela Task 0.

## Visão geral

O **Multi-IA Orchestrator** é um aplicativo desktop Windows em Python que coordena o ciclo **ChatGPT (planejador) → Google Antigravity (executor) → OpenAI Codex (auditor) → avaliação e próxima iteração**, sempre com limites, rastreabilidade e controle humano.

O objetivo é substituir a transferência manual de prompts e respostas entre ferramentas, mantendo evidências da execução e permitindo pausar, parar e retomar com segurança.

**Estratégia híbrida:** priorizar API, SDK e CLI oficiais; considerar automação visual apenas quando necessária, permitida e validada.

## Objetivos

- Receber do usuário o objetivo e os limites de uma execução.
- Planejar instruções estruturadas para o executor.
- Aguardar o término verificável da execução no Antigravity.
- Solicitar auditoria e testes ao Codex.
- Devolver evidências ao planejador para decidir se encerra ou inicia outro ciclo.
- Registrar prompts, resultados, comandos, testes, falhas e decisões.
- Oferecer controles de início, pausa, retomada e interrupção.

## Fluxo

```mermaid
flowchart TD
 U[Usuário define objetivo] --> O[Orquestrador Python]
 O --> P[ChatGPT: planejamento]
 P --> E[Antigravity: execução]
 E --> C{Resultado terminal válido?}
 C -- Não --> F[Erro, timeout ou cancelamento]
 C -- Sim --> A[Codex: auditoria]
 A --> D{Critérios atendidos?}
 D -- Sim --> S[Finalizar com evidências]
 D -- Não --> L{Limites permitem novo ciclo?}
 L -- Sim --> P
 L -- Não --> H[Pausar para decisão humana]
```

Resultados parciais não podem avançar o fluxo. Cada agente recebe um resumo verificável do trabalho anterior, não apenas uma repetição textual do prompt.

## Arquitetura

| Camada | Responsabilidade | Tecnologia prevista |
|---|---|---|
| Core Engine | Estados, transições e coordenação assíncrona | Python 3.12+, asyncio |
| Agent Adapters | Integração com modelos e interfaces | API / SDK / CLI; fallback visual |
| Task Manager | Timeouts, tentativas, cancelamento | asyncio, subprocess |
| Memory Manager | Histórico e checkpoints | SQLite, JSON |
| Workspace Manager | Isolamento e inspeção de mudanças | Git, worktrees |
| Safety Controller | Aprovações, limites, permissões | Regras explícitas |
| Observabilidade | Eventos e evidências | Logs estruturados |
| Interface gráfica | Dashboard operacional, monitoramento e controles | PySide6 |

### Contratos de agentes

Cada etapa terá `run_id`, `cycle_id`, papel, tarefa, contexto, status, evidências, erros, arquivos e testes. Os contratos serão versionados e validados.

Estados terminais aceitos: `completed`, `failed` e `cancelled`; `pending` e `running` não equivalem a conclusão.

### Segurança e autonomia

- Um único agente pode modificar o workspace de cada vez.
- Ações destrutivas, alterações externas e publicação exigem aprovação humana.
- Nunca alterar produção automaticamente.
- Definir limites de ciclos, tempo, tentativas e orçamento.
- Não registrar credenciais nem alegar acesso a pensamentos internos de agentes.
- Interrupção impede novas etapas; recuperação não repete escrita incerta.
- Testar inicialmente com adaptadores simulados e repositórios descartáveis.

## Plano de implantação

A execução foi detalhada em [docs/superpowers/plans/2026-11-23-multi-ia-orchestrator.md](docs/superpowers/plans/2026-11-23-multi-ia-orchestrator.md).

| Etapa | Entrega |
|---|---|
| 0 | Descoberta e decisão de integração |
| 1 | Contratos e agentes simulados |
| 2 | Máquina de estados |
| 3 | Cancelamento, timeout e aprovações |
| 4 | Persistência e checkpoints |
| 5 | Isolamento e ciclo repetível |
| 6 | Integrações reais, condicionadas à Etapa 0 |
| 7 | CLI do MVP e ensaio completo |

A interface PySide6 já foi implementada. O empacotamento e as integrações reais permanecem em fases separadas.

## Critérios de sucesso

- Ciclo completo entre três agentes com evidências.
- Detecção confiável de conclusão, falha e timeout.
- Interrupção antes da próxima etapa.
- Checkpoint consistente e recuperação segura.
- Ausência de modificações concorrentes conflitantes.
- Encerramento automático por limites ou falta de progresso.

## Decisão pendente

A integração real requer decidir entre `existing_ui` (janelas/sessões já abertas) e `independent` (sessões independentes via API/CLI). A disponibilidade, autorização, autenticação e mecanismo de término/cancelamento de cada ferramenta devem ser comprovados antes de implementar adaptadores reais.

Consulte [docs/integrations/compatibility.md](docs/integrations/compatibility.md).

## Restrições do MVP

Não pressupor acesso às mesmas conversas pela API do ChatGPT; não presumir CLI/API do Antigravity sem validação; não automatizar interfaces contrariando restrições dos fornecedores; não executar alterações na máquina do usuário a partir do GitHub sem sua instalação e autorização.

## CLI do MVP (modo fake)

Enquanto a Task 0 não validar as integrações reais, a CLI executa somente adaptadores simulados:

```powershell
python -m multi_ia.cli start --run-id demo-1 --goal "validar o fluxo" --repo .
python -m multi_ia.cli status --run-id demo-1
python -m multi_ia.cli stop --run-id demo-1
python -m multi_ia.cli resume --run-id demo-1 --goal "validar o fluxo" --repo .
```

O comando `resume` recusa automaticamente checkpoints incertos, legados sem evidência suficiente ou com workspace pendente. Somente a fronteira comprovada após confirmação do executor fake de diretório comum admite retomada de auditoria sem novo despacho.

### Banco e workspace compartilhados

GUI e CLI usam o mesmo `ExecutionService` e o banco padrão `~/.multi-ia-orchestrator/runs.sqlite`. Configure `MULTI_IA_DB_PATH` para outro local; na CLI, `--db` prevalece. Bancos anteriores não são removidos nem migrados automaticamente. Para consultar um banco CLI antigo, use `--db ./multi_ia.sqlite`.

Ao selecionar um projeto Git local confiável, o serviço cria um worktree de HEAD e branch próprios, adquire o writer lock do repositório e registra a intenção de preparação antes de despachar agentes. Alterações não commitadas do checkout original não são copiadas. Worktrees limpos são removidos sem força e a branch é mantida. Operações incertas, falhas de limpeza ou interrupções preservam o worktree/lock para revisão; a retomada não adota automaticamente locks abandonados. Em diretórios não Git o fake de leitura não precisa de worktree. Não use repositórios Git desconhecidos: o Git pode executar hooks/filtros configurados pelo projeto ou ambiente.

O modo real continua desativado; nenhum grant de escrita é criado automaticamente. O botão Retomar da GUI continua sendo a retomada de pausa da sessão ativa; recuperação de processo interrompido permanece acessível via CLI. O worktree não é sandbox nem autorização para escrita real.

**Estado atual:** Tasks 1–5, CLI fake da Task 7 e interface PySide6 estão disponíveis na `main`. Nenhuma integração real com ChatGPT, Antigravity ou Codex foi alegada ou testada.


## Interface desktop PySide6

A interface visual utiliza o mesmo ExecutionService da CLI na branch de Core Hardening, mantendo somente adaptadores simulados.

### Instalação no Windows

No PowerShell, dentro do repositório:

```powershell
python -m pip install -e ".[gui]"
```

O extra `gui` instala PySide6. A linha atual do PySide6 suporta Python 3.14.

### Abrir a interface

```powershell
multi-ia-gui
```

Alternativamente:

```powershell
python -m multi_ia.gui.app
```

A janela oferece:

- objetivo da execução;
- seleção do repositório;
- máximo de ciclos;
- botões **Iniciar**, **Pausar/Retomar** e **Parar**;
- estados de ChatGPT, Antigravity e Codex;
- ciclo, cronômetro e estado geral;
- logs em tempo real;
- indicação explícita de **Modo simulado**.

O botão **Pausar** é cooperativo: a pausa acontece em limites seguros entre etapas. **Parar** solicita cancelamento e impede novas etapas.

> A interface ainda não controla sessões reais de ChatGPT, Antigravity ou Codex. Isso será implementado somente após a Task 0 validar os mecanismos disponíveis.

### Dashboard operacional refinado

A interface foi refinada para uso operacional diário: configuração mais compacta, fluxo visual **ChatGPT → Antigravity → Codex**, cards que mudam conforme o estado de cada agente, barra de progresso por ciclos, badge de estado destacado e painel de logs ampliado com limpeza somente visual. Uma nova execução limpa a visualização anterior dos logs sem apagar eventos ou checkpoints persistidos.
