from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from .models import PRSnapshot, RiskFeatureSet


_DIFF_FILE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@.*$", re.MULTILINE)


def estimate_tokens(text: str) -> int:
    """Conservative provider-independent estimate for UTF-8/code text."""
    return max(1, (len(text) + 3) // 4) if text else 0


@dataclass(frozen=True)
class DiffHunk:
    file: str
    start_line: int
    end_line: int
    text: str
    risk_score: int = 0

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(frozen=True)
class ContextPack:
    role: str
    level: str
    text: str
    selected_files: tuple[str, ...]
    selected_hunks: tuple[str, ...]
    omitted_files: tuple[str, ...]
    tokens: int
    budget: int
    compression_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "context_level": self.level,
            "context": self.text,
            "selected_files": self.selected_files,
            "selected_hunks": self.selected_hunks,
            "omitted_files": self.omitted_files,
            "estimated_input_tokens": self.tokens,
            "input_budget": self.budget,
            "compression_reason": self.compression_reason,
        }


@dataclass(frozen=True)
class ContextBudget:
    security: int = 10_000
    correctness: int = 10_000
    verifier: int = 6_000
    summary: int = 1_000
    hunk: int = 4_000

    def for_role(self, role: str) -> int:
        return {"security": self.security, "correctness": self.correctness, "verifier": self.verifier}.get(role, self.correctness)


class ContextExpander(Protocol):
    def expand(self, snapshot: PRSnapshot, files: tuple[str, ...], reason: str) -> str: ...


class SummaryCache:
    """Small versioned cache boundary; persistence can be supplied later."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str, str], str] = {}

    def get_or_build(self, repo_id: str, head_sha: str, parser_version: str, policy_version: str, builder: Callable[[], str]) -> str:
        key = (repo_id, head_sha, parser_version, policy_version)
        if key not in self._values:
            self._values[key] = builder()
        return self._values[key]


class ReviewContextBuilder:
    """Builds bounded, role-specific PR context without calling a model."""

    SECURITY_TERMS = ("auth", "permission", "tenant", "sql", "query", "exec", "shell", "deserialize", "pickle", "yaml", "file", "http")
    CORRECTNESS_TERMS = ("lock", "transaction", "retry", "async", "cache", "state", "error", "exception", "return", "timeout")

    def __init__(self, budget: ContextBudget | None = None, cache: SummaryCache | None = None, parser_version: str = "diff-v1") -> None:
        self.budget = budget or ContextBudget()
        self.cache = cache or SummaryCache()
        self.parser_version = parser_version

    def build(self, snapshot: PRSnapshot, features: RiskFeatureSet, role: str) -> ContextPack:
        budget = self.budget.for_role(role)
        hunks = self._index_hunks(snapshot.diff, snapshot.changed_files, role)
        summary = self.cache.get_or_build(snapshot.repo_id, snapshot.head_sha, self.parser_version, "risk-v1", lambda: self._summary(snapshot, features))
        selected: list[DiffHunk] = []
        used = estimate_tokens(summary)
        for hunk in sorted(hunks, key=lambda item: (-item.risk_score, item.file, item.start_line)):
            text = hunk.text
            if hunk.tokens > self.budget.hunk:
                text = self._trim_hunk(text, self.budget.hunk * 4)
                hunk = DiffHunk(hunk.file, hunk.start_line, hunk.end_line, text, hunk.risk_score)
            if used + hunk.tokens > budget:
                continue
            selected.append(hunk)
            used += hunk.tokens
        while True:
            selected_files = tuple(dict.fromkeys(hunk.file for hunk in selected))
            omitted_files = tuple(file for file in snapshot.changed_files if file not in selected_files)
            parts = [summary]
            if selected:
                parts.append("\n\n## Risk-relevant hunks\n" + "\n\n".join(hunk.text for hunk in selected))
            if omitted_files:
                omitted_header = "\n\n## Omitted files\n"
                omitted_text = omitted_header
                for file in omitted_files:
                    candidate = omitted_text + file + "\n"
                    if estimate_tokens("".join(parts) + candidate) > budget:
                        break
                    omitted_text = candidate
                if omitted_text != omitted_header:
                    parts.append(omitted_text.rstrip())
            text = "".join(parts)
            if estimate_tokens(text) <= budget or not selected:
                break
            selected.pop()
        selected_files = tuple(dict.fromkeys(hunk.file for hunk in selected))
        omitted_files = tuple(file for file in snapshot.changed_files if file not in selected_files)
        level = "L1" if selected else "L0"
        reason = "input_budget" if omitted_files else None
        return ContextPack(role, level, text, selected_files, tuple(hunk.file + ":" + str(hunk.start_line) for hunk in selected), omitted_files, estimate_tokens(text), budget, reason)

    def _summary(self, snapshot: PRSnapshot, features: RiskFeatureSet) -> str:
        files = "\n".join(f"- {file}" for file in snapshot.changed_files)
        surfaces = ", ".join(surface.value for surface in features.risk_surfaces) or "none"
        return (
            "## PR summary\n"
            f"repository: {snapshot.repo_id}\npr: {snapshot.pr_number}\n"
            f"base_sha: {snapshot.base_sha}\nhead_sha: {snapshot.head_sha}\n"
            f"changed_files: {len(snapshot.changed_files)}\n"
            f"risk_surfaces: {surfaces}\n"
            f"executable_change: {features.executable_change}\n"
            f"test_gap: {features.test_gap}\n"
            f"files:\n{files}"
        )

    def _index_hunks(self, diff: str, files: Iterable[str], role: str) -> tuple[DiffHunk, ...]:
        if not diff:
            return ()
        matches = list(_DIFF_FILE.finditer(diff))
        hunks: list[DiffHunk] = []
        terms = self.SECURITY_TERMS if role == "security" else self.CORRECTNESS_TERMS
        for index, file_match in enumerate(matches):
            file = file_match.group(2)
            start = file_match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(diff)
            block = diff[start:end]
            hunk_matches = list(_HUNK.finditer(block))
            if not hunk_matches:
                hunks.append(DiffHunk(file, 1, 1, f"diff --git a/{file} b/{file}\n{block.strip()}", sum(block.lower().count(term) for term in terms)))
                continue
            for hidx, match in enumerate(hunk_matches):
                hend = hunk_matches[hidx + 1].start() if hidx + 1 < len(hunk_matches) else len(block)
                text = block[match.start():hend].strip()
                count = int(match.group(2) or "1")
                hunks.append(DiffHunk(file, int(match.group(1)), int(match.group(1)) + max(count - 1, 0), text, sum(text.lower().count(term) for term in terms)))
        return tuple(hunks)

    @staticmethod
    def _trim_hunk(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        head = max_chars // 2
        tail = max_chars - head
        return text[:head] + "\n... [hunk context trimmed] ...\n" + text[-tail:]
