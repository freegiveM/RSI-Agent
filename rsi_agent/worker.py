from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import time

from .agents import AgentRunner, ToolRegistry, run_detector, verify_findings
from .models import JobStatus, ReviewResult
from typing import Protocol


class CommentWriter(Protocol):
    def create_issue_comment(self, repository: str, number: int, body: str) -> dict: ...
    def finding_body(self, finding) -> str: ...
from .service import ReviewService


@dataclass(frozen=True)
class WorkerConfig:
    # Leave room for a normal provider response while bounding a stuck node.
    timeout_seconds: float = 75.0
    max_retries: int = 1
    lease_seconds: int = 120


class ReviewWorker:
    """Synchronous core of an async worker; queue adapters can call process()."""

    def __init__(self, service: ReviewService, runner: AgentRunner, tools: ToolRegistry, config: WorkerConfig | None = None, comment_writer: CommentWriter | None = None) -> None:
        self.service = service
        self.runner = runner
        self.tools = tools
        self.config = config or WorkerConfig()
        self.comment_writer = comment_writer

    def process(self, job_id: str) -> ReviewResult:
        try:
            self.service.store.recover_interrupted(job_id)
            if not self.service.store.begin_attempt(job_id, "worker", self.config.lease_seconds):
                raise RuntimeError("job lease is held by another worker")
            result = self.service.precheck(job_id)
            started = time.perf_counter()
            nodes = [{"node": "precheck", "status": "completed", "route": list(result.route), "duration_ms": round((time.perf_counter() - started) * 1000, 1)}]
            job = self.service.store.get_job(job_id)
            self.service.store.transition(job_id, JobStatus.ROUTED, JobStatus.ANALYZING)
            findings = []
            for role in result.route:
                if role in {"security", "correctness"}:
                    node_started = time.perf_counter()
                    findings.extend(self._run_with_retry(
                        run_detector, self.runner, role, job.snapshot, result.risk_features,
                        self.tools, job_id,
                    ))
                    nodes.append({"node": role, "status": "completed", "duration_ms": round((time.perf_counter() - node_started) * 1000, 1)})
            self.service.store.transition(job_id, JobStatus.ANALYZING, JobStatus.VERIFYING)
            node_started = time.perf_counter()
            verified = self._run_with_retry(
                verify_findings, self.runner, tuple(findings), job.snapshot, self.tools, job_id,
            )
            self.service.store.save_findings(verified)
            nodes.append({"node": "verifier", "status": "completed", "duration_ms": round((time.perf_counter() - node_started) * 1000, 1)})
            self.service.store.save_runtime(job_id, result.route, result.risk_features, nodes, {"status": "not_reported", "worker_timeout_seconds": self.config.timeout_seconds})
            if self.comment_writer:
                for finding in verified:
                    if finding.verification_status == "verified":
                        self.comment_writer.create_issue_comment(
                            job.snapshot.repo_id, job.snapshot.pr_number,
                            self.comment_writer.finding_body(finding),
                        )
            self.service.store.transition(job_id, JobStatus.VERIFYING, JobStatus.COMPLETED)
            self.service.store.clear_lease(job_id)
            return ReviewResult(self.service.store.get_job(job_id), verified, result.risk_features, result.route, result.trace)
        except Exception as exc:
            try:
                self.service.store.save_runtime(job_id, (), None, [{"node": "worker", "status": "failed", "error_class": self._failure_class(exc)}], {"status": "not_reported", "worker_timeout_seconds": self.config.timeout_seconds})
            except Exception:
                pass
            current = self.service.store.get_job(job_id).status
            if current not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STALE}:
                self.service.store.mark_failed(job_id, self._failure_class(exc), type(exc).__name__, str(exc))
            raise

    @staticmethod
    def _failure_class(exc: Exception) -> str:
        text = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timeout" in text:
            return "PROVIDER_TIMEOUT"
        if "credential" in text or "auth" in text or "api key" in text:
            return "PROVIDER_AUTH"
        if "finding" in text or "json" in text or "output" in text:
            return "INVALID_OUTPUT"
        if "lease" in text:
            return "TRANSIENT_EXHAUSTED"
        return "TRANSIENT_EXHAUSTED"

    def _run_with_retry(self, function, *args):
        last_error: Exception | None = None
        for _ in range(self.config.max_retries + 1):
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(function, *args)
            try:
                return future.result(timeout=self.config.timeout_seconds)
            except FutureTimeout as exc:
                last_error = TimeoutError("agent node timed out")
            except Exception as exc:
                last_error = exc
            finally:
                # Do not wait for a provider call that ignored its timeout.
                executor.shutdown(wait=False, cancel_futures=True)
        assert last_error is not None
        raise last_error
