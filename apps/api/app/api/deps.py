from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.security import decode_token
from app.db.database import get_session
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _credentials_error()

    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _credentials_error() from exc

    subject = payload.get("sub")
    if payload.get("type") != "access" or not isinstance(subject, str):
        raise _credentials_error()

    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise _credentials_error() from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise _credentials_error()
    return user
