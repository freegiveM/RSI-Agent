from rsi_agent.github_comments import GitHubCommentWriter
from rsi_agent.models import Finding, PRSnapshot, RiskSurface


def test_finding_comment_contains_idempotency_marker():
    finding = Finding("f-1", "job", PRSnapshot("org/repo", 1, "b", "h", ("src/a.py",)), RiskSurface.AUTH_BOUNDARY, "missing auth", "src/a.py", 3, 3, ("trace-1",), "verified", "high")
    body = GitHubCommentWriter("token").finding_body(finding)
    assert "rsi-agent:finding=f-1" in body
    assert "/rsi accept f-1" in body
