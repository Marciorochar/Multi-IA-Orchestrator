"""Experimental read-only mode. No real agent or network is used here."""
import asyncio
import io
import json
import subprocess
import sys
import time

import pytest

from multi_ia.adapters.base import AgentAdapter
from multi_ia.adapters.claude_code import READONLY_ARGS, ClaudeCodeExecutor
from multi_ia.adapters.manual import ManualAuditor, ManualPlanner
from multi_ia.cli import main
from multi_ia.contracts import AgentRequest, AgentResult
from multi_ia.engine import Orchestrator, OrchestratorState
from multi_ia.experimental import snapshot_head
from multi_ia.policies import ExecutionPolicy
from multi_ia.store import RunStore


def done(payload=None):
    return AgentResult("completed", payload or {}, ["test:ok"])


class Static:
    def __init__(self, result, calls=None, delay=0, human=False):
        self.result, self.calls, self.delay = result, calls, delay
        self.waits_for_human = human

    async def run(self, request):
        if self.calls is not None:
            self.calls.append(request.role)
        # Blocking on purpose: the manual bridge blocks while a person types.
        time.sleep(self.delay)
        await asyncio.sleep(0)
        return self.result


def engine(planner=None, executor=None, auditor=None, **kwargs):
    kwargs.setdefault("policy", ExecutionPolicy(1, 30, 0))
    return Orchestrator(
        planner or Static(done()), executor or Static(done()),
        auditor or Static(done({"approved": True})),
        execution_mode="readonly", **kwargs,
    )


# Engine -------------------------------------------------------------------

def test_readonly_mode_completes_a_read_cycle():
    assert asyncio.run(engine().run("r")).state is OrchestratorState.COMPLETED


@pytest.mark.parametrize("action", ["write_workspace", "delete"])
def test_readonly_mode_never_dispatches_a_mutating_operation(action):
    calls = []
    plan = {"operation": {
        "version": "1.0", "operation_id": "op", "run_id": "r", "cycle_id": 1,
        "scope": "simulation", "effect": "simulated", "action": action,
    }}
    outcome = asyncio.run(engine(
        planner=Static(done(plan)), executor=Static(done(), calls),
    ).run("r"))
    assert outcome.reason == "readonly_mode"
    assert calls == []


def test_readonly_mode_rejects_a_claimed_write():
    calls = []
    outcome = asyncio.run(engine(
        executor=Static(done({"write_status": "confirmed"})),
        auditor=Static(done({"approved": True}), calls),
    ).run("r"))
    assert outcome.reason == "invalid_result"
    assert calls == []


def test_readonly_mode_refuses_the_simulated_journal(tmp_path):
    calls = []
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        outcome = asyncio.run(engine(planner=Static(done(), calls), store=store).run_cycle("r", 1))
    finally:
        store.close()
    assert outcome.reason == "readonly_persistence_unsupported"
    assert calls == []


def test_time_waiting_for_a_person_is_not_limited():
    outcome = asyncio.run(engine(
        planner=Static(done(), delay=.5, human=True),
        auditor=Static(done({"approved": True}), delay=.5, human=True),
        policy=ExecutionPolicy(1, .3, 0),
    ).run("r"))
    assert outcome.state is OrchestratorState.COMPLETED


def test_agent_time_is_still_limited():
    outcome = asyncio.run(engine(
        executor=Static(done(), delay=.5), policy=ExecutionPolicy(1, .3, 0),
    ).run("r"))
    assert outcome.reason == "timeout"


def test_human_flag_is_ignored_outside_readonly_mode():
    outcome = asyncio.run(Orchestrator(
        Static(done(), delay=.5, human=True), Static(done()),
        Static(done({"approved": True})), policy=ExecutionPolicy(1, .3, 0),
    ).run("r"))
    assert outcome.reason == "timeout"


# Claude Code executor ------------------------------------------------------

FAKE_CLI = """
import json, os, sys
record = {"args": sys.argv[2:], "stdin": sys.stdin.buffer.read().decode("utf-8"),
          "cwd": os.getcwd(),
          "leaked": [k for k in os.environ if k.upper().startswith(("CLAUDE", "ANTHROPIC"))]}
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(record))
"""


