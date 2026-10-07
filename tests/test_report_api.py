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
    feedback = client.post(f"/api/jobs/{job.job_id}/feedback", json={"event_id": "ui-1", "kind": "accepted", "finding_id": "f-1"})
    assert feedback.status_code == 201
    assert feedback.json()["duplicate"] is False
    duplicate = client.post(f"/api/jobs/{job.job_id}/feedback", json={"event_id": "ui-1", "kind": "accepted", "finding_id": "f-1"})
    assert duplicate.json()["duplicate"] is True
    assert "RSI-Agent" in client.get("/").text
    page = client.get("/").text
    assert "document.getElementById('jobId').value = id" in page
    assert "loadJobs();\n  </script>" in page
