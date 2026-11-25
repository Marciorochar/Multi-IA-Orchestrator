# Core Hardening — Etapa 8

Base: main 669b98505dc24841241ce5dde6143858daa148a9. Abordagem incremental com Application Service aprovada na conversa. Escopo: execução simulada, sem novos adaptadores reais.

## Abordagens
Correções pontuais não resolvem a divergência GUI/CLI. Reescrita total aumenta regressões. Escolhida camada de serviço comum, preservando contratos existentes quando seguro.

## Invariantes
- Nenhuma execução real, publicação, produção ou ação externa nesta etapa. Worktree isola alterações Git, não é sandbox.
- Planner e auditor não têm permissão de escrita; executor recebe somente workspace gerenciado. Enforcement efetivo depende de adaptadores futuros e gate Task 0.
- Nenhum resultado parcial, inválido ou sem aprovação booleana explícita conclui uma execução.
- Registrar intenção durável antes de cada operação potencialmente mutável. Falha, interrupção ou resultado incerto preserva necessidade de reconciliação; não repetir escrita automaticamente.
- Identidade de run e operação única, lock de proprietário verificável, paths canonizados e limites comuns por repositório Git (inclusive worktrees vinculadas).
- Preservar worktree com alterações, evidências e locks necessários à recuperação. Nunca usar remoção forçada ou apagar alterações do usuário.
- Aprovações humanas vinculadas à execução, operação e escopo; desconhecido nega por padrão. Aprovação do auditor não substitui aprovação humana.
- Retomada verifica checkpoint, versão, configuração, workspace, evidências e estado de reconciliação. Não reiniciar silenciosamente o ciclo 1.
- Limites verificáveis de ciclos, tempo total, tentativas de operações comprovadamente seguras e orçamento; sem contabilidade verificável, orçamento finito bloqueia execução real. Não estimar custo fictício como custo real.
- Pausa cooperativa em limites seguros; parada impede novos despachos; não alegar interrupção imediata de processos externos.
- Não persistir credenciais, prompts/saídas brutas indiscriminadamente nem exceções com segredos.
- CLI e GUI compartilham política e persistência; nenhuma mudança visual estrutural obrigatória. Diretório comum configurável de dados; banco legado não é descartado.
- Falhas de persistência bloqueiam operações mutáveis. Eventos e checkpoint coerentes e transacionais; persistência não equivale a autorização.
- Execuções em Windows e Linux devem ser verificadas antes de liberar agentes reais.

## Decisões técnicas propostas
Manter Python 3.12+, asyncio, SQLite, Git e PySide6. Serviço comum de execução com estado versionado. Adotar fail-closed, sem autoaprovação e sem retry de escrita incerta. Retomada de checkpoints legados insuficientes exige revisão humana. Execuções de teste fake podem continuar usando diretórios não Git apenas quando não há escrita e isso está explícito; modo mutável sempre requer Git e isolamento. Gui e CLI não terão bypass de segurança. A estrutura exata dos adaptadores reais será definida após Task 0.

## Gates de saída
Testes de falha injetada (antes/depois de despacho), concorrência, recuperação, aprovação, limites, validação e segredos; suíte existente preservada; CI Windows e Linux; revisão de diff e teste local Windows. Não liberar integração real apenas por aprovação do CI.
