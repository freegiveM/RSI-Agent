from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import time
from uuid import uuid4

from .agents import AgentRunner, ToolRegistry, run_detector, verify_findings
from .models import JobStatus, ReviewResult
from typing import Protocol


class CommentWriter(Protocol):
    def create_issue_comment(self, repository: str, number: int, body: str) -> dict: ...
    def finding_body(self, finding) -> str: ...


class HeadReader(Protocol):
    def current_head_sha(self, repository: str, number: int) -> str: ...
from .service import ReviewService


@dataclass(frozen=True)
class WorkerConfig:
    # Leave room for a normal provider response while bounding a stuck node.
    timeout_seconds: float = 75.0
    max_retries: int = 1
    lease_seconds: int = 120
    max_tokens: int | None = None
    provider_timeout_seconds: float | None = None
    thinking_mode: str | None = None


class ReviewWorker:
    """Synchronous core of an async worker; queue adapters can call process()."""

    def __init__(self, service: ReviewService, runner: AgentRunner, tools: ToolRegistry, config: WorkerConfig | None = None, comment_writer: CommentWriter | None = None, head_reader: HeadReader | None = None) -> None:
        self.service = service
        self.runner = runner
        self.tools = tools
        self.config = config or WorkerConfig()
        self.comment_writer = comment_writer
        self.head_reader = head_reader

    def process(self, job_id: str) -> ReviewResult:
        review_started = time.perf_counter()
        try:
            self.service.store.recover_interrupted(job_id)
            if not self.service.store.begin_attempt(job_id, "worker", self.config.lease_seconds):
                self._audit(job_id, "worker", "lease", "lease_rejected", "failed")
                raise RuntimeError("job lease is held by another worker")
            job = self.service.store.get_job(job_id)
            self._audit(job_id, "worker", "lease", "lease_acquired", "completed", attempt=job.attempt_count)
            result = self.service.precheck(job_id)
            started = time.perf_counter()
            nodes = [{"node": "precheck", "status": "completed", "route": list(result.route), "duration_ms": round((time.perf_counter() - started) * 1000, 1)}]
            self._audit(job_id, "worker", "router", "route_selected", "completed", metadata={"route": list(result.route), "risk_surfaces": [item.value for item in result.risk_features.risk_surfaces]})
            job = self.service.store.get_job(job_id)
            self.service.store.transition(job_id, JobStatus.ROUTED, JobStatus.ANALYZING)
            findings = []
            for role in result.route:
                if role in {"security", "correctness"}:
                    node_started = time.perf_counter()
                    trace_count = len(getattr(self.runner, "traces", ()))
                    self._audit(job_id, "worker", role, "node_started", "running")
                    try:
                        findings.extend(self._run_with_retry(
                            run_detector, self.runner, role, job.snapshot, result.risk_features,
                            self.tools, job_id,
                        ))
                    except Exception as exc:
                        self._persist_runner_traces(job_id, trace_count)
                        self._audit(job_id, "worker", role, "node_failed", "failed", error_class=self._failure_class(exc))
                        raise
                    self._persist_runner_traces(job_id, trace_count)
                    nodes.append({"node": role, "status": "completed", "duration_ms": round((time.perf_counter() - node_started) * 1000, 1)})
                    self._audit(job_id, "worker", role, "node_finished", "completed", duration_ms=round((time.perf_counter() - node_started) * 1000, 1), metadata={"finding_count": len(findings)})
            self.service.store.transition(job_id, JobStatus.ANALYZING, JobStatus.VERIFYING)
            node_started = time.perf_counter()
            trace_count = len(getattr(self.runner, "traces", ()))
            self._audit(job_id, "worker", "verifier", "node_started", "running", metadata={"finding_count": len(findings)})
            try:
                verified = self._run_with_retry(
                    verify_findings, self.runner, tuple(findings), job.snapshot, self.tools, job_id,
                )
            except Exception as exc:
                self._persist_runner_traces(job_id, trace_count)
                self._audit(job_id, "worker", "verifier", "node_failed", "failed", error_class=self._failure_class(exc))
                raise
            self._persist_runner_traces(job_id, trace_count)
            self.service.store.save_findings(verified)
            nodes.append({"node": "verifier", "status": "completed", "duration_ms": round((time.perf_counter() - node_started) * 1000, 1)})
            self._audit(job_id, "worker", "verifier", "node_finished", "completed", duration_ms=round((time.perf_counter() - node_started) * 1000, 1), metadata={"verified_count": sum(item.verification_status == "verified" for item in verified)})
            self.service.store.save_runtime(job_id, result.route, result.risk_features, nodes, self._budget_snapshot(
                list(getattr(self.runner, "traces", ())),
                last_node_duration_ms=round((time.perf_counter() - node_started) * 1000, 1),
            ))
            if self.head_reader and self.head_reader.current_head_sha(job.snapshot.repo_id, job.snapshot.pr_number) != job.snapshot.head_sha:
                self.service.store.mark_stale(job_id, "head_changed_before_publish")
                self._audit(job_id, "github", "head", "head_changed", "skipped", error_class="STALE_HEAD", metadata={"snapshot_head_sha": job.snapshot.head_sha})
                self.service.store.clear_lease(job_id)
                return ReviewResult(self.service.store.get_job(job_id), verified, result.risk_features, result.route, result.trace)
            if self.comment_writer:
                for finding in verified:
                    if finding.verification_status == "verified":
                        try:
                            response = self.comment_writer.create_issue_comment(
                                job.snapshot.repo_id, job.snapshot.pr_number,
                                self.comment_writer.finding_body(finding),
                            )
                            event = "comment_skipped_duplicate" if response.get("deduplicated") else "comment_created"
                            self._audit(job_id, "github", "comment", event, "completed", metadata={"finding_id": finding.finding_id})
                        except Exception as exc:
                            self._audit(job_id, "github", "comment", "comment_failed", "failed", error_class="COMMENT_FAILED", metadata={"finding_id": finding.finding_id})
                            raise
            self.service.store.save_runtime(job_id, result.route, result.risk_features, nodes, self._budget_snapshot(
                list(getattr(self.runner, "traces", ())),
                last_node_duration_ms=round((time.perf_counter() - node_started) * 1000, 1),
                review_duration_ms=round((time.perf_counter() - review_started) * 1000, 1),
            ))
            self.service.store.transition(job_id, JobStatus.VERIFYING, JobStatus.COMPLETED)
            self._audit(job_id, "worker", "review", "completed", "completed")
            self.service.store.clear_lease(job_id)
            return ReviewResult(self.service.store.get_job(job_id), verified, result.risk_features, result.route, result.trace)
        except Exception as exc:
            try:
                existing = self.service.store.runtime_for_job(job_id)
                route = tuple(existing.get("route", ())) if existing else ()
                features = existing.get("risk_features") if existing else None
                nodes = list(existing.get("nodes", ())) if existing else []
                nodes.append({"node": "worker", "status": "failed", "error_class": self._failure_class(exc)})
                self.service.store.save_runtime(job_id, route, features, nodes, self._budget_snapshot(list(getattr(self.runner, "traces", ()))))
            except Exception:
                pass
            current = self.service.store.get_job(job_id).status
            if current not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STALE}:
                self.service.store.mark_failed(job_id, self._failure_class(exc), type(exc).__name__, str(exc))
            raise

    def _audit(self, job_id: str, source: str, node: str, event_type: str, status: str, *, duration_ms: float | None = None, error_class: str | None = None, metadata: dict | None = None, attempt: int | None = None) -> None:
        try:
            current = self.service.store.get_job(job_id)
            self.service.store.record_audit_event(
                job_id, source, node, event_type, status, attempt=current.attempt_count if attempt is None else attempt,
                trace_id=str(uuid4()), duration_ms=duration_ms, error_class=error_class, metadata=metadata,
            )
        except Exception:
            # Observability must not turn a valid review into a failed review.
            pass

    def _persist_runner_traces(self, job_id: str, start: int) -> None:
        traces = getattr(self.runner, "traces", ())
        for trace in list(traces)[start:]:
            phase = trace.get("phase")
            if phase == "compaction":
                event_type = "context_compacted"
            elif phase == "runner":
                event_type = "response_parsed"
            else:
                event_type = "model_finished" if not trace.get("error_class") else "model_failed"
            self._audit(
                job_id, "agentscope", str(trace.get("role", "agent")), event_type,
                "failed" if trace.get("error_class") else "completed", duration_ms=trace.get("provider_latency_ms"),
                error_class=trace.get("error_class"), metadata={key: trace.get(key) for key in ("context_level", "estimated_input_tokens", "input_tokens", "output_tokens", "reasoning_tokens", "message_count", "request_chars", "phase")},
            )

    def _budget_snapshot(self, traces: list[dict] | None = None, *, last_node_duration_ms: float | None = None, review_duration_ms: float | None = None) -> dict:
        traces = traces or []
        provider_traces = [item for item in traces if item.get("phase") == "provider"]
        usage_traces = provider_traces or [item for item in traces if item.get("phase") == "runner"] or traces
        input_tokens = [item.get("input_tokens") for item in usage_traces if item.get("input_tokens") is not None]
        estimated_tokens = [item.get("estimated_input_tokens") for item in traces if item.get("estimated_input_tokens") is not None]
        output_tokens = [item.get("output_tokens") for item in usage_traces if item.get("output_tokens") is not None]
        reasoning_tokens = [item.get("reasoning_tokens") for item in usage_traces if item.get("reasoning_tokens") is not None]
        return {
            "status": "reported" if traces or self.config.max_tokens is not None else "not_reported",
            "configured": {
                "node_timeout_seconds": self.config.timeout_seconds,
                "provider_timeout_seconds": self.config.provider_timeout_seconds,
                "max_tokens": self.config.max_tokens,
                "thinking_mode": self.config.thinking_mode,
            },
            "observed": {
                "last_node_duration_ms": last_node_duration_ms,
                "review_duration_ms": review_duration_ms,
                "provider_input_tokens": sum(input_tokens) if input_tokens else None,
                "provider_output_tokens": sum(output_tokens) if output_tokens else None,
                "provider_reasoning_tokens": sum(reasoning_tokens) if reasoning_tokens else None,
            },
            "estimated": {"input_tokens": sum(estimated_tokens) if estimated_tokens else None},
        }

    @staticmethod
    def _failure_class(exc: Exception) -> str:
        text = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timeout" in text:
            return "PROVIDER_TIMEOUT"
        if "credential" in text or "auth" in text or "api key" in text:
            return "PROVIDER_AUTH"
        if "finding" in text or "json" in text or "output" in text:
            return "INVALID_OUTPUT"
        if "comment" in text:
            return "COMMENT_FAILED"
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
