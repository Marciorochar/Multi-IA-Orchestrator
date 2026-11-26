"""Cumulative execution limits for the simulated orchestration profile."""
from __future__ import annotations

import math
import time

from multi_ia.policies import ExecutionLimitError, ExecutionPolicy

KNOWN_COST = "builtin_fake_zero"
UNKNOWN_COST = "unverified"


def initial_limits(policy: ExecutionPolicy, *, known_zero: bool) -> dict:
    now = time.time()
    return {
        "schema_version": 1,
        "started_at": now,
        "deadline": now + policy.max_seconds,
        "last_wall": now,
        "paused_seconds": 0.0,
        "max_seconds": float(policy.max_seconds),
        "max_retries": policy.max_retries,
        "retries_used": 0,
        "max_cycles": policy.max_cycles,
        "budget": policy.max_budget,
        "budget_unit": "simulation_cost_units",
        "spent": 0 if known_zero else None,
        "cost_source": KNOWN_COST if known_zero else UNKNOWN_COST,
    }


def extend_deadline(record: dict, paused: float, now: float) -> None:
    if now < record["last_wall"]:
        raise ExecutionLimitError("clock_regression")
    record["paused_seconds"] = record.get("paused_seconds", 0.0) + paused
    # Same evaluation order as validation, so the floating-point result matches.
    record["deadline"] = (
        record["started_at"] + record["max_seconds"] + record["paused_seconds"]
    )
    record["last_wall"] = now


class ExecutionLimits:
    """One run's time and retry allowance; persisted when the service owns the run."""

    def __init__(self, policy: ExecutionPolicy, *, store=None, run_id=None,
                 known_zero: bool = False):
        self.policy = policy
        self.store = store
        self.run_id = run_id
        self.known_zero = known_zero
        self.record = (store.load_limits(run_id) if store is not None
                       else initial_limits(policy, known_zero=known_zero))
        self._validate()
        now = time.time()
        if now < self.record["last_wall"]:
            raise ExecutionLimitError("clock_regression")
        self.deadline = time.monotonic() + min(
            float(policy.max_seconds), self.record["deadline"] - now
        )

    def _validate(self) -> None:
        r = self.record
        if not isinstance(r, dict):
            raise ExecutionLimitError("limits_evidence_missing")
        try:
            # Records written before pause accounting have no paused time.
            paused = r.get("paused_seconds", 0.0)
            if (
                type(paused) not in (int, float) or not math.isfinite(paused)
                or paused < 0
                or type(r["schema_version"]) is not int or r["schema_version"] != 1
                or r["max_seconds"] != self.policy.max_seconds
                or r["max_cycles"] != self.policy.max_cycles
                or r["max_retries"] != self.policy.max_retries
                or r["budget"] != self.policy.max_budget
                or r["budget_unit"] != "simulation_cost_units"
                or r["cost_source"] != (KNOWN_COST if self.known_zero else UNKNOWN_COST)
                or r["spent"] != (0 if self.known_zero else None)
                or type(r["retries_used"]) is not int
                or not 0 <= r["retries_used"] <= self.policy.max_retries
                or not all(type(r[key]) in (int, float) and math.isfinite(r[key])
                           for key in ("started_at", "deadline", "last_wall"))
                or r["deadline"] != r["started_at"] + self.policy.max_seconds + paused
                or r["last_wall"] < r["started_at"]
            ):
                raise ExecutionLimitError("limits_evidence_invalid")
        except (KeyError, TypeError, OverflowError):
            raise ExecutionLimitError("limits_evidence_invalid") from None

    def remaining(self) -> float:
        if self.policy.max_budget == 0:
            raise ExecutionLimitError("budget_exhausted")
        if self.policy.max_budget is not None and not self.known_zero:
            raise ExecutionLimitError("budget_unverified")
        now = time.time()
        if now < self.record["last_wall"]:
            raise ExecutionLimitError("clock_regression")
        left = min(self.deadline - time.monotonic(), self.record["deadline"] - now)
        if left <= 0:
            raise TimeoutError("timeout")
        return left

    def checkpoint(self) -> float:
        self.remaining()
        now = time.time()
        if self.store is not None:
            self.record = self.store.advance_limits(self.run_id, now)
            self._validate()
        else:
            self.record["last_wall"] = now
        return self.remaining()

    def begin_pause(self) -> float:
        # An already expired limit cannot be revived by pausing.
        self.checkpoint()
        return time.monotonic()

    def end_pause(self, started: float) -> None:
        """Extend both deadlines by the time the engine was blocked in a pause.

        Credit is recorded only after the pause ends, so a process that dies
        while paused keeps the earlier deadline.
        """
        paused = max(0.0, time.monotonic() - started)
        now = time.time()
        if self.store is not None:
            self.record = self.store.credit_pause(self.run_id, paused, now)
        else:
            extend_deadline(self.record, paused, now)
        self.deadline += paused
        self._validate()

    def reserve_retry(self) -> None:
        self.remaining()
        if self.record["retries_used"] >= self.policy.max_retries:
            raise ExecutionLimitError("retry_exhausted")
        now = time.time()
        if self.store is not None:
            self.record = self.store.advance_limits(self.run_id, now, retry=True)
            self._validate()
        else:
            self.record["retries_used"] += 1
            self.record["last_wall"] = now
        self.remaining()
