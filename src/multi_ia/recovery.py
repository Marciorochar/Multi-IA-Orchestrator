"""Versioned recovery evidence for the built-in, read-only fake profile.

Raw prompts, plan text and arbitrary adapter outputs are not persisted here.
An exact supported fake result is required before an audit boundary is recoverable.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict

from multi_ia.adapters.fake import FakeAdapter
from multi_ia.contracts import AgentResult, Operation, canonical_plan

PROFILE = "builtin-fake-v1"


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_plan(value).encode("utf-8")).hexdigest()


def expected_results(initial_payload: dict) -> tuple[AgentResult, AgentResult]:
    return (
        AgentResult("completed", {
            "goal": initial_payload.get("goal"), "repo": initial_payload.get("repo"),
        }, ["fake:planner:completed"]),
        AgentResult("completed", {
            "result": "fake execution", "write_status": "confirmed",
        }, ["fake:executor:completed"]),
    )


def fake_adapters() -> tuple[FakeAdapter, FakeAdapter, FakeAdapter]:
    return (
        FakeAdapter(lambda request: expected_results(request.payload)[0]),
        FakeAdapter(lambda request: expected_results({})[1]),
        FakeAdapter(lambda request: AgentResult(
            "completed", {"approved": True}, ["fake:auditor:approved"]
        )),
    )


def recovery_marker(
    initial_payload: dict, plan: AgentResult, execution: AgentResult,
    operation: Operation,
) -> dict | None:
    expected_plan, expected_execution = expected_results(initial_payload)
    if (
        operation.action != "read" or operation.scope != "simulation"
        or operation.effect != "simulated"
        or plan != expected_plan or execution != expected_execution
        or operation.plan_digest != digest(expected_plan.payload)
    ):
        return None
    return {
        "profile": PROFILE,
        "plan_digest": operation.plan_digest,
        "planner_result_digest": digest(asdict(expected_plan)),
        "executor_result_digest": digest(asdict(expected_execution)),
    }
