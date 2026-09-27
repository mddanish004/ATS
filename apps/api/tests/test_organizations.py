from uuid import UUID

import pytest
from sqlmodel import select

from app.models import Membership, MembershipRole, Organization, User


def test_create_organization_returns_201_and_creates_admin_membership(
    client,
    session,
    user,
    auth_headers,
):
    payload = {
        "name": "  Acme Inc  ",
        "website": "https://acme.example.com",
        "description": "We build hiring tools",
        "timezone": "Europe/Berlin",
        "currency": "eur",
    }

    response = client.post("/api/v1/organizations", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    organization_id = UUID(data["id"])
    assert data["name"] == "Acme Inc"
    assert data["website"] == "https://acme.example.com/"
    assert data["description"] == "We build hiring tools"
    assert data["timezone"] == "Europe/Berlin"
    assert data["currency"] == "EUR"
    assert data["logo_url"] is None
    assert data["created_at"]
    assert data["updated_at"]

    organization = session.get(Organization, organization_id)
    assert organization is not None

    memberships = session.exec(
        select(Membership).where(Membership.organization_id == organization_id)
    ).all()
    assert len(memberships) == 1
    assert memberships[0].user_id == user.id
    assert memberships[0].role == MembershipRole.ADMIN


def test_create_organization_applies_documented_defaults(
    client,
    auth_headers,
):
    response = client.post(
        "/api/v1/organizations",
        json={"name": "Default Co"},
        headers=auth_headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["timezone"] == "UTC"
    assert data["currency"] == "USD"
    assert data["website"] is None
    assert data["description"] is None


def test_organization_endpoints_require_authentication(client, missing_organization_id):
    assert (
        client.post("/api/v1/organizations", json={"name": "Acme"}).status_code == 401
    )
    assert (
        client.get(f"/api/v1/organizations/{missing_organization_id}").status_code
        == 401
    )
    assert (
        client.get(
            f"/api/v1/organizations/{missing_organization_id}/memberships"
        ).status_code
        == 401
    )


def test_create_organization_rejects_invalid_token(client):
    response = client.post(
        "/api/v1/organizations",
        json={"name": "Acme"},
        headers={"Authorization": "Bearer not-a-token"},
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"name": "   "}, id="blank-name"),
        pytest.param(
            {"name": "Acme", "timezone": "Mars/Olympus"}, id="unknown-timezone"
        ),
        pytest.param({"name": "Acme", "currency": "dollars"}, id="invalid-currency"),
        pytest.param({"name": "Acme", "website": "not-a-url"}, id="invalid-website"),
        pytest.param({"name": "x" * 101}, id="name-too-long"),
    ],
)
def test_create_organization_validates_payload(client, auth_headers, payload):
    response = client.post("/api/v1/organizations", json=payload, headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["detail"]


def test_get_organization_returns_organization(client, organization, auth_headers):
    response = client.get(
        f"/api/v1/organizations/{organization.id}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert UUID(data["id"]) == organization.id
    assert data["name"] == "Acme Inc"
    assert data["timezone"] == "UTC"
    assert data["currency"] == "USD"
    assert data["created_at"]
    assert data["updated_at"]


def test_get_organization_returns_404_for_unknown_organization(
    client,
    auth_headers,
    missing_organization_id,
):
    response = client.get(
        f"/api/v1/organizations/{missing_organization_id}",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Organization not found"


def test_get_organization_rejects_invalid_organization_id(client, auth_headers):
    response = client.get(
        "/api/v1/organizations/not-a-uuid",
        headers=auth_headers,
    )

    assert response.status_code == 422


def test_list_memberships_returns_members_with_user_details(
    client,
    session,
    organization,
    user,
    auth_headers,
):
    other = User(
        email="recruiter@hireflow.test",
        first_name="Rita",
        last_name="Recruiter",
    )
    session.add(other)
    session.add(
        Membership(
            organization_id=organization.id,
            user_id=user.id,
            role=MembershipRole.ADMIN,
        )
    )
    session.add(
        Membership(
            organization_id=organization.id,
            user_id=other.id,
            role=MembershipRole.RECRUITER,
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/organizations/{organization.id}/memberships",
        headers=auth_headers,
    )

    assert response.status_code == 200
    members = response.json()
    assert len(members) == 2

    by_email = {member["user"]["email"]: member for member in members}
    admin = by_email["admin@hireflow.test"]
    recruiter = by_email["recruiter@hireflow.test"]

    assert UUID(admin["organization_id"]) == organization.id
    assert UUID(admin["user_id"]) == user.id
    assert admin["role"] == "admin"
    assert admin["user"]["first_name"] == "Ada"
    assert admin["user"]["last_name"] == "Admin"
    assert recruiter["role"] == "recruiter"
    assert recruiter["user"]["first_name"] == "Rita"
    assert admin["created_at"]
    assert recruiter["created_at"]


def test_list_memberships_returns_empty_list_without_members(
    client,
    organization,
    auth_headers,
):
    response = client.get(
        f"/api/v1/organizations/{organization.id}/memberships",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


def test_list_memberships_returns_404_for_unknown_organization(
    client,
    auth_headers,
    missing_organization_id,
):
    response = client.get(
        f"/api/v1/organizations/{missing_organization_id}/memberships",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Organization not found"
