from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(StrEnum):
    RECEIVED = "RECEIVED"
    SNAPSHOTTED = "SNAPSHOTTED"
    PRECHECKED = "PRECHECKED"
    ROUTED = "ROUTED"
    ANALYZING = "ANALYZING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    STALE = "STALE"
    FAILED = "FAILED"


class RiskSurface(StrEnum):
    INPUT_SINK = "input_sink"
    AUTH_BOUNDARY = "auth_boundary"
    PARSER_SERIALIZATION = "parser_serialization"
    STATE_CONCURRENCY = "state_concurrency"
    DEPENDENCY_CONFIG = "dependency_config"
    BUSINESS_REGRESSION = "business_regression"


@dataclass(frozen=True)
class PRSnapshot:
    repo_id: str
    pr_number: int
    base_sha: str
    head_sha: str
    changed_files: tuple[str, ...]
    diff: str = ""


@dataclass(frozen=True)
class RiskFeatureSet:
    changed_surface: int
    executable_change: bool
    sensitive_sink: bool
    test_gap: bool
    static_warnings: tuple[str, ...] = ()
    risk_surfaces: tuple[RiskSurface, ...] = ()


@dataclass(frozen=True)
class ReviewJob:
    job_id: str
    snapshot: PRSnapshot
    policy_version: str
    status: JobStatus = JobStatus.RECEIVED
    created_at: str = field(default_factory=utc_now)
    attempt_count: int = 0
    failure_class: str | None = None
    failure_code: str | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class Finding:
    finding_id: str
    job_id: str
    snapshot: PRSnapshot
    risk_surface: RiskSurface
    claim: str
    file: str
    start_line: int
    end_line: int
    evidence_refs: tuple[str, ...]
    verification_status: str = "unverified"
    severity: str = "medium"


@dataclass(frozen=True)
class ReviewResult:
    job: ReviewJob
    findings: tuple[Finding, ...]
    risk_features: RiskFeatureSet
    route: tuple[str, ...]
    trace: tuple[dict[str, Any], ...]


class FeedbackKind(StrEnum):
    ACCEPTED = "accepted"
    FALSE_POSITIVE = "false_positive"
    MISSED_RISK = "missed_risk"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class FeedbackEvent:
    event_id: str
    repo_id: str
    pr_number: int
    head_sha: str
    kind: FeedbackKind
    finding_id: str | None = None
    note: str = ""
    reporter: str = "local"
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class PolicyCandidate:
    policy_id: str
    parent_policy_id: str
    kind: str
    patch: str
    source_feedback_ids: tuple[str, ...]
    status: str = "CANDIDATE"


@dataclass(frozen=True)
class EvaluationResult:
    policy_id: str
    split: str
    metrics: dict[str, float]
    decision: str
