from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class EmailVerificationToken(SQLModel, table=True):
    __tablename__ = "email_verification_tokens"

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

    consumed_at: datetime | None = Field(default=None, nullable=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
