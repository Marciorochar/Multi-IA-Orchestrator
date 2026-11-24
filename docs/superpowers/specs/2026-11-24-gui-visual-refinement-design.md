# Refinamento Visual e Usabilidade — Dashboard Operacional

**Data:** 24/11/2026  
**Projeto:** Multi-IA Orchestrator  
**Base:** interface PySide6 já integrada à `main`  
**Status:** design aguardando aprovação antes da implementação.

## Intenção

Refinar a interface atual para que deixe de parecer uma ferramenta interna e passe a funcionar como um **dashboard operacional profissional**, mantendo a arquitetura existente, o tema escuro e o fluxo de uma única janela.

O objetivo desta fase é melhorar leitura, hierarquia, proporção, feedback visual e operação diária. Nenhuma integração real com ChatGPT, Antigravity ou Codex será adicionada aqui.

## Abordagens consideradas

### 1. Refinamento leve
Manter o layout atual e ajustar apenas proporções, espaçamentos e cores.

**Vantagem:** baixo risco.  
**Limitação:** a interface continuaria visualmente próxima de um formulário técnico.

### 2. Dashboard operacional profissional — escolhido
Reorganizar a mesma janela em quatro zonas claras:
1. configuração;
2. fluxo dos agentes;
3. progresso/estado;
4. logs.

**Vantagem:** melhora forte de hierarquia e uso diário sem reescrever a aplicação.  
**Custo:** exige reestruturar o layout do `MainWindow` e os testes visuais/funcionais.

### 3. Redesign estilo IDE
Adicionar barra lateral, console expandido e chrome de janela customizado.

**Vantagem:** visual mais sofisticado.  
**Limitação:** escopo e manutenção maiores do que esta fase justifica.

## Direção escolhida

A interface será um dashboard de operação com leitura da esquerda para a direita e de cima para baixo:

```text
┌──────────────────────────────────────────────────────────────┐
│ MULTI-IA ORCHESTRATOR                     ● MODO SIMULADO   │
│ Orquestração e auditoria multiagente                        │
├──────────────────────────────────────────────────────────────┤
│ OBJETIVO                                                     │
│ [ Analise o graphify-out................................. ] │
│                                                              │
│ REPOSITÓRIO                                     CICLOS       │
│ [ C:\...\RME-ok                         ] [📁]    [ 5 ]      │
│                                                              │
│ [ ▶ INICIAR ]   [ ⏸ PAUSAR ]   [ ■ PARAR ]                  │
├──────────────────────────────────────────────────────────────┤
│   ChatGPT              Antigravity              Codex        │
│  ● CONCLUÍDO    →      ● CONCLUÍDO      →       ● CONCLUÍDO │
│  Planejador            Executor                  Auditor      │
├──────────────────────────────────────────────────────────────┤
│ CICLO 1 / 5      ████████░░░░  20%     TEMPO 00:00:03      │
│                                      [ ✓ COMPLETED ]         │
├──────────────────────────────────────────────────────────────┤
│ LOGS DA EXECUÇÃO                              [ LIMPAR ]      │
│ 22:00:26  Planner iniciado                                  │
│ 22:00:26  Planner concluído                                 │
│ 22:00:27  Executor iniciado                                 │
│ ...                                                          │
└──────────────────────────────────────────────────────────────┘
```

## 1. Cabeçalho

O cabeçalho será mais compacto que o atual.

### Conteúdo
- **MULTI-IA ORCHESTRATOR** como título principal.
- Subtítulo: **Orquestração e auditoria multiagente**.
- Badge no canto direito: **Modo simulado**.

### Decisão
A barra de título nativa do Windows será mantida nesta fase. Não haverá janela sem borda ou title bar customizada.

**Motivo:** evitar adicionar complexidade de arraste, maximização, DPI, acessibilidade e comportamento específico do Windows a uma fase focada em usabilidade interna.

## 2. Configuração da execução

A área de configuração continuará no topo, mas será mais compacta.

### Objetivo
- Campo multilinha.
- Altura menor que a atual, suficiente para 2–3 linhas.
- Placeholder curto.
- Label em caixa alta discreta: **OBJETIVO**.

### Repositório
- Campo ocupa a maior parte da linha.
- Botão **Procurar** passa a ser compacto, apenas com largura necessária.
- Caminho continua editável manualmente.

### Ciclos
- `QSpinBox` na mesma linha do repositório.
- Label curta: **CICLOS**.
- Não haverá um grande bloco vazio ao redor do controle.

### Botões
Ordem:
- **Iniciar**
- **Pausar / Retomar**
- **Parar**

Regras:
- Iniciar continua sendo a ação primária.
- Pausar usa azul.
- Parar usa vermelho.
- Estado desabilitado continuará visível e legível, com contraste maior que o atual.
- Os três botões ficam juntos e alinhados à esquerda.

## 3. Fluxo dos agentes

As linhas atuais serão substituídas por **três cards horizontais**, um para cada agente:

```text
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ ChatGPT      │  →   │ Antigravity  │  →   │ Codex        │
│ Planejador   │      │ Executor     │      │ Auditor      │
│ ● Concluído  │      │ ● Executando │      │ ○ Aguardando │
└──────────────┘      └──────────────┘      └──────────────┘
```

### Estados
- **Aguardando:** cinza.
- **Ativo:** azul.
- **Concluído:** verde.
- **Falhou:** vermelho.
- **Cancelado:** vermelho suave.

### Semântica
A seta entre cards comunica a ordem real:
**ChatGPT → Antigravity → Codex**.

### Regra visual
O status será o elemento de maior destaque dentro de cada card. O papel do agente será secundário.

## 4. Progresso e estado geral

A linha atual de ciclo/tempo/estado será transformada em uma faixa operacional.

