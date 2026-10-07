from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


def load_env_file(path: str | Path = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class AppConfig:
    deployment_mode: str = "local"
    host: str = "127.0.0.1"
    port: int = 8787
    database_path: str = ".rsi/reviews.db"
    github_token: str = ""
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"
    redis_url: str = ""
    redis_protocol: int = 2
    redis_stream: str = "rsi:review-jobs"
    redis_consumer_group: str = "rsi-workers"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_max_tokens: int = 4096
    deepseek_temperature: float = 0.0
    deepseek_timeout_seconds: float = 60.0
    deepseek_thinking: str = "disabled"

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_env_file()
        redis_url = os.getenv("REDIS_URL", "")
        if not redis_url:
            redis_user = os.getenv("REDIS_USERNAME", "")
            redis_password = os.getenv("REDIS_PASSWORD", "")
            auth = f"{quote(redis_user)}:{quote(redis_password)}@" if redis_user or redis_password else ""
            redis_url = f"redis://{auth}{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/{os.getenv('REDIS_DB', '0')}"
        return cls(
            os.getenv("DEPLOYMENT_MODE", "local"), os.getenv("APP_HOST", "127.0.0.1"), int(os.getenv("APP_PORT", "8787")),
            os.getenv("DATABASE_PATH", ".rsi/reviews.db"), os.getenv("GITHUB_TOKEN", ""),
            os.getenv("GITHUB_WEBHOOK_SECRET", ""), os.getenv("GITHUB_API_URL", "https://api.github.com"), redis_url,
            2, os.getenv("REDIS_STREAM", "rsi:review-jobs"), os.getenv("REDIS_CONSUMER_GROUP", "rsi-workers"),
            os.getenv("DEEPSEEK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"), int(os.getenv("DEEPSEEK_MAX_TOKENS", "4096")),
            float(os.getenv("DEEPSEEK_TEMPERATURE", "0")),
            float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60")),
            os.getenv("DEEPSEEK_THINKING", "disabled"),
        )

    def validate_api(self) -> None:
        missing = [name for name, value in {
            "GITHUB_TOKEN": self.github_token,
            "GITHUB_WEBHOOK_SECRET": self.github_webhook_secret,
        }.items() if not value]
        if missing:
            raise ValueError("missing required configuration: " + ", ".join(missing))

    def validate_mode(self) -> None:
        if self.deepseek_thinking not in {"disabled", "enabled"}:
            raise ValueError("DEEPSEEK_THINKING must be disabled or enabled")
        if self.deployment_mode not in {"local", "remote"}:
            raise ValueError("DEPLOYMENT_MODE must be local or remote")
        if not self.redis_url:
            raise ValueError("REDIS_URL must not be empty")

    @property
    def is_deep_reasoning(self) -> bool:
        return self.deepseek_thinking == "enabled"

    @property
    def agent_timeout_seconds(self) -> float:
        return max(self.deepseek_timeout_seconds + 15.0, 150.0 if self.is_deep_reasoning else 75.0)

    @property
    def provider_timeout_seconds(self) -> float:
        return max(self.deepseek_timeout_seconds, 120.0) if self.is_deep_reasoning else self.deepseek_timeout_seconds

    @property
    def agent_max_tokens(self) -> int:
        return max(self.deepseek_max_tokens, 8192 if self.is_deep_reasoning else 4096)
