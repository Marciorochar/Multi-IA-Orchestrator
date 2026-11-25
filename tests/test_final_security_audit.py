import asyncio
import json
import sqlite3
import subprocess
import sys

from multi_ia.cli import main
from multi_ia.policies import ExecutionPolicy
from multi_ia.service import ExecutionService
from multi_ia.store import RunStore


def crash_after_executor(db, repo):
    code = """
import asyncio, os, sys
from multi_ia.service import ExecutionService
from multi_ia.store import RunStore
from multi_ia.policies import ExecutionPolicy
class CrashStore(RunStore):
    def confirm_operation(self, *args, **kwargs):
        super().confirm_operation(*args, **kwargs)
        os._exit(73)
asyncio.run(ExecutionService(CrashStore(sys.argv[1]), goal="demo",
    repo=sys.argv[2], policy=ExecutionPolicy(1, 300, 0)).start("r"))
"""
    child = subprocess.run([sys.executable, "-c", code, str(db), str(repo)],
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 73, child.stderr


def test_generic_completed_checkpoint_cannot_substitute_for_auditor(tmp_path, capsys):
    db = tmp_path / "runs.sqlite"
    crash_after_executor(db, tmp_path)
    store = RunStore(db)
    try:
        cp = store.load_checkpoint("r")
        cp["state"] = "completed"
        store.save_checkpoint("r", cp)
        before = store.load_checkpoint("r")
        service = ExecutionService(store, goal="demo", repo=str(tmp_path),
                                   policy=ExecutionPolicy(1, 300, 0))
        result = asyncio.run(service.resume("r"))
        assert result.state.value == "failed"
        assert result.reason == "audit_evidence_missing"
        assert store.load_checkpoint("r") == before
        assert main(["status", "--run-id", "r", "--db", str(db)]) == 0
        assert json.loads(capsys.readouterr().out)["state"] != "completed"
    finally:
        store.close()


def test_completed_run_has_durable_audit_evidence(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite")
    try:
        service = ExecutionService(store, goal="demo", repo=str(tmp_path),
                                   policy=ExecutionPolicy(1, 300, 0))
        assert asyncio.run(service.start("r")).state.value == "completed"
        assert store.has_completed_audit("r", 1)
        assert not store.has_completed_audit("r", 2)
        assert asyncio.run(service.resume("r")).state.value == "completed"
    finally:
        store.close()


def test_failed_audit_completion_transaction_cannot_report_success(tmp_path):
    db = tmp_path / "runs.sqlite"
    store = RunStore(db)
    try:
        with sqlite3.connect(db) as other:
            other.execute("""
                CREATE TRIGGER reject_approval BEFORE INSERT ON events
                WHEN json_extract(NEW.event_json, '$.type') = 'audit_approved'
                BEGIN SELECT RAISE(ABORT, 'injected'); END
            """)
        service = ExecutionService(store, goal="demo", repo=str(tmp_path),
                                   policy=ExecutionPolicy(1, 300, 0))
        result = asyncio.run(service.start("r"))
        assert result.state.value == "failed"
        assert result.reason == "persistence_failure"
        assert store.load_checkpoint("r")["state"] != "completed"
        assert not store.has_completed_audit("r", 1)
    finally:
        store.close()
