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
    assert any(event["event_type"] == "comment_created" for event in service.store.audit_events_for_job(job.job_id))


def test_stale_head_does_not_publish_comment():
    class HeadReader:
        def current_head_sha(self, repository, number):
            return "new-head"

    service = ReviewService(TaskStore())
    job = service.submit(PRSnapshot("org/repo", 1, "base", "old-head", ("src/a.py",), "query(value)"))
    runner = DeterministicAgentRunner({
        "security": {"findings": [{"finding_id": "f-1", "risk_surface": RiskSurface.INPUT_SINK, "claim": "unsafe input", "file": "src/a.py", "start_line": 1, "end_line": 1}]},
        "verifier": {"status": "verified"},
    })
    writer = Writer()
    result = ReviewWorker(service, runner, ToolRegistry(), comment_writer=writer, head_reader=HeadReader()).process(job.job_id)
    assert result.job.status.value == "STALE"
    assert writer.comments == []
    assert any(event["error_class"] == "STALE_HEAD" for event in service.store.audit_events_for_job(job.job_id))


def test_comment_failure_preserves_previous_runtime_nodes():
    class FailingWriter(Writer):
        def create_issue_comment(self, repository, number, body):
            raise RuntimeError("comment API unavailable")

    service = ReviewService(TaskStore())
    job = service.submit(PRSnapshot("org/repo", 1, "base", "head", ("src/a.py",), "query(value)"))
    runner = DeterministicAgentRunner({
        "security": {"findings": [{"finding_id": "f-1", "risk_surface": RiskSurface.INPUT_SINK, "claim": "unsafe input", "file": "src/a.py", "start_line": 1, "end_line": 1}]},
        "verifier": {"status": "verified"},
    })
    try:
        ReviewWorker(service, runner, ToolRegistry(), comment_writer=FailingWriter()).process(job.job_id)
    except RuntimeError:
        pass
    runtime = service.store.runtime_for_job(job.job_id)
    assert runtime is not None
    assert any(node["node"] == "verifier" for node in runtime["nodes"])
    assert any(event["error_class"] == "COMMENT_FAILED" for event in service.store.audit_events_for_job(job.job_id))
