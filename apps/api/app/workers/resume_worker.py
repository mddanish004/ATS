from typing import ClassVar
from uuid import UUID

from arq import Retry
from arq.worker import func
from sqlmodel import Session

from app.core.config import settings
from app.db.database import engine
from app.services.resume_processing import (
    TransientResumeProcessingError,
    process_resume_document,
    resume_processing_retry_delay,
)
from app.services.resume_queue import redis_settings_from_url


async def process_resume(
    ctx: dict,
    task_id: str,
    organization_id: str,
    resume_document_id: str,
) -> None:
    attempt = int(ctx.get("job_try") or 1)
    try:
        organization_uuid = UUID(organization_id)
        resume_uuid = UUID(resume_document_id)
    except ValueError:
        return

    with Session(engine) as session:
        try:
            process_resume_document(
                session,
                task_id=task_id,
                organization_id=organization_uuid,
                resume_document_id=resume_uuid,
                attempt=attempt,
            )
        except TransientResumeProcessingError as exc:
            raise Retry(defer=resume_processing_retry_delay(attempt)) from exc


class WorkerSettings:
    functions: ClassVar = [
        func(
            process_resume,
            name="process_resume",
            max_tries=settings.resume_processing_max_retries,
        )
    ]
    redis_settings: ClassVar = redis_settings_from_url(settings.redis_url)
    queue_name: ClassVar = settings.resume_processing_queue_name
    retry_jobs: ClassVar = True
    handle_signals: ClassVar = True
