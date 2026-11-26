# Matriz de integração — Task 0

**Status:** parcialmente validada em 2026-11-25, em uma máquina Windows 11 local. Há um modo experimental somente leitura, apenas pelo terminal (`multi-ia experimental`); CLI padrão e interface gráfica continuam simuladas.

## Decisões do usuário (2026-11-25)

- `session_mode`: `independent` (sessões independentes por CLI).
- Planejador: ChatGPT no aplicativo desktop. A forma de conexão proposta é uma **ponte manual** (a pessoa copia o pedido e cola a resposta), sem automação do aplicativo; confirmada pelo usuário.
- Dispensa do gate adicional: autorizada **apenas** para o modo experimental somente leitura na máquina local. Os testes Linux/Windows em CI, o teste visual e as pendências de segurança continuam em aberto.
- Enquanto o Codex não for validado, a auditoria é humana.
- Executor: Claude Code, **somente leitura** na primeira versão. Substitui o Antigravity.
- Auditor: Codex.

## Matriz

| Papel / ferramenta | Mecanismo | Autenticação | Início | Conclusão e resultado | Cancelamento | Estado |
|---|---|---|---|---|---|---|
| Planejador / ChatGPT desktop | Ponte manual | Sessão da própria pessoa no aplicativo | Manual | Testada com respostas simuladas; colagem em console real não testada | `CANCELAR` ou fim da entrada, coberto por testes; Ctrl+C em console real não testado | Implementada, aguarda teste de uso |
| Executor / Claude Code 2.1.319 | `claude --print` | Funcionou sem variáveis `CLAUDE*`/`ANTHROPIC*` herdadas | Comprovado | Comprovado: saída 0 e JSON com `subtype`, `is_error`, `result`, `permission_denials`, `total_cost_usd` | Comprovado com `taskkill /T /F`, sem processos remanescentes | Validado para somente leitura, com as ressalvas abaixo |
| Auditor / Codex CLI 0.171.0 | `codex exec --json` | `codex login status`: conta ChatGPT | Comprovado (`thread.started`) | **Apenas falha** observada: saída 1 e `turn.failed` por limite de uso. Sucesso não comprovado | Não testado | Pendente |
| Alternativa não escolhida / Antigravity CLI 1.5.3 | `agy --print` | Funcionou com a sessão local | Comprovado | Comprovado: saída 0 e JSON com `status`, `response`, `usage` | Comprovado, sem processos remanescentes | Não será usado; somente leitura **não** comprovada |

## Evidências do executor

Argumentos testados:

```text
--print --restricted --safe-mode --strict-mcp-config --tools Read,Glob,Grep
--permission-mode dontAsk --no-session-persistence --output-format json
```

Em uma pasta descartável, com o pedido enviado por stdin:

- criar um arquivo: não havia ferramenta de escrita e o arquivo não foi criado;
- ler um arquivo de teste fora da pasta de trabalho: negado (`permission_denials: Read`) e o conteúdo não apareceu na saída;
- ler um arquivo dentro da pasta: funcionou.

Ressalvas: é um único teste de permissão em uma versão específica, não uma auditoria de isolamento nem um sandbox do sistema operacional. Arquivos dentro da pasta de trabalho, inclusive eventuais segredos, ficam legíveis e podem ser enviados ao fornecedor. `total_cost_usd` é o valor informado pela CLI; a correspondência com a cobrança do plano não foi verificada. Uma atualização da CLI exige repetir o teste. O cancelamento foi testado 6 s após o início, sem confirmar que o fornecedor interrompe o processamento remoto.

Teste de ponta a ponta em 2026-11-25, com a CLI real, um repositório descartável e respostas humanas simuladas por script: o relatório apontou o erro plantado, listou apenas os dois arquivos do commit, o conteúdo de um `.env` não versionado não apareceu na saída, o repositório de origem não mudou e a cópia temporária foi removida.

## Observações sobre o auditor

- Argumentos testados: `exec --sandbox read-only --skip-git-repo-check --ephemeral --ignore-user-config --json -C <pasta>`. A restrição de escrita do `read-only` **não** foi comprovada, porque o turno não chegou a executar.
- No Windows o executável é um `.CMD`. O adaptador deve enviar o pedido por stdin, nunca como argumento, para evitar interpretação pelo `cmd.exe`.

## Observação sobre o Antigravity

Durante um teste, a árvore de processos do `agy` incluía processos `node.exe` e `cmd.exe` adicionais. A origem deles (por exemplo, extensões ou servidores MCP configurados) não foi verificada.

## Pendências

1. Testar a ponte manual em um console real, incluindo colagem longa e Ctrl+C.
2. Comprovar sucesso, somente leitura e cancelamento do Codex antes de substituir a auditoria humana.
3. Atender o gate adicional antes de qualquer uso além do modo experimental.

**Cada adaptador automático só deve ser habilitado depois que a sua linha estiver comprovada.**

## Critério de saída

Para cada integração, registrar evidências de entrada, conclusão, saída e cancelamento, incluindo limitações de segurança, licenciamento e custos. Licenciamento e custos ainda não foram verificados.

A etapa 0 é um gate técnico: se o mecanismo não puder ser comprovado, o módulo deve permanecer não implementado e uma alternativa explícita deve ser apresentada.

## Gate adicional — Core Hardening
A aprovação do núcleo fake não habilita adaptadores reais. Exigir execução bem-sucedida dos testes Linux/Windows, validação visual e operacional no Windows local e encerramento das pendências do relatório de segurança aplicáveis ao escopo real. Cada adaptador necessita comprovação independente de identidade da sessão, permissões efetivamente aplicadas, isolamento, contabilidade de custo, término/cancelamento e associação de evidência aos artefatos reais. Não considerar um worktree um sandbox. Não liberar produção/publicação nesta fase.
