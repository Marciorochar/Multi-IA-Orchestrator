import asyncio
import json
import sqlite3
import subprocess
import sys

import pytest

from multi_ia.cli import main
from multi_ia.policies import ExecutionPolicy
from multi_ia.store import RunStore


def args(db, repo, command="start", goal="demo"):
    return [command, "--run-id", "run-1", "--db", str(db),
            "--goal", goal, "--repo", str(repo), "--max-cycles", "2"]


def test_cli_duplicate_start_preserves_existing_run(tmp_path, capsys):
    db = tmp_path / "runs.sqlite"
    assert main(args(db, tmp_path)) == 0
    store = RunStore(db)
    before = store.load_checkpoint("run-1")
    events = store.list_events("run-1")
    assert main(args(db, tmp_path)) == 3
    assert store.load_checkpoint("run-1") == before
    assert store.list_events("run-1") == events
    assert "run_already_exists" in capsys.readouterr().out
    store.close()


def test_cli_legacy_checkpoint_is_not_silently_restarted(tmp_path, capsys):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    checkpoint = {"state": "planning", "cycle_id": 1}
    store.save_checkpoint("run-1", checkpoint)
    assert main(args(db, tmp_path, "resume")) == 3
    assert store.load_checkpoint("run-1") == checkpoint
    assert store.list_operations("run-1") == []
    assert "legacy_checkpoint" in capsys.readouterr().out
    store.close()


def crash_after_confirmation(db, repo):
    # Force a real process exit immediately after the journal transaction commits.
    code = """
import asyncio, os, sys
from multi_ia.store import RunStore
from multi_ia.service import ExecutionService
from multi_ia.policies import ExecutionPolicy
class CrashStore(RunStore):
    def confirm_operation(self, *args, **kwargs):
        super().confirm_operation(*args, **kwargs)
        os._exit(73)
store = CrashStore(sys.argv[1])
service = ExecutionService(store, goal="demo", repo=sys.argv[2],
                           policy=ExecutionPolicy(2, 300, 0))
asyncio.run(service.start("run-1"))
"""
    child = subprocess.run([sys.executable, "-c", code, str(db), str(repo)],
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 73, child.stderr


def test_recovery_after_process_exit_runs_only_auditor(tmp_path):
    from multi_ia.service import ExecutionService
    db = tmp_path / "runs.sqlite"
    crash_after_confirmation(db, tmp_path)
    store = RunStore(db)
    try:
        before = store.list_operations("run-1")
        assert before[0]["status"] == "confirmed"
        events = []
        service = ExecutionService(store, goal="demo", repo=str(tmp_path),
                                   policy=ExecutionPolicy(2, 300, 0), event_sink=events.append)
        result = asyncio.run(service.resume("run-1"))
        assert result.state.value == "completed"
        assert [e["role"] for e in events if e["type"] == "stage_started"] == ["auditor"]
        assert store.list_operations("run-1") == before
        assert store.load_checkpoint("run-1")["state"] == "completed"
        events.clear()
        assert asyncio.run(service.resume("run-1")).state.value == "completed"
        assert events == []
    finally:
        store.close()


@pytest.mark.parametrize("change", ["goal", "repo", "policy", "snapshot", "stop"])
def test_recovery_refuses_divergent_or_unproven_context(tmp_path, change):
    from multi_ia.service import ExecutionService
    db = tmp_path / "runs.sqlite"
    crash_after_confirmation(db, tmp_path)
    store = RunStore(db)
    try:
        goal, repo, policy = "demo", tmp_path, ExecutionPolicy(2, 300, 0)
        if change == "goal":
            goal = "other"
        elif change == "repo":
            repo = tmp_path / "other"
            repo.mkdir()
        elif change == "policy":
            policy = ExecutionPolicy(3, 300, 0)
        elif change == "snapshot":
            checkpoint = store.load_checkpoint("run-1")
            checkpoint["recovery"]["plan_digest"] = "0" * 64
            store.save_checkpoint("run-1", checkpoint)
        else:
            store.request_stop("run-1")
        before = store.load_checkpoint("run-1")
        events = []
        service = ExecutionService(store, goal=goal, repo=str(repo),
                                   policy=policy, event_sink=events.append)
        result = asyncio.run(service.resume("run-1"))
        assert result.state.value == "failed"
        assert result.reason in {"recovery_config_mismatch", "recovery_evidence_invalid", "stop_requested"}
        assert not any(e["type"] == "stage_started" for e in events)
        assert store.load_checkpoint("run-1") == before
        assert len(store.list_operations("run-1")) == 1
    finally:
        store.close()


def test_service_rejects_concurrent_resume_while_start_is_active(tmp_path):
    from multi_ia.service import ExecutionService
    from multi_ia.adapters.fake import FakeAdapter
    from multi_ia.contracts import AgentResult
    db = tmp_path / "runs.sqlite"
    first, second = RunStore(db), RunStore(db)
    events = []

    async def scenario():
        first_service = ExecutionService(first, goal="demo", repo=str(tmp_path),
                                         policy=ExecutionPolicy(2, 300, 0))
        second_service = ExecutionService(second, goal="demo", repo=str(tmp_path),
                                          policy=ExecutionPolicy(2, 300, 0))
        from multi_ia.service import fake_adapters
        planner, executor, _ = fake_adapters()
        class HoldingAuditor:
            async def run(self, request):
                blocked = await second_service.resume("run-1")
                events.append(blocked.reason)
                return AgentResult("completed", {"approved": True}, ["fake:auditor:approved"])
        first_service.adapters = (planner, executor, HoldingAuditor())
        result = await first_service.start("run-1")
        assert result.state.value == "completed"
    try:
        asyncio.run(scenario())
        assert events == ["run_busy"]
    finally:
        first.close()
        second.close()


def test_audit_already_dispatched_is_not_repeated(tmp_path):
    from multi_ia.service import ExecutionService
    db = tmp_path / "runs.sqlite"
    crash_after_confirmation(db, tmp_path)
    store = RunStore(db)
    try:
        checkpoint = store.load_checkpoint("run-1")
        checkpoint["phase"] = "audit_started"
        checkpoint["state"] = "auditing"
        store.save_checkpoint("run-1", checkpoint)
        events = []
        service = ExecutionService(store, goal="demo", repo=str(tmp_path),
                                   policy=ExecutionPolicy(2, 300, 0), event_sink=events.append)
        result = asyncio.run(service.resume("run-1"))
        assert result.reason == "recovery_boundary_unsupported"
        assert events == []
    finally:
        store.close()
