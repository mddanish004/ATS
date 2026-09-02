from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from app.models.membership import MembershipRole


class InvitationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class Invitation(SQLModel, table=True):
    __tablename__ = "invitations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_id: UUID = Field(foreign_key="organizations.id", index=True)
    email: str = Field(index=True)
    role: MembershipRole = Field(
        sa_type=SAEnum(
            MembershipRole,
            values_callable=lambda enum: [member.value for member in enum],
            name="membership_role",
        )
    )
    token_hash: str
    status: InvitationStatus = Field(
        default=InvitationStatus.PENDING,
        sa_type=SAEnum(
            InvitationStatus,
            values_callable=lambda enum: [member.value for member in enum],
            name="invitation_status",
        ),
    )
    expires_at: datetime
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    accepted_at: datetime | None = None