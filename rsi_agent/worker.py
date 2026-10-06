from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from .agents import AgentRunner, ToolRegistry, run_detector, verify_findings
from .models import JobStatus, ReviewResult
from .service import ReviewService


@dataclass(frozen=True)
class WorkerConfig:
    timeout_seconds: float = 30.0
    max_retries: int = 1


class ReviewWorker:
    """Synchronous core of an async worker; queue adapters can call process()."""

    def __init__(self, service: ReviewService, runner: AgentRunner, tools: ToolRegistry, config: WorkerConfig | None = None) -> None:
        self.service = service
        self.runner = runner
        self.tools = tools
        self.config = config or WorkerConfig()

    def process(self, job_id: str) -> ReviewResult:
        try:
            result = self.service.precheck(job_id)
            job = self.service.store.get_job(job_id)
            self.service.store.transition(job_id, JobStatus.ROUTED, JobStatus.ANALYZING)
            findings = []
            for role in result.route:
                if role in {"security", "correctness"}:
                    findings.extend(self._run_with_retry(
                        run_detector, self.runner, role, job.snapshot, result.risk_features,
                        self.tools, job_id,
                    ))
            self.service.store.transition(job_id, JobStatus.ANALYZING, JobStatus.VERIFYING)
            verified = self._run_with_retry(
                verify_findings, self.runner, tuple(findings), job.snapshot, self.tools, job_id,
            )
            self.service.store.transition(job_id, JobStatus.VERIFYING, JobStatus.COMPLETED)
            return ReviewResult(self.service.store.get_job(job_id), verified, result.risk_features, result.route, result.trace)
        except Exception:
            current = self.service.store.get_job(job_id).status
            if current not in {JobStatus.COMPLETED, JobStatus.FAILED}:
                self.service.store.transition(job_id, current, JobStatus.FAILED)
            raise

    def _run_with_retry(self, function, *args):
        last_error: Exception | None = None
        for _ in range(self.config.max_retries + 1):
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(function, *args)
                    return future.result(timeout=self.config.timeout_seconds)
            except FutureTimeout as exc:
                last_error = TimeoutError("agent node timed out")
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
