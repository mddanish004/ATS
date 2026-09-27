"""Focused tests for the reusable `require_permission` dependency.

These exercise the authorization layer directly -- one dependency call per
(role, permission) -- complementing the endpoint-level tests in
test_rbac.py. No domain routers (Jobs/Candidates/...) exist yet; this locks
the contract those routers will depend on:

- each role is authorized for its own permissions (string and enum forms),
- anything else is a generic 403,
- unknown permissions fail fast at definition time,
- the resolved organization context passes through untouched.
"""

import pytest
from fastapi import HTTPException

from app.api.deps import OrganizationContext, require_permission
from app.core.permissions import Permission
from app.models import Membership, MembershipRole, Organization, User


def _ctx(role):
    user = User(email="ctx@hireflow.test", first_name="Ctx", last_name="User")
    organization = Organization(name="Acme Inc")
    membership = Membership(
        organization_id=organization.id,
        user_id=user.id,
        role=role,
    )
    return OrganizationContext(
        user=user, organization=organization, membership=membership
    )


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (MembershipRole.ADMIN, "users.manage"),
        (MembershipRole.ADMIN, "jobs.approve"),
        (MembershipRole.RECRUITER, "jobs.create"),
        (MembershipRole.RECRUITER, "candidates.export"),
        (MembershipRole.RECRUITER, "interviews.create"),
        (MembershipRole.HIRING_MANAGER, "jobs.approve"),
        (MembershipRole.HIRING_MANAGER, "offers.approve"),
        (MembershipRole.HIRING_MANAGER, "interviews.feedback"),
        (MembershipRole.INTERVIEWER, "interviews.feedback"),
        (MembershipRole.INTERVIEWER, "candidates.read"),
        (MembershipRole.INTERVIEWER, "jobs.read"),
    ],
)
def test_role_is_authorized_for_own_permission(role, permission):
    ctx = _ctx(role)

    result = require_permission(permission)(ctx)

    assert result is ctx
    assert result.role is role
    assert result.organization.id == ctx.organization.id
    assert result.membership.user_id == ctx.membership.user_id


def test_enum_and_string_forms_are_equivalent():
    ctx = _ctx(MembershipRole.RECRUITER)

    assert require_permission(Permission.JOBS_READ)(ctx) is ctx
    assert require_permission("jobs.read")(ctx) is ctx


def test_admin_is_never_denied():
    ctx = _ctx(MembershipRole.ADMIN)

    for permission in Permission:
        assert require_permission(permission)(ctx) is ctx


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (MembershipRole.RECRUITER, "users.manage"),
        (MembershipRole.RECRUITER, "jobs.approve"),
        (MembershipRole.RECRUITER, "settings.manage"),
        (MembershipRole.HIRING_MANAGER, "jobs.create"),
        (MembershipRole.HIRING_MANAGER, "settings.manage"),
        (MembershipRole.HIRING_MANAGER, "users.manage"),
        (MembershipRole.INTERVIEWER, "jobs.create"),
        (MembershipRole.INTERVIEWER, "analytics.read"),
        (MembershipRole.INTERVIEWER, "candidates.delete"),
        (MembershipRole.INTERVIEWER, "users.manage"),
    ],
)
def test_role_without_permission_gets_generic_403(role, permission):
    with pytest.raises(HTTPException) as exc_info:
        require_permission(permission)(_ctx(role))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Insufficient permissions"


def test_denial_leaks_no_authorization_internals():
    with pytest.raises(HTTPException) as exc_info:
        require_permission("users.manage")(_ctx(MembershipRole.INTERVIEWER))

    body = str(exc_info.value.detail)
    assert "interviewer" not in body.lower()
    assert "users.manage" not in body
    assert "role" not in body.lower()


def test_unknown_permission_fails_fast_at_definition_time():
    with pytest.raises(ValueError, match="not a valid Permission"):
        require_permission("bogus.permission")
