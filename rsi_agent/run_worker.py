from __future__ import annotations

import argparse

from .agents import DeterministicAgentRunner, ToolRegistry
from .config import AppConfig
from .queue import RedisStreamQueue
from .service import ReviewService
from .worker import ReviewWorker
from .store import TaskStore
from .agentscope_runner import AgentScopeRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RSI-Agent Redis Stream worker")
    parser.add_argument("--consumer", default="worker-1")
    args = parser.parse_args()
    config = AppConfig.from_env()
    config.validate_mode()
    queue = RedisStreamQueue(config.redis_url, stream=config.redis_stream, group=config.redis_consumer_group, consumer=args.consumer)
    service = ReviewService(TaskStore(config.database_path))
    runner = AgentScopeRunner.from_deepseek_env(config) if config.deepseek_api_key else DeterministicAgentRunner()
    worker = ReviewWorker(service, runner, ToolRegistry())
    print(f"Worker listening on Redis stream as {args.consumer}")
    while True:
        for message in queue.consume():
            try:
                worker.process(message.job_id)
                queue.ack(message.message_id)
            except Exception as exc:
                print(f"job {message.job_id} failed: {exc}")


if __name__ == "__main__":
    main()
