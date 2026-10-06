import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from rsi_agent.api import create_app
from rsi_agent.github import verify_signature
from rsi_agent.models import PRSnapshot
from rsi_agent.queue import ReviewQueue
from rsi_agent.service import ReviewService


class FakeReader:
    def pull_snapshot(self, repository, number):
        return PRSnapshot(repository, number, "base", "head", ("src/a.py",), "return value")


def test_webhook_signature_and_idempotency():
    service = ReviewService()
    queue = ReviewQueue()
    app = create_app(service, FakeReader(), queue, webhook_secret="secret")
    client = TestClient(app)
    payload = {"action": "opened", "number": 1, "pull_request": {}, "repository": {"full_name": "org/repo"}}
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-1", "X-Hub-Signature-256": signature}
    assert client.post("/webhooks/github", content=body, headers=headers).status_code == 202
    assert queue.get(timeout=1)
    assert client.post("/webhooks/github", content=body, headers=headers).json()["reason"] == "duplicate_delivery"


def test_invalid_webhook_signature_is_rejected():
    client = TestClient(create_app(ReviewService(), FakeReader(), webhook_secret="secret"))
    response = client.post("/webhooks/github", content=b"{}", headers={"X-GitHub-Delivery": "x", "X-Hub-Signature-256": "sha256=bad"})
    assert response.status_code == 401


def test_github_ping_is_accepted_without_pull_request_fields():
    service = ReviewService()
    app = create_app(service, FakeReader(), webhook_secret="secret")
    client = TestClient(app)
    body = json.dumps({"zen": "Keep it logically awesome."}).encode()
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": "ping", "X-GitHub-Delivery": "ping-1", "X-Hub-Signature-256": signature}
    response = client.post("/webhooks/github", content=body, headers=headers)
    assert response.status_code == 202
    assert response.json()["event"] == "ping"
