from __future__ import annotations

import json
from urllib.request import Request, urlopen

from .models import PRSnapshot


class GitHubApiReader:
    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _get(self, path: str, accept: str = "application/vnd.github+json"):
        request = Request(self.api_url + path, headers={"Authorization": f"Bearer {self.token}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"})
        with urlopen(request, timeout=20) as response:
            return response.read()

    def pull_snapshot(self, repository: str, number: int) -> PRSnapshot:
        pr = json.loads(self._get(f"/repos/{repository}/pulls/{number}").decode())
        files = json.loads(self._get(f"/repos/{repository}/pulls/{number}/files").decode())
        diff = self._get(f"/repos/{repository}/pulls/{number}", "application/vnd.github.diff").decode()
        return PRSnapshot(
            repository, number, pr["base"]["sha"], pr["head"]["sha"],
            tuple(item["filename"] for item in files), diff,
        )

    def current_head_sha(self, repository: str, number: int) -> str:
        pr = json.loads(self._get(f"/repos/{repository}/pulls/{number}").decode())
        return str(pr["head"]["sha"])
