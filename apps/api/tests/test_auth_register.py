from uuid import UUID

import pytest
from sqlmodel import select

from app.core.security import verify_password
from app.models import Membership, User

REGISTER_URL = "/api/v1/auth/register"

VALID_PAYLOAD = {
    "email": "ada@example.com",
    "password": "CorrectHorseBatteryStaple9",
    "first_name": "Ada",
    "last_name": "Lovelace",
}


def test_register_returns_201_with_safe_user_payload(client, session):
    response = client.post(
        REGISTER_URL,
        json={**VALID_PAYLOAD, "email": "  Ada@Example.COM "},
    )

    assert response.status_code == 201
    data = response.json()
    assert UUID(data["id"])
    assert data["email"] == "ada@example.com"
    assert data["first_name"] == "Ada"
    assert data["last_name"] == "Lovelace"
    assert data["is_email_verified"] is False
    assert data["is_active"] is True
    assert data["created_at"]
    assert data["updated_at"]
    assert "password" not in data
    assert "password_hash" not in data

    user = session.exec(select(User).where(User.email == "ada@example.com")).one()
    assert user.is_email_verified is False
    assert user.is_active is True


def test_register_hashes_password_and_never_persists_plaintext(client, session):
    plaintext = VALID_PAYLOAD["password"]

    response = client.post(REGISTER_URL, json=VALID_PAYLOAD)

    assert response.status_code == 201

    user = session.exec(select(User).where(User.email == VALID_PAYLOAD["email"])).one()
    assert user.password_hash is not None
    assert user.password_hash != plaintext
    assert plaintext not in user.password_hash
    assert verify_password(plaintext, user.password_hash)


def test_register_rejects_duplicate_email(client, session):
    first = client.post(REGISTER_URL, json=VALID_PAYLOAD)
    assert first.status_code == 201

    duplicate = client.post(REGISTER_URL, json=VALID_PAYLOAD)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Email already registered"

    case_variant = client.post(
        REGISTER_URL,
        json={**VALID_PAYLOAD, "email": "ADA@EXAMPLE.COM"},
    )
    assert case_variant.status_code == 409

    users = session.exec(select(User).where(User.email == VALID_PAYLOAD["email"])).all()
    assert len(users) == 1


def test_register_creates_no_membership(client, session):
    response = client.post(REGISTER_URL, json=VALID_PAYLOAD)

    assert response.status_code == 201
    user_id = UUID(response.json()["id"])
    memberships = session.exec(select(Membership)).all()
    assert memberships == []
    assert session.get(User, user_id) is not None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({**VALID_PAYLOAD, "email": "not-an-email"}, id="invalid-email"),
        pytest.param({**VALID_PAYLOAD, "email": "a@"}, id="incomplete-email"),
        pytest.param({**VALID_PAYLOAD, "password": "short7"}, id="password-too-short"),
        pytest.param({**VALID_PAYLOAD, "password": "x" * 129}, id="password-too-long"),
        pytest.param({**VALID_PAYLOAD, "first_name": "   "}, id="blank-first-name"),
        pytest.param({**VALID_PAYLOAD, "last_name": ""}, id="blank-last-name"),
        pytest.param(
            {**VALID_PAYLOAD, "first_name": "x" * 101}, id="first-name-too-long"
        ),
    ],
)
def test_register_validates_payload(client, payload):
    response = client.post(REGISTER_URL, json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {
                "password": VALID_PAYLOAD["password"],
                "first_name": "Ada",
                "last_name": "Lovelace",
            },
            id="missing-email",
        ),
        pytest.param(
            {
                "email": VALID_PAYLOAD["email"],
                "first_name": "Ada",
                "last_name": "Lovelace",
            },
            id="missing-password",
        ),
        pytest.param(
            {
                "email": VALID_PAYLOAD["email"],
                "password": VALID_PAYLOAD["password"],
                "last_name": "Lovelace",
            },
            id="missing-first-name",
        ),
    ],
)
def test_register_requires_all_fields(client, session, payload):
    response = client.post(REGISTER_URL, json=payload)

    assert response.status_code == 422
    assert session.exec(select(User)).all() == []
