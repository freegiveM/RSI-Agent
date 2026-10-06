from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import JobStatus, PRSnapshot, ReviewJob, utc_now


class TaskStore:
    """Small durable store for idempotent review jobs and state transitions."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS webhook_events (
                event_id TEXT PRIMARY KEY,
                event_name TEXT NOT NULL,
                received_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_jobs (
                job_id TEXT PRIMARY KEY,
                repo_id TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                base_sha TEXT NOT NULL,
                head_sha TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                changed_files_json TEXT NOT NULL,
                diff TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(repo_id, pr_number, head_sha, policy_version)
            );
            CREATE TABLE IF NOT EXISTS review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def record_event(self, event_id: str, event_name: str, payload: dict) -> bool:
        try:
            self.connection.execute(
                "INSERT INTO webhook_events VALUES (?, ?, ?, ?)",
                (event_id, event_name, utc_now(), json.dumps(payload, sort_keys=True)),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def create_job(self, job: ReviewJob) -> ReviewJob:
        row = self.connection.execute(
            "SELECT * FROM review_jobs WHERE repo_id=? AND pr_number=? AND head_sha=? AND policy_version=?",
            (job.snapshot.repo_id, job.snapshot.pr_number, job.snapshot.head_sha, job.policy_version),
        ).fetchone()
        if row:
            return self._job_from_row(row)
        self.connection.execute(
            "INSERT INTO review_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job.job_id, job.snapshot.repo_id, job.snapshot.pr_number, job.snapshot.base_sha,
             job.snapshot.head_sha, job.policy_version, json.dumps(job.snapshot.changed_files),
             job.snapshot.diff, job.status.value, job.created_at),
        )
        self.connection.execute(
            "INSERT INTO review_events(job_id, from_status, to_status, created_at) VALUES (?, ?, ?, ?)",
            (job.job_id, None, job.status.value, utc_now()),
        )
        self.connection.commit()
        return job

    def transition(self, job_id: str, expected: JobStatus, target: JobStatus) -> None:
        cursor = self.connection.execute(
            "UPDATE review_jobs SET status=? WHERE job_id=? AND status=?",
            (target.value, job_id, expected.value),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"invalid transition for {job_id}: {expected} -> {target}")
        self.connection.execute(
            "INSERT INTO review_events(job_id, from_status, to_status, created_at) VALUES (?, ?, ?, ?)",
            (job_id, expected.value, target.value, utc_now()),
        )
        self.connection.commit()

    def get_job(self, job_id: str) -> ReviewJob:
        row = self.connection.execute("SELECT * FROM review_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return self._job_from_row(row)

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> ReviewJob:
        snapshot = PRSnapshot(
            row["repo_id"], row["pr_number"], row["base_sha"], row["head_sha"],
            tuple(json.loads(row["changed_files_json"])), row["diff"],
        )
        return ReviewJob(row["job_id"], snapshot, row["policy_version"], JobStatus(row["status"]), row["created_at"])
