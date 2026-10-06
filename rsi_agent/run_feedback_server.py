from __future__ import annotations

import argparse

from .http_api import serve
from .service import ReviewService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local RSI-Agent feedback service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    server = serve(ReviewService(), args.host, args.port)
    print(f"Feedback API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
