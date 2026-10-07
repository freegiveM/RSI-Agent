"""Safe parsing of GitHub unified diffs for the review console.

The parser is deliberately independent from the agents.  Diff text is treated
as untrusted data and is never executed or interpolated into an HTML response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_FILE_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$"
)


@dataclass(frozen=True)
class DiffLine:
    kind: str
    text: str
    old_line: int | None
    new_line: int | None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "text": self.text,
            "old": self.old_line,
            "new": self.new_line,
        }


@dataclass(frozen=True)
class DiffHunk:
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str
    lines: tuple[DiffLine, ...]
    finding_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "header": self.header,
            "old_start": self.old_start,
            "old_count": self.old_count,
            "new_start": self.new_start,
            "new_count": self.new_count,
            "section": self.section.strip(),
            "lines": [line.as_dict() for line in self.lines],
            "finding_ids": list(self.finding_ids),
        }


@dataclass(frozen=True)
class DiffFile:
    path: str
    old_path: str | None
    new_path: str | None
    status: str
    parse_status: str
    hunks: tuple[DiffHunk, ...] = ()
    finding_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "old_path": self.old_path,
            "new_path": self.new_path,
            "status": self.status,
            "parse_status": self.parse_status,
            "hunks": [hunk.as_dict() for hunk in self.hunks],
            "finding_ids": list(self.finding_ids),
        }


def _path(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value == "/dev/null":
        return None
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def _file_status(old_path: str | None, new_path: str | None, flags: set[str]) -> str:
    if "missing" in flags:
        return "unknown"
    if "binary" in flags:
        return "binary"
    if "rename" in flags or (old_path and new_path and old_path != new_path):
        return "renamed"
    if old_path is None and new_path is not None:
        return "added"
    if new_path is None and old_path is not None:
        return "deleted"
    return "modified"


def parse_unified_diff(diff: str, changed_files: tuple[str, ...] = ()) -> tuple[DiffFile, ...]:
    """Parse a Git unified diff without failing the review on unsupported input."""

    if not diff:
        return tuple(
            DiffFile(path=file, old_path=file, new_path=file, status="unknown", parse_status="empty")
            for file in changed_files
        )

    records: list[dict] = []
    current: dict | None = None
    hunk: dict | None = None

    def finish_hunk() -> None:
        nonlocal hunk
        if current is not None and hunk is not None:
            current["hunks"].append(
                DiffHunk(
                    header=hunk["header"], old_start=hunk["old_start"], old_count=hunk["old_count"],
                    new_start=hunk["new_start"], new_count=hunk["new_count"], section=hunk["section"],
                    lines=tuple(hunk["lines"]),
                )
            )
        hunk = None

    def finish_file() -> None:
        nonlocal current
        finish_hunk()
        if current is not None:
            records.append(current)
        current = None

    for raw in diff.splitlines():
        match = _FILE_HEADER.match(raw)
        if match:
            finish_file()
            current = {
                "old_path": _path(match.group(1)), "new_path": _path(match.group(2)),
                "flags": set(), "hunks": [],
            }
            continue
        if current is None:
            continue
        if raw.startswith("rename from "):
            current["old_path"] = _path(raw.removeprefix("rename from "))
            current["flags"].add("rename")
            continue
        if raw.startswith("rename to "):
            current["new_path"] = _path(raw.removeprefix("rename to "))
            current["flags"].add("rename")
            continue
        if raw.startswith("new file mode "):
            current["old_path"] = None
            continue
        if raw.startswith("deleted file mode "):
            current["new_path"] = None
            continue
        if raw.startswith("Binary files "):
            current["flags"].add("binary")
            continue
        if raw.startswith("--- "):
            current["old_path"] = _path(raw[4:].split("\t", 1)[0])
            continue
        if raw.startswith("+++ "):
            current["new_path"] = _path(raw[4:].split("\t", 1)[0])
            continue
        hmatch = _HUNK_HEADER.match(raw)
        if hmatch:
            finish_hunk()
            hunk = {
                "header": raw,
                "old_start": int(hmatch.group("old_start")),
                "old_count": int(hmatch.group("old_count") or 1),
                "new_start": int(hmatch.group("new_start")),
                "new_count": int(hmatch.group("new_count") or 1),
                "section": hmatch.group("section"),
                "lines": [],
                "old_cursor": int(hmatch.group("old_start")),
                "new_cursor": int(hmatch.group("new_start")),
            }
            continue
        if hunk is None:
            continue
        if raw.startswith("\\ No newline at end of file"):
            hunk["lines"].append(DiffLine("meta", raw, None, None))
            continue
        if raw.startswith("+"):
            hunk["lines"].append(DiffLine("addition", raw, None, hunk["new_cursor"]))
            hunk["new_cursor"] += 1
        elif raw.startswith("-"):
            hunk["lines"].append(DiffLine("deletion", raw, hunk["old_cursor"], None))
            hunk["old_cursor"] += 1
        elif raw.startswith(" "):
            hunk["lines"].append(DiffLine("context", raw, hunk["old_cursor"], hunk["new_cursor"]))
            hunk["old_cursor"] += 1
            hunk["new_cursor"] += 1
        else:
            hunk["lines"].append(DiffLine("meta", raw, None, None))

    finish_file()
    known = {record["new_path"] or record["old_path"] for record in records}
    for file in changed_files:
        if file not in known:
            records.append({"old_path": file, "new_path": file, "flags": {"missing"}, "hunks": []})

    result: list[DiffFile] = []
    for record in records:
        old_path, new_path = record["old_path"], record["new_path"]
        path = new_path or old_path or "<unknown>"
        hunks = tuple(record["hunks"])
        parse_status = "ok" if hunks else ("binary" if "binary" in record["flags"] else "no_hunks")
        if path == "<unknown>":
            parse_status = "unsupported"
        result.append(DiffFile(path, old_path, new_path, _file_status(old_path, new_path, record["flags"]), parse_status, hunks))
    return tuple(result)


def attach_findings(files: tuple[DiffFile, ...], findings: tuple[object, ...]) -> tuple[DiffFile, ...]:
    """Annotate parsed files/hunks with Finding IDs using new-file line numbers."""

    output: list[DiffFile] = []
    for file in files:
        related = [finding for finding in findings if getattr(finding, "file", None) == file.path]
        ids = tuple(str(getattr(finding, "finding_id")) for finding in related)
        hunks: list[DiffHunk] = []
        for hunk in file.hunks:
            hunk_ids = tuple(
                str(getattr(finding, "finding_id"))
                for finding in related
                if _line_overlaps(hunk.new_start, hunk.new_count, getattr(finding, "start_line", 0), getattr(finding, "end_line", 0))
            )
            hunks.append(DiffHunk(hunk.header, hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count, hunk.section, hunk.lines, hunk_ids))
        output.append(DiffFile(file.path, file.old_path, file.new_path, file.status, file.parse_status, tuple(hunks), ids))
    return tuple(output)


def _line_overlaps(start: int, count: int, finding_start: int, finding_end: int) -> bool:
    if count == 0:
        return finding_start == start
    end = start + count - 1
    return start <= finding_end and finding_start <= end
