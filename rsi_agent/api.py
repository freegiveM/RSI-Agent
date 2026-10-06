from __future__ import annotations

import json
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from .github import GitHubReader, WebhookEvent, verify_signature
from .queue import ReviewQueue
from .service import ReviewService


def create_app(service: ReviewService, reader: GitHubReader, queue: ReviewQueue | None = None, webhook_secret: str | None = None) -> FastAPI:
    app = FastAPI(title="RSI-Agent")
    review_queue = queue or ReviewQueue()
    secret = webhook_secret if webhook_secret is not None else os.getenv("GITHUB_WEBHOOK_SECRET", "")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
            action = payload.get("action")
            pr = payload["pull_request"]
            if x_github_event != "pull_request" or action not in {"opened", "reopened", "synchronize", "ready_for_review"}:
                return {"accepted": True, "ignored": True}
            repository = payload["repository"]["full_name"]
            number = int(payload["number"])
            snapshot = reader.pull_snapshot(repository, number)
            job = service.submit(snapshot)
            review_queue.publish(job.job_id)
            return {"accepted": True, "job_id": job.job_id}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid webhook payload: {exc}")

    return app
