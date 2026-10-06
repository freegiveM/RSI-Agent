from rsi_agent.agents import DeterministicAgentRunner, ToolRegistry
from rsi_agent.models import PRSnapshot
from rsi_agent.service import ReviewService
from rsi_agent.store import TaskStore
from rsi_agent.worker import ReviewWorker
from rsi_agent.worker import WorkerConfig


def test_worker_runs_review_to_completion():
    service = ReviewService(TaskStore())
    job = service.submit(PRSnapshot("org/repo", 1, "base", "head", ("src/query.py",), "query(user_id)\nrequest.args"))
    result = ReviewWorker(service, DeterministicAgentRunner(), ToolRegistry()).process(job.job_id)
    assert result.job.status.value == "COMPLETED"


def test_worker_marks_job_failed_after_agent_error():
    class FailingRunner:
        def run(self, role, context, tools):
            raise RuntimeError("provider unavailable")

    service = ReviewService(TaskStore())
    snapshot = PRSnapshot("org/repo", 1, "base", "head", ("src/query.py",), "query(user_id)\nrequest.args")
    job = service.submit(snapshot)
    worker = ReviewWorker(service, FailingRunner(), ToolRegistry(), WorkerConfig(max_retries=1))
    try:
        worker.process(job.job_id)
    except TimeoutError:
        pass
    except Exception:
        pass
    assert service.store.get_job(job.job_id).status.value == "FAILED"
