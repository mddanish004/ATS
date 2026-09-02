from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


class MembershipRole(str, Enum):
    ADMIN = "admin"
    RECRUITER = "recruiter"
    HIRING_MANAGER = "hiring_manager"
    INTERVIEWER = "interviewer"


class Membership(SQLModel, table=True):
    __tablename__ = "memberships"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    organization_id: UUID = Field(foreign_key="organizations.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    role: MembershipRole = Field(
        sa_type=SAEnum(
            MembershipRole,
            values_callable=lambda enum: [member.value for member in enum],
            name="membership_role",
        )
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )