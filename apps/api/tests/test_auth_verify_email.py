from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlmodel import select

from app.core.security import (
    get_email_verification_expires_at,
    hash_email_verification_token,
)
from app.models import EmailVerificationToken, User

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
VERIFY_URL = "/api/v1/auth/verify-email"

EMAIL = "ada@example.com"
PASSWORD = "CorrectHorseBatteryStaple9"

VERIFY_DETAIL = "Invalid or expired verification token"

VALID_PAYLOAD = {
    "email": EMAIL,
    "password": PASSWORD,
    "first_name": "Ada",
    "last_name": "Lovelace",
}


def _register(client, **overrides):
    return client.post(REGISTER_URL, json={**VALID_PAYLOAD, **overrides})


def _user(session, email=EMAIL):
    return session.exec(select(User).where(User.email == email)).one()


def _issue(session, user, raw="verification-token-value", expires_at=None):
    record = EmailVerificationToken(
        user_id=user.id,
        token_hash=hash_email_verification_token(raw),
        expires_at=expires_at or get_email_verification_expires_at(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def test_register_issues_hash_only_verification_token(client, session):
    response = _register(client)

    assert response.status_code == 201
    assert "token" not in response.json()
    assert "token_hash" not in response.text

    user = _user(session)
    assert user.is_email_verified is False

    records = session.exec(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id
        )
    ).all()
    assert len(records) == 1
    assert len(records[0].token_hash) == 64  # sha256 hex, never the raw token
    assert records[0].consumed_at is None
    assert records[0].expires_at > records[0].created_at


def test_verify_valid_token_marks_user_verified(client, session):
    assert _register(client).status_code == 201
    raw = "valid-token-abc123"
    _issue(session, _user(session), raw=raw)

    response = client.post(VERIFY_URL, json={"token": raw})

    assert response.status_code == 200
    assert response.json() == {"detail": "Email verified"}
    assert raw not in response.text

    user = _user(session)
    assert user.is_email_verified is True
    record = session.exec(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == sha256(raw.encode()).hexdigest()
        )
    ).one()
    assert record.consumed_at is not None
    assert record.token_hash != raw


def test_verify_expired_token_rejected(client, session):
    assert _register(client).status_code == 201
    user = _user(session)
    _issue(
        session,
        user,
        raw="expired-token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    response = client.post(VERIFY_URL, json={"token": "expired-token"})

    assert response.status_code == 400
    assert response.json()["detail"] == VERIFY_DETAIL
    assert _user(session).is_email_verified is False


def test_verify_unknown_token_rejected_without_oracle(client, session):
    assert _register(client).status_code == 201
    user = _user(session)
    _issue(
        session,
        user,
        raw="expired-token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    unknown = client.post(VERIFY_URL, json={"token": "no-such-token"})
    expired = client.post(VERIFY_URL, json={"token": "expired-token"})

    assert unknown.status_code == expired.status_code == 400
    assert unknown.json() == expired.json() == {"detail": VERIFY_DETAIL}


def test_verify_token_is_single_use(client, session):
    assert _register(client).status_code == 201
    raw = "single-use-token"
    _issue(session, _user(session), raw=raw)

    assert client.post(VERIFY_URL, json={"token": raw}).status_code == 200
    replay = client.post(VERIFY_URL, json={"token": raw})

    assert replay.status_code == 400
    assert replay.json()["detail"] == VERIFY_DETAIL
    # Reuse never unsets verification.
    assert _user(session).is_email_verified is True


def test_verify_never_unsets_verified_user(client, session):
    assert _register(client).status_code == 201
    user = _user(session)
    user.is_email_verified = True
    session.add(user)
    session.commit()
    _issue(session, user, raw="late-token")

    response = client.post(VERIFY_URL, json={"token": "late-token"})

    assert response.status_code == 200
    assert _user(session).is_email_verified is True


def test_verify_requires_token_field(client):
    response = client.post(VERIFY_URL, json={})

    assert response.status_code == 422


def test_unverified_user_blocked_from_protected_resources_until_verified(
    client, session
):
    assert _register(client).status_code == 201
    access_token = client.post(
        LOGIN_URL, json={"email": EMAIL, "password": PASSWORD}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    blocked = client.post(
        "/api/v1/organizations", json={"name": "Acme"}, headers=headers
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Email verification required"

    raw = "gate-token"
    _issue(session, _user(session), raw=raw)
    assert client.post(VERIFY_URL, json={"token": raw}).status_code == 200

    # Same access token now passes the gate: verification, not re-login.
    allowed = client.post(
        "/api/v1/organizations", json={"name": "Acme"}, headers=headers
    )
    assert allowed.status_code == 201
