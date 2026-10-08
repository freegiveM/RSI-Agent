from fastapi.testclient import TestClient

from rsi_agent.agents import DeterministicAgentRunner, ToolRegistry
from rsi_agent.api import create_app
from rsi_agent.models import PRSnapshot, RiskSurface
from rsi_agent.queue import ReviewQueue
from rsi_agent.service import ReviewService
from rsi_agent.store import TaskStore
from rsi_agent.worker import ReviewWorker


class Reader:
    def pull_snapshot(self, repository, number):
        return PRSnapshot(repository, number, "base", "head", ("src/query.py",), "query")


def test_report_api_exposes_persisted_findings():
    store = TaskStore()
    service = ReviewService(store)
    snapshot = Reader().pull_snapshot("org/repo", 3)
    runner = DeterministicAgentRunner({
        "security": {"findings": [{
            "finding_id": "f-1", "risk_surface": RiskSurface.INPUT_SINK,
            "claim": "unsafe query", "file": "src/query.py", "start_line": 1,
            "end_line": 1, "evidence_refs": ["line:1"], "severity": "high",
        }]},
        "verifier": {"status": "verified"},
    })
    job = service.submit(snapshot)
    ReviewWorker(service, runner, ToolRegistry()).process(job.job_id)
    client = TestClient(create_app(service, Reader(), queue=ReviewQueue(), webhook_secret="secret"))
    response = client.get(f"/api/jobs/{job.job_id}")
    assert response.status_code == 200
    assert response.json()["findings"][0]["verification_status"] == "verified"
    assert response.json()["outcome"] == "verified_risks"
    assert response.json()["budget"]["status"] == "not_reported"
    assert response.json()["diff"]["files"][0]["path"] == "src/query.py"
    assert response.json()["audit_events"]
    assert response.json()["failure_guidance"]["retryable"] is False
    feedback = client.post(f"/api/jobs/{job.job_id}/feedback", json={"event_id": "ui-1", "kind": "accepted", "finding_id": "f-1"})
    assert feedback.status_code == 201
    assert feedback.json()["duplicate"] is False
    duplicate = client.post(f"/api/jobs/{job.job_id}/feedback", json={"event_id": "ui-1", "kind": "accepted", "finding_id": "f-1"})
    assert duplicate.json()["duplicate"] is True
    detail = client.get(f"/api/jobs/{job.job_id}").json()
    assert [item["kind"] for item in detail["feedback"]] == ["accepted"]
    assert detail["attempt_count"] == 1
    listing = client.get("/api/jobs").json()["jobs"][0]
    assert listing["verified_count"] == 1 and listing["created_at"]
    page = client.get("/").text
    assert "RSI-Agent" in page
    assert '<script src="/assets/app.js" defer></script>' in page
    script = client.get("/assets/app.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert client.get("/assets/console.css").headers["content-type"].startswith("text/css")
    # Only allowlisted assets are served; traversal-like names never reach the filesystem.
    assert client.get("/assets/index.html").status_code == 404
    assert client.get("/assets/..%2Fapi.py").status_code == 404


def test_feedback_for_head_only_returns_matching_snapshot():
    from rsi_agent.feedback import FeedbackStore
    from rsi_agent.models import FeedbackEvent, FeedbackKind

    store = FeedbackStore()
    store.record_feedback(FeedbackEvent("a", "org/repo", 3, "head", FeedbackKind.ACCEPTED))
    store.record_feedback(FeedbackEvent("b", "org/repo", 3, "other", FeedbackKind.FALSE_POSITIVE))
    assert [item.event_id for item in store.feedback_for_head("org/repo", 3, "head")] == ["a"]
