from pathlib import Path

from rsi_agent.agents import DeterministicAgentRunner, ToolRegistry
from rsi_agent.models import PRSnapshot, RiskSurface
from rsi_agent.service import ReviewService
from rsi_agent.store import TaskStore
from rsi_agent.worker import ReviewWorker


class RecordingComments:
    def __init__(self):
        self.bodies = []

    def finding_body(self, finding):
        return f"<!-- rsi-agent:finding={finding.finding_id} -->\n{finding.claim}"

    def create_issue_comment(self, repository, number, body):
        self.bodies.append((repository, number, body))
        return {"id": len(self.bodies)}


def test_synthetic_finding_completes_and_publishes_comment():
    fixture = Path(__file__).parent / "fixtures" / "e2e" / "vulnerable_query.py"
    source = fixture.read_text(encoding="utf-8")
    snapshot = PRSnapshot("org/repo", 7, "base", "head", (str(fixture.relative_to(Path(__file__).parents[1])),), source)
    runner = DeterministicAgentRunner({
        "security": {"findings": [{
            "finding_id": "fixture-sqli", "risk_surface": RiskSurface.INPUT_SINK,
            "claim": "request parameter reaches SQL query", "file": snapshot.changed_files[0],
            "start_line": 6, "end_line": 6, "evidence_refs": ["fixture:line-6"], "severity": "high",
        }]},
        "verifier": {"status": "verified"},
    })
    comments = RecordingComments()
    service = ReviewService(TaskStore())
    job = service.submit(snapshot)
    result = ReviewWorker(service, runner, ToolRegistry(), comment_writer=comments).process(job.job_id)
    assert result.job.status.value == "COMPLETED"
    assert result.findings[0].verification_status == "verified"
    assert len(comments.bodies) == 1
    assert "rsi-agent:finding=fixture-sqli" in comments.bodies[0][2]
