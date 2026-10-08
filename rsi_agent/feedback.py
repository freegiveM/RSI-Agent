from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import EvaluationResult, FeedbackEvent, FeedbackKind, PolicyCandidate


class FeedbackStore:
    """Durable feedback and policy registry with event-level idempotency."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        # The API may share the review database file with the worker.
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS feedback_events (
                event_id TEXT PRIMARY KEY,
                repo_id TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                head_sha TEXT NOT NULL,
                kind TEXT NOT NULL,
                finding_id TEXT,
                note TEXT NOT NULL,
                reporter TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_candidates (
                policy_id TEXT PRIMARY KEY,
                parent_policy_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                patch TEXT NOT NULL,
                source_feedback_json TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                policy_id TEXT NOT NULL,
                split TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                decision TEXT NOT NULL,
                UNIQUE(policy_id, split)
            );
            """
        )
        self.connection.commit()

    def record_feedback(self, event: FeedbackEvent) -> bool:
        try:
            self.connection.execute(
                "INSERT INTO feedback_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event.event_id, event.repo_id, event.pr_number, event.head_sha,
                 event.kind.value, event.finding_id, event.note, event.reporter, event.created_at),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def feedback(self) -> tuple[FeedbackEvent, ...]:
        rows = self.connection.execute("SELECT * FROM feedback_events ORDER BY created_at").fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def feedback_for_head(self, repo_id: str, pr_number: int, head_sha: str) -> tuple[FeedbackEvent, ...]:
        """Feedback is bound to an immutable head, so a report only shows events for its own snapshot."""
        rows = self.connection.execute(
            "SELECT * FROM feedback_events WHERE repo_id=? AND pr_number=? AND head_sha=? ORDER BY created_at",
            (repo_id, pr_number, head_sha),
        ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> FeedbackEvent:
        return FeedbackEvent(row["event_id"], row["repo_id"], row["pr_number"], row["head_sha"], FeedbackKind(row["kind"]), row["finding_id"], row["note"], row["reporter"], row["created_at"])

    def add_candidate(self, candidate: PolicyCandidate) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO policy_candidates VALUES (?, ?, ?, ?, ?, ?)",
            (candidate.policy_id, candidate.parent_policy_id, candidate.kind, candidate.patch, json.dumps(candidate.source_feedback_ids), candidate.status),
        )
        self.connection.commit()

    def evaluate(self, result: EvaluationResult) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO policy_evaluations(policy_id, split, metrics_json, decision) VALUES (?, ?, ?, ?)",
            (result.policy_id, result.split, json.dumps(result.metrics, sort_keys=True), result.decision),
        )
        self.connection.commit()

    def evaluations(self, policy_id: str) -> tuple[EvaluationResult, ...]:
        rows = self.connection.execute("SELECT * FROM policy_evaluations WHERE policy_id=? ORDER BY split", (policy_id,)).fetchall()
        return tuple(EvaluationResult(row["policy_id"], row["split"], json.loads(row["metrics_json"]), row["decision"]) for row in rows)

    def activate(self, policy_id: str) -> None:
        results = self.evaluations(policy_id)
        if not results or {result.split for result in results} != {"validation", "holdout"} or any(result.decision != "PASSED" for result in results):
            raise ValueError("policy must pass validation and holdout before activation")
        self.connection.execute("UPDATE policy_candidates SET status='ACTIVE' WHERE policy_id=?", (policy_id,))
        self.connection.commit()
