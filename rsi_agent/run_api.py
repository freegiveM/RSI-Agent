from __future__ import annotations

import argparse
import uvicorn

from .api import create_app
from .github_api import GitHubApiReader
from .service import ReviewService
from .config import AppConfig
from .feedback import FeedbackStore
from .queue import RedisStreamQueue
from .store import TaskStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RSI-Agent FastAPI service")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config = AppConfig.from_env()
    config.validate_mode()
    config.validate_api()
    # Feedback shares the review database so it survives API restarts.
    service = ReviewService(TaskStore(config.database_path), FeedbackStore(config.database_path))
    app = create_app(service, GitHubApiReader(config.github_token, config.github_api_url), queue=RedisStreamQueue(config.redis_url, stream=config.redis_stream, group=config.redis_consumer_group), webhook_secret=config.github_webhook_secret)
    uvicorn.run(app, host=args.host or config.host, port=args.port or config.port)


if __name__ == "__main__":
    main()