def fake_claude(tmp_path, body):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI + body, encoding="utf-8")
    return [sys.executable, str(script), str(tmp_path / "record.json")]


def reply(**overrides):
    data = {"subtype": "success", "is_error": False, "result": "relatório ç",
            "session_id": "abc-123", "permission_denials": [], "total_cost_usd": 0.01}
    data.update(overrides)
    return f"print(json.dumps({data!r}))"


def execute(command, workdir, instructions="analyse & echo %PATH%"):
    request = AgentRequest("r", 1, "executor", {"instructions": instructions})
    return asyncio.run(ClaudeCodeExecutor(workdir, command=command).run(request))


def test_executor_sends_prompt_by_stdin_with_restrictions(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_TEST_VARIABLE", "x")
    result = execute(fake_claude(tmp_path, reply()), tmp_path)
    record = json.loads((tmp_path / "record.json").read_text(encoding="utf-8"))

    assert record["args"] == list(READONLY_ARGS)
    for flag in ("--restricted", "--safe-mode", "--strict-mcp-config"):
        assert flag in record["args"]
    assert record["args"][record["args"].index("--tools") + 1] == "Read,Glob,Grep"
    assert "analyse & echo %PATH%" in record["stdin"]
    assert record["leaked"] == []
    assert record["cwd"] == str(tmp_path)
    assert result.status == "completed"
    assert result.payload["report"] == "relatório ç"
    assert result.payload["reported_cost_usd"] == 0.01
    assert "claude-code:session:abc-123" in result.evidence
    assert "write_status" not in result.payload


@pytest.mark.parametrize("body,error", [
    ("sys.exit(3)", "claude_exit_3"),
    ("print('not json')", "claude_invalid_output"),
    ("print('[1]')", "claude_invalid_output"),
    (reply(is_error=True), "claude_reported_error"),
    (reply(subtype="error_max_turns"), "claude_reported_error"),
    (reply(result="  "), "claude_empty_result"),
])
def test_executor_fails_closed_with_a_safe_code(tmp_path, body, error):
    result = execute(fake_claude(tmp_path, body), tmp_path)
    assert (result.status, result.payload) == ("failed", {"error": error})


def test_executor_requires_instructions(tmp_path):
    result = execute(fake_claude(tmp_path, reply()), tmp_path, instructions="   ")
    assert result.payload == {"error": "missing_instructions"}
    assert not (tmp_path / "record.json").exists()


def test_cancelling_the_executor_kills_the_process_tree(tmp_path):
    marker = tmp_path / "survivor.txt"
    grandchild = f"import time; time.sleep(2); open({str(marker)!r}, 'w').write('x')"
    body = (
        "import subprocess, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
        "time.sleep(30)"
    )
    command = fake_claude(tmp_path, body)

    async def scenario():
        request = AgentRequest("r", 1, "executor", {"instructions": "x"})
        task = asyncio.create_task(ClaudeCodeExecutor(tmp_path, command=command).run(request))
        await asyncio.sleep(1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    time.sleep(2.5)
    assert not marker.exists()


# Manual bridge --------------------------------------------------------------

def bridge(cls, typed):
    output, copied = io.StringIO(), []
    adapter = cls(io.StringIO(typed), output, copy=lambda text: copied.append(text) or True)
    return adapter, output, copied


def plan_request(**extra):
    return AgentRequest("r", 1, "planner", {"goal": "revisar", "repo": r"C:\Users\someone\p", **extra})


def test_manual_planner_returns_the_pasted_text():
    planner, _, copied = bridge(ManualPlanner, "passo 1\n\npasso 2\nFIM\nignored\n")
    result = asyncio.run(planner.run(plan_request()))
    assert result.status == "completed"
    assert result.payload == {"instructions": "passo 1\n\npasso 2"}
    assert "revisar" in copied[0]
    # The local path (which may contain the user name) is not sent to the provider.
    assert "someone" not in copied[0]


def test_manual_planner_shows_previous_feedback():
    planner, _, copied = bridge(ManualPlanner, "novo plano\nFIM\n")
    asyncio.run(planner.run(plan_request(
        previous_cycle={"cycle_id": 1, "audit": {"feedback": "faltou o teste"}},
    )))
    assert "faltou o teste" in copied[0]


@pytest.mark.parametrize("typed", ["", "texto sem fim\n", "CANCELAR\n"])
def test_manual_planner_cancels_on_eof_or_request(typed):
    planner, _, _ = bridge(ManualPlanner, typed)
    assert asyncio.run(planner.run(plan_request())).status == "cancelled"


def test_manual_planner_asks_again_after_an_empty_reply():
    planner, _, _ = bridge(ManualPlanner, "FIM\nplano\nFIM\n")
    assert asyncio.run(planner.run(plan_request())).payload == {"instructions": "plano"}


def audit_request(report="achado\x1b[2J"):
    return AgentRequest("r", 1, "auditor", {"plan": {}, "execution": {"report": report}})


def test_manual_auditor_needs_an_explicit_answer():
    auditor, output, _ = bridge(ManualAuditor, "talvez\n\ns\n")
    result = asyncio.run(auditor.run(audit_request()))
    assert result.payload == {"approved": True}
    assert "achado" in output.getvalue()
    # Terminal control characters from an agent are not replayed.
    assert "\x1b" not in output.getvalue()


def test_manual_auditor_rejection_carries_feedback():
    auditor, _, _ = bridge(ManualAuditor, "n\nfaltou X\nFIM\n")
    result = asyncio.run(auditor.run(audit_request()))
    assert result.payload == {"approved": False, "feedback": "faltou X"}


def test_manual_auditor_eof_never_approves():
    auditor, _, _ = bridge(ManualAuditor, "")
    assert asyncio.run(auditor.run(audit_request())).status == "cancelled"


# Snapshot and command --------------------------------------------------------

def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "test")
    (path / "app.py").write_text("print(1)\n", encoding="utf-8")
    (path / ".gitignore").write_text(".env\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    (path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (path / "untracked.txt").write_text("local\n", encoding="utf-8")
    (path / "app.py").write_text("print('uncommitted')\n", encoding="utf-8")
    return path


def test_snapshot_contains_only_committed_files(repo, tmp_path):
    target = tmp_path / "snapshot"
    target.mkdir()
    snapshot_head(repo, target)
    assert sorted(p.name for p in target.iterdir()) == [".gitignore", "app.py"]
    assert (target / "app.py").read_text(encoding="utf-8").strip() == "print(1)"


def test_snapshot_requires_a_git_repository(tmp_path):
    with pytest.raises(ValueError):
        snapshot_head(tmp_path, tmp_path / "snapshot")


def run_command(monkeypatch, repo, typed, command):
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    monkeypatch.setattr("multi_ia.experimental.claude_command", lambda: command)
    monkeypatch.setattr("multi_ia.adapters.manual.copy_to_clipboard", lambda text: False)
    return main(["experimental", "--goal", "revisar", "--repo", str(repo)])


def test_command_does_nothing_without_consent(monkeypatch, repo, tmp_path, capsys):
    command = fake_claude(tmp_path, reply())
    assert run_command(monkeypatch, repo, "n\n", command) == 2
    assert not (tmp_path / "record.json").exists()
    assert "consent_declined" in capsys.readouterr().out


def test_command_runs_a_full_cycle_with_rejection_feedback(monkeypatch, repo, tmp_path, capsys):
    command = fake_claude(tmp_path, reply())
    typed = "sim\nplano 1\nFIM\nn\nfaltou X\nFIM\nplano 2\nFIM\ns\n"

    assert run_command(monkeypatch, repo, typed, command) == 0

    output = capsys.readouterr().out
    assert "faltou X" in output
    assert '"state": "completed"' in output
    assert '"approved_by": "human"' in output
    record = json.loads((tmp_path / "record.json").read_text(encoding="utf-8"))
    assert "plano 2" in record["stdin"]
    # The agent worked on a temporary copy, which is removed afterwards.
    assert record["cwd"] != str(repo)
    from pathlib import Path
    assert not Path(record["cwd"]).exists()


def test_command_reports_a_missing_claude_cli(monkeypatch, repo, capsys):
    assert run_command(monkeypatch, repo, "sim\n", None) == 2
    assert "claude_not_found" in capsys.readouterr().out
