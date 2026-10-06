from __future__ import annotations

from queue import Queue


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
