from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import StringConstraints, field_validator, model_validator
from sqlmodel import SQLModel

from app.models.job import EmploymentType, JobApprovalStatus, JobStatus, WorkMode
from app.schemas.organization import CURRENCY_PATTERN

TitleField = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]

ShortTextField = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=200),
]

LongTextField = Annotated[
    str | None,
    StringConstraints(strip_whitespace=True, max_length=20000),
]

SlugField = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]


class JobCreate(SQLModel):
    title: TitleField
    slug: SlugField
    department: ShortTextField = None
    employment_type: EmploymentType | None = None
    location: ShortTextField = None
    work_mode: WorkMode | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: Annotated[str | None, StringConstraints(min_length=3, max_length=3)] = (
        None
    )
    description: LongTextField = None
    responsibilities: LongTextField = None
    requirements: LongTextField = None
    preferred_qualifications: LongTextField = None
    skills: list[str] = []
    hiring_manager_id: UUID | None = None
    recruiter_id: UUID | None = None
    openings: int = 1
    target_hire_date: date | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_lifecycle_fields(cls, value: Any) -> Any:
        if isinstance(value, dict) and {
            "status",
            "approval_status",
            "published_at",
            "closed_at",
        }.intersection(value):
            raise ValueError(
                "lifecycle fields can only be changed through lifecycle actions"
            )
        return value

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not CURRENCY_PATTERN.match(normalized):
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return normalized

    @model_validator(mode="after")
    def check_salary_range(self) -> JobCreate:
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must not exceed salary_max")
        for amount in (self.salary_min, self.salary_max):
            if amount is not None and amount < 0:
                raise ValueError("salary amounts must not be negative")
        if self.openings < 1:
            raise ValueError("openings must be at least 1")
        return self


class JobUpdate(SQLModel):
    title: TitleField | None = None
    slug: SlugField | None = None
    department: ShortTextField = None
    employment_type: EmploymentType | None = None
    location: ShortTextField = None
    work_mode: WorkMode | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: Annotated[str | None, StringConstraints(min_length=3, max_length=3)] = (
        None
    )
    description: LongTextField = None
    responsibilities: LongTextField = None
    requirements: LongTextField = None
    preferred_qualifications: LongTextField = None
    skills: list[str] | None = None
    hiring_manager_id: UUID | None = None
    recruiter_id: UUID | None = None
    openings: int | None = None
    target_hire_date: date | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_lifecycle_fields(cls, value: Any) -> Any:
        if isinstance(value, dict) and {
            "status",
            "approval_status",
            "published_at",
            "closed_at",
        }.intersection(value):
            raise ValueError(
                "lifecycle fields can only be changed through lifecycle actions"
            )
        return value

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not CURRENCY_PATTERN.match(normalized):
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return normalized

    @model_validator(mode="after")
    def check_salary_range(self) -> JobUpdate:
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must not exceed salary_max")
        for amount in (self.salary_min, self.salary_max):
            if amount is not None and amount < 0:
                raise ValueError("salary amounts must not be negative")
        if self.openings is not None and self.openings < 1:
            raise ValueError("openings must be at least 1")
        return self

    def changes(self) -> dict[str, Any]:
        """Fields explicitly set by the caller, for scoped PATCH-style updates."""
        return self.model_dump(exclude_unset=True)


class JobRead(SQLModel):
    id: UUID
    organization_id: UUID
    title: str
    slug: str
    department: str | None = None
    employment_type: EmploymentType | None = None
    location: str | None = None
    work_mode: WorkMode | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    description: str | None = None
    responsibilities: str | None = None
    requirements: str | None = None
    preferred_qualifications: str | None = None
    skills: list[str] = []
    hiring_manager_id: UUID | None = None
    recruiter_id: UUID | None = None
    openings: int
    target_hire_date: date | None = None
    status: JobStatus
    approval_status: JobApprovalStatus
    published_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PublicJobRead(SQLModel):
    """Candidate-facing fields; excludes tenant IDs and workflow state."""

    slug: str
    title: str
    department: str | None = None
    employment_type: EmploymentType | None = None
    location: str | None = None
    work_mode: WorkMode | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    description: str | None = None
    responsibilities: str | None = None
    requirements: str | None = None
    preferred_qualifications: str | None = None
    skills: list[str] = []
    openings: int
    target_hire_date: date | None = None
    published_at: datetime | None = None