### Conteúdo
- **Ciclo atual / máximo**.
- `QProgressBar` proporcional ao ciclo atual.
- **Tempo decorrido**.
- Badge do estado geral.

### Progress bar
Exemplo:
- ciclo 1 de 5 → 20%;
- ciclo 3 de 5 → 60%;
- completo → 100%.

A barra representa **progresso de ciclos**, não percentual de conclusão intelectual do trabalho.

### Badge do estado
Cores:
- IDLE → cinza;
- PLANNING / EXECUTING / AUDITING → azul;
- PAUSED → âmbar;
- COMPLETED → verde;
- FAILED / CANCELLED → vermelho.

O badge deve ser mais legível do que o texto simples atual.

## 5. Logs

Os logs passam a ser uma área dominante da metade inferior da janela.

### Cabeçalho
- Título: **LOGS DA EXECUÇÃO**.
- Botão compacto **Limpar** à direita.

### Comportamento
Ao iniciar uma nova execução:
- limpar a visualização atual dos logs;
- registrar imediatamente o início da nova execução;
- não apagar eventos persistidos no SQLite.

O botão **Limpar**:
- limpa apenas o widget visual;
- não modifica banco, checkpoints ou histórico persistente.

### Formatação
Cada linha mantém:
- timestamp;
- mensagem curta.

Exemplo:

```text
22:00:26  Execução iniciada
22:00:26  Planner iniciado
22:00:26  Planner concluído
22:00:27  Executor iniciado
```

O prefixo com colchetes será removido para reduzir ruído visual.

### Tamanho
O painel de logs receberá prioridade vertical e deverá crescer quando a janela for redimensionada.

## 6. Layout e responsividade

A interface continuará em uma única janela.

### Tamanho-alvo
- tamanho inicial próximo de 1000 × 760;
- mínimo em torno de 900 × 680.

### Regras
- evitar larguras fixas excessivas;
- usar `QSizePolicy`, stretches e layouts;
- botão Procurar não expande horizontalmente;
- cards dos três agentes têm largura equilibrada;
- logs ocupam espaço restante;
- nenhuma informação importante deve ser cortada no tamanho mínimo.

## 7. Tema

Manter tema escuro, porém com menos blocos pretos internos.

### Paleta operacional proposta
- fundo principal: grafite muito escuro;
- painéis: grafite médio;
- bordas: cinza azulado discreto;
- texto primário: quase branco;
- texto secundário: cinza frio;
- azul: atividade;
- verde: sucesso;
- âmbar: pausa/simulação;
- vermelho: falha/parada.

### Anti-meta
Não transformar a interface em:
- visual neon/gamer;
- painel excessivamente colorido;
- cópia de VS Code;
- interface cheia de gradientes;
- janela customizada sem controles nativos nesta fase.

## 8. Feedback de execução

Ao clicar em **Iniciar**:
1. limpar visualização dos logs;
2. resetar cards;
3. mostrar ciclo 0/N;
4. estado passa para preparação/execução;
5. controles de entrada ficam bloqueados;
6. Iniciar desabilita;
7. Pausar e Parar habilitam.

Durante a execução:
- card ativo muda para azul;
- card concluído muda para verde;
- próximos permanecem cinza;
- progresso e estado geral acompanham eventos.

Ao concluir:
- cards concluídos verdes;
- progresso em 100%;
- badge **COMPLETED** verde;
- campos são reabilitados somente após a `QThread` encerrar.

## 9. Usabilidade e acessibilidade

- não depender somente de cor: todos os estados têm texto;
- contraste suficiente nos botões desabilitados;
- foco de teclado visível;
- labels persistentes, não apenas placeholders;
- componentes mantêm ordem de tabulação natural;
- nenhuma animação necessária para compreender o estado.

## 10. Arquitetura

Esta fase não altera o desenho fundamental:

```text
MainWindow
    ↓
OrchestratorWorker / QThread
    ↓
Orchestrator
    ↓
Adapters
```

O refinamento deve ficar majoritariamente em:
- `src/multi_ia/gui/main_window.py`;
- testes da GUI.

Se necessário, estilos poderão ser extraídos para:
- `src/multi_ia/gui/theme.py`.

Não haverá regra de negócio duplicada na camada visual.

## 11. Testes esperados

Além de preservar os testes atuais, a fase deve verificar:

- botão Procurar permanece compacto;
- os três cards existem e mantêm ordem ChatGPT → Antigravity → Codex;
- status textual acompanha eventos;
- progresso reflete `cycle_id / max_cycles`;
- COMPLETED define progresso 100%;
- novo run limpa apenas o log visual;
- botão Limpar não altera SQLite;
- estado geral usa badge textual;
- controles desabilitados continuam legíveis por estado, não apenas por cor;
- layout constrói corretamente em modo offscreen;
- fechamento seguro de `QThread` continua verde.

## 12. Fora de escopo

- agentes reais;
- edição de prompts;
- histórico de execuções;
- painel de custos;
- métricas detalhadas;
- múltiplos projetos simultâneos;
- title bar customizada;
- empacotamento EXE;
- system tray.

## Critérios de aceite

A fase será considerada concluída quando:

1. a janela apresentar configuração, fluxo de agentes, progresso e logs com hierarquia visual clara;
2. botão Procurar e controle de ciclos deixarem de dominar espaço horizontal;
3. o fluxo ChatGPT → Antigravity → Codex estiver visualmente explícito;
4. estado geral estiver destacado por badge;
5. ciclo possuir progress bar coerente;
6. logs tiverem mais espaço e botão Limpar;
7. nova execução não misturar visualmente logs anteriores;
8. comportamento funcional atual permanecer intacto;
9. testes GUI e núcleo continuarem verdes;
10. nenhuma integração real for adicionada.
