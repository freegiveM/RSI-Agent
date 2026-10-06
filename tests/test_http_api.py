import json
import threading
from urllib.request import Request, urlopen

from rsi_agent.http_api import serve
from rsi_agent.service import ReviewService


def test_feedback_http_endpoint_is_idempotent():
    server = serve(ReviewService(), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/feedback"
        payload = json.dumps({"event_id": "http-1", "repo_id": "org/repo", "pr_number": 1, "head_sha": "head", "kind": "accepted"}).encode()
        request = Request(url, data=payload, headers={"Content-Type": "application/json"})
        assert json.loads(urlopen(request).read())["accepted"] is True
        request = Request(url, data=payload, headers={"Content-Type": "application/json"})
        assert json.loads(urlopen(request).read())["accepted"] is False
    finally:
        server.shutdown()
        server.server_close()
