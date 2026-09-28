import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    ResumeDocument,
    ResumeProcessingStatus,
    can_transition_resume_processing,
)

logger = logging.getLogger(__name__)


class ResumeProcessingError(Exception):
    pass


class PermanentResumeProcessingError(ResumeProcessingError):
    pass


class TransientResumeProcessingError(ResumeProcessingError):
    pass


class InvalidResumeProcessingTransition(ResumeProcessingError):
    pass


@dataclass(frozen=True)
class ResumeProcessingOutcome:
    status: ResumeProcessingStatus
    error_message: str | None = None


class ResumeProcessor(Protocol):
    def process(self, resume: ResumeDocument) -> ResumeProcessingOutcome: ...


class PlaceholderResumeProcessor:
    def process(self, resume: ResumeDocument) -> ResumeProcessingOutcome:
        return ResumeProcessingOutcome(
            status=ResumeProcessingStatus.NEEDS_REVIEW,
            error_message="Resume parsing is not implemented yet",
        )


def _truncate_error(error: str) -> str:
    return error[:1000]


def _set_status(
    resume: ResumeDocument,
    status: ResumeProcessingStatus,
    *,
    error_message: str | None = None,
) -> None:
    if resume.status == status:
        resume.error_message = error_message
        resume.updated_at = datetime.now(UTC)
        return
    if not can_transition_resume_processing(resume.status, status):
        raise InvalidResumeProcessingTransition(
            f"Cannot move resume processing from {resume.status.value} to {status.value}"
        )
    resume.status = status
    resume.error_message = error_message
    resume.updated_at = datetime.now(UTC)


def mark_resume_queued(
    session: Session,
    *,
    organization_id: UUID,
    resume_document_id: UUID,
) -> ResumeDocument:
    resume = session.exec(
        select(ResumeDocument).where(
            ResumeDocument.id == resume_document_id,
            ResumeDocument.organization_id == organization_id,
        )
    ).first()
    if resume is None:
        raise PermanentResumeProcessingError("Resume document not found")
    _set_status(resume, ResumeProcessingStatus.QUEUED)
    session.add(resume)
    return resume


def record_resume_queue_failure(
    session: Session,
    *,
    organization_id: UUID,
    resume_document_id: UUID,
    error_message: str,
) -> None:
    resume = session.exec(
        select(ResumeDocument).where(
            ResumeDocument.id == resume_document_id,
            ResumeDocument.organization_id == organization_id,
        )
    ).first()
    if resume is None:
        return
    resume.error_message = _truncate_error(error_message)
    resume.updated_at = datetime.now(UTC)
    session.add(resume)


def resume_processing_retry_delay(attempt: int) -> int:
    delay = settings.resume_processing_retry_base_seconds * (2 ** max(attempt - 1, 0))
    return min(delay, settings.resume_processing_retry_max_seconds)


def process_resume_document(
    session: Session,
    *,
    task_id: str,
    organization_id: UUID,
    resume_document_id: UUID,
    attempt: int = 1,
    processor: ResumeProcessor | None = None,
) -> ResumeProcessingStatus | None:
    resume = session.exec(
        select(ResumeDocument).where(
            ResumeDocument.id == resume_document_id,
            ResumeDocument.organization_id == organization_id,
        )
    ).first()
    if resume is None:
        logger.info(
            "resume processing skipped task_id=%s organization_id=%s resume_id=%s",
            task_id,
            organization_id,
            resume_document_id,
        )
        return None
    if resume.status in {
        ResumeProcessingStatus.COMPLETED,
        ResumeProcessingStatus.FAILED,
        ResumeProcessingStatus.NEEDS_REVIEW,
    }:
        logger.info(
            "resume processing idempotent skip task_id=%s organization_id=%s "
            "resume_id=%s status=%s",
            task_id,
            organization_id,
            resume_document_id,
            resume.status.value,
        )
        return resume.status

    logger.info(
        "resume processing started task_id=%s organization_id=%s resume_id=%s "
        "attempt=%s",
        task_id,
        organization_id,
        resume_document_id,
        attempt,
    )
    _set_status(resume, ResumeProcessingStatus.PROCESSING)
    session.add(resume)
    session.commit()

    active_processor = processor or PlaceholderResumeProcessor()
    try:
        outcome = active_processor.process(resume)
    except TransientResumeProcessingError as exc:
        if attempt >= settings.resume_processing_max_retries:
            _set_status(
                resume,
                ResumeProcessingStatus.FAILED,
                error_message=_truncate_error(str(exc)),
            )
            session.add(resume)
            session.commit()
            logger.warning(
                "resume processing failed after retries task_id=%s "
                "organization_id=%s resume_id=%s",
                task_id,
                organization_id,
                resume_document_id,
            )
            return ResumeProcessingStatus.FAILED
        resume.error_message = _truncate_error(str(exc))
        resume.updated_at = datetime.now(UTC)
        session.add(resume)
        session.commit()
        raise
    except PermanentResumeProcessingError as exc:
        _set_status(
            resume,
            ResumeProcessingStatus.FAILED,
            error_message=_truncate_error(str(exc)),
        )
        session.add(resume)
        session.commit()
        logger.info(
            "resume processing permanent failure task_id=%s organization_id=%s "
            "resume_id=%s",
            task_id,
            organization_id,
            resume_document_id,
        )
        return ResumeProcessingStatus.FAILED

    _set_status(
        resume,
        outcome.status,
        error_message=(
            _truncate_error(outcome.error_message) if outcome.error_message else None
        ),
    )
    session.add(resume)
    session.commit()
    logger.info(
        "resume processing finished task_id=%s organization_id=%s resume_id=%s "
        "status=%s",
        task_id,
        organization_id,
        resume_document_id,
        resume.status.value,
    )
    return resume.status
