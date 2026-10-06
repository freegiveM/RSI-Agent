from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from .models import JobStatus, PRSnapshot, ReviewJob, ReviewResult
from .routing import extract_features, route
from .store import TaskStore


class ReviewService:
    """Orchestrates deterministic workflow; model-backed agents plug into these boundaries."""

    def __init__(self, store: TaskStore | None = None) -> None:
        self.store = store or TaskStore()

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
