import pytest

from rsi_agent.models import FeedbackEvent, FeedbackKind, PRSnapshot
from rsi_agent.service import ReviewService
from rsi_agent.store import TaskStore


def test_missed_risk_creates_idempotent_rereview_job():
    service = ReviewService(TaskStore())
    snapshot = PRSnapshot("org/repo", 1, "base", "head", ("src/a.py",), "return value")
    event = FeedbackEvent("feedback-1", "org/repo", 1, "head", FeedbackKind.MISSED_RISK, note="missing authorization check")
    first = service.resubmit_missed_risk(event, snapshot)
    second = service.resubmit_missed_risk(event, snapshot)
    assert first.job_id == second.job_id
    assert len(service.feedback_store.feedback()) == 1


def test_missed_risk_requires_note_and_current_head():
    service = ReviewService(TaskStore())
    snapshot = PRSnapshot("org/repo", 1, "base", "head", ("src/a.py",), "return value")
    with pytest.raises(ValueError):
        service.resubmit_missed_risk(FeedbackEvent("f", "org/repo", 1, "head", FeedbackKind.MISSED_RISK), snapshot)
    with pytest.raises(ValueError):
        service.resubmit_missed_risk(FeedbackEvent("f", "org/repo", 1, "old", FeedbackKind.MISSED_RISK, note="issue"), snapshot)
