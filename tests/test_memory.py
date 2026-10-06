from pathlib import Path

from rsi_agent.memory import MemoryEngine, load_skills


def test_fts_search_ranks_and_limits_history():
    engine = MemoryEngine()
    engine.add("m1", "feedback", "SQL input", "parameterized query prevents SQL injection", tier=1)
    engine.add("m2", "feedback", "Concurrency", "review transaction retry behavior", tier=2)
    assert engine.search("SQL injection", limit=1)[0].memory_id == "m1"
    assert engine.search("transaction", max_tier=1) == ()


def test_repository_memory_is_progressively_disclosed(tmp_path: Path):
    memory = tmp_path / ".rsi" / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Keep auth checks explicit.", encoding="utf-8")
    (memory / "auth.md").write_text("auth boundary tenant permission", encoding="utf-8")
    (memory / "database.md").write_text("SQL migration conventions", encoding="utf-8")
    hits = MemoryEngine(workspace=tmp_path).load_repository("auth permission", max_chars=200)
    assert [hit.title for hit in hits] == ["MEMORY", "auth"]


def test_skills_are_loaded_as_versioned_files(tmp_path: Path):
    skill = tmp_path / ".rsi" / "skills" / "security-input"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("scope: python sql\nCheck input sinks.", encoding="utf-8")
    skills = load_skills(tmp_path)
    assert skills[0].name == "security-input"
    assert skills[0].triggers == ("python sql",)
