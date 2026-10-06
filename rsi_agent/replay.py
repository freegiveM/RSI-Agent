from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .models import EvaluationResult


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    split: str
    repository: str
    changed_files: tuple[str, ...]
    diff: str
    expected: tuple[dict, ...]


@dataclass(frozen=True)
class ReplayFinding:
    rule_id: str
    path: str
    start_line: int


def load_jsonl(path: str | Path, split: str | None = None) -> tuple[ReplayCase, ...]:
    cases: list[ReplayCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if split and item.get("split") != split:
            continue
        cases.append(ReplayCase(
            item["id"], item.get("split", "unknown"), item["repository"],
            tuple(item.get("after_files", {}).keys()), item.get("diff", ""),
            tuple(item.get("expected_findings", ())),
        ))
    return tuple(cases)


def evaluate(cases: Iterable[ReplayCase], detector: Callable[[ReplayCase], Iterable[ReplayFinding]], policy_id: str) -> EvaluationResult:
    cases = tuple(cases)
    expected = 0
    predicted = 0
    matched = 0
    cost = 0
    for case in cases:
        truth = {(item.get("rule_id"), item.get("path"), item.get("start_line")) for item in case.expected}
        found = {(item.rule_id, item.path, item.start_line) for item in detector(case)}
        expected += len(truth)
        predicted += len(found)
        matched += len(truth & found)
        cost += 1
    false_positives = max(predicted - matched, 0)
    negatives = max(sum(1 for case in cases if not case.expected), 1)
    metrics = {
        "recall": matched / expected if expected else 1.0,
        "precision": matched / predicted if predicted else 1.0,
        # Case-level proxy; a production benchmark should provide reviewed negatives.
        "false_positive_case_rate": false_positives / negatives,
        "cost_delta": 0.0,
        "cases": float(cost),
    }
    split = cases[0] if cases else None
    return EvaluationResult(policy_id, split.split if split else "unknown", metrics, "MEASURED")


def shadow(cases: Iterable[ReplayCase], detector: Callable[[ReplayCase], Iterable[ReplayFinding]], policy_id: str) -> EvaluationResult:
    """Run a candidate for observation only; this function has no publish side effect."""
    return evaluate(tuple(cases), detector, policy_id)
