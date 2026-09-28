import inspect
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID

from arq.connections import RedisSettings, create_pool

from app.core.config import settings


class ResumeQueueError(Exception):
    pass


class ResumeProcessingQueue(Protocol):
    async def enqueue_resume_processing(
        self,
        *,
        organization_id: UUID,
        resume_document_id: UUID,
    ) -> str: ...


def resume_processing_task_id(
    *,
    organization_id: UUID,
    resume_document_id: UUID,
) -> str:
    return f"resume-processing:{organization_id}:{resume_document_id}"


def redis_settings_from_url(redis_url: str) -> RedisSettings:
    parsed = urlparse(redis_url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ResumeQueueError("Unsupported Redis URL scheme")
    database = int(parsed.path.lstrip("/") or "0")
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=database,
        password=parsed.password,
        ssl=parsed.scheme == "rediss",
    )


@dataclass(frozen=True)
class ArqResumeProcessingQueue:
    redis_url: str
    queue_name: str

    async def enqueue_resume_processing(
        self,
        *,
        organization_id: UUID,
        resume_document_id: UUID,
    ) -> str:
        task_id = resume_processing_task_id(
            organization_id=organization_id,
            resume_document_id=resume_document_id,
        )
        try:
            redis = await create_pool(redis_settings_from_url(self.redis_url))
            try:
                await redis.enqueue_job(
                    "process_resume",
                    task_id,
                    str(organization_id),
                    str(resume_document_id),
                    _job_id=task_id,
                    _queue_name=self.queue_name,
                )
            finally:
                close_result = redis.close()
                if inspect.isawaitable(close_result):
                    await close_result
        except Exception as exc:
            raise ResumeQueueError("Resume processing queue is unavailable") from exc
        return task_id


def get_resume_processing_queue() -> ResumeProcessingQueue:
    return ArqResumeProcessingQueue(
        redis_url=settings.redis_url,
        queue_name=settings.resume_processing_queue_name,
    )
