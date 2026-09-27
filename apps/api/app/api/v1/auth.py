from datetime import datetime, timezone
from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_email_verification_token,
    generate_password_reset_token,
    generate_refresh_token,
    get_email_verification_expires_at,
    get_password_reset_expires_at,
    get_refresh_token_expires_at,
    hash_email_verification_token,
    hash_password,
    hash_password_reset_token,
    hash_refresh_token,
    verify_password,
)
from app.db.database import get_session
from app.models import EmailVerificationToken, PasswordResetToken, RefreshToken, User
from app.schemas import (
    ForgotPasswordRequest,
    LoginResponse,
    ResetPasswordRequest,
    UserLogin,
    UserRead,
    UserRegister,
    VerifyEmailRequest,
)
from app.services import email_service

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"

_INVALID_CREDENTIALS = "Invalid email or password"
_INVALID_REFRESH = "Invalid or expired refresh token"
_INVALID_VERIFICATION = "Invalid or expired verification token"
_INVALID_RESET = "Invalid or expired reset token"


def _invalid_credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_INVALID_CREDENTIALS,
    )


def _invalid_refresh_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_INVALID_REFRESH,
    )


def _invalid_verification_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=_INVALID_VERIFICATION,
    )


def _invalid_reset_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=_INVALID_RESET,
    )


def _is_secure_cookie() -> bool:
    return settings.environment.lower() in ("production", "staging")


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {"description": "Email already registered"},
    },
)
def register(
    payload: UserRegister,
    session: Annotated[Session, Depends(get_session)],
) -> User:
    existing = session.exec(
        select(User).where(func.lower(User.email) == payload.email)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=str(payload.email),
        password_hash=hash_password(payload.password),
        first_name=payload.first_name,
        last_name=payload.last_name,
        is_email_verified=False,
        is_active=True,
    )
    session.add(user)
    session.flush()

    # Registration creates an unverified user plus a single-use verification
    # token, persisted hash-only in the same transaction. The raw token is
    # handed solely to the email boundary for future delivery; it never
    # appears in the API response or logs.
    raw_verification_token = generate_email_verification_token()
    session.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_email_verification_token(raw_verification_token),
            expires_at=get_email_verification_expires_at(),
        )
    )
    session.commit()
    session.refresh(user)

    email_service.send_verification_email(
        to_email=user.email,
        verification_token=raw_verification_token,
    )
    return user


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid credentials"},
    },
)
def login(
    payload: UserLogin,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> LoginResponse:
    normalized_email = str(payload.email)
    user = session.exec(
        select(User).where(func.lower(User.email) == normalized_email)
    ).first()

    password_valid = False
    if user is not None and user.password_hash:
        try:
            password_valid = verify_password(payload.password, user.password_hash)
        except Exception:  # noqa: BLE001 - any verification failure must fail closed
            password_valid = False

    # Single generic failure path: unknown email, wrong password, missing
    # hash, and inactive accounts all return the same 401 so callers cannot
    # enumerate accounts. Unverified users are allowed to authenticate here;
    # per the PRD, verification gates protected organization resources, and
    # email verification itself is out of scope for this task.
    if user is None or not password_valid or not user.is_active:
        raise _invalid_credentials_error()

    access_token = create_access_token(str(user.id))

    raw_refresh_token = generate_refresh_token()
    refresh_record = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh_token),
        expires_at=get_refresh_token_expires_at(),
    )
    session.add(refresh_record)
    session.commit()

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_refresh_token,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserRead.model_validate(user),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
)
def logout(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str]:
    # The persisted refresh token is the server-side session state. Revoke
    # the session presented via the auth cookie; the record is retained with
    # revoked_at for audit/security purposes, never deleted. The short-lived
    # access JWT is not retroactively invalidated (no such mechanism exists).
    raw_refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw_refresh_token:
        record = session.exec(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(raw_refresh_token)
            )
        ).first()
        # Idempotent: unknown or already-revoked tokens are not errors, and a
        # first revocation timestamp is preserved for audit purposes.
        if record is not None and record.revoked_at is None:
            record.revoked_at = datetime.now(timezone.utc)
            session.add(record)
            session.commit()

    # Always clear the client cookie with attributes consistent with login.
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
    )
    return {"detail": "Logged out"}


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.post(
    "/refresh",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid refresh token"},
    },
)
def refresh(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> LoginResponse:
    # Rotation: the presented cookie token is single-use. All failure modes
    # (missing, unknown, expired, revoked) share one generic 401 so callers
    # cannot probe session state. A replayed (already-rotated) token is
    # rejected without minting a new session and without reviving anything.
    raw_refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_refresh_token:
        raise _invalid_refresh_error()

    record = session.exec(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(raw_refresh_token)
        )
    ).first()

    now = datetime.now(timezone.utc)
    if (
        record is None
        or record.revoked_at is not None
        or _as_aware(record.expires_at) <= now
    ):
        raise _invalid_refresh_error()

    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise _invalid_refresh_error()

    # Atomic rotation: revoke the old record and persist the new one in a
    # single transaction, linked via replaced_by_id (token family).
    new_raw_refresh_token = generate_refresh_token()
    new_record = RefreshToken(
        user_id=record.user_id,
        token_hash=hash_refresh_token(new_raw_refresh_token),
        expires_at=get_refresh_token_expires_at(now),
    )
    session.add(new_record)
    session.flush()
    record.revoked_at = now
    record.replaced_by_id = new_record.id
    session.add(record)
    session.commit()

    access_token = create_access_token(str(user.id))

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=new_raw_refresh_token,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserRead.model_validate(user),
    )


