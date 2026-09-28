from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


class ApplicationStage(str, Enum):
    APPLIED = "applied"
    SCREENING = "screening"
    SHORTLISTED = "shortlisted"
    INTERVIEW = "interview"
    ASSESSMENT = "assessment"
    OFFER = "offer"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class Application(SQLModel, table=True):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "candidate_id",
            "job_id",
            name="uq_applications_organization_candidate_job",
        ),
        Index(
            "ix_applications_organization_job_stage",
            "organization_id",
            "job_id",
            "stage",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_id: UUID = Field(
        foreign_key="organizations.id", index=True, nullable=False
    )
    candidate_id: UUID = Field(foreign_key="candidates.id", index=True, nullable=False)
    job_id: UUID = Field(foreign_key="jobs.id", index=True, nullable=False)
    stage: ApplicationStage = Field(
        default=ApplicationStage.APPLIED,
        sa_type=SAEnum(
            ApplicationStage,
            values_callable=lambda enum: [member.value for member in enum],
            name="application_stage",
        ),
    )
    source: str | None = None
    cover_letter: str | None = None
    screening_answers: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    applied_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_stage_change_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
