import asyncio

from rsi_agent.agentscope_context import PRContextMiddleware


def test_context_middleware_records_model_call():
    traces = []
    middleware = PRContextMiddleware("security", traces.append)

    async def next_handler(**kwargs):
        return type("Response", (), {"usage": type("Usage", (), {"output_tokens": 3})()})()

    result = asyncio.run(middleware.on_model_call(None, {"messages": ["prompt"]}, next_handler))
    assert result.usage.output_tokens == 3
    assert traces[0]["role"] == "security"
    assert traces[0]["output_tokens"] == 3