@router.post(
    "/verify-email",
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid verification token"},
    },
)
def verify_email(
    payload: VerifyEmailRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str]:
    # POST (not GET) keeps the token in a JSON body instead of the URL, so it
    # never lands in access logs or browser history -- consistent with the
    # other state-changing /auth/* endpoints. Unknown, expired, and consumed
    # tokens share one generic 400 so callers cannot probe token existence.
    presented_hash = hash_email_verification_token(payload.token)
    record = session.exec(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == presented_hash
        )
    ).first()

    now = datetime.now(timezone.utc)
    if (
        record is None
        # Constant-time comparison as defense-in-depth on top of the indexed
        # hash lookup; the raw token is never logged or returned.
        or not compare_digest(record.token_hash, presented_hash)
        or record.consumed_at is not None
        or _as_aware(record.expires_at) <= now
    ):
        raise _invalid_verification_error()

    user = session.get(User, record.user_id)
    if user is None:
        raise _invalid_verification_error()

    # Single transaction: consume the token and mark the user verified. A
    # verified flag is only ever set True here, never unset.
    record.consumed_at = now
    user.is_email_verified = True
    session.add(record)
    session.add(user)
    session.commit()

    return {"detail": "Email verified"}


@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
)
def forgot_password(
    payload: ForgotPasswordRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str]:
    # Identical public response whether or not the account exists, so callers
    # cannot enumerate registered emails. Token generation/hashing runs
    # unconditionally to avoid cheap timing oracles; only persistence and
    # email dispatch are gated on an existing user. The raw token goes solely
    # to the email boundary -- never the response or logs.
    raw_reset_token = generate_password_reset_token()
    token_hash = hash_password_reset_token(raw_reset_token)

    user = session.exec(
        select(User).where(func.lower(User.email) == str(payload.email))
    ).first()
    if user is not None:
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=get_password_reset_expires_at(),
            )
        )
        session.commit()
        email_service.send_password_reset_email(
            to_email=user.email,
            reset_token=raw_reset_token,
        )

    return {
        "detail": "If an account exists for this email, a reset email has been sent."
    }


@router.post(
    "/reset-password",
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid reset token"},
    },
)
def reset_password(
    payload: ResetPasswordRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str]:
    # Unknown, expired, and consumed tokens share one generic 400 so callers
    # cannot probe token state. The token applies only to its associated user.
    presented_hash = hash_password_reset_token(payload.token)
    record = session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == presented_hash
        )
    ).first()

    now = datetime.now(timezone.utc)
    if (
        record is None
        # Constant-time comparison as defense-in-depth on top of the indexed
        # hash lookup; the raw token is never logged or returned.
        or not compare_digest(record.token_hash, presented_hash)
        or record.consumed_at is not None
        or _as_aware(record.expires_at) <= now
    ):
        raise _invalid_reset_error()

    user = session.get(User, record.user_id)
    if user is None:
        raise _invalid_reset_error()

    # Single transaction: rotate the password hash, consume the reset token,
    # and revoke all live refresh sessions so previously issued sessions stop
    # working at their next refresh. Verification state is left untouched.
    user.password_hash = hash_password(payload.new_password)
    record.consumed_at = now
    session.add(user)
    session.add(record)
    live_sessions = session.exec(
        select(RefreshToken).where(RefreshToken.user_id == user.id)
    ).all()
    for live in live_sessions:
        if live.revoked_at is None:
            live.revoked_at = now
            session.add(live)
    session.commit()

    return {"detail": "Password has been reset."}
