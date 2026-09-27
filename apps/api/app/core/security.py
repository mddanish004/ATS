from pwdlib import PasswordHash
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import jwt
from app.core.config import settings
password_hash = PasswordHash.recommended()

def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)

def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

def decode_token(token:str) ->  dict:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])

def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_refresh_token_expires_at(now: datetime | None = None) -> datetime:
    base = now if now is not None else datetime.now(timezone.utc)
    return base + timedelta(days=settings.refresh_token_expire_days)

def generate_email_verification_token() -> str:
    return secrets.token_urlsafe(32)


def hash_email_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_email_verification_expires_at(now: datetime | None = None) -> datetime:
    base = now if now is not None else datetime.now(timezone.utc)
    return base + timedelta(hours=settings.email_verification_token_expire_hours)

def generate_password_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_password_reset_expires_at(now: datetime | None = None) -> datetime:
    base = now if now is not None else datetime.now(timezone.utc)
    return base + timedelta(hours=settings.password_reset_token_expire_hours)