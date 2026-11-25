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


def test_linked_worktree_shares_repository_lock_across_processes(tmp_path):
    import sys
    from pathlib import Path

    repo = tmp_path / "repo"
    make_repo(repo)
    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", "--detach", str(linked), "HEAD"],
                   cwd=repo, check=True, capture_output=True)
    assert (linked / ".git").is_file()
    manager = WorkspaceManager()
    workspace = manager.prepare(str(repo), "owner")
    try:
        child = subprocess.run(
            [sys.executable, "-c",
             "import sys; from multi_ia.workspace import WorkspaceManager; "
             "WorkspaceManager().prepare(sys.argv[1], 'other')", str(linked)],
            capture_output=True, text=True, timeout=20,
        )
        assert child.returncode != 0
        assert "writer lock already held" in child.stderr
        assert Path(workspace).is_dir()
    finally:
        manager.release(str(repo), workspace)
    workspace = manager.prepare(str(linked), "linked-owner")
    try:
        assert Path(workspace).is_dir()
    finally:
        manager.release(str(repo), workspace)


def test_non_owner_cannot_release_workspace(tmp_path):
    from pathlib import Path

    repo = tmp_path / "repo"
    make_repo(repo)
    owner = WorkspaceManager()
    workspace = owner.prepare(str(repo), "owner")
    with pytest.raises(RuntimeError, match="owner"):
        WorkspaceManager().release(str(repo), workspace)
    assert Path(workspace).is_dir()
    with pytest.raises(RuntimeError, match="writer"):
        WorkspaceManager().prepare(str(repo), "other")
    owner.release(str(repo), workspace)


def test_foreign_lock_is_not_removed_by_owner(tmp_path):
    from pathlib import Path

    repo = tmp_path / "repo"
    make_repo(repo)
    owner = WorkspaceManager()
    workspace = owner.prepare(str(repo), "owner")
    lock = repo / ".git" / "multi-ia-writer.lock"
    original = lock.read_bytes()
    lock.write_text("foreign", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="owner"):
            owner.release(str(repo), workspace)
        assert lock.read_text(encoding="utf-8") == "foreign"
        assert Path(workspace).is_dir()
    finally:
        lock.write_bytes(original)
        if Path(workspace).exists():
            owner.release(str(repo), workspace)


def test_distinct_run_ids_cannot_collide_after_normalization(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorkspaceManager()
    first = manager.prepare(str(repo), "run/a")
    manager.release(str(repo), first)
    second = manager.prepare(str(repo), "run-a")
    try:
        assert first != second
    finally:
        manager.release(str(repo), second)


def test_invalid_run_id_does_not_leave_lock(tmp_path):
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorkspaceManager()
    with pytest.raises(ValueError):
        manager.prepare(str(repo), "   ")
    assert not (repo / ".git" / "multi-ia-writer.lock").exists()
    workspace = manager.prepare(str(repo), "valid")
    manager.release(str(repo), workspace)


def test_prepare_failure_before_creation_releases_only_own_lock(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorkspaceManager()
    original_run = subprocess.run

    def fail_add(args, *a, **kw):
        if "worktree" in args and "add" in args:
            raise subprocess.CalledProcessError(1, args)
        return original_run(args, *a, **kw)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", fail_add)
        with pytest.raises(subprocess.CalledProcessError):
            manager.prepare(str(repo), "failure")
    assert not (repo / ".git" / "multi-ia-writer.lock").exists()
    workspace = manager.prepare(str(repo), "next")
    manager.release(str(repo), workspace)


def test_partial_creation_preserves_lock_and_worktree(tmp_path, monkeypatch):
    from pathlib import Path

    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorkspaceManager()
    original_run = subprocess.run
    created = []

    def fail_after_add(args, *a, **kw):
        result = original_run(args, *a, **kw)
        if "worktree" in args and "add" in args:
            created.append(Path(args[-2]))
            raise subprocess.CalledProcessError(1, args)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", fail_after_add)
        with pytest.raises(subprocess.CalledProcessError):
            manager.prepare(str(repo), "partial")
    assert created and created[0].is_dir()
    assert (repo / ".git" / "multi-ia-writer.lock").exists()
    with pytest.raises(RuntimeError, match="writer"):
        WorkspaceManager().prepare(str(repo), "next")


def test_dirty_workspace_retains_changes_and_lock(tmp_path):
    from pathlib import Path

    repo = tmp_path / "repo"
    make_repo(repo)
    owner = WorkspaceManager()
    workspace = owner.prepare(str(repo), "owner")
    modified = Path(workspace) / "README.md"
    modified.write_text("changed", encoding="utf-8")
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        owner.release(str(repo), workspace)
    assert modified.read_text(encoding="utf-8") == "changed"
    assert (repo / "README.md").read_text(encoding="utf-8") == "base\n"
    with pytest.raises(RuntimeError, match="writer"):
        WorkspaceManager().prepare(str(repo), "next")


def test_ignored_files_are_not_deleted_by_release(tmp_path):
    from pathlib import Path

    repo = tmp_path / "repo"
    make_repo(repo)
    (repo / ".gitignore").write_text("private.log\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "ignore"], cwd=repo, check=True)
    owner = WorkspaceManager()
    workspace = owner.prepare(str(repo), "owner")
    private = Path(workspace) / "private.log"
    private.write_text("preserve", encoding="utf-8")
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        owner.release(str(repo), workspace)
    assert private.read_text(encoding="utf-8") == "preserve"
    assert (repo / ".git" / "multi-ia-writer.lock").exists()
