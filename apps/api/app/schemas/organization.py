import re
from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, StringConstraints, field_validator
from sqlmodel import SQLModel

from app.models.membership import MembershipRole

CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class OrganizationCreate(SQLModel):
    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ]
    slug: Annotated[
        str | None,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        ),
    ] = None
    logo_url: AnyHttpUrl | None = None
    website: AnyHttpUrl | None = None
    description: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, max_length=5000),
    ] = None
    timezone: str = "UTC"
    currency: str = "USD"

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not CURRENCY_PATTERN.match(normalized):
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return normalized


class UserSummary(SQLModel):
    id: UUID
    email: str
    first_name: str
    last_name: str
    avatar_url: str | None = None


class OrganizationRead(SQLModel):
    id: UUID
    name: str
    slug: str
    logo_url: str | None = None
    website: str | None = None
    description: str | None = None
    timezone: str
    currency: str
    created_at: datetime
    updated_at: datetime


class MembershipRead(SQLModel):
    id: UUID
    organization_id: UUID
    user_id: UUID
    role: MembershipRole
    created_at: datetime
    updated_at: datetime
    user: UserSummary
