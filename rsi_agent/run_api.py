from __future__ import annotations

import argparse
import os

import uvicorn

from .api import create_app
from .github_api import GitHubApiReader
from .service import ReviewService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RSI-Agent FastAPI service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    app = create_app(ReviewService(), GitHubApiReader(token))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
