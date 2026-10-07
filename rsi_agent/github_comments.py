from __future__ import annotations

import json
from urllib.request import Request, urlopen

from .models import Finding


class GitHubCommentWriter:
    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        self.token, self.api_url = token, api_url.rstrip("/")

    def _request(self, method: str, path: str, payload: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.api_url + path, data=body, method=method, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    def _comments(self, repository: str, number: int) -> list[dict]:
        return self._request("GET", f"/repos/{repository}/issues/{number}/comments")

    def create_issue_comment(self, repository: str, number: int, body: str) -> dict:
        marker = body.split("\n", 1)[0]
        if any(comment.get("body", "").startswith(marker) for comment in self._comments(repository, number)):
            return {"deduplicated": True, "marker": marker}
        return self._request("POST", f"/repos/{repository}/issues/{number}/comments", {"body": body})

    def finding_body(self, finding: Finding) -> str:
        marker = f"<!-- rsi-agent:finding={finding.finding_id} -->"
        return f"{marker}\n### Potential {finding.severity} risk\n\n`{finding.file}:{finding.start_line}`\n\n{finding.claim}\n\n**Verification:** `{finding.verification_status}`\n\n**Evidence:** {', '.join(finding.evidence_refs) or 'not provided'}\n\nReply with `/rsi accept {finding.finding_id}` or `/rsi false-positive {finding.finding_id}`."
