from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import EmailStr, StringConstraints, field_validator
from sqlmodel import SQLModel

NameField = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]

# Project password policy (registration + reset share it).
PasswordField = Annotated[
    str,
    StringConstraints(min_length=8, max_length=128),
]


class UserRegister(SQLModel):
    email: EmailStr
    password: PasswordField
    first_name: NameField
    last_name: NameField

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class UserRead(SQLModel):
    id: UUID
    email: str
    first_name: str
    last_name: str
    avatar_url: str | None = None
    is_email_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserLogin(SQLModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class LoginResponse(SQLModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class VerifyEmailRequest(SQLModel):
    token: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class ForgotPasswordRequest(SQLModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class ResetPasswordRequest(SQLModel):
    token: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    new_password: PasswordField
