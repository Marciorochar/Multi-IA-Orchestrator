"""Experimental read-only executor backed by the Claude Code CLI.

The restrictions were verified once against Claude Code 2.1.319 (see
docs/integrations/compatibility.md). They are CLI options, not an OS sandbox.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from multi_ia.adapters.process_tree import ProcessTree
from multi_ia.contracts import AgentRequest, AgentResult

READONLY_ARGS = (
    "--print", "--restricted", "--safe-mode", "--strict-mcp-config",
    "--tools", "Read,Glob,Grep", "--permission-mode", "dontAsk",
    "--no-session-persistence", "--output-format", "json",
)

_PROMPT = """You are the read-only analyst in a supervised workflow.
You can only read and search files in the current directory. Do not claim to
have changed files, run commands or run tests. Report findings with file paths,
and say what you could not verify. Reply in the language of the instructions.

Instructions from the planner:
"""


def claude_command() -> list[str] | None:
    executable = shutil.which("claude")
    return None if executable is None else [executable]


def _environment() -> dict[str, str]:
    # Matches the tested configuration: the CLI uses the person's own login and
    # does not inherit settings from a surrounding Claude session.
    return {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("CLAUDE", "ANTHROPIC"))
    }


async def _drain(process: asyncio.subprocess.Process) -> None:
    """Let the pipes close before the event loop does. Bounded, never raises."""
    try:
        await asyncio.wait_for(
            asyncio.gather(
                process.stdout.read(), process.stderr.read(), process.wait()
            ),
            timeout=10,
        )
    except (OSError, RuntimeError, TimeoutError):
        pass


def _failed(code: str) -> AgentResult:
    # A fixed code only: CLI output may contain file contents or credentials.
    return AgentResult("failed", {"error": code})


class ClaudeCodeExecutor:
    def __init__(self, workdir: str | Path, *, command: Sequence[str]) -> None:
        self.workdir = str(workdir)
        self.command = list(command)

    async def run(self, request: AgentRequest) -> AgentResult:
        instructions = request.payload.get("instructions")
        if type(instructions) is not str or not instructions.strip():
            return _failed("missing_instructions")
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command, *READONLY_ARGS,
                cwd=self.workdir, env=_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
        except OSError:
            return _failed("claude_not_started")
        tree = ProcessTree(process)
        try:
            # The prompt goes through stdin so no shell ever interprets it.
            output, _ = await process.communicate((_PROMPT + instructions).encode("utf-8"))
        finally:
            # Covers success, cancellation and the engine's time limit. A
            # descendant can still be running after the CLI itself exited.
            tree.kill()
            await _drain(process)
        if process.returncode != 0:
            return _failed(f"claude_exit_{process.returncode}")
        return self._parse(output)

    @staticmethod
    def _parse(output: bytes) -> AgentResult:
        try:
            data = json.loads(output.decode("utf-8"))
        except ValueError:
            return _failed("claude_invalid_output")
        if type(data) is not dict:
            return _failed("claude_invalid_output")
        if data.get("subtype") != "success" or data.get("is_error") is not False:
            return _failed("claude_reported_error")
        report = data.get("result")
        if type(report) is not str or not report.strip():
            return _failed("claude_empty_result")

        payload: dict = {"report": report}
        denials = data.get("permission_denials")
        if type(denials) is list:
            payload["permission_denials"] = len(denials)
        cost = data.get("total_cost_usd")
        if type(cost) in (int, float):
            payload["reported_cost_usd"] = cost
        # Records what the CLI returned; it does not prove the report is right.
        evidence = ["claude-code:report-sha256:"
                    + hashlib.sha256(report.encode("utf-8")).hexdigest()]
        session = data.get("session_id")
        if type(session) is str and session.strip():
            evidence.append(f"claude-code:session:{session}")
        return AgentResult("completed", payload, evidence)
