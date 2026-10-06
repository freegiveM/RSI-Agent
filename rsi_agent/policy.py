from __future__ import annotations

from dataclasses import dataclass

from .models import EvaluationResult, FeedbackEvent, FeedbackKind, PolicyCandidate


@dataclass(frozen=True)
class PolicyGate:
    min_recall: float = 0.80
    max_false_positive_rate: float = 0.20
    max_cost_delta: float = 0.25

    def decide(self, validation: EvaluationResult, holdout: EvaluationResult) -> str:
        if validation.split != "validation" or holdout.split != "holdout":
            raise ValueError("policy gate requires validation and holdout results")
        for result in (validation, holdout):
            if result.metrics.get("recall", 0.0) < self.min_recall:
                return "REJECTED"
            if result.metrics.get("false_positive_rate", 1.0) > self.max_false_positive_rate:
                return "REJECTED"
            if result.metrics.get("cost_delta", 0.0) > self.max_cost_delta:
                return "REJECTED"
        return "PASSED"


def propose_skill_patch(candidate_id: str, parent_policy_id: str, feedback: tuple[FeedbackEvent, ...]) -> PolicyCandidate | None:
    useful = tuple(event for event in feedback if event.kind in {FeedbackKind.FALSE_POSITIVE, FeedbackKind.MISSED_RISK, FeedbackKind.INSUFFICIENT_EVIDENCE})
    if not useful:
        return None
    lines = [f"Feedback {event.event_id}: {event.kind.value}. {event.note}".strip() for event in useful]
    return PolicyCandidate(candidate_id, parent_policy_id, "skill_patch", "\n".join(lines), tuple(event.event_id for event in useful))
