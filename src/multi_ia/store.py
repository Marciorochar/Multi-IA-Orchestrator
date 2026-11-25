from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from multi_ia.contracts import Operation


class JournalBlocked(RuntimeError):
    """A persisted operation cannot safely be dispatched."""

SENSITIVE_KEYS = {"token", "password", "secret", "api_key", "authorization", "credential"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key.lower() in SENSITIVE_KEYS else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class RunStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_json TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id TEXT PRIMARY KEY,
                checkpoint_json TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS controls (
                run_id TEXT PRIMARY KEY,
                stop_requested INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS write_journal (
                run_id TEXT NOT NULL,
                cycle_id INTEGER NOT NULL,
                operation_id TEXT NOT NULL,
                action TEXT NOT NULL,
                scope TEXT NOT NULL,
                effect TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'confirmed')),
                PRIMARY KEY (run_id, cycle_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_runs (
                run_id TEXT PRIMARY KEY,
                config_digest TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()

    def register_run(self, run_id: str, config_digest: str) -> None:
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            if (self.load_checkpoint(run_id) is not None
                    or self.run_registration(run_id) is not None
                    or self.list_operations(run_id)):
                raise JournalBlocked("run_already_exists")
            self._conn.execute(
                "INSERT INTO recovery_runs VALUES (?, ?, 1)",
                (run_id, config_digest),
            )
            self._write_checkpoint(run_id, {
                "schema_version": 1, "cycle_id": 1,
                "state": "planning", "phase": "registered",
            })

    def run_registration(self, run_id: str) -> tuple | None:
        return self._conn.execute(
            "SELECT config_digest, schema_version FROM recovery_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    def claim_audit(self, run_id: str, cycle_id: int) -> None:
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            checkpoint = self.load_checkpoint(run_id)
            if not checkpoint or (
                checkpoint.get("phase") != "executor_completed"
                or checkpoint.get("cycle_id") != cycle_id
                or checkpoint.get("write_status") != "confirmed"
            ):
                raise JournalBlocked("recovery_boundary_unsupported")
            checkpoint["phase"] = "audit_started"
            checkpoint["state"] = "auditing"
            self._write_checkpoint(run_id, checkpoint)
            self._write_event(run_id, {"type": "audit_started", "cycle_id": cycle_id})

    def append_event(self, run_id: str, event: dict) -> None:
        safe_event = _redact(event)
        with self._conn:
            self._conn.execute(
                "INSERT INTO events(run_id, event_json) VALUES (?, ?)",
                (run_id, json.dumps(safe_event, sort_keys=True)),
            )

    def list_events(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT event_json FROM events WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _write_checkpoint(self, run_id: str, checkpoint: dict) -> None:
        self._conn.execute(
            """
            INSERT INTO checkpoints(run_id, checkpoint_json)
            VALUES (?, ?)
            ON CONFLICT(run_id) DO UPDATE SET checkpoint_json = excluded.checkpoint_json
            """,
            (run_id, json.dumps(_redact(checkpoint), sort_keys=True)),
        )

    def _write_event(self, run_id: str, event: dict) -> None:
        self._conn.execute(
            "INSERT INTO events(run_id, event_json) VALUES (?, ?)",
            (run_id, json.dumps(_redact(event), sort_keys=True)),
        )

    def list_operations(self, run_id: str) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT * FROM write_journal WHERE run_id = ? ORDER BY cycle_id",
            (run_id,),
        )
        keys = [column[0] for column in cursor.description]
        return [dict(zip(keys, row)) for row in cursor.fetchall()]

    def dispatch_block_reason(self, run_id: str, cycle_id: int) -> str | None:
        checkpoint = self.load_checkpoint(run_id)
        if not self.can_replay_write(checkpoint):
            return "needs_reconciliation"
        if self._conn.execute(
            "SELECT 1 FROM write_journal WHERE run_id = ? AND status = 'pending'",
            (run_id,),
        ).fetchone():
            return "needs_reconciliation"
        if self._conn.execute(
            "SELECT 1 FROM write_journal WHERE run_id = ? AND cycle_id >= ?",
            (run_id, cycle_id),
        ).fetchone():
            return "operation_replayed"
        return None

    @staticmethod
    def _operation_values(operation: Operation) -> tuple:
        return (
            operation.run_id, operation.cycle_id, operation.operation_id,
            operation.action, operation.scope, operation.effect,
            operation.plan_digest, operation.version,
        )

    def begin_operation(self, operation: Operation) -> None:
        if type(operation) is not Operation:
            raise ValueError("invalid operation")
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            reason = self.dispatch_block_reason(operation.run_id, operation.cycle_id)
            if reason:
                raise JournalBlocked(reason)
            self._conn.execute(
                """
                INSERT INTO write_journal
                (run_id, cycle_id, operation_id, action, scope, effect, plan_digest, version, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                self._operation_values(operation),
            )
            self._write_checkpoint(operation.run_id, {
                "schema_version": 1, "cycle_id": operation.cycle_id,
                "state": "executing", "phase": "write_intent",
                "operation_id": operation.operation_id,
                "write_status": "needs_reconciliation",
            })
            self._write_event(operation.run_id, {
                "type": "write_intent", "cycle_id": operation.cycle_id,
                "operation_id": operation.operation_id,
                "plan_digest": operation.plan_digest,
            })

    def confirm_operation(self, operation: Operation, *, recovery: dict | None = None) -> None:
        """Commit confirmation for the exact reserved simulated operation."""
        if type(operation) is not Operation:
            raise ValueError("invalid operation")
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            cursor = self._conn.execute(
                """
                UPDATE write_journal SET status = 'confirmed'
                WHERE run_id = ? AND cycle_id = ? AND operation_id = ?
                  AND action = ? AND scope = ? AND effect = ?
                  AND plan_digest = ? AND version = ? AND status = 'pending'
                """,
                self._operation_values(operation),
            )
            if cursor.rowcount != 1:
                raise JournalBlocked("needs_reconciliation")
            checkpoint = {
                "schema_version": 1, "cycle_id": operation.cycle_id,
                "state": "executing", "phase": "executor_completed",
                "operation_id": operation.operation_id,
                "write_status": "confirmed",
            }
            if recovery is not None:
                checkpoint["recovery"] = recovery
            self._write_checkpoint(operation.run_id, checkpoint)
            self._write_event(operation.run_id, {
                "type": "write_confirmed", "cycle_id": operation.cycle_id,
                "operation_id": operation.operation_id,
                "plan_digest": operation.plan_digest,
            })

    def save_checkpoint(self, run_id: str, checkpoint: dict) -> None:
        safe_checkpoint = _redact(checkpoint)
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            previous = self.load_checkpoint(run_id)
            pending = self._conn.execute(
                "SELECT 1 FROM write_journal WHERE run_id = ? AND status = 'pending'",
                (run_id,),
            ).fetchone()
            if (
                pending or not self.can_replay_write(previous)
                or safe_checkpoint.get("write_status") == "unconfirmed"
            ):
                safe_checkpoint["write_status"] = "needs_reconciliation"
            elif previous and previous.get("write_status") == "confirmed":
                safe_checkpoint["write_status"] = "confirmed"
            if previous and previous.get("schema_version") == 1:
                safe_checkpoint["schema_version"] = 1
                if previous.get("cycle_id") == safe_checkpoint.get("cycle_id"):
                    safe_checkpoint.setdefault("operation_id", previous.get("operation_id"))
            self._write_checkpoint(run_id, safe_checkpoint)

    def load_checkpoint(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT checkpoint_json FROM checkpoints WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def request_stop(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO controls(run_id, stop_requested)
                VALUES (?, 1)
                ON CONFLICT(run_id) DO UPDATE SET stop_requested = 1
                """,
                (run_id,),
            )

    def clear_stop_request(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO controls(run_id, stop_requested)
                VALUES (?, 0)
                ON CONFLICT(run_id) DO UPDATE SET stop_requested = 0
                """,
                (run_id,),
            )

    def stop_requested(self, run_id: str) -> bool:
        row = self._conn.execute(
            "SELECT stop_requested FROM controls WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return bool(row and row[0])

    @staticmethod
    def can_replay_write(checkpoint: dict | None) -> bool:
        return not checkpoint or checkpoint.get("write_status") not in {"needs_reconciliation", "unconfirmed"}

    def close(self) -> None:
        self._conn.close()
