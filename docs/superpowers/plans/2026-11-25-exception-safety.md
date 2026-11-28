# Etapa 8.7 — Exceções, cancelamento e credenciais

- Falhas de observador antes de despachos bloqueiam o avanço; uma falha durante a entrega de notificação terminal não altera o resultado já persistido. Notificação não é fonte de verdade.
- O cancelamento local e a pausa usam threading.Event; toggle é atômico. O motor reavalia pausa, stop e prazo após observadores e antes de despachar. Cancelamento de tarefa asyncio é propagado depois de tentativa de checkpoint cancelado. Um journal pendente permanece incerto. Não há promessa de interrupção imediata de processo externo.
- GUI e CLI tratam abertura e fechamento do banco e emitem códigos fixos, nunca texto de exceção. A GUI emite sucesso somente após fechamento do banco. Falhas de persistência não autorizam despacho.
- Redação de registros e eventos reconhece aliases de chaves sensíveis, padrões de credenciais rotuladas, cabeçalhos de autenticação e alguns formatos de tokens. Não é DLP universal, não protege segredos arbitrários sem rótulo, não sanitiza arquivos antigos retroativamente, não criptografa SQLite e não substitui um cofre de credenciais. Não inserir segredos em identificadores de execução/operação, objetivo ou argumentos da CLI. Resultados brutos em memória não constituem registros públicos.
- Mantém o perfil simulado. Integração do serviço com GUI e testes Windows permanecem para as etapas 8.9 e 8.10.
