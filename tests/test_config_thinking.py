from types import SimpleNamespace

import pytest

from rsi_agent.config import AppConfig


def test_thinking_mode_has_explicit_strategy():
    disabled = AppConfig(deepseek_thinking="disabled")
    enabled = AppConfig(deepseek_thinking="enabled")
    assert disabled.agent_max_tokens == 4096
    assert disabled.agent_timeout_seconds == 75.0
    assert enabled.agent_max_tokens == 8192
    assert enabled.agent_timeout_seconds == 150.0
    assert enabled.provider_timeout_seconds == 120.0


def test_invalid_thinking_mode_is_rejected():
    config = AppConfig(deepseek_thinking="sometimes")
    with pytest.raises(ValueError, match="DEEPSEEK_THINKING"):
        config.validate_mode()
