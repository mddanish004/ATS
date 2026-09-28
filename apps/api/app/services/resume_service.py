from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import settings
from app.models import ResumeDocument
from app.services.storage_service import PrivateStorage, StorageError


def resume_storage_key(
    *,
    organization_id: UUID,
    candidate_id: UUID,
    resume_id: UUID,
    extension: str = "pdf",
) -> str:
    return (
        f"org/{organization_id}/candidates/{candidate_id}/resumes/"
        f"{resume_id}/original.{extension}"
    )


def authorized_resume_signed_url(
    *,
    session: Session,
    storage: PrivateStorage,
    organization_id: UUID,
    resume_id: UUID,
    expires_in: int | None = None,
) -> str:
    resume = session.exec(
        select(ResumeDocument).where(
            ResumeDocument.id == resume_id,
            ResumeDocument.organization_id == organization_id,
        )
    ).first()
    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resume document not found",
        )

    try:
        return storage.generate_signed_url(
            resume.storage_key,
            expires_in=expires_in or settings.resume_signed_url_expire_seconds,
        )
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Resume document is temporarily unavailable",
        ) from exc
