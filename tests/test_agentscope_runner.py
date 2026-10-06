import pytest

from rsi_agent.agentscope_runner import AgentScopeRunner


def test_agentscope_runner_accepts_injected_agent():
    runner = AgentScopeRunner({"security": lambda prompt: {"findings": []}})
    assert runner.run("security", {"x": 1}, ("read_file",)) == {"findings": []}


def test_agentscope_runner_requires_role():
    runner = AgentScopeRunner({"security": lambda prompt: {"findings": []}})
    with pytest.raises(ValueError):
        runner.run("verifier", {}, ())


def test_deepseek_factory_requires_key():
    from types import SimpleNamespace
    with pytest.raises(ValueError):
        AgentScopeRunner.from_deepseek_env(SimpleNamespace(deepseek_api_key=""))
