# AgentScope integration boundary

The repository owns the review contracts. AgentScope is an execution adapter, not the source of business truth.

## Adapter boundary

`AgentRunner.run(role, context, tools)` is the stable project interface. The current tests use `DeterministicAgentRunner`, so the workflow is testable without credentials. A later adapter can translate this call into AgentScope agents, tool calls, sessions and traces without changing routing, evidence validation or persistence.

## Agent responsibilities

- `triage`: risk-surface extraction and route recommendation.
- `security`: input/sink, authorization, parser and sensitive-data checks.
- `correctness`: state, concurrency, exception and regression checks.
- `verifier`: independent evidence re-check; only structured claims are passed in.

Agents return structured data. The worker, state machine and publish gate remain deterministic code.

## Local verification

```powershell
python -m pytest
```

The deterministic runner proves that a review reaches `COMPLETED`, detector output becomes a structured finding, invalid verifier output becomes `insufficient_evidence`, and no model credentials are required. It is also useful for implementing a model-backed adapter without coupling business state to a provider SDK.
