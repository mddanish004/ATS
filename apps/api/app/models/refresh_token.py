from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    user_id: UUID = Field(
        foreign_key="users.id",
        index=True,
        nullable=False,
    )

    token_hash: str = Field(
        unique=True,
        index=True,
        nullable=False,
    )

    expires_at: datetime = Field(nullable=False)

    revoked_at: datetime | None = Field(default=None, nullable=True)

    replaced_by_id: UUID | None = Field(
        default=None,
        foreign_key="refresh_tokens.id",
        nullable=True,
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )