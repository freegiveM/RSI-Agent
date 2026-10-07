from __future__ import annotations

import time
from typing import Any, Awaitable, Callable


try:
    from agentscope.middleware import MiddlewareBase
except ImportError:  # Keep deterministic/test installs lightweight.
    class MiddlewareBase:  # type: ignore[no-redef]
        pass


class PRContextMiddleware(MiddlewareBase):
    """AgentScope lifecycle adapter for project-owned context and tracing policy."""

    def __init__(self, role: str, trace_sink=None) -> None:
        self.role = role
        self.trace_sink = trace_sink

    async def on_model_call(self, agent, input_kwargs: dict, next_handler: Callable[..., Awaitable[Any]]):
        started = time.perf_counter()
        messages = input_kwargs.get("messages", ())
        try:
            result = await next_handler(**input_kwargs)
        except Exception as exc:
            self._emit(input_kwargs, started, None, type(exc).__name__)
            raise
        usage = getattr(result, "usage", None)
        self._emit(input_kwargs, started, usage, None, len(messages))
        return result

    async def on_compress_context(self, agent, input_kwargs: dict, next_handler: Callable[..., Awaitable[None]]):
        # The actual PR compaction is performed by ReviewContextBuilder before
        # the request. This hook is retained for AgentScope overflow recovery.
        started = time.perf_counter()
        try:
            result = await next_handler(**input_kwargs)
        except Exception as exc:
            if self.trace_sink:
                self.trace_sink({"role": self.role, "phase": "compaction", "provider_latency_ms": round((time.perf_counter() - started) * 1000, 1), "error_class": type(exc).__name__})
            raise
        if self.trace_sink:
            self.trace_sink({"role": self.role, "phase": "compaction", "provider_latency_ms": round((time.perf_counter() - started) * 1000, 1), "error_class": None})
        return result

    def _emit(self, input_kwargs, started: float, usage, error_class, message_count: int = 0) -> None:
        if not self.trace_sink:
            return
        messages = input_kwargs.get("messages", ())
        input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
        reasoning_tokens = _usage_value(usage, "reasoning_tokens", "reasoning_token_count")
        self.trace_sink({
            "role": self.role,
            "message_count": message_count,
            "request_chars": sum(len(str(message)) for message in messages),
            "provider_latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "error_class": error_class,
        })


def _usage_value(usage: Any, *names: str) -> int | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        for name in names:
            value = usage.get(name)
            if value is not None:
                return int(value)
        return None
    for name in names:
        value = getattr(usage, name, None)
        if value is not None:
            return int(value)
    return None
