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
    host: str = "127.0.0.1"
    port: int = 8787
    database_path: str = ".rsi/reviews.db"
    github_token: str = ""
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"
    redis_url: str = ""
    redis_protocol: int = 2

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_env_file()
        redis_user = os.getenv("REDIS_USERNAME", "")
        redis_password = os.getenv("REDIS_PASSWORD", "")
        auth = ""
        if redis_user or redis_password:
            auth = f"{quote(redis_user)}:{quote(redis_password)}@"
        redis_url = f"redis://{auth}{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/{os.getenv('REDIS_DB', '0')}"
        return cls(
            os.getenv("APP_HOST", "127.0.0.1"), int(os.getenv("APP_PORT", "8787")),
            os.getenv("DATABASE_PATH", ".rsi/reviews.db"), os.getenv("GITHUB_TOKEN", ""),
            os.getenv("GITHUB_WEBHOOK_SECRET", ""), os.getenv("GITHUB_API_URL", "https://api.github.com"), redis_url,
        )

    def validate_api(self) -> None:
        missing = [name for name, value in {
            "GITHUB_TOKEN": self.github_token,
            "GITHUB_WEBHOOK_SECRET": self.github_webhook_secret,
        }.items() if not value]
        if missing:
            raise ValueError("missing required configuration: " + ", ".join(missing))
