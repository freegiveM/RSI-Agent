from __future__ import annotations

import argparse

from .agents import DeterministicAgentRunner, ToolRegistry
from .config import AppConfig
from .queue import RedisStreamQueue
from .service import ReviewService
from .worker import ReviewWorker
from .worker import WorkerConfig
from .store import TaskStore
from .agentscope_runner import AgentScopeRunner
from .github_comments import GitHubCommentWriter
from .github_api import GitHubApiReader


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
        config=WorkerConfig(
            timeout_seconds=config.agent_timeout_seconds,
            max_tokens=config.agent_max_tokens,
            provider_timeout_seconds=config.provider_timeout_seconds,
            thinking_mode=config.deepseek_thinking,
        ),
        comment_writer=GitHubCommentWriter(config.github_token, config.github_api_url),
        head_reader=GitHubApiReader(config.github_token, config.github_api_url) if config.github_token else None,
    )
    print(f"Worker listening on Redis stream as {args.consumer}")
    while True:
        pending = queue.claim_pending()
        messages = pending + queue.consume()
        for message in messages:
            try:
                try:
                    queue_event = "claimed" if message in pending else "consumed"
                    service.store.record_audit_event(message.job_id, "queue", "stream", queue_event, "running", trace_id=message.message_id, metadata={"message_id": message.message_id, "consumer": args.consumer})
                except Exception:
                    pass
                attempts = queue.delivery_count(message.message_id)
                if attempts < 2:
                    try:
                        if service.store.get_job(message.job_id).status.value == "FAILED":
                            service.store.retry_failed(message.job_id)
                    except KeyError:
                        pass
                worker.process(message.job_id)
                queue.ack(message.message_id)
                try:
                    service.store.record_audit_event(message.job_id, "queue", "stream", "acknowledged", "completed", trace_id=message.message_id, metadata={"message_id": message.message_id})
                except Exception:
                    pass
            except Exception as exc:
                attempts = queue.delivery_count(message.message_id)
                if attempts >= 2:
                    dead_id = queue.dead_letter(message, worker._failure_class(exc), str(exc), attempts)
                    queue.ack(message.message_id)
                    try:
                        service.store.record_audit_event(message.job_id, "queue", "dead_letter", "dead_lettered", "failed", trace_id=message.message_id, error_class="QUEUE_REDELIVERY_EXHAUSTED", metadata={"message_id": message.message_id, "dead_id": dead_id, "attempts": attempts, "failure_class": worker._failure_class(exc)})
                    except Exception:
                        pass
                    print(f"job {message.job_id} moved to dead letter {dead_id}: {exc}")
                else:
                    try:
                        service.store.record_audit_event(message.job_id, "queue", "retry", "retry_scheduled", "waiting", trace_id=message.message_id, error_class=worker._failure_class(exc), metadata={"message_id": message.message_id, "attempts": attempts})
                    except Exception:
                        pass
                    print(f"job {message.job_id} deferred after attempt {attempts}: {exc}")


if __name__ == "__main__":
    main()
