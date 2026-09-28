import re
import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import (
    OrganizationContext,
    get_organization_context,
    get_verified_user,
    require_permission,
)
from app.core.permissions import Permission
from app.db.database import get_session
from app.db.tenant import scoped_select
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
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Email verification required or insufficient permissions",
        },
    },
)


def _slugify(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")


def _organization_slug(session: Session, name: str, requested_slug: str | None) -> str:
    base = requested_slug or _slugify(name) or "organization"
    candidate = base
    suffix = 2
    while session.exec(
        select(Organization.id).where(Organization.slug == candidate)
    ).first():
        if requested_slug is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Organization slug already exists",
            )
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@router.post(
    "",
    response_model=OrganizationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_organization(
    payload: OrganizationCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_verified_user)],
) -> Organization:
    organization = Organization(
        name=payload.name,
        slug=_organization_slug(session, payload.name, payload.slug),
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
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
) -> Organization:
    # Membership already verified by the context dependency; the tenant root
    # itself needs no further permission check to be viewed by its members.
    return ctx.organization


@router.get(
    "/{organization_id}/memberships",
    response_model=list[MembershipRead],
    responses={status.HTTP_404_NOT_FOUND: {"description": "Organization not found"}},
)
def list_memberships(
    ctx: Annotated[
        OrganizationContext, Depends(require_permission(Permission.USERS_MANAGE))
    ],
    session: Annotated[Session, Depends(get_session)],
) -> list[MembershipRead]:
    # Tenant scope comes from the resolved membership context, not the raw
    # client-supplied path ID: id + organization_id are bound together.
    rows = session.exec(
        scoped_select(Membership, ctx.organization.id, User)
        .join(User, Membership.user_id == User.id)
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
