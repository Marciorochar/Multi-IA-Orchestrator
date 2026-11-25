# Etapa 8.9 — Serviço de aplicação unificado

- GUI e CLI usam ExecutionService, registro único por run_id, fábrica fake comum, journal, validação, limites persistidos e controles de cancelamento/pausa.
- Banco padrão compartilhado com configuração por MULTI_IA_DB_PATH; --db explícito prevalece na CLI. Bancos antigos permanecem intactos.
- Diretório Git selecionado: aquisição de WorkspaceManager obrigatória antes de despacho, registro de preparação durável independente do checkpoint, caminho isolado entregue aos agentes. A seleção pressupõe repositório local confiável. Diretórios não Git mantêm compatibilidade com a simulação de leitura. Nenhuma aprovação de escrita é concedida pela GUI/CLI.
- Liberação somente após ausência de journal pendente e verificação de limpeza/propriedade pelo WorkspaceManager. Falha de liberação preserva evidências e impede notificação de sucesso. Recuperação com status de workspace pendente é bloqueada inclusive se o checkpoint do motor disser completed.
- A marca released é gravada após liberação física. Uma interrupção entre as duas operações deixa a execução bloqueada para revisão (fail-closed). Não há adoção automática de locks abandonados. Branches são preservadas.
- GUI mantém layout e pausa cooperativa. Eventos de workspace e motivo final aparecem nos logs. Retomada de processo interrompido continua via CLI; não há diálogo de aprovação real nem seleção de execução antiga na GUI.
- Limites do motor não interrompem comandos Git síncronos; estes têm timeout próprio. Worktree não constitui sandbox. Nenhum adaptador real habilitado. Validação em Windows e auditoria final permanecem pendentes.
