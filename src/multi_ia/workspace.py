from __future__ import annotations

import re
import subprocess
from pathlib import Path


class WorkspaceManager:
    @staticmethod
    def path_for(path: str) -> Path:
        return Path(path)

    @staticmethod
    def _safe_run_id(run_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip("-.")
        if not safe:
            raise ValueError("run_id must contain a filesystem-safe character")
        return safe

    def prepare(self, repo_path: str, run_id: str) -> str:
        repo = Path(repo_path).resolve()
        if not (repo / ".git").exists():
            raise ValueError("repo_path must be a Git worktree")

        lock = repo / ".git" / "multi-ia-writer.lock"
        try:
            with lock.open("x", encoding="utf-8") as handle:
                handle.write(run_id)
        except FileExistsError as exc:
            raise RuntimeError("writer lock already held") from exc

        safe_run_id = self._safe_run_id(run_id)
        root = repo.parent / f".{repo.name}-worktrees"
        root.mkdir(parents=True, exist_ok=True)
        target = root / safe_run_id
        branch_name = f"multi-ia/{safe_run_id}"

        try:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(target), "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            lock.unlink(missing_ok=True)
            raise
        return str(target)

    def release(self, repo_path: str, workspace_path: str) -> None:
        repo = Path(repo_path).resolve()
        lock = repo / ".git" / "multi-ia-writer.lock"
        subprocess.run(
            ["git", "worktree", "remove", workspace_path],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        lock.unlink(missing_ok=True)
