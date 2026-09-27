from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.db.database import get_session
from app.models import Membership, MembershipRole, Organization, User
from app.schemas import (
    MembershipRead,
    OrganizationCreate,
    OrganizationRead,
    UserSummary,
)

router = APIRouter(
    prefix="/organizations",
    tags=["organizations"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing, malformed, or expired access token",
        }
    },
)


@router.post(
    "",
    response_model=OrganizationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_organization(
    payload: OrganizationCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Organization:
    organization = Organization(
        name=payload.name,
        logo_url=str(payload.logo_url) if payload.logo_url else None,
        website=str(payload.website) if payload.website else None,
        description=payload.description,
        timezone=payload.timezone,
        currency=payload.currency,
    )
    membership = Membership(
        organization_id=organization.id,
        user_id=current_user.id,
        role=MembershipRole.ADMIN,
    )
    session.add(organization)
    session.add(membership)
    session.commit()
    session.refresh(organization)
    return organization


@router.get(
    "/{organization_id}",
    response_model=OrganizationRead,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Organization not found"}},
)
def get_organization(
    organization_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Organization:
    organization = session.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    return organization


@router.get(
    "/{organization_id}/memberships",
    response_model=list[MembershipRead],
    responses={status.HTTP_404_NOT_FOUND: {"description": "Organization not found"}},
)
def list_memberships(
    organization_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[MembershipRead]:
    organization = session.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    rows = session.exec(
        select(Membership, User)
        .join(User, Membership.user_id == User.id)
        .where(Membership.organization_id == organization_id)
        .order_by(Membership.created_at, Membership.id)
    ).all()
    return [
        MembershipRead(
            id=membership.id,
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            role=membership.role,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
            user=UserSummary.model_validate(user),
        )
        for membership, user in rows
    ]
