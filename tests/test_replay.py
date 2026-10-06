from pathlib import Path

from rsi_agent.memory import load_skills, select_skills
from rsi_agent.replay import ReplayFinding, evaluate, load_jsonl, shadow


def test_fixture_can_be_loaded_without_becoming_a_runtime_dependency():
    path = Path("pr_diff_100.jsonl")
    if not path.exists():
        return
    cases = load_jsonl(path, "validation")
    assert len(cases) == 80
    assert sum(bool(case.expected) for case in cases) == 32


def test_replay_reports_exact_location_metrics():
    cases = load_jsonl(Path("pr_diff_100.jsonl"), "holdout") if Path("pr_diff_100.jsonl").exists() else ()
    if not cases:
        return
    def detector(case):
        return [ReplayFinding(item["rule_id"], item["path"], item["start_line"]) for item in case.expected]
    result = evaluate(cases, detector, "p-1")
    assert result.metrics["recall"] == 1.0
    assert result.metrics["precision"] == 1.0
    assert result.metrics["false_positive_case_rate"] == 0.0
    assert shadow(cases, detector, "p-1").decision == "MEASURED"


def test_skill_selection_prefers_matching_scope(tmp_path: Path):
    root = tmp_path / ".rsi" / "skills"
    for name, text in {
        "sql": "scope: python sql\nCheck query input.",
        "auth": "scope: python auth\nCheck permissions.",
    }.items():
        path = root / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(text, encoding="utf-8")
    selected = select_skills(load_skills(tmp_path), "sql query")
    assert selected[0].name == "sql"
