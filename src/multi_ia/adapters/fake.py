from __future__ import annotations

from collections.abc import Callable

from multi_ia.contracts import AgentRequest, AgentResult


class FakeAdapter:
    def __init__(
        self,
        result_factory: Callable[[AgentRequest], AgentResult] | None = None,
    ) -> None:
        self._result_factory = result_factory

    async def run(self, request: AgentRequest) -> AgentResult:
        if self._result_factory is not None:
            return self._result_factory(request)

        return AgentResult(
            status="completed",
            payload={"role": request.role, "request": request.payload},
            evidence=[f"fake:{request.role}:completed"],
        )
