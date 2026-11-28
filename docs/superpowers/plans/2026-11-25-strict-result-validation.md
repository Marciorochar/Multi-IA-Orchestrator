# Etapa 8.8 — Strict Result Validation & Auditor Decision Controller

- O protocolo suportado é 1.0. Request exige run ID não vazio, ciclo inteiro positivo (não booleano), papel planner/executor/auditor e payload JSON estrito.
- Resultados são revalidados na fronteira do adaptador: protocolo, estado, JSON, evidência e campos opcionais de progresso (artifacts/tests: listas de strings não vazias). Dados inválidos não confirmam o journal.
- O motor cria snapshots independentes antes de emitir eventos, e requests copiam payloads. Isso evita alterações posteriores dos objetos do adaptador ou de dados anteriores via request.
- Auditor exige approved booleano explícito. Se tests_passed for informado, deve ser booleano; approved=true com tests_passed=false é inconsistente e bloqueado. approved=false mantém a política existente de novo ciclo ou revisão humana na retomada.
- Executor completed com status de escrita incerto não avança, inclusive no fake sem banco. Operações potencialmente mutáveis continuam exigindo journal e aprovação.
- Strings de evidência e flags são apenas declarações no perfil simulado, não comprovação independente de testes reais ou efeitos no filesystem. Adaptadores reais, associação de evidência ao artefato e verificação externa continuam bloqueados até Task 0. A proteção por cópia não é sandbox de código não confiável.
