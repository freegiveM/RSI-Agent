from __future__ import annotations

from .models import RiskFeatureSet, RiskSurface


def extract_features(files: tuple[str, ...], diff: str = "") -> RiskFeatureSet:
    text = f"{' '.join(files)}\n{diff}".lower()
    warnings: list[str] = []
    surfaces: set[RiskSurface] = set()
    sensitive = any(token in text for token in ("sql", "query", "exec", "command", "shell", "http", "file"))
    if sensitive:
        surfaces.add(RiskSurface.INPUT_SINK)
        warnings.append("sensitive_sink")
    if any(token in text for token in ("auth", "permission", "tenant", "role", "owner")):
        surfaces.add(RiskSurface.AUTH_BOUNDARY)
    if any(token in text for token in ("deserialize", "parser", "yaml", "pickle", "json")):
        surfaces.add(RiskSurface.PARSER_SERIALIZATION)
    if any(token in text for token in ("lock", "transaction", "retry", "async", "cache")):
        surfaces.add(RiskSurface.STATE_CONCURRENCY)
    if any(token in text for token in ("requirements", "package", "dockerfile", ".env", "config")):
        surfaces.add(RiskSurface.DEPENDENCY_CONFIG)
    test_gap = not any("test" in name for name in files)
    executable_change = any(name.endswith((".py", ".js", ".ts", ".java", ".go", ".rs")) for name in files)
    return RiskFeatureSet(len(files), executable_change, sensitive, test_gap, tuple(warnings), tuple(sorted(surfaces, key=lambda item: item.value)))


def route(features: RiskFeatureSet) -> tuple[str, ...]:
    selected: list[str] = []
    if features.risk_surfaces:
        selected.append("security" if any(surface in features.risk_surfaces for surface in (
            RiskSurface.INPUT_SINK, RiskSurface.AUTH_BOUNDARY, RiskSurface.PARSER_SERIALIZATION,
        )) else "correctness")
    # A missing test file alone is not a correctness risk. It only raises
    # priority when the change already touches executable code or behavior.
    if (features.test_gap and features.executable_change) or features.changed_surface > 8:
        if "correctness" not in selected:
            selected.append("correctness")
    if not selected:
        selected.append("verifier")
    return tuple(selected)
