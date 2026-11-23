from __future__ import annotations

from typing import Protocol

from multi_ia.contracts import AgentRequest, AgentResult


class AgentAdapter(Protocol):
    async def run(self, request: AgentRequest) -> AgentResult:
        """Execute one agent request and return a validated result."""
        ...
