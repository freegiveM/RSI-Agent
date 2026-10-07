from rsi_agent.diff import attach_findings, parse_unified_diff
from rsi_agent.models import Finding, PRSnapshot, RiskSurface


def test_parse_hunk_tracks_old_and_new_lines():
    diff = """diff --git a/src/query.py b/src/query.py
--- a/src/query.py
+++ b/src/query.py
@@ -1,3 +1,4 @@ def query():
 keep()
-old()
+new()
+audit()
 end()
"""
    files = parse_unified_diff(diff, ("src/query.py",))
    assert files[0].status == "modified"
    assert files[0].parse_status == "ok"
    lines = files[0].hunks[0].lines
    assert (lines[1].kind, lines[1].old_line, lines[1].new_line) == ("deletion", 2, None)
    assert (lines[2].kind, lines[2].old_line, lines[2].new_line) == ("addition", None, 2)
    assert (lines[3].kind, lines[3].new_line) == ("addition", 3)


def test_parse_added_deleted_binary_and_empty_files():
    diff = """diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+print('x')
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
diff --git a/image.png b/image.png
Binary files /dev/null and b/image.png differ
"""
    files = parse_unified_diff(diff, ("new.py", "old.py", "image.png", "README.md"))
    statuses = {file.path: (file.status, file.parse_status) for file in files}
    assert statuses["new.py"] == ("added", "ok")
    assert statuses["old.py"][0] == "deleted"
    assert statuses["image.png"] == ("binary", "binary")
    assert statuses["README.md"] == ("unknown", "no_hunks")


def test_finding_is_attached_to_new_line_hunk():
    diff = """diff --git a/src/query.py b/src/query.py
--- a/src/query.py
+++ b/src/query.py
@@ -1 +1,2 @@
 safe()
+unsafe(request)
"""
    parsed = parse_unified_diff(diff, ("src/query.py",))
    finding = Finding("f-1", "job", PRSnapshot("repo", 1, "b", "h", ("src/query.py",)), RiskSurface.INPUT_SINK, "unsafe", "src/query.py", 2, 2, ())
    linked = attach_findings(parsed, (finding,))
    assert linked[0].finding_ids == ("f-1",)
    assert linked[0].hunks[0].finding_ids == ("f-1",)
