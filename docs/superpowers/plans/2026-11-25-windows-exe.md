# Executável Windows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Abrir o modo experimental somente leitura com um duplo clique em `dist\multi-ia.exe`.

**Architecture:** Um assistente de terminal (`launcher.py`) pergunta pasta e objetivo, valida e chama o `run_experimental` existente, sem mudar consentimento, restrições ou auditoria. O PyInstaller empacota esse assistente em um único arquivo.

**Tech Stack:** Python 3.14 (local), PyInstaller, PowerShell, pytest.

**Spec:** [2026-11-25-windows-exe-design.md](../specs/2026-11-25-windows-exe-design.md)

## Global Constraints

- Padrões inalterados: 3 ciclos e 900 s de tempo dos agentes.
- Textos ao usuário em português.
- Git e Claude Code não são incluídos no executável.
- Nada é salvo em arquivo. Sem interface gráfica, instalador, ícone ou assinatura.
- `requires-python = ">=3.12"` permanece; o executável não exige PySide6.
- Commits somente quando o usuário pedir.

## Review Focus

1. Pasta arrastada, com aspas, espaços e acentos: deve ser aceita.
2. Subpasta de um repositório: aceita; a cópia contém apenas essa subpasta.
3. Repositório sem commit: explicar e perguntar de novo, antes do aviso.
4. Git fora do PATH: explicar e encerrar, sem repetir a pergunta indefinidamente.
5. Ctrl+C ou fim da entrada durante as perguntas: encerrar sem traceback e sem iniciar agentes.

---

### Task 1: Assistente de terminal

**Files:**
- Create: `src/multi_ia/launcher.py`
- Test: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `run_experimental(goal: str, repo: str, *, max_cycles: int, max_seconds: float) -> int` de `multi_ia.experimental`.
- Produces: `run_launcher(*, runner=run_experimental) -> int` (usa `sys.stdin` e `sys.stdout`) e `main() -> int`, que ajusta a codificação para UTF-8 quando a entrada ou saída não é um console e chama `run_launcher()`.

- [ ] **Step 1: Write the failing tests**

Cada teste substitui `sys.stdin` por `io.StringIO`, usa um `runner` que grava os argumentos e devolve um código, e cria repositórios Git em `tmp_path`.

| Teste | Entrada | Verificação |
|---|---|---|
| `test_passes_answers_and_defaults_to_runner` | pasta, `revisar`, Enter | `calls == [("revisar", str(repo), 3, 900)]`; retorno igual ao do runner (7); saída contém `Pressione Enter para fechar.` |
| `test_accepts_quoted_path_with_spaces_and_accents` | `"<...\meu projeto ção>"` | runner recebe o caminho sem aspas |
| `test_accepts_repository_subfolder` | subpasta versionada | runner recebe a subpasta |
| `test_asks_again_for_invalid_folder` (parametrizado: inexistente, sem Git, sem commit, vazio) | inválida, depois válida | uma chamada, com a válida |
| `test_asks_again_for_empty_goal` | linha vazia, depois `revisar` | uma chamada com `revisar` |
| `test_cancel_never_starts_agents` (parametrizado: `CANCELAR`, `cancelar`, fim da entrada, cada um na pasta e no objetivo) | | `calls == []`; retorno 2 |
| `test_ctrl_c_during_questions_exits_cleanly` | `stdin.readline` levanta `KeyboardInterrupt` | `calls == []`; retorno 2 |
| `test_missing_git_explains_and_stops` | `subprocess.run` levanta `FileNotFoundError` | saída contém `Git não encontrado`; retorno 2; `calls == []` |
| `test_window_waits_even_when_runner_fails` | runner levanta `RuntimeError` | retorno 2; saída contém `internal_failure` e `Pressione Enter para fechar.` |

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest -q tests/test_launcher.py`
Expected: FAIL, `No module named 'multi_ia.launcher'`

- [ ] **Step 3: Implement `run_launcher` and `main`**

Validação: `git rev-parse --verify HEAD` com `cwd` na pasta. A espera pelo Enter fica em um `finally` e tolera `KeyboardInterrupt` e fim da entrada. O módulo termina com `if __name__ == "__main__": raise SystemExit(main())`. Usar apenas imports absolutos, pois o PyInstaller executa o arquivo como script.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest -q`
Expected: todos passam (222 anteriores e os novos)

### Task 2: Geração, verificação e documentação do executável

**Files:**
- Create: `scripts/build_exe.ps1`
- Modify: `pyproject.toml` (extra `build = ["pyinstaller>=6.15"]`)
- Modify: `README.md` (como gerar, usar e limitações)
- Modify: `docs/superpowers/specs/2026-11-25-windows-exe-design.md` (status: aprovado)

**Interfaces:**
- Consumes: `src/multi_ia/launcher.py`.
- Produces: `dist\multi-ia.exe`.

- [ ] **Step 1: Declare the extra and install it**

Run: `python -m pip install -e ".[build]"`
Expected: PyInstaller instalado. Se não houver versão compatível com Python 3.14, parar e informar o usuário.

- [ ] **Step 2: Write `scripts/build_exe.ps1`**

Parte da raiz do repositório (`$PSScriptRoot`), falha com código diferente de zero e executa `python -m PyInstaller --onefile --console --clean --noconfirm --name multi-ia --paths src --exclude-module PySide6 --specpath build src/multi_ia/launcher.py`.

- [ ] **Step 3: Build**

Run: `pwsh scripts/build_exe.ps1`
Expected: saída 0 e `dist\multi-ia.exe` existente; `git status --short` não lista `dist/` nem `build/`.

- [ ] **Step 4: Verify the executable**

Com um script no diretório temporário, executar o `.exe`:
- entrada `CANCELAR` e Enter: saída 2, sem iniciar agentes;
- repositório descartável com erro plantado e `.env` não versionado, respostas por script, **Claude Code real** (consome cota): saída 0; o relatório cita `calc.py`; o conteúdo do `.env` não aparece; o repositório não muda; não sobra pasta `multi-ia-snapshot-*`; acentos corretos na saída.

- [ ] **Step 5: Document, then run the suite**

Run: `python -m pytest -q` e `git diff --check`
Expected: todos passam, sem erros de espaços.

Pendente de teste manual pelo usuário: duplo clique, colagem longa, acentos e Ctrl+C em console real.
