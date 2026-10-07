from __future__ import annotations

import json
import sqlite3
from uuid import uuid4
from dataclasses import is_dataclass, asdict
from pathlib import Path

from .models import Finding, JobStatus, PRSnapshot, ReviewJob, RiskSurface, utc_now


def _json_default(value):
    if hasattr(value, "value"):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    return str(value)


class TaskStore:
    """Small durable store for idempotent review jobs and state transitions."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        if str(path) != ":memory:":
            try:
                self.connection.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
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
                attempt_count INTEGER NOT NULL DEFAULT 0,
                failure_class TEXT,
                failure_code TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                lease_owner TEXT,
                lease_until TEXT,
                UNIQUE(repo_id, pr_number, head_sha, policy_version)
            );
            CREATE TABLE IF NOT EXISTS review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS findings (
                finding_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                risk_surface TEXT NOT NULL,
                claim TEXT NOT NULL,
                file TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                severity TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_runtime (
                job_id TEXT PRIMARY KEY,
                route_json TEXT NOT NULL,
                features_json TEXT NOT NULL,
                nodes_json TEXT NOT NULL,
                budget_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_audit_events (
                event_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                job_id TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                trace_id TEXT,
                source TEXT NOT NULL,
                node TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                duration_ms REAL,
                error_class TEXT,
                error_code TEXT,
                metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_job ON review_audit_events(job_id, event_id);
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(review_jobs)")}
        for name, definition in {
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "failure_class": "TEXT",
            "failure_code": "TEXT",
            "last_error": "TEXT",
            "updated_at": "TEXT",
            "lease_owner": "TEXT",
            "lease_until": "TEXT",
        }.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE review_jobs ADD COLUMN {name} {definition}")
        self.connection.execute("UPDATE review_jobs SET updated_at=created_at WHERE updated_at IS NULL")
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
            "INSERT INTO review_jobs(job_id,repo_id,pr_number,base_sha,head_sha,policy_version,changed_files_json,diff,status,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job.job_id, job.snapshot.repo_id, job.snapshot.pr_number, job.snapshot.base_sha,
             job.snapshot.head_sha, job.policy_version, json.dumps(job.snapshot.changed_files),
             job.snapshot.diff, job.status.value, job.created_at, job.created_at),
        )
        self.connection.execute(
            "INSERT INTO review_events(job_id, from_status, to_status, created_at) VALUES (?, ?, ?, ?)",
            (job.job_id, None, job.status.value, utc_now()),
        )
        self.connection.commit()
        return job

    def transition(self, job_id: str, expected: JobStatus, target: JobStatus) -> None:
        cursor = self.connection.execute(
            "UPDATE review_jobs SET status=?, updated_at=? WHERE job_id=? AND status=?",
            (target.value, utc_now(), job_id, expected.value),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"invalid transition for {job_id}: {expected} -> {target}")
        self.connection.execute(
            "INSERT INTO review_events(job_id, from_status, to_status, created_at) VALUES (?, ?, ?, ?)",
            (job_id, expected.value, target.value, utc_now()),
        )
        self.connection.commit()

    def begin_attempt(self, job_id: str, owner: str, lease_seconds: int = 120) -> bool:
        now = utc_now()
        from datetime import datetime, timedelta, timezone
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        cursor = self.connection.execute(
            "UPDATE review_jobs SET attempt_count=attempt_count+1, updated_at=?, lease_owner=?, lease_until=? WHERE job_id=? AND (lease_until IS NULL OR lease_until < ? OR lease_owner=?)",
            (now, owner, lease_until, job_id, now, owner),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def clear_lease(self, job_id: str) -> None:
        self.connection.execute("UPDATE review_jobs SET lease_owner=NULL, lease_until=NULL, updated_at=? WHERE job_id=?", (utc_now(), job_id))
        self.connection.commit()

    def recover_interrupted(self, job_id: str) -> None:
        row = self.connection.execute("SELECT status FROM review_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row and row[0] in {JobStatus.ANALYZING.value, JobStatus.VERIFYING.value}:
            self.connection.execute("UPDATE review_jobs SET status=?, lease_owner=NULL, lease_until=NULL, updated_at=? WHERE job_id=?", (JobStatus.RECEIVED.value, utc_now(), job_id))
            self.connection.execute("INSERT INTO review_events(job_id, from_status, to_status, created_at) VALUES (?, ?, ?, ?)", (job_id, row[0], JobStatus.RECEIVED.value, utc_now()))
            self.connection.commit()

    def mark_failed(self, job_id: str, failure_class: str, failure_code: str, error: str) -> None:
        detail = error[:1000]
        previous = self.connection.execute("SELECT status FROM review_jobs WHERE job_id=?", (job_id,)).fetchone()
        self.connection.execute("UPDATE review_jobs SET status=?, failure_class=?, failure_code=?, last_error=?, lease_owner=NULL, lease_until=NULL, updated_at=? WHERE job_id=?", (JobStatus.FAILED.value, failure_class, failure_code, detail, utc_now(), job_id))
        if previous and previous[0] != JobStatus.FAILED.value:
            self.connection.execute("INSERT INTO review_events(job_id, from_status, to_status, created_at) VALUES (?, ?, ?, ?)", (job_id, previous[0], JobStatus.FAILED.value, utc_now()))
        self.connection.commit()

    def retry_failed(self, job_id: str) -> None:
        self.connection.execute("UPDATE review_jobs SET status=?, lease_owner=NULL, lease_until=NULL, updated_at=? WHERE job_id=? AND status=?", (JobStatus.RECEIVED.value, utc_now(), job_id, JobStatus.FAILED.value))
        self.connection.execute("INSERT INTO review_events(job_id, from_status, to_status, created_at) VALUES (?, ?, ?, ?)", (job_id, JobStatus.FAILED.value, JobStatus.RECEIVED.value, utc_now()))
        self.connection.commit()

    def mark_stale(self, job_id: str, reason: str = "head_changed") -> None:
        self.connection.execute("UPDATE review_jobs SET status=?, failure_class=?, failure_code=?, last_error=?, updated_at=? WHERE job_id=?", (JobStatus.STALE.value, "STALE_HEAD", reason, reason, utc_now(), job_id))
        self.connection.commit()

    def get_job(self, job_id: str) -> ReviewJob:
        row = self.connection.execute("SELECT * FROM review_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return self._job_from_row(row)

    def list_jobs(self, limit: int = 50) -> tuple[ReviewJob, ...]:
        rows = self.connection.execute(
            "SELECT * FROM review_jobs ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 200)),)
        ).fetchall()
        return tuple(self._job_from_row(row) for row in rows)

    def save_findings(self, findings: tuple[Finding, ...]) -> None:
        for finding in findings:
            self.connection.execute(
                "INSERT OR REPLACE INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (finding.finding_id, finding.job_id, finding.risk_surface.value, finding.claim,
                 finding.file, finding.start_line, finding.end_line, json.dumps(finding.evidence_refs),
                 finding.verification_status, finding.severity),
            )
        self.connection.commit()

    def findings_for_job(self, job_id: str) -> tuple[Finding, ...]:
        job = self.get_job(job_id)
        rows = self.connection.execute("SELECT * FROM findings WHERE job_id=? ORDER BY finding_id", (job_id,)).fetchall()
        return tuple(Finding(
            row["finding_id"], job_id, job.snapshot, RiskSurface(row["risk_surface"]), row["claim"],
            row["file"], row["start_line"], row["end_line"], tuple(json.loads(row["evidence_refs_json"])),
            row["verification_status"], row["severity"],
        ) for row in rows)

    def save_runtime(self, job_id: str, route: tuple[str, ...], features: object, nodes: list[dict], budget: dict) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO review_runtime VALUES (?, ?, ?, ?, ?)",
            (job_id, json.dumps(route), json.dumps(features, default=_json_default), json.dumps(nodes, default=_json_default), json.dumps(budget, default=_json_default)),
        )
        self.connection.commit()

    def runtime_for_job(self, job_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM review_runtime WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        return {"route": json.loads(row["route_json"]), "risk_features": json.loads(row["features_json"]), "nodes": json.loads(row["nodes_json"]), "budget": json.loads(row["budget_json"])}

    def events_for_job(self, job_id: str) -> tuple[dict, ...]:
        rows = self.connection.execute("SELECT from_status,to_status,created_at FROM review_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        return tuple({"from": row["from_status"], "to": row["to_status"], "at": row["created_at"]} for row in rows)

    def record_audit_event(
        self,
        job_id: str,
        source: str,
        node: str,
        event_type: str,
        status: str = "completed",
        *,
        event_id: str | None = None,
        attempt: int = 0,
        trace_id: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_ms: float | None = None,
        error_class: str | None = None,
        error_code: str | None = None,
        metadata: dict | None = None,
    ) -> bool:
        event_id = event_id or str(uuid4())
        try:
            self.connection.execute(
                """INSERT INTO review_audit_events
                (event_id,schema_version,job_id,attempt,trace_id,source,node,event_type,status,
                 started_at,finished_at,duration_ms,error_class,error_code,metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, 1, job_id, attempt, trace_id, source, node, event_type, status,
                 started_at, finished_at, duration_ms, error_class, error_code,
                 json.dumps(metadata or {}, sort_keys=True, default=str)),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def audit_events_for_job(self, job_id: str) -> tuple[dict, ...]:
        rows = self.connection.execute(
            "SELECT * FROM review_audit_events WHERE job_id=? ORDER BY rowid", (job_id,)
        ).fetchall()
        return tuple({
            "event_id": row["event_id"], "schema_version": row["schema_version"],
            "job_id": row["job_id"], "attempt": row["attempt"], "trace_id": row["trace_id"],
            "source": row["source"], "node": row["node"], "event_type": row["event_type"],
            "status": row["status"], "started_at": row["started_at"],
            "finished_at": row["finished_at"], "duration_ms": row["duration_ms"],
            "error_class": row["error_class"], "error_code": row["error_code"],
            "metadata": json.loads(row["metadata_json"]),
        } for row in rows)

    def find_job(self, snapshot: PRSnapshot, policy_version: str) -> ReviewJob | None:
        row = self.connection.execute(
            "SELECT * FROM review_jobs WHERE repo_id=? AND pr_number=? AND head_sha=? AND policy_version=?",
            (snapshot.repo_id, snapshot.pr_number, snapshot.head_sha, policy_version),
        ).fetchone()
        return self._job_from_row(row) if row else None

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> ReviewJob:
        snapshot = PRSnapshot(
            row["repo_id"], row["pr_number"], row["base_sha"], row["head_sha"],
            tuple(json.loads(row["changed_files_json"])), row["diff"],
        )
        return ReviewJob(row["job_id"], snapshot, row["policy_version"], JobStatus(row["status"]), row["created_at"], row["attempt_count"] or 0, row["failure_class"], row["failure_code"], row["last_error"])
