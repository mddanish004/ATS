from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class Candidate(SQLModel, table=True):
    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "email", name="uq_candidates_organization_email"
        ),
        CheckConstraint(
            "years_of_experience IS NULL OR years_of_experience >= 0",
            name="ck_candidates_years_experience_nonnegative",
        ),
        CheckConstraint("email = lower(email)", name="ck_candidates_email_normalized"),
        Index("ix_candidates_organization_created", "organization_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_id: UUID = Field(
        foreign_key="organizations.id", index=True, nullable=False
    )
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    current_title: str | None = None
    current_company: str | None = None
    skills: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    years_of_experience: float | None = None
    education: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    certifications: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    source: str | None = None
    consent_status: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
