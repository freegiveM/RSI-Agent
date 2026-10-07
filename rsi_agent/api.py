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


def create_app(service: ReviewService, reader: GitHubReader, queue: ReviewQueue | None = None, webhook_secret: str | None = None) -> FastAPI:
    app = FastAPI(title="RSI-Agent")
    review_queue = queue or ReviewQueue()
    secret = webhook_secret if webhook_secret is not None else os.getenv("GITHUB_WEBHOOK_SECRET", "")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50) -> dict[str, Any]:
        return {"jobs": [_job_view(service, job.job_id) for job in service.store.list_jobs(limit)]}

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
        html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(request: Request, x_github_event: str | None = Header(default=None), x_github_delivery: str | None = Header(default=None), x_hub_signature_256: str | None = Header(default=None)) -> dict[str, Any]:
        body = await request.body()
        if secret and not verify_signature(body, x_hub_signature_256, secret):
            raise HTTPException(status_code=401, detail="invalid webhook signature")
        if not x_github_delivery:
            raise HTTPException(status_code=400, detail="missing delivery id")
        if not service.store.record_event(x_github_delivery, x_github_event or "unknown", {"source": "github"}):
            return {"accepted": False, "reason": "duplicate_delivery"}
        try:
            payload = json.loads(body)
            if x_github_event == "ping":
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
            review_queue.publish(job.job_id)
            return {"accepted": True, "job_id": job.job_id}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid webhook payload: {exc}")

    return app


def _job_view(service: ReviewService, job_id: str) -> dict[str, Any]:
    job = service.store.get_job(job_id)
    findings = service.store.findings_for_job(job_id)
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
        "policy_version": job.policy_version,
        "changed_files": list(job.snapshot.changed_files),
        "failure": {"class": job.failure_class, "code": job.failure_code, "error": job.last_error},
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
    }
