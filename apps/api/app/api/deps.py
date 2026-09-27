from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from app.core.permissions import Permission, has_permission
from app.core.security import decode_token
from app.db.database import get_session
from app.models import Membership, MembershipRole, Organization, User

bearer_scheme = HTTPBearer(auto_error=False)


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _credentials_error()

    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _credentials_error() from exc

    subject = payload.get("sub")
    if payload.get("type") != "access" or not isinstance(subject, str):
        raise _credentials_error()

    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise _credentials_error() from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise _credentials_error()
    return user


def get_verified_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Authenticated user with a verified email.

    Per the PRD, unverified users must not access protected organization
    resources. Authentication (401) stays in ``get_current_user``; this layer
    reports the verified-email gate as 403 since the caller is authenticated
    but not authorized for protected resources yet.
    """
    if not current_user.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required",
        )
    return current_user


@dataclass(frozen=True)
class OrganizationContext:
    """Tenant scope resolved server-side from authenticated membership.

    The client-supplied organization ID is used only to look up the caller's
    own membership; downstream code must use ``user`` / ``organization`` /
    ``membership`` from this context (never raw client input) for identity,
    authorization, and scoping tenant-owned queries.
    """

    user: User
    organization: Organization
    membership: Membership

    @property
    def role(self) -> MembershipRole:
        return self.membership.role


def _organization_not_found() -> HTTPException:
    # Same response for "no such organization" and "not a member": a valid
    # object ID must never confirm another tenant's object existence.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Organization not found",
    )


def get_organization_context(
    organization_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_verified_user)],
) -> OrganizationContext:
    organization = session.get(Organization, organization_id)
    if organization is None:
        raise _organization_not_found()
    membership = session.exec(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == current_user.id,
        )
    ).first()
    if membership is None:
        raise _organization_not_found()
    return OrganizationContext(
        user=current_user, organization=organization, membership=membership
    )


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions",
    )


def require_permission(permission: Permission | str) -> Callable[..., OrganizationContext]:
    """FastAPI dependency factory enforcing a PRD permission in org context.

    Usage: ``ctx: Annotated[OrganizationContext, Depends(require_permission("jobs.read"))]``.
    Resolves membership, checks the role grant server-side, and returns the
    tenant context. Unknown permission names fail fast at route definition.
    """
    required = Permission(permission)

    def check(
        ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    ) -> OrganizationContext:
        if not has_permission(ctx.membership.role, required):
            raise _forbidden()
        return ctx

    return check
