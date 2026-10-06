from __future__ import annotations

import json
import os
from typing import Any

from .agents import AgentRunner


class AgentScopeRunner(AgentRunner):
    """Provider boundary for AgentScope; model-specific construction stays isolated here."""

    def __init__(self, agents: dict[str, Any] | None = None) -> None:
        self.agents = agents or {}
        if not self.agents:
            raise ValueError("no AgentScope agents configured")

    def run(self, role: str, context: dict[str, Any], tools: tuple[str, ...]) -> dict[str, Any]:
        agent = self.agents.get(role)
        if agent is None:
            raise ValueError(f"no AgentScope agent configured for role: {role}")
        prompt = json.dumps({"role": role, "context": context, "allowed_tools": tools}, default=str)
        result = agent(prompt)
        if isinstance(result, dict):
            return result
        if hasattr(result, "content"):
            result = result.content
        if isinstance(result, str):
            return json.loads(result)
        raise ValueError("AgentScope agent returned unsupported output")
