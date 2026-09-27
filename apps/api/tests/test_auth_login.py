from datetime import datetime, timezone
from hashlib import sha256

import jwt
from sqlmodel import select

from app.core.config import settings
from app.core.security import decode_token, hash_password
from app.models import RefreshToken, User

LOGIN_URL = "/api/v1/auth/login"
REGISTER_URL = "/api/v1/auth/register"

EMAIL = "ada@example.com"
PASSWORD = "CorrectHorseBatteryStaple9"

GENERIC_DETAIL = "Invalid email or password"


def _register(client, email=EMAIL, password=PASSWORD):
    return client.post(
        REGISTER_URL,
        json={
            "email": email,
            "password": password,
            "first_name": "Ada",
            "last_name": "Lovelace",
        },
    )


def _login(client, email=EMAIL, password=PASSWORD):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


def test_login_success_returns_tokens_and_sets_cookie(client, session):
    assert _register(client).status_code == 201

    response = _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == EMAIL
    assert body["user"]["is_active"] is True

    # Safe response: no sensitive material.
    assert "password" not in body
    assert "password_hash" not in body
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert "refresh_token" not in body
    assert "token_hash" not in body

    # HttpOnly cookie-based browser session.
    raw_refresh = response.cookies.get("refresh_token")
    assert raw_refresh
    set_cookie = response.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()

    # Access token is a valid JWT for this user.
    payload = decode_token(body["access_token"])
    assert payload["type"] == "access"
    user = session.exec(select(User).where(User.email == EMAIL)).one()
    assert payload["sub"] == str(user.id)

    # Refresh token persisted as hash only, with correct expiry.
    records = session.exec(select(RefreshToken)).all()
    assert len(records) == 1
    record = records[0]
    assert record.user_id == user.id
    assert record.revoked_at is None
    assert record.token_hash == sha256(raw_refresh.encode()).hexdigest()
    assert record.token_hash != raw_refresh
    assert raw_refresh not in record.token_hash

    expected_lifetime = settings.refresh_token_expire_days * 24 * 60 * 60
    actual_lifetime = (record.expires_at - record.created_at).total_seconds()
    # SQLite may drop tzinfo; compare instants tolerantly.
    assert abs(actual_lifetime - expected_lifetime) < 300
    now = datetime.now(timezone.utc)
    expires = record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    assert expires > now


def test_login_normalizes_email(client):
    assert _register(client).status_code == 201

    response = _login(client, email="  ADA@Example.COM ")

    assert response.status_code == 200
    assert response.json()["user"]["email"] == EMAIL


def test_login_unverified_user_can_authenticate(client):
    # Registration leaves users unverified; verification gates org resources
    # (per PRD), not login itself.
    assert _register(client).status_code == 201

    response = _login(client)

    assert response.status_code == 200
    assert response.json()["user"]["is_email_verified"] is False


def test_login_wrong_password_rejected_without_cookie_or_record(client, session):
    assert _register(client).status_code == 201

    response = _login(client, password="WrongPassword123!")

    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_DETAIL
    assert response.cookies.get("refresh_token") is None
    assert session.exec(select(RefreshToken)).all() == []


def test_login_unknown_email_rejected(client, session):
    response = _login(client, email="nobody@example.com")

    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_DETAIL
    assert session.exec(select(RefreshToken)).all() == []


def test_login_failure_does_not_reveal_account_existence(client):
    assert _register(client).status_code == 201

    wrong_password = _login(client, password="WrongPassword123!")
    unknown_email = _login(client, email="ghost@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_login_inactive_user_rejected_with_generic_error(client, session):
    assert _register(client).status_code == 201
    user = session.exec(select(User).where(User.email == EMAIL)).one()
    user.is_active = False
    session.add(user)
    session.commit()

    response = _login(client)

    # Same generic 401 so inactive accounts cannot be enumerated.
    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_DETAIL
    assert session.exec(select(RefreshToken)).all() == []


def test_login_rejects_missing_fields(client):
    response = client.post(LOGIN_URL, json={"email": EMAIL})

    assert response.status_code == 422


def test_login_access_token_uses_configured_jwt(client):
    assert _register(client).status_code == 201

    token = _login(client).json()["access_token"]
    payload = jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )

    assert payload["type"] == "access"
    assert payload["sub"]


def test_login_with_directly_created_user(client, session):
    user = User(
        email="grace@example.com",
        password_hash=hash_password(PASSWORD),
        first_name="Grace",
        last_name="Hopper",
        is_email_verified=True,
        is_active=True,
    )
    session.add(user)
    session.commit()

    response = _login(client, email="grace@example.com")

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "grace@example.com"
