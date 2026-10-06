# RSI-Agent

English | [中文](README.md)

Evidence-driven risk review agent for engineering Pull Requests. The project uses AgentScope as the agent execution boundary and owns PR snapshots, risk-surface routing, specialist analysis, independent verification, evidence contracts and durable task state.

## Architecture

```text
PR Webhook
  -> Idempotent job and commit snapshot
  -> Static precheck and risk routing
  -> Security / Correctness specialist agents
  -> Independent verifier
  -> Finding and PR feedback
  -> Offline Replay / Validation / Holdout / Shadow
```

The first routing layer uses a small set of risk surfaces instead of an unbounded vulnerability taxonomy. Unmatched changes go through a generic verifier or manual review and are not treated as safe by default.

AgentScope provides the agent execution, tool invocation and base observability boundary. The project owns PR idempotency, routing, evidence validation, feedback and policy versions. A candidate policy is activated explicitly only after offline evaluation.

## Project layout

```text
rsi_agent/
  models.py       # Review contracts and domain models
  store.py        # SQLite events, jobs and state transitions
  routing.py      # Risk-surface extraction and routing
  service.py      # Review workflow boundary
  agents.py       # AgentRunner, tool permissions and evidence contract
  worker.py       # Retry and timeout execution boundary
  memory.py       # FTS5 history and repository Skill loading
  feedback.py     # Feedback events and policy candidates
  policy.py       # Validation/Holdout policy gate
  replay.py       # Offline Replay and Shadow evaluation
  http_api.py     # Local feedback HTTP API
  queue.py        # In-memory and Redis Stream adapters
  run_worker.py   # Redis Stream worker entry point
  github_comments.py # GitHub comment formatting
  agentscope_runner.py # AgentScope execution boundary
  api.py           # FastAPI webhook ingress
  github_api.py   # Read-only GitHub PR client
tests/            # Automated tests
docs/             # AgentScope integration notes
```

## Quickstart

### 1. Install

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Copy the configuration template and fill in local credentials:

```powershell
Copy-Item .env.example .env
```

`.env` is ignored by Git. Never commit real tokens, passwords or webhook secrets.

### 2. Run tests

```powershell
python -m pytest -q
```

The test suite runs without model credentials or a public GitHub Webhook.

For a real GitHub integration, set `GITHUB_TOKEN` and `GITHUB_WEBHOOK_SECRET`, then run:

```powershell
python -m rsi_agent.run_api --port 8787
```

The one-command local script checks configuration, installs server dependencies and verifies Redis:

```powershell
.\scripts\start-local.ps1
```

To skip the Redis check temporarily:

```powershell
.\scripts\start-local.ps1 -SkipRedisCheck
```

Run the asynchronous worker with Redis Streams:

```powershell
python -m rsi_agent.run_worker
```

The GitHub Webhook must include `Pull requests`. Select `Issue comments` as well when comment-based feedback is enabled. The webhook only validates and enqueues; the worker performs the review.

Install the real AgentScope runner with:

```powershell
python -m pip install -e ".[agent]"
```

When `DEEPSEEK_API_KEY` is set, `run_worker` creates Security, Correctness and Verifier agents through AgentScope 2.0.9. Set `DEEPSEEK_MODEL` to the model ID available in your DeepSeek account.

### 3. Start the local feedback service

```powershell
python -m rsi_agent.run_feedback_server --port 8787
```

### 4. Submit feedback

Send JSON to `POST /feedback`. Supported kinds are `accepted`, `false_positive`, `missed_risk` and `insufficient_evidence`.

```powershell
$payload = @{
  event_id = "feedback-1"
  repo_id = "org/repo"
  pr_number = 1
  head_sha = "<commit-sha>"
  kind = "accepted"
  note = "reviewed by maintainer"
  reporter = "local"
} | ConvertTo-Json

Invoke-RestMethod -Uri http://127.0.0.1:8787/feedback `
  -Method Post -ContentType "application/json" -Body $payload
```

Duplicate `event_id` values are ignored. `missed_risk` feedback requires a note before it can enter the re-review flow.
