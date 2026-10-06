from pathlib import Path

from rsi_agent.config import AppConfig, load_env_file


def test_env_template_loads_without_overwriting_process_env(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("APP_PORT=9191\nREDIS_PASSWORD=secret value\n", encoding="utf-8")
    monkeypatch.delenv("APP_PORT", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    import os
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        config = AppConfig.from_env()
    finally:
        os.chdir(original)
    assert config.port == 9191
    assert "secret%20value" in config.redis_url
