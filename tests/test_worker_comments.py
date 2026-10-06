from rsi_agent.agents import DeterministicAgentRunner, ToolRegistry
from rsi_agent.models import PRSnapshot, RiskSurface
from rsi_agent.service import ReviewService
from rsi_agent.store import TaskStore
from rsi_agent.worker import ReviewWorker


class Writer:
    def __init__(self): self.comments = []
    def finding_body(self, finding): return finding.finding_id
    def create_issue_comment(self, repository, number, body): self.comments.append((repository, number, body)); return {}


def test_verified_finding_is_published_when_writer_is_configured():
    service = ReviewService(TaskStore())
    job = service.submit(PRSnapshot("org/repo", 1, "base", "head", ("src/a.py",), "query(value)"))
    runner = DeterministicAgentRunner({
        "security": {"findings": [{"finding_id": "f-1", "risk_surface": RiskSurface.INPUT_SINK, "claim": "unsafe input", "file": "src/a.py", "start_line": 1, "end_line": 1}]},
        "verifier": {"status": "verified"},
    })
    writer = Writer()
    result = ReviewWorker(service, runner, ToolRegistry(), comment_writer=writer).process(job.job_id)
    assert result.job.status.value == "COMPLETED"
    assert writer.comments == [("org/repo", 1, "f-1")]
