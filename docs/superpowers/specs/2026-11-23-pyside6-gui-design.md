# PySide6 GUI — Design

**Data:** 23/11/2026  
**Projeto:** Multi-IA Orchestrator  
**Status:** aguardando aprovação do usuário antes da implementação.

## Intenção

Criar uma interface desktop Windows em PySide6 para operar o núcleo já existente do Multi-IA Orchestrator sem substituir a CLI nem duplicar regras de execução. A GUI deve permitir definir objetivo e repositório, iniciar/parar uma execução, visualizar o agente ativo, o ciclo, o tempo, o estado e os logs.

Nesta fase a GUI trabalha apenas com os adaptadores fake já existentes. ChatGPT, Antigravity e Codex reais continuam bloqueados pela Task 0/Task 6.

## Abordagens consideradas

### 1. GUI chamando a CLI por subprocesso
Vantagem: implementação rápida.  
Desvantagens: sincronização de estado frágil, parsing de stdout e controle de cancelamento pior.

### 2. GUI conectada diretamente ao Orchestrator em QThread — escolhida
Vantagens: reutiliza o núcleo atual, atualiza estado/logs em tempo real, mantém a janela responsiva e prepara o projeto para adaptadores reais sem refazer a tela.  
Custo: exige uma pequena camada de worker/sinais entre Qt e asyncio.

### 3. MVVM/event bus completo
Vantagem: escalabilidade maior.  
Desvantagem: complexidade desnecessária para o estágio atual.

## Arquitetura escolhida

```text
MainWindow (PySide6)
        |
        | sinais/slots
        v
OrchestratorWorker (QThread)
        |
        | asyncio.run(...)
        v
Orchestrator existente
        |
        +--> Fake Planner
        +--> Fake Executor
        +--> Fake Auditor
        |
        +--> RunStore (SQLite)
```

A janela nunca executa o Orchestrator na thread principal.

## Tela principal

A interface será uma única janela inicial:

```text
┌────────────────────────────────────────────────────┐
│              MULTI-IA ORCHESTRATOR                 │
├────────────────────────────────────────────────────┤
│ Objetivo                                           │
│ [ Melhorar meu projeto.......................... ] │
│                                                    │
│ Repositório                                        │
│ [ C:\Users\marci\Desktop\MeuProjeto......... ] │
│ [ Procurar... ]                                    │
│                                                    │
│ [ ▶ INICIAR ]  [ ⏸ PAUSAR ]  [ ■ PARAR ]          │
├────────────────────────────────────────────────────┤
│ ChatGPT       ● Planejando                         │
│ Antigravity   ○ Aguardando                         │
│ Codex         ○ Aguardando                         │
├────────────────────────────────────────────────────┤
│ Ciclo: 1/5          Tempo: 00:01:32                │
│ Estado: PLANNING                                   │
├────────────────────────────────────────────────────┤
│ Logs                                               │
│ > Planner iniciado                                 │
│ > Plano recebido                                   │
│ > Executor aguardando                              │
└────────────────────────────────────────────────────┘
```

## Componentes

### Entrada
- Campo multilinha de objetivo.
- Campo de caminho do repositório.
- Botão **Procurar...** usando seletor nativo de diretórios.
- Campo opcional de limite de ciclos, com valor inicial 5.

### Controles
- **Iniciar**: habilitado quando não existe execução ativa e objetivo/repositório estão válidos.
- **Pausar**: visível e inicialmente desabilitado. Nesta fase, pausa entre etapas/ciclos; não interrompe uma chamada de agente já em andamento.
- **Parar**: solicita cancelamento e impede novas etapas.
- Durante execução, objetivo e repositório ficam bloqueados para edição.

### Status dos agentes
Cada agente terá nome, indicador e texto:
- ChatGPT — Planejando / Concluído / Aguardando / Falhou.
- Antigravity — Executando / Concluído / Aguardando / Falhou.
- Codex — Auditando / Concluído / Aguardando / Falhou.

Enquanto os adaptadores reais não existirem, os nomes continuam os mesmos, mas a interface mostra uma indicação discreta **Modo simulado**.

### Status geral
- Ciclo atual / máximo.
- Cronômetro desde o início da execução.
- Estado do Orchestrator.
- Resultado terminal: concluído, pausado, cancelado ou falhou.

