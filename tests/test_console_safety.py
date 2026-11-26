"""Findings from the final review: pasted input, Ctrl+C, leftovers and empty folders."""
import asyncio
import io
import os
import signal
import subprocess
import sys

import pytest

from multi_ia import experimental
from multi_ia.adapters.manual import ManualAuditor, ManualPlanner
from multi_ia.console import discard_pending_input, on_console_close, process_alive
from multi_ia.contracts import AgentRequest
from multi_ia.launcher import run_launcher


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "project"
    (path / "sub").mkdir(parents=True)
    (path / "root.py").write_text("print(0)\n", encoding="utf-8")
    (path / "sub" / "app.py").write_text("print(1)\n", encoding="utf-8")
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "test")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, goal, repo, **limits):
        self.calls.append((goal, repo))
        return 0


class PendingInput:
    """Simulates a console where extra lines are waiting after a paste."""

    def __init__(self, stream, lines_waiting):
        self.stream, self.lines_waiting = stream, lines_waiting
        self.discards = []

    def __call__(self, stream=None):
        position = self.stream.tell()
        self.discards.append(position)
        waiting = self.lines_waiting.pop(position, 0)
        for _ in range(waiting):
            self.stream.readline()
        return waiting > 0


# 1. Pasted or typed-ahead input never answers a later question ----------------

def test_discard_ignores_redirected_input():
    stream = io.StringIO("sim\n")
    assert discard_pending_input(stream) is False
    assert stream.readline() == "sim\n"


def test_extra_goal_lines_are_discarded_and_reported(monkeypatch, repo, capsys):
    stream = io.StringIO(f"{repo}\nrevise:\nsim\n- testes\n\n")
    after_goal = len(f"{repo}\nrevise:\n")
    monkeypatch.setattr(sys, "stdin", stream)
    monkeypatch.setattr(
        "multi_ia.launcher.discard_pending_input", PendingInput(stream, {after_goal: 2}),
    )
    runner = Runner()

    assert run_launcher(runner=runner) == 0

    assert runner.calls == [("revise:", str(repo))]
    assert "descartadas" in capsys.readouterr().out
    assert stream.read() == ""


def test_consent_discards_pending_input_first(monkeypatch, repo):
    stream = io.StringIO("sim\nn\n")
    monkeypatch.setattr(sys, "stdin", stream)
    # The typed-ahead "sim" is waiting before the warning is answered.
    monkeypatch.setattr(experimental, "discard_pending_input", PendingInput(stream, {0: 1}))
    monkeypatch.setattr(experimental, "claude_command", lambda: pytest.fail("no consent"))

    assert experimental.run_experimental("g", str(repo), max_cycles=1, max_seconds=5) == 2


def audit_request():
    return AgentRequest("r", 1, "auditor", {"plan": {}, "execution": {"report": "x"}})


def test_audit_discards_input_typed_during_the_analysis(monkeypatch):
    stream = io.StringIO("s\nCANCELAR\n")
    monkeypatch.setattr(
        "multi_ia.adapters.manual.discard_pending_input", PendingInput(stream, {0: 1}),
    )
    result = asyncio.run(ManualAuditor(stream, io.StringIO()).run(audit_request()))
    assert result.status == "cancelled"


# 2. A folder with nothing committed is refused before the warning ------------

@pytest.mark.parametrize("kind", ["untracked", "ignored", "new_empty"])
def test_folder_without_committed_files_is_asked_again(monkeypatch, repo, capsys, kind):
    folder = repo / kind
    folder.mkdir()
    if kind != "new_empty":
        (folder / "file.txt").write_text("x\n", encoding="utf-8")
    if kind == "ignored":
        (repo / ".git" / "info" / "exclude").write_text("ignored/\n", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{folder}\n{repo}\nrevisar\n\n"))
    runner = Runner()

    run_launcher(runner=runner)

    assert runner.calls == [("revisar", str(repo))]
    assert "não tem arquivos no último commit" in capsys.readouterr().out


def test_subfolder_snapshot_contains_only_that_folder(repo, tmp_path):
    target = tmp_path / "out" / "tree"
    target.mkdir(parents=True)
    experimental.snapshot_head(repo / "sub", target)
    assert [p.name for p in target.rglob("*")] == ["app.py"]


# 3. Ctrl+C while waiting for the person takes effect at once ----------------

class CtrlC(io.StringIO):
    """Raises a real SIGINT during the read, as a console Ctrl+C would."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    def readline(self, *args):
        self.reads += 1
        if self.reads == 1:
            signal.raise_signal(signal.SIGINT)
        return "text\n" if self.reads < 4 else "FIM\n"


@pytest.mark.parametrize("cls,request_", [
    (ManualPlanner, AgentRequest("r", 1, "planner", {"goal": "g"})),
    (ManualAuditor, audit_request()),
])
def test_one_ctrl_c_cancels_the_bridge_read(cls, request_):
    stream = CtrlC()
    adapter = cls(stream, io.StringIO(), copy=lambda text: False)

    async def scenario():
        before = signal.getsignal(signal.SIGINT)
        result = await adapter.run(request_)
        assert signal.getsignal(signal.SIGINT) is before
        return result

    assert asyncio.run(scenario()).status == "cancelled"
    assert stream.reads == 1


# 4. The copy of the project does not stay behind -----------------------------

def test_snapshot_workspace_is_removed_after_use(tmp_path):
    with experimental.temporary_snapshot(root=tmp_path) as workspace:
        assert workspace.name.startswith(f"multi-ia-snapshot-{os.getpid()}-")
        (workspace / "file.txt").write_text("x", encoding="utf-8")
    assert list(tmp_path.iterdir()) == []


def test_closing_the_console_removes_the_snapshot(monkeypatch, tmp_path):
    handlers = []

    def register(callback):
        from contextlib import nullcontext
        handlers.append(callback)
        return nullcontext()

    monkeypatch.setattr(experimental, "on_console_close", register)
    with experimental.temporary_snapshot(root=tmp_path) as workspace:
        (workspace / "file.txt").write_text("x", encoding="utf-8")
        handlers[0]()
        assert not workspace.exists()


def test_leftovers_from_dead_processes_are_removed(tmp_path):
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    dead = tmp_path / f"multi-ia-snapshot-{finished.pid}-abc"
    alive = tmp_path / f"multi-ia-snapshot-{os.getpid()}-abc"
    unrelated = tmp_path / "multi-ia-snapshot-notes"
    for folder in (dead, alive, unrelated):
        folder.mkdir()
        (folder / "file.txt").write_text("x", encoding="utf-8")

    experimental.remove_stale_snapshots(tmp_path)

    assert not dead.exists()
    assert alive.exists() and unrelated.exists()


def test_process_alive_does_not_harm_the_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        assert process_alive(child.pid) is True
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_failed_cleanup_is_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(experimental.shutil, "rmtree", lambda *a, **k: None)
    with experimental.temporary_snapshot(root=tmp_path) as workspace:
        pass
    assert str(workspace) in capsys.readouterr().out


def test_close_handler_can_be_registered_and_removed():
    with on_console_close(lambda: None):
        pass
