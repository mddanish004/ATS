from datetime import UTC, date, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, Column, Index, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


class JobStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    ON_HOLD = "on_hold"
    CLOSED = "closed"
    ARCHIVED = "archived"


class JobApprovalStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    INTERNSHIP = "internship"


class WorkMode(str, Enum):
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    __table_args__ = (
        # One tenant's slug namespace: future public careers routes resolve
        # (organization, slug) without cross-tenant ambiguity.
        UniqueConstraint("organization_id", "slug", name="uq_jobs_organization_slug"),
        CheckConstraint(
            "salary_max IS NULL OR salary_min IS NULL OR salary_min <= salary_max",
            name="ck_jobs_salary_range",
        ),
        CheckConstraint("openings >= 1", name="ck_jobs_openings_positive"),
        Index("ix_jobs_organization_status", "organization_id", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_id: UUID = Field(
        foreign_key="organizations.id", index=True, nullable=False
    )
    title: str
    slug: str = Field(nullable=False)
    department: str | None = None
    employment_type: EmploymentType | None = Field(
        default=None,
        sa_type=SAEnum(
            EmploymentType,
            values_callable=lambda enum: [member.value for member in enum],
            name="employment_type",
        ),
    )
    location: str | None = None
    work_mode: WorkMode | None = Field(
        default=None,
        sa_type=SAEnum(
            WorkMode,
            values_callable=lambda enum: [member.value for member in enum],
            name="work_mode",
        ),
    )
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    description: str | None = None
    responsibilities: str | None = None
    requirements: str | None = None
    preferred_qualifications: str | None = None
    skills: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    hiring_manager_id: UUID | None = Field(
        default=None, foreign_key="users.id", index=True
    )
    recruiter_id: UUID | None = Field(
        default=None, foreign_key="users.id", index=True
    )
    openings: int = 1
    target_hire_date: date | None = None
    status: JobStatus = Field(
        default=JobStatus.DRAFT,
        sa_type=SAEnum(
            JobStatus,
            values_callable=lambda enum: [member.value for member in enum],
            name="job_status",
        ),
    )
    approval_status: JobApprovalStatus = Field(
        default=JobApprovalStatus.NONE,
        sa_type=SAEnum(
            JobApprovalStatus,
            values_callable=lambda enum: [member.value for member in enum],
            name="job_approval_status",
        ),
    )
    published_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
