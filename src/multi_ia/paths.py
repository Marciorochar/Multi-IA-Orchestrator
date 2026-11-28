"""Shared database location for desktop and CLI entry points."""
from __future__ import annotations

import os
from pathlib import Path


def default_db_path() -> Path:
    override = os.environ.get("MULTI_IA_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".multi-ia-orchestrator" / "runs.sqlite"


def prepare_db_path(path: str | Path | None = None) -> Path:
    target = default_db_path() if path is None else Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
