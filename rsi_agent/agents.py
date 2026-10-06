from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from .models import Finding, PRSnapshot, RiskFeatureSet, RiskSurface


class AgentRunner(Protocol):
    def run(self, role: str, context: dict[str, Any], tools: tuple[str, ...]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ToolSpec:
    name: str
    read_only: bool = True
    timeout_seconds: float = 10.0


class ToolRegistry:
    def __init__(self, specs: tuple[ToolSpec, ...] = ()) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def allowed(self, role: str) -> tuple[str, ...]:
        permissions = {
            "security": {"read_only"},
            "correctness": {"read_only"},
            "verifier": {"read_only"},
        }
        if role not in permissions:
            return ()
        return tuple(name for name, spec in self._specs.items() if spec.read_only)

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec


class DeterministicAgentRunner:
    """Test runner; AgentScope can implement the same AgentRunner boundary."""

    def __init__(self, outputs: dict[str, dict[str, Any]] | None = None) -> None:
        self.outputs = outputs or {}

    def run(self, role: str, context: dict[str, Any], tools: tuple[str, ...]) -> dict[str, Any]:
        return self.outputs.get(role, {"findings": [], "evidence": []})


def run_detector(
    runner: AgentRunner,
    role: str,
    snapshot: PRSnapshot,
    features: RiskFeatureSet,
    tools: ToolRegistry,
    job_id: str,
) -> tuple[Finding, ...]:
    output = runner.run(
        role,
        {"job_id": job_id, "snapshot": snapshot, "features": features},
        tools.allowed(role),
    )
    if not isinstance(output, dict) or not isinstance(output.get("findings", []), list):
        raise ValueError("detector output must contain a findings list")
    findings: list[Finding] = []
    allowed_files = set(snapshot.changed_files)
    for item in output.get("findings", []):
        try:
            finding = Finding(
                finding_id=str(item["finding_id"]),
                job_id=job_id,
                snapshot=snapshot,
                risk_surface=item["risk_surface"] if hasattr(item["risk_surface"], "value") else RiskSurface(item["risk_surface"]),
                claim=str(item["claim"]),
                file=str(item["file"]),
                start_line=int(item["start_line"]),
                end_line=int(item["end_line"]),
                evidence_refs=tuple(str(ref) for ref in item.get("evidence_refs", ())),
                severity=str(item.get("severity", "medium")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("detector emitted an invalid finding") from exc
        if (finding.file not in allowed_files or finding.start_line < 1
                or finding.end_line < finding.start_line or not finding.claim.strip()):
            raise ValueError("detector finding violates the evidence contract")
        findings.append(finding)
    return tuple(findings)


def verify_findings(
    runner: AgentRunner,
    findings: tuple[Finding, ...],
    snapshot: PRSnapshot,
    tools: ToolRegistry,
    job_id: str,
) -> tuple[Finding, ...]:
    verified: list[Finding] = []
    for finding in findings:
        output = runner.run(
            "verifier",
            {"job_id": job_id, "snapshot": snapshot, "claim": {
                "finding_id": finding.finding_id,
                "risk_surface": finding.risk_surface.value,
                "claim": finding.claim,
                "file": finding.file,
                "start_line": finding.start_line,
                "end_line": finding.end_line,
                "evidence_refs": finding.evidence_refs,
            }},
            tools.allowed("verifier"),
        )
        status = output.get("status", "insufficient_evidence")
        if status not in {"verified", "rejected", "insufficient_evidence"}:
            status = "insufficient_evidence"
        verified.append(replace(finding, verification_status=status))
    return tuple(verified)
