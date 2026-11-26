"""Double-click assistant for the experimental read-only mode."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Absolute imports only: the packaged executable runs this file as a script.
from multi_ia.console import discard_pending_input
from multi_ia.experimental import run_experimental
from multi_ia.workspace import git_environment

MAX_CYCLES = 3
MAX_SECONDS = 900


class _Stop(Exception):
    """The person cancelled, or the program cannot continue."""


def _say(text: str = "") -> None:
    print(text, flush=True)


def _ask(question: str) -> str:
    print(question, end=" ", flush=True)
    line = sys.stdin.readline()
    if line == "" or line.strip().upper() == "CANCELAR":
        raise _Stop
    return line.strip()


def _repository_problem(answer: str) -> str | None:
    if not answer:
        return "Informe a pasta."
    if not Path(answer).is_dir():
        return "Pasta não encontrada."
    try:
        check = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=answer, env=git_environment(), capture_output=True, timeout=60,
        )
    except FileNotFoundError:
        _say("Git não encontrado. Instale o Git e abra o programa novamente.")
        raise _Stop from None
    except (OSError, subprocess.SubprocessError):
        return "Não foi possível consultar o Git nesta pasta."
    if check.returncode != 0:
        return "A pasta precisa ser um repositório Git com pelo menos um commit."
    try:
        committed = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD", "--", "."],
            cwd=answer, env=git_environment(), capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return "Não foi possível consultar o Git nesta pasta."
    if committed.returncode != 0 or not committed.stdout.strip():
        return "Esta pasta não tem arquivos no último commit."
    return None


def _ask_repository() -> str:
    while True:
        # Dragging a folder into the window adds quotes around it.
        answer = _ask("\nPasta do projeto (digite ou arraste a pasta):").strip('"').strip()
        problem = _repository_problem(answer)
        if problem is None:
            return answer
        _say(f"{problem} (ou digite CANCELAR)")


def _ask_goal() -> str:
    while True:
        goal = _ask("Objetivo (uma linha):")
        if goal:
            return goal
        _say("O objetivo não pode ficar vazio. (ou digite CANCELAR)")


def run_launcher(*, runner=run_experimental) -> int:
    try:
        _say("Multi-IA Orchestrator (experimental, somente leitura)")
        repo = _ask_repository()
        goal = _ask_goal()
        if discard_pending_input():
            _say(f"Linhas extras coladas foram descartadas. Objetivo usado: {goal}")
        _say()
        return runner(goal, repo, max_cycles=MAX_CYCLES, max_seconds=MAX_SECONDS)
    except (_Stop, KeyboardInterrupt):
        _say("\nEncerrado.")
        return 2
    except Exception:
        _say("\nFalha inesperada: internal_failure")
        return 2
    finally:
        # Keeps the window open so the report or the error can be read.
        try:
            discard_pending_input()
            print("\nPressione Enter para fechar.", flush=True)
            sys.stdin.readline()
        except KeyboardInterrupt:
            pass


def main() -> int:
    # A real console already handles Unicode; redirected streams may not.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    return run_launcher()


if __name__ == "__main__":
    raise SystemExit(main())
