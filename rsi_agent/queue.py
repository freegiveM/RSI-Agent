from __future__ import annotations

from queue import Queue
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    job_id: str


class ReviewQueue:
    """Small local queue; a Redis implementation can satisfy the same methods."""

    def __init__(self) -> None:
        self._queue: Queue[str] = Queue()

    def publish(self, job_id: str) -> None:
        self._queue.put(job_id)

    def get(self, timeout: float | None = None) -> str:
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        self._queue.task_done()


class RedisStreamQueue:
    """Redis Streams adapter using RESP2 for compatibility with Redis 5."""

    def __init__(self, url: str, stream: str = "rsi:review-jobs", group: str = "rsi-workers", consumer: str = "worker-1") -> None:
        import redis
        self.client = redis.Redis.from_url(url, protocol=2, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
        self.stream, self.group, self.consumer = stream, group, consumer
        try:
            self.client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def publish(self, job_id: str) -> str:
        return str(self.client.xadd(self.stream, {"job_id": job_id}, maxlen=10000, approximate=True))

    def consume(self, block_ms: int = 5000, count: int = 1) -> tuple[QueueMessage, ...]:
        rows = self.client.xreadgroup(self.group, self.consumer, {self.stream: ">"}, count=count, block=block_ms)
        return tuple(QueueMessage(message_id, fields["job_id"]) for _, entries in rows for message_id, fields in entries)

    def ack(self, message_id: str) -> None:
        self.client.xack(self.stream, self.group, message_id)
