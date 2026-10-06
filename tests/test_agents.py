from rsi_agent.agents import DeterministicAgentRunner, ToolRegistry, ToolSpec, run_detector, verify_findings
from rsi_agent.models import PRSnapshot, RiskFeatureSet, RiskSurface


def snapshot():
    return PRSnapshot("org/repo", 1, "base", "head", ("src/query.py",), "query(user_id)")


def test_detector_output_is_structured():
    runner = DeterministicAgentRunner({"security": {"findings": [{
        "finding_id": "f-1", "risk_surface": RiskSurface.INPUT_SINK,
        "claim": "input reaches query", "file": "src/query.py",
        "start_line": 1, "end_line": 2, "evidence_refs": ["trace-1"]
    }]}})
    findings = run_detector(runner, "security", snapshot(), RiskFeatureSet(1, True, True, True), ToolRegistry((ToolSpec("call_graph"),)), "job")
    assert findings[0].finding_id == "f-1"
    assert findings[0].evidence_refs == ("trace-1",)


def test_verifier_rejects_invalid_status_to_safe_state():
    finding = run_detector(
        DeterministicAgentRunner({"security": {"findings": [{
            "finding_id": "f-1", "risk_surface": RiskSurface.INPUT_SINK,
            "claim": "input reaches query", "file": "src/query.py",
            "start_line": 1, "end_line": 2
        }]}}),
        "security", snapshot(), RiskFeatureSet(1, True, True, True), ToolRegistry(), "job",
    )
    result = verify_findings(DeterministicAgentRunner({"verifier": {"status": "surprise"}}), finding, snapshot(), ToolRegistry(), "job")
    assert result[0].verification_status == "insufficient_evidence"


def test_tools_are_read_only_for_review_roles():
    registry = ToolRegistry((ToolSpec("call_graph"), ToolSpec("apply_patch", read_only=False)))
    assert registry.allowed("security") == ("call_graph",)
    assert registry.allowed("verifier") == ("call_graph",)
    assert registry.allowed("unknown") == ()


def test_invalid_finding_does_not_cross_detector_contract():
    runner = DeterministicAgentRunner({"security": {"findings": [{
        "finding_id": "f-1", "risk_surface": "input_sink",
        "claim": "input reaches query", "file": "outside.py",
        "start_line": 1, "end_line": 2,
    }]}})
    try:
        run_detector(runner, "security", snapshot(), RiskFeatureSet(1, True, True, True), ToolRegistry(), "job")
    except ValueError as exc:
        assert "evidence contract" in str(exc)
    else:
        raise AssertionError("invalid detector output was accepted")
