import subprocess

import pytest

from multi_ia.policies import ExecutionPolicy, ProgressGuard
from multi_ia.workspace import WorkspaceManager


def make_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)


def test_workspace_is_isolated(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorkspaceManager()

    workspace = manager.prepare(str(repo), "run-1")
    try:
        assert workspace != str(repo)
        assert (manager.path_for(workspace) / ".git").exists()
        assert (manager.path_for(workspace) / "README.md").read_text() == "base\n"
    finally:
        manager.release(str(repo), workspace)


def test_parallel_writes_are_rejected(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo)
    first = WorkspaceManager()
    second = WorkspaceManager()
    workspace = first.prepare(str(repo), "run-1")
    try:
        with pytest.raises(RuntimeError, match="writer"):
            second.prepare(str(repo), "run-2")
    finally:
        first.release(str(repo), workspace)


def test_repeated_no_progress_pauses():
    guard = ProgressGuard(max_stagnant_cycles=2)
    snapshot = {"status": "needs_work", "evidence": ["same"], "artifacts": []}

    assert guard.evaluate(snapshot, snapshot.copy()) == "continue"
    assert guard.evaluate(snapshot, snapshot.copy()) == "pause"


def test_max_cycles_stops():
    policy = ExecutionPolicy(max_cycles=2, max_seconds=30, max_retries=0)

    assert policy.allows_cycle(1) is True
    assert policy.allows_cycle(2) is True
    assert policy.allows_cycle(3) is False


def test_verified_success_completes():
    guard = ProgressGuard(max_stagnant_cycles=2)

    assert guard.evaluate({}, {"verified_success": True, "evidence": ["tests pass"]}) == "complete"
