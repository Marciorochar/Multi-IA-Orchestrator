"""Experimental read-only run with real tools, from the terminal only.

Manual planner (ChatGPT) -> Claude Code reading a copy of HEAD -> human audit.
Nothing is persisted and the run cannot be resumed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from multi_ia.adapters.claude_code import ClaudeCodeExecutor, claude_command
from multi_ia.adapters.manual import ManualAuditor, ManualPlanner
from multi_ia.console import discard_pending_input, on_console_close, process_alive
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
    # The archive stays next to the target, inside the same disposable folder.
    with tempfile.TemporaryDirectory(
        prefix="multi-ia-archive-", dir=Path(target).parent
    ) as scratch:
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


_SNAPSHOT_NAME = re.compile(r"multi-ia-snapshot-(\d+)-.+")


def remove_stale_snapshots(root: str | Path | None = None) -> None:
    """Remove copies left by runs whose process no longer exists."""
    try:
        entries = list(Path(root or tempfile.gettempdir()).iterdir())
    except OSError:
        return
    for entry in entries:
        match = _SNAPSHOT_NAME.fullmatch(entry.name)
        if (match and entry.is_dir() and not entry.is_symlink()
                and not process_alive(int(match.group(1)))):
            shutil.rmtree(entry, ignore_errors=True)


@contextmanager
def temporary_snapshot(root: str | Path | None = None) -> Iterator[Path]:
    """A disposable folder for the project copy, named after this process."""
    workspace = Path(tempfile.mkdtemp(
        prefix=f"multi-ia-snapshot-{os.getpid()}-", dir=root
    ))

    def remove() -> None:
        # A process that is still exiting can briefly hold the folder open.
        for _ in range(5):
            shutil.rmtree(workspace, ignore_errors=True)
            if not workspace.exists():
                return
            time.sleep(0.2)

    try:
        with on_console_close(remove):
            yield workspace
    finally:
        remove()
        if workspace.exists():
            print(f"Atenção: apague manualmente a cópia temporária: {workspace}", flush=True)


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
    # Pasted or typed-ahead text must not give consent.
    discard_pending_input()
    if sys.stdin.readline().strip().lower() != "sim":
        _report("cancelled", "consent_declined")
        return 2
    command = claude_command()
    if command is None:
        _report("failed", "claude_not_found")
        return 2

    try:
        remove_stale_snapshots()
        with temporary_snapshot() as workspace:
            snapshot = workspace / "tree"
            snapshot.mkdir()
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
