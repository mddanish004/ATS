"""Focused tests for the reusable auth dependency and protected routes.

Exercises ``get_current_user`` / ``get_verified_user`` (app/api/deps.py)
through the protected organization endpoints: every token failure mode must
produce the same generic 401 (no oracle, no JWT internals leaked), unverified
users get 403, and the token identity -- never client input -- drives
authorization.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from sqlmodel import select

from app.core.config import settings
from app.models import Membership, MembershipRole, User

GENERIC_401 = {"detail": "Could not validate credentials"}


def _mint(
    sub=None,
    token_type="access",
    secret=settings.jwt_secret_key,
    lifetime=timedelta(minutes=15),
    include_sub=True,
):
    now = datetime.now(timezone.utc)
    payload = {"type": token_type, "iat": now, "exp": now + lifetime}
    if include_sub:
        payload["sub"] = sub
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_missing_token_rejected_with_authenticate_header(client, organization):
    response = client.get(f"/api/v1/organizations/{organization.id}")

    assert response.status_code == 401
    assert response.json() == GENERIC_401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_valid_token_succeeds(client, session, organization, user, auth_headers):
    session.add(
        Membership(
            organization_id=organization.id,
            user_id=user.id,
            role=MembershipRole.ADMIN,
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/organizations/{organization.id}", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(organization.id)


@pytest.mark.parametrize(
    "make_token",
    [
        pytest.param(
            lambda user: _mint(str(user.id), lifetime=timedelta(seconds=-1)),
            id="expired",
        ),
        pytest.param(
            lambda user: _mint(str(user.id), secret="wrong-secret"),
            id="invalid-signature",
        ),
        pytest.param(
            lambda user: _mint(str(user.id), token_type="refresh"),
            id="refresh-as-access",
        ),
        pytest.param(
            lambda user: _mint(str(user.id), token_type="bogus"),
            id="unknown-type",
        ),
        pytest.param(lambda user: _mint("x", include_sub=False), id="missing-sub"),
        pytest.param(lambda user: _mint("not-a-uuid"), id="malformed-sub"),
        pytest.param(lambda user: _mint(str(uuid4())), id="unknown-user"),
        pytest.param(lambda user: "not-a-jwt", id="malformed-token"),
    ],
)
def test_token_failures_share_generic_401(client, organization, user, make_token):
    response = client.get(
        f"/api/v1/organizations/{organization.id}",
        headers=_bearer(make_token(user)),
    )

    assert response.status_code == 401
    assert response.json() == GENERIC_401


def test_inactive_user_rejected(client, organization, session, user, auth_headers):
    user.is_active = False
    session.add(user)
    session.commit()

    response = client.get(
        f"/api/v1/organizations/{organization.id}", headers=auth_headers
    )

    assert response.status_code == 401
    assert response.json() == GENERIC_401


def test_unverified_user_forbidden_on_protected_resource(client, organization, session):
    unverified = User(
        email="new@hireflow.test",
        first_name="New",
        last_name="User",
        is_email_verified=False,
    )
    session.add(unverified)
    session.commit()

    response = client.get(
        f"/api/v1/organizations/{organization.id}",
        headers=_bearer(
            _mint(str(unverified.id), secret=settings.jwt_secret_key)
        ),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Email verification required"}


def test_non_bearer_scheme_rejected(client, organization, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")

    response = client.get(
        f"/api/v1/organizations/{organization.id}",
        headers={"Authorization": f"Token {token}"},
    )

    assert response.status_code == 401
    assert response.json() == GENERIC_401


def test_cookie_credential_is_not_an_access_credential(
    client, organization, auth_headers
):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    client.cookies.set("refresh_token", token)

    response = client.get(f"/api/v1/organizations/{organization.id}")

    assert response.status_code == 401
    assert response.json() == GENERIC_401


def test_client_identity_cannot_override_authenticated_user(
    client, session, user, auth_headers
):
    other = User(email="other@hireflow.test", first_name="O", last_name="Other")
    session.add(other)
    session.commit()

    response = client.post(
        "/api/v1/organizations",
        json={"name": "Acme", "user_id": str(other.id)},
        headers=auth_headers,
    )

    assert response.status_code == 201
    memberships = session.exec(select(Membership)).all()
    assert len(memberships) == 1
    assert memberships[0].user_id == user.id
    assert memberships[0].user_id != other.id


def test_protected_responses_expose_no_credential_hashes(
    client, session, organization, user, auth_headers
):
    session.add(
        Membership(
            organization_id=organization.id,
            user_id=user.id,
            role=MembershipRole.ADMIN,
        )
    )
    session.commit()

    response = client.get(
        f"/api/v1/organizations/{organization.id}/memberships",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert "token_hash" not in response.text
