"""Findings from the pull request review: Git overrides and surviving descendants."""
import asyncio
import io
import subprocess
import sys
import time

import pytest

from multi_ia.adapters.claude_code import ClaudeCodeExecutor
from multi_ia.contracts import AgentRequest
from multi_ia.experimental import snapshot_head
from multi_ia.launcher import run_launcher


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(path, filename):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "test")
    (path / filename).write_text("x\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


# Inherited Git overrides must not change which repository is read ------------

@pytest.fixture
def selected_and_other(tmp_path):
    return (make_repo(tmp_path / "selected", "selected.txt"),
            make_repo(tmp_path / "other", "private.txt"))


def inherit_overrides(monkeypatch, other):
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))


def test_snapshot_ignores_inherited_git_overrides(
        monkeypatch, tmp_path, selected_and_other):
    selected, other = selected_and_other
    inherit_overrides(monkeypatch, other)
    target = tmp_path / "out" / "tree"
    target.mkdir(parents=True)

    snapshot_head(selected, target)

    assert [p.name for p in target.iterdir()] == ["selected.txt"]


def test_launcher_validation_ignores_inherited_git_overrides(
        monkeypatch, tmp_path, selected_and_other, capsys):
    selected, other = selected_and_other
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    inherit_overrides(monkeypatch, other)
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{plain}\n{selected}\ngoal\n\n"))
    calls = []

    run_launcher(runner=lambda goal, repo, **limits: calls.append(repo) or 0)

    assert calls == [str(selected)]


# Descendants must not outlive the executor ----------------------------------

def spawning_cli(tmp_path, marker, *, keeps_pipes):
    """Starts a delayed writer and exits at once."""
    writer = f"import time; time.sleep(3); open({str(marker)!r}, 'w').write('x')"
    streams = ("stdout=sys.stdout, stderr=sys.stderr" if keeps_pipes else
               "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
               "stderr=subprocess.DEVNULL")
    script = tmp_path / "exits_early.py"
    script.write_text(
        "import json, subprocess, sys\n"
        "sys.stdin.read()\n"
        f"subprocess.Popen([sys.executable, '-c', {writer!r}], {streams})\n"
        "print(json.dumps({'subtype': 'success', 'is_error': False, 'result': 'ok'}),"
        " flush=True)\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


REQUEST = AgentRequest("r", 1, "executor", {"instructions": "x"})


@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_descendant_holding_the_pipes_is_killed_after_the_parent_exits(
        tmp_path, ending):
    marker = tmp_path / "survivor.txt"
    command = spawning_cli(tmp_path, marker, keeps_pipes=True)

    async def scenario():
        run = ClaudeCodeExecutor(tmp_path, command=command).run(REQUEST)
        if ending == "timeout":
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(run, timeout=1.5)
            return
        task = asyncio.create_task(run)
        await asyncio.sleep(1.5)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    time.sleep(3.5)
    assert not marker.exists()


def test_detached_descendant_is_killed_after_a_successful_run(tmp_path):
    marker = tmp_path / "survivor.txt"
    command = spawning_cli(tmp_path, marker, keeps_pipes=False)

    result = asyncio.run(ClaudeCodeExecutor(tmp_path, command=command).run(REQUEST))
    time.sleep(3.5)

    assert result.status == "completed"
    assert not marker.exists()
