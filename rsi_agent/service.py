from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from .models import JobStatus, PRSnapshot, ReviewJob, ReviewResult
from .models import FeedbackEvent, FeedbackKind
from .feedback import FeedbackStore
from .routing import extract_features, route
from .store import TaskStore


class ReviewService:
    """Orchestrates deterministic workflow; model-backed agents plug into these boundaries."""

    def __init__(self, store: TaskStore | None = None, feedback_store: FeedbackStore | None = None) -> None:
        self.store = store or TaskStore()
        self.feedback_store = feedback_store or FeedbackStore()

    def submit(self, snapshot: PRSnapshot, policy_version: str = "policy-v1", event_id: str | None = None) -> ReviewJob:
        if event_id:
            self.store.record_event(event_id, "pull_request", {"repo": snapshot.repo_id, "head_sha": snapshot.head_sha})
        job = ReviewJob(str(uuid4()), snapshot, policy_version)
        return self.store.create_job(job)

    def precheck(self, job_id: str) -> ReviewResult:
        job = self.store.get_job(job_id)
        if job.status is JobStatus.RECEIVED:
            self.store.transition(job_id, JobStatus.RECEIVED, JobStatus.SNAPSHOTTED)
            self.store.transition(job_id, JobStatus.SNAPSHOTTED, JobStatus.PRECHECKED)
        features = extract_features(job.snapshot.changed_files, job.snapshot.diff)
        selected = route(features)
        self.store.transition(job_id, JobStatus.PRECHECKED, JobStatus.ROUTED)
        routed_job = replace(self.store.get_job(job_id), status=JobStatus.ROUTED)
        return ReviewResult(routed_job, (), features, selected, ({"node": "precheck", "route": selected},))

    def submit_feedback(self, event: FeedbackEvent) -> bool:
        if event.kind is FeedbackKind.MISSED_RISK and not event.note.strip():
            raise ValueError("missed-risk feedback requires a note")
        return self.feedback_store.record_feedback(event)

    def resubmit_missed_risk(self, event: FeedbackEvent, snapshot: PRSnapshot) -> ReviewJob:
        if event.kind is not FeedbackKind.MISSED_RISK:
            raise ValueError("only missed-risk feedback can create a re-review")
        if (event.repo_id, event.pr_number, event.head_sha) != (snapshot.repo_id, snapshot.pr_number, snapshot.head_sha):
            raise ValueError("feedback head_sha does not match snapshot")
        if self.submit_feedback(event) is False:
            existing = self.store.find_job(snapshot, "feedback:" + event.event_id)
            if existing:
                return existing
        return self.submit(snapshot, policy_version="feedback:" + event.event_id, event_id="feedback:" + event.event_id)