### Logs
- Área somente leitura.
- Timestamp local por linha.
- Eventos de início/fim de estágio, transição de estado, cancelamento e erro.
- Não exibir credenciais ou segredos.
- Auto-scroll opcionalmente até a linha mais recente.

## Integração Qt ↔ núcleo

Novo módulo sugerido:

```text
src/multi_ia/gui/
├── __init__.py
├── app.py
├── main_window.py
└── worker.py
```

Responsabilidades:

- `app.py`: cria `QApplication`, tema e `MainWindow`.
- `main_window.py`: widgets, layout, validação e slots.
- `worker.py`: executa o Orchestrator fora da thread da GUI e emite sinais de estado, log, ciclo, conclusão e erro.

O worker não implementa regras de negócio que já pertençam ao Orchestrator.

## Eventos necessários do núcleo

Para a GUI acompanhar o trabalho sem polling agressivo, será adicionada uma interface opcional de eventos/callbacks no Orchestrator. Eventos mínimos:

- `run_started`
- `cycle_started`
- `stage_started`
- `stage_completed`
- `state_changed`
- `run_completed`
- `run_failed`
- `run_cancelled`

A CLI continuará funcionando sem registrar callback.

## Persistência

A GUI reutilizará `RunStore`.

O banco padrão ficará em um diretório de dados da aplicação, e não no diretório de instalação. Para desenvolvimento, será permitido informar/usar um caminho local.

A primeira versão visual não terá navegador de histórico; apenas a execução corrente será mostrada.

## Pause e Stop

### Stop
O botão **Parar** usa `CancellationController.request_stop()`. O núcleo verifica o cancelamento antes e depois de cada estágio. A GUI entra imediatamente em estado “Cancelamento solicitado” e aguarda o worker terminar com segurança.

### Pause
Como o núcleo atual não possui `PauseController`, a implementação adicionará uma pausa cooperativa simples aplicada apenas em limites seguros, entre estágios/ciclos. Não será fingida uma pausa instantânea de chamadas externas.

## Erros

Erros não tratados pelo worker serão convertidos em sinal de falha para a interface. A GUI:
- não fecha;
- mostra estado FAILED;
- registra mensagem resumida;
- reabilita os controles permitidos.

## Visual

Decisão do builder para a primeira versão:
- tema escuro neutro;
- fundo grafite;
- cartões/painéis com contraste moderado;
- verde para concluído;
- azul para ativo;
- cinza para aguardando;
- vermelho para falha/cancelamento crítico;
- tipografia nativa do sistema;
- sem pacote externo de ícones nesta fase.

O objetivo é clareza operacional, não uma identidade visual definitiva.

## Comandos

Adicionar entrada:

```powershell
multi-ia-gui
```

Também será possível:

```powershell
python -m multi_ia.gui.app
```

A CLI existente `multi-ia` permanece disponível.

## Dependências

Adicionar PySide6 como dependência opcional ou grupo de interface para não obrigar ambientes headless/CI a instalar Qt quando executam apenas o núcleo.

A implementação deve manter os testes atuais independentes de display gráfico.

## Testes

Testes mínimos:
- criação da janela em modo offscreen;
- validação de objetivo/repositório;
- estado inicial dos botões;
- iniciar desabilita campos de entrada;
- worker emite estados planner → executor → auditor;
- stop solicita cancelamento;
- conclusão restaura os controles;
- erro do worker não fecha a interface;
- CLI existente continua verde.

No CI Qt será executado com `QT_QPA_PLATFORM=offscreen` quando os testes da GUI estiverem presentes.

## Fora de escopo desta fase

- adaptadores reais ChatGPT/Antigravity/Codex;
- login/autenticação na GUI;
- automação visual de outras aplicações;
- editor de prompts;
- histórico avançado;
- dashboards de custos;
- empacotamento EXE/installer;
- system tray;
- atualização automática.

## Critério de aceite

A fase está aceita quando:

1. `multi-ia-gui` abre uma janela no Windows;
2. objetivo e repositório podem ser definidos;
3. uma execução fake ocorre sem bloquear a janela;
4. os três agentes mudam visualmente de estado na ordem correta;
5. ciclo, tempo, estado e logs são atualizados;
6. Stop impede novas etapas;
7. Pause funciona apenas em limites seguros e é rotulada de forma coerente;
8. a suíte do núcleo continua passando;
9. os testes da GUI passam em modo offscreen;
10. nenhuma integração real é alegada.
