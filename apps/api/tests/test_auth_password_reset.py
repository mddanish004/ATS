from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlmodel import select

from app.core.security import (
    get_password_reset_expires_at,
    hash_password_reset_token,
    verify_password,
)
from app.models import PasswordResetToken, RefreshToken, User

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
FORGOT_URL = "/api/v1/auth/forgot-password"
RESET_URL = "/api/v1/auth/reset-password"

EMAIL = "ada@example.com"
PASSWORD = "CorrectHorseBatteryStaple9"
NEW_PASSWORD = "EvenMoreCorrectHorse42"

PUBLIC_DETAIL = "If an account exists for this email, a reset email has been sent."
RESET_DETAIL = "Invalid or expired reset token"

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


def _issue(session, user, raw="reset-token-value", expires_at=None):
    record = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_password_reset_token(raw),
        expires_at=expires_at or get_password_reset_expires_at(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _record_for(session, raw):
    return session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == sha256(raw.encode()).hexdigest()
        )
    ).one()


def test_forgot_password_existing_account_issues_hash_only_token(client, session):
    assert _register(client).status_code == 201

    response = client.post(FORGOT_URL, json={"email": "  ADA@Example.COM "})

    assert response.status_code == 200
    assert response.json() == {"detail": PUBLIC_DETAIL}
    assert "token" not in response.text

    records = session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == _user(session).id
        )
    ).all()
    assert len(records) == 1
    assert len(records[0].token_hash) == 64  # sha256 hex, never the raw token
    assert records[0].consumed_at is None
    assert records[0].expires_at > records[0].created_at


def test_forgot_password_unknown_email_returns_same_response(client, session):
    known = client.post(FORGOT_URL, json={"email": "ghost@example.com"})
    assert _register(client).status_code == 201
    existing = client.post(FORGOT_URL, json={"email": EMAIL})

    assert known.status_code == existing.status_code == 200
    assert known.json() == existing.json() == {"detail": PUBLIC_DETAIL}
    # Only the real account produced a persisted token.
    assert len(session.exec(select(PasswordResetToken)).all()) == 1


def test_reset_valid_token_changes_password_and_consumes(client, session):
    assert _register(client).status_code == 201
    user = _user(session)
    old_hash = user.password_hash
    was_verified = user.is_email_verified
    raw = "good-reset-token"
    _issue(session, user, raw=raw)

    response = client.post(
        RESET_URL, json={"token": raw, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json() == {"detail": "Password has been reset."}
    assert "hash" not in response.text.lower()
    assert raw not in response.text

    user = _user(session)
    assert user.password_hash != old_hash
    assert verify_password(NEW_PASSWORD, user.password_hash)
    assert not verify_password(PASSWORD, user.password_hash)
    assert user.is_email_verified == was_verified
    assert _record_for(session, raw).consumed_at is not None


def test_reset_token_scoped_to_its_user(client, session):
    assert _register(client).status_code == 201
    assert _register(client, email="grace@example.com").status_code == 201
    raw = "scoped-token"
    _issue(session, _user(session, "grace@example.com"), raw=raw)

    # Token works (for its own user) and does not touch the other account.
    assert (
        client.post(RESET_URL, json={"token": raw, "new_password": NEW_PASSWORD})
    ).status_code == 200
    assert verify_password(PASSWORD, _user(session).password_hash)
    assert verify_password(NEW_PASSWORD, _user(session, "grace@example.com").password_hash)


def test_reset_expired_token_rejected(client, session):
    assert _register(client).status_code == 201
    user = _user(session)
    _issue(
        session,
        user,
        raw="expired-token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    response = client.post(
        RESET_URL, json={"token": "expired-token", "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == RESET_DETAIL
    assert verify_password(PASSWORD, _user(session).password_hash)
    assert _record_for(session, "expired-token").consumed_at is None


def test_reset_unknown_token_rejected_without_oracle(client, session):
    assert _register(client).status_code == 201
    _issue(
        session,
        _user(session),
        raw="expired-token",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    unknown = client.post(
        RESET_URL, json={"token": "no-such-token", "new_password": NEW_PASSWORD}
    )
    expired = client.post(
        RESET_URL, json={"token": "expired-token", "new_password": NEW_PASSWORD}
    )

    assert unknown.status_code == expired.status_code == 400
    assert unknown.json()["error"]["message"] == expired.json()["error"]["message"] == RESET_DETAIL


def test_reset_token_cannot_be_reused(client, session):
    assert _register(client).status_code == 201
    raw = "one-shot-token"
    _issue(session, _user(session), raw=raw)

    first = client.post(RESET_URL, json={"token": raw, "new_password": NEW_PASSWORD})
    replay = client.post(
        RESET_URL, json={"token": raw, "new_password": "AnotherPassword99"}
    )

    assert first.status_code == 200
    assert replay.status_code == 400
    assert replay.json()["error"]["message"] == RESET_DETAIL
    # Second password never applied.
    assert verify_password(NEW_PASSWORD, _user(session).password_hash)


def test_reset_validates_new_password(client, session):
    assert _register(client).status_code == 201
    _issue(session, _user(session), raw="policy-token")

    short = client.post(
        RESET_URL, json={"token": "policy-token", "new_password": "short7"}
    )
    assert short.status_code == 422
    # Rejected before any state change.
    assert verify_password(PASSWORD, _user(session).password_hash)
    assert _record_for(session, "policy-token").consumed_at is None


def test_new_password_authenticates_and_old_does_not(client, session):
    assert _register(client).status_code == 201
    raw = "rotate-credentials-token"
    _issue(session, _user(session), raw=raw)
    assert client.post(RESET_URL, json={"token": raw, "new_password": NEW_PASSWORD})

    assert (
        client.post(LOGIN_URL, json={"email": EMAIL, "password": NEW_PASSWORD})
    ).status_code == 200
    denied = client.post(LOGIN_URL, json={"email": EMAIL, "password": PASSWORD})
    assert denied.status_code == 401


def test_reset_revokes_existing_refresh_sessions(client, session):
    assert _register(client).status_code == 201
    old_cookie = client.post(
        LOGIN_URL, json={"email": EMAIL, "password": PASSWORD}
    ).cookies.get("refresh_token")
    assert old_cookie
    raw = "session-killer-token"
    _issue(session, _user(session), raw=raw)

    assert client.post(RESET_URL, json={"token": raw, "new_password": NEW_PASSWORD})

    sessions = session.exec(
        select(RefreshToken).where(RefreshToken.user_id == _user(session).id)
    ).all()
    assert sessions
    assert all(s.revoked_at is not None for s in sessions)

    # The pre-reset refresh cookie no longer rotates.
    client.cookies.set("refresh_token", old_cookie)
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_reset_requires_fields(client):
    assert client.post(RESET_URL, json={"token": "x"}).status_code == 422
    assert client.post(FORGOT_URL, json={}).status_code == 422
