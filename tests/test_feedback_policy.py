from rsi_agent.feedback import FeedbackStore
from rsi_agent.models import EvaluationResult, FeedbackEvent, FeedbackKind
from rsi_agent.policy import PolicyGate, propose_skill_patch


def event(event_id="fb-1", kind=FeedbackKind.MISSED_RISK):
    return FeedbackEvent(event_id, "org/repo", 1, "head", kind, note="add evidence for parser boundary")


def test_feedback_is_idempotent_and_missed_risk_is_traceable():
    store = FeedbackStore()
    assert store.record_feedback(event()) is True
    assert store.record_feedback(event()) is False
    assert [item.event_id for item in store.feedback()] == ["fb-1"]
    candidate = propose_skill_patch("p-2", "p-1", store.feedback())
    assert candidate is not None
    assert candidate.status == "CANDIDATE"
    assert candidate.source_feedback_ids == ("fb-1",)


def test_policy_gate_requires_both_splits_and_blocks_regression():
    gate = PolicyGate(min_recall=0.8, max_false_positive_rate=0.2)
    validation = EvaluationResult("p-2", "validation", {"recall": 0.9, "false_positive_rate": 0.1, "cost_delta": 0.1}, "pending")
    holdout = EvaluationResult("p-2", "holdout", {"recall": 0.7, "false_positive_rate": 0.1, "cost_delta": 0.1}, "pending")
    assert gate.decide(validation, holdout) == "REJECTED"


def test_policy_gate_passes_only_valid_pair():
    gate = PolicyGate()
    validation = EvaluationResult("p-2", "validation", {"recall": 0.9, "false_positive_rate": 0.1, "cost_delta": 0.1}, "pending")
    holdout = EvaluationResult("p-2", "holdout", {"recall": 0.85, "false_positive_rate": 0.15, "cost_delta": 0.2}, "pending")
    assert gate.decide(validation, holdout) == "PASSED"
