"""Execution entry point for durable simulated runs and conservative recovery."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from multi_ia.contracts import Operation
from multi_ia.engine import CycleOutcome, Orchestrator, OrchestratorState
from multi_ia.policies import CancellationController, ExecutionLimitError, ExecutionPolicy, PauseController
from multi_ia.limits import ExecutionLimits, initial_limits
from multi_ia.recovery import PROFILE, digest, expected_results, fake_adapters, recovery_marker
from multi_ia.store import JournalBlocked, RunStore
from multi_ia.workspace import WorkspaceManager


class ExecutionService:
    def __init__(
        self, store: RunStore, *, goal: str, repo: str,
        policy: ExecutionPolicy, supervised: bool = False,
        adapters=None, event_sink=None,
        cancellation: CancellationController | None = None,
        pause: PauseController | None = None,
    ) -> None:
        self.store = store
        self.goal = goal
        self.repo = Path(repo)
        self.policy = policy
        self.supervised = supervised
        self.adapters = adapters if adapters is not None else fake_adapters()
        self._builtin_adapters = self.adapters if adapters is None else None
        self.event_sink = event_sink
        self.cancellation = cancellation or CancellationController()
        self.pause = pause or PauseController()

    @staticmethod
    def _blocked(reason: str) -> CycleOutcome:
        return CycleOutcome(OrchestratorState.FAILED, reason=reason)

    def _context(self) -> tuple[dict, str]:
        if type(self.goal) is not str or not self.goal.strip():
            raise ValueError("invalid goal")
        repo = self.repo.resolve(strict=True)
        if not repo.is_dir():
            raise ValueError("invalid repository")
        stat = repo.stat()
        initial = {
            "goal": self.goal, "repo": str(repo), "supervised": self.supervised,
            "mode": "fake",
        }
        config = {
            "profile": PROFILE, "initial": initial,
            "repository_identity": [stat.st_dev, stat.st_ino],
            "policy": {
                "max_cycles": self.policy.max_cycles,
                "max_seconds": float(self.policy.max_seconds),
                "max_retries": self.policy.max_retries,
                "max_budget": None if self.policy.max_budget is None else float(self.policy.max_budget),
            },
        }
        return initial, digest(config)

    @contextmanager
    def _lease(self, run_id: str):
        """A separate SQLite lock DB releases its OS lock when a process exits.

        The lock file is intentionally retained; unlinking it could create two
        independent locks. Requires cooperating callers and local SQLite storage.
        """
        if type(run_id) is not str or not run_id.strip():
            raise ValueError("invalid run ID")
        db = Path(self.store.path).resolve(strict=True)
        if not db.is_file() or self.store.path == ":memory:":
            raise ValueError("durable store required")
        root = db.parent / ".multi-ia-recovery-locks"
        root.mkdir(exist_ok=True)
        key = hashlib.sha256((str(db) + "\0" + run_id).encode("utf-8")).hexdigest()
        conn = sqlite3.connect(root / (key + ".sqlite"), timeout=0)
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise JournalBlocked("run_busy") from None
                raise
            yield
        finally:
            conn.close()

    def _known_zero_cost(self) -> bool:
        return self._builtin_adapters is not None and self.adapters is self._builtin_adapters

    def _limits(self, run_id: str) -> ExecutionLimits:
        return ExecutionLimits(self.policy, store=self.store, run_id=run_id,
                               known_zero=self._known_zero_cost())

    def _engine(self, initial: dict, limits: ExecutionLimits, *, event_sink=None) -> Orchestrator:
        return Orchestrator(
            *self.adapters, store=self.store, policy=self.policy,
            initial_payload=initial, event_sink=self.event_sink if event_sink is None else event_sink,
            cancellation=self.cancellation, pause=self.pause,
            execution_mode="fake", recovery_enabled=True, limits=limits,
        )

    @staticmethod
    def _requires_workspace(repo: str) -> bool:
        # A selected nested directory belongs to the same Git worktree.
        # A broken or inaccessible .git marker is not a non-Git fallback.
        path = Path(repo)
        return any(os.path.lexists(directory / ".git")
                   for directory in (path, *path.parents))

    def _workspace_failure(self, run_id: str, reason: str) -> CycleOutcome:
        checkpoint = self.store.load_checkpoint(run_id) or {"cycle_id": 1}
        checkpoint["state"] = "failed"
        checkpoint["reason"] = reason
        self.store.save_checkpoint(run_id, checkpoint)
        return self._blocked(reason)

    def _notify(self, event: dict) -> None:
        if self.event_sink is not None:
            try:
                self.event_sink(event)
            except Exception:
                # Diagnostic delivery never changes a durable workspace decision.
                pass

    async def _start_managed(self, run_id: str, initial: dict,
                             limits: ExecutionLimits) -> CycleOutcome:
        source = initial["repo"]
        manager = WorkspaceManager()
        self.store.mark_workspace_pending(run_id)
        self._notify({"type": "workspace_preparing", "run_id": run_id})
        try:
            workspace = manager.prepare(source, run_id)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return self._workspace_failure(run_id, "workspace_unavailable")

        terminal_events = []

        def observe(event: dict) -> None:
            if event.get("type") in {"state_changed", "run_completed", "run_failed",
                                      "run_cancelled", "run_paused"}:
                terminal_events.append(event)
            elif self.event_sink is not None:
                self.event_sink(event)

        # All agents see the isolated checkout, not the original project path.
        # A crash or unhandled exception leaves the durable pending marker and lock.
        isolated = {**initial, "repo": workspace}
        outcome = await self._engine(isolated, limits, event_sink=observe).run(run_id)
        checkpoint = self.store.load_checkpoint(run_id)
        if not self.store.can_replay_write(checkpoint) or any(
            row["status"] == "pending" for row in self.store.list_operations(run_id)
        ):
            self._notify({"type": "workspace_reconciliation_required", "run_id": run_id})
            return self._workspace_failure(run_id, "workspace_reconciliation_required")
        try:
            manager.release(source, workspace)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            self._notify({"type": "workspace_reconciliation_required", "run_id": run_id})
            return self._workspace_failure(run_id, "workspace_reconciliation_required")
        # Never announce completion before cleanup and its durable marker.
        self.store.mark_workspace_released(run_id)
        self._notify({"type": "workspace_released", "run_id": run_id})
        for event in terminal_events:
            self._notify(event)
        return outcome

    async def start(self, run_id: str) -> CycleOutcome:
        try:
            with self._lease(run_id):
                initial, config_digest = self._context()
                self.store.register_run(
                    run_id, config_digest,
                    limits=initial_limits(self.policy, known_zero=self._known_zero_cost()),
                )
                limits = self._limits(run_id)
                limits.checkpoint()
                if (
                    self._requires_workspace(initial["repo"])
                    and not self.cancellation.stop_requested
                    and not self.store.stop_requested(run_id)
                ):
                    return await self._start_managed(run_id, initial, limits)
                return await self._engine(initial, limits).run(run_id)
        except (JournalBlocked, ExecutionLimitError) as exc:
            return self._blocked(str(exc))
        except TimeoutError:
            return self._blocked("timeout")
        except (sqlite3.Error, OSError):
            return self._blocked("persistence_failure")
        except (ValueError, TypeError):
            return self._blocked("invalid_run_configuration")
        except Exception:
            return self._blocked("internal_failure")

    async def resume(self, run_id: str) -> CycleOutcome:
        try:
            with self._lease(run_id):
                checkpoint = self.store.load_checkpoint(run_id)
                if self.store.workspace_status(run_id) == "pending":
                    return self._blocked("workspace_reconciliation_required")
                journal = self.store.list_operations(run_id)
                if not self.store.can_replay_write(checkpoint) or any(
                    row["status"] == "pending" for row in journal
                ):
                    return self._blocked("needs_reconciliation")
                registration = self.store.run_registration(run_id)
                if checkpoint is None:
                    return self._blocked("run_not_found" if registration is None else "recovery_evidence_invalid")
                if registration is None or registration[1] != 1:
                    return self._blocked("legacy_checkpoint")
                initial, config_digest = self._context()
                if config_digest != registration[0]:
                    return self._blocked("recovery_config_mismatch")
                if self.store.stop_requested(run_id):
                    return self._blocked("stop_requested")
                cycle = checkpoint.get("cycle_id")
                if (type(cycle) is not int or not self.policy.allows_cycle(cycle)
                        or checkpoint.get("schema_version") != 1):
                    return self._blocked("recovery_evidence_invalid")
                if checkpoint.get("state") == "completed":
                    if not journal or journal[-1]["cycle_id"] != cycle or journal[-1]["status"] != "confirmed":
                        return self._blocked("recovery_evidence_invalid")
                    if not self.store.has_completed_audit(run_id, cycle):
                        return self._blocked("audit_evidence_missing")
                    return CycleOutcome(OrchestratorState.COMPLETED)
                if self._requires_workspace(initial["repo"]) or self.store.workspace_status(run_id) is not None:
                    # A new checkout is not evidence of the prior workspace.
                    return self._blocked("workspace_recovery_unsupported")
                if checkpoint.get("phase") != "executor_completed" or checkpoint.get("state") != "executing":
                    return self._blocked("recovery_boundary_unsupported")
                planner, executor = expected_results(initial)
                operation = Operation.from_plan(planner.payload, run_id=run_id, cycle_id=cycle)
                expected_marker = recovery_marker(initial, planner, executor, operation)
                if (
                    checkpoint.get("write_status") != "confirmed"
                    or checkpoint.get("operation_id") != operation.operation_id
                    or expected_marker is None or checkpoint.get("recovery") != expected_marker
                    or not journal
                    or journal[-1] != {
                        "run_id": run_id, "cycle_id": cycle,
                        "operation_id": operation.operation_id, "action": operation.action,
                        "scope": operation.scope, "effect": operation.effect,
                        "plan_digest": operation.plan_digest, "version": operation.version,
                        "status": "confirmed",
                    }
                ):
                    return self._blocked("recovery_evidence_invalid")
                limits = self._limits(run_id)
                limits.checkpoint()
                return await self._engine(initial, limits)._resume_confirmed_audit(run_id, cycle, planner, executor)
        except (JournalBlocked, ExecutionLimitError) as exc:
            return self._blocked(str(exc))
        except TimeoutError:
            return self._blocked("timeout")
        except (sqlite3.Error, OSError):
            return self._blocked("persistence_failure")
        except (ValueError, TypeError, KeyError):
            return self._blocked("recovery_evidence_invalid")
        except Exception:
            return self._blocked("internal_failure")
