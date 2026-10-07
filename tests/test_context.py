from rsi_agent.context import ContextBudget, ReviewContextBuilder, estimate_tokens
from rsi_agent.models import PRSnapshot, RiskFeatureSet, RiskSurface


def snapshot(diff: str) -> PRSnapshot:
    return PRSnapshot("org/repo", 1, "base", "head", ("src/query.py", "docs/readme.md"), diff)


def features() -> RiskFeatureSet:
    return RiskFeatureSet(2, True, True, True, risk_surfaces=(RiskSurface.INPUT_SINK,))


def test_builder_selects_role_relevant_hunk_and_records_budget():
    diff = """diff --git a/src/query.py b/src/query.py
@@ -1,3 +1,5 @@
 def run(request):
+    query = request.args['id']
+    return db.execute(query)
diff --git a/docs/readme.md b/docs/readme.md
@@ -1 +1 @@
-old
+new
"""
    pack = ReviewContextBuilder(ContextBudget(security=80)).build(snapshot(diff), features(), "security")
    assert pack.level == "L1"
    assert "src/query.py" in pack.selected_files
    assert pack.tokens <= pack.budget
    assert pack.omitted_files == ("docs/readme.md",)


def test_empty_diff_is_summary_only():
    pack = ReviewContextBuilder().build(snapshot(""), features(), "correctness")
    assert pack.level == "L0"
    assert pack.selected_hunks == ()
    assert "PR summary" in pack.text


def test_token_estimate_is_deterministic():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
