import pytest

from rsi_agent.agentscope_runner import AgentScopeRunner
from rsi_agent.models import PRSnapshot, RiskFeatureSet, RiskSurface


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


def test_runner_uses_bounded_context_for_detector():
    prompts = []

    def agent(prompt):
        prompts.append(prompt)
        return {"findings": []}

    runner = AgentScopeRunner({"security": agent})
    runner.run(
        "security",
        {
            "job_id": "job-1",
            "snapshot": PRSnapshot("org/repo", 1, "base", "head", ("src/query.py",), "diff --git a/src/query.py b/src/query.py\n@@ -1 +1 @@\n+query(user_id)"),
            "features": RiskFeatureSet(1, True, True, True, risk_surfaces=(RiskSurface.INPUT_SINK,)),
        },
        (),
    )
    assert "context_pack" in prompts[0]
    assert "src/query.py" in prompts[0]
    assert runner.traces[0]["context_level"] == "L1"
