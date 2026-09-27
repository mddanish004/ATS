"""Final Day 4 security-review probes.

Closes the remaining attack-vector gaps not covered elsewhere:
- role forging via query param and request body (headers were covered in
  test_rbac.py; the decision must come from persisted MembershipRole only),
- same natural user holding memberships in two tenants, with per-tenant
  record isolation asserted in both directions.

No new production surface is exercised here beyond the existing
organization endpoints and tenant helpers.
"""

from app.core.security import create_access_token
from app.db.tenant import get_by_id_for_org, list_for_org
from app.models import Membership, MembershipRole, Organization, User


def _make_user(session, email):
    user = User(
        email=email,
        first_name="Review",
        last_name="Probe",
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


def test_role_forging_via_query_param_has_no_effect(client, session, organization):
    user = _make_user(session, "recruiter@hireflow.test")
    _member(session, organization, user, MembershipRole.RECRUITER)

    for query in (
        "?role=admin",
        "?role=admin&permissions=users.manage",
        "?membership_role=admin",
    ):
        response = client.get(
            f"/api/v1/organizations/{organization.id}/memberships{query}",
            headers=_headers(user),
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "Insufficient permissions"}


def test_role_forging_via_body_cannot_elevate_or_smuggle(client, session):
    creator = _make_user(session, "creator@hireflow.test")
    intruder = _make_user(session, "intruder@hireflow.test")

    response = client.post(
        "/api/v1/organizations",
        json={
            "name": "Acme",
            "role": "admin",
            "user_id": str(intruder.id),
            "membership": {"user_id": str(intruder.id), "role": "admin"},
        },
        headers=_headers(creator),
    )

    assert response.status_code == 201
    members = client.get(
        f"/api/v1/organizations/{response.json()['id']}/memberships",
        headers=_headers(creator),
    )
    assert members.status_code == 200
    rows = members.json()
    assert len(rows) == 1
    assert rows[0]["user_id"] == str(creator.id)
    assert rows[0]["role"] == "admin"


def test_forged_role_never_persists_as_membership(session):
    from sqlmodel import select

    rows = session.exec(select(Membership)).all()
    assert rows == []


def test_same_user_two_tenants_records_stay_separated(session):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    user = _make_user(session, "both@hireflow.test")
    _member(session, org_a, user, MembershipRole.RECRUITER)
    _member(session, org_b, user, MembershipRole.INTERVIEWER)

    from sqlmodel import select

    memberships = session.exec(
        select(Membership).where(Membership.user_id == user.id)
    ).all()
    assert len(memberships) == 2
    row_a = next(m for m in memberships if m.organization_id == org_a.id)
    row_b = next(m for m in memberships if m.organization_id == org_b.id)

    # Each membership ID resolves only under its own tenant.
    assert get_by_id_for_org(session, Membership, row_a.id, org_a.id) is not None
    assert get_by_id_for_org(session, Membership, row_b.id, org_b.id) is not None
    assert get_by_id_for_org(session, Membership, row_a.id, org_b.id) is None
    assert get_by_id_for_org(session, Membership, row_b.id, org_a.id) is None

    # Listings never mix tenants even though user_id is identical.
    assert [m.id for m in list_for_org(session, Membership, org_a.id)] == [row_a.id]
    assert [m.id for m in list_for_org(session, Membership, org_b.id)] == [row_b.id]


def test_same_user_per_tenant_roles_enforced_at_endpoint(client, session):
    org_a = _make_org(session, "Org A")
    org_b = _make_org(session, "Org B")
    user = _make_user(session, "both@hireflow.test")
    _member(session, org_a, user, MembershipRole.RECRUITER)
    _member(session, org_b, user, MembershipRole.ADMIN)

    denied = client.get(
        f"/api/v1/organizations/{org_a.id}/memberships", headers=_headers(user)
    )
    allowed = client.get(
        f"/api/v1/organizations/{org_b.id}/memberships", headers=_headers(user)
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert [m["user"]["email"] for m in allowed.json()] == ["both@hireflow.test"]
