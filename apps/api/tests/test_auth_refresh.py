from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlmodel import select

from app.core.config import settings
from app.core.security import decode_token
from app.models import RefreshToken, User

LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
REFRESH_URL = "/api/v1/auth/refresh"
REGISTER_URL = "/api/v1/auth/register"

EMAIL = "ada@example.com"
PASSWORD = "CorrectHorseBatteryStaple9"

REFRESH_DETAIL = "Invalid or expired refresh token"


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


def _all_hashes(session):
    return [r.token_hash for r in session.exec(select(RefreshToken)).all()]


def test_refresh_rotates_tokens_and_links_family(client, session):
    assert _register(client).status_code == 201
    old_raw = _login(client).cookies.get("refresh_token")
    assert old_raw

    response = client.post(REFRESH_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == EMAIL

    # Raw refresh never in JSON; cookie replaced with a different token.
    assert "refresh_token" not in body
    assert "token_hash" not in body
    new_raw = response.cookies.get("refresh_token")
    assert new_raw and new_raw != old_raw

    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie

    # New access JWT valid for the same user.
    user = session.exec(select(User).where(User.email == EMAIL)).one()
    assert decode_token(body["access_token"])["sub"] == str(user.id)

    # Old revoked + linked; new persisted hash-only with correct expiry.
    old_record = _record_for(session, old_raw)
    new_record = _record_for(session, new_raw)
    assert old_record.revoked_at is not None
    assert old_record.replaced_by_id == new_record.id
    assert new_record.revoked_at is None
    assert new_record.replaced_by_id is None
    assert new_record.user_id == user.id
    assert new_raw not in _all_hashes(session)
    assert new_record.token_hash == sha256(new_raw.encode()).hexdigest()
    # New token carries a full fresh lifetime (SQLite may drop tzinfo, and
    # created_at/expires_at clocks can skew by microseconds: be tolerant).
    expected_lifetime = settings.refresh_token_expire_days * 24 * 60 * 60
    actual_lifetime = (new_record.expires_at - new_record.created_at).total_seconds()
    assert abs(actual_lifetime - expected_lifetime) < 300


def test_refresh_replay_rejected_without_new_session(client, session):
    assert _register(client).status_code == 201
    old_raw = _login(client).cookies.get("refresh_token")
    assert old_raw
    assert client.post(REFRESH_URL).status_code == 200
    count_after_rotation = len(session.exec(select(RefreshToken)).all())
    assert count_after_rotation == 2

    # Replay the rotated (revoked) token: rejected, mints nothing.
    client.cookies.set("refresh_token", old_raw)
    replay = client.post(REFRESH_URL)

    assert replay.status_code == 401
    assert replay.json()["error"]["message"] == REFRESH_DETAIL
    assert len(session.exec(select(RefreshToken)).all()) == count_after_rotation
    # The rotated-in session remains the only active one.
    active = [r for r in session.exec(select(RefreshToken)).all() if r.revoked_at is None]
    assert len(active) == 1


def test_refresh_rejects_unknown_token_without_state_change(client, session):
    client.cookies.set("refresh_token", "no-such-token")
    response = client.post(REFRESH_URL)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == REFRESH_DETAIL
    assert session.exec(select(RefreshToken)).all() == []


def test_refresh_rejects_missing_cookie(client, session):
    response = client.post(REFRESH_URL)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == REFRESH_DETAIL
    assert session.exec(select(RefreshToken)).all() == []


def test_refresh_rejects_expired_token(client, session):
    assert _register(client).status_code == 201
    old_raw = _login(client).cookies.get("refresh_token")
    record = _record_for(session, old_raw)
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.add(record)
    session.commit()

    response = client.post(REFRESH_URL)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == REFRESH_DETAIL
    # Expired token mints nothing and links nothing.
    assert len(session.exec(select(RefreshToken)).all()) == 1
    assert _record_for(session, old_raw).replaced_by_id is None


def test_refresh_rejects_revoked_token(client, session):
    assert _register(client).status_code == 201
    old_raw = _login(client).cookies.get("refresh_token")
    client.cookies.set("refresh_token", old_raw)
    assert client.post(LOGOUT_URL).status_code == 200

    client.cookies.set("refresh_token", old_raw)
    response = client.post(REFRESH_URL)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == REFRESH_DETAIL
    assert len(session.exec(select(RefreshToken)).all()) == 1


def test_refresh_rejects_inactive_user(client, session):
    assert _register(client).status_code == 201
    _login(client)
    user = session.exec(select(User).where(User.email == EMAIL)).one()
    user.is_active = False
    session.add(user)
    session.commit()

    response = client.post(REFRESH_URL)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == REFRESH_DETAIL
    assert len(session.exec(select(RefreshToken)).all()) == 1
