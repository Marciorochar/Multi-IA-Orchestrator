from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

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
        self._conn.commit()

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

    def save_checkpoint(self, run_id: str, checkpoint: dict) -> None:
        safe_checkpoint = _redact(checkpoint)
        if safe_checkpoint.get("write_status") == "unconfirmed":
            safe_checkpoint["write_status"] = "needs_reconciliation"
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO checkpoints(run_id, checkpoint_json)
                VALUES (?, ?)
                ON CONFLICT(run_id) DO UPDATE SET checkpoint_json = excluded.checkpoint_json
                """,
                (run_id, json.dumps(safe_checkpoint, sort_keys=True)),
            )

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
        return not checkpoint or checkpoint.get("write_status") != "needs_reconciliation"

    def close(self) -> None:
        self._conn.close()
