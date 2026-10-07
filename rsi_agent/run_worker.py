from __future__ import annotations

import argparse

from .agents import DeterministicAgentRunner, ToolRegistry
from .config import AppConfig
from .queue import RedisStreamQueue
from .service import ReviewService
from .worker import ReviewWorker
from .store import TaskStore
from .agentscope_runner import AgentScopeRunner
from .github_comments import GitHubCommentWriter


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RSI-Agent Redis Stream worker")
    parser.add_argument("--consumer", default="worker-1")
    args = parser.parse_args()
    config = AppConfig.from_env()
    config.validate_mode()
    queue = RedisStreamQueue(config.redis_url, stream=config.redis_stream, group=config.redis_consumer_group, consumer=args.consumer)
    service = ReviewService(TaskStore(config.database_path))
    runner = AgentScopeRunner.from_deepseek_env(config) if config.deepseek_api_key else DeterministicAgentRunner()
    worker = ReviewWorker(
        service,
        runner,
        ToolRegistry(),
        config=__import__("rsi_agent.worker", fromlist=["WorkerConfig"]).WorkerConfig(
            timeout_seconds=config.agent_timeout_seconds,
        ),
        comment_writer=GitHubCommentWriter(config.github_token, config.github_api_url),
    )
    print(f"Worker listening on Redis stream as {args.consumer}")
    while True:
        messages = queue.claim_pending() + queue.consume()
        for message in messages:
            try:
                attempts = queue.delivery_count(message.message_id)
                if attempts < 2:
                    try:
                        if service.store.get_job(message.job_id).status.value == "FAILED":
                            service.store.retry_failed(message.job_id)
                    except KeyError:
                        pass
                worker.process(message.job_id)
                queue.ack(message.message_id)
            except Exception as exc:
                attempts = queue.delivery_count(message.message_id)
                if attempts >= 2:
                    dead_id = queue.dead_letter(message, worker._failure_class(exc), str(exc), attempts)
                    queue.ack(message.message_id)
                    print(f"job {message.job_id} moved to dead letter {dead_id}: {exc}")
                else:
                    print(f"job {message.job_id} deferred after attempt {attempts}: {exc}")


if __name__ == "__main__":
    main()
