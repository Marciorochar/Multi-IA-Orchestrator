"""Experimental read-only run with real tools, from the terminal only.

Manual planner (ChatGPT) -> Claude Code reading a copy of HEAD -> human audit.
Nothing is persisted and the run cannot be resumed.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

from multi_ia.adapters.claude_code import ClaudeCodeExecutor, claude_command
from multi_ia.adapters.manual import ManualAuditor, ManualPlanner
from multi_ia.engine import CycleOutcome, Orchestrator, OrchestratorState
from multi_ia.policies import ExecutionPolicy

WARNING = """MODO EXPERIMENTAL SOMENTE LEITURA
- O Claude Code lerá uma cópia temporária dos arquivos do último commit (HEAD).
  Arquivos não versionados e alterações sem commit ficam de fora.
- Segredos que estejam no commit podem ser lidos e enviados à Anthropic.
- O que você colar no ChatGPT vai para a OpenAI.
- O uso consome cota ou gera custo; o orquestrador não controla esse gasto.
- Use apenas repositórios em que você confia. Isto não é um sandbox.
- A auditoria é feita por você. Nada é salvo e não há como retomar.
Digite "sim" para continuar:"""


def snapshot_head(repo: str | Path, target: str | Path) -> None:
    """Extract the files committed in HEAD, without .git, into target."""
    with tempfile.TemporaryDirectory(prefix="multi-ia-archive-") as scratch:
        archive = Path(scratch) / "head.tar"
        try:
            subprocess.run(
                ["git", "archive", "--format=tar", f"--output={archive}", "HEAD"],
                cwd=Path(repo).resolve(strict=True),
                check=True, capture_output=True, timeout=120,
            )
            with tarfile.open(archive) as bundle:
                # Refuses entries and links that would leave the target.
                bundle.extractall(target, filter="data")
        except (OSError, subprocess.SubprocessError, tarfile.TarError):
            raise ValueError("repository_snapshot_failed") from None


def _report(state: str, reason: str | None, **extra) -> None:
    print(json.dumps(
        {"mode": "experimental-readonly", "state": state, "reason": reason, **extra},
        sort_keys=True,
    ), flush=True)


def _progress(event: dict) -> None:
    if event.get("type") == "stage_started" and event.get("role") == "executor":
        print("\nClaude Code analisando a cópia do projeto... (Ctrl+C cancela)", flush=True)


def _reason(outcome: CycleOutcome) -> str | None:
    executor = outcome.results.get("executor")
    if executor is not None and executor.status == "failed":
        return executor.payload.get("error", outcome.reason)
    return outcome.reason


def run_experimental(goal: str, repo: str, *, max_cycles: int, max_seconds: float) -> int:
    try:
        if not goal.strip():
            raise ValueError("empty goal")
        policy = ExecutionPolicy(max_cycles=max_cycles, max_seconds=max_seconds, max_retries=0)
    except ValueError:
        _report("failed", "invalid_run_configuration")
        return 2

    print(WARNING, flush=True)
    if sys.stdin.readline().strip().lower() != "sim":
        _report("cancelled", "consent_declined")
        return 2
    command = claude_command()
    if command is None:
        _report("failed", "claude_not_found")
        return 2

    try:
        with tempfile.TemporaryDirectory(
            prefix="multi-ia-snapshot-", ignore_cleanup_errors=True
        ) as snapshot:
            snapshot_head(repo, snapshot)
            engine = Orchestrator(
                ManualPlanner(), ClaudeCodeExecutor(snapshot, command=command),
                ManualAuditor(), policy=policy, execution_mode="readonly",
                initial_payload={"goal": goal}, event_sink=_progress,
            )
            outcome = asyncio.run(engine.run(f"experimental-{uuid.uuid4().hex[:12]}"))
    except KeyboardInterrupt:
        _report("cancelled", "interrupted")
        return 2
    except ValueError as exc:
        _report("failed", str(exc))
        return 2
    except Exception:
        _report("failed", "internal_failure")
        return 2

    completed = outcome.state is OrchestratorState.COMPLETED
    extra = {"approved_by": "human"} if completed else {}
    _report(outcome.state.value, _reason(outcome), **extra)
    return 0 if completed else 2
