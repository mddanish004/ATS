from uuid import uuid4, UUID
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"

    id:UUID = Field(default_factory=uuid4, primary_key=True)
    name:str
    logo_url:str | None = None
    website: str | None = None
    description: str | None = None
    timezone: str = "UTC"
    currency: str = "USD"
    created_at : datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at : datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


