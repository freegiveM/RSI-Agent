from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

from .models import PRSnapshot


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), digest)


class GitHubReader(Protocol):
    def pull_snapshot(self, repository: str, number: int) -> PRSnapshot: ...


@dataclass(frozen=True)
class WebhookEvent:
    delivery_id: str
    event_name: str
    repository: str
    number: int
    snapshot: PRSnapshot
