from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class _Lease:
    common_dir: Path
    source: Path
    workspace: Path
    branch: str
    run_id: str
    token: str

    @property
    def lock(self) -> Path:
        return self.common_dir / "multi-ia-writer.lock"

    def record(self) -> dict:
        return {
            "version": 1,
            "repository": str(self.common_dir),
            "source": str(self.source),
            "workspace": str(self.workspace),
            "branch": self.branch,
            "run_id": self.run_id,
            "token": self.token,
        }


def git_environment() -> dict[str, str]:
    """The environment without overrides that select another repository."""
    # An inherited repository override must not redirect commands to another
    # repository when the selected path is used as the working directory.
    return {
        key: value for key, value in os.environ.items()
        if key.upper() not in {
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_NAMESPACE", "GIT_PREFIX",
        }
    }


class WorkspaceManager:
    """Cooperative writer exclusion for worktrees of a single Git repository.

    A lock survives uncertain creation, dirty release and process termination.
    It is not an OS sandbox or protection against non-cooperating programs.
    """

    def __init__(self) -> None:
        self._leases: dict[Path, _Lease] = {}
        self._mutex = threading.RLock()

    @staticmethod
    def path_for(path: str) -> Path:
        return Path(path)

    @staticmethod
    def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            env=git_environment(),
            check=check,
            capture_output=True,
            text=True,
            timeout=60,
        )

    @classmethod
    def _repository(cls, path: str | Path) -> tuple[Path, Path]:
        directory = Path(path).resolve(strict=True)
        try:
            source = cls._git(directory, "rev-parse", "--show-toplevel")
            common = cls._git(
                directory, "rev-parse", "--path-format=absolute", "--git-common-dir"
            )
        except subprocess.CalledProcessError:
            raise ValueError("repo_path must be a Git worktree") from None
        return (
            Path(source.stdout.rstrip("\r\n")).resolve(strict=True),
            Path(common.stdout.rstrip("\r\n")).resolve(strict=True),
        )

    @staticmethod
    def _assert_owner(lease: _Lease) -> None:
        try:
            if lease.lock.is_symlink():
                raise RuntimeError("writer lock owner could not be verified")
            record = json.loads(lease.lock.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise RuntimeError("writer lock owner could not be verified") from None
        if record != lease.record():
            raise RuntimeError("writer lock owner does not match")

    def _unlock(self, lease: _Lease) -> None:
        self._assert_owner(lease)
        lease.lock.unlink()
        self._leases.pop(lease.common_dir, None)

    def _no_creation_residue(self, lease: _Lease) -> bool:
        """Only release after failure if absence of Git/filesystem residue is verified."""
        try:
            if os.path.lexists(lease.workspace):
                return False
            registered = self._git(
                lease.source, "worktree", "list", "--porcelain", "-z"
            )
            for field in registered.stdout.split("\0"):
                if field.startswith("worktree "):
                    if Path(field[len("worktree "):]).resolve() == lease.workspace:
                        return False
            branch = self._git(
                lease.source, "show-ref", "--verify", "--quiet",
                f"refs/heads/{lease.branch}", check=False,
            )
            return branch.returncode == 1
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def prepare(self, repo_path: str, run_id: str) -> str:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        with self._mutex:
            source, common = self._repository(repo_path)
            # No user-supplied path component; the allocation token makes even
            # identical or case-folded run names safe on Windows.
            token = uuid.uuid4().hex
            digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
            name = f"run-{digest}-{token}"
            root = source.parent / f".{source.name}-worktrees"
            if root.is_symlink() or root.resolve() != root:
                raise RuntimeError("workspace root must not redirect to another path")
            target = root / name
            lease = _Lease(common, source, target, f"multi-ia/{name}", run_id, token)

            try:
                # Exclusive creation is shared by all cooperating processes.
                with lease.lock.open("x", encoding="utf-8") as handle:
                    json.dump(lease.record(), handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                raise RuntimeError("writer lock already held") from None

            self._leases[common] = lease
            try:
                root.mkdir(parents=True, exist_ok=True)
                if root.resolve() != root or os.path.lexists(target):
                    raise RuntimeError("workspace path is not safe for creation")
                self._git(source, "worktree", "add", "-b", lease.branch, str(target), "HEAD")
            except Exception:
                # Unknown or partial side effects require reconciliation. Never
                # remove a foreign lock, branch or potentially modified worktree.
                if self._no_creation_residue(lease):
                    self._unlock(lease)
                raise
            return str(target)

    def release(self, repo_path: str, workspace_path: str) -> None:
        with self._mutex:
            _, common = self._repository(repo_path)
            lease = self._leases.get(common)
            if lease is None or Path(workspace_path).resolve() != lease.workspace:
                raise RuntimeError("workspace owner does not match")
            self._assert_owner(lease)

            source, workspace_common = self._repository(lease.workspace)
            if source != lease.workspace or workspace_common != common:
                raise RuntimeError("workspace owner does not match repository")

            current_branch = self._git(
                lease.workspace, "symbolic-ref", "--quiet", "HEAD", check=False
            )
            if (
                current_branch.returncode != 0
                or current_branch.stdout.rstrip("\r\n") != f"refs/heads/{lease.branch}"
            ):
                raise RuntimeError("workspace branch changed; reconciliation required")

            # Git can remove ignored files from a worktree without --force.
            # Preserve tracked, untracked and ignored data instead.
            status = self._git(
                lease.workspace, "status", "--porcelain=v1",
                "--untracked-files=all", "--ignored=matching",
                "--ignore-submodules=none",
            )
            if status.stdout:
                raise RuntimeError("workspace contains changes; reconciliation required")

            self._assert_owner(lease)
            self._git(lease.source, "worktree", "remove", str(lease.workspace))
            # Keep the branch and its commits as evidence. No --force, prune,
            # branch deletion or automatic cleanup of uncertain workspaces.
            self._unlock(lease)
