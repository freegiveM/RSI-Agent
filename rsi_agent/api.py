from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from .github import GitHubReader, verify_signature
from .queue import ReviewQueue
from .service import ReviewService
from .models import FeedbackEvent, FeedbackKind
from .diff import attach_findings, parse_unified_diff


WEB_DIR = Path(__file__).parent / "web"
WEB_ASSETS = {
    "console.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}


def create_app(service: ReviewService, reader: GitHubReader, queue: ReviewQueue | None = None, webhook_secret: str | None = None) -> FastAPI:
    app = FastAPI(title="RSI-Agent")
    review_queue = queue or ReviewQueue()
    secret = webhook_secret if webhook_secret is not None else os.getenv("GITHUB_WEBHOOK_SECRET", "")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50) -> dict[str, Any]:
        return {"jobs": [_job_summary(service, job.job_id) for job in service.store.list_jobs(limit)]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return _job_view(service, job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")

    @app.post("/api/jobs/{job_id}/feedback", status_code=201)
    async def submit_feedback(job_id: str, request: Request) -> dict[str, Any]:
        try:
            job = service.store.get_job(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        try:
            data = await request.json()
            event = FeedbackEvent(
                event_id=str(data["event_id"]), repo_id=job.snapshot.repo_id,
                pr_number=job.snapshot.pr_number, head_sha=job.snapshot.head_sha,
                kind=FeedbackKind(data["kind"]), finding_id=data.get("finding_id"),
                note=str(data.get("note", "")), reporter=str(data.get("reporter", "review-console")),
            )
            created = service.submit_feedback(event)
            return {"accepted": True, "duplicate": not created, "event_id": event.event_id}
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid feedback: {exc}")

    @app.get("/api/evolution")
    def evolution() -> dict[str, Any]:
        feedback = service.feedback_store.feedback()
        return {
            "feedback_count": len(feedback),
            "feedback_by_kind": {kind.value: sum(item.kind is kind for item in feedback) for kind in FeedbackKind},
            "candidates": [],
            "note": "候选策略和 Validation/Holdout 结果通过离线评测接口登记；当前页面只读展示，不自动激活策略。",
        }

    @app.get("/", include_in_schema=False)
    def report_page() -> Any:
        from fastapi.responses import HTMLResponse
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/assets/{name}", include_in_schema=False)
    def report_asset(name: str) -> Any:
        from fastapi.responses import Response
        # Only a fixed allowlist is served; request paths never touch the filesystem directly.
        media_type = WEB_ASSETS.get(name)
        if media_type is None:
            raise HTTPException(status_code=404, detail="asset not found")
        return Response((WEB_DIR / name).read_bytes(), media_type=media_type, headers={"Cache-Control": "no-cache"})

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(request: Request, x_github_event: str | None = Header(default=None), x_github_delivery: str | None = Header(default=None), x_hub_signature_256: str | None = Header(default=None)) -> dict[str, Any]:
        body = await request.body()
        if secret and not verify_signature(body, x_hub_signature_256, secret):
            service.store.record_audit_event(f"delivery:{x_github_delivery or 'unknown'}", "webhook", "signature", "signature_rejected", "failed", trace_id=x_github_delivery, metadata={"event": x_github_event})
            raise HTTPException(status_code=401, detail="invalid webhook signature")
        if not x_github_delivery:
            raise HTTPException(status_code=400, detail="missing delivery id")
        if not service.store.record_event(x_github_delivery, x_github_event or "unknown", {"source": "github"}):
            service.store.record_audit_event(f"delivery:{x_github_delivery}", "webhook", "delivery", "duplicate_delivery", "skipped", trace_id=x_github_delivery)
            return {"accepted": False, "reason": "duplicate_delivery"}
        try:
            payload = json.loads(body)
            if x_github_event == "ping":
                service.store.record_audit_event(f"delivery:{x_github_delivery}", "webhook", "delivery", "received", "ignored", trace_id=x_github_delivery, metadata={"event": "ping"})
                return {"accepted": True, "ignored": True, "event": "ping"}
            action = payload.get("action")
            if x_github_event != "pull_request" or action not in {"opened", "reopened", "synchronize", "ready_for_review"}:
                return {"accepted": True, "ignored": True}
            if "pull_request" not in payload or "repository" not in payload:
                raise ValueError("pull_request and repository are required")
            repository = payload["repository"]["full_name"]
            number = int(payload["number"])
            snapshot = reader.pull_snapshot(repository, number)
            job = service.submit(snapshot)
            service.store.record_audit_event(job.job_id, "webhook", "snapshot", "snapshot_loaded", "completed", trace_id=x_github_delivery, metadata={"delivery_id": x_github_delivery, "repository": repository, "pr_number": number, "head_sha": snapshot.head_sha})
            review_queue.publish(job.job_id)
            service.store.record_audit_event(job.job_id, "webhook", "queue", "enqueued", "completed", trace_id=x_github_delivery, metadata={"delivery_id": x_github_delivery})
            return {"accepted": True, "job_id": job.job_id}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid webhook payload: {exc}")

    return app


def _job_view(service: ReviewService, job_id: str) -> dict[str, Any]:
    job = service.store.get_job(job_id)
    findings = service.store.findings_for_job(job_id)
    diff_files = attach_findings(parse_unified_diff(job.snapshot.diff, job.snapshot.changed_files), findings)
    runtime = service.store.runtime_for_job(job_id)
    if job.status.value == "COMPLETED":
        outcome = "verified_risks" if any(item.verification_status == "verified" for item in findings) else "no_reportable_risks"
    elif job.status.value in {"FAILED", "STALE"}:
        outcome = "needs_attention"
    else:
        outcome = "in_progress"
    return {
        "job_id": job.job_id,
        "repository": job.snapshot.repo_id,
        "pr_number": job.snapshot.pr_number,
        "head_sha": job.snapshot.head_sha,
        "status": job.status.value,
        "outcome": outcome,
        "created_at": job.created_at,
        "attempt_count": job.attempt_count,
        "policy_version": job.policy_version,
        "changed_files": list(job.snapshot.changed_files),
        "diff": {"files": [item.as_dict() for item in diff_files]},
        "failure": {"class": job.failure_class, "code": job.failure_code, "error": job.last_error},
        "failure_guidance": _failure_guidance(job.failure_class),
        "findings": [
            {
                "finding_id": item.finding_id,
                "risk_surface": item.risk_surface.value,
                "claim": item.claim,
                "file": item.file,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "evidence_refs": list(item.evidence_refs),
                "verification_status": item.verification_status,
                "severity": item.severity,
            }
            for item in findings
        ],
        "route": runtime["route"] if runtime else None,
        "risk_features": runtime["risk_features"] if runtime else None,
        "nodes": runtime["nodes"] if runtime else [],
        "budget": runtime["budget"] if runtime else {"status": "not_reported"},
        "events": list(service.store.events_for_job(job_id)),
        "audit_events": list(service.store.audit_events_for_job(job_id)),
        "feedback": [
            {"event_id": item.event_id, "kind": item.kind.value, "finding_id": item.finding_id, "note": item.note, "created_at": item.created_at}
            for item in service.feedback_store.feedback_for_head(job.snapshot.repo_id, job.snapshot.pr_number, job.snapshot.head_sha)
        ],
    }


def _job_summary(service: ReviewService, job_id: str) -> dict[str, Any]:
    """Return a cheap list-row view; large Diff and audit payloads are detail-only."""
    job = service.store.get_job(job_id)
    findings = service.store.findings_for_job(job_id)
    if job.status.value == "COMPLETED":
        outcome = "verified_risks" if any(item.verification_status == "verified" for item in findings) else "no_reportable_risks"
    elif job.status.value in {"FAILED", "STALE"}:
        outcome = "needs_attention"
    else:
        outcome = "in_progress"
    return {
        "job_id": job.job_id, "repository": job.snapshot.repo_id,
        "pr_number": job.snapshot.pr_number, "head_sha": job.snapshot.head_sha,
        "status": job.status.value, "outcome": outcome, "created_at": job.created_at,
        "policy_version": job.policy_version, "changed_files": list(job.snapshot.changed_files),
        "finding_count": len(findings),
        "verified_count": sum(item.verification_status == "verified" for item in findings),
        "failure": {"class": job.failure_class, "code": job.failure_code},
    }


def _failure_guidance(failure_class: str | None) -> dict[str, Any]:
    guidance = {
        "PROVIDER_TIMEOUT": (True, "检查思考模式、Token 上限和 Provider 延迟预算"),
        "PROVIDER_AUTH": (False, "检查 API Key、模型权限或配额"),
        "INVALID_OUTPUT": (True, "保留原始 trace，重试或收紧结构化输出契约"),
        "STALE_HEAD": (False, "PR 已产生新提交，请重新触发审查"),
        "COMMENT_FAILED": (True, "Finding 已生成，检查 GitHub Token 权限后重试评论"),
        "QUEUE_REDELIVERY_EXHAUSTED": (False, "检查死信详情，修复根因后人工重新入队"),
        "AUDIT_WRITE_FAILED": (True, "检查 SQLite 或存储可用性；业务结果可能仍已完成"),
    }
    retry, action = guidance.get(failure_class, (False, "查看审计事件和错误详情"))
    return {"retryable": retry, "action": action}
