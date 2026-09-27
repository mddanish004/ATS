"""Focused tests for the reusable organization-context mechanism.

These pin the contract future tenant-owned routers will depend on:
`get_organization_context` resolves (user, organization, membership, role)
entirely server-side from the authenticated user plus a membership lookup.
Endpoint-level cross-tenant behavior is covered in test_rbac.py; here the
dependency itself is invoked directly alongside targeted request tests.
"""

from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.api.deps import get_organization_context
from app.core.security import create_access_token
from app.models import Membership, MembershipRole, Organization, User


def _make_user(session, email):
    user = User(
        email=email,
        first_name="Test",
        last_name="User",
        is_email_verified=True,
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


def test_valid_membership_yields_full_context(session, organization, user):
    _member(session, organization, user, MembershipRole.RECRUITER)

    ctx = get_organization_context(organization.id, session, user)

    assert ctx.user.id == user.id
    assert ctx.organization.id == organization.id
    assert ctx.membership.user_id == user.id
    assert ctx.membership.organization_id == organization.id
    assert ctx.membership.role is MembershipRole.RECRUITER
    assert ctx.role is MembershipRole.RECRUITER


def test_role_comes_from_persisted_membership(session, organization, user):
    _member(session, organization, user, MembershipRole.INTERVIEWER)
    assert get_organization_context(organization.id, session, user).role is (
        MembershipRole.INTERVIEWER
    )

    membership = session.exec(
        select(Membership).where(
            Membership.organization_id == organization.id,
            Membership.user_id == user.id,
        )
    ).one()
    membership.role = MembershipRole.ADMIN
    session.add(membership)
    session.commit()

    assert get_organization_context(organization.id, session, user).role is (
        MembershipRole.ADMIN
    )


def test_user_resolves_each_membership_independently(session, user):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    _member(session, org_a, user, MembershipRole.RECRUITER)
    _member(session, org_b, user, MembershipRole.INTERVIEWER)

    ctx_a = get_organization_context(org_a.id, session, user)
    ctx_b = get_organization_context(org_b.id, session, user)

    assert (ctx_a.organization.id, ctx_a.role) == (org_a.id, MembershipRole.RECRUITER)
    assert (ctx_b.organization.id, ctx_b.role) == (org_b.id, MembershipRole.INTERVIEWER)
    assert ctx_a.user.id == ctx_b.user.id == user.id


def test_missing_membership_rejected_without_oracle(session, organization, user):
    with pytest.raises(HTTPException) as exc_info:
        get_organization_context(organization.id, session, user)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Organization not found"


def test_unknown_and_forbidden_organizations_are_indistinguishable(
    session, user, missing_organization_id
):
    org = _make_org(session, "Real Org")

    with pytest.raises(HTTPException) as unknown:
        get_organization_context(UUID(missing_organization_id), session, user)
    with pytest.raises(HTTPException) as forbidden:
        get_organization_context(org.id, session, user)

    assert unknown.value.status_code == forbidden.value.status_code == 404
    assert unknown.value.detail == forbidden.value.detail == "Organization not found"


def test_spoofed_user_id_in_body_cannot_change_context_user(
    client, session, user
):
    other = _make_user(session, "other@hireflow.test")
    created = client.post(
        "/api/v1/organizations",
        json={"name": "Acme", "user_id": str(other.id)},
        headers=_headers(user),
    )
    assert created.status_code == 201

    ctx = get_organization_context(UUID(created.json()["id"]), session, user)

    assert ctx.user.id == user.id
    assert ctx.user.id != other.id
    assert ctx.membership.user_id == user.id


def test_context_user_is_authenticated_user_not_a_lookup(session, user):
    stranger = _make_user(session, "stranger@hireflow.test")
    org = _make_org(session, "Org")
    _member(session, org, user, MembershipRole.ADMIN)
    _member(session, org, stranger, MembershipRole.ADMIN)

    ctx = get_organization_context(org.id, session, user)

    assert ctx.user.id == user.id
    assert ctx.membership.user_id == user.id


def test_unauthenticated_request_never_reaches_context(client, organization):
    response = client.get(f"/api/v1/organizations/{organization.id}")

    assert response.status_code == 401


def test_member_of_a_cannot_establish_b_context_at_endpoint(
    client, session, user
):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    _member(session, org_a, user, MembershipRole.ADMIN)

    response = client.get(
        f"/api/v1/organizations/{org_b.id}", headers=_headers(user)
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Organization not found"}
    assert "Org B" not in response.text
