"""Day 4 RBAC + tenant-isolation security tests.

Covers the centralized permission matrix, the organization-context
dependency, and cross-tenant (BOLA) enforcement on the organization
endpoints -- the current tenant-scoped surface. No domain resources
(Jobs/Candidates/...) exist yet; these tests lock the foundation those
routers will reuse.
"""

import pytest

from app.api.deps import require_permission
from app.core.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
)
from app.core.security import create_access_token
from app.models import Membership, MembershipRole, Organization, User


def _make_user(session, email, verified=True):
    user = User(
        email=email,
        first_name="Test",
        last_name="User",
        is_email_verified=verified,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _member(session, organization, user, role):
    session.add(
        Membership(
            organization_id=organization.id,
            user_id=user.id,
            role=role,
        )
    )
    session.commit()


def _make_org(session, name):
    org = Organization(name=name)
    session.add(org)
    session.commit()
    session.refresh(org)
    return org


# --- Permission matrix -----------------------------------------------------


def test_admin_holds_every_permission():
    for permission in Permission:
        assert has_permission(MembershipRole.ADMIN, permission)


def test_every_permission_is_granted_to_at_least_one_role():
    for permission in Permission:
        assert any(
            has_permission(role, permission) for role in MembershipRole
        ), permission


def test_every_role_holds_at_least_one_permission():
    for role in MembershipRole:
        assert len(ROLE_PERMISSIONS[role]) > 0, role


@pytest.mark.parametrize(
    ("role", "permission", "expected"),
    [
        (MembershipRole.RECRUITER, Permission.JOBS_CREATE, True),
        (MembershipRole.RECRUITER, Permission.CANDIDATES_EXPORT, True),
        (MembershipRole.RECRUITER, Permission.JOBS_APPROVE, False),
        (MembershipRole.RECRUITER, Permission.INTERVIEWS_FEEDBACK, False),
        (MembershipRole.RECRUITER, Permission.USERS_MANAGE, False),
        (MembershipRole.HIRING_MANAGER, Permission.OFFERS_APPROVE, True),
        (MembershipRole.HIRING_MANAGER, Permission.INTERVIEWS_FEEDBACK, True),
        (MembershipRole.HIRING_MANAGER, Permission.JOBS_CREATE, False),
        (MembershipRole.HIRING_MANAGER, Permission.SETTINGS_MANAGE, False),
        (MembershipRole.INTERVIEWER, Permission.INTERVIEWS_FEEDBACK, True),
        (MembershipRole.INTERVIEWER, Permission.CANDIDATES_READ, True),
        (MembershipRole.INTERVIEWER, Permission.CANDIDATES_DELETE, False),
        (MembershipRole.INTERVIEWER, Permission.ANALYTICS_READ, False),
        (MembershipRole.INTERVIEWER, Permission.USERS_MANAGE, False),
    ],
)
def test_matrix_spot_checks(role, permission, expected):
    assert has_permission(role, permission) is expected


def test_require_permission_rejects_unknown_permission_name():
    with pytest.raises(ValueError):
        require_permission("bogus.permission")


# --- Permitted access ------------------------------------------------------


def test_admin_can_list_members(client, session, organization, user):
    _member(session, organization, user, MembershipRole.ADMIN)

    response = client.get(
        f"/api/v1/organizations/{organization.id}/memberships",
        headers=_headers(user),
    )

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_member_with_narrow_role_can_still_read_own_organization(
    client, session, organization, user
):
    _member(session, organization, user, MembershipRole.INTERVIEWER)

    response = client.get(
        f"/api/v1/organizations/{organization.id}", headers=_headers(user)
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(organization.id)


# --- Permission denial -----------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [MembershipRole.RECRUITER, MembershipRole.HIRING_MANAGER, MembershipRole.INTERVIEWER],
)
def test_non_admin_roles_cannot_list_members(client, session, organization, user, role):
    _member(session, organization, user, role)

    response = client.get(
        f"/api/v1/organizations/{organization.id}/memberships",
        headers=_headers(user),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}


def test_client_role_claims_are_ignored(client, session, organization, user):
    _member(session, organization, user, MembershipRole.RECRUITER)
    headers = {
        **_headers(user),
        "X-Role": "admin",
        "X-Permissions": "users.manage",
    }

    response = client.get(
        f"/api/v1/organizations/{organization.id}/memberships", headers=headers
    )

    assert response.status_code == 403


# --- Membership / tenant isolation -----------------------------------------


def test_memberless_user_cannot_access_organization(client, organization, user):
    response = client.get(
        f"/api/v1/organizations/{organization.id}", headers=_headers(user)
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Organization not found"}


def test_member_of_a_cannot_access_b(client, session, user):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    _member(session, org_a, user, MembershipRole.ADMIN)

    for url in (
        f"/api/v1/organizations/{org_b.id}",
        f"/api/v1/organizations/{org_b.id}/memberships",
    ):
        response = client.get(url, headers=_headers(user))
        assert response.status_code == 404
        assert response.json() == {"detail": "Organization not found"}
        assert "Org B" not in response.text


def test_membership_list_is_scoped_to_context_organization(
    client, session, organization, user
):
    other_org = _make_org(session, "Other Org")
    other_user = _make_user(session, "other@hireflow.test")
    _member(session, organization, user, MembershipRole.ADMIN)
    _member(session, other_org, other_user, MembershipRole.ADMIN)

    response = client.get(
        f"/api/v1/organizations/{organization.id}/memberships",
        headers=_headers(user),
    )

    assert response.status_code == 200
    emails = [m["user"]["email"] for m in response.json()]
    assert emails == [user.email]


def test_multiple_memberships_resolve_per_organization(client, session, user):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    _member(session, org_a, user, MembershipRole.RECRUITER)
    _member(session, org_b, user, MembershipRole.ADMIN)

    assert (
        client.get(
            f"/api/v1/organizations/{org_a.id}", headers=_headers(user)
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/organizations/{org_b.id}", headers=_headers(user)
        ).status_code
        == 200
    )
    # Role is resolved per organization: recruiter in A, admin in B.
    assert (
        client.get(
            f"/api/v1/organizations/{org_a.id}/memberships", headers=_headers(user)
        ).status_code
        == 403
    )
    b_members = client.get(
        f"/api/v1/organizations/{org_b.id}/memberships", headers=_headers(user)
    )
    assert b_members.status_code == 200
    assert [m["user"]["email"] for m in b_members.json()] == [user.email]


def test_organization_creation_still_grants_admin(client, session, user):
    response = client.post(
        "/api/v1/organizations", json={"name": "Fresh Co"}, headers=_headers(user)
    )

    assert response.status_code == 201
    members = client.get(
        f"/api/v1/organizations/{response.json()['id']}/memberships",
        headers=_headers(user),
    )
    assert members.status_code == 200
    assert members.json()[0]["role"] == "admin"


def test_unauthenticated_requests_still_rejected(client, organization):
    assert (
        client.get(f"/api/v1/organizations/{organization.id}").status_code == 401
    )
    assert (
        client.get(
            f"/api/v1/organizations/{organization.id}/memberships"
        ).status_code
        == 401
    )
