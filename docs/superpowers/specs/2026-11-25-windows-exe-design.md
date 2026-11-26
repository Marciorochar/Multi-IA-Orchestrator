# Executável Windows do modo experimental

**Status:** aprovado pelo usuário em 2026-11-25.

## Intenção
Usar o modo com o Claude Code real com um duplo clique, sem digitar o comando. Escolha do usuário: "Terminal com Claude real".

## Como será o uso
```text
Multi-IA Orchestrator (experimental, somente leitura)

Pasta do projeto (digite ou arraste a pasta): C:\caminho\do\projeto
Objetivo (uma linha): revisar o projeto e apontar erros

[aviso atual do modo experimental]  Digite "sim" para continuar:
... planejador (copiar e colar) -> Claude Code -> sua auditoria ...

Pressione Enter para fechar.
```

## Comportamento
- Sem argumentos, o programa faz as duas perguntas. Aspas ao redor da pasta (arrastar e soltar) são removidas.
- Pasta inexistente, que não seja um repositório Git ou sem commit, e objetivo vazio: explica o problema e pergunta de novo, antes do aviso. `CANCELAR` ou fim da entrada encerra.
- Depois das perguntas, chama o mesmo `run_experimental` já existente. Consentimento, restrições e auditoria humana não mudam. Valem os padrões atuais: 3 ciclos e 900 s de tempo dos agentes.
- A janela só fecha após Enter, inclusive em caso de erro, para o relatório poder ser lido e copiado. Nada é salvo em arquivo (decisão minha, igual ao modo atual).
- Git e Claude Code instalados e logados continuam necessários; não vão dentro do executável. Nesta máquina, ambos estão no PATH salvo.

## Abordagens
| Abordagem | Contrapartida |
|---|---|
| **PyInstaller, arquivo único (escolhida, decisão minha)** | Um só `.exe`, fácil de copiar. Abre um pouco mais devagar, pois se extrai a cada execução. |
| PyInstaller, pasta | Abre mais rápido, mas exige manter a pasta inteira junto. |
| Atalho ou `.cmd` chamando o Python | Sem dependência nova, mas não é um executável e depende do Python e desta cópia do código. |

## Partes
- `src/multi_ia/launcher.py`: perguntas, validação, chamada e espera final. Com testes automáticos.
- `scripts/build_exe.ps1`: gera `dist\multi-ia.exe` (`dist/` e `build/` já são ignorados pelo Git).
- `pyproject.toml`: extra `build` com o PyInstaller.
- README: como gerar e usar.

## Verificação
Testes existentes e novos; gerar o `.exe`; executá-lo com um repositório descartável, respostas por script e o Claude Code real. O duplo clique em console real, a colagem de textos longos, os acentos e Ctrl+C precisam de teste manual do usuário.

## Fora do escopo
Interface gráfica, instalador, assinatura digital, ícone, atualização automática e validação do Codex. Sem assinatura, o Windows ou o antivírus podem alertar sobre o arquivo.

## Riscos
A compatibilidade do PyInstaller com o Python 3.14 desta máquina só será confirmada ao gerar. O executável é uma cópia do código no momento da geração: mudanças exigem gerar de novo.
