from rsi_agent.context import SummaryCache


def test_summary_cache_is_versioned_and_reuses_same_head():
    cache = SummaryCache()
    calls = []

    def build():
        calls.append(1)
        return "summary"

    assert cache.get_or_build("repo", "head", "parser-v1", "policy-v1", build) == "summary"
    assert cache.get_or_build("repo", "head", "parser-v1", "policy-v1", build) == "summary"
    assert len(calls) == 1
    cache.get_or_build("repo", "new-head", "parser-v1", "policy-v1", build)
    assert len(calls) == 2
