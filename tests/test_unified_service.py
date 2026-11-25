import asyncio
import json
import subprocess
from pathlib import Path

from multi_ia.cli import main, build_parser
from multi_ia.gui.worker import OrchestratorWorker
from multi_ia.policies import ExecutionPolicy
from multi_ia.service import ExecutionService, fake_adapters
from multi_ia.store import RunStore
from multi_ia.workspace import WorkspaceManager


def make_repo(root):
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return root


def test_gui_and_cli_share_registration_and_duplicate_guard(tmp_path, capsys):
    db = tmp_path / "runs.sqlite"
    worker = OrchestratorWorker(run_id="shared", goal="demo", repo_path=str(tmp_path),
                                db_path=db, max_cycles=1)
    results = []
    worker.finished.connect(results.append)
    worker.run()
    assert results[0]["state"] == "completed"
    store = RunStore(db)
    try:
        assert store.run_registration("shared") is not None
        assert store.load_limits("shared") is not None
        before = store.load_checkpoint("shared")
        events = store.list_events("shared")
        common = ["--run-id", "shared", "--db", str(db), "--goal", "demo",
                  "--repo", str(tmp_path), "--max-cycles", "1"]
        assert main(["resume", *common]) == 0
        assert main(["start", *common]) == 3
        assert "run_already_exists" in capsys.readouterr().out
        assert store.load_checkpoint("shared") == before
        assert store.list_events("shared") == events
    finally:
        store.close()


def test_cli_and_gui_defaults_use_same_database(tmp_path, monkeypatch):
    from multi_ia.paths import default_db_path
    db = tmp_path / "custom" / "runs.sqlite"
    monkeypatch.setenv("MULTI_IA_DB_PATH", str(db))
    parser = build_parser()
    assert Path(parser.parse_args(["status", "--run-id", "r"]).db) == db
    worker = OrchestratorWorker(run_id="r", goal="demo", repo_path=str(tmp_path))
    assert worker.db_path == default_db_path() == db


def test_git_service_uses_isolated_workspace_and_preserves_source(tmp_path):
    repo = make_repo(tmp_path / "repo")
    (repo / "README.md").write_text("local changes\n", encoding="utf-8")
    store = RunStore(tmp_path / "runs.sqlite")
    observed = []
    planner, executor, auditor = fake_adapters()

    class InspectPlanner:
        async def run(self, request):
            path = Path(request.payload["repo"])
            observed.append(path)
            assert path != repo
            assert (path / "README.md").read_text(encoding="utf-8") == "base\n"
            assert (repo / ".git" / "multi-ia-writer.lock").exists()
            return await planner.run(request)

    try:
        service = ExecutionService(store, goal="demo", repo=str(repo),
                                   policy=ExecutionPolicy(1, 30, 0),
                                   adapters=(InspectPlanner(), executor, auditor))
        outcome = asyncio.run(service.start("r"))
        assert outcome.state.value == "completed"
        assert observed and not observed[0].exists()
        assert (repo / "README.md").read_text(encoding="utf-8") == "local changes\n"
        assert not (repo / ".git" / "multi-ia-writer.lock").exists()
        assert store.workspace_status("r") == "released"
    finally:
        store.close()


def test_gui_cannot_dispatch_when_git_writer_lock_is_held(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorkspaceManager()
    workspace = manager.prepare(str(repo), "owner")
    try:
        worker = OrchestratorWorker(run_id="blocked", goal="demo", repo_path=str(repo),
                                    db_path=tmp_path / "runs.sqlite", max_cycles=1)
        events, results = [], []
        worker.event.connect(events.append)
        worker.finished.connect(results.append)
        worker.run()
        assert results[0]["reason"] == "workspace_unavailable"
        assert not any(e["type"] == "stage_started" for e in events)
        assert Path(workspace).exists()
    finally:
        manager.release(str(repo), workspace)


def test_dirty_workspace_blocks_success_and_preserves_evidence(tmp_path):
    repo = make_repo(tmp_path / "repo")
    store = RunStore(tmp_path / "runs.sqlite")
    planner, executor, auditor = fake_adapters()
    paths, events = [], []

    class DirtyExecutor:
        async def run(self, request):
            path = Path(request.payload["repo"])
            paths.append(path)
            (path / "untracked.txt").write_text("preserve", encoding="utf-8")
            return await executor.run(request)

    try:
        service = ExecutionService(store, goal="demo", repo=str(repo),
                                   policy=ExecutionPolicy(1, 30, 0),
                                   adapters=(planner, DirtyExecutor(), auditor),
                                   event_sink=events.append)
        result = asyncio.run(service.start("r"))
        assert result.reason == "workspace_reconciliation_required"
        assert result.state.value == "failed"
        assert store.workspace_status("r") == "pending"
        assert store.load_checkpoint("r")["state"] == "failed"
        assert not any(e["type"] == "run_completed" for e in events)
        assert paths[0] != repo
        assert (paths[0] / "untracked.txt").read_text() == "preserve"
        assert not (repo / "untracked.txt").exists()
        assert (repo / ".git" / "multi-ia-writer.lock").exists()
        assert asyncio.run(service.resume("r")).reason == "workspace_reconciliation_required"
    finally:
        store.close()
