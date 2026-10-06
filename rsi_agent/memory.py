from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemoryHit:
    memory_id: str
    source: str
    title: str
    body: str
    score: float
    tier: int


@dataclass(frozen=True)
class Skill:
    name: str
    path: str
    content: str
    triggers: tuple[str, ...]


class MemoryEngine:
    """SQLite history plus auditable repository files; no vector index required."""

    def __init__(self, db_path: str | Path = ":memory:", workspace: str | Path | None = None) -> None:
        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row
        self.workspace = Path(workspace) if workspace else None
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                tier INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                memory_id UNINDEXED, title, body
            );
            """
        )
        self.connection.commit()

    def add(self, memory_id: str, source: str, title: str, body: str, tier: int = 1) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO memories(memory_id, source, title, body, tier) VALUES (?, ?, ?, ?, ?)",
            (memory_id, source, title, body, tier),
        )
        self.connection.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
        self.connection.execute("INSERT INTO memories_fts(memory_id, title, body) VALUES (?, ?, ?)", (memory_id, title, body))
        self.connection.commit()

    def search(self, query: str, limit: int = 5, max_tier: int = 2) -> tuple[MemoryHit, ...]:
        terms = tuple(re.findall(r"[\w.-]+", query.lower()))
        if not terms:
            return ()
        match = " OR ".join(terms)
        rows = self.connection.execute(
            """
            SELECT m.*, bm25(memories_fts) AS score
            FROM memories_fts JOIN memories m ON m.memory_id=memories_fts.memory_id
            WHERE memories_fts MATCH ? AND m.active=1 AND m.tier<=?
            ORDER BY score LIMIT ?
            """,
            (match, max_tier, limit),
        ).fetchall()
        return tuple(MemoryHit(row["memory_id"], row["source"], row["title"], row["body"], float(row["score"]), row["tier"]) for row in rows)

    def load_repository(self, query: str, max_chars: int = 6000) -> tuple[MemoryHit, ...]:
        if not self.workspace:
            return ()
        root = self.workspace / ".rsi"
        paths = [root / "memory" / "MEMORY.md"]
        paths.extend(sorted((root / "memory").glob("**/*.md")) if (root / "memory").exists() else [])
        hits: list[MemoryHit] = []
        remaining = max_chars
        query_terms = {term.lower() for term in re.findall(r"[\w.-]+", query)}
        seen: set[Path] = set()
        for path in paths:
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            content = path.read_text(encoding="utf-8")
            if path.name != "MEMORY.md" and query_terms and not query_terms.intersection(content.lower().split()):
                continue
            content = content[:remaining]
            if not content:
                break
            hits.append(MemoryHit(str(path), "repository", path.stem, content, 0.0, 0 if path.name == "MEMORY.md" else 1))
            remaining -= len(content)
            if remaining <= 0:
                break
        return tuple(hits)


def load_skills(workspace: str | Path) -> tuple[Skill, ...]:
    root = Path(workspace) / ".rsi" / "skills"
    if not root.exists():
        return ()
    skills: list[Skill] = []
    for path in sorted(root.glob("*/SKILL.md")):
        content = path.read_text(encoding="utf-8")
        triggers = tuple(sorted(set(re.findall(r"(?:trigger|scope)\s*:\s*([^\n]+)", content, re.I))))
        skills.append(Skill(path.parent.name, str(path), content, triggers))
    return tuple(skills)


def select_skills(skills: tuple[Skill, ...], query: str, limit: int = 2) -> tuple[Skill, ...]:
    terms = set(re.findall(r"[\w.-]+", query.lower()))
    ranked = []
    for skill in skills:
        haystack = f"{skill.name} {' '.join(skill.triggers)} {skill.content}".lower()
        score = sum(term in haystack for term in terms)
        if score:
            ranked.append((score, skill))
    ranked.sort(key=lambda item: (-item[0], item[1].name))
    return tuple(skill for _, skill in ranked[:limit])
