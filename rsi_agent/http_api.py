from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .models import FeedbackEvent, FeedbackKind
from .service import ReviewService


def create_handler(service: ReviewService):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/feedback":
                self._json(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 64 * 1024:
                    raise ValueError("request too large")
                data = json.loads(self.rfile.read(length))
                event = FeedbackEvent(
                    event_id=str(data["event_id"]), repo_id=str(data["repo_id"]),
                    pr_number=int(data["pr_number"]), head_sha=str(data["head_sha"]),
                    kind=FeedbackKind(data["kind"]), finding_id=data.get("finding_id"),
                    note=str(data.get("note", "")), reporter=str(data.get("reporter", "local")),
                )
                created = service.submit_feedback(event)
                self._json(200, {"accepted": created, "event_id": event.event_id})
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, *_args) -> None:
            return

    return Handler


def serve(service: ReviewService, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), create_handler(service))
    return server
