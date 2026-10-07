from rsi_agent.models import JobStatus, PRSnapshot, ReviewJob
from rsi_agent.store import TaskStore


def make_job(head="abc"):
    return ReviewJob("job-1", PRSnapshot("org/repo", 1, "base", head, ("src/auth.py",)), "policy-v1")


def test_event_is_idempotent():
    store = TaskStore()
    assert store.record_event("delivery-1", "pull_request", {})
    assert not store.record_event("delivery-1", "pull_request", {})


def test_review_key_is_idempotent():
    store = TaskStore()
    first = store.create_job(make_job())
    second = store.create_job(ReviewJob("different", first.snapshot, first.policy_version))
    assert second.job_id == first.job_id


def test_state_transition_is_persisted():
    store = TaskStore()
    job = store.create_job(make_job())
    store.transition(job.job_id, JobStatus.RECEIVED, JobStatus.SNAPSHOTTED)
    assert store.get_job(job.job_id).status is JobStatus.SNAPSHOTTED


def test_audit_event_is_append_only_and_idempotent():
    store = TaskStore()
    store.create_job(make_job())
    assert store.record_audit_event("job-1", "worker", "router", "route_selected", event_id="audit-1", metadata={"route": ["security"]})
    assert not store.record_audit_event("job-1", "worker", "router", "route_selected", event_id="audit-1")
    events = store.audit_events_for_job("job-1")
    assert len(events) == 1
    assert events[0]["metadata"] == {"route": ["security"]}
