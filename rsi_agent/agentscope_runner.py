from __future__ import annotations

import json
import asyncio
import time
from typing import Any

from .agents import AgentRunner
from .context import ReviewContextBuilder, ContextBudget, estimate_tokens
from .models import PRSnapshot, RiskFeatureSet
from .agentscope_context import PRContextMiddleware


class AgentScopeRunner(AgentRunner):
    """Provider boundary for AgentScope; model-specific construction stays isolated here."""

    def __init__(self, agents: dict[str, Any] | None = None, context_builder: ReviewContextBuilder | None = None, trace_sink=None) -> None:
        self.agents = agents or {}
        if not self.agents:
            raise ValueError("no AgentScope agents configured")
        self.context_builder = context_builder or ReviewContextBuilder()
        self.trace_sink = trace_sink
        self.traces: list[dict[str, Any]] = []

    def run(self, role: str, context: dict[str, Any], tools: tuple[str, ...]) -> dict[str, Any]:
        agent = self.agents.get(role)
        if agent is None:
            raise ValueError(f"no AgentScope agent configured for role: {role}")
        prepared, context_level, context_tokens = self._prepare_context(role, context)
        prompt = json.dumps({"role": role, "context": prepared, "allowed_tools": tools}, default=str)
        prompt += "\nReturn a JSON object only. Do not use Markdown fences or extra commentary."
        started = time.perf_counter()
        error_class = None
        try:
            if callable(agent) and not hasattr(agent, "reply"):
                result = agent(prompt)
            else:
                from agentscope.message import Msg
                result = asyncio.run(agent.reply(Msg(name="review-orchestrator", role="user", content=[{"type": "text", "text": prompt}])))
        except Exception as exc:
            error_class = type(exc).__name__
            self._trace(role, context_level, context_tokens, started, None, error_class)
            raise
        if isinstance(result, dict):
            self._trace(role, context_level, context_tokens, started, None, error_class)
            return result
        if hasattr(result, "content"):
            content = result.content
            if isinstance(content, list):
                text_blocks = []
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", block.get("content"))
                    else:
                        text = getattr(block, "text", None)
                    if isinstance(text, str) and text.strip():
                        text_blocks.append(text)
                if text_blocks:
                    # Thinking blocks are intentionally ignored; the final text block
                    # is the structured response requested from the model.
                    result = text_blocks[-1]
            elif isinstance(content, str):
                result = content
        if isinstance(result, str):
            parsed = self._parse_json(result)
            self._trace(role, context_level, context_tokens, started, None, error_class)
            return parsed
        raise ValueError("AgentScope agent returned unsupported output")

    @staticmethod
    def _parse_json(value: str) -> dict[str, Any]:
        text = value.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("agent output must be a JSON object")
        findings = parsed.get("findings")
        if isinstance(findings, list):
            normalized = []
            for item in findings:
                if not isinstance(item, dict):
                    normalized.append(item)
                    continue
                # Accept common model aliases, but retain the evidence contract.
                item = dict(item)
                if "finding_id" not in item and "id" in item:
                    item["finding_id"] = item["id"]
                if "claim" not in item and "description" in item:
                    item["claim"] = item["description"]
                if "start_line" not in item and "line" in item:
                    line = item["line"]
                    if isinstance(line, str) and "-" in line:
                        start, end = line.split("-", 1)
                        item["start_line"], item["end_line"] = int(start), int(end)
                    else:
                        item["start_line"] = int(line)
                if "end_line" not in item and "start_line" in item:
                    item["end_line"] = item["start_line"]
                if "risk_surface" not in item:
                    item["risk_surface"] = "business_regression"
                normalized.append(item)
            parsed["findings"] = normalized
        return parsed

    def _prepare_context(self, role: str, context: dict[str, Any]) -> tuple[dict[str, Any], str, int]:
        snapshot = context.get("snapshot")
        features = context.get("features")
        if isinstance(snapshot, PRSnapshot) and isinstance(features, RiskFeatureSet) and role in {"security", "correctness"}:
            pack = self.context_builder.build(snapshot, features, role)
            prepared = {key: value for key, value in context.items() if key not in {"snapshot", "features"}}
            prepared["context_pack"] = pack.as_dict()
            return prepared, pack.level, pack.tokens
        serialised = json.dumps(context, default=str)
        return context, "raw", estimate_tokens(serialised)

    def _trace(self, role: str, level: str, input_tokens: int, started: float, output_tokens: int | None, error_class: str | None) -> None:
        item = {
            "role": role,
            "context_level": level,
            "estimated_input_tokens": input_tokens,
            "provider_latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "output_tokens": output_tokens,
            "error_class": error_class,
        }
        self.traces.append(item)
        if self.trace_sink:
            self.trace_sink(item)

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
            max_retries=0,
            client_kwargs={"timeout": config.provider_timeout_seconds},
            extra_body={"thinking": {"type": config.deepseek_thinking}} if config.deepseek_thinking else None,
            parameters=OpenAIChatModel.Parameters(
                temperature=config.deepseek_temperature,
                max_tokens=config.agent_max_tokens,
            ),
        )
        prompts = {
            "security": "Review the PR for security risks. Return JSON only: {\"findings\":[{\"finding_id\":\"...\",\"risk_surface\":\"input_sink|auth_boundary|parser_serialization|state_concurrency|dependency_config|business_regression\",\"claim\":\"...\",\"file\":\"changed file\",\"start_line\":1,\"end_line\":1,\"evidence_refs\":[],\"severity\":\"low|medium|high\"}]}. Return {\"findings\":[]} when no verified finding.",
            "correctness": "Review the PR for correctness and reliability risks. Return JSON only using the exact findings schema: finding_id, risk_surface, claim, file, start_line, end_line, evidence_refs, severity. Return {\"findings\":[]} when no verified finding.",
            "verifier": "Independently verify the structured claim and evidence. Return JSON only: {\"status\":\"verified|rejected|insufficient_evidence\"}.",
        }
        traces = []
        agents = {
            role: Agent(name=role, system_prompt=prompt, model=model, middlewares=[PRContextMiddleware(role, traces.append)])
            for role, prompt in prompts.items()
        }
        return cls(agents)
