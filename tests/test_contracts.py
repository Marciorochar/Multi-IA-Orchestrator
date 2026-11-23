import asyncio

import pytest

from multi_ia.adapters.fake import FakeAdapter
from multi_ia.contracts import AgentRequest, AgentResult


def test_rejects_missing_run_id():
    with pytest.raises(ValueError, match="run_id"):
        AgentRequest(run_id="", cycle_id=1, role="planner", payload={})


def test_rejects_invalid_status():
    with pytest.raises(ValueError, match="status"):
        AgentResult(status="unknown", payload={}, evidence=["x"])


def test_rejects_completed_without_evidence():
    with pytest.raises(ValueError, match="evidence"):
        AgentResult(status="completed", payload={}, evidence=[])


def test_fake_adapter_returns_terminal_result():
    request = AgentRequest(
        run_id="run-1",
        cycle_id=1,
        role="planner",
        payload={"goal": "test"},
    )
    result = asyncio.run(FakeAdapter().run(request))

    assert result.status == "completed"
    assert result.is_terminal is True
    assert result.evidence
