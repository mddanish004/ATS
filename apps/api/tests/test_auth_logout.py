from datetime import datetime, timezone
from hashlib import sha256

from sqlmodel import select

from app.models import RefreshToken

LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
REGISTER_URL = "/api/v1/auth/register"

EMAIL = "ada@example.com"
PASSWORD = "CorrectHorseBatteryStaple9"


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


def _record_for(session, raw_token):
    return session.exec(
        select(RefreshToken).where(
            RefreshToken.token_hash == sha256(raw_token.encode()).hexdigest()
        )
    ).one()


def test_logout_revokes_session_and_clears_cookie(client, session):
    assert _register(client).status_code == 201
    raw_refresh = _login(client).cookies.get("refresh_token")
    assert raw_refresh
    assert _record_for(session, raw_refresh).revoked_at is None

    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    assert response.json() == {"detail": "Logged out"}

    # Raw token never returned or persisted.
    assert raw_refresh not in response.text
    record = _record_for(session, raw_refresh)
    assert record.token_hash != raw_refresh
    assert record.token_hash == sha256(raw_refresh.encode()).hexdigest()

    # Session revoked but retained for audit; revoked_at populated.
    assert record.revoked_at is not None
    revoked_at = record.revoked_at
    if revoked_at.tzinfo is None:
        revoked_at = revoked_at.replace(tzinfo=timezone.utc)
    assert revoked_at <= datetime.now(timezone.utc)

    # Cookie cleared with consistent attributes.
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "refresh_token" in set_cookie
    assert "httponly" in set_cookie
    assert "max-age=0" in set_cookie or "expires=" in set_cookie


def test_logout_without_cookie_is_safe(client, session):
    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    assert response.json() == {"detail": "Logged out"}
    assert session.exec(select(RefreshToken)).all() == []


def test_logout_twice_is_idempotent(client, session):
    assert _register(client).status_code == 201
    raw_refresh = _login(client).cookies.get("refresh_token")
    assert raw_refresh

    first = client.post(LOGOUT_URL)
    first_revoked_at = _record_for(session, raw_refresh).revoked_at

    # Second logout re-presents the now-revoked cookie via the client jar.
    client.cookies.set("refresh_token", raw_refresh)
    second = client.post(LOGOUT_URL)

    assert first.status_code == second.status_code == 200
    assert second.json() == {"detail": "Logged out"}
    record = _record_for(session, raw_refresh)
    assert record.revoked_at is not None
    assert record.revoked_at == first_revoked_at
    assert len(session.exec(select(RefreshToken)).all()) == 1


def test_logout_with_unknown_token_is_safe(client, session):
    assert _register(client).status_code == 201
    raw_refresh = _login(client).cookies.get("refresh_token")
    assert raw_refresh

    client.cookies.set("refresh_token", "bogus-token-value")
    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    # The real session is untouched; nothing created or revoked.
    assert _record_for(session, raw_refresh).revoked_at is None
    assert len(session.exec(select(RefreshToken)).all()) == 1


def test_logout_only_revokes_presented_session(client, session):
    assert _register(client).status_code == 201
    first_raw = _login(client).cookies.get("refresh_token")
    second_raw = _login(client).cookies.get("refresh_token")
    assert first_raw and second_raw and first_raw != second_raw

    # Log out of the first session while the jar holds the second.
    client.cookies.set("refresh_token", first_raw)
    response = client.post(LOGOUT_URL)

    assert response.status_code == 200
    assert _record_for(session, first_raw).revoked_at is not None
    assert _record_for(session, second_raw).revoked_at is None
