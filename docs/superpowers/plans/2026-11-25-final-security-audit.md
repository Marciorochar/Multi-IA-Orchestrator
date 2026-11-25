# Etapa 8.10 — Auditoria de segurança final (escopo fake)

## Baseline e método
PR #9, baseline 5cdc9157b59fc3b3fe8ea4227bc75baab45738fc. Revisão dos contratos, motor, serviço, persistência, limites, workspace, entradas CLI/GUI e matriz Task 0, cruzada com a especificação da Etapa 8. CI Linux do baseline: 167 testes aprovados (run 45710324028). RED da aprovação durável: 3 falhas e 167 aprovados (run 45711071291). A matriz multiplataforma foi configurada; resultado deve ser verificado no SHA final, não inferido deste documento.

## Correção de auditoria
Um checkpoint genérico completed podia substituir a aprovação do auditor ao retomar. A conclusão agora exige recibo transacional vinculado à operação confirmada, com digest da resposta validada, checkpoint e evento audit_approved. CLI status e resume não aceitam conclusão sem recibo. O digest não é assinatura nem defesa contra adulteração deliberada do banco por um processo com acesso de escrita.

## Riscos remanescentes e gates
| Prioridade | Evidência / componente | Disposição |
|---|---|---|
| Crítica para integração real | execution_mode fake é uma política do motor; adaptadores Python injetados executam com permissões do processo. WorkspaceManager não é sandbox. | Task 0 e isolamento efetivo antes de qualquer adaptador real. |
| Alta | WorkspaceManager usa git worktree add; hooks/filtros/configuração do Git podem executar código em repositório não confiável. | Somente repositórios locais confiáveis no fake; hardening específico antes de aceitar projetos não confiáveis. |
| Alta | ExecutionService deixa workspace pendente após crash; não há adoção/reconciliação automatizada. | Bloqueio conservador; revisão manual, sem apagar lock ou arquivos. |
| Alta | Aprovação humana interativa e persistência de grants não implementadas; evidências fake são declarações. | Não habilitar escrita real. Gate de aprovação e provas independentes necessários. |
| Alta | Timeout cooperativo e subprocessos Git síncronos com timeout próprio; não garante limite de tempo total da preparação/liberação nem interrupção de árvore de processos externa. | Controle de processos e prazo de ciclo de vida antes da integração real. |
| Alta | Banco e lock de recuperação são locais/cooperativos; caminhos/aliases, adulteração do banco e programas não cooperantes não têm garantia de exclusão. | Usar armazenamento local confiável; avaliar identidade canônica e ACLs antes de uso real. |
| Alta | Sanitização best-effort; identificadores podem conter dados fornecidos pelo usuário; logs antigos não são migrados; SQLite sem criptografia. | Nunca usar credenciais em IDs/objetivos/argumentos; cofre e política de retenção antes de uso real. |
| Média | GUI retoma pausa, não recupera processo interrompido; CLI suporta apenas fronteiras comprovadas. | Manter restrição documentada. |
| Média | Releases de worktree conservam branches; retenção/limpeza manual necessária. | Não remover automaticamente evidências. |
| Gate | Teste Windows local real (layout 900x680, iniciar/pausar/retomar/parar, logs e status). | Pendente; CI offscreen não substitui esse teste. |
| Gate | Revisão de segurança independente. | Não realizada nesta execução; revisão direta não equivale a auditoria independente. |

## Critério de saída
CI verde nas três combinações para o SHA candidato, validação Windows local com repositório descartável confiável, revisão de riscos e aprovação explícita de merge. Não liberar Task 0 automaticamente. Não alegar segurança de agentes reais, verificação de custos reais ou recuperação universal.
