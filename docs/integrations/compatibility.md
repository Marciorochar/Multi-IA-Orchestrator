# Matriz de integração — Task 0

**Status:** pendente de validação técnica. A publicação da documentação no GitHub não constitui teste das instalações Windows do usuário.

| Agente | Mecanismo | Autenticação | Início | Conclusão | Resultado | Cancelamento | Estado |
|---|---|---|---|---|---|---|---|
| ChatGPT / planejador | API ou interface existente: a decidir | A verificar | A verificar | A verificar | A verificar | A verificar | Pendente |
| Antigravity / executor | Descobrir e validar interface suportada | A verificar | A verificar | A verificar | A verificar | A verificar | Pendente |
| Codex / auditor | CLI ou interface suportada: a validar | A verificar | A verificar | A verificar | A verificar | A verificar | Pendente |

## Decisão necessária

Escolher `session_mode`: `existing_ui` (sessões e janelas já abertas) ou `independent` (sessões independentes por API/CLI).

**Não implementar adaptadores reais até a decisão e os testes não destrutivos.**

## Critério de saída

Para cada integração, registrar evidências de entrada, conclusão, saída e cancelamento, incluindo limitações de segurança, licenciamento e custos.

A etapa 0 é um gate técnico: se o mecanismo não puder ser comprovado, o módulo deve permanecer não implementado e uma alternativa explícita deve ser apresentada.
