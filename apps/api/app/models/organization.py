from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug", name="uq_organizations_slug"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    slug: str = Field(
        default_factory=lambda: f"organization-{uuid4().hex}", nullable=False
    )
    logo_url: str | None = None
    website: str | None = None
    description: str | None = None
    timezone: str = "UTC"
    currency: str = "USD"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
