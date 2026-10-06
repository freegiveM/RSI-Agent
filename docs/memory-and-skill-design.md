# Memory and Skill Design

This project uses two storage paths because review execution state and repository knowledge have different consistency requirements.

## Storage split

SQLite is the source of truth for operational data:

- review jobs, leases, checkpoints and retry attempts;
- structured findings and feedback events;
- historical cases and their verification outcomes;
- memory metadata, lifecycle state and policy versions.

The repository workspace stores human-readable knowledge:

```text
.rsi/
  memory/
    MEMORY.md                 # small, stable repository conventions
    security/
      input-sink.md
      auth-boundary.md
    correctness/
      state-and-concurrency.md
  skills/
    security-input/
      SKILL.md
      examples.md
      metadata.json
```

`MEMORY.md` is the default context. Detail files are loaded only when the current diff matches their declared scope. A file is not treated as executable policy merely because it is present in the repository; loaded instructions are bounded by the same tool and evidence contracts as model output.

The workspace files are versioned with the repository when the user opts in. SQLite remains local runtime state and can be deleted or rebuilt from event history.

## Retrieval

Retrieval is deliberately hybrid but text-only:

1. Always load a size-limited `MEMORY.md` header and repository metadata.
2. Use SQLite FTS5/BM25 for historical findings, feedback and compact skill summaries. BM25 handles synonyms and ranked keyword matches better than a raw grep over every case.
3. Use `rg`/grep over `.rsi/memory` and `.rsi/skills` for explicit, auditable file retrieval. This preserves file paths and line-oriented evidence for repository knowledge.
4. Merge results with source, scope, freshness and confidence metadata, then deduplicate.

The default budget is small. Retrieval expands in layers:

```text
L0: MEMORY.md header and active repository constraints
L1: matching risk-surface summary and top BM25/grep hits
L2: one or two detailed cases or skill instructions
L3: only on uncertainty, verifier request or explicit user action
```

If the token budget is exceeded, drop L3 first, then low-confidence or stale entries. Never drop the current diff, the evidence contract or a safety constraint.

## Skills and train-free evolution

A skill is a versioned, scoped procedure rather than an opaque prompt. It contains:

- trigger conditions and supported languages/frameworks;
- allowed tools and read/write requirements;
- a short procedure;
- expected evidence fields;
- negative examples and known limitations.

The active skill is selected by the route and retrieval result. Feedback can create a `SkillPatch` candidate, but it is only a file candidate until paired replay, validation and holdout checks pass. Candidate generation never edits the active skill automatically.

The policy registry stores parent version, source failures, patch diff, metrics and decision. Activation is an explicit operation, so every change is reviewable and reversible.

## Dataset note

`pr_diff_100.jsonl` is a local synthetic fixture and is intentionally ignored by Git. It is useful for parser, routing and evidence-contract regression tests. Its repeated templates, generated source metadata and small rule set make it unsuitable as a standalone claim of real-world recall or precision.
