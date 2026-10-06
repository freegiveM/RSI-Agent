from rsi_agent.models import RiskSurface
from rsi_agent.routing import extract_features, route


def test_auth_change_routes_security_and_correctness_when_tests_are_missing():
    features = extract_features(("src/auth.py",), "permission check changed")
    assert RiskSurface.AUTH_BOUNDARY in features.risk_surfaces
    assert route(features) == ("security", "correctness")


def test_unknown_low_signal_still_reaches_verifier():
    features = extract_features(("docs/readme.md",), "")
    assert route(features) == ("verifier",)


def test_input_sink_routes_security():
    features = extract_features(("src/query.py",), "query(user_id)")
    assert "security" in route(features)
