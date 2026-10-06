from __future__ import annotations

import json
import asyncio
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
        if callable(agent) and not hasattr(agent, "reply"):
            result = agent(prompt)
        else:
            from agentscope.message import Msg
            result = asyncio.run(agent.reply(Msg(name="review-orchestrator", role="user", content=[{"type": "text", "text": prompt}])))
        if isinstance(result, dict):
            return result
        if hasattr(result, "content"):
            content = result.content
            if isinstance(content, list) and content and isinstance(content[0], dict):
                result = content[0].get("text", content[0].get("content", content))
            else:
                result = content
        if isinstance(result, str):
            return json.loads(result)
        raise ValueError("AgentScope agent returned unsupported output")

    @classmethod
    def from_deepseek_env(cls, config) -> "AgentScopeRunner":
        if not config.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for the real AgentScope runner")
        try:
            from agentscope.agent import Agent
            from agentscope.credential import OpenAICredential
            from agentscope.model import OpenAIChatModel
        except ImportError as exc:
            raise RuntimeError("install the agent extra: python -m pip install -e \".[agent]\"") from exc
        credential = OpenAICredential(api_key=config.deepseek_api_key, base_url=config.deepseek_base_url)
        model = OpenAIChatModel(
            credential=credential,
            model=config.deepseek_model,
            stream=False,
            parameters=OpenAIChatModel.Parameters(
                temperature=config.deepseek_temperature,
                max_tokens=config.deepseek_max_tokens,
            ),
        )
        prompts = {
            "security": "Review the PR for security risks. Return JSON with findings only.",
            "correctness": "Review the PR for correctness and reliability risks. Return JSON with findings only.",
            "verifier": "Independently verify the structured claim and evidence. Return JSON with status.",
        }
        agents = {role: Agent(name=role, system_prompt=prompt, model=model) for role, prompt in prompts.items()}
        return cls(agents)
