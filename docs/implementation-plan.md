# Implementation Plan

## Stage 1: Durable review core

Scope:

- PR snapshot and review-key idempotency;
- webhook event deduplication;
- SQLite task state and transition history;
- deterministic risk-surface precheck and routing;
- package metadata, README and unit tests.

Acceptance:

```powershell
python -m pytest
```

Expected: all tests pass without model credentials, network access or GitHub access. The tests must cover duplicate webhook events, duplicate review jobs, legal state transitions, illegal transitions, security routing, correctness routing and low-signal verifier fallback.

## Stage 2: AgentScope review workflow

Scope:

- AgentScope adapter behind project-owned interfaces;
- Security and Correctness specialist agents;
- tool registry with schemas, timeout and read-only permissions;
- independent Verifier and Finding evidence contract;
- async worker, checkpoint, lease and stale-head checks;
- fixture-based integration tests with a deterministic fake model.

Acceptance:

- a fixed PR fixture reaches `verified`, `rejected` or `insufficient_evidence`;
- invalid model output cannot enter the publish path;
- a stale `head_sha` cannot publish a finding;
- worker timeout resumes from the last checkpoint;
- duplicate delivery does not run the review twice.

## Stage 3: Feedback and train-free policy evolution

Scope:

- local review page and structured feedback events;
- missed-risk report workflow;
- SQLite + FTS5 memory engine with candidate/active/stale states;
- GEPA-inspired Reflection Agent producing constrained PolicyPatch candidates;
- paired replay, Validation, repository-level Holdout and Shadow runs;
- policy registry, metrics and manual activation.

Acceptance:

- feedback is idempotent and permission checked;
- a missed-risk report cannot enter active memory without confirmation;
- a candidate cannot modify protected fields;
- Holdout data is unavailable to candidate generation;
- dominated candidates leave the active search frontier but remain auditable;
- Shadow never publishes a PR comment, blocks a merge or writes production memory;
- candidate status is `PASSED`, `REJECTED` or `INCONCLUSIVE`, never an ambiguous boolean.

Canary traffic splitting, automatic online activation and automatic patch generation are explicitly out of scope for the three-stage implementation.
