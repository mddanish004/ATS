from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


class ResumeProcessingStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


RESUME_PROCESSING_TRANSITIONS: dict[
    ResumeProcessingStatus, set[ResumeProcessingStatus]
] = {
    ResumeProcessingStatus.UPLOADED: {ResumeProcessingStatus.QUEUED},
    ResumeProcessingStatus.QUEUED: {
        ResumeProcessingStatus.PROCESSING,
        ResumeProcessingStatus.FAILED,
    },
    ResumeProcessingStatus.PROCESSING: {
        ResumeProcessingStatus.PROCESSING,
        ResumeProcessingStatus.COMPLETED,
        ResumeProcessingStatus.FAILED,
        ResumeProcessingStatus.NEEDS_REVIEW,
    },
    ResumeProcessingStatus.COMPLETED: set(),
    ResumeProcessingStatus.FAILED: set(),
    ResumeProcessingStatus.NEEDS_REVIEW: set(),
}


def can_transition_resume_processing(
    current: ResumeProcessingStatus,
    target: ResumeProcessingStatus,
) -> bool:
    return target in RESUME_PROCESSING_TRANSITIONS[current]


class ResumeDocument(SQLModel, table=True):
    __tablename__ = "resume_documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_id: UUID = Field(
        foreign_key="organizations.id", index=True, nullable=False
    )
    candidate_id: UUID = Field(foreign_key="candidates.id", index=True, nullable=False)
    application_id: UUID = Field(
        foreign_key="applications.id", index=True, nullable=False
    )
    storage_key: str = Field(nullable=False, unique=True)
    original_filename: str
    content_type: str
    size_bytes: int
    status: ResumeProcessingStatus = Field(
        default=ResumeProcessingStatus.UPLOADED,
        sa_type=SAEnum(
            ResumeProcessingStatus,
            values_callable=lambda enum: [member.value for member in enum],
            name="resume_processing_status",
        ),
    )
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
