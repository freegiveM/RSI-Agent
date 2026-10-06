from __future__ import annotations

import argparse
import uvicorn

from .api import create_app
from .github_api import GitHubApiReader
from .service import ReviewService
from .config import AppConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RSI-Agent FastAPI service")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config = AppConfig.from_env()
    config.validate_api()
    app = create_app(ReviewService(), GitHubApiReader(config.github_token, config.github_api_url), webhook_secret=config.github_webhook_secret)
    uvicorn.run(app, host=args.host or config.host, port=args.port or config.port)


if __name__ == "__main__":
    main()
